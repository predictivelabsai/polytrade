from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from polytrade_agent.auth import AuthenticatedPrincipal
from polytrade_agent.config import AgentSettings, get_settings
from polytrade_agent.model import MODEL_ID
from polytrade_agent.schemas import RestingOrderProposal, UnsignedProposalEnvelope
from polytrade_agent.server import (
    AgentServices,
    RunLimiter,
    _delete_expired_threads,
    _stream_agent_run,
    _thread_title,
    create_app,
    require_principal,
)
from polytrade_agent.storage import ThreadRecord
from polytrade_agent.usage import QueryAuthorization


def test_thread_title_collapses_whitespace_and_limits_length() -> None:
    assert _thread_title("  Find   a market\nwith liquidity  ") == "Find a market with liquidity"
    assert _thread_title("x" * 100) == "x" * 80
    assert _thread_title(" \n ") == "New chat"


class FakeLease:
    def __init__(self) -> None:
        self.released = False

    async def release(self) -> None:
        self.released = True


class FakeRepository:
    def __init__(self, principal_id: str, *, busy: bool = False) -> None:
        self.principal_id = principal_id
        self.busy = busy
        self.thread_id = uuid4()
        now = datetime.now(UTC)
        self.record = ThreadRecord(
            thread_id=self.thread_id,
            principal_id=principal_id,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=30),
        )
        self.finished: list[tuple[UUID, str, str | None]] = []
        self.promoted: list[tuple[UUID, UUID, str]] = []
        self.touched = False
        self.deleted = False
        self.lease = FakeLease()
        # Usage-gate state (mirrors the agent_daily_usage / agent_llm_usage rows).
        self.queries_used = 0
        self.daily_cost = Decimal("0")
        self.exhausted = False
        self.refunds = 0
        self.activity_starts: list[dict[str, Any]] = []
        self.activity_completions: list[dict[str, Any]] = []
        self.usage_rows: list[dict[str, Any]] = []

    async def authorize_daily_query(self, _principal_id: str, limit: int) -> int | None:
        if self.exhausted or self.queries_used >= limit:
            return None
        self.queries_used += 1
        return self.queries_used

    async def refund_daily_query(self, _principal_id: str) -> None:
        self.refunds += 1
        self.queries_used = max(self.queries_used - 1, 0)

    async def sum_daily_cost(self) -> Decimal:
        return self.daily_cost

    async def used_queries_today(self, _principal_id: str) -> int:
        return self.queries_used

    async def insert_llm_usage(self, **kwargs: Any) -> None:
        self.usage_rows.append(kwargs)

    async def insert_activity(self, **kwargs: Any) -> None:
        self.activity_starts.append(kwargs)

    async def complete_activity(self, **kwargs: Any) -> None:
        self.activity_completions.append(kwargs)

    async def admin_usage_summary(self) -> dict[str, Any]:
        return {
            "usage_date": datetime.now(UTC),
            "by_runtime": [
                {
                    "runtime": "deepseek",
                    "calls": 2,
                    "total_tokens": 900,
                    "estimated_cost_usd": Decimal("0.50"),
                }
            ],
            "by_principal": [
                {
                    "principal_id": self.principal_id,
                    "queries_used": 2,
                    "estimated_cost_usd": Decimal("0.50"),
                }
            ],
        }

    async def create_thread(self, principal_id: str) -> ThreadRecord:
        assert principal_id == self.principal_id
        return self.record

    async def list_owned_threads(
        self,
        principal_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ThreadRecord]:
        assert principal_id == self.principal_id
        rows = [] if self.deleted else [self.record]
        return rows[offset : offset + limit]

    async def set_initial_title(
        self,
        thread_id: UUID,
        principal_id: str,
        title: str,
    ) -> None:
        assert thread_id == self.thread_id
        assert principal_id == self.principal_id
        if self.record.title == "New chat":
            self.record = replace(self.record, title=title, updated_at=datetime.now(UTC))

    async def get_owned_thread(
        self,
        thread_id: UUID,
        principal_id: str,
    ) -> ThreadRecord | None:
        if thread_id == self.thread_id and principal_id == self.principal_id and not self.deleted:
            return self.record
        return None

    async def try_acquire_thread(self, _thread_id: UUID) -> FakeLease | None:
        return None if self.busy else self.lease

    async def create_run(self, _thread_id: UUID, _principal_id: str) -> UUID:
        return uuid4()

    async def finish_run(self, run_id: UUID, state: str, code: str | None = None) -> None:
        self.finished.append((run_id, state, code))

    async def commit_completed_run(
        self,
        run_id: UUID,
        thread_id: UUID,
        principal_id: str,
    ) -> None:
        self.promoted.append((run_id, thread_id, principal_id))
        self.finished.append((run_id, "completed", None))
        self.touched = True

    async def touch_thread(self, _thread_id: UUID, _principal_id: str) -> None:
        self.touched = True

    async def delete_owned_thread(self, _thread_id: UUID, _principal_id: str) -> bool:
        self.deleted = True
        return True

    async def expired_thread_ids(self) -> list[UUID]:
        return [] if self.deleted else [self.thread_id]

    async def delete_thread(self, _thread_id: UUID) -> bool:
        self.deleted = True
        return True


