import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from chat.commands import CommandRouter
from chat.errors import InvalidChatRequest, ThreadBusy, ThreadNotFound
from chat.events import (
    AGENT_ROUTE,
    MESSAGE_COMPLETED,
    MESSAGE_DELTA,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_STARTED,
)
from chat.repository import MemoryChatRepository
from chat.service import ChatService
from components.command_processor import CommandProcessor


USER_A = "00000000-0000-0000-0000-000000000001"
USER_B = "00000000-0000-0000-0000-000000000002"


class NoCommands:
    async def run(self, content, user_id):
        return None


class FixedCommand:
    async def run(self, content, user_id):
        from chat.commands import CommandResult
        return CommandResult("Market probability: 61%", "poly:weather")


class FakeAgent:
    def __init__(self, answer="Research answer"):
        self.answer = answer
        self.calls = []

    async def astream_events(self, payload, version):
        self.calls.append(payload)
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="Research ")},
        }
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="answer")},
        }
        yield {
            "event": "on_chain_end",
            "data": {"output": {"messages": [AIMessage(content=self.answer)]}},
        }


class SlowAgent:
    async def astream_events(self, payload, version):
        await asyncio.sleep(0.1)
        if False:
            yield None


async def _service_with_thread(user_id=USER_A):
    repository = MemoryChatRepository()
    agent = FakeAgent()
    service = ChatService(
        repository=repository,
        agent_factory=lambda: agent,
        command_router=NoCommands(),
    )
    thread = await service.create_thread(user_id)
    return service, repository, agent, thread["thread_id"]


@pytest.mark.asyncio
async def test_stream_persists_one_turn_and_orders_terminal_events():
    service, _, agent, thread_id = await _service_with_thread()

    events = [
        event
        async for event in service.stream_message(
            user_id=USER_A,
            thread_id=thread_id,
            content="What happened?",
            idempotency_key="request-1",
        )
    ]

    assert events[0].event == RUN_STARTED
    assert events[-1].event == RUN_COMPLETED
    assert [event.event for event in events].count(MESSAGE_DELTA) == 2
    completed = next(event for event in events if event.event == MESSAGE_COMPLETED)
    assert completed.data["message"]["content"] == "Research answer"

    messages = await service.get_messages(USER_A, thread_id)
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_idempotency_replays_completed_run_without_calling_model_twice():
    service, _, agent, thread_id = await _service_with_thread()

    first = [
        event
        async for event in service.stream_message(
            user_id=USER_A,
            thread_id=thread_id,
            content="Analyse AAPL",
            idempotency_key="same-key",
        )
    ]
    second = [
        event
        async for event in service.stream_message(
            user_id=USER_A,
            thread_id=thread_id,
            content="Analyse AAPL",
            idempotency_key="same-key",
        )
    ]

    assert first[-1].event == RUN_COMPLETED
    assert second[0].data["replayed"] is True
    assert second[-1].event == RUN_COMPLETED
    assert len(agent.calls) == 1
    assert len(await service.get_messages(USER_A, thread_id)) == 2


@pytest.mark.asyncio
async def test_run_recovery_does_not_expose_idempotency_internals():
    service, _, _, thread_id = await _service_with_thread()
    events = [
        event
        async for event in service.stream_message(
            user_id=USER_A,
            thread_id=thread_id,
            content="Analyse AAPL",
            idempotency_key="private-retry-key",
        )
    ]
    run_id = events[0].data["run_id"]

    run = await service.get_run(USER_A, run_id)

    assert run["status"] == "completed"
    assert run["message"]["content"] == "Research answer"
    assert "idempotency_key" not in run
    assert "request_fingerprint" not in run
    assert "user_id" not in run


