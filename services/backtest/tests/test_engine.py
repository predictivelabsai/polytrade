from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from polytrade_backtest.engine import PriceObservation, fee_for, run_backtest, run_momentum_backtest
from polytrade_backtest.schemas import (
    BreakoutBacktestConfig,
    CreateBacktestRequest,
    MeanReversionBacktestConfig,
    MomentumBacktestConfig,
    parse_backtest_config,
)


def point(start: datetime, minute: int, price: str) -> PriceObservation:
    return PriceObservation(start + timedelta(minutes=minute), Decimal(price))


def flat_history(start: datetime, price: str, end: int = 70) -> list[PriceObservation]:
    return [point(start, minute, price) for minute in range(end + 1)]


def test_fee_formula_rounds_to_five_decimals() -> None:
    assert fee_for(Decimal("100"), Decimal("0.04"), Decimal("0.50")) == Decimal("1.00000")


def test_date_ranges_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        MomentumBacktestConfig(start_at=datetime(2026, 5, 1))


def test_strategy_configs_default_to_momentum_and_reject_cross_strategy_fields() -> None:
    request = CreateBacktestRequest(market_id="condition-1", config={})
    assert request.config.strategy == "momentum_v1"
    with pytest.raises(ValidationError, match="momentumWindowMinutes"):
        parse_backtest_config(
            {
                "strategy": "mean_reversion_v1",
                "momentumWindowMinutes": 30,
            }
        )
    with pytest.raises(ValidationError, match="greater than zero"):
        BreakoutBacktestConfig(breakout_threshold="0")


def test_signal_and_exits_fill_at_the_next_observation() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    yes = flat_history(start, "0.50")
    yes[60] = point(start, 60, "0.56")
    yes[61] = point(start, 61, "0.56")
    yes[62] = point(start, 62, "0.67")
    yes[63] = point(start, 63, "0.68")
    output = run_momentum_backtest(
        {"YES": yes, "NO": flat_history(start, "0.50")},
        resolved_outcome="YES",
        fee_rate=Decimal("0"),
        config=MomentumBacktestConfig(slippage="0"),
        settlement_at=start + timedelta(minutes=70),
    )

    assert len(output.trades) == 1
    trade = output.trades[0]
    assert trade.entry_at == start + timedelta(minutes=61)
    assert trade.exit_at == start + timedelta(minutes=63)
    assert trade.exit_reason == "take_profit"
    assert trade.outcome == "YES"


def test_equal_momentum_uses_yes_as_the_stable_tie_breaker() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    histories = {"YES": flat_history(start, "0.40"), "NO": flat_history(start, "0.40")}
    for outcome in ("YES", "NO"):
        for minute in range(60, 71):
            histories[outcome][minute] = point(start, minute, "0.46")
    output = run_momentum_backtest(
        histories,
        resolved_outcome="YES",
        fee_rate=Decimal("0"),
        config=MomentumBacktestConfig(slippage="0"),
    )

    assert output.trades[0].outcome == "YES"
    assert output.trades[0].exit_reason == "settlement"


def test_stale_next_point_skips_entry() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    yes = [point(start, minute, "0.50") for minute in range(61)]
    yes[60] = point(start, 60, "0.56")
    yes.append(point(start, 66, "0.56"))
    output = run_momentum_backtest(
        {"YES": yes, "NO": flat_history(start, "0.50")},
        resolved_outcome="NO",
        fee_rate=Decimal("0"),
        config=MomentumBacktestConfig(slippage="0"),
    )

    assert output.trades == []
    # The stale fill and the final signal with no subsequent YES observation
    # are both explicitly reported rather than silently disappearing.
    assert output.metrics.skipped_signals == 2


def test_settlement_uses_binary_payoff_and_is_deterministic() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    yes = flat_history(start, "0.40", 65)
    for minute in range(60, 66):
        yes[minute] = point(start, minute, "0.46")
    histories = {"YES": yes, "NO": flat_history(start, "0.60", 65)}
    config = MomentumBacktestConfig(slippage="0")
    first = run_momentum_backtest(
        histories,
        resolved_outcome="YES",
        fee_rate=Decimal("0.04"),
        config=config,
    )
    second = run_momentum_backtest(
        histories,
        resolved_outcome="YES",
        fee_rate=Decimal("0.04"),
        config=config,
    )

    assert first.trades[-1].exit_price == "1"
    assert first.trades[-1].exit_fee == "0"
    assert first.metrics.model_dump() == second.metrics.model_dump()
    assert [trade.model_dump() for trade in first.trades] == [
        trade.model_dump() for trade in second.trades
    ]


def test_stop_loss_uses_adverse_slippage_on_the_next_observation() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    yes = flat_history(start, "0.40", 70)
    yes[60] = point(start, 60, "0.46")
    yes[61] = point(start, 61, "0.46")
    yes[62] = point(start, 62, "0.41")
    yes[63] = point(start, 63, "0.40")
    output = run_momentum_backtest(
        {"YES": yes, "NO": flat_history(start, "0.60", 70)},
        resolved_outcome="NO",
        fee_rate=Decimal("0"),
        config=MomentumBacktestConfig(slippage="0.01"),
    )

    trade = output.trades[0]
    assert trade.entry_at == start + timedelta(minutes=61)
    assert trade.entry_price == "0.47"
    assert trade.exit_at == start + timedelta(minutes=63)
    assert trade.exit_price == "0.39"
    assert trade.exit_reason == "stop_loss"


