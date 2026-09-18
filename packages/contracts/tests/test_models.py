from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from polytrade_contracts import (
    DEFAULT_BREAKOUT_BACKTEST_CONFIG,
    DEFAULT_MEAN_REVERSION_BACKTEST_CONFIG,
    STRATEGY_TEMPLATES,
    AgentPredictionHitRate,
    AgentPredictionRequest,
    AlertChannel,
    AlertCreateChannelRequest,
    BacktestRunList,
    BreakoutBacktestConfig,
    MarketSearchMarket,
    MomentumBacktestConfig,
    PaperOrderRequest,
    PaperPortfolio,
    PaperQuote,
    PaperShareStatus,
    PaperStrategySnapshot,
    PaperStrategyStartRequest,
    PublicTrackRecord,
    StrategyTemplateList,
    TradingActionProposal,
    WalletSessionStatus,
    is_backtest_eligible_market,
    parse_backtest_config,
    resolved_binary_market_winner,
)

NOW = "2026-09-01T00:00:00.000Z"


def market(**changes: object) -> MarketSearchMarket:
    value = {
        "id": "market-1",
        "conditionId": "condition-1",
        "slug": "market-1",
        "question": "Will this resolve Yes?",
        "description": "",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["1", "0"],
        "clobTokenIds": ["101", "202"],
        "active": False,
        "closed": True,
        "acceptingOrders": False,
        "enableOrderBook": True,
        "archived": False,
        "restricted": False,
        "minimumOrderSize": "5",
        "minimumTickSize": "0.01",
        "endDate": "2026-05-01T02:00:00.000Z",
        "startDate": "2026-05-01T00:00:00.000Z",
        "createdAt": "2026-05-01T00:00:00.000Z",
        "closedTime": "2026-05-01T02:00:00.000Z",
        "liquidity": "100",
        "volume": "1000",
    }
    value.update(changes)
    return MarketSearchMarket.model_validate(value)


def test_trading_action_proposals_match_the_wire_contract() -> None:
    base = {
        "action": "create",
        "tokenId": "123",
        "marketId": "condition",
        "marketQuestion": "Will this test pass?",
        "outcome": "Yes",
        "side": "BUY",
        "rationale": "contract test",
        "observedAt": NOW,
    }
    adapter = TypeAdapter(TradingActionProposal)
    parsed = adapter.validate_python(
        {**base, "execution": "GTC", "price": "0.45", "size": "10", "postOnly": True}
    )
    assert parsed.execution == "GTC"
    assert parsed.postOnly is True
    for invalid in (
        {**base, "execution": "FOK", "amount": "10", "limitPrice": "0.5", "postOnly": True},
        {**base, "execution": "GTD", "price": "0.45", "size": "10"},
    ):
        try:
            adapter.validate_python(invalid)
        except ValidationError:
            pass
        else:
            raise AssertionError("invalid proposal was accepted")


def test_backtest_defaults_are_discriminated_and_strict() -> None:
    assert MomentumBacktestConfig().model_dump()["momentumWindowMinutes"] == 60
    assert parse_backtest_config({}).strategy == "momentum_v1"
    assert DEFAULT_MEAN_REVERSION_BACKTEST_CONFIG.reversionThreshold == "0.05"
    assert DEFAULT_BREAKOUT_BACKTEST_CONFIG.breakoutWindowMinutes == 240
    for invalid in (
        {"strategy": "mean_reversion_v1", "momentumWindowMinutes": 30},
        {"strategy": "breakout_v1", "breakoutThreshold": "0"},
    ):
        try:
            parse_backtest_config(invalid)
        except ValidationError:
            pass
        else:
            raise AssertionError("invalid backtest config was accepted")
    try:
        MomentumBacktestConfig(startAt="2026-06-02T00:00:00Z", endAt="2026-06-01T00:00:00Z")
    except ValidationError as error:
        assert "later than startAt" in str(error)
    else:
        raise AssertionError("inverted range was accepted")


