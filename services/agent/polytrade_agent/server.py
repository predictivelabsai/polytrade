from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from sse_starlette.sse import EventSourceResponse

from .auth import AuthenticatedPrincipal, get_verifier
from .config import AgentSettings, enforce_no_langsmith, get_settings
from .context import AgentRunContext
from .graph import build_agent
from .model import build_model
from .schemas import (
    AgentRunRequest,
    BacktestRunReference,
    PublicBacktest,
    PublicMessage,
    PublicProposal,
    PublicThreadItem,
    ThreadListResponse,
    ThreadMessagesResponse,
    ThreadResponse,
    UnsignedProposalEnvelope,
)
from .storage import AgentStorage, ThreadLease, open_storage

logger = logging.getLogger("polytrade.agent")


def _thread_title(message: str) -> str:
    return " ".join(message.split())[:80] or "New chat"


def _thread_response(record: Any) -> ThreadResponse:
    return ThreadResponse(
        thread_id=record.thread_id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
        expires_at=record.expires_at,
    )


class RunLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.active = 0
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self.active >= self.limit:
                return False
            self.active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            self.active = max(0, self.active - 1)


@dataclass
class AgentServices:
    settings: AgentSettings
    storage: AgentStorage
    graph: Any
    limiter: RunLimiter


async def require_principal(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AuthenticatedPrincipal:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or separator != " " or not token or " " in token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await get_verifier().verify(token, required_scope="research")
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_services(request: Request) -> AgentServices:
    services = getattr(request.app.state, "services", None)
    if not isinstance(services, AgentServices):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Not ready")
    return services


async def _delete_expired_threads(services: AgentServices) -> None:
    while True:
        expired = await services.storage.repository.expired_thread_ids()
        deleted = 0
        for thread_id in expired:
            lease = await services.storage.repository.try_acquire_thread(thread_id)
            if lease is None:
                continue
            try:
                await services.storage.checkpointer.adelete_thread(str(thread_id))
                deleted += int(await services.storage.repository.delete_thread(thread_id))
            finally:
                await lease.release()
        if len(expired) < 100 or deleted == 0:
            return


async def _retention_loop(services: AgentServices) -> None:
    while True:
        try:
            await _delete_expired_threads(services)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitized background-task boundary
            logger.error("thread cleanup failed error_type=%s", type(exc).__name__)
        await asyncio.sleep(3600)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.configured_settings
    storage = await open_storage(settings)
    cleanup_task: asyncio.Task[None] | None = None
    try:
        if not await storage.repository.schema_ready():
            raise RuntimeError("Agent database schema is not ready")
        await storage.repository.mark_interrupted_runs()
        model = build_model(settings)
        services = AgentServices(
            settings=settings,
            storage=storage,
            graph=build_agent(model=model, checkpointer=storage.checkpointer),
            limiter=RunLimiter(settings.AGENT_MAX_CONCURRENT_RUNS),
        )
        app.state.services = services
        cleanup_task = asyncio.create_task(_retention_loop(services))
        yield
    finally:
        app.state.services = None
        if cleanup_task is not None:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
        await storage.close()


def create_app(settings: AgentSettings | None = None) -> FastAPI:
    enforce_no_langsmith()
    resolved_settings = settings or get_settings()
    application = FastAPI(
        title="PolyTrade Agent API",
        version="1.0.0",
        lifespan=_lifespan,
    )
    application.state.configured_settings = resolved_settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Cache-Control"],
        max_age=600,
    )

    @application.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/readyz")
    async def readiness(request: Request, response: Response) -> dict[str, Any]:
        services = getattr(request.app.state, "services", None)
        if not isinstance(services, AgentServices):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "not_ready"}
        database_ready = False
        gateway_ready = False
        try:
            database_ready = await services.storage.repository.schema_ready()
        except Exception as exc:  # noqa: BLE001 - readiness fails closed
            logger.error("database readiness failed error_type=%s", type(exc).__name__)
        try:
            async with httpx.AsyncClient(
                base_url=str(services.settings.GATEWAY_BASE_URL).rstrip("/"),
                timeout=5,
                follow_redirects=False,
            ) as client:
                gateway_response = await client.get("/health")
                gateway_ready = gateway_response.is_success
        except (httpx.HTTPError, OSError) as exc:
            logger.error("gateway readiness failed error_type=%s", type(exc).__name__)
        ready = database_ready and gateway_ready
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ready" if ready else "not_ready",
            "components": {"database": database_ready, "gateway": gateway_ready},
        }

    @application.post(
        "/v1/agent/threads",
        response_model=ThreadResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_thread(
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    ) -> ThreadResponse:
        services = get_services(request)
        record = await services.storage.repository.create_thread(principal.identity)
        return _thread_response(record)

    @application.get(
        "/v1/agent/threads",
        response_model=ThreadListResponse,
    )
    async def list_threads(
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
        limit: Annotated[int, Query(ge=1, le=50)] = 50,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> ThreadListResponse:
        records = await get_services(request).storage.repository.list_owned_threads(
            principal.identity,
            limit,
            offset,
        )
        return ThreadListResponse(items=[_thread_response(record) for record in records])

    @application.get(
        "/v1/agent/threads/{thread_id}/messages",
        response_model=ThreadMessagesResponse,
    )
    async def get_thread_messages(
        thread_id: UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    ) -> ThreadMessagesResponse:
        services = get_services(request)
        await _require_owned_thread(services, thread_id, principal.identity)
        snapshot = await services.graph.aget_state(_thread_config(thread_id))
        values = snapshot.values if snapshot else {}
        messages = values.get("messages", []) if isinstance(values, dict) else []
        return ThreadMessagesResponse(
            thread_id=thread_id,
            items=_public_thread_items(messages),
        )

    @application.delete(
        "/v1/agent/threads/{thread_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_thread(
        thread_id: UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    ) -> Response:
        services = get_services(request)
        await _require_owned_thread(services, thread_id, principal.identity)
        lease = await services.storage.repository.try_acquire_thread(thread_id)
        if lease is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Thread is busy")
        try:
            await _require_owned_thread(services, thread_id, principal.identity)
            await services.storage.checkpointer.adelete_thread(str(thread_id))
            await services.storage.repository.delete_owned_thread(thread_id, principal.identity)
        finally:
            await lease.release()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.post("/v1/agent/threads/{thread_id}/runs/stream")
    async def stream_run(
        thread_id: UUID,
        body: AgentRunRequest,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    ) -> EventSourceResponse:
        services = get_services(request)
        message = body.message.strip()
        if not message or len(message) > services.settings.AGENT_MAX_MESSAGE_CHARS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Message length is invalid",
            )
        await _require_owned_thread(services, thread_id, principal.identity)
        if not await services.limiter.try_acquire():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Agent capacity is full",
            )
        lease: ThreadLease | None = None
        try:
            lease = await services.storage.repository.try_acquire_thread(thread_id)
            if lease is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Thread already has an active run",
                )
            await _require_owned_thread(services, thread_id, principal.identity)
            await services.storage.repository.set_initial_title(
                thread_id,
                principal.identity,
                _thread_title(message),
            )
            run_id = await services.storage.repository.create_run(thread_id, principal.identity)
        except BaseException:
            if lease is not None:
                await lease.release()
            await services.limiter.release()
            raise

        return EventSourceResponse(
            _stream_agent_run(
                request=request,
                services=services,
                principal=principal,
                thread_id=thread_id,
                run_id=run_id,
                message=message,
                lease=lease,
            ),
            ping=15,
            send_timeout=30,
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    return application


async def _require_owned_thread(
    services: AgentServices,
    thread_id: UUID,
    principal_id: str,
) -> None:
    if await services.storage.repository.get_owned_thread(thread_id, principal_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")


def _thread_config(thread_id: UUID) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": str(thread_id)}}


async def _stream_agent_run(
    *,
    request: Request,
    services: AgentServices,
    principal: AuthenticatedPrincipal,
    thread_id: UUID,
    run_id: UUID,
    message: str,
    lease: ThreadLease,
) -> AsyncIterator[dict[str, str]]:
    status_value: Literal["completed", "failed", "cancelled"] = "failed"
    error_code: str | None = "run_failed"
    run_committed = False
    started_messages: set[str] = set()
    emitted_proposals: set[str] = set()
    emitted_backtests: set[str] = set()
    yield _sse("run.started", {"runId": str(run_id), "threadId": str(thread_id)})
    try:
        context = AgentRunContext(
            principal_id=principal.identity,
            scopes=principal.scopes,
            gateway_bearer=principal.bearer,
        )
        run_input = await _isolated_run_input(services.graph, thread_id, message)
        async with asyncio.timeout(services.settings.AGENT_RUN_TIMEOUT_SECONDS):
            stream = services.graph.astream(
                run_input,
                config=_thread_config(run_id),
                context=context,
                stream_mode=["messages", "updates"],
                durability="exit",
            )
            async for mode, payload in stream:
                if await request.is_disconnected():
                    raise asyncio.CancelledError
                if mode == "messages":
                    chunk, metadata = payload
                    if isinstance(chunk, AIMessageChunk):
                        text = _public_content(chunk.content)
                        if text:
                            message_id = _stream_message_id(chunk, metadata, run_id)
                            if message_id not in started_messages:
                                started_messages.add(message_id)
                                yield _sse("message.started", {"messageId": message_id})
                            yield _sse(
                                "message.delta",
                                {"messageId": message_id, "textDelta": text},
                            )
                elif mode == "updates":
                    for tool_message in _tool_messages(payload):
                        item_id = tool_message.tool_call_id or tool_message.id
                        if not item_id:
                            continue
                        if tool_message.name == "propose_trading_action":
                            envelope = _proposal_envelope(tool_message.content)
                            if envelope is None or item_id in emitted_proposals:
                                continue
                            emitted_proposals.add(item_id)
                            yield _sse(
                                "proposal.created",
                                {
                                    "proposalId": item_id,
                                    "envelope": envelope.model_dump(mode="json", by_alias=True),
                                },
                            )
                        elif tool_message.name == "start_polymarket_backtest":
                            backtest = _backtest_reference(tool_message.content)
                            if backtest is None or item_id in emitted_backtests:
                                continue
                            emitted_backtests.add(item_id)
                            yield _sse(
                                "backtest.created",
                                {
                                    "backtestId": item_id,
                                    "backtest": backtest.model_dump(mode="json", by_alias=True),
                                },
                            )
        if await request.is_disconnected():
            raise asyncio.CancelledError
        await services.storage.repository.commit_completed_run(
            run_id,
            thread_id,
            principal.identity,
        )
        run_committed = True
        status_value = "completed"
        error_code = None
        yield _sse("run.completed", {"runId": str(run_id)})
    except TimeoutError:
        error_code = "run_timeout"
        yield _sse(
            "run.failed",
            {"runId": str(run_id), "code": error_code, "message": "Agent run timed out"},
        )
    except asyncio.CancelledError:
        status_value = "cancelled"
        error_code = "client_disconnected"
        raise
    except Exception as exc:  # noqa: BLE001 - sanitized streaming boundary
        logger.error(
            "agent run failed run_id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        yield _sse(
            "run.failed",
            {"runId": str(run_id), "code": error_code, "message": "Agent run failed"},
        )
    finally:
        if not run_committed:
            with suppress(BaseException):
                await services.storage.checkpointer.adelete_thread(str(run_id))
            with suppress(BaseException):
                await services.storage.repository.finish_run(run_id, status_value, error_code)
        with suppress(BaseException):
            await lease.release()
        with suppress(BaseException):
            await services.limiter.release()


async def _isolated_run_input(graph: Any, thread_id: UUID, message: str) -> dict[str, Any]:
    """Seed a disposable run thread from the last completed canonical checkpoint."""
    snapshot = await graph.aget_state(_thread_config(thread_id))
    values = snapshot.values if snapshot and isinstance(snapshot.values, dict) else {}
    run_input = dict(values)
    prior_messages = values.get("messages", [])
    run_input["messages"] = [
        *(prior_messages if isinstance(prior_messages, list) else []),
        HumanMessage(content=message),
    ]
    return run_input


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {
        "event": event,
        "data": json.dumps(data, separators=(",", ":"), ensure_ascii=False),
    }


def _stream_message_id(chunk: AIMessageChunk, metadata: Any, run_id: UUID) -> str:
    if chunk.id:
        return chunk.id
    step = metadata.get("langgraph_step", 0) if isinstance(metadata, dict) else 0
    return f"assistant-{run_id}-{step}"


def _public_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _walk_messages(value: Any) -> Iterable[BaseMessage]:
    if isinstance(value, BaseMessage):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_messages(child)
    elif isinstance(value, list | tuple):
        for child in value:
            yield from _walk_messages(child)


def _tool_messages(value: Any) -> Iterable[ToolMessage]:
    for message in _walk_messages(value):
        if (
            isinstance(message, ToolMessage)
            and message.name in {"propose_trading_action", "start_polymarket_backtest"}
            and message.status == "success"
        ):
            yield message


def _proposal_envelope(value: Any) -> UnsignedProposalEnvelope | None:
    if not isinstance(value, str):
        return None
    try:
        return UnsignedProposalEnvelope.model_validate_json(value)
    except ValueError:
        return None


def _backtest_reference(value: Any) -> BacktestRunReference | None:
    if not isinstance(value, str):
        return None
    try:
        return BacktestRunReference.model_validate_json(value)
    except ValueError:
        return None


def _public_thread_items(messages: Any) -> list[PublicThreadItem]:
    if not isinstance(messages, list):
        return []
    items: list[PublicThreadItem] = []
    for index, message in enumerate(messages):
        if not isinstance(message, BaseMessage):
            continue
        identifier = message.id or f"message-{index}"
        if isinstance(message, HumanMessage):
            text = _public_content(message.content)
            if text:
                items.append(PublicMessage(id=identifier, role="user", text=text))
        elif isinstance(message, AIMessage):
            text = _public_content(message.content)
            if text:
                items.append(PublicMessage(id=identifier, role="assistant", text=text))
        elif isinstance(message, ToolMessage):
            if message.status != "success":
                continue
            if message.name == "propose_trading_action":
                envelope = _proposal_envelope(message.content)
                if envelope is None:
                    continue
                items.append(
                    PublicProposal(
                        id=message.tool_call_id or identifier,
                        envelope=envelope,
                    )
                )
            elif message.name == "start_polymarket_backtest":
                backtest = _backtest_reference(message.content)
                if backtest is not None:
                    items.append(
                        PublicBacktest(
                            id=message.tool_call_id or identifier,
                            backtest=backtest,
                        )
                    )
    return items


app = create_app()