class FakeCheckpointer:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


class FakeGraph:
    def __init__(
        self,
        envelope: UnsignedProposalEnvelope,
        *,
        fail: bool = False,
        timeout: bool = False,
        backtest_count: int = 0,
        fail_immediately: bool = False,
        usage_metadata: dict[str, int] | None = None,
    ) -> None:
        self.envelope = envelope
        self.fail = fail
        self.timeout = timeout
        self.backtest_count = backtest_count
        self.fail_immediately = fail_immediately
        self.usage_metadata = usage_metadata
        self.call: dict[str, Any] = {}

    async def astream(self, input: Any, **kwargs: Any):
        self.call = {"input": input, **kwargs}
        if self.fail_immediately:
            raise RuntimeError("sidecar upstream is unreachable")
        yield (
            "messages",
            (
                AIMessageChunk(
                    content="Current market answer",
                    id="assistant-message",
                    additional_kwargs={"reasoning_content": "never expose this"},
                    usage_metadata=self.usage_metadata,
                ),
                {"langgraph_step": 1},
            ),
        )
        if self.fail:
            raise RuntimeError("private upstream payload and bearer should stay hidden")
        if self.timeout:
            raise TimeoutError
        yield (
            "updates",
            {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content=self.envelope.model_dump_json(by_alias=True),
                            tool_call_id="injected-market-data",
                            name="search_polymarket_markets",
                        ),
                        ToolMessage(
                            content=self.envelope.model_dump_json(by_alias=True),
                            tool_call_id="proposal-call",
                            name="propose_trading_action",
                        ),
                        *[
                            ToolMessage(
                                content=json.dumps(
                                    {
                                        "kind": "backtest_run",
                                        "runId": (
                                            "11111111-1111-4111-8111-111111111111",
                                            "22222222-2222-4222-8222-222222222222",
                                            "33333333-3333-4333-8333-333333333333",
                                        )[index],
                                        "marketId": "condition",
                                        "marketQuestion": "Question?",
                                        "strategy": (
                                            "momentum_v1",
                                            "mean_reversion_v1",
                                            "breakout_v1",
                                        )[index],
                                        "status": "queued",
                                        "phase": "queued",
                                        "progress": 0,
                                        "createdAt": "2026-08-03T00:00:00Z",
                                    }
                                ),
                                tool_call_id=f"backtest-call-{index + 1}",
                                name="start_polymarket_backtest",
                            )
                            for index in range(self.backtest_count)
                        ],
                    ]
                }
            },
        )

    async def aget_state(self, _config: Any) -> Any:
        return SimpleNamespace(
            values={
                "messages": [
                    HumanMessage(content="Find a market", id="human-message"),
                    AIMessage(
                        content="Public answer",
                        id="assistant-message",
                        additional_kwargs={"reasoning_content": "private reasoning"},
                    ),
                    ToolMessage(
                        content=self.envelope.model_dump_json(by_alias=True),
                        tool_call_id="proposal-call",
                        name="propose_trading_action",
                    ),
                    ToolMessage(
                        content='{"raw":"gateway result"}',
                        tool_call_id="raw-call",
                        name="search_polymarket_markets",
                    ),
                ]
            }
        )