def test_backtest_market_helpers_preserve_v2_rules() -> None:
    assert is_backtest_eligible_market(market())
    assert not is_backtest_eligible_market(market(startDate="2024-01-04T00:00:00.000Z"))
    assert not is_backtest_eligible_market(market(closed=False))
    assert not is_backtest_eligible_market(market(outcomes=["A", "B"]))
    assert not is_backtest_eligible_market(market(outcomePrices=["0.5", "0.5"]))
    assert resolved_binary_market_winner(market()) == "Yes"
    assert resolved_binary_market_winner(market(outcomePrices=["0", "1"])) == "No"
    assert resolved_binary_market_winner(market(closed=False)) is None
    assert resolved_binary_market_winner(market(outcomePrices=["x", "1"])) is None


def test_workspace_read_contracts_do_not_accept_secret_fields() -> None:
    session = WalletSessionStatus.model_validate(
        {
            "sessionId": "00000000-0000-4000-8000-000000000001",
            "walletAddress": "0x0000000000000000000000000000000000000001",
            "signatureType": 0,
            "idleExpiresAt": "2026-08-03T00:30:00.000Z",
            "expiresAt": "2026-08-03T08:00:00.000Z",
            "encryptedCredentials": "must-be-dropped",
        }
    )
    assert "encryptedCredentials" not in session.model_dump()
    assert BacktestRunList(items=[], activeCount=4, activeLimit=10).activeLimit == 10


def test_paper_contracts_keep_decimal_precision_and_bounds() -> None:
    quote = PaperQuote.model_validate(
        {
            "conditionId": "0xcondition",
            "tokenId": "123",
            "marketQuestion": "Will paper contracts parse?",
            "outcome": "Yes",
            "side": "BUY",
            "shares": "10.000000",
            "averagePrice": "0.420000",
            "limitPrice": "0.430000",
            "grossNotional": "4.200000",
            "feeRate": "0.040000",
            "fee": "0.09744",
            "cashEffect": "-4.297440",
            "observedAt": NOW,
        }
    )
    assert quote.cashEffect == "-4.297440"
    portfolio = PaperPortfolio.model_validate(
        {
            "initialCash": "10000.000000",
            "cash": "9995.702560",
            "positionsValue": "4.000000",
            "equity": "9999.702560",
            "realizedPnl": "0.000000",
            "unrealizedPnl": "-0.297440",
            "totalPnl": "-0.297440",
            "totalFees": "0.097440",
            "positions": [],
            "warnings": [],
            "observedAt": NOW,
        }
    )
    assert portfolio.equity == "9999.702560"
    for shares, price in (("0", "0.4"), ("1", "0.1234567")):
        try:
            PaperOrderRequest(
                conditionId="0xcondition",
                tokenId="123",
                side="BUY",
                shares=shares,
                limitPrice=price,
            )
        except ValidationError:
            pass
        else:
            raise AssertionError("invalid paper order was accepted")


def test_strategy_and_alert_contracts_match_existing_guards() -> None:
    strategy = PaperStrategyStartRequest(
        conditionId="0xcondition",
        tokenId="123",
        entryPrice="0.35",
        exitPrice="0.65",
        sharesPerOrder="10",
        maxPosition="50",
        intervalSeconds=15,
    )
    assert strategy.maxPosition == "50"
    assert PaperStrategySnapshot(strategy=None, events=[]).events == []

    adapter = TypeAdapter(AlertCreateChannelRequest)
    assert (
        adapter.validate_python(
            {
                "kind": "discord",
                "label": "Trading Discord",
                "target": "https://discord.com/api/webhooks/1234/abcdefghij",
                "eventKinds": ["BUY", "SELL"],
            }
        ).kind
        == "discord"
    )
    assert (
        adapter.validate_python(
            {
                "kind": "telegram",
                "label": "Phone",
                "target": "-1001234567890",
                "eventKinds": ["ERROR"],
            }
        ).target
        == "-1001234567890"
    )
    for target in (
        "not-a-url",
        "https://evil.example/api/webhooks/123/token",
        "http://discord.com/api/webhooks/123/token",
    ):
        try:
            adapter.validate_python(
                {"kind": "discord", "label": "Bad", "target": target, "eventKinds": ["BUY"]}
            )
        except ValidationError:
            pass
        else:
            raise AssertionError("invalid webhook was accepted")

    channel = AlertChannel.model_validate(
        {
            "channelId": "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f",
            "kind": "telegram",
            "label": "Phone",
            "eventKinds": ["BUY"],
            "enabled": True,
            "targetHint": "chat 123456789",
            "target": "secret",
            "createdAt": NOW,
            "updatedAt": NOW,
        }
    )
    assert "target" not in channel.model_dump()