@pytest.mark.asyncio
async def test_idempotency_key_cannot_be_reused_for_different_content():
    service, _, agent, thread_id = await _service_with_thread()

    _ = [
        event
        async for event in service.stream_message(
            user_id=USER_A,
            thread_id=thread_id,
            content="Analyse AAPL",
            idempotency_key="same-key",
        )
    ]

    with pytest.raises(InvalidChatRequest):
        _ = [
            event
            async for event in service.stream_message(
                user_id=USER_A,
                thread_id=thread_id,
                content="Analyse MSFT",
                idempotency_key="same-key",
            )
        ]

    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_client_message_id_cannot_overwrite_an_existing_message():
    service, _, agent, thread_id = await _service_with_thread()
    message_id = str(uuid4())

    _ = [
        event
        async for event in service.stream_message(
            user_id=USER_A,
            thread_id=thread_id,
            content="Original content",
            idempotency_key="first-key",
            client_message_id=message_id,
        )
    ]
    second = [
        event
        async for event in service.stream_message(
            user_id=USER_A,
            thread_id=thread_id,
            content="Replacement content",
            idempotency_key="second-key",
            client_message_id=message_id,
        )
    ]

    assert second[-1].event == RUN_FAILED
    assert second[-1].data["code"] == "invalid_chat_request"
    messages = await service.get_messages(USER_A, thread_id)
    assert [message["content"] for message in messages] == [
        "Original content",
        "Research answer",
    ]
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_thread_history_is_not_visible_to_another_user():
    service, _, _, thread_id = await _service_with_thread()

    with pytest.raises(ThreadNotFound):
        await service.get_messages(USER_B, thread_id)


@pytest.mark.asyncio
async def test_thread_with_active_run_cannot_be_deleted():
    repository = MemoryChatRepository()
    thread = await repository.create_thread(USER_A)
    await repository.start_run(
        thread["thread_id"],
        USER_A,
        "active-request",
        "a" * 64,
        str(uuid4()),
        str(uuid4()),
    )

    with pytest.raises(ThreadBusy):
        await repository.delete_thread(thread["thread_id"], USER_A)


@pytest.mark.asyncio
async def test_api_mode_does_not_recreate_a_deleted_or_missing_thread():
    repository = MemoryChatRepository()
    service = ChatService(
        repository=repository,
        agent_factory=lambda: FakeAgent(),
        command_router=NoCommands(),
    )
    missing_thread_id = str(uuid4())

    with pytest.raises(ThreadNotFound):
        _ = [
            event
            async for event in service.stream_message(
                user_id=USER_A,
                thread_id=missing_thread_id,
                content="Do not resurrect this thread",
                idempotency_key="missing-thread",
                create_thread_if_missing=False,
            )
        ]


@pytest.mark.asyncio
async def test_real_money_command_is_blocked_before_tool_execution():
    repository = MemoryChatRepository()
    service = ChatService(
        repository=repository,
        agent_factory=lambda: FakeAgent(),
        command_router=CommandRouter(),
    )
    thread = await service.create_thread(USER_A)

    events = [
        event
        async for event in service.stream_message(
            user_id=USER_A,
            thread_id=thread["thread_id"],
            content="poly:buy 50 secret-market",
            idempotency_key=str(uuid4()),
        )
    ]

    assert events[-1].event == RUN_FAILED
    assert events[-1].data["code"] == "unsafe_command"


@pytest.mark.asyncio
async def test_spaced_real_money_command_cannot_bypass_guard():
    repository = MemoryChatRepository()
    service = ChatService(
        repository=repository,
        agent_factory=lambda: FakeAgent(),
        command_router=CommandRouter(),
    )
    thread = await service.create_thread(USER_A)

    events = [
        event
        async for event in service.stream_message(
            user_id=USER_A,
            thread_id=thread["thread_id"],
            content="poly: buy 50 secret-market",
            idempotency_key=str(uuid4()),
        )
    ]

    assert events[-1].event == RUN_FAILED
    assert events[-1].data["code"] == "unsafe_command"


@pytest.mark.asyncio
async def test_run_timeout_is_a_typed_retryable_failure():
    repository = MemoryChatRepository()
    service = ChatService(
        repository=repository,
        agent_factory=SlowAgent,
        command_router=NoCommands(),
        run_timeout_seconds=0.01,
    )
    thread = await service.create_thread(USER_A)

    events = [
        event
        async for event in service.stream_message(
            user_id=USER_A,
            thread_id=thread["thread_id"],
            content="Take too long",
            idempotency_key=str(uuid4()),
        )
    ]

    assert events[-1].event == RUN_FAILED
    assert events[-1].data["code"] == "run_timeout"
    assert events[-1].data["retryable"] is True