@pytest.fixture
def proposal_envelope() -> UnsignedProposalEnvelope:
    now = datetime.now(UTC)
    return UnsignedProposalEnvelope(
        proposal=RestingOrderProposal(
            token_id="123",  # noqa: S106 - Polymarket outcome token identifier
            market_id="condition",
            market_question="Will the runtime remain isolated?",
            outcome="Yes",
            side="BUY",
            execution="GTC",
            price="0.45",
            size="10",
            observed_at=now,
        ),
        expires_at=now + timedelta(minutes=2),
    )


def make_client(
    proposal_envelope: UnsignedProposalEnvelope,
    *,
    busy: bool = False,
    fail: bool = False,
    timeout: bool = False,
    backtest_count: int = 0,
    settings: AgentSettings | None = None,
    graphs: dict[str, Any] | None = None,
    repository: FakeRepository | None = None,
) -> tuple[httpx.AsyncClient, FakeRepository, FakeGraph, FakeCheckpointer]:
    settings = settings or get_settings()
    principal = AuthenticatedPrincipal(
        identity="assethero:user-123",
        issuer="assethero",
        scopes=("research", "trade"),
        bearer="short-lived-browser-token",
    )
    repository = repository or FakeRepository(principal.identity, busy=busy)
    graph = FakeGraph(
        proposal_envelope,
        fail=fail,
        timeout=timeout,
        backtest_count=backtest_count,
    )
    checkpointer = FakeCheckpointer()
    storage = SimpleNamespace(repository=repository, checkpointer=checkpointer)
    application = create_app(settings)
    application.state.services = AgentServices(
        settings=settings,
        storage=storage,
        graph=graphs["deepseek"] if graphs else graph,
        graphs=graphs,
        limiter=RunLimiter(2),
    )
    application.dependency_overrides[require_principal] = lambda: principal
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://agent.polytrade.test",
    )
    return client, repository, graph, checkpointer


@pytest.mark.asyncio
async def test_stream_emits_only_public_typed_events(proposal_envelope) -> None:
    client, repository, graph, _checkpointer = make_client(proposal_envelope)
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Find a market"},
        )

    assert response.status_code == 200
    body = response.text
    assert "event: run.started" in body
    assert "event: message.started" in body
    assert "event: message.delta" in body
    assert "Current market answer" in body
    assert "event: proposal.created" in body
    assert body.count("event: proposal.created") == 1
    assert "event: run.completed" in body
    assert "never expose this" not in body
    assert "short-lived-browser-token" not in body
    assert repository.finished[0][1:] == ("completed", None)
    assert repository.touched is True
    assert repository.lease.released is True
    assert graph.call["durability"] == "exit"
    assert graph.call["config"]["configurable"]["thread_id"] == str(repository.finished[0][0])
    assert graph.call["context"].gateway_bearer == "short-lived-browser-token"
    assert "short-lived-browser-token" not in repr(graph.call)
    assert "short-lived-browser-token" not in json_safe(graph.call["input"])
    assert "short-lived-browser-token" not in json_safe(graph.call["config"])
    assert repository.promoted == [
        (repository.finished[0][0], repository.thread_id, repository.principal_id)
    ]
    assert _checkpointer.deleted == []


@pytest.mark.asyncio
async def test_stream_emits_every_typed_backtest_created_event(proposal_envelope) -> None:
    client, repository, _graph, _checkpointer = make_client(
        proposal_envelope, backtest_count=3
    )
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Backtest this market"},
        )

    assert response.status_code == 200
    assert response.text.count("event: backtest.created") == 3
    assert '"runId":"11111111-1111-4111-8111-111111111111"' in response.text
    assert '"strategy":"mean_reversion_v1"' in response.text
    assert '"strategy":"breakout_v1"' in response.text


