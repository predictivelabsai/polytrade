from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from polytrade_backtest.engine import PriceObservation, run_backtest
from polytrade_backtest.experiments import (
    CreateExperimentRequest,
    EvaluationPeriod,
    SimulationCheckpoint,
    StrategyGrid,
    WalkForwardConfig,
    combined_metrics,
    expand_candidates,
    experiment_plan,
    fold_windows,
)
from polytrade_backtest.market import MarketDataError, MarketSnapshot, build_dataset
from polytrade_backtest.schemas import MomentumBacktestConfig

START = datetime(2026, 5, 1, tzinfo=UTC)


def dataset(*, outcome="YES", mutate_after=None, flat=False):
    histories = {"YES": [], "NO": []}
    for minute in range(1001):
        price = Decimal("0.4") if flat else Decimal("0.4") + Decimal(minute % 40) / 200
        if mutate_after is not None and minute >= mutate_after:
            price = Decimal("0.7") - Decimal(minute % 20) / 100
        for name, value in (("YES", price), ("NO", 1 - price)):
            histories[name].append(PriceObservation(START + timedelta(minutes=minute), value))
    return build_dataset(
        MarketSnapshot(
            "market-1",
            "Example?",
            "yes",
            "no",
            outcome,
            Decimal("0.04"),
            START,
            START + timedelta(days=10),
        ),
        histories,
        request_start_at=None,
        request_end_at=None,
    )


def request(**kwargs):
    return CreateExperimentRequest(
        market_ids=["market-1"],
        strategies=[
            StrategyGrid(
                base_config=MomentumBacktestConfig(momentum_window_minutes=5, slippage="0.01"),
                parameters={"momentumThreshold": ["0.02", "0.05"], "takeProfit": ["0.02", "0.10"]},
            )
        ],
        **kwargs,
    )


def evaluate(req, data):
    plan = experiment_plan(req, {"market-1": data})
    checkpoints = []
    try:
        job = next(plan)
        while True:
            output = run_backtest(
                data.histories,
                config=job.config,
                fee_rate=data.snapshot.fee_rate,
                resolved_outcome=data.snapshot.resolved_outcome,
                settlement_at=data.snapshot.closed_at,
                benchmark_capital=Decimal(req.initial_capital),
            )
            checkpoint = SimulationCheckpoint.from_output(output, job.keep_path)
            checkpoints.append((job, checkpoint))
            job = plan.send(checkpoint)
    except StopIteration as complete:
        return complete.value, checkpoints


def test_grid_expansion_count_and_order_are_deterministic():
    req = request()
    assert req.simulation_count() == 4
    assert expand_candidates(req) == expand_candidates(req)
    assert [c.take_profit for _, c in expand_candidates(req)] == ["0.02", "0.10"] * 2
    req.validate_budget(4)
    with pytest.raises(ValueError, match="4 simulations"):
        req.validate_budget(3)


def test_grid_rejects_unknown_parameters_invalid_values_and_dates():
    for parameters in (
        {"surprise": [1]},
        {"maxHoldMinutes": [0]},
        {"takeProfit": []},
        {"reversionThreshold": ["0.2"]},
        {"initialCapital": ["1"]},
    ):
        with pytest.raises(ValidationError):
            StrategyGrid(parameters=parameters)
    with pytest.raises(ValidationError, match="timezone"):
        EvaluationPeriod(start_at=datetime(2026, 5, 1))
    with pytest.raises(ValidationError, match="both"):
        WalkForwardConfig(train_minutes=10)


def test_huge_grid_is_counted_before_expansion():
    req = CreateExperimentRequest(
        market_ids=["x"],
        strategies=[
            StrategyGrid(
                parameters={
                    "takeProfit": [str(Decimal(i) / 100) for i in range(100)],
                    "stopLoss": [str(Decimal(i) / 100) for i in range(100)],
                }
            )
        ],
    )
    with pytest.raises(ValueError, match="10000"):
        req.validate_budget(1000)