def test_public_track_record_strips_identity_adjacent_fields() -> None:
    position = {
        "conditionId": "0xcondition",
        "tokenId": "123",
        "marketQuestion": "Will the Fed hold rates?",
        "outcome": "Yes",
        "shares": "10.000000",
        "costBasis": "5.000000",
        "averageCost": "0.500000",
        "liquidationValue": "5.200000",
        "unrealizedPnl": "0.200000",
        "markStatus": "current",
    }
    fill = {
        "fillId": "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f",
        "kind": "BUY",
        "conditionId": "0xcondition",
        "tokenId": "123",
        "marketQuestion": "Will the Fed hold rates?",
        "outcome": "Yes",
        "shares": "10.000000",
        "averagePrice": "0.500000",
        "grossNotional": "5.000000",
        "feeRate": "0.000000",
        "fee": "0.000000",
        "cashEffect": "-5.000000",
        "realizedPnl": "0.000000",
        "createdAt": NOW,
    }
    record = PublicTrackRecord.model_validate(
        {
            "profile": {"displayName": "Paper account", "startedAt": NOW},
            "stats": {
                "initialCash": "10000.000000",
                "cash": "9500.000000",
                "equity": "9505.200000",
                "totalPnl": "-494.800000",
                "realizedPnl": "10.000000",
                "unrealizedPnl": "0.200000",
                "totalFees": "1.000000",
                "tradeCount": 2,
                "winRate": "100.00",
            },
            "equityCurve": [{"t": NOW, "equity": "9505.200000"}],
            "positions": [position],
            "fills": [fill],
            "observedAt": NOW,
        }
    )
    assert "conditionId" not in record.positions[0].model_dump()
    assert "tokenId" not in record.fills[0].model_dump()
    assert PaperShareStatus(token=None, enabled=False, createdAt=None, updatedAt=None).token is None


def test_prediction_and_template_contracts_are_validated() -> None:
    assert (
        AgentPredictionRequest(
            conditionId="0xabc",
            tokenId="123",
            marketQuestion="Will X win?",
            predictedOutcome="Yes",
            confidence="0.75",
        ).confidence
        == "0.75"
    )
    try:
        AgentPredictionRequest(
            conditionId="0xabc",
            marketQuestion="q",
            predictedOutcome="Yes",
            confidence="1.01",
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("invalid confidence was accepted")

    rates = AgentPredictionHitRate.model_validate(
        {
            "totals": {
                "graded": 0,
                "hits": 0,
                "hitRatePct": None,
                "pending": 0,
                "voided": 0,
                "lastGradedAt": None,
            },
            "byCategory": [],
            "recent": [],
            "observedAt": NOW,
        }
    )
    assert rates.totals.hitRatePct is None

    templates = StrategyTemplateList(items=STRATEGY_TEMPLATES)
    assert len(templates.items) == 5
    assert len({template.id for template in templates.items}) == len(templates.items)
    assert all(
        float(item.band.exitOffset) > float(item.band.entryOffset) for item in templates.items
    )


def test_breakout_model_rejects_zero_threshold_directly() -> None:
    try:
        BreakoutBacktestConfig(breakoutThreshold="0")
    except ValidationError as error:
        assert "greater than zero" in str(error)
    else:
        raise AssertionError("zero breakout threshold was accepted")
