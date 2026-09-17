"""Polymarket HTTP adapter and normalization rules."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx
from polytrade_contracts import (
    MarketSearchEvent,
    MarketSearchMarket,
    PublicMarket,
    PublicMarketListResponse,
    PublicOrderBook,
    PublicOrderBookLevel,
    PublicPriceHistory,
    PublicPriceHistoryPoint,
    is_backtest_eligible_market,
    resolved_binary_market_winner,
)

from .config import GatewayConfig
from .errors import AppError, not_found, unavailable, validation


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def pick_category(tags: list[str], event_category: str | None) -> str | None:
    for label in tags:
        cleaned = label.strip()
        if cleaned and cleaned.lower() != "all":
            return cleaned
    category = (event_category or "").strip()
    return category or None


def build_l1_typed_data(wallet_address: str, timestamp_seconds: int, nonce: int) -> dict[str, Any]:
    return {
        "domain": {"name": "ClobAuthDomain", "version": "1", "chainId": 137},
        "types": {
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
                {"name": "message", "type": "string"},
            ]
        },
        "primaryType": "ClobAuth",
        "message": {
            "address": wallet_address,
            "timestamp": str(timestamp_seconds),
            "nonce": nonce,
            "message": "This message attests that I control the given wallet",
        },
    }


class PolymarketAdapter:
    def __init__(self, config: GatewayConfig, client: httpx.AsyncClient | None = None) -> None:
        if config.POLYMARKET_CHAIN_ID != 137:
            raise ValueError("Production gateway supports Polygon mainnet only")
        self.config = config
        self.client = client or httpx.AsyncClient(
            timeout=config.POLYMARKET_REQUEST_TIMEOUT_MS / 1_000,
            follow_redirects=False,
        )

    async def search_markets(self, query: str, limit: int, state: str = "active") -> dict[str, Any]:
        raw = await self._get_json(
            f"{self.config.POLYMARKET_GAMMA_URL}/public-search",
            params={
                "q": query,
                "limit_per_type": str(limit),
                "events_status": "active" if state == "active" else "closed",
                "search_profiles": "false",
            },
        )
        events_raw = raw.get("events") if isinstance(raw, dict) else None
        if not isinstance(events_raw, list):
            raise unavailable("Polymarket returned malformed data")
        events = [_normalize_event(event) for event in events_raw if isinstance(event, dict)]
        if state == "resolved":
            resolved: list[MarketSearchEvent] = []
            for event in events:
                markets = [item for item in event.markets if is_backtest_eligible_market(item)]
                if markets:
                    resolved.append(event.model_copy(update={"markets": markets}))
            events = resolved
        return {
            "query": query,
            "state": state,
            "observedAt": now_iso(),
            "events": [event.model_dump(mode="json") for event in events[:limit]],
        }

    async def list_active_markets(
        self, *, limit: int, offset: int, order: str
    ) -> PublicMarketListResponse:
        raw = await self._get_json(
            f"{self.config.POLYMARKET_GAMMA_URL}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": str(limit),
                "offset": str(offset),
                "order": order,
                "ascending": "false",
            },
        )
        if not isinstance(raw, list):
            raise unavailable("Polymarket returned malformed data")
        markets = [
            _normalize_public_market(item)
            for item in raw
            if isinstance(item, dict) and item.get("enableOrderBook") is not False
        ]
        markets = [item for item in markets if len(item.clobTokenIds) == 2]
        return PublicMarketListResponse(
            markets=markets,
            limit=limit,
            offset=offset,
            hasMore=len(raw) == limit,
            observedAt=now_iso(),
        )

    async def get_market(self, identifier: str, kind: str = "slug") -> dict[str, Any]:
        path = (
            f"/markets/slug/{quote(identifier)}"
            if kind == "slug"
            else f"/markets/{quote(identifier)}"
        )
        raw = await self._get_json(f"{self.config.POLYMARKET_GAMMA_URL}{path}")
        if not isinstance(raw, dict):
            raise unavailable("Polymarket returned malformed data")
        return {"market": _normalize_market(raw).model_dump(mode="json"), "observedAt": now_iso()}

    async def get_public_market(self, slug: str) -> dict[str, Any]:
        raw = await self._get_json(
            f"{self.config.POLYMARKET_GAMMA_URL}/markets/slug/{quote(slug)}",
            not_found_message="Public market not found",
        )
        if not isinstance(raw, dict):
            raise unavailable("Polymarket returned malformed data")
        return {"market": _normalize_public_market(raw), "observedAt": now_iso()}

    async def get_market_by_condition(self, condition_id: str) -> dict[str, Any]:
        market, _, observed_at = await self._fetch_market_by_condition(condition_id)
        return {"market": market, "observedAt": observed_at}

    async def _fetch_market_by_condition(
        self, condition_id: str
    ) -> tuple[MarketSearchMarket, dict[str, Any], str]:
        raw = await self._get_json(
            f"{self.config.POLYMARKET_GAMMA_URL}/markets",
            params={"condition_ids": condition_id, "limit": "2"},
        )
        if not isinstance(raw, list):
            raise unavailable("Polymarket returned malformed data")
        matches = [
            item
            for item in raw
            if isinstance(item, dict) and item.get("conditionId") == condition_id
        ]
        if not matches:
            raise validation("The paper market is not listed by Polymarket")
        if len(matches) != 1:
            raise unavailable("Polymarket returned ambiguous market metadata")
        try:
            market = _normalize_market(matches[0])
        except Exception as exc:
            raise unavailable("Polymarket returned malformed market metadata") from exc
        return market, matches[0], now_iso()

    async def get_market_resolution(self, condition_id: str) -> dict[str, Any]:
        market, raw, observed_at = await self._fetch_market_by_condition(condition_id)
        tags: list[str] = []
        category = None
        events = raw.get("events")
        event_slug = ""
        if isinstance(events, list) and events and isinstance(events[0], dict):
            event_slug = str(events[0].get("slug") or "")
        if event_slug:
            try:
                event = await self._get_json(
                    f"{self.config.POLYMARKET_GAMMA_URL}/events/slug/{quote(event_slug)}",
                    params={"include_tags": "true"},
                )
                if isinstance(event, dict):
                    raw_tags = event.get("tags")
                    if isinstance(raw_tags, list):
                        tags = [
                            str(item["label"])
                            for item in raw_tags
                            if isinstance(item, dict) and item.get("label")
                        ]
                    category = pick_category(tags, event.get("category"))
            except Exception:  # noqa: S110 - category enrichment is optional by design
                pass
        return {
            "market": market,
            "winner": resolved_binary_market_winner(market),
            "closedTime": market.closedTime,
            "category": category,
            "tags": tags,
            "observedAt": observed_at,
        }

    async def get_order_book(self, token_id: str) -> PublicOrderBook:
        raw = await self._get_json(
            f"{self.config.POLYMARKET_CLOB_URL}/book", params={"token_id": token_id}
        )
        if not isinstance(raw, dict):
            raise unavailable("Polymarket returned malformed data")
        return PublicOrderBook(
            tokenId=token_id,
            bids=_normalize_levels(raw.get("bids")),
            asks=_normalize_levels(raw.get("asks", raw.get("tasks"))),
            minimumOrderSize=str(raw.get("min_order_size", "")),
            tickSize=str(raw.get("tick_size", "")),
            negativeRisk=bool(raw.get("neg_risk", False)),
            lastTradePrice=str(raw.get("last_trade_price"))
            if raw.get("last_trade_price")
            else None,
            observedAt=now_iso(),
        )

    async def get_fee_rate(self, token_id: str) -> str:
        raw = await self._get_json(
            f"{self.config.POLYMARKET_CLOB_URL}/fee-rate", params={"token_id": token_id}
        )
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("base_fee"), int)
            or raw["base_fee"] < 0
        ):
            raise unavailable("Polymarket returned malformed data")
        return f"{Decimal(raw['base_fee']) / Decimal(10_000):.6f}"

    async def get_price_history(self, token_id: str, interval: str) -> PublicPriceHistory:
        raw = await self._get_json(
            f"{self.config.POLYMARKET_CLOB_URL}/prices-history",
            params={"market": token_id, "interval": interval},
        )
        history = raw.get("history") if isinstance(raw, dict) else raw
        if not isinstance(history, list):
            raise unavailable("Polymarket returned malformed data")
        points = [
            PublicPriceHistoryPoint(timestamp=int(item["t"]), price=str(item["p"]))
            for item in history
            if isinstance(item, dict) and "t" in item and "p" in item
        ]
        return PublicPriceHistory(
            tokenId=token_id, interval=interval, points=points, observedAt=now_iso()
        )

    async def get_recent_trades(self, condition_id: str) -> dict[str, Any]:
        raw = await self._get_json(
            f"{self.config.POLYMARKET_CLOB_URL}/trades", params={"market": condition_id}
        )
        return {"conditionId": condition_id, "trades": raw, "observedAt": now_iso()}

    async def exchange_l1_credentials(
        self, wallet_address: str, signature: str, timestamp_seconds: int, nonce: int
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "POLY_ADDRESS": wallet_address,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": str(timestamp_seconds),
            "POLY_NONCE": str(nonce),
        }
        create = await self.client.post(
            f"{self.config.POLYMARKET_CLOB_URL}/auth/api-key", headers=headers
        )
        if create.is_success:
            return _normalize_credentials(create.json())
        if create.status_code not in {400, 409}:
            raise unavailable("Polymarket wallet authentication is unavailable")
        derive = await self.client.get(
            f"{self.config.POLYMARKET_CLOB_URL}/auth/derive-api-key", headers=headers
        )
        if not derive.is_success:
            raise unavailable("Polymarket rejected wallet authentication")
        return _normalize_credentials(derive.json())

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        not_found_message: str | None = None,
    ) -> Any:
        for attempt in range(3):
            try:
                response = await self.client.get(
                    url, params=params, headers={"Accept": "application/json"}
                )
                if response.status_code == 404 and not_found_message:
                    raise not_found(not_found_message)
                if response.status_code in {429} or response.status_code >= 500:
                    if attempt < 2:
                        await asyncio.sleep(0.1 * (attempt + 1))
                        continue
                if not response.is_success:
                    raise unavailable(f"Polymarket data request failed ({response.status_code})")
                return response.json()
            except AppError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                if attempt < 2:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                raise unavailable("Polymarket data is temporarily unavailable") from exc
        raise unavailable("Polymarket data is temporarily unavailable")


def normalize_onchain_balance(value: str) -> Decimal:
    return Decimal(value or 0) / Decimal(1_000_000)


def _normalize_credentials(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise unavailable("Polymarket returned invalid API credentials")
    key = raw.get("apiKey", raw.get("key"))
    if not all(isinstance(value, str) for value in (key, raw.get("secret"), raw.get("passphrase"))):
        raise unavailable("Polymarket returned invalid API credentials")
    return {"key": key, "secret": raw["secret"], "passphrase": raw["passphrase"]}


def _parse_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except ValueError:
        return []


def _normalize_timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, int | float):
            timestamp = value / 1_000 if value >= 1_000_000_000_000 else value
            parsed = datetime.fromtimestamp(timestamp, UTC)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OSError):
        return None


def _normalize_market(raw: dict[str, Any]) -> MarketSearchMarket:
    return MarketSearchMarket(
        id=str(raw.get("id", "")),
        conditionId=str(raw.get("conditionId", "")),
        slug=str(raw.get("slug", "")),
        question=str(raw.get("question", "")),
        description=str(raw.get("description", "")),
        outcomes=[str(item) for item in _parse_array(raw.get("outcomes"))],
        outcomePrices=[str(item) for item in _parse_array(raw.get("outcomePrices"))],
        clobTokenIds=[str(item) for item in _parse_array(raw.get("clobTokenIds"))],
        active=bool(raw.get("active")),
        closed=bool(raw.get("closed")),
        acceptingOrders=bool(raw.get("acceptingOrders")),
        enableOrderBook=bool(raw.get("enableOrderBook")),
        archived=bool(raw.get("archived")),
        restricted=bool(raw.get("restricted")),
        minimumOrderSize=str(raw.get("orderMinSize", "")),
        minimumTickSize=str(raw.get("orderPriceMinTickSize", "")),
        endDate=_normalize_timestamp(raw.get("endDate")),
        startDate=_normalize_timestamp(raw.get("startDate")),
        createdAt=_normalize_timestamp(raw.get("createdAt")),
        closedTime=_normalize_timestamp(raw.get("closedTime")),
        liquidity=str(raw.get("liquidity", "")),
        volume=str(raw.get("volume", "")),
    )


def _normalize_public_market(raw: dict[str, Any]) -> PublicMarket:
    value = _normalize_market(raw).model_dump(mode="json")
    if raw.get("icon"):
        value["icon"] = str(raw["icon"])
    if raw.get("volume24hr") is not None:
        value["volume24hr"] = str(raw["volume24hr"])
    return PublicMarket.model_validate(value)


def _normalize_event(raw: dict[str, Any]) -> MarketSearchEvent:
    markets = raw.get("markets") if isinstance(raw.get("markets"), list) else []
    return MarketSearchEvent(
        id=str(raw.get("id", "")),
        slug=str(raw.get("slug", "")),
        title=str(raw.get("title", "")),
        description=str(raw.get("description", "")),
        endDate=_normalize_timestamp(raw.get("endDate")),
        liquidity=str(raw.get("liquidity", "")),
        volume=str(raw.get("volume", "")),
        markets=[_normalize_market(item) for item in markets if isinstance(item, dict)],
    )


def _normalize_levels(raw: Any) -> list[PublicOrderBookLevel]:
    if not isinstance(raw, list):
        return []
    return [
        PublicOrderBookLevel(price=str(item["price"]), size=str(item["size"]))
        for item in raw[:100]
        if isinstance(item, dict) and "price" in item and "size" in item
    ]
