"""Authenticated, stateful chat API shared by all first-party windows."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.security import (
    Principal,
    require_chat_reader,
    require_chat_writer,
)
from chat.errors import ChatError
from chat.events import MESSAGE_COMPLETED, RUN_COMPLETED, RUN_FAILED, ChatEvent
from chat.service import ChatService, get_chat_service

router = APIRouter(prefix="/v1", tags=["chat"])
logger = logging.getLogger(__name__)


class CreateThreadRequest(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=200)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    stream: bool = True
    client_message_id: Optional[UUID] = None


def _service() -> ChatService:
    return get_chat_service()


def _raise_chat_error(exc: ChatError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc), "retryable": exc.retryable},
    )


@router.post("/threads", status_code=status.HTTP_201_CREATED)
async def create_thread(
    body: CreateThreadRequest,
    principal: Principal = Depends(require_chat_writer),
    service: ChatService = Depends(_service),
):
    try:
        return await service.create_thread(
            principal.user_id,
            title=body.title,
        )
    except ChatError as exc:
        _raise_chat_error(exc)


@router.get("/threads")
async def list_threads(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_chat_reader),
    service: ChatService = Depends(_service),
):
    try:
        rows = await service.list_threads(principal.user_id, limit, offset)
    except ChatError as exc:
        _raise_chat_error(exc)
    return {"threads": rows, "count": len(rows), "limit": limit, "offset": offset}


@router.get("/threads/{thread_id}/messages")
async def list_messages(
    thread_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_chat_reader),
    service: ChatService = Depends(_service),
):
    try:
        rows = await service.get_messages(principal.user_id, str(thread_id), limit)
    except ChatError as exc:
        _raise_chat_error(exc)
    return {"thread_id": str(thread_id), "messages": rows, "count": len(rows)}


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: UUID,
    principal: Principal = Depends(require_chat_writer),
    service: ChatService = Depends(_service),
):
    try:
        await service.delete_thread(principal.user_id, str(thread_id))
    except ChatError as exc:
        _raise_chat_error(exc)
    return None


async def _produce_events(iterator, queue: asyncio.Queue) -> None:
    try:
        async for event in iterator:
            await queue.put(event)
    except ChatError as exc:
        await queue.put(
            ChatEvent(
                RUN_FAILED,
                {
                    "run_id": None,
                    "thread_id": None,
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": exc.retryable,
                },
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Unhandled error while producing chat stream events")
        await queue.put(
            ChatEvent(
                RUN_FAILED,
                {
                    "run_id": None,
                    "thread_id": None,
                    "code": "internal_error",
                    "message": "The chat request failed.",
                    "retryable": True,
                },
            )
        )
    finally:
        await queue.put(None)


async def _sse_with_heartbeats(iterator, request: Request):
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    producer = asyncio.create_task(_produce_events(iterator, queue))
    heartbeat = max(5, int(os.getenv("CHAT_SSE_HEARTBEAT_SECONDS", "15")))
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if event is None:
                break
            yield event.to_sse()
    finally:
        if not producer.done():
            producer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await producer


@router.post("/threads/{thread_id}/messages")
async def send_message(
    thread_id: UUID,
    body: SendMessageRequest,
    request: Request,
    response: Response,
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=200,
    ),
    principal: Principal = Depends(require_chat_writer),
    service: ChatService = Depends(_service),
):
    key = idempotency_key or str(uuid4())
    response.headers["Idempotency-Key"] = key
    response.headers["X-Persistence-Mode"] = service.persistence_mode
    try:
        service.validate_message(body.content, key)
        # Resolve ownership before StreamingResponse commits a 200 status.
        await service.get_messages(principal.user_id, str(thread_id), limit=1)
    except ChatError as exc:
        _raise_chat_error(exc)

    iterator = service.stream_message(
        user_id=principal.user_id,
        thread_id=str(thread_id),
        content=body.content,
        idempotency_key=key,
        client_message_id=(
            str(body.client_message_id) if body.client_message_id else None
        ),
        create_thread_if_missing=False,
    )

    if body.stream:
        return StreamingResponse(
            _sse_with_heartbeats(iterator, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Persistence-Mode": service.persistence_mode,
                "Idempotency-Key": key,
            },
        )

    completed_message: Optional[Dict[str, Any]] = None
    completed_run: Optional[Dict[str, Any]] = None
    failure: Optional[Dict[str, Any]] = None
    try:
        async for event in iterator:
            if event.event == MESSAGE_COMPLETED:
                completed_message = event.data.get("message")
            elif event.event == RUN_COMPLETED:
                completed_run = event.data
            elif event.event == RUN_FAILED:
                failure = event.data
    except ChatError as exc:
        _raise_chat_error(exc)

    if failure:
        code = failure.get("code", "chat_error")
        status_code = {
            "thread_not_found": 404,
            "thread_busy": 409,
            "unsafe_command": 403,
            "invalid_chat_request": 422,
            "persistence_unavailable": 503,
            "run_timeout": 504,
            "query_limit_exceeded": 429,
            "daily_budget_exceeded": 429,
        }.get(code, 502)
        raise HTTPException(status_code=status_code, detail=failure)
    if not completed_message or not completed_run:
        raise HTTPException(
            status_code=502,
            detail={"code": "incomplete_run", "message": "Chat run did not complete."},
        )
    return {
        "thread_id": str(thread_id),
        "run_id": completed_run["run_id"],
        "message": completed_message,
        "tool_calls": completed_run.get("tool_calls", []),
        "idempotency_key": key,
    }


@router.get("/runs/{run_id}")
async def get_run(
    run_id: UUID,
    principal: Principal = Depends(require_chat_reader),
    service: ChatService = Depends(_service),
):
    try:
        run = await service.get_run(principal.user_id, str(run_id))
    except ChatError as exc:
        _raise_chat_error(exc)
    if not run:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": "Run not found."},
        )
    return run
