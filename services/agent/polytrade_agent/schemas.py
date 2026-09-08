from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


def require_positive(value: str) -> str:
    if Decimal(value) <= 0:
        raise ValueError("value must be greater than zero")
    return value


def require_price(value: str) -> str:
    if Decimal(value) > 1:
        raise ValueError("price must be at most one")
    return value


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )

DecimalString = Annotated[
    str,
    StringConstraints(pattern=r"^(0|[1-9]\d*)(\.\d{1,6})?$"),
]
PositiveDecimalString = Annotated[
    DecimalString,
    AfterValidator(require_positive),
]
PriceString = Annotated[
    PositiveDecimalString,
    AfterValidator(require_price),
]


class ProposalBase(ContractModel):

    token_id: Annotated[str, StringConstraints(pattern=r"^\d+$")]
    market_id: str = Field(min_length=1, max_length=200)
    market_question: str = Field(min_length=1, max_length=1_000)
    outcome: str = Field(min_length=1, max_length=200)
    side: Literal["BUY", "SELL"]
    rationale: str = Field(default="", max_length=2_000)
    observed_at: datetime


class RestingOrderProposal(ProposalBase):
    action: Literal["create"] = "create"
    execution: Literal["GTC", "GTD"]
    price: PriceString
    size: PositiveDecimalString
    expiration: int | None = Field(default=None, gt=0)
    post_only: bool = False

    @model_validator(mode="after")
    def validate_expiration(self) -> "RestingOrderProposal":
        if self.execution == "GTD" and self.expiration is None:
            raise ValueError("GTD requires expiration")
        if self.execution == "GTC" and self.expiration is not None:
            raise ValueError("GTC cannot include expiration")
        return self


class ImmediateOrderProposal(ProposalBase):
    action: Literal["create"] = "create"
    execution: Literal["FOK", "FAK"]
    amount: PositiveDecimalString
    limit_price: PriceString
    post_only: Literal[False] = False


class OrderCancellation(ContractModel):

    kind: Literal["order"]
    order_id: str = Field(min_length=1, max_length=200)


class MarketCancellation(ContractModel):

    kind: Literal["market"]
    market_id: str = Field(min_length=1, max_length=200)
    token_id: Annotated[str, StringConstraints(pattern=r"^\d+$")] | None = None


class AllCancellation(ContractModel):

    kind: Literal["all"]


CancellationSelector = Annotated[
    OrderCancellation | MarketCancellation | AllCancellation,
    Field(discriminator="kind"),
]


class CancellationProposal(ContractModel):

    action: Literal["cancel"] = "cancel"
    selector: CancellationSelector
    rationale: str = Field(default="", max_length=2_000)
    observed_at: datetime


TradingActionProposal = RestingOrderProposal | ImmediateOrderProposal | CancellationProposal


class TradingActionInput(ContractModel):
    proposal: TradingActionProposal


class UnsignedProposalEnvelope(ContractModel):
    kind: Literal["unsigned_trading_action_proposal"] = "unsigned_trading_action_proposal"
    proposal: TradingActionProposal
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    warning: str = (
        "This is an unsigned draft, not a paper or real order. Review it in the client; "
        "a new order exists only after the wallet signs and the gateway accepts it."
    )


class ThreadResponse(ContractModel):
    thread_id: UUID
    title: str = Field(min_length=1, max_length=80)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class ThreadListResponse(ContractModel):
    items: list[ThreadResponse]


class AgentRunRequest(ContractModel):
    message: str = Field(min_length=1, max_length=32_000)


class PublicMessage(ContractModel):
    kind: Literal["message"] = "message"
    id: str = Field(min_length=1, max_length=200)
    role: Literal["user", "assistant"]
    text: str


class PublicProposal(ContractModel):
    kind: Literal["proposal"] = "proposal"
    id: str = Field(min_length=1, max_length=200)
    envelope: UnsignedProposalEnvelope


class BacktestRunReference(ContractModel):
    kind: Literal["backtest_run"] = "backtest_run"
    run_id: UUID
    market_id: str
    market_question: str | None = None
    strategy: Literal["momentum_v1", "mean_reversion_v1", "breakout_v1"] = "momentum_v1"
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    phase: Literal[
        "queued", "fetching", "simulating", "saving", "completed", "failed", "cancelled"
    ]
    progress: int = Field(ge=0, le=100)
    created_at: datetime


class PublicBacktest(ContractModel):
    kind: Literal["backtest"] = "backtest"
    id: str = Field(min_length=1, max_length=200)
    backtest: BacktestRunReference


PublicThreadItem = Annotated[
    PublicMessage | PublicProposal | PublicBacktest,
    Field(discriminator="kind"),
]


class ThreadMessagesResponse(ContractModel):
    thread_id: UUID
    items: list[PublicThreadItem]


ConfidenceString = Annotated[
    str,
    StringConstraints(pattern=r"^(0(\.\d{1,4})?|1(\.0{1,4})?)$"),
]


class PredictionInput(ContractModel):

    condition_id: str = Field(min_length=1, max_length=200)
    token_id: Annotated[str, StringConstraints(pattern=r"^\d+$")] | None = None
    market_question: str = Field(min_length=1, max_length=1_000)
    predicted_outcome: str = Field(min_length=1, max_length=200)
    confidence: ConfidenceString | None = None


class PredictionRecorded(ContractModel):

    prediction_id: UUID
    condition_id: str = Field(min_length=1, max_length=200)
    token_id: Annotated[str, StringConstraints(pattern=r"^\d+$")] | None = None
    market_question: str = Field(min_length=1, max_length=1_000)
    predicted_outcome: str = Field(min_length=1, max_length=200)
    confidence: ConfidenceString | None = None
    status: Literal["PENDING", "GRADED", "VOID"]
    made_at: datetime
    category: str | None = None
