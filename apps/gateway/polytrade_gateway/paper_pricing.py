"""Deterministic Decimal pricing for paper fills and portfolio marks."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from polytrade_contracts import PaperQuote, PaperQuoteRequest


class PaperPricingError(Exception):
    def __init__(
        self,
        reason: Literal["MALFORMED_BOOK", "INSUFFICIENT_LIQUIDITY", "PRICE_MOVED"],
        message: str,
    ) -> None:
        super().__init__(message)
        self.reason = reason


def _decimal(value: Any, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise InvalidOperation
        return parsed
    except (InvalidOperation, ValueError) as exc:
        raise PaperPricingError("MALFORMED_BOOK", f"Invalid {label}") from exc


def _book(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    if not isinstance(raw, dict) or not isinstance(raw.get("observedAt"), str):
        raise PaperPricingError("MALFORMED_BOOK", "Polymarket returned a malformed order book")
    bids, asks = raw.get("bids"), raw.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list):
        raise PaperPricingError("MALFORMED_BOOK", "Polymarket returned a malformed order book")
    if not all(
        isinstance(item, dict) and "price" in item and "size" in item for item in bids + asks
    ):
        raise PaperPricingError("MALFORMED_BOOK", "Polymarket returned a malformed order book")
    return bids, asks, raw["observedAt"]


def best_bid_from_order_book(raw: Any) -> dict[str, str] | None:
    bids, _, observed_at = _book(raw)
    prices: list[Decimal] = []
    for level in bids:
        bid_price = _decimal(level["price"], "book price")
        bid_size = _decimal(level["size"], "book size")
        if bid_price <= 0 or bid_price > 1 or bid_size <= 0:
            raise PaperPricingError(
                "MALFORMED_BOOK", "Polymarket returned an invalid order-book level"
            )
        prices.append(bid_price)
    return {"price": price(max(prices)), "observedAt": observed_at} if prices else None


def quote_paper_order(
    request: PaperQuoteRequest,
    identity: dict[str, str],
    raw_book: Any,
    raw_fee_rate: str,
    limit_price: str | None = None,
) -> PaperQuote:
    bids, asks, observed_at = _book(raw_book)
    requested_shares = _decimal(request.shares, "share quantity")
    fee_rate = _decimal(raw_fee_rate, "fee rate")
    if requested_shares <= 0 or fee_rate < 0 or fee_rate > 1:
        raise PaperPricingError("MALFORMED_BOOK", "Polymarket returned invalid pricing data")
    raw_levels = asks if request.side == "BUY" else bids
    levels: list[tuple[Decimal, Decimal]] = []
    for level in raw_levels:
        level_price = _decimal(level["price"], "book price")
        level_size = _decimal(level["size"], "book size")
        if level_price <= 0 or level_price > 1 or level_size <= 0:
            raise PaperPricingError(
                "MALFORMED_BOOK", "Polymarket returned an invalid order-book level"
            )
        levels.append((level_price, level_size))
    levels.sort(key=lambda item: item[0], reverse=request.side == "SELL")
    bound = _decimal(limit_price, "limit price") if limit_price is not None else None
    if bound is not None and (bound <= 0 or bound > 1):
        raise PaperPricingError("MALFORMED_BOOK", "Paper price bound is invalid")
    allowed = [
        level
        for level in levels
        if bound is None
        or (request.side == "BUY" and level[0] <= bound)
        or (request.side == "SELL" and level[0] >= bound)
    ]
    total_depth = sum((item[1] for item in levels), Decimal(0))
    allowed_depth = sum((item[1] for item in allowed), Decimal(0))
    if allowed_depth < requested_shares:
        if bound is not None and total_depth >= requested_shares:
            raise PaperPricingError(
                "PRICE_MOVED", "The paper quote moved beyond its confirmed price"
            )
        raise PaperPricingError(
            "INSUFFICIENT_LIQUIDITY", "The visible order book cannot fill the complete paper order"
        )
    remaining = requested_shares
    gross = Decimal(0)
    raw_fee = Decimal(0)
    worst_price = Decimal(0)
    for level_price, level_size in allowed:
        if remaining <= 0:
            break
        quantity = min(remaining, level_size)
        gross += quantity * level_price
        raw_fee += quantity * fee_rate * level_price * (Decimal(1) - level_price)
        worst_price = level_price
        remaining -= quantity
    rounded_gross = gross.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    fee = raw_fee.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    average_price = gross / requested_shares
    cash_effect = -(rounded_gross + fee) if request.side == "BUY" else rounded_gross - fee
    return PaperQuote(
        **identity,
        side=request.side,
        shares=shares(requested_shares),
        averagePrice=price(average_price),
        limitPrice=price(worst_price),
        grossNotional=money(rounded_gross),
        feeRate=money(fee_rate),
        fee=f"{fee:.5f}",
        cashEffect=money(cash_effect),
        observedAt=observed_at,
    )


def paper_liquidation_value(raw_shares: str, raw_best_bid: str, raw_fee_rate: str) -> str:
    quantity = Decimal(raw_shares)
    best_bid = Decimal(raw_best_bid)
    fee_rate = Decimal(raw_fee_rate)
    gross = quantity * best_bid
    fee = (quantity * fee_rate * best_bid * (Decimal(1) - best_bid)).quantize(
        Decimal("0.00001"), rounding=ROUND_HALF_UP
    )
    return money(max(Decimal(0), gross - fee))


def money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP):.6f}"


def shares(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.000001'), rounding=ROUND_DOWN):.6f}"


def price(value: Decimal) -> str:
    return money(value)