def test_window_closes_without_future_resolution_and_warmup_never_trades():
    data = dataset()
    config = MomentumBacktestConfig(
        start_at=START + timedelta(minutes=20),
        end_at=START + timedelta(minutes=30),
        momentum_window_minutes=5,
        momentum_threshold="0.01",
        take_profit="1",
        stop_loss="1",
        position_size_pct="1",
        slippage="0.01",
    )
    yes = run_backtest(
        data.histories,
        config=config,
        fee_rate=Decimal("0.04"),
        resolved_outcome="YES",
        settlement_at=data.snapshot.closed_at,
    )
    no = run_backtest(
        dataset(mutate_after=30).histories,
        config=config,
        fee_rate=Decimal("0.04"),
        resolved_outcome="NO",
        settlement_at=data.snapshot.closed_at,
    )
    assert yes == no
    assert len(yes.trades) == 1
    trade = yes.trades[0]
    assert trade.entry_at == START + timedelta(minutes=21)
    assert trade.exit_at == START + timedelta(minutes=29)
    assert trade.exit_reason == "window_end"
    assert trade.exit_price == "0.535"
    assert Decimal(trade.entry_fee) > 0 and Decimal(trade.exit_fee) > 0
    assert all(p.timestamp >= config.start_at for p in yes.series)
    assert all(p.timestamp < config.end_at for p in yes.series)


def test_window_benchmark_includes_both_fills_and_costs():
    data = dataset(flat=True)
    config = MomentumBacktestConfig(
        start_at=START,
        end_at=START + timedelta(minutes=10),
        momentum_window_minutes=1,
        momentum_threshold="1",
        slippage="0.01",
    )
    output = run_backtest(
        data.histories,
        config=config,
        fee_rate=Decimal(0),
        resolved_outcome="YES",
        settlement_at=data.snapshot.closed_at,
    )
    assert output.metrics.final_equity == "10000"
    # 10,000 / .41 shares (six decimals); sale at .39, no settlement payoff.
    assert output.metrics.yes_buy_hold_return_pct == "-4.87804878"
    assert output.metrics.no_buy_hold_return_pct == "-3.27868852"


def test_default_folds_use_five_disjoint_test_windows():
    end = START + timedelta(minutes=1000)
    folds = list(fold_windows(START, end, WalkForwardConfig()))
    assert len(folds) == 5
    assert folds[0] == (START, START + timedelta(minutes=500), START + timedelta(minutes=600))
    assert folds[-1][2] == end
    assert all(a[2] == b[1] for a, b in zip(folds, folds[1:], strict=False))
    with pytest.raises(MarketDataError, match="exceed"):
        list(fold_windows(START, end, WalkForwardConfig(train_minutes=600, test_minutes=100)))


def test_walk_forward_selection_ignores_future_prices_and_resolution():
    req = request(
        mode="walk_forward",
        periods=[
            EvaluationPeriod(
                start_at=START,
                end_at=START + timedelta(minutes=1000),
            )
        ],
    )
    first, jobs = evaluate(req, dataset())
    changed, _ = evaluate(req, dataset(outcome="NO", mutate_after=500))
    assert req.simulation_count() == len(jobs) == 25
    assert first.markets[0].folds[0].selected == changed.markets[0].folds[0].selected
    assert first.markets[0].folds[0].test_metrics != changed.markets[0].folds[0].test_metrics
    assert all(p.timestamp >= START + timedelta(minutes=500) for p in first.markets[0].series)
    folds = first.markets[0].folds
    for previous, following in zip(folds, folds[1:], strict=False):
        assert previous.test_metrics.final_equity == following.test_metrics.initial_capital
    metrics = first.markets[0].out_of_sample
    assert metrics.initial_capital == "10000"
    assert metrics.final_equity == folds[-1].test_metrics.final_equity
    assert metrics.trade_count == sum(f.test_metrics.trade_count for f in folds)
    assert Decimal(metrics.fees) == sum(Decimal(f.test_metrics.fees) for f in folds)
    tests = [cp for job, cp in jobs if job.keep_path]
    assert combined_metrics(tests) == metrics


def test_zero_trade_ranking_is_stable_and_not_missing_results():
    result, _ = evaluate(request(), dataset(flat=True))
    ranking = result.markets[0].ranking
    assert [c.candidate_id for c in ranking] == sorted(c.candidate_id for c in ranking)
    assert all(c.metrics.trade_count == 0 and c.metrics.return_pct == "0" for c in ranking)


