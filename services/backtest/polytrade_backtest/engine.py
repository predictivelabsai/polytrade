from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Literal

from .schemas import (
    BacktestConfig,
    BacktestMetrics,
    BacktestSeriesPoint,
    BacktestTrade,
    MomentumBacktestConfig,
    Outcome,
    strategy_lookback_minutes,
)

PRICE_QUANTUM = Decimal("0.00000001")
SHARE_QUANTUM = Decimal("0.000001")
FEE_QUANTUM = Decimal("0.00001")
PERCENT = Decimal("100")
ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class PriceObservation:
    timestamp: datetime
    price: Decimal

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Price timestamps must be timezone-aware")
        if self.price < ZERO or self.price > ONE:
            raise ValueError("Prices must be between zero and one")


@dataclass(frozen=True)
class SimulationOutput:
    metrics: BacktestMetrics
    trades: list[BacktestTrade]
    series: list[BacktestSeriesPoint]


@dataclass
class _Position:
    outcome: Outcome
    entry_at: datetime
    entry_price: Decimal
    shares: Decimal
    entry_fee: Decimal


@dataclass(frozen=True)
class _PendingEntry:
    outcome: Outcome
    signal_at: datetime


@dataclass(frozen=True)
class _PendingExit:
    signal_at: datetime
    reason: Literal["take_profit", "stop_loss", "max_hold"]


def run_momentum_backtest(
    histories: dict[Outcome, list[PriceObservation]],
    *,
    resolved_outcome: Outcome,
    fee_rate: Decimal,
    config: MomentumBacktestConfig,
    settlement_at: datetime | None = None,
) -> SimulationOutput:
    return run_backtest(
        histories,
        resolved_outcome=resolved_outcome,
        fee_rate=fee_rate,
        config=config,
        settlement_at=settlement_at,
    )