@pytest.mark.asyncio
async def test_stream_failure_is_sanitized(proposal_envelope, caplog) -> None:
    client, repository, _graph, _checkpointer = make_client(proposal_envelope, fail=True)
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Trigger failure"},
        )

    assert response.status_code == 200
    assert "event: run.failed" in response.text
    assert "Agent run failed" in response.text
    assert "private upstream payload" not in response.text
    assert "private upstream payload" not in caplog.text
    assert "short-lived-browser-token" not in caplog.text
    assert repository.finished[0][1:] == ("failed", "run_failed")


@pytest.mark.asyncio
async def test_stream_timeout_returns_typed_failure(proposal_envelope) -> None:
    client, repository, _graph, _checkpointer = make_client(
        proposal_envelope,
        timeout=True,
    )
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Slow question"},
        )

    assert response.status_code == 200
    assert "event: run.failed" in response.text
    assert '"code":"run_timeout"' in response.text
    assert "Agent run timed out" in response.text
    assert repository.finished[0][1:] == ("failed", "run_timeout")


@pytest.mark.asyncio
async def test_messages_filter_reasoning_and_raw_tool_results(proposal_envelope) -> None:
    client, repository, _graph, _checkpointer = make_client(proposal_envelope)
    async with client:
        response = await client.get(f"/v1/agent/threads/{repository.thread_id}/messages")

    assert response.status_code == 200
    payload = response.json()
    assert [item["kind"] for item in payload["items"]] == ["message", "message", "proposal"]
    encoded = response.text
    assert "private reasoning" not in encoded
    assert "gateway result" not in encoded
    assert payload["items"][2]["envelope"]["proposal"]["tokenId"] == "123"


@pytest.mark.asyncio
async def test_thread_ownership_busy_state_and_deletion(proposal_envelope) -> None:
    client, repository, _graph, checkpointer = make_client(proposal_envelope)
    foreign_thread = uuid4()
    async with client:
        created = await client.post("/v1/agent/threads")
        assert (await client.get(f"/v1/agent/threads/{foreign_thread}/messages")).status_code == 404
        deleted = await client.delete(f"/v1/agent/threads/{repository.thread_id}")

    assert created.status_code == 201
    assert created.json()["threadId"] == str(repository.thread_id)
    assert created.json()["title"] == "New chat"
    assert deleted.status_code == 204
    assert checkpointer.deleted == [str(repository.thread_id)]
    assert repository.deleted is True

    busy_client, busy_repository, _graph, _checkpointer = make_client(
        proposal_envelope,
        busy=True,
    )
    async with busy_client:
        busy = await busy_client.post(
            f"/v1/agent/threads/{busy_repository.thread_id}/runs/stream",
            json={"message": "Second run"},
        )
    assert busy.status_code == 409


@pytest.mark.asyncio
async def test_thread_listing_and_first_message_title(proposal_envelope) -> None:
    client, repository, _graph, _checkpointer = make_client(proposal_envelope)
    async with client:
        listed = await client.get("/v1/agent/threads?limit=20&offset=0")
        streamed = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "  Find   a market\nwith enough liquidity  "},
        )
        updated = await client.get("/v1/agent/threads")

    assert listed.status_code == 200
    assert listed.json()["items"][0]["threadId"] == str(repository.thread_id)
    assert streamed.status_code == 200
    assert repository.record.title == "Find a market with enough liquidity"
    assert updated.json()["items"][0]["title"] == repository.record.title


