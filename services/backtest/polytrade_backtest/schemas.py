from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


NonNegativeDecimalString = Annotated[
    str,
    StringConstraints(pattern=r"^(0|[1-9]\d*)(\.\d{1,8})?$"),
]
SignedDecimalString = Annotated[
    str,
    StringConstraints(pattern=r"^-?(0|[1-9]\d*)(\.\d{1,8})?$"),
]

BacktestStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
BacktestPhase = Literal[
    "queued", "fetching", "simulating", "saving", "completed", "failed", "cancelled"
]
Outcome = Literal["YES", "NO"]


class BaseBacktestConfig(ContractModel):
    initial_capital: NonNegativeDecimalString = "10000"
    position_size_pct: NonNegativeDecimalString = "0.10"
    take_profit: NonNegativeDecimalString = "0.10"
    stop_loss: NonNegativeDecimalString = "0.05"
    max_hold_minutes: int = Field(default=1_440, ge=1, le=43_200)
    cooldown_minutes: int = Field(default=60, ge=0, le=43_200)
    slippage: NonNegativeDecimalString = "0.01"
    max_fill_delay_minutes: int = Field(default=5, ge=1, le=60)
    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def validate_values(self) -> "BaseBacktestConfig":
        if Decimal(self.initial_capital) <= 0:
            raise ValueError("initialCapital must be greater than zero")
        if not Decimal("0") < Decimal(self.position_size_pct) <= Decimal("1"):
            raise ValueError("positionSizePct must be greater than zero and at most one")
        for name in ("take_profit", "stop_loss", "slippage"):
            if Decimal(getattr(self, name)) > Decimal("1"):
                raise ValueError(f"{to_camel(name)} must be at most one")
        for name in ("start_at", "end_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{to_camel(name)} must include a timezone")
        if self.start_at and self.end_at and self.start_at >= self.end_at:
            raise ValueError("startAt must be earlier than endAt")
        return self


class MomentumBacktestConfig(BaseBacktestConfig):
    strategy: Literal["momentum_v1"] = "momentum_v1"
    momentum_window_minutes: int = Field(default=60, ge=1, le=1_440)
    momentum_threshold: NonNegativeDecimalString = "0.05"

    @model_validator(mode="after")
    def validate_momentum(self) -> "MomentumBacktestConfig":
        if Decimal(self.momentum_threshold) > Decimal("1"):
            raise ValueError("momentumThreshold must be at most one")
        return self


class MeanReversionBacktestConfig(BaseBacktestConfig):
    strategy: Literal["mean_reversion_v1"] = "mean_reversion_v1"
    reversion_window_minutes: int = Field(default=60, ge=1, le=1_440)
    reversion_threshold: NonNegativeDecimalString = "0.05"

    @model_validator(mode="after")
    def validate_reversion(self) -> "MeanReversionBacktestConfig":
        threshold = Decimal(self.reversion_threshold)
        if threshold <= 0 or threshold > Decimal("1"):
            raise ValueError("reversionThreshold must be greater than zero and at most one")
        return self


class BreakoutBacktestConfig(BaseBacktestConfig):
    strategy: Literal["breakout_v1"] = "breakout_v1"
    breakout_window_minutes: int = Field(default=240, ge=1, le=1_440)
    breakout_threshold: NonNegativeDecimalString = "0.02"

    @model_validator(mode="after")
    def validate_breakout(self) -> "BreakoutBacktestConfig":
        threshold = Decimal(self.breakout_threshold)
        if threshold <= 0 or threshold > Decimal("1"):
            raise ValueError("breakoutThreshold must be greater than zero and at most one")
        return self


type TaggedBacktestConfig = Annotated[
    MomentumBacktestConfig | MeanReversionBacktestConfig | BreakoutBacktestConfig,
    Field(discriminator="strategy"),
]


def _default_backtest_strategy(value: object) -> object:
    if isinstance(value, Mapping) and "strategy" not in value:
        return {"strategy": "momentum_v1", **value}
    return value


type BacktestConfig = Annotated[
    TaggedBacktestConfig,
    BeforeValidator(_default_backtest_strategy),
]
BACKTEST_CONFIG_ADAPTER = TypeAdapter(BacktestConfig)


def parse_backtest_config(value: object) -> BacktestConfig:
    return BACKTEST_CONFIG_ADAPTER.validate_python(value)


def strategy_lookback_minutes(config: BacktestConfig) -> int:
    if config.strategy == "momentum_v1":
        return config.momentum_window_minutes
    if config.strategy == "mean_reversion_v1":
        return config.reversion_window_minutes
    return config.breakout_window_minutes


class CreateBacktestRequest(ContractModel):
    market_id: str = Field(min_length=1, max_length=200)
    config: BacktestConfig = Field(default_factory=MomentumBacktestConfig)


class BacktestFailure(ContractModel):
    code: str
    message: str


class BacktestRun(ContractModel):
    run_id: UUID
    market_id: str
    market_question: str | None = None
    status: BacktestStatus
    phase: BacktestPhase
    progress: int = Field(ge=0, le=100)
    config: BacktestConfig
    resolved_outcome: Outcome | None = None
    dataset_hash: str | None = None
    cancel_requested: bool = False
    failure: BacktestFailure | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class BacktestMetrics(ContractModel):
    initial_capital: NonNegativeDecimalString
    final_equity: NonNegativeDecimalString
    pnl: SignedDecimalString
    return_pct: SignedDecimalString
    max_drawdown_pct: NonNegativeDecimalString
    trade_count: int = Field(ge=0)
    win_rate_pct: NonNegativeDecimalString
    profit_factor: NonNegativeDecimalString | None = None
    average_holding_seconds: NonNegativeDecimalString
    exposure_pct: NonNegativeDecimalString
    fees: NonNegativeDecimalString
    skipped_signals: int = Field(ge=0)
    yes_buy_hold_return_pct: SignedDecimalString
    no_buy_hold_return_pct: SignedDecimalString


class BacktestResult(ContractModel):
    metrics: BacktestMetrics
    assumptions: list[str]


class BacktestTrade(ContractModel):
    trade_index: int = Field(ge=0)
    outcome: Outcome
    entry_at: datetime
    exit_at: datetime
    entry_price: NonNegativeDecimalString
    exit_price: NonNegativeDecimalString
    shares: NonNegativeDecimalString
    entry_fee: NonNegativeDecimalString
    exit_fee: NonNegativeDecimalString
    pnl: SignedDecimalString
    exit_reason: Literal["take_profit", "stop_loss", "max_hold", "settlement"]


class BacktestSeriesPoint(ContractModel):
    timestamp: datetime
    yes_price: NonNegativeDecimalString | None = None
    no_price: NonNegativeDecimalString | None = None
    equity: NonNegativeDecimalString


class BacktestSeriesResponse(ContractModel):
    run_id: UUID
    points: list[BacktestSeriesPoint]


class BacktestTradesResponse(ContractModel):
    run_id: UUID
    items: list[BacktestTrade]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)


class BacktestRunEnvelope(ContractModel):
    run: BacktestRun
    result: BacktestResult | None = None


class BacktestRunList(ContractModel):
    items: list[BacktestRun]
    active_count: int = Field(ge=0)
    active_limit: int = Field(ge=1)


class BacktestProgressEvent(ContractModel):
    phase: BacktestPhase
    progress: int = Field(ge=0, le=100)
    message: str
    created_at: datetime