@pytest.mark.asyncio
async def test_help_command_uses_shared_command_backend_without_calling_model():
    repository = MemoryChatRepository()

    def model_must_not_run():
        raise AssertionError("help should not invoke the model")

    service = ChatService(
        repository=repository,
        agent_factory=model_must_not_run,
        command_router=CommandRouter(),
    )
    thread = await service.create_thread(USER_A)

    events = [
        event
        async for event in service.stream_message(
            user_id=USER_A,
            thread_id=thread["thread_id"],
            content="help",
            idempotency_key=str(uuid4()),
        )
    ]

    completed = next(event for event in events if event.event == MESSAGE_COMPLETED)
    assert "PolyTrade Commands" in completed.data["message"]["content"]
    assert events[-1].event == RUN_COMPLETED


@pytest.mark.asyncio
async def test_command_backend_failure_is_typed_and_retryable():
    repository = MemoryChatRepository()

    class BrokenCommands:
        async def run(self, content, user_id):
            from chat.errors import CommandUnavailable
            raise CommandUnavailable(
                "The `poly:weather` command is temporarily unavailable. Please try again."
            )

    service = ChatService(
        repository=repository,
        agent_factory=lambda: FakeAgent(),
        command_router=BrokenCommands(),
    )
    thread = await service.create_thread(USER_A)
    events = [event async for event in service.stream_message(
        user_id=USER_A,
        thread_id=thread["thread_id"],
        content="/hermes poly:weather London",
        idempotency_key=str(uuid4()),
    )]

    assert events[-1].event == RUN_FAILED
    assert events[-1].data["code"] == "command_unavailable"
    assert events[-1].data["retryable"] is True
    assert "poly:weather" in events[-1].data["message"]


@pytest.mark.asyncio
async def test_command_router_hides_initialization_failure(monkeypatch):
    def broken_registry():
        raise RuntimeError("provider secret must not reach the browser")

    monkeypatch.setattr("chat.commands.get_tool_registry", broken_registry)

    with pytest.raises(Exception) as captured:
        await CommandRouter().run("poly:weather London", USER_A)

    assert captured.value.code == "command_unavailable"
    assert captured.value.retryable is True
    assert "provider secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_safe_command_processor_cannot_initialize_wallet_client():
    processor = CommandProcessor(
        SimpleNamespace(tool_map={}, allow_real_trading=False),
        user_id=USER_A,
    )

    with patch(
        "agent.tools.polymarket_tool.get_polymarket_client",
        new_callable=AsyncMock,
    ) as get_client:
        handled, _ = await processor.process_command("poly:buy 50 market-id")

    assert handled is True
    get_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_trade_report_queries_are_scoped_to_authenticated_user(monkeypatch):
    import db.repository as trade_repository

    get_trades = AsyncMock(return_value=[])
    get_summary = AsyncMock(return_value={})
    monkeypatch.setattr(trade_repository, "get_trades", get_trades)
    monkeypatch.setattr(trade_repository, "get_pnl_summary", get_summary)
    processor = CommandProcessor(
        SimpleNamespace(tool_map={}, allow_real_trading=False),
        user_id=USER_A,
    )

    await processor._display_trades_report(
        domain="weather",
        trade_type="paper",
    )

    assert get_trades.await_args.kwargs["user_id"] == USER_A
    assert get_summary.await_args.kwargs["user_id"] == USER_A


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "runtime"),
    [("plain question", "deepagents"), ("/deepagent question", "deepagents"),
     ("/deepagents question", "deepagents"), ("/hermes question", "hermes")],
)
async def test_all_agent_routes_share_chat_service_and_persist_attribution(
    monkeypatch, content, runtime
):
    repository = MemoryChatRepository()
    agents = {"deepagents": FakeAgent("Deep answer"), "hermes": FakeAgent("Hermes answer")}

    def select(selected, **kwargs):
        return agents[selected]

    monkeypatch.setattr("chat.service.get_runtime_agent", select)
    service = ChatService(repository=repository, agent_factory=lambda: agents["deepagents"],
                          command_router=NoCommands())
    thread = await service.create_thread(USER_A)
    events = [event async for event in service.stream_message(
        user_id=USER_A, thread_id=thread["thread_id"], content=content,
        idempotency_key=str(uuid4()))]

    route = next(event for event in events if event.event == AGENT_ROUTE)
    assert route.data["runtime"] == runtime
    messages = await service.get_messages(USER_A, thread["thread_id"])
    assert messages[-1]["metadata"]["runtime"] == runtime
    assert len(agents[runtime].calls) == 1