@pytest.mark.asyncio
async def test_cors_allows_only_exact_configured_origins(proposal_envelope) -> None:
    client, _repository, _graph, _checkpointer = make_client(proposal_envelope)
    async with client:
        allowed = await client.options(
            "/v1/agent/threads",
            headers={
                "Origin": "https://assethero.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        forbidden = await client.options(
            "/v1/agent/threads",
            headers={
                "Origin": "https://evil.test",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://assethero.test"
    assert forbidden.status_code == 400
    assert "access-control-allow-origin" not in forbidden.headers


def json_safe(value: Any) -> str:
    return str(value)


@pytest.mark.asyncio
async def test_disconnect_marks_run_cancelled_and_releases_capacity(proposal_envelope) -> None:
    settings = get_settings()
    principal = AuthenticatedPrincipal(
        identity="assethero:user-123",
        issuer="assethero",
        scopes=("research",),
        bearer="ephemeral-jwt",
    )
    repository = FakeRepository(principal.identity)
    limiter = RunLimiter(1)
    assert await limiter.try_acquire()
    services = AgentServices(
        settings=settings,
        storage=SimpleNamespace(repository=repository, checkpointer=FakeCheckpointer()),
        graph=FakeGraph(proposal_envelope),
        limiter=limiter,
    )
    run_id = uuid4()
    disconnected_request = SimpleNamespace(is_disconnected=lambda: async_true())
    stream = _stream_agent_run(
        request=disconnected_request,
        services=services,
        principal=principal,
        thread_id=repository.thread_id,
        run_id=run_id,
        message="Question",
        lease=repository.lease,
        requested_runtime="deepseek",
        authorization=QueryAuthorization("platform", 1, settings.FREE_PLATFORM_QUERY_LIMIT),
    )

    assert (await anext(stream))["event"] == "run.started"
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)

    assert repository.finished == [(run_id, "cancelled", "client_disconnected")]
    assert repository.lease.released is True
    assert limiter.active == 0


async def async_true() -> bool:
    return True


@pytest.mark.asyncio
async def test_expired_thread_cleanup_removes_checkpoint_and_metadata(proposal_envelope) -> None:
    principal = AuthenticatedPrincipal(
        identity="assethero:user-123",
        issuer="assethero",
        scopes=("research",),
        bearer="ephemeral-jwt",
    )
    repository = FakeRepository(principal.identity)
    checkpointer = FakeCheckpointer()
    services = AgentServices(
        settings=get_settings(),
        storage=SimpleNamespace(repository=repository, checkpointer=checkpointer),
        graph=FakeGraph(proposal_envelope),
        limiter=RunLimiter(1),
    )

    await _delete_expired_threads(services)

    assert checkpointer.deleted == [str(repository.thread_id)]
    assert repository.deleted is True
    assert repository.lease.released is True


def make_settings(**overrides: Any) -> AgentSettings:
    return AgentSettings.model_validate({**get_settings().model_dump(), **overrides})


HERMES_SETTINGS = {
    "HERMES_API_URL": "http://hermes.test:8642/v1",
    "HERMES_API_SERVER_KEY": "sidecar-shared-secret",
}


@pytest.mark.asyncio
async def test_hermes_runtime_requires_configuration(proposal_envelope) -> None:
    client, repository, graph, _checkpointer = make_client(proposal_envelope)
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Ask Hermes", "runtime": "hermes"},
        )

    assert response.status_code == 400
    assert "Hermes runtime is not available" in response.text
    assert repository.finished == []
    assert repository.activity_starts == []
    assert graph.call == {}


@pytest.mark.asyncio
async def test_quota_exhaustion_returns_429_and_releases_capacity(proposal_envelope) -> None:
    settings = get_settings()
    principal = AuthenticatedPrincipal(
        identity="assethero:user-123",
        issuer="assethero",
        scopes=("research",),
        bearer="ephemeral-jwt",
    )
    repository = FakeRepository(principal.identity)
    repository.queries_used = settings.FREE_PLATFORM_QUERY_LIMIT
    limiter = RunLimiter(2)
    assert await limiter.try_acquire()  # one unrelated in-flight run
    services = AgentServices(
        settings=settings,
        storage=SimpleNamespace(repository=repository, checkpointer=FakeCheckpointer()),
        graph=FakeGraph(proposal_envelope),
        graphs={"deepseek": FakeGraph(proposal_envelope)},
        limiter=limiter,
    )
    application = create_app(settings)
    application.state.services = services
    application.dependency_overrides[require_principal] = lambda: principal
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://agent.polytrade.test",
    ) as client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "One query too many"},
        )

    assert response.status_code == 429
    assert "platform-funded AI queries" in response.text
    assert repository.finished == []  # create_run never happened
    assert repository.activity_starts == []
    assert repository.lease.released is True
    assert limiter.active == 1  # the gated run released; the unrelated run kept


