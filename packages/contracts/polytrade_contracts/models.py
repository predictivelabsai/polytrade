"""Pydantic equivalents of the original browser/gateway Zod contracts.

Field names intentionally remain camelCase: they are the public JSON contract,
and keeping them verbatim makes server-rendered views and API responses share a
single representation without a second alias layer.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class StrictContractModel(ContractModel):
    model_config = ConfigDict(extra="forbid")


def _matches(pattern: str, message: str):
    compiled = re.compile(pattern)

    def validate(value: str) -> str:
        if not compiled.fullmatch(value):
            raise ValueError(message)
        return value

    return validate


def _positive(value: str) -> str:
    if float(value) <= 0:
        raise ValueError("Value must be greater than zero")
    return value


def _price(value: str) -> str:
    if float(value) > 1:
        raise ValueError("Price must be at most 1")
    return value


def _datetime_string(value: str) -> str:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Input should be a valid datetime") from exc
    return value


DecimalString = Annotated[
    str,
    AfterValidator(
        _matches(
            r"(0|[1-9]\d*)(\.\d{1,6})?",
            "Use a non-negative decimal string with at most 6 decimal places",
        )
    ),
]
PositiveDecimalString = Annotated[DecimalString, AfterValidator(_positive)]
SignedDecimalString = Annotated[
    str,
    AfterValidator(
        _matches(
            r"-?(0|[1-9]\d*)(\.\d{1,6})?", "Use a decimal string with at most 6 decimal places"
        )
    ),
]
PriceString = Annotated[PositiveDecimalString, AfterValidator(_price)]
BacktestDecimalString = Annotated[
    str,
    AfterValidator(_matches(r"(0|[1-9]\d*)(\.\d{1,8})?", "Use a non-negative decimal string")),
]
SignedBacktestDecimalString = Annotated[
    str,
    AfterValidator(_matches(r"-?(0|[1-9]\d*)(\.\d{1,8})?", "Use a decimal string")),
]
EvmAddress = Annotated[str, AfterValidator(_matches(r"0x[a-fA-F0-9]{40}", "Invalid EVM address"))]
HexSignature = Annotated[
    str, AfterValidator(_matches(r"0x[a-fA-F0-9]+", "Invalid hexadecimal signature"))
]
TokenId = Annotated[str, AfterValidator(_matches(r"\d+", "Token ID must be an unsigned integer"))]
DateTimeString = Annotated[str, AfterValidator(_datetime_string)]
ConfidenceString = Annotated[
    str,
    AfterValidator(
        _matches(
            r"(0(\.\d{1,4})?|1(\.0{1,4})?)",
            "Use a decimal string between 0 and 1 with at most 4 decimal places",
        )
    ),
]

Side = Literal["BUY", "SELL"]
OrderType = Literal["GTC", "GTD", "FOK", "FAK"]
SignatureType = Literal[0, 1, 2, 3]


class RestingOrderProposal(ContractModel):
    action: Literal["create"]
    execution: Literal["GTC", "GTD"]
    tokenId: TokenId
    marketId: str = Field(min_length=1, max_length=200)
    marketQuestion: str = Field(min_length=1, max_length=1_000)
    outcome: str = Field(min_length=1, max_length=200)
    side: Side
    rationale: str = Field(default="", max_length=2_000)
    observedAt: DateTimeString
    price: PriceString
    size: PositiveDecimalString
    expiration: int | None = Field(default=None, gt=0)
    postOnly: bool = False

    @model_validator(mode="after")
    def validate_expiration(self):
        if self.execution == "GTD" and self.expiration is None:
            raise ValueError("GTD requires expiration")
        if self.execution == "GTC" and self.expiration is not None:
            raise ValueError("GTC cannot include expiration")
        return self


class ImmediateOrderProposal(ContractModel):
    action: Literal["create"]
    execution: Literal["FOK", "FAK"]
    tokenId: TokenId
    marketId: str = Field(min_length=1, max_length=200)
    marketQuestion: str = Field(min_length=1, max_length=1_000)
    outcome: str = Field(min_length=1, max_length=200)
    side: Side
    rationale: str = Field(default="", max_length=2_000)
    observedAt: DateTimeString
    amount: PositiveDecimalString
    limitPrice: PriceString
    postOnly: Literal[False] = False


class OrderCancellationSelector(ContractModel):
    kind: Literal["order"]
    orderId: str = Field(min_length=1, max_length=200)


class MarketCancellationSelector(ContractModel):
    kind: Literal["market"]
    marketId: str = Field(min_length=1, max_length=200)
    tokenId: TokenId | None = None


class AllCancellationSelector(ContractModel):
    kind: Literal["all"]


CancellationSelector = Annotated[
    OrderCancellationSelector | MarketCancellationSelector | AllCancellationSelector,
    Field(discriminator="kind"),
]
CreateOrderProposal = Annotated[
    RestingOrderProposal | ImmediateOrderProposal,
    Field(discriminator="execution"),
]


class CancellationProposal(ContractModel):
    action: Literal["cancel"]
    selector: CancellationSelector
    rationale: str = Field(default="", max_length=2_000)
    observedAt: DateTimeString


TradingActionProposal = RestingOrderProposal | ImmediateOrderProposal | CancellationProposal


class WalletChallengeRequest(ContractModel):
    walletAddress: EvmAddress
    signatureType: SignatureType = 0
    funderAddress: EvmAddress | None = None


class WalletSessionRequest(ContractModel):
    challengeId: UUID
    signature: HexSignature


class TypedDataField(ContractModel):
    name: str
    type: str


class TypedData(ContractModel):
    domain: dict[str, Any]
    types: dict[str, list[TypedDataField]]
    primaryType: str = Field(min_length=1)
    message: dict[str, Any]


class WalletChallengeResponse(ContractModel):
    challengeId: UUID
    expiresAt: DateTimeString
    typedData: TypedData


class WalletSessionStatus(ContractModel):
    sessionId: UUID
    walletAddress: EvmAddress
    funderAddress: EvmAddress | None = None
    signatureType: SignatureType
    idleExpiresAt: DateTimeString
    expiresAt: DateTimeString


WalletSessionResponse = WalletSessionStatus


class CreateIntentRequest(ContractModel):
    sessionId: UUID
    proposal: CreateOrderProposal


class SubmitIntentRequest(ContractModel):
    signature: HexSignature


class CancelRequest(ContractModel):
    sessionId: UUID
    selector: CancellationSelector
    confirmed: Literal[True]


class AccountPosition(ContractModel):
    positionId: str = Field(min_length=1)
    conditionId: str | None
    assetId: str | None
    marketTitle: str | None
    outcome: str | None
    size: str | None
    averagePrice: str | None
    currentPrice: str | None
    currentValue: str | None
    cashPnl: str | None
    percentPnl: str | None
    redeemable: bool


class AccountOrder(ContractModel):
    orderId: str = Field(min_length=1)
    marketId: str | None
    assetId: str | None
    outcome: str | None
    side: str | None
    originalSize: str | None
    matchedSize: str | None
    remainingSize: str | None
    price: str | None
    orderType: str | None
    status: str | None
    createdAt: DateTimeString | None
    expiration: DateTimeString | None


class AccountFill(ContractModel):
    tradeId: str = Field(min_length=1)
    marketId: str | None
    assetId: str | None
    outcome: str | None
    side: str | None
    size: str | None
    price: str | None
    status: str | None
    matchedAt: DateTimeString | None
    traderSide: str | None
    transactionHash: str | None


class AccountOverview(ContractModel):
    walletAddress: EvmAddress
    funderAddress: EvmAddress | None = None
    positions: list[AccountPosition]
    openOrders: list[AccountOrder]
    fills: list[AccountFill]
    observedAt: DateTimeString


class MarketSearchMarket(ContractModel):
    id: str
    conditionId: str
    slug: str
    question: str
    description: str
    outcomes: list[str]
    outcomePrices: list[str]
    clobTokenIds: list[str]
    active: bool
    closed: bool
    acceptingOrders: bool
    enableOrderBook: bool
    archived: bool
    restricted: bool
    minimumOrderSize: str
    minimumTickSize: str
    endDate: DateTimeString | None
    startDate: DateTimeString | None
    createdAt: DateTimeString | None
    closedTime: DateTimeString | None
    liquidity: str
    volume: str


class MarketSearchEvent(ContractModel):
    id: str
    slug: str
    title: str
    description: str
    endDate: DateTimeString | None
    liquidity: str
    volume: str
    markets: list[MarketSearchMarket]


class MarketSearchResponse(ContractModel):
    query: str
    state: Literal["active", "resolved"]
    observedAt: DateTimeString
    events: list[MarketSearchEvent]


class PublicMarket(MarketSearchMarket):
    icon: str | None = None
    volume24hr: str | None = None


class PublicMarketSummary(ContractModel):
    id: str
    conditionId: str
    slug: str
    question: str
    outcomes: list[str]
    outcomePrices: list[str]
    clobTokenIds: list[str]
    active: bool
    closed: bool
    acceptingOrders: bool
    endDate: DateTimeString | None
    liquidity: str
    volume: str
    icon: str | None = None
    volume24hr: str | None = None


class PublicOutcomeQuote(ContractModel):
    outcome: str
    tokenId: TokenId
    price: str | None
    bestBid: str | None
    bestAsk: str | None
    source: Literal["order-book", "gamma"]


class PublicMarketDetail(ContractModel):
    market: PublicMarket
    quotes: list[PublicOutcomeQuote]
    observedAt: DateTimeString


class PublicMarketListResponse(ContractModel):
    markets: list[PublicMarketSummary]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    hasMore: bool
    observedAt: DateTimeString


class PublicOrderBookLevel(ContractModel):
    price: str
    size: str


class PublicOrderBook(ContractModel):
    tokenId: TokenId
    minimumOrderSize: str
    tickSize: str
    negativeRisk: bool
    lastTradePrice: str | None
    bids: list[PublicOrderBookLevel]
    asks: list[PublicOrderBookLevel]
    observedAt: DateTimeString


class PublicPriceHistoryPoint(ContractModel):
    timestamp: int
    price: str


class PublicPriceHistory(ContractModel):
    tokenId: TokenId
    interval: Literal["1h", "6h", "1d", "1w", "max"]
    points: list[PublicPriceHistoryPoint]
    observedAt: DateTimeString


class PaperQuoteRequest(ContractModel):
    conditionId: str = Field(min_length=1, max_length=200)
    tokenId: TokenId
    side: Side
    shares: PositiveDecimalString


class PaperOrderRequest(PaperQuoteRequest):
    limitPrice: PriceString


class PaperQuote(ContractModel):
    conditionId: str = Field(min_length=1, max_length=200)
    tokenId: TokenId
    marketQuestion: str = Field(min_length=1, max_length=1_000)
    outcome: str = Field(min_length=1, max_length=200)
    side: Side
    shares: PositiveDecimalString
    averagePrice: PriceString
    limitPrice: PriceString
    grossNotional: DecimalString
    feeRate: DecimalString
    fee: DecimalString
    cashEffect: SignedDecimalString
    observedAt: DateTimeString


class PaperPosition(ContractModel):
    conditionId: str = Field(min_length=1, max_length=200)
    tokenId: TokenId
    marketQuestion: str = Field(min_length=1, max_length=1_000)
    outcome: str = Field(min_length=1, max_length=200)
    shares: PositiveDecimalString
    costBasis: DecimalString
    averageCost: DecimalString
    bestBid: PriceString | None
    liquidationValue: DecimalString
    unrealizedPnl: SignedDecimalString
    markStatus: Literal["current", "stale", "unpriced"]
    markedAt: DateTimeString | None


class PaperFill(ContractModel):
    fillId: UUID
    kind: Literal["BUY", "SELL", "SETTLEMENT"]
    conditionId: str = Field(min_length=1, max_length=200)
    tokenId: TokenId
    marketQuestion: str = Field(min_length=1, max_length=1_000)
    outcome: str = Field(min_length=1, max_length=200)
    shares: PositiveDecimalString
    averagePrice: DecimalString
    grossNotional: DecimalString
    feeRate: DecimalString
    fee: DecimalString
    cashEffect: SignedDecimalString
    realizedPnl: SignedDecimalString
    observedAt: DateTimeString
    createdAt: DateTimeString


class PaperPortfolio(ContractModel):
    initialCash: DecimalString
    cash: DecimalString
    positionsValue: DecimalString
    equity: DecimalString
    realizedPnl: SignedDecimalString
    unrealizedPnl: SignedDecimalString
    totalPnl: SignedDecimalString
    totalFees: DecimalString
    positions: list[PaperPosition]
    warnings: list[str]
    observedAt: DateTimeString


class PaperOrderResponse(ContractModel):
    fill: PaperFill
    portfolio: PaperPortfolio


class PaperFillsResponse(ContractModel):
    items: list[PaperFill]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)


class PaperShareStatus(ContractModel):
    token: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{32,64}$")] | None
    enabled: bool
    createdAt: DateTimeString | None
    updatedAt: DateTimeString | None


class PaperShareManageRequest(ContractModel):
    rotate: bool = False


class PublicTrackRecordStats(ContractModel):
    initialCash: DecimalString
    cash: DecimalString
    equity: DecimalString
    totalPnl: SignedDecimalString
    realizedPnl: SignedDecimalString
    unrealizedPnl: SignedDecimalString
    totalFees: DecimalString
    tradeCount: int = Field(ge=0)
    winRate: str | None


class PublicTrackRecordPoint(ContractModel):
    t: DateTimeString
    equity: DecimalString


class PublicTrackRecordProfile(ContractModel):
    displayName: str = Field(min_length=1, max_length=80)
    startedAt: DateTimeString


class PublicTrackRecordPosition(ContractModel):
    marketQuestion: str = Field(min_length=1, max_length=1_000)
    outcome: str = Field(min_length=1, max_length=200)
    shares: PositiveDecimalString
    averageCost: DecimalString
    liquidationValue: DecimalString
    unrealizedPnl: SignedDecimalString
    markStatus: Literal["current", "stale", "unpriced"]


class PublicTrackRecordFill(ContractModel):
    fillId: UUID
    kind: Literal["BUY", "SELL", "SETTLEMENT"]
    marketQuestion: str = Field(min_length=1, max_length=1_000)
    outcome: str = Field(min_length=1, max_length=200)
    shares: PositiveDecimalString
    averagePrice: DecimalString
    fee: DecimalString
    cashEffect: SignedDecimalString
    realizedPnl: SignedDecimalString
    createdAt: DateTimeString


class PublicTrackRecord(ContractModel):
    profile: PublicTrackRecordProfile
    stats: PublicTrackRecordStats
    equityCurve: list[PublicTrackRecordPoint] = Field(max_length=501)
    positions: list[PublicTrackRecordPosition] = Field(max_length=100)
    fills: list[PublicTrackRecordFill] = Field(max_length=50)
    observedAt: DateTimeString


class PaperStrategyStartRequest(ContractModel):
    conditionId: str = Field(min_length=1, max_length=200)
    tokenId: TokenId
    entryPrice: PriceString
    exitPrice: PriceString
    sharesPerOrder: PositiveDecimalString
    maxPosition: PositiveDecimalString
    intervalSeconds: int = Field(ge=5, le=3_600)

    @model_validator(mode="after")
    def validate_band(self):
        if float(self.entryPrice) >= float(self.exitPrice):
            raise ValueError("Exit price must be higher than entry price")
        if float(self.maxPosition) < float(self.sharesPerOrder):
            raise ValueError("Maximum position must allow one complete order")
        return self


PaperStrategyAction = Literal["STARTED", "WAIT", "BUY", "SELL", "ERROR", "STOPPED"]


class PaperStrategy(ContractModel):
    strategyId: UUID
    conditionId: str = Field(min_length=1, max_length=200)
    tokenId: TokenId
    marketQuestion: str = Field(min_length=1, max_length=1_000)
    outcome: str = Field(min_length=1, max_length=200)
    entryPrice: PriceString
    exitPrice: PriceString
    sharesPerOrder: PositiveDecimalString
    maxPosition: PositiveDecimalString
    intervalSeconds: int = Field(ge=5, le=3_600)
    status: Literal["RUNNING", "STOPPED", "FAILED"]
    ordersPlaced: int = Field(ge=0)
    scansCompleted: int = Field(ge=0)
    lastAction: PaperStrategyAction
    lastMessage: str = Field(min_length=1, max_length=2_000)
    lastQuoteSide: Side | None
    lastQuotePrice: PriceString | None
    lastScannedAt: DateTimeString | None
    nextScanAt: DateTimeString | None
    startedAt: DateTimeString
    stoppedAt: DateTimeString | None
    updatedAt: DateTimeString


class PaperStrategyEvent(ContractModel):
    eventId: UUID
    action: PaperStrategyAction
    message: str = Field(min_length=1, max_length=2_000)
    side: Side | None
    price: PriceString | None
    fillId: UUID | None
    createdAt: DateTimeString


class PaperStrategySnapshot(ContractModel):
    strategy: PaperStrategy | None
    events: list[PaperStrategyEvent]


def _template_offset(value: str) -> str:
    if abs(float(value)) > 0.5:
        raise ValueError("Offset must be within half a dollar of the reference price")
    return value


TemplateOffsetString = Annotated[SignedDecimalString, AfterValidator(_template_offset)]


class StrategyTemplateBand(ContractModel):
    entryOffset: TemplateOffsetString
    exitOffset: TemplateOffsetString
    sharesPerOrder: PositiveDecimalString
    positionMultiplier: PositiveDecimalString
    intervalSeconds: int = Field(ge=5, le=3_600)

    @model_validator(mode="after")
    def validate_offsets(self):
        if float(self.exitOffset) <= float(self.entryOffset):
            raise ValueError("Exit offset must exceed entry offset")
        return self


class StrategyTemplateStats(ContractModel):
    kind: Literal["illustrative"]
    returnPct: DecimalString
    winRatePct: DecimalString
    tradeCount: int = Field(ge=0)
    maxDrawdownPct: DecimalString
    basis: str = Field(min_length=10, max_length=200)


class BacktestHint(ContractModel):
    strategy: Literal["momentum_v1", "mean_reversion_v1", "breakout_v1"]
    note: str = Field(min_length=5, max_length=160)


class StrategyTemplate(ContractModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,47}$")
    strategyType: Literal["price_band_v1"] = "price_band_v1"
    name: str = Field(min_length=3, max_length=48)
    tagline: str = Field(min_length=10, max_length=110)
    description: str = Field(min_length=20, max_length=600)
    suggestedSearchQuery: str = Field(min_length=2, max_length=60)
    outcomePick: Literal["higher_price", "lower_price"]
    band: StrategyTemplateBand
    stats: StrategyTemplateStats
    backtestHint: BacktestHint | None = None


class StrategyTemplateList(ContractModel):
    items: list[StrategyTemplate] = Field(min_length=1, max_length=12)


AlertEventKind = Literal["STARTED", "BUY", "SELL", "ERROR", "STOPPED"]


class AlertChannel(ContractModel):
    channelId: UUID
    kind: Literal["discord", "telegram"]
    label: str = Field(min_length=1, max_length=80)
    eventKinds: list[AlertEventKind] = Field(min_length=1)
    enabled: bool
    createdAt: DateTimeString
    updatedAt: DateTimeString
    targetHint: str = Field(min_length=1, max_length=120)


def _discord_webhook(value: str) -> str:
    parsed = urlparse(value)
    hosts = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or not re.fullmatch(r"/api/webhooks/\d+/[\w-]+", parsed.path)
    ):
        raise ValueError("Target must be an HTTPS Discord webhook URL")
    return value


DiscordWebhook = Annotated[str, AfterValidator(_discord_webhook)]
TelegramChatId = Annotated[
    str, AfterValidator(_matches(r"-?\d{3,20}", "Target must be a Telegram chat id"))
]


class DiscordAlertCreateRequest(ContractModel):
    kind: Literal["discord"]
    label: str = Field(min_length=1, max_length=80)
    target: DiscordWebhook
    eventKinds: list[AlertEventKind] = Field(min_length=1)


class TelegramAlertCreateRequest(ContractModel):
    kind: Literal["telegram"]
    label: str = Field(min_length=1, max_length=80)
    target: TelegramChatId
    eventKinds: list[AlertEventKind] = Field(min_length=1)


AlertCreateChannelRequest = Annotated[
    DiscordAlertCreateRequest | TelegramAlertCreateRequest,
    Field(discriminator="kind"),
]


class AlertDeliveryContext(ContractModel):
    marketQuestion: str | None = Field(max_length=1_000)
    outcome: str | None = Field(max_length=200)
    side: Side | None
    price: PriceString | None


class AlertDelivery(ContractModel):
    deliveryId: UUID
    channelId: UUID
    channelLabel: str = Field(min_length=1, max_length=80)
    channelKind: Literal["discord", "telegram"]
    action: PaperStrategyAction
    message: str = Field(min_length=1, max_length=2_000)
    context: AlertDeliveryContext
    status: Literal["pending", "delivered", "failed"]
    attempts: int = Field(ge=0)
    lastError: str | None
    createdAt: DateTimeString
    deliveredAt: DateTimeString | None


class AlertDeliveryList(ContractModel):
    items: list[AlertDelivery]
    limit: int = Field(gt=0)


class AlertTestSendResponse(ContractModel):
    status: Literal["sent", "failed"]
    error: str | None


class OrderIntentResponse(ContractModel):
    intentId: UUID
    expiresAt: DateTimeString
    orderType: OrderType
    postOnly: bool
    typedData: TypedData
    order: dict[str, Any]


class CommonBacktestConfig(StrictContractModel):
    initialCapital: BacktestDecimalString = "10000"
    positionSizePct: BacktestDecimalString = "0.10"
    takeProfit: BacktestDecimalString = "0.10"
    stopLoss: BacktestDecimalString = "0.05"
    maxHoldMinutes: int = Field(default=1_440, ge=1, le=43_200)
    cooldownMinutes: int = Field(default=60, ge=0, le=43_200)
    slippage: BacktestDecimalString = "0.01"
    maxFillDelayMinutes: int = Field(default=5, ge=1, le=60)
    startAt: DateTimeString | None = None
    endAt: DateTimeString | None = None

    @model_validator(mode="after")
    def validate_common(self):
        if float(self.initialCapital) <= 0:
            raise ValueError("initialCapital must be greater than zero")
        if not 0 < float(self.positionSizePct) <= 1:
            raise ValueError("positionSizePct must be greater than zero and at most one")
        for name in ("takeProfit", "stopLoss", "slippage"):
            if float(getattr(self, name)) > 1:
                raise ValueError(f"{name} must be at most one")
        if self.startAt and self.endAt:
            start = datetime.fromisoformat(self.startAt.replace("Z", "+00:00"))
            end = datetime.fromisoformat(self.endAt.replace("Z", "+00:00"))
            if start >= end:
                raise ValueError("endAt must be later than startAt")
        return self


class MomentumBacktestConfig(CommonBacktestConfig):
    strategy: Literal["momentum_v1"] = "momentum_v1"
    momentumWindowMinutes: int = Field(default=60, ge=1, le=1_440)
    momentumThreshold: BacktestDecimalString = "0.05"

    @model_validator(mode="after")
    def validate_threshold(self):
        if float(self.momentumThreshold) > 1:
            raise ValueError("momentumThreshold must be at most one")
        return self


class MeanReversionBacktestConfig(CommonBacktestConfig):
    strategy: Literal["mean_reversion_v1"] = "mean_reversion_v1"
    reversionWindowMinutes: int = Field(default=60, ge=1, le=1_440)
    reversionThreshold: BacktestDecimalString = "0.05"

    @model_validator(mode="after")
    def validate_threshold(self):
        if not 0 < float(self.reversionThreshold) <= 1:
            raise ValueError("reversionThreshold must be greater than zero and at most one")
        return self


class BreakoutBacktestConfig(CommonBacktestConfig):
    strategy: Literal["breakout_v1"] = "breakout_v1"
    breakoutWindowMinutes: int = Field(default=240, ge=1, le=1_440)
    breakoutThreshold: BacktestDecimalString = "0.02"

    @model_validator(mode="after")
    def validate_threshold(self):
        if not 0 < float(self.breakoutThreshold) <= 1:
            raise ValueError("breakoutThreshold must be greater than zero and at most one")
        return self


BacktestConfig = Annotated[
    MomentumBacktestConfig | MeanReversionBacktestConfig | BreakoutBacktestConfig,
    Field(discriminator="strategy"),
]


def parse_backtest_config(value: dict[str, Any] | None = None) -> BacktestConfig:
    from pydantic import TypeAdapter

    prepared = dict(value or {})
    prepared.setdefault("strategy", "momentum_v1")
    return TypeAdapter(BacktestConfig).validate_python(prepared)


DEFAULT_MOMENTUM_BACKTEST_CONFIG = MomentumBacktestConfig()
DEFAULT_MEAN_REVERSION_BACKTEST_CONFIG = MeanReversionBacktestConfig()
DEFAULT_BREAKOUT_BACKTEST_CONFIG = BreakoutBacktestConfig()


class CreateBacktestRequest(ContractModel):
    marketId: str = Field(min_length=1, max_length=200)
    config: BacktestConfig = Field(default_factory=MomentumBacktestConfig)


class BacktestFailure(ContractModel):
    code: str
    message: str


class BacktestRun(ContractModel):
    runId: UUID
    marketId: str
    marketQuestion: str | None = None
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    phase: Literal["queued", "fetching", "simulating", "saving", "completed", "failed", "cancelled"]
    progress: int = Field(ge=0, le=100)
    config: BacktestConfig
    resolvedOutcome: Literal["YES", "NO"] | None = None
    datasetHash: Annotated[str, Field(min_length=64, max_length=64)] | None = None
    cancelRequested: bool = False
    failure: BacktestFailure | None = None
    warnings: list[str] = Field(default_factory=list)
    createdAt: DateTimeString
    startedAt: DateTimeString | None = None
    completedAt: DateTimeString | None = None


class BacktestMetrics(ContractModel):
    initialCapital: BacktestDecimalString
    finalEquity: BacktestDecimalString
    pnl: SignedBacktestDecimalString
    returnPct: SignedBacktestDecimalString
    maxDrawdownPct: BacktestDecimalString
    tradeCount: int = Field(ge=0)
    winRatePct: BacktestDecimalString
    profitFactor: BacktestDecimalString | None
    averageHoldingSeconds: BacktestDecimalString
    exposurePct: BacktestDecimalString
    fees: BacktestDecimalString
    skippedSignals: int = Field(ge=0)
    yesBuyHoldReturnPct: SignedBacktestDecimalString
    noBuyHoldReturnPct: SignedBacktestDecimalString


class BacktestResult(ContractModel):
    metrics: BacktestMetrics
    assumptions: list[str]


class BacktestTrade(ContractModel):
    tradeIndex: int = Field(ge=0)
    outcome: Literal["YES", "NO"]
    entryAt: DateTimeString
    exitAt: DateTimeString
    entryPrice: BacktestDecimalString
    exitPrice: BacktestDecimalString
    shares: BacktestDecimalString
    entryFee: BacktestDecimalString
    exitFee: BacktestDecimalString
    pnl: SignedBacktestDecimalString
    exitReason: Literal["take_profit", "stop_loss", "max_hold", "settlement"]


class BacktestSeriesPoint(ContractModel):
    timestamp: DateTimeString
    yesPrice: BacktestDecimalString | None = None
    noPrice: BacktestDecimalString | None = None
    equity: BacktestDecimalString


class BacktestRunEnvelope(ContractModel):
    run: BacktestRun
    result: BacktestResult | None = None


class BacktestRunList(ContractModel):
    items: list[BacktestRun]
    activeCount: int = Field(ge=0)
    activeLimit: int = Field(gt=0)


class BacktestTradesResponse(ContractModel):
    runId: UUID
    items: list[BacktestTrade]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)


class BacktestSeriesResponse(ContractModel):
    runId: UUID
    points: list[BacktestSeriesPoint]


BACKTEST_CLOB_V2_START = "2026-04-28T00:00:00.000Z"


def is_backtest_eligible_market(market: MarketSearchMarket) -> bool:
    labels = [outcome.strip().upper() for outcome in market.outcomes]
    try:
        prices = [float(price) for price in market.outcomePrices]
    except ValueError:
        return False
    started_at = market.startDate or market.createdAt
    return (
        market.closed
        and not market.acceptingOrders
        and market.enableOrderBook
        and len(labels) == 2
        and len(set(labels)) == 2
        and "YES" in labels
        and "NO" in labels
        and len(market.clobTokenIds) == 2
        and len(prices) == 2
        and all(math.isfinite(price) for price in prices)
        and prices.count(1) == 1
        and prices.count(0) == 1
        and started_at is not None
        and datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        >= datetime.fromisoformat(BACKTEST_CLOB_V2_START.replace("Z", "+00:00"))
    )


def resolved_binary_market_winner(market: MarketSearchMarket) -> str | None:
    if not market.closed or market.acceptingOrders:
        return None
    if len(market.outcomes) != 2 or len(market.outcomePrices) != 2:
        return None
    try:
        prices = [float(price) for price in market.outcomePrices]
    except ValueError:
        return None
    if not all(math.isfinite(price) for price in prices):
        return None
    winners = [
        outcome for outcome, price in zip(market.outcomes, prices, strict=True) if price == 1
    ]
    return winners[0] if len(winners) == 1 else None


class AgentPredictionRequest(ContractModel):
    conditionId: str = Field(min_length=1, max_length=200)
    tokenId: TokenId | None = None
    marketQuestion: str = Field(min_length=1, max_length=1_000)
    predictedOutcome: str = Field(min_length=1, max_length=200)
    confidence: ConfidenceString | None = None


class AgentPredictionRecord(AgentPredictionRequest):
    predictionId: UUID
    status: Literal["PENDING", "GRADED", "VOID"]
    madeAt: DateTimeString
    category: str | None


class AgentPredictionCategory(ContractModel):
    category: str
    graded: int = Field(ge=0)
    hits: int = Field(ge=0)
    hitRatePct: str | None


class AgentPredictionRecent(ContractModel):
    marketQuestion: str
    predictedOutcome: str
    gradedOutcome: str | None
    hit: bool | None
    madeAt: DateTimeString
    gradedAt: DateTimeString | None
    category: str | None


class AgentPredictionTotals(ContractModel):
    graded: int = Field(ge=0)
    hits: int = Field(ge=0)
    hitRatePct: str | None
    pending: int = Field(ge=0)
    voided: int = Field(ge=0)
    lastGradedAt: DateTimeString | None


class AgentPredictionHitRate(ContractModel):
    totals: AgentPredictionTotals
    byCategory: list[AgentPredictionCategory] = Field(max_length=8)
    recent: list[AgentPredictionRecent] = Field(max_length=25)
    observedAt: DateTimeString
