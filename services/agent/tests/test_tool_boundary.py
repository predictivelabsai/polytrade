import json
from typing import Any

import httpx
import pytest
import respx
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langchain.agents.middleware import ToolCallRequest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from polytrade_agent.boundary import BacktestCapacityMiddleware, StrictToolAllowlistMiddleware
from polytrade_agent.context import AgentRunContext
from polytrade_agent.graph import (
    ALLOWED_AGENT_TOOLS,
    HIDDEN_DEEP_AGENT_TOOLS,
    SYSTEM_PROMPT,
    build_agent,
)
from polytrade_agent.model import DeepSeekThinkingChat
from polytrade_agent.tools import POLYMARKET_TOOLS


class CapturingDeepSeek(DeepSeekThinkingChat):
    _seen_tools: list[str] = PrivateAttr(default_factory=list)

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager
        self._seen_tools = sorted(
            tool["function"]["name"] for tool in kwargs.get("tools", [])
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


class ToolCallingDeepSeek(DeepSeekThinkingChat):
    _call_count: int = PrivateAttr(default=0)

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        self._call_count += 1
        if self._call_count == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_polymarket_markets",
                        "args": {"query": "election", "limit": 1},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            message = AIMessage(content="Public answer")
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager
        return self._generate(messages, stop=stop, **kwargs)


class BacktestCallingDeepSeek(DeepSeekThinkingChat):
    _call_count: int = PrivateAttr(default=0)
    _backtest_args: dict[str, Any] = PrivateAttr(default_factory=dict)
    _backtest_args_list: list[dict[str, Any]] = PrivateAttr(default_factory=list)

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        self._call_count += 1
        if self._call_count == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_resolved_polymarket_markets",
                        "args": {"query": "election", "limit": 5},
                        "id": "resolved-search-1",
                        "type": "tool_call",
                    }
                ],
            )
        elif self._call_count == 2 and self._backtest_args_list:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_my_backtests",
                        "args": {"limit": 50},
                        "id": "backtest-capacity",
                        "type": "tool_call",
                    }
                ],
            )
        elif self._call_count == (3 if self._backtest_args_list else 2):
            argument_sets = self._backtest_args_list or [self._backtest_args]
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "start_polymarket_backtest",
                        "args": {"market_id": "condition-1", **arguments},
                        "id": f"backtest-start-{index}",
                        "type": "tool_call",
                    }
                    for index, arguments in enumerate(argument_sets, start=1)
                ],
            )
        else:
            message = AIMessage(content="Queued the exact resolved market.")
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager
        return self._generate(messages, stop=stop, **kwargs)


def test_agent_exposes_only_polymarket_reads_backtests_and_unsigned_drafting() -> None:
    names = {tool.name for tool in POLYMARKET_TOOLS}
    assert names == {
        "search_polymarket_markets",
        "search_resolved_polymarket_markets",
        "get_polymarket_market",
        "get_polymarket_order_book",
        "get_polymarket_price_history",
        "get_polymarket_recent_trades",
        "get_my_polymarket_account",
        "start_polymarket_backtest",
        "list_my_backtests",
        "get_my_backtest",
        "record_prediction",
        "propose_trading_action",
    }
    assert not names.intersection(
        {
            "shell",
            "execute",
            "http",
            "browser",
            "web_search",
            "weather",
            "news",
            "submit_order",
            "cancel_order",
        }
    )

    compiled_tools = set(build_agent().nodes["tools"].bound._tools_by_name)
    assert compiled_tools == names | HIDDEN_DEEP_AGENT_TOOLS


def test_deep_agent_scaffold_tools_are_blocked_at_execution() -> None:
    guard = StrictToolAllowlistMiddleware(ALLOWED_AGENT_TOOLS)
    executed = False

    def execute(_request: ToolCallRequest) -> ToolMessage:
        nonlocal executed
        executed = True
        return ToolMessage(content="unsafe", tool_call_id="attack")

    request = ToolCallRequest(
        tool_call={
            "name": "read_file",
            "args": {"file_path": "/etc/passwd"},
            "id": "attack",
            "type": "tool_call",
        },
        tool=None,
        state={},
        runtime=None,  # type: ignore[arg-type]
    )
    result = guard.wrap_tool_call(request, execute)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert executed is False


def test_agent_prompt_states_the_exact_active_run_limit() -> None:
    normalized_prompt = " ".join(SYSTEM_PROMPT.split())
    assert "At most 10 backtests may be queued or running" in normalized_prompt
    assert "If the request itself exceeds 10, start no runs" in normalized_prompt
    assert "activeCount and activeLimit" in normalized_prompt