@pytest.mark.asyncio
async def test_platform_budget_exhaustion_returns_429(proposal_envelope) -> None:
    repository = FakeRepository("assethero:user-123")
    repository.daily_cost = Decimal("5.00")
    client, repository, _graph, _checkpointer = make_client(
        proposal_envelope,
        repository=repository,
        settings=make_settings(PLATFORM_LLM_DAILY_BUDGET_USD=5.0),
    )
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Ask anyway"},
        )

    assert response.status_code == 429
    assert "daily AI budget" in response.text
    assert repository.finished == []
    assert repository.queries_used == 0


@pytest.mark.asyncio
async def test_hermes_falls_back_to_deepseek_when_nothing_streamed(proposal_envelope) -> None:
    deepseek_graph = FakeGraph(proposal_envelope)
    hermes_graph = FakeGraph(proposal_envelope, fail_immediately=True)
    client, repository, _graph, checkpointer = make_client(
        proposal_envelope,
        settings=make_settings(**HERMES_SETTINGS),
        graphs={"deepseek": deepseek_graph, "hermes": hermes_graph},
    )
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Ask Hermes", "runtime": "hermes"},
        )

    assert response.status_code == 200
    assert "event: runtime.fallback" in response.text
    assert '"from":"hermes"' in response.text and '"to":"deepseek"' in response.text
    assert "Current market answer" in response.text
    assert "event: run.completed" in response.text
    assert "event: run.failed" not in response.text
    assert hermes_graph.call != {} and deepseek_graph.call != {}
    # The abandoned run-thread checkpoint is deleted once, before the DeepSeek
    # attempt re-seeds from the canonical thread state.
    assert len(checkpointer.deleted) == 1
    assert repository.finished[0][1:] == ("completed", None)
    assert repository.refunds == 0  # the user saw an answer; the slot is kept
    completion = repository.activity_completions[0]
    assert completion["status"] == "completed"
    assert completion["metadata"]["fallback"] is True
    assert completion["metadata"]["runtime"] == "deepseek"
    assert completion["metadata"]["requested_runtime"] == "hermes"


@pytest.mark.asyncio
async def test_partial_hermes_stream_fails_instead_of_falling_back(proposal_envelope) -> None:
    hermes_graph = FakeGraph(proposal_envelope, fail=True)
    deepseek_graph = FakeGraph(proposal_envelope)
    client, repository, _graph, _checkpointer = make_client(
        proposal_envelope,
        settings=make_settings(**HERMES_SETTINGS),
        graphs={"deepseek": deepseek_graph, "hermes": hermes_graph},
    )
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Ask Hermes", "runtime": "hermes"},
        )

    assert response.status_code == 200
    assert "event: run.failed" in response.text
    assert "event: runtime.fallback" not in response.text
    assert deepseek_graph.call == {}  # never retried after client-visible output
    assert repository.finished[0][1:] == ("failed", "run_failed")
    assert repository.refunds == 0  # text was streamed; the slot is consumed


@pytest.mark.asyncio
async def test_failed_run_without_content_refunds_the_query_slot(proposal_envelope) -> None:
    deepseek_graph = FakeGraph(proposal_envelope, fail_immediately=True)
    client, repository, _graph, checkpointer = make_client(
        proposal_envelope,
        graphs={"deepseek": deepseek_graph},
    )
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Break the run"},
        )

    assert response.status_code == 200
    assert "event: run.failed" in response.text
    assert repository.finished[0][1:] == ("failed", "run_failed")
    assert repository.refunds == 1
    assert repository.queries_used == 0
    assert len(checkpointer.deleted) == 1


@pytest.mark.asyncio
async def test_quota_warning_notice_is_streamed_at_eighty_percent(proposal_envelope) -> None:
    repository = FakeRepository("assethero:user-123")
    repository.queries_used = 7
    client, repository, _graph, _checkpointer = make_client(
        proposal_envelope,
        repository=repository,
        settings=make_settings(FREE_PLATFORM_QUERY_LIMIT=10),
    )
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Ask a question"},
        )

    assert response.status_code == 200
    assert "event: run.completed" in response.text
    assert "event: usage.notice" in response.text
    assert '"kind":"quota_warning"' in response.text
    assert "8 of 10" in response.text
    assert repository.queries_used == 8


