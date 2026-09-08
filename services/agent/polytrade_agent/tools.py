import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import uuid4

import httpx
from langchain.tools import ToolRuntime, tool
from pydantic import Field

from .config import get_settings
from .context import AgentRunContext
from .schemas import (
    BacktestRunReference,
    PredictionInput,
    PredictionRecorded,
    TradingActionInput,
    UnsignedProposalEnvelope,
)

MAX_TOOL_RESULT_CHARS = 32_000
TRUNCATED_PREFIX_CHARS = 12_000


def _gateway_token(runtime: ToolRuntime[AgentRunContext]) -> str:
    context = runtime.context
    token = context.gateway_bearer if isinstance(context, AgentRunContext) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("Authenticated gateway delegation is unavailable")
    return token


async def _gateway_get(
    path: str,
    *,
    runtime: ToolRuntime[AgentRunContext] | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    headers: dict[str, str] = {"Accept": "application/json"}
    if runtime is not None:
        headers["Authorization"] = f"Bearer {_gateway_token(runtime)}"

    async with httpx.AsyncClient(
        base_url=str(settings.GATEWAY_BASE_URL).rstrip("/"),
        timeout=settings.AGENT_HTTP_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as client:
        response = await client.get(path, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()

    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(encoded) > MAX_TOOL_RESULT_CHARS:
        return json.dumps(
            {
                "truncated": True,
                "originalCharacters": len(encoded),
                "payloadPrefix": encoded[:TRUNCATED_PREFIX_CHARS],
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
    return encoded


async def _backtest_request(
    path: str,
    *,
    runtime: ToolRuntime[AgentRunContext],
    method: Literal["GET", "POST"] = "GET",
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Any:
    settings = get_settings()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {_gateway_token(runtime)}",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(
        base_url=str(settings.BACKTEST_BASE_URL).rstrip("/"),
        timeout=settings.AGENT_HTTP_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as client:
        response = await client.request(
            method,
            path,
            params=params,
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


def _encoded_tool_result(payload: Any) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(encoded) <= MAX_TOOL_RESULT_CHARS:
        return encoded
    return json.dumps(
        {
            "truncated": True,
            "originalCharacters": len(encoded),
            "payloadPrefix": encoded[:TRUNCATED_PREFIX_CHARS],
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


async def _gateway_post(
    path: str,
    *,
    runtime: ToolRuntime[AgentRunContext],
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> Any:
    settings = get_settings()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_gateway_token(runtime)}",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(
        base_url=str(settings.GATEWAY_BASE_URL).rstrip("/"),
        timeout=settings.AGENT_HTTP_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as client:
        response = await client.post(path, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


@tool
async def search_polymarket_markets(
    query: Annotated[str, Field(min_length=1, max_length=200)],
    runtime: ToolRuntime[AgentRunContext],
    limit: Annotated[int, Field(ge=1, le=25)] = 10,
) -> str:
    """Search active Polymarket events and markets by text."""
    return await _gateway_get(
        "/v1/research/markets",
        runtime=runtime,
        params={"query": query, "limit": limit},
    )


@tool
async def search_resolved_polymarket_markets(
    query: Annotated[str, Field(min_length=1, max_length=200)],
    runtime: ToolRuntime[AgentRunContext],
    limit: Annotated[int, Field(ge=1, le=25)] = 10,
) -> str:
    """Search resolved binary CLOB V2 markets eligible for backtesting."""
    return await _gateway_get(
        "/v1/research/markets",
        runtime=runtime,
        params={"query": query, "limit": limit, "state": "resolved"},
    )


@tool
async def get_polymarket_market(
    identifier: Annotated[str, Field(min_length=1, max_length=200)],
    runtime: ToolRuntime[AgentRunContext],
    identifier_type: Literal["id", "slug"] = "slug",
) -> str:
    """Get normalized Polymarket metadata for one market by ID or slug."""
    return await _gateway_get(
        "/v1/research/market",
        runtime=runtime,
        params={"identifier": identifier, "identifierType": identifier_type},
    )


@tool
async def get_polymarket_order_book(
    token_id: Annotated[str, Field(pattern=r"^\d+$")],
    runtime: ToolRuntime[AgentRunContext],
) -> str:
    """Get the current Polymarket CLOB order book for an outcome token."""
    return await _gateway_get(f"/v1/research/order-books/{token_id}", runtime=runtime)


@tool
async def get_polymarket_price_history(
    token_id: Annotated[str, Field(pattern=r"^\d+$")],
    runtime: ToolRuntime[AgentRunContext],
    interval: Literal["1h", "6h", "1d", "1w", "max"] = "1d",
) -> str:
    """Get Polymarket price history for an outcome token."""
    return await _gateway_get(
        f"/v1/research/price-history/{token_id}",
        runtime=runtime,
        params={"interval": interval},
    )


@tool
async def get_polymarket_recent_trades(
    condition_id: Annotated[str, Field(min_length=1, max_length=200)],
    runtime: ToolRuntime[AgentRunContext],
) -> str:
    """Get recent public Polymarket trades for a condition."""
    return await _gateway_get(f"/v1/research/trades/{condition_id}", runtime=runtime)


@tool
async def get_my_polymarket_account(runtime: ToolRuntime[AgentRunContext]) -> str:
    """Get the authenticated user's positions, open orders, and fills."""
    return await _gateway_get("/v1/account/snapshot", runtime=runtime)


@tool
async def start_polymarket_backtest(
    market_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description="Exact resolved Polymarket condition ID selected by the user",
        ),
    ],
    runtime: ToolRuntime[AgentRunContext],
    strategy: Literal["momentum_v1", "mean_reversion_v1", "breakout_v1"] = "momentum_v1",
    initial_capital: str = "10000",
    position_size_pct: str = "0.10",
    momentum_window_minutes: int | None = None,
    momentum_threshold: str | None = None,
    reversion_window_minutes: int | None = None,
    reversion_threshold: str | None = None,
    breakout_window_minutes: int | None = None,
    breakout_threshold: str | None = None,
    take_profit: str = "0.10",
    stop_loss: str = "0.05",
    max_hold_minutes: int = 1_440,
    cooldown_minutes: int = 60,
    slippage: str = "0.01",
    max_fill_delay_minutes: int = 5,
    start_at: str | None = None,
    end_at: str | None = None,
) -> str:
    """Queue one selected deterministic strategy for an exact resolved binary market.

    Search resolved markets first. If multiple candidates match, show them to
    the user and ask which one to use; never guess a condition ID. Momentum buys
    strength, mean reversion buys a discount to the trailing mean, and breakout
    buys a move above the prior rolling high. Call this tool once per requested
    strategy; calls for a multi-strategy suite may be issued together only after
    checking capacity with list_my_backtests. Omitted strategy settings use the
    documented defaults for the selected strategy.
    """
    supplied_strategy_fields = {
        "momentum_v1": {
            "momentumWindowMinutes": momentum_window_minutes,
            "momentumThreshold": momentum_threshold,
        },
        "mean_reversion_v1": {
            "reversionWindowMinutes": reversion_window_minutes,
            "reversionThreshold": reversion_threshold,
        },
        "breakout_v1": {
            "breakoutWindowMinutes": breakout_window_minutes,
            "breakoutThreshold": breakout_threshold,
        },
    }
    for candidate, fields in supplied_strategy_fields.items():
        if candidate != strategy and any(value is not None for value in fields.values()):
            raise ValueError(f"{candidate} parameters cannot be used with {strategy}")

    strategy_defaults: dict[str, dict[str, int | str]] = {
        "momentum_v1": {"momentumWindowMinutes": 60, "momentumThreshold": "0.05"},
        "mean_reversion_v1": {
            "reversionWindowMinutes": 60,
            "reversionThreshold": "0.05",
        },
        "breakout_v1": {"breakoutWindowMinutes": 240, "breakoutThreshold": "0.02"},
    }
    strategy_fields = {
        key: value if value is not None else strategy_defaults[strategy][key]
        for key, value in supplied_strategy_fields[strategy].items()
    }
    config = {
        "strategy": strategy,
        "initialCapital": initial_capital,
        "positionSizePct": position_size_pct,
        **strategy_fields,
        "takeProfit": take_profit,
        "stopLoss": stop_loss,
        "maxHoldMinutes": max_hold_minutes,
        "cooldownMinutes": cooldown_minutes,
        "slippage": slippage,
        "maxFillDelayMinutes": max_fill_delay_minutes,
        **({"startAt": start_at} if start_at else {}),
        **({"endAt": end_at} if end_at else {}),
    }
    tool_call_id = getattr(runtime, "tool_call_id", None)
    key = f"agent:{tool_call_id or uuid4()}"
    payload = await _backtest_request(
        "/v1/backtests",
        runtime=runtime,
        method="POST",
        payload={"marketId": market_id, "config": config},
        idempotency_key=key,
    )
    run = payload.get("run") if isinstance(payload, dict) else None
    if not isinstance(run, dict):
        raise RuntimeError("Backtest service returned an invalid run envelope")
    reference = BacktestRunReference.model_validate(
        {
            "kind": "backtest_run",
            "runId": run.get("runId"),
            "marketId": run.get("marketId"),
            "marketQuestion": run.get("marketQuestion"),
            "strategy": (
                run.get("config", {}).get("strategy", strategy)
                if isinstance(run.get("config"), dict)
                else strategy
            ),
            "status": run.get("status"),
            "phase": run.get("phase"),
            "progress": run.get("progress"),
            "createdAt": run.get("createdAt"),
        }
    )
    return reference.model_dump_json(by_alias=True)


@tool
async def list_my_backtests(
    runtime: ToolRuntime[AgentRunContext],
    limit: Annotated[int, Field(ge=1, le=50)] = 50,
) -> str:
    """List recent runs plus the authenticated user's active count and limit."""
    payload = await _backtest_request("/v1/backtests", runtime=runtime, params={"limit": limit})
    return _encoded_tool_result(payload)


@tool
async def record_prediction(
    condition_id: Annotated[str, Field(min_length=1, max_length=200)],
    market_question: Annotated[str, Field(min_length=1, max_length=1_000)],
    predicted_outcome: Annotated[str, Field(min_length=1, max_length=200)],
    runtime: ToolRuntime[AgentRunContext],
    token_id: Annotated[str | None, Field(pattern=r"^\d+$")] = None,
    confidence: Annotated[str | None, Field(pattern=r"^(0(\.\d{1,4})?|1(\.0{1,4})?)$")] = None,
) -> str:
    """Record a falsifiable directional call for the public accuracy scorecard.

    Call this exactly once, in the same turn, whenever you state which outcome
    will win one specific live Polymarket market. Use the exact condition ID and
    market question copied from Polymarket tool output, and the outcome you say
    will win. Never call it for an already-resolved market, a hypothetical or
    conditional statement, a backtest run, or a restatement of someone else's
    claim. This is measurement bookkeeping, not forecasting: it stores only the
    market question, the predicted outcome, and the eventual resolution, and it
    grants no license to promise returns.
    """
    payload = PredictionInput(
        condition_id=condition_id,
        token_id=token_id,
        market_question=market_question,
        predicted_outcome=predicted_outcome,
        confidence=confidence,
    ).model_dump(by_alias=True, exclude_none=True)
    tool_call_id = getattr(runtime, "tool_call_id", None)
    record = await _gateway_post(
        "/v1/agent/predictions",
        runtime=runtime,
        payload=payload,
        idempotency_key=f"prediction:{tool_call_id or uuid4()}",
    )
    return PredictionRecorded.model_validate(record).model_dump_json(by_alias=True)


@tool
async def get_my_backtest(
    run_id: Annotated[str, Field(min_length=36, max_length=36)],
    runtime: ToolRuntime[AgentRunContext],
) -> str:
    """Read one owned backtest's status, assumptions, and completed metrics."""
    payload = await _backtest_request(f"/v1/backtests/{run_id}", runtime=runtime)
    return _encoded_tool_result(payload)


@tool(args_schema=TradingActionInput)
def propose_trading_action(proposal: Any) -> str:
    """Create an unsigned, expiring create-or-cancel draft for explicit user review.

    This tool has no trading side effect. Use exact decimal strings and only
    fields observed from Polymarket data. Never describe the result as filled,
    submitted, simulated, or guaranteed.
    """
    validated = TradingActionInput.model_validate({"proposal": proposal}).proposal
    envelope = UnsignedProposalEnvelope(
        proposal=validated,
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )
    return envelope.model_dump_json(by_alias=True)


POLYMARKET_TOOLS = [
    search_polymarket_markets,
    search_resolved_polymarket_markets,
    get_polymarket_market,
    get_polymarket_order_book,
    get_polymarket_price_history,
    get_polymarket_recent_trades,
    get_my_polymarket_account,
    start_polymarket_backtest,
    list_my_backtests,
    get_my_backtest,
    record_prediction,
    propose_trading_action,
]