def test_maximum_hold_and_cooldown_keep_only_one_position_open() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    yes = flat_history(start, "0.40", 80)
    for minute in range(60, 81):
        yes[minute] = point(start, minute, "0.46")
    output = run_momentum_backtest(
        {"YES": yes, "NO": flat_history(start, "0.60", 80)},
        resolved_outcome="YES",
        fee_rate=Decimal("0"),
        config=MomentumBacktestConfig(
            slippage="0",
            take_profit="1",
            stop_loss="1",
            max_hold_minutes=2,
            cooldown_minutes=60,
        ),
    )

    assert len(output.trades) == 1
    assert output.trades[0].entry_at == start + timedelta(minutes=61)
    assert output.trades[0].exit_at == start + timedelta(minutes=64)
    assert output.trades[0].exit_reason == "max_hold"


def test_fee_aware_sizing_rounds_shares_down_to_six_decimals() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    yes = flat_history(start, "0.40", 65)
    yes[60] = point(start, 60, "0.45")
    yes[61] = point(start, 61, "0.45")
    output = run_momentum_backtest(
        {"YES": yes, "NO": flat_history(start, "0.60", 65)},
        resolved_outcome="YES",
        fee_rate=Decimal("0.04"),
        config=MomentumBacktestConfig(slippage="0.01"),
    )

    trade = output.trades[0]
    expected_shares = (Decimal("1000") / Decimal("0.469936")).quantize(
        Decimal("0.000001"), rounding="ROUND_DOWN"
    )
    assert Decimal(trade.shares) == expected_shares
    assert Decimal(trade.entry_fee) == fee_for(
        expected_shares, Decimal("0.04"), Decimal("0.46")
    )
    assert Decimal(trade.shares).as_tuple().exponent >= -6


def test_no_outcome_can_trigger_independently() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    no = flat_history(start, "0.30", 65)
    for minute in range(60, 66):
        no[minute] = point(start, minute, "0.36")
    output = run_momentum_backtest(
        {"YES": flat_history(start, "0.70", 65), "NO": no},
        resolved_outcome="NO",
        fee_rate=Decimal("0"),
        config=MomentumBacktestConfig(slippage="0"),
    )

    assert output.trades[0].outcome == "NO"
    assert output.trades[0].exit_reason == "settlement"


def test_mean_reversion_uses_prior_window_and_fills_next_observation() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    yes = flat_history(start, "0.50", 6)
    yes[2] = point(start, 2, "0.40")
    yes[3] = point(start, 3, "0.41")
    output = run_backtest(
        {"YES": yes, "NO": flat_history(start, "0.50", 6)},
        resolved_outcome="YES",
        fee_rate=Decimal("0"),
        config=MeanReversionBacktestConfig(
            reversion_window_minutes=2,
            reversion_threshold="0.10",
            slippage="0",
        ),
    )

    assert len(output.trades) == 1
    assert output.trades[0].outcome == "YES"
    assert output.trades[0].entry_at == start + timedelta(minutes=3)
    assert output.trades[0].entry_price == "0.41"


def test_breakout_excludes_current_price_and_uses_yes_tie_breaker() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    histories = {
        "YES": [
            point(start, 0, "0.40"),
            point(start, 1, "0.50"),
            point(start, 2, "0.45"),
            point(start, 3, "0.53"),
            point(start, 4, "0.54"),
        ],
        "NO": [
            point(start, 0, "0.40"),
            point(start, 1, "0.50"),
            point(start, 2, "0.45"),
            point(start, 3, "0.53"),
            point(start, 4, "0.54"),
        ],
    }
    output = run_backtest(
        histories,
        resolved_outcome="YES",
        fee_rate=Decimal("0"),
        config=BreakoutBacktestConfig(
            breakout_window_minutes=3,
            breakout_threshold="0.02",
            slippage="0",
        ),
    )

    assert len(output.trades) == 1
    assert output.trades[0].outcome == "YES"
    assert output.trades[0].entry_at == start + timedelta(minutes=4)
    assert output.trades[0].entry_price == "0.54"


def test_breakout_does_not_signal_when_price_only_matches_prior_high() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    history = [
        point(start, 0, "0.40"),
        point(start, 1, "0.50"),
        point(start, 2, "0.45"),
        point(start, 3, "0.50"),
        point(start, 4, "0.50"),
    ]
    output = run_backtest(
        {"YES": history, "NO": history},
        resolved_outcome="YES",
        fee_rate=Decimal("0"),
        config=BreakoutBacktestConfig(
            breakout_window_minutes=3,
            breakout_threshold="0.01",
            slippage="0",
        ),
    )

    assert output.trades == []