def test_agent_prompt_scopes_record_prediction_as_bookkeeping() -> None:
    normalized_prompt = " ".join(SYSTEM_PROMPT.split())
    assert "call record_prediction once in that turn" in normalized_prompt
    assert "Never call record_prediction for an already-resolved market" in normalized_prompt
    assert "measurement bookkeeping, not forecasting" in normalized_prompt
    # The anti-advice posture must survive the scorecard paragraph.
    assert "not expected returns" in normalized_prompt
    assert "remains a hypothetical" in normalized_prompt


def test_backtest_capacity_guard_blocks_an_over_limit_batch_before_execution() -> None:
    guard = BacktestCapacityMiddleware(10)
    tool_calls = [
        {
            "name": "start_polymarket_backtest",
            "args": {"market_id": f"condition-{index}"},
            "id": f"backtest-{index}",
            "type": "tool_call",
        }
        for index in range(11)
    ]
    executed = False

    def execute(_request: ToolCallRequest) -> ToolMessage:
        nonlocal executed
        executed = True
        return ToolMessage(content="started", tool_call_id="backtest-0")

    request = ToolCallRequest(
        tool_call=tool_calls[0],
        tool=None,
        state={
            "messages": [
                HumanMessage(content="Run eleven"),
                AIMessage(content="", tool_calls=tool_calls),
            ]
        },
        runtime=None,  # type: ignore[arg-type]
    )
    result = guard.wrap_tool_call(request, execute)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "asks for 11 backtests" in str(result.content)
    assert "No runs were started" in str(result.content)
    assert executed is False


@pytest.mark.parametrize(("active_count", "allowed"), [(8, False), (7, True)])
def test_backtest_capacity_guard_requires_the_whole_batch_to_fit(
    active_count: int, allowed: bool
) -> None:
    guard = BacktestCapacityMiddleware(10)
    tool_calls = [
        {
            "name": "start_polymarket_backtest",
            "args": {"market_id": "condition-1", "strategy": strategy},
            "id": f"backtest-{index}",
            "type": "tool_call",
        }
        for index, strategy in enumerate(
            ("momentum_v1", "mean_reversion_v1", "breakout_v1"), start=1
        )
    ]
    executed = False

    def execute(request: ToolCallRequest) -> ToolMessage:
        nonlocal executed
        executed = True
        return ToolMessage(content="started", tool_call_id=request.tool_call["id"])

    request = ToolCallRequest(
        tool_call=tool_calls[0],
        tool=None,
        state={
            "messages": [
                HumanMessage(content="Run all strategies"),
                ToolMessage(
                    content=json.dumps(
                        {"items": [], "activeCount": active_count, "activeLimit": 10}
                    ),
                    tool_call_id="backtest-capacity",
                    name="list_my_backtests",
                ),
                AIMessage(content="", tool_calls=tool_calls),
            ]
        },
        runtime=None,  # type: ignore[arg-type]
    )
    result = guard.wrap_tool_call(request, execute)

    assert isinstance(result, ToolMessage)
    assert executed is allowed
    assert result.status == ("success" if allowed else "error")
    if not allowed:
        assert "only 2 of the 10 active-run slots are available" in str(result.content)