def test_insufficient_history_and_gaps_are_explicit_errors():
    with pytest.raises(MarketDataError, match="Unavailable"):
        evaluate(request(periods=[EvaluationPeriod(start_at=START - timedelta(days=1))]), dataset())
    with pytest.raises(MarketDataError, match="warm-up"):
        evaluate(
            request(periods=[EvaluationPeriod(end_at=START + timedelta(minutes=2))]), dataset()
        )
    data = dataset()
    data.histories["YES"][100:120] = []
    with pytest.raises(MarketDataError, match="gaps"):
        evaluate(request(), data)


def test_bankrupt_test_cash_stays_zero_and_reports_loss_instead_of_hiding_result():
    data = dataset(flat=True)
    for minute in range(1001):
        price = Decimal("0.4") if minute < 501 else Decimal("0.5") if minute < 503 else Decimal(0)
        data.histories["YES"][minute] = PriceObservation(START + timedelta(minutes=minute), price)
        data.histories["NO"][minute] = PriceObservation(
            START + timedelta(minutes=minute), 1 - price
        )
    from dataclasses import replace

    data = replace(data, snapshot=replace(data.snapshot, fee_rate=Decimal(0)))
    req = CreateExperimentRequest(
        market_ids=["market-1"],
        mode="walk_forward",
        periods=[EvaluationPeriod(start_at=START, end_at=START + timedelta(minutes=1000))],
        strategies=[
            StrategyGrid(
                base_config=MomentumBacktestConfig(
                    momentum_window_minutes=1,
                    momentum_threshold="0.05",
                    slippage="0",
                    position_size_pct="1",
                    take_profit="1",
                    stop_loss="1",
                    max_hold_minutes=1000,
                )
            )
        ],
    )
    result, checkpoints = evaluate(req, data)
    assert len(result.markets[0].folds) == 5
    assert result.markets[0].out_of_sample.return_pct == "-100"
    assert result.markets[0].out_of_sample.max_drawdown_pct == "100"
    assert all(fold.test_metrics.initial_capital == "0" for fold in result.markets[0].folds[1:])
    assert all(fold.test_metrics.trade_count == 0 for fold in result.markets[0].folds[1:])
    assert len(checkpoints) == req.simulation_count()


def test_combined_return_and_drawdown_follow_cash_curve_not_average_fold_returns():
    from polytrade_backtest.schemas import BacktestMetrics, BacktestSeriesPoint

    def checkpoint(initial, final, return_pct, minute):
        return SimulationCheckpoint(
            metrics=BacktestMetrics(
                initial_capital=initial,
                final_equity=final,
                pnl=str(Decimal(final) - Decimal(initial)),
                return_pct=return_pct,
                max_drawdown_pct="0",
                trade_count=0,
                win_rate_pct="0",
                profit_factor=None,
                average_holding_seconds="0",
                exposure_pct="0",
                fees="0",
                skipped_signals=0,
                yes_buy_hold_return_pct=return_pct,
                no_buy_hold_return_pct="0",
            ),
            series=[
                BacktestSeriesPoint(timestamp=START + timedelta(minutes=minute), equity=initial),
                BacktestSeriesPoint(timestamp=START + timedelta(minutes=minute + 1), equity=final),
            ],
        )

    metrics = combined_metrics(
        [checkpoint("100", "120", "20", 0), checkpoint("120", "90", "-25", 2)]
    )
    assert metrics.return_pct == "-10"
    assert metrics.pnl == "-10"
    assert metrics.max_drawdown_pct == "25"
    assert metrics.yes_buy_hold_return_pct == "-10"


def test_shorter_cached_lookback_is_not_reused_for_longer_warmup():
    from polytrade_backtest.market import dataset_covers_warmup

    data = dataset()
    config = MomentumBacktestConfig(
        start_at=START + timedelta(minutes=100), momentum_window_minutes=60
    )
    assert dataset_covers_warmup(data, config)
    for outcome in ("YES", "NO"):
        data.histories[outcome] = data.histories[outcome][90:]
    assert not dataset_covers_warmup(data, config)
    assert dataset_covers_warmup(data, config.model_copy(update={"momentum_window_minutes": 5}))