def run_backtest(
    histories: dict[Outcome, list[PriceObservation]],
    *,
    resolved_outcome: Outcome,
    fee_rate: Decimal,
    config: BacktestConfig,
    settlement_at: datetime | None = None,
) -> SimulationOutput:
    normalized = {outcome: _normalize(points) for outcome, points in histories.items()}
    if not normalized.get("YES") or not normalized.get("NO"):
        raise ValueError("Both YES and NO price histories are required")
    if fee_rate < ZERO or fee_rate > ONE:
        raise ValueError("Fee rate must be between zero and one")

    grouped: dict[datetime, dict[Outcome, Decimal]] = {}
    for outcome in ("YES", "NO"):
        for point in normalized[outcome]:
            grouped.setdefault(point.timestamp, {})[outcome] = point.price
    timestamps = sorted(grouped)
    if len(timestamps) < 2:
        raise ValueError("At least two observations are required")

    initial_capital = Decimal(config.initial_capital)
    cash = initial_capital
    latest: dict[Outcome, Decimal] = {}
    seen_times: dict[Outcome, list[datetime]] = {"YES": [], "NO": []}
    seen_prices: dict[Outcome, list[Decimal]] = {"YES": [], "NO": []}
    position: _Position | None = None
    pending_entry: _PendingEntry | None = None
    pending_exit: _PendingExit | None = None
    last_exit_at: datetime | None = None
    skipped_signals = 0
    total_fees = ZERO
    held_seconds = Decimal("0")
    trades: list[BacktestTrade] = []
    series: list[BacktestSeriesPoint] = []
    last_timestamp = timestamps[0]

    window = timedelta(minutes=strategy_lookback_minutes(config))
    cooldown = timedelta(minutes=config.cooldown_minutes)
    max_hold = timedelta(minutes=config.max_hold_minutes)
    max_delay = timedelta(minutes=config.max_fill_delay_minutes)
    if config.strategy == "momentum_v1":
        threshold = Decimal(config.momentum_threshold)
    elif config.strategy == "mean_reversion_v1":
        threshold = Decimal(config.reversion_threshold)
    else:
        threshold = Decimal(config.breakout_threshold)
    take_profit = Decimal(config.take_profit)
    stop_loss = Decimal(config.stop_loss)
    slippage = Decimal(config.slippage)

    for timestamp in timestamps:
        if position is not None:
            held_seconds += Decimal(str((timestamp - last_timestamp).total_seconds()))
        last_timestamp = timestamp
        updates = grouped[timestamp]
        for outcome in ("YES", "NO"):
            if outcome in updates:
                latest[outcome] = updates[outcome]
                seen_times[outcome].append(timestamp)
                seen_prices[outcome].append(updates[outcome])

        if pending_exit is not None and position is not None and timestamp > pending_exit.signal_at:
            observed = updates.get(position.outcome)
            if observed is not None:
                if timestamp - pending_exit.signal_at <= max_delay:
                    fill = max(ZERO, observed - slippage)
                    cash, fee, trade = _close_position(
                        position,
                        timestamp,
                        fill,
                        fee_rate,
                        pending_exit.reason,
                        cash,
                        len(trades),
                    )
                    total_fees += fee
                    trades.append(trade)
                    position = None
                    last_exit_at = timestamp
                else:
                    skipped_signals += 1
                pending_exit = None

        if pending_entry is not None and position is None and timestamp > pending_entry.signal_at:
            observed = updates.get(pending_entry.outcome)
            if observed is not None:
                if timestamp - pending_entry.signal_at <= max_delay:
                    fill = min(ONE, observed + slippage)
                    opened = _open_position(
                        pending_entry.outcome,
                        timestamp,
                        fill,
                        cash,
                        fee_rate,
                        Decimal(config.position_size_pct),
                    )
                    if opened is not None:
                        position, debit = opened
                        cash -= debit
                        total_fees += position.entry_fee
                    else:
                        skipped_signals += 1
                else:
                    skipped_signals += 1
                pending_entry = None

        if position is not None and pending_exit is None and timestamp > position.entry_at:
            observed = latest.get(position.outcome)
            if observed is not None:
                reason: Literal["take_profit", "stop_loss", "max_hold"] | None = None
                if observed >= position.entry_price + take_profit:
                    reason = "take_profit"
                elif observed <= position.entry_price - stop_loss:
                    reason = "stop_loss"
                elif timestamp - position.entry_at >= max_hold:
                    reason = "max_hold"
                if reason is not None:
                    pending_exit = _PendingExit(signal_at=timestamp, reason=reason)

        if position is None and pending_entry is None:
            cooldown_ready = last_exit_at is None or timestamp >= last_exit_at + cooldown
            if cooldown_ready:
                candidates = _entry_candidates(
                    config.strategy,
                    timestamp,
                    window,
                    threshold,
                    max_delay,
                    latest,
                    seen_times,
                    seen_prices,
                )
                if candidates:
                    candidates.sort(key=lambda item: (-item[0], 0 if item[1] == "YES" else 1))
                    pending_entry = _PendingEntry(outcome=candidates[0][1], signal_at=timestamp)

        equity = cash
        if position is not None:
            equity += position.shares * latest.get(position.outcome, position.entry_price)
        series.append(
            BacktestSeriesPoint(
                timestamp=timestamp,
                yes_price=_optional_decimal(latest.get("YES")),
                no_price=_optional_decimal(latest.get("NO")),
                equity=_decimal(equity),
            )
        )

    if pending_entry is not None and position is None:
        skipped_signals += 1

    final_timestamp = settlement_at or timestamps[-1]
    if final_timestamp.tzinfo is None:
        final_timestamp = final_timestamp.replace(tzinfo=UTC)
    if final_timestamp < timestamps[-1]:
        final_timestamp = timestamps[-1]
    if position is not None:
        if final_timestamp > last_timestamp:
            held_seconds += Decimal(str((final_timestamp - last_timestamp).total_seconds()))
        settlement_price = ONE if position.outcome == resolved_outcome else ZERO
        cash, fee, trade = _close_position(
            position,
            final_timestamp,
            settlement_price,
            ZERO,
            "settlement",
            cash,
            len(trades),
        )
        total_fees += fee
        trades.append(trade)
        position = None
        series.append(
            BacktestSeriesPoint(
                timestamp=final_timestamp,
                yes_price="1" if resolved_outcome == "YES" else "0",
                no_price="1" if resolved_outcome == "NO" else "0",
                equity=_decimal(cash),
            )
        )

    equities = [Decimal(point.equity) for point in series]
    final_equity = cash
    pnl = final_equity - initial_capital
    duration_seconds = Decimal(str(max(1, (final_timestamp - timestamps[0]).total_seconds())))
    wins = [Decimal(trade.pnl) for trade in trades if Decimal(trade.pnl) > ZERO]
    losses = [Decimal(trade.pnl) for trade in trades if Decimal(trade.pnl) < ZERO]
    holding = [Decimal(str((trade.exit_at - trade.entry_at).total_seconds())) for trade in trades]
    yes_benchmark = _buy_hold_return(
        initial_capital, normalized["YES"][0].price, resolved_outcome == "YES", fee_rate, slippage
    )
    no_benchmark = _buy_hold_return(
        initial_capital, normalized["NO"][0].price, resolved_outcome == "NO", fee_rate, slippage
    )
    metrics = BacktestMetrics(
        initial_capital=_decimal(initial_capital),
        final_equity=_decimal(final_equity),
        pnl=_decimal(pnl),
        return_pct=_decimal(pnl / initial_capital * PERCENT),
        max_drawdown_pct=_decimal(_max_drawdown(equities) * PERCENT),
        trade_count=len(trades),
        win_rate_pct=_decimal(Decimal(len(wins)) / Decimal(len(trades)) * PERCENT)
        if trades
        else "0",
        profit_factor=_decimal(sum(wins, ZERO) / abs(sum(losses, ZERO))) if losses else None,
        average_holding_seconds=_decimal(sum(holding, ZERO) / Decimal(len(holding)))
        if holding
        else "0",
        exposure_pct=_decimal(min(ONE, held_seconds / duration_seconds) * PERCENT),
        fees=_decimal(total_fees),
        skipped_signals=skipped_signals,
        yes_buy_hold_return_pct=_decimal(yes_benchmark),
        no_buy_hold_return_pct=_decimal(no_benchmark),
    )
    return SimulationOutput(metrics=metrics, trades=trades, series=series)


