import json
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import BaseMessage, ToolMessage
from langgraph.types import Command

BACKTEST_CREATE_TOOL = "start_polymarket_backtest"
BACKTEST_LIST_TOOL = "list_my_backtests"
BACKTEST_BATCH_DENIED_PREFIX = "BACKTEST_BATCH_DENIED: "


class StrictToolAllowlistMiddleware(AgentMiddleware[Any, Any, Any]):
    """Fail closed if any Deep Agents scaffold tool reaches execution."""

    def __init__(self, allowed: Collection[str]) -> None:
        self._allowed = frozenset(allowed)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if request.tool_call["name"] not in self._allowed:
            return self._blocked(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest],
            Awaitable[ToolMessage | Command[Any]],
        ],
    ) -> ToolMessage | Command[Any]:
        if request.tool_call["name"] not in self._allowed:
            return self._blocked(request)
        return await handler(request)

    @staticmethod
    def _blocked(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="Tool execution denied by the PolyTrade allowlist",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
            status="error",
        )


class BacktestCapacityMiddleware(AgentMiddleware[Any, Any, Any]):
    """Require a capacity preflight and prevent partial multi-run batches."""

    def __init__(self, max_active_runs: int) -> None:
        self._max_active_runs = max_active_runs

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        error = self._capacity_error(request)
        return error if error is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest],
            Awaitable[ToolMessage | Command[Any]],
        ],
    ) -> ToolMessage | Command[Any]:
        error = self._capacity_error(request)
        return error if error is not None else await handler(request)

    def _capacity_error(self, request: ToolCallRequest) -> ToolMessage | None:
        if request.tool_call["name"] != BACKTEST_CREATE_TOOL:
            return None
        messages = _state_messages(request.state)
        if _batch_was_denied_this_turn(messages):
            return self._blocked(
                request,
                f"{BACKTEST_BATCH_DENIED_PREFIX}This user request was already rejected as an "
                "oversized batch. Start no runs until the user sends a narrower request.",
            )
        batch_size = _current_backtest_batch_size(messages, request.tool_call["id"])
        if batch_size > self._max_active_runs:
            return self._blocked(
                request,
                f"{BACKTEST_BATCH_DENIED_PREFIX}The request asks for {batch_size} backtests, "
                f"but the active-run limit is "
                f"{self._max_active_runs}. No runs were started. Tell the user the exact limit "
                "and ask them to narrow the request.",
            )
        if batch_size == 1:
            return None

        capacity = _latest_backtest_capacity(messages)
        if capacity is None:
            return self._blocked(
                request,
                "Capacity must be checked before a multi-run backtest request. No runs were "
                "started. Call list_my_backtests with limit 50, then retry only if the entire "
                "batch fits.",
            )
        active_count, reported_limit, capacity_index = capacity
        effective_limit = min(self._max_active_runs, reported_limit)
        starts_since_capacity_check = sum(
            isinstance(message, ToolMessage)
            and message.name == BACKTEST_CREATE_TOOL
            and message.status != "error"
            for message in messages[capacity_index + 1 :]
        )
        active_after_known_starts = active_count + starts_since_capacity_check
        available = max(0, effective_limit - active_after_known_starts)
        if batch_size > available:
            return self._blocked(
                request,
                f"{BACKTEST_BATCH_DENIED_PREFIX}The request asks for {batch_size} backtests, "
                f"but only {available} of the "
                f"{effective_limit} active-run slots are available. No runs from this batch "
                "were started. Tell the user these exact counts and ask them to narrow the "
                "request or wait for active runs to finish.",
            )
        return None

    @staticmethod
    def _blocked(request: ToolCallRequest, content: str) -> ToolMessage:
        return ToolMessage(
            content=content,
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
            status="error",
        )


def _state_messages(state: Any) -> list[BaseMessage]:
    raw: Any
    if isinstance(state, Mapping):
        raw = state.get("messages", [])
    else:
        raw = getattr(state, "messages", [])
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return []
    return [message for message in raw if isinstance(message, BaseMessage)]


def _current_backtest_batch_size(messages: list[BaseMessage], tool_call_id: str) -> int:
    for message in reversed(messages):
        tool_calls = getattr(message, "tool_calls", None)
        if not isinstance(tool_calls, list):
            continue
        if not any(call.get("id") == tool_call_id for call in tool_calls):
            continue
        return sum(call.get("name") == BACKTEST_CREATE_TOOL for call in tool_calls)
    return 1


def _batch_was_denied_this_turn(messages: list[BaseMessage]) -> bool:
    for message in reversed(messages):
        if message.type == "human":
            return False
        if (
            isinstance(message, ToolMessage)
            and message.name == BACKTEST_CREATE_TOOL
            and message.status == "error"
            and isinstance(message.content, str)
            and message.content.startswith(BACKTEST_BATCH_DENIED_PREFIX)
        ):
            return True
    return False


def _latest_backtest_capacity(
    messages: list[BaseMessage],
) -> tuple[int, int, int] | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.type == "human":
            return None
        if not isinstance(message, ToolMessage) or message.name != BACKTEST_LIST_TOOL:
            continue
        if message.status == "error" or not isinstance(message.content, str):
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        active_count = payload.get("activeCount")
        active_limit = payload.get("activeLimit")
        if (
            isinstance(active_count, int)
            and not isinstance(active_count, bool)
            and active_count >= 0
            and isinstance(active_limit, int)
            and not isinstance(active_limit, bool)
            and active_limit >= 1
        ):
            return active_count, active_limit, index
    return None