@pytest.mark.asyncio
async def test_measured_usage_is_recorded_per_message_id(proposal_envelope) -> None:
    deepseek_graph = FakeGraph(
        proposal_envelope,
        usage_metadata={"input_tokens": 12, "output_tokens": 30, "total_tokens": 42},
    )
    client, repository, _graph, _checkpointer = make_client(
        proposal_envelope,
        graphs={"deepseek": deepseek_graph},
    )
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Ask a question"},
        )

    assert response.status_code == 200
    assert "event: run.completed" in response.text
    assert len(repository.usage_rows) == 1
    row = repository.usage_rows[0]
    assert row["runtime"] == "deepseek"
    assert row["provider"] == "deepseek"
    assert row["model"] == MODEL_ID
    assert row["input_tokens"] == 12 and row["output_tokens"] == 30
    assert row["usage_quality"] == "measured"
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_unmeasured_run_records_a_single_estimate(proposal_envelope) -> None:
    client, repository, _graph, _checkpointer = make_client(proposal_envelope)
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Ask a question"},
        )

    assert response.status_code == 200
    assert len(repository.usage_rows) == 1
    row = repository.usage_rows[0]
    assert row["runtime"] == "deepseek" and row["provider"] == "deepseek"
    assert row["usage_quality"] == "estimated"
    assert row["input_tokens"] > 0 and row["output_tokens"] > 0
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_usage_endpoint_reports_remaining_allowance(proposal_envelope) -> None:
    repository = FakeRepository("assethero:user-123")
    repository.queries_used = 2
    repository.daily_cost = Decimal("1.250000")
    client, repository, _graph, _checkpointer = make_client(
        proposal_envelope,
        repository=repository,
    )
    async with client:
        response = await client.get("/v1/agent/usage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["fundingSource"] == "platform"
    assert payload["used"] == 2 and payload["limit"] == 5 and payload["remaining"] == 3
    assert payload["costUsd"] == "1.25"
    assert payload["platformCostUsd"] == "1.25"
    assert payload["platformBudgetUsd"] == "5"


@pytest.mark.asyncio
async def test_admin_usage_endpoint_requires_an_admin_identity(proposal_envelope) -> None:
    client, _repository, _graph, _checkpointer = make_client(proposal_envelope)
    async with client:
        forbidden = await client.get("/v1/agent/admin/usage")

    assert forbidden.status_code == 403
    assert "Admin access required" in forbidden.text

    admin_client, _repository, _graph, _checkpointer = make_client(
        proposal_envelope,
        settings=make_settings(ADMIN_PRINCIPAL_IDS="clerk:other,assethero:user-123"),
    )
    async with admin_client:
        allowed = await admin_client.get("/v1/agent/admin/usage")

    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["byRuntime"][0]["runtime"] == "deepseek"
    assert payload["byRuntime"][0]["calls"] == 2
    assert payload["byRuntime"][0]["estimatedCostUsd"] == "0.5"
    assert payload["byPrincipal"][0]["principalId"] == "assethero:user-123"
    assert payload["byPrincipal"][0]["queriesUsed"] == 2


@pytest.mark.asyncio
async def test_activity_log_redacts_secrets_from_prompt_and_response(proposal_envelope) -> None:
    client, repository, _graph, _checkpointer = make_client(proposal_envelope)
    async with client:
        response = await client.post(
            f"/v1/agent/threads/{repository.thread_id}/runs/stream",
            json={"message": "Use xai-leakykey12345 and api_key=hunter2 please"},
        )

    assert response.status_code == 200
    start = repository.activity_starts[0]
    assert start["principal_id"] == "assethero:user-123"
    assert start["requested_runtime"] == "deepseek"
    assert "xai-leakykey12345" not in start["request_text"]
    assert "hunter2" not in start["request_text"]
    completion = repository.activity_completions[0]
    assert completion["status"] == "completed"
    assert completion["response_text"] == "Current market answer"
    assert set(completion["metadata"]) == {"runtime", "requested_runtime", "fallback", "code"}