def _entry_candidates(
    strategy: str,
    timestamp: datetime,
    window: timedelta,
    threshold: Decimal,
    max_delay: timedelta,
    latest: dict[Outcome, Decimal],
    seen_times: dict[Outcome, list[datetime]],
    seen_prices: dict[Outcome, list[Decimal]],
) -> list[tuple[Decimal, Outcome]]:
    candidates: list[tuple[Decimal, Outcome]] = []
    target = timestamp - window
    for outcome in ("YES", "NO"):
        current = latest.get(outcome)
        if current is None:
            continue
        if strategy == "momentum_v1":
            reference = _reference_price(
                seen_times[outcome], seen_prices[outcome], target, max_delay
            )
            if reference is None:
                continue
            signal = current - reference
        else:
            prior = _prior_window_prices(
                seen_times[outcome], seen_prices[outcome], target, max_delay
            )
            if prior is None:
                continue
            if strategy == "mean_reversion_v1":
                signal = sum(prior, ZERO) / Decimal(len(prior)) - current
            else:
                signal = current - max(prior)
        if signal >= threshold:
            candidates.append((signal, outcome))
    return candidates


def fee_for(shares: Decimal, fee_rate: Decimal, price: Decimal) -> Decimal:
    return (shares * fee_rate * price * (ONE - price)).quantize(FEE_QUANTUM, rounding=ROUND_HALF_UP)


def _normalize(points: list[PriceObservation]) -> list[PriceObservation]:
    deduplicated: dict[datetime, PriceObservation] = {}
    for point in points:
        timestamp = point.timestamp.astimezone(UTC)
        deduplicated[timestamp] = PriceObservation(timestamp=timestamp, price=point.price)
    return [deduplicated[timestamp] for timestamp in sorted(deduplicated)]


