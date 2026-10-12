"""Validated experiment contracts and a deterministic, resumable simulation plan."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import product
from math import prod
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .engine import SimulationOutput, _decimal, _max_drawdown
from .market import HistoricalDataset, MarketDataError
from .schemas import (
    BacktestConfig,
    BacktestFailure,
    BacktestMetrics,
    BacktestSeriesPoint,
    BacktestStatus,
    BacktestTrade,
    ContractModel,
    MomentumBacktestConfig,
    NonNegativeDecimalString,
    parse_backtest_config,
    strategy_lookback_minutes,
)


class StrategyGrid(ContractModel):
    base_config: BacktestConfig = Field(default_factory=MomentumBacktestConfig)
    parameters: dict[str, list[str | int]] = Field(default_factory=dict, max_length=12)

    @model_validator(mode="after")
    def validate_grid(self) -> StrategyGrid:
        fields = type(self.base_config).model_fields
        allowed = {
            field.alias
            for name, field in fields.items()
            if name not in {"strategy", "start_at", "end_at", "initial_capital"}
        }
        if self.base_config.start_at or self.base_config.end_at:
            raise ValueError("Use experiment periods for dates, not baseConfig dates")
        for key, values in self.parameters.items():
            if key not in allowed:
                raise ValueError(f"Unsupported grid parameter for this strategy: {key}")
            if not values or len(values) > 100:
                raise ValueError("Each grid parameter must contain 1-100 values")
            # Validate individual values without expanding a possibly huge Cartesian product.
            for value in values:
                parse_backtest_config({**self.base_config.model_dump(by_alias=True), key: value})
        return self


class EvaluationPeriod(ContractModel):
    label: str = Field(default="Available history", min_length=1, max_length=80)
    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> EvaluationPeriod:
        for value in (self.start_at, self.end_at):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("Period dates must include a timezone")
        if self.start_at and self.end_at and self.start_at >= self.end_at:
            raise ValueError("Period startAt must precede endAt")
        return self


class WalkForwardConfig(ContractModel):
    folds: int = Field(default=5, ge=2, le=20)
    train_minutes: int | None = Field(default=None, ge=1)
    test_minutes: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def paired_windows(self) -> WalkForwardConfig:
        if (self.train_minutes is None) != (self.test_minutes is None):
            raise ValueError("Supply both trainMinutes and testMinutes")
        return self


class CreateExperimentRequest(ContractModel):
    market_ids: list[str] = Field(min_length=1, max_length=50)
    strategies: list[StrategyGrid] = Field(min_length=1, max_length=30)
    initial_capital: NonNegativeDecimalString = "10000"
    periods: list[EvaluationPeriod] = Field(
        default_factory=lambda: [EvaluationPeriod()], min_length=1, max_length=12
    )
    mode: Literal["grid", "walk_forward"] = "grid"
    walk_forward: WalkForwardConfig = Field(default_factory=WalkForwardConfig)

    @model_validator(mode="after")
    def validate_request(self) -> CreateExperimentRequest:
        if Decimal(self.initial_capital) <= 0:
            raise ValueError("initialCapital must be greater than zero")
        if any(not market.strip() or len(market) > 200 for market in self.market_ids):
            raise ValueError("marketIds must contain nonempty identifiers up to 200 characters")
        self.market_ids = [market.strip() for market in self.market_ids]
        if len(set(self.market_ids)) != len(self.market_ids):
            raise ValueError("marketIds must be unique")
        if len({p.label for p in self.periods}) != len(self.periods):
            raise ValueError("Period labels must be unique")
        return self

    def simulation_count(self) -> int:
        variants = sum(prod(len(v) for v in grid.parameters.values()) for grid in self.strategies)
        per_period = variants if self.mode == "grid" else (variants + 1) * self.walk_forward.folds
        return per_period * len(self.market_ids) * len(self.periods)

    def validate_budget(self, limit: int) -> None:
        count = self.simulation_count()
        if count > limit:
            raise ValueError(f"Experiment needs {count} simulations; the limit is {limit}")


class ExperimentRun(ContractModel):
    experiment_id: UUID
    status: BacktestStatus
    request: CreateExperimentRequest
    total_simulations: int
    completed_simulations: int = 0
    message: str = "Waiting for a worker"
    failure: BacktestFailure | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class CandidateResult(ContractModel):
    candidate_id: str
    config: BacktestConfig
    metrics: BacktestMetrics


class FoldResult(ContractModel):
    fold: int
    train_start_at: datetime
    train_end_at: datetime
    test_start_at: datetime
    test_end_at: datetime
    selected: CandidateResult
    test_metrics: BacktestMetrics


class MarketExperimentResult(ContractModel):
    market_id: str
    market_question: str
    dataset_hash: str
    period: str
    start_at: datetime
    end_at: datetime
    ranking: list[CandidateResult] = Field(default_factory=list)
    folds: list[FoldResult] = Field(default_factory=list)
    out_of_sample: BacktestMetrics | None = None
    series: list[BacktestSeriesPoint] = Field(default_factory=list)


class ExperimentResult(ContractModel):
    markets: list[MarketExperimentResult]
    assumptions: list[str] = Field(
        default_factory=lambda: [
            "Each market has its own capital; results are not a shared portfolio.",
            "Grid rankings are in-sample; only walk-forward test windows are out-of-sample.",
            "Training selects net return after costs, then lower drawdown, then candidate ID.",
            "Windows are start-inclusive and end-exclusive. Indicators use past observations only.",
            "Positions close at each window boundary using the last in-window price and costs.",
            "Out-of-sample cash carries forward; training candidates start with equal capital.",
            "YES/NO benchmarks reinvest across the same test windows with the same boundary costs.",
            "One-minute observations do not model order-book depth, spreads or partial fills.",
            "Markets were selected retrospectively; walk-forward does not remove selection bias.",
        ]
    )


class ExperimentEnvelope(ContractModel):
    experiment: ExperimentRun
    result: ExperimentResult | None = None


class ExperimentList(ContractModel):
    items: list[ExperimentRun]


class SimulationCheckpoint(ContractModel):
    metrics: BacktestMetrics
    trades: list[BacktestTrade] = Field(default_factory=list)
    series: list[BacktestSeriesPoint] = Field(default_factory=list)

    @classmethod
    def from_output(cls, output: SimulationOutput, keep_path: bool) -> SimulationCheckpoint:
        return cls(
            metrics=output.metrics,
            trades=output.trades if keep_path else [],
            series=output.series if keep_path else [],
        )


@dataclass(frozen=True)
class SimulationJob:
    key: str
    market_id: str
    config: BacktestConfig
    keep_path: bool = False


def expand_candidates(request: CreateExperimentRequest) -> list[tuple[str, BacktestConfig]]:
    candidates = []
    for grid_index, grid in enumerate(request.strategies):
        keys = sorted(grid.parameters)
        for variant_index, values in enumerate(product(*(grid.parameters[key] for key in keys))):
            config = parse_backtest_config(
                {
                    **grid.base_config.model_dump(by_alias=True),
                    **dict(zip(keys, values, strict=True)),
                    "initialCapital": request.initial_capital,
                }
            )
            candidates.append((f"{grid_index:03d}-{variant_index:06d}", config))
    return candidates


def rank_candidates(candidates: list[CandidateResult]) -> list[CandidateResult]:
    return sorted(
        candidates,
        key=lambda c: (
            -Decimal(c.metrics.return_pct),
            Decimal(c.metrics.max_drawdown_pct),
            c.candidate_id,
        ),
    )


def period_bounds(
    dataset: HistoricalDataset, period: EvaluationPeriod
) -> tuple[datetime, datetime]:
    first = max(points[0].timestamp for points in dataset.histories.values())
    last = min(points[-1].timestamp for points in dataset.histories.values())
    start, end = period.start_at or first, period.end_at or (last + timedelta(microseconds=1))
    # Explicit ranges are never silently shortened to whatever the provider returned.
    tolerance = timedelta(minutes=5)
    if start < first - tolerance or end > last + tolerance or start >= end:
        raise MarketDataError("INSUFFICIENT_HISTORY", f"Unavailable history for {period.label}")
    return start, end


def fold_windows(start: datetime, end: datetime, config: WalkForwardConfig):
    train = timedelta(minutes=config.train_minutes) if config.train_minutes else (end - start) / 2
    test = (
        timedelta(minutes=config.test_minutes)
        if config.test_minutes
        else (end - start - train) / config.folds
    )
    if train + test * config.folds > end - start + timedelta(microseconds=10):
        raise MarketDataError("INSUFFICIENT_HISTORY", "Walk-forward windows exceed the period")
    for index in range(config.folds):
        train_start = start + test * index
        train_end = train_start + train
        yield train_start, train_end, min(end, train_end + test)


def validate_window(
    dataset: HistoricalDataset, start: datetime, end: datetime, config: BacktestConfig
) -> None:
    window = timedelta(minutes=strategy_lookback_minutes(config))
    tolerance = timedelta(minutes=config.max_fill_delay_minutes)
    for outcome, points in dataset.histories.items():
        active = [p for p in points if start <= p.timestamp < end]
        if (
            len(active) < 2
            or active[0].timestamp - start > tolerance
            or end - active[-1].timestamp > tolerance
        ):
            raise MarketDataError(
                "INSUFFICIENT_HISTORY", f"{outcome} lacks window boundary history"
            )
        prior = [p for p in points if p.timestamp < start]
        target = start - window
        has_warmup = any(timedelta(0) <= target - p.timestamp <= tolerance for p in prior)
        if not has_warmup and active[-1].timestamp - active[0].timestamp < window + timedelta(
            minutes=1
        ):
            raise MarketDataError(
                "INSUFFICIENT_HISTORY", f"{outcome} lacks indicator warm-up history"
            )
        # A gap is not a flat price or a valid fill. Fail explicitly for incomplete experiments.
        relevant = [p for p in points if start - window - tolerance <= p.timestamp < end]
        if any(
            b.timestamp - a.timestamp > tolerance
            for a, b in zip(relevant, relevant[1:], strict=False)
        ):
            raise MarketDataError(
                "INSUFFICIENT_HISTORY", f"{outcome} has gaps exceeding fill tolerance"
            )


def experiment_plan(
    request: CreateExperimentRequest, datasets: dict[str, HistoricalDataset]
) -> Generator[SimulationJob, SimulationCheckpoint, ExperimentResult]:
    candidates = expand_candidates(request)
    results = []
    validated_windows: set[tuple[str, datetime, datetime, int, int]] = set()

    def ensure_window(dataset, start, end, config):
        key = (
            dataset.dataset_hash,
            start,
            end,
            strategy_lookback_minutes(config),
            config.max_fill_delay_minutes,
        )
        if key not in validated_windows:
            validate_window(dataset, start, end, config)
            validated_windows.add(key)

    for market_index, market_id in enumerate(request.market_ids):
        dataset = datasets[market_id]
        for period_index, period in enumerate(request.periods):
            start, end = period_bounds(dataset, period)
            result = MarketExperimentResult(
                market_id=market_id,
                market_question=dataset.snapshot.question,
                dataset_hash=dataset.dataset_hash,
                period=period.label,
                start_at=start,
                end_at=end,
            )
            prefix = f"{market_index}:{period_index}"
            if request.mode == "grid":
                for candidate_id, config in candidates:
                    ensure_window(dataset, start, end, config)
                    bounded = config.model_copy(update={"start_at": start, "end_at": end})
                    output = yield SimulationJob(
                        f"{prefix}:grid:{candidate_id}", market_id, bounded
                    )
                    result.ranking.append(
                        CandidateResult(
                            candidate_id=candidate_id, config=bounded, metrics=output.metrics
                        )
                    )
                result.ranking = rank_candidates(result.ranking)
            else:
                tests = []
                cash = request.initial_capital
                for fold, (train_start, train_end, test_end) in enumerate(
                    fold_windows(start, end, request.walk_forward)
                ):
                    ranked = []
                    for candidate_id, config in candidates:
                        ensure_window(dataset, train_start, train_end, config)
                        ensure_window(dataset, train_end, test_end, config)
                        bounded = config.model_copy(
                            update={"start_at": train_start, "end_at": train_end}
                        )
                        output = yield SimulationJob(
                            f"{prefix}:{fold}:train:{candidate_id}", market_id, bounded
                        )
                        ranked.append(
                            CandidateResult(
                                candidate_id=candidate_id, config=bounded, metrics=output.metrics
                            )
                        )
                    selected = rank_candidates(ranked)[0]
                    bounded = selected.config.model_copy(
                        update={
                            "start_at": train_end,
                            "end_at": test_end,
                            "initial_capital": cash,
                        }
                    )
                    output = yield SimulationJob(f"{prefix}:{fold}:test", market_id, bounded, True)
                    tests.append(output)
                    cash = output.metrics.final_equity
                    result.folds.append(
                        FoldResult(
                            fold=fold + 1,
                            train_start_at=train_start,
                            train_end_at=train_end,
                            test_start_at=train_end,
                            test_end_at=test_end,
                            selected=selected,
                            test_metrics=output.metrics,
                        )
                    )
                result.out_of_sample = combined_metrics(tests)
                series = [point for output in tests for point in output.series]
                stride = max(1, (len(series) + 998) // 999)
                result.series = series[::stride]
                if series and result.series[-1] != series[-1]:
                    result.series.append(series[-1])
            results.append(result)
    return ExperimentResult(markets=results)


def combined_metrics(outputs: list[SimulationCheckpoint]) -> BacktestMetrics:
    first, last = outputs[0].metrics, outputs[-1].metrics
    initial, final = Decimal(first.initial_capital), Decimal(last.final_equity)
    trades = [trade for output in outputs for trade in output.trades]
    series = [point for output in outputs for point in output.series]
    wins = [Decimal(t.pnl) for t in trades if Decimal(t.pnl) > 0]
    losses = [Decimal(t.pnl) for t in trades if Decimal(t.pnl) < 0]
    held = sum((Decimal(str((t.exit_at - t.entry_at).total_seconds())) for t in trades), Decimal(0))
    duration = Decimal(str(max(1, (series[-1].timestamp - series[0].timestamp).total_seconds())))
    benchmarks = {}
    for field in ("yes_buy_hold_return_pct", "no_buy_hold_return_pct"):
        growth = Decimal(1)
        for output in outputs:
            growth *= 1 + Decimal(getattr(output.metrics, field)) / 100
        benchmarks[field] = _decimal((growth - 1) * 100)
    return BacktestMetrics(
        initial_capital=first.initial_capital,
        final_equity=last.final_equity,
        pnl=_decimal(final - initial),
        return_pct=_decimal((final - initial) / initial * 100),
        max_drawdown_pct=_decimal(
            _max_drawdown([initial, *(Decimal(p.equity) for p in series)]) * 100
        ),
        trade_count=len(trades),
        win_rate_pct=_decimal(Decimal(len(wins)) / len(trades) * 100) if trades else "0",
        profit_factor=_decimal(sum(wins, Decimal(0)) / abs(sum(losses))) if losses else None,
        average_holding_seconds=_decimal(held / len(trades)) if trades else "0",
        exposure_pct=_decimal(min(Decimal(1), held / duration) * 100),
        fees=_decimal(sum((Decimal(o.metrics.fees) for o in outputs), Decimal(0))),
        skipped_signals=sum(o.metrics.skipped_signals for o in outputs),
        **benchmarks,
    )