def test_backtest_capacity_guard_rejects_a_stale_preflight() -> None:
    guard = BacktestCapacityMiddleware(10)
    tool_calls = [
        {
            "name": "start_polymarket_backtest",
            "args": {"market_id": "condition-1", "strategy": strategy},
            "id": f"backtest-{index}",
            "type": "tool_call",
        }
        for index, strategy in enumerate(("momentum_v1", "breakout_v1"), start=1)
    ]
    request = ToolCallRequest(
        tool_call=tool_calls[0],
        tool=None,
        state={
            "messages": [
                ToolMessage(
                    content='{"items":[],"activeCount":0,"activeLimit":10}',
                    tool_call_id="old-capacity",
                    name="list_my_backtests",
                ),
                HumanMessage(content="Run two new backtests"),
                AIMessage(content="", tool_calls=tool_calls),
            ]
        },
        runtime=None,  # type: ignore[arg-type]
    )
    result = guard.wrap_tool_call(
        request,
        lambda current: ToolMessage(
            content="started", tool_call_id=current.tool_call["id"]
        ),
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "Capacity must be checked" in str(result.content)


def test_backtest_capacity_guard_waits_for_a_new_user_turn_after_denial() -> None:
    guard = BacktestCapacityMiddleware(10)
    executed = False

    def execute(_request: ToolCallRequest) -> ToolMessage:
        nonlocal executed
        executed = True
        return ToolMessage(content="started", tool_call_id="retry")

    request = ToolCallRequest(
        tool_call={
            "name": "start_polymarket_backtest",
            "args": {"market_id": "condition-1"},
            "id": "retry",
            "type": "tool_call",
        },
        tool=None,
        state={
            "messages": [
                HumanMessage(content="Run eleven backtests"),
                ToolMessage(
                    content=(
                        "BACKTEST_BATCH_DENIED: The request asks for 11 backtests, "
                        "but the active-run limit is 10."
                    ),
                    tool_call_id="denied",
                    name="start_polymarket_backtest",
                    status="error",
                ),
            ]
        },
        runtime=None,  # type: ignore[arg-type]
    )
    result = guard.wrap_tool_call(request, execute)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "until the user sends a narrower request" in str(result.content)
    assert executed is False


def test_model_receives_only_the_explicit_tool_allowlist() -> None:
    model = CapturingDeepSeek(
        model="deepseek-v4-flash",
        api_key="test",
        reasoning_effort="max",
        extra_body={"thinking": {"type": "enabled"}},
    )
    test_graph = create_deep_agent(
        model=model,
        tools=POLYMARKET_TOOLS,
        middleware=[StrictToolAllowlistMiddleware(ALLOWED_AGENT_TOOLS)],
        subagents=[],
        backend=StateBackend(),
    )

    test_graph.invoke({"messages": [HumanMessage(content="List available tools")]})

    assert set(model._seen_tools) == ALLOWED_AGENT_TOOLS


def test_injected_text_cannot_change_fixed_gateway_routes() -> None:
    search = next(tool for tool in POLYMARKET_TOOLS if tool.name == "search_polymarket_markets")
    schema = search.tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {"query", "limit"}
    assert "runtime" not in schema["properties"]

    draft = next(tool for tool in POLYMARKET_TOOLS if tool.name == "propose_trading_action")
    draft_schema = draft.tool_call_schema.model_json_schema()
    encoded = str(draft_schema).lower()
    assert "authorization" not in encoded
    assert "credential" not in encoded
    assert "private key" not in encoded


@pytest.mark.asyncio
async def test_gateway_bearer_is_taken_only_from_request_context() -> None:
    model = ToolCallingDeepSeek(
        model="deepseek-v4-flash",
        api_key="test",
        reasoning_effort="max",
        extra_body={"thinking": {"type": "enabled"}},
    )
    agent = build_agent(model=model)
    bearer_fixture = "request-scoped-gateway-token"

    with respx.mock:
        route = respx.get("http://localhost:4000/v1/research/markets").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="Find election markets")]},
            context=AgentRunContext(
                principal_id="assethero:user-123",
                scopes=("research",),
                gateway_bearer=bearer_fixture,
            ),
        )

    assert route.called
    assert route.call_count == 1
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {bearer_fixture}"
    assert bearer_fixture not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "strategy_fields"),
    [
        (
            {
                "strategy": "mean_reversion_v1",
                "reversion_window_minutes": 90,
                "reversion_threshold": "0.07",
            },
            {
                "strategy": "mean_reversion_v1",
                "reversionWindowMinutes": 90,
                "reversionThreshold": "0.07",
            },
        ),
        (
            {
                "strategy": "breakout_v1",
                "breakout_window_minutes": 180,
                "breakout_threshold": "0.03",
            },
            {
                "strategy": "breakout_v1",
                "breakoutWindowMinutes": 180,
                "breakoutThreshold": "0.03",
            },
        ),
    ],
)
async def test_agent_forwards_only_the_selected_strategy_parameters(
    arguments: dict[str, Any], strategy_fields: dict[str, Any]
) -> None:
    model = BacktestCallingDeepSeek(
        model="deepseek-v4-flash",
        api_key="test",
        reasoning_effort="max",
        extra_body={"thinking": {"type": "enabled"}},
    )
    model._backtest_args = arguments
    agent = build_agent(model=model)

    with respx.mock:
        respx.get("http://localhost:4000/v1/research/markets").mock(
            return_value=httpx.Response(200, json={"events": []})
        )
        create_route = respx.post("http://localhost:8100/v1/backtests").mock(
            return_value=httpx.Response(
                202,
                json={
                    "run": {
                        "runId": "11111111-1111-4111-8111-111111111111",
                        "marketId": "condition-1",
                        "status": "queued",
                        "phase": "queued",
                        "progress": 0,
                        "createdAt": "2026-08-04T00:00:00Z",
                    }
                },
            )
        )
        await agent.ainvoke(
            {"messages": [HumanMessage(content="Run the selected strategy")]},
            context=AgentRunContext(
                principal_id="assethero:user-123",
                scopes=("research",),
                gateway_bearer="request-scoped-research-token",
            ),
        )

    config = json.loads(create_route.calls.last.request.content)["config"]
    assert config == {
        **strategy_fields,
        "initialCapital": "10000",
        "positionSizePct": "0.10",
        "takeProfit": "0.10",
        "stopLoss": "0.05",
        "maxHoldMinutes": 1440,
        "cooldownMinutes": 60,
        "slippage": "0.01",
        "maxFillDelayMinutes": 5,
    }