@pytest.mark.asyncio
async def test_empty_agent_prefix_returns_help_without_model_call():
    repository = MemoryChatRepository()
    service = ChatService(repository=repository,
                          agent_factory=lambda: (_ for _ in ()).throw(AssertionError()),
                          command_router=NoCommands())
    thread = await service.create_thread(USER_A)
    events = [event async for event in service.stream_message(
        user_id=USER_A, thread_id=thread["thread_id"], content="/hermes",
        idempotency_key=str(uuid4()))]
    completed = next(event for event in events if event.event == MESSAGE_COMPLETED)
    assert "/hermes <question>" in completed.data["message"]["content"]


@pytest.mark.asyncio
async def test_hermes_help_is_local_and_does_not_call_model():
    repository = MemoryChatRepository()
    service = ChatService(repository=repository,
                          agent_factory=lambda: (_ for _ in ()).throw(AssertionError()),
                          command_router=NoCommands())
    thread = await service.create_thread(USER_A)
    events = [event async for event in service.stream_message(
        user_id=USER_A, thread_id=thread["thread_id"], content="/hermes help",
        idempotency_key=str(uuid4()))]
    completed = next(event for event in events if event.event == MESSAGE_COMPLETED)
    assert "# Hermes agent" in completed.data["message"]["content"]
    assert "/hermes poly:weather London" in completed.data["message"]["content"]


@pytest.mark.asyncio
async def test_explicit_agent_interprets_deterministic_command_result(monkeypatch):
    hermes = FakeAgent("Hermes conclusion")
    monkeypatch.setattr("chat.service.get_runtime_agent", lambda selected, **kwargs: hermes)
    repository = MemoryChatRepository()
    service = ChatService(repository=repository, agent_factory=lambda: hermes,
                          command_router=FixedCommand())
    thread = await service.create_thread(USER_A)
    events = [event async for event in service.stream_message(
        user_id=USER_A, thread_id=thread["thread_id"],
        content="/hermes poly:weather London", idempotency_key=str(uuid4()))]

    completed = next(event for event in events if event.event == MESSAGE_COMPLETED)
    final = completed.data["message"]["content"]
    assert final.startswith("Market probability: 61%")
    assert "### Hermes analysis" in final
    assert final.endswith("Hermes conclusion")
    deltas = "".join(
        event.data.get("delta", "") for event in events
        if event.event == MESSAGE_DELTA
    )
    assert deltas.startswith("Market probability: 61%")
    prompt = hermes.calls[0]["messages"][-1].content
    assert "Market probability: 61%" in prompt
    assert "Keep every reported figure unchanged" in prompt
    assert "Risks and limitations" in prompt
    assert "Confidence (Low, Medium" in prompt


@pytest.mark.asyncio
async def test_hermes_early_failure_falls_back_but_partial_failure_does_not(monkeypatch):
    class FailingHermes:
        async def astream_events(self, payload, version):
            raise RuntimeError("offline")
            yield

    deep = FakeAgent("Fallback answer")
    monkeypatch.setattr(
        "chat.service.get_runtime_agent",
        lambda selected, **kwargs: FailingHermes() if selected == "hermes" else deep,
    )
    repository = MemoryChatRepository()
    service = ChatService(repository=repository, agent_factory=lambda: deep,
                          command_router=NoCommands())
    thread = await service.create_thread(USER_A)
    events = [event async for event in service.stream_message(
        user_id=USER_A, thread_id=thread["thread_id"], content="/hermes question",
        idempotency_key=str(uuid4()))]
    routes = [event.data for event in events if event.event == AGENT_ROUTE]
    assert routes[-1]["runtime"] == "deepagents"
    assert routes[-1]["fallback"] is True
    assert len(deep.calls) == 1
    from db.attribution import agent_framework
    assert agent_framework.get() == "unknown"