def _reference_price(
    times: list[datetime],
    prices: list[Decimal],
    target: datetime,
    tolerance: timedelta,
) -> Decimal | None:
    index = bisect_right(times, target) - 1
    if index < 0 or target - times[index] > tolerance:
        return None
    return prices[index]


def _prior_window_prices(
    times: list[datetime],
    prices: list[Decimal],
    target: datetime,
    tolerance: timedelta,
) -> list[Decimal] | None:
    current_index = len(times) - 1
    if current_index < 2:
        return None
    anchor_index = bisect_right(times, target, hi=current_index) - 1
    if anchor_index < 0 or target - times[anchor_index] > tolerance:
        return None
    prior = prices[anchor_index:current_index]
    return prior if len(prior) >= 2 else None


def _open_position(
    outcome: Outcome,
    timestamp: datetime,
    price: Decimal,
    cash: Decimal,
    fee_rate: Decimal,
    position_size_pct: Decimal,
) -> tuple[_Position, Decimal] | None:
    if price <= ZERO or price >= ONE:
        return None
    allocation = cash * position_size_pct
    per_share = price + fee_rate * price * (ONE - price)
    shares = (allocation / per_share).quantize(SHARE_QUANTUM, rounding=ROUND_DOWN)
    if shares <= ZERO:
        return None
    fee = fee_for(shares, fee_rate, price)
    debit = shares * price + fee
    if debit > allocation:
        shares = (shares - SHARE_QUANTUM).quantize(SHARE_QUANTUM, rounding=ROUND_DOWN)
        if shares <= ZERO:
            return None
        fee = fee_for(shares, fee_rate, price)
        debit = shares * price + fee
    return (
        _Position(
            outcome=outcome,
            entry_at=timestamp,
            entry_price=price.quantize(PRICE_QUANTUM),
            shares=shares,
            entry_fee=fee,
        ),
        debit,
    )


def _close_position(
    position: _Position,
    timestamp: datetime,
    price: Decimal,
    fee_rate: Decimal,
    reason: Literal["take_profit", "stop_loss", "max_hold", "settlement"],
    cash: Decimal,
    trade_index: int,
) -> tuple[Decimal, Decimal, BacktestTrade]:
    price = min(ONE, max(ZERO, price)).quantize(PRICE_QUANTUM)
    fee = fee_for(position.shares, fee_rate, price)
    proceeds = position.shares * price - fee
    pnl = proceeds - position.shares * position.entry_price - position.entry_fee
    trade = BacktestTrade(
        trade_index=trade_index,
        outcome=position.outcome,
        entry_at=position.entry_at,
        exit_at=timestamp,
        entry_price=_decimal(position.entry_price),
        exit_price=_decimal(price),
        shares=_decimal(position.shares),
        entry_fee=_decimal(position.entry_fee),
        exit_fee=_decimal(fee),
        pnl=_decimal(pnl),
        exit_reason=reason,
    )
    return cash + proceeds, fee, trade


def _buy_hold_return(
    initial_capital: Decimal,
    observed_price: Decimal,
    winner: bool,
    fee_rate: Decimal,
    slippage: Decimal,
) -> Decimal:
    price = min(ONE, observed_price + slippage)
    opened = _open_position(
        "YES", datetime(1970, 1, 1, tzinfo=UTC), price, initial_capital, fee_rate, ONE
    )
    if opened is None:
        return ZERO
    position, debit = opened
    final = initial_capital - debit + position.shares * (ONE if winner else ZERO)
    return (final - initial_capital) / initial_capital * PERCENT


def _max_drawdown(equities: list[Decimal]) -> Decimal:
    peak = ZERO
    maximum = ZERO
    for equity in equities:
        peak = max(peak, equity)
        if peak > ZERO:
            maximum = max(maximum, (peak - equity) / peak)
    return maximum


def _decimal(value: Decimal) -> str:
    normalized = value.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    encoded = format(normalized, "f").rstrip("0").rstrip(".")
    return encoded if encoded not in {"", "-0"} else "0"


def _optional_decimal(value: Decimal | None) -> str | None:
    return None if value is None else _decimal(value)