@pytest.mark.asyncio
async def test_agent_can_queue_all_three_strategies_in_one_turn() -> None:
    model = BacktestCallingDeepSeek(
        model="deepseek-v4-flash",
        api_key="test",
        reasoning_effort="max",
        extra_body={"thinking": {"type": "enabled"}},
    )
    model._backtest_args_list = [
        {"strategy": "momentum_v1"},
        {"strategy": "mean_reversion_v1"},
        {"strategy": "breakout_v1"},
    ]
    agent = build_agent(model=model)
    run_ids = {
        "momentum_v1": "11111111-1111-4111-8111-111111111111",
        "mean_reversion_v1": "22222222-2222-4222-8222-222222222222",
        "breakout_v1": "33333333-3333-4333-8333-333333333333",
    }

    def create_response(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        strategy = body["config"]["strategy"]
        return httpx.Response(
            202,
            json={
                "run": {
                    "runId": run_ids[strategy],
                    "marketId": "condition-1",
                    "marketQuestion": "Will the selected candidate win?",
                    "status": "queued",
                    "phase": "queued",
                    "progress": 0,
                    "config": body["config"],
                    "createdAt": "2026-08-04T00:00:00Z",
                }
            },
        )

    with respx.mock:
        respx.get("http://localhost:4000/v1/research/markets").mock(
            return_value=httpx.Response(200, json={"events": []})
        )
        capacity_route = respx.get("http://localhost:8100/v1/backtests").mock(
            return_value=httpx.Response(
                200,
                json={"items": [], "activeCount": 0, "activeLimit": 10},
            )
        )
        create_route = respx.post("http://localhost:8100/v1/backtests").mock(
            side_effect=create_response
        )
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="Run all strategies on this market")]},
            context=AgentRunContext(
                principal_id="assethero:user-123",
                scopes=("research",),
                gateway_bearer="request-scoped-research-token",
            ),
        )

    assert capacity_route.called
    assert capacity_route.calls.last.request.url.params["limit"] == "50"
    assert create_route.call_count == 3
    requests = [json.loads(call.request.content) for call in create_route.calls]
    assert {request["config"]["strategy"] for request in requests} == set(run_ids)
    assert {
        call.request.headers["Idempotency-Key"] for call in create_route.calls
    } == {
        "agent:backtest-start-1",
        "agent:backtest-start-2",
        "agent:backtest-start-3",
    }
    tool_messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.name == "start_polymarket_backtest"
    ]
    assert len(tool_messages) == 3


@pytest.mark.asyncio
async def test_resolved_search_and_backtest_forward_only_the_research_bearer() -> None:
    model = BacktestCallingDeepSeek(
        model="deepseek-v4-flash",
        api_key="test",
        reasoning_effort="max",
        extra_body={"thinking": {"type": "enabled"}},
    )
    agent = build_agent(model=model)
    bearer_fixture = "request-scoped-research-token"
    run_id = "11111111-1111-4111-8111-111111111111"

    with respx.mock:
        search_route = respx.get("http://localhost:4000/v1/research/markets").mock(
            return_value=httpx.Response(200, json={"events": []})
        )
        create_route = respx.post("http://localhost:8100/v1/backtests").mock(
            return_value=httpx.Response(
                202,
                json={
                    "run": {
                        "runId": run_id,
                        "marketId": "condition-1",
                        "marketQuestion": "Will the test pass?",
                        "status": "queued",
                        "phase": "queued",
                        "progress": 0,
                        "createdAt": "2026-08-04T00:00:00Z",
                    }
                },
            )
        )
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="Backtest a resolved election market")]},
            context=AgentRunContext(
                principal_id="assethero:user-123",
                scopes=("research",),
                gateway_bearer=bearer_fixture,
            ),
        )

    assert search_route.calls.last.request.url.params["state"] == "resolved"
    request = create_route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {bearer_fixture}"
    assert request.headers["Idempotency-Key"] == "agent:backtest-start-1"
    body = json.loads(request.content)
    assert body["marketId"] == "condition-1"
    assert body["config"] == {
        "strategy": "momentum_v1",
        "initialCapital": "10000",
        "positionSizePct": "0.10",
        "momentumWindowMinutes": 60,
        "momentumThreshold": "0.05",
        "takeProfit": "0.10",
        "stopLoss": "0.05",
        "maxHoldMinutes": 1440,
        "cooldownMinutes": 60,
        "slippage": "0.01",
        "maxFillDelayMinutes": 5,
    }
    assert bearer_fixture not in repr(result)
