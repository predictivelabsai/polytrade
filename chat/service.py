"""The single transport-neutral backend for every PolyTrade chat window."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from functools import lru_cache
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import uuid4
from weakref import WeakValueDictionary

from .agent_factory import get_chat_agent
from .commands import CommandRouter
from .errors import (
    ChatError,
    InvalidChatRequest,
    PersistenceUnavailable,
    ThreadBusy,
)
from .events import (
    AGENT_ROUTE,
    MESSAGE_COMPLETED,
    MESSAGE_DELTA,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_STARTED,
    TOOL_COMPLETED,
    TOOL_STARTED,
    ChatEvent,
)
from .repository import ChatRepository, get_chat_repository
from .routing import AgentRoute, route_message
from .runtimes import get_runtime_agent

logger = logging.getLogger(__name__)


async def _safe_start_activity(run_id, user_id, thread_id, content):
    try:
        from .activity import start_user_log
        await start_user_log(run_id, user_id, thread_id, content)
    except Exception:
        logger.warning("Could not start user activity log", exc_info=True)


async def _safe_finish_activity(run_id, *, response, framework, status="completed",
                                error=None, metadata=None):
    try:
        from .activity import complete_user_log
        await complete_user_log(run_id, response=response, framework=framework,
                                status=status, error=error, metadata=metadata)
    except Exception:
        logger.warning("Could not complete user activity log", exc_info=True)


def _text_content(content: Any) -> str:
    """Normalize text blocks returned by different LangChain providers."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {"text", "text_delta"}:
                parts.append(str(block.get("text") or block.get("content") or ""))
        return "".join(parts)
    return ""


def _redacted_args(value: Any, depth: int = 0) -> Any:
    """Return small, safe tool arguments suitable for a client trace pane."""
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:20]:
            lowered = str(key).lower()
            if any(
                marker in lowered
                for marker in (
                    "key",
                    "token",
                    "secret",
                    "password",
                    "private",
                    "credential",
                    "authorization",
                    "cookie",
                )
            ):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _redacted_args(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_redacted_args(item, depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


class ChatService:
    """Orchestrate authentication-scoped history, commands, agent runs, and storage."""

    def __init__(
        self,
        repository: Optional[ChatRepository] = None,
        agent_factory=get_chat_agent,
        command_router: Optional[CommandRouter] = None,
        max_history_messages: Optional[int] = None,
        max_concurrent_runs: Optional[int] = None,
        run_timeout_seconds: Optional[float] = None,
    ) -> None:
        self.repository = repository or get_chat_repository()
        self._agent_factory = agent_factory
        self._command_router = command_router or CommandRouter()
        self._max_history_messages = max_history_messages or int(
            os.getenv("CHAT_MAX_HISTORY_MESSAGES", "50")
        )
        concurrency = max_concurrent_runs or int(
            os.getenv("CHAT_MAX_CONCURRENT_RUNS", "8")
        )
        self._run_timeout_seconds = (
            max(0.01, float(run_timeout_seconds))
            if run_timeout_seconds is not None
            else max(10, int(os.getenv("CHAT_RUN_TIMEOUT_SECONDS", "300")))
        )
        self._run_slots = asyncio.Semaphore(max(1, concurrency))
        self._thread_locks: WeakValueDictionary[str, asyncio.Lock] = (
            WeakValueDictionary()
        )

    @property
    def persistence_mode(self) -> str:
        return "postgres" if self.repository.persistent else "memory"

    def _thread_lock(self, thread_id: str) -> asyncio.Lock:
        return self._thread_locks.setdefault(str(thread_id), asyncio.Lock())

    def validate_message(self, content: str, idempotency_key: str) -> str:
        """Validate a message before an HTTP streaming response commits headers."""
        normalized = content.strip()
        if not normalized:
            raise InvalidChatRequest("Message content cannot be empty.")
        if len(normalized) > int(os.getenv("CHAT_MAX_MESSAGE_CHARS", "20000")):
            raise InvalidChatRequest("Message content is too long.")
        if not idempotency_key or len(idempotency_key) > 200:
            raise InvalidChatRequest("A valid idempotency key is required.")
        return normalized

    async def _mark_run_failed(
        self,
        run_id: str,
        user_id: Optional[str],
        code: str,
        message: str,
        *,
        status: str = "failed",
    ) -> None:
        """Best-effort terminal persistence that never hides the original failure."""
        try:
            await self.repository.fail_run(
                run_id, user_id, code, message, status=status
            )
        except Exception:
            logger.exception("Could not persist terminal state for chat run %s", run_id)

    async def _get_persisted_message(
        self,
        thread_id: str,
        user_id: Optional[str],
        message_id: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            return await self.repository.get_message(
                thread_id, user_id, message_id
            )
        except ChatError:
            raise
        except Exception as exc:
            raise PersistenceUnavailable(
                "Chat persistence is unavailable."
            ) from exc

    async def create_thread(
        self,
        user_id: str,
        title: str = "New chat",
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not user_id:
            raise InvalidChatRequest("An authenticated user is required.")
        try:
            return await self.repository.create_thread(user_id, title, thread_id)
        except ChatError:
            raise
        except Exception as exc:
            logger.exception("Could not create chat thread")
            raise PersistenceUnavailable("Chat persistence is unavailable.") from exc

    async def list_threads(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> List[Dict[str, Any]]:
        try:
            return await self.repository.list_threads(user_id, limit, offset)
        except ChatError:
            raise
        except Exception as exc:
            logger.exception("Could not list chat threads")
            raise PersistenceUnavailable("Chat persistence is unavailable.") from exc

    async def delete_thread(self, user_id: str, thread_id: str) -> bool:
        lock = self._thread_lock(thread_id)
        if lock.locked():
            raise ThreadBusy("Cannot delete a thread with an active run.")
        try:
            async with lock:
                return await self.repository.delete_thread(thread_id, user_id)
        except ChatError:
            raise
        except Exception as exc:
            logger.exception("Could not delete chat thread")
            raise PersistenceUnavailable("Chat persistence is unavailable.") from exc

    async def get_messages(
        self,
        user_id: Optional[str],
        thread_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        try:
            return await self.repository.load_messages(
                thread_id,
                user_id,
                min(limit or self._max_history_messages, 200),
            )
        except ChatError:
            raise
        except Exception as exc:
            logger.exception("Could not load chat messages")
            raise PersistenceUnavailable("Chat persistence is unavailable.") from exc

    async def get_run(
        self, user_id: Optional[str], run_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            run = await self.repository.get_run(run_id, user_id)
            if run and run.get("assistant_message_id"):
                run["message"] = await self._get_persisted_message(
                    run["thread_id"], user_id, run["assistant_message_id"]
                )
        except ChatError:
            raise
        except Exception as exc:
            logger.exception("Could not load chat run")
            raise PersistenceUnavailable("Chat persistence is unavailable.") from exc
        if not run:
            return None
        public_fields = {
            "run_id",
            "thread_id",
            "user_message_id",
            "assistant_message_id",
            "status",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
            "message",
            "agent_framework",
            "agent_name",
            "requested_runtime",
        }
        return {key: value for key, value in run.items() if key in public_fields}

    async def stream_message(
        self,
        *,
        user_id: Optional[str],
        thread_id: str,
        content: str,
        idempotency_key: str,
        client_message_id: Optional[str] = None,
        assistant_message_id: Optional[str] = None,
        create_thread_if_missing: bool = True,
    ) -> AsyncIterator[ChatEvent]:
        """Persist and stream one turn for an owned conversation."""
        content = self.validate_message(content, idempotency_key)
        route = route_message(content)
        request_fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()

        lock = self._thread_lock(thread_id)
        if lock.locked():
            raise ThreadBusy("This thread already has an active run.")

        async with lock, self._run_slots:
            title = content[:80]
            try:
                await self.repository.ensure_thread(
                    thread_id,
                    user_id,
                    title=title,
                    create_if_missing=create_thread_if_missing,
                )
            except ChatError:
                raise
            except Exception as exc:
                logger.exception("Could not prepare chat thread")
                raise PersistenceUnavailable(
                    "Chat persistence is unavailable."
                ) from exc

            user_message_id = client_message_id or str(uuid4())
            assistant_id = assistant_message_id or str(uuid4())
            try:
                run, created = await self.repository.start_run(
                    thread_id,
                    user_id,
                    idempotency_key,
                    request_fingerprint,
                    user_message_id,
                    assistant_id,
                    route.runtime,
                    route.requested_runtime,
                )
            except ChatError:
                raise
            except Exception as exc:
                logger.exception("Could not start chat run")
                raise PersistenceUnavailable(
                    "Chat persistence is unavailable."
                ) from exc
            run_id = run["run_id"]

            if not created:
                stored_fingerprint = run.get("request_fingerprint")
                if (
                    stored_fingerprint
                    and stored_fingerprint != request_fingerprint
                ):
                    raise InvalidChatRequest(
                        "This idempotency key was already used for another message."
                    )
                if not stored_fingerprint and run.get("user_message_id"):
                    prior_message = await self._get_persisted_message(
                        thread_id, user_id, run["user_message_id"]
                    )
                    if (
                        prior_message
                        and prior_message.get("content") != content
                    ):
                        raise InvalidChatRequest(
                            "This idempotency key was already used for another message."
                        )
                async for replay_event in self._replay_run(run, user_id):
                    yield replay_event
                return

            if self.repository.persistent:
                await _safe_start_activity(run_id, user_id, thread_id, content)

            yield ChatEvent(
                RUN_STARTED,
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "user_message_id": user_message_id,
                    "assistant_message_id": assistant_id,
                    "replayed": False,
                },
            )

            try:
                try:
                    await self.repository.save_message(
                        thread_id,
                        user_id,
                        "user",
                        content,
                        user_message_id,
                    )
                    history = await self.repository.load_messages(
                        thread_id,
                        user_id,
                        self._max_history_messages,
                    )
                except ChatError:
                    raise
                except Exception as exc:
                    raise PersistenceUnavailable(
                        "Chat persistence is unavailable."
                    ) from exc

                final_content = ""
                final_metadata: Dict[str, Any] = {}
                tool_calls: List[Dict[str, Any]] = []
                async with asyncio.timeout(self._run_timeout_seconds):
                    from db.attribution import bind_agent, reset_agent

                    attribution_tokens = bind_agent(route.runtime)
                    try:
                        turn = self._stream_turn(
                        history=history,
                        current_content=route.content,
                        route=route,
                        user_id=user_id,
                        run_id=run_id,
                        thread_id=thread_id,
                        assistant_message_id=assistant_id,
                        )
                        async for event in turn:
                            if event.event == MESSAGE_COMPLETED:
                                final_content = event.data["message"]["content"]
                                final_metadata = event.data["message"].get("metadata", {})
                            elif event.event == TOOL_STARTED:
                                tool_calls.append(
                                {
                                    "tool_call_id": event.data["tool_call_id"],
                                    "name": event.data["name"],
                                    "args": event.data.get("args", {}),
                                }
                                )
                            elif event.event == RUN_FAILED:
                                raise ChatError(event.data.get("message", "Chat run failed."))
                            else:
                                yield event
                    finally:
                        reset_agent(attribution_tokens)

                if not final_content:
                    raise ChatError("The model returned an empty response.")

                try:
                    assistant = await self.repository.save_message(
                        thread_id,
                        user_id,
                        "assistant",
                        final_content,
                        assistant_id,
                        metadata={"run_id": run_id, "tool_calls": tool_calls, **final_metadata},
                    )
                    await self.repository.complete_run(
                        run_id, user_id, assistant_id,
                        final_metadata.get("runtime"), final_metadata.get("agent"),
                    )
                    if self.repository.persistent:
                        await _safe_finish_activity(
                            run_id, response=final_content,
                            framework=final_metadata.get("runtime", route.runtime),
                            metadata=final_metadata,
                        )
                except ChatError:
                    raise
                except Exception as exc:
                    raise PersistenceUnavailable(
                        "Chat persistence is unavailable."
                    ) from exc

                yield ChatEvent(
                    MESSAGE_COMPLETED,
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "message": assistant,
                    },
                )
                yield ChatEvent(
                    RUN_COMPLETED,
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "message_id": assistant_id,
                        "tool_calls": tool_calls,
                    },
            )
            except asyncio.CancelledError:
                await self._mark_run_failed(
                    run_id,
                    user_id,
                    "cancelled",
                    "Client disconnected.",
                    status="cancelled",
                )
                if self.repository.persistent:
                    await _safe_finish_activity(
                        run_id, response="Client disconnected.", framework=route.runtime,
                        status="cancelled", error="Client disconnected.",
                        metadata={"code": "cancelled"},
                    )
                raise
            except asyncio.TimeoutError:
                message = "The research agent timed out."
                await self._mark_run_failed(
                    run_id, user_id, "run_timeout", message, status="failed"
                )
                if self.repository.persistent:
                    await _safe_finish_activity(
                        run_id, response=message, framework=route.runtime,
                        status="failed", error=message, metadata={"code": "run_timeout"},
                    )
                yield ChatEvent(
                    RUN_FAILED,
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "code": "run_timeout",
                        "message": message,
                        "retryable": True,
                    },
                )
            except ChatError as exc:
                await self._mark_run_failed(
                    run_id, user_id, exc.code, str(exc), status="failed"
                )
                blocked = exc.code in {"query_limit_exceeded", "daily_budget_exceeded"}
                if self.repository.persistent:
                    await _safe_finish_activity(
                        run_id, response=str(exc), framework=route.runtime,
                        status="blocked" if blocked else "failed", error=str(exc),
                        metadata={"code": exc.code},
                    )
                yield ChatEvent(
                    RUN_FAILED,
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": exc.retryable,
                    },
                )
            except Exception:
                logger.exception("Chat run %s failed", run_id)
                public_message = "The research agent could not complete this request."
                if os.getenv("POLYCODE_DEBUG", "").lower() in {"1", "true", "yes"}:
                    public_message = "The research agent failed; inspect server logs."
                await self._mark_run_failed(
                    run_id, user_id, "agent_error", public_message, status="failed"
                )
                if self.repository.persistent:
                    await _safe_finish_activity(
                        run_id, response=public_message, framework=route.runtime,
                        status="failed", error=public_message,
                        metadata={"code": "agent_error"},
                    )
                yield ChatEvent(
                    RUN_FAILED,
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "code": "agent_error",
                        "message": public_message,
                        "retryable": True,
                    },
                )

    async def _replay_run(
        self, run: Dict[str, Any], user_id: Optional[str]
    ) -> AsyncIterator[ChatEvent]:
        status = run.get("status")
        run_id = run["run_id"]
        thread_id = run["thread_id"]
        if status == "running":
            raise ThreadBusy("The idempotent request is still running.")

        yield ChatEvent(
            RUN_STARTED,
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "user_message_id": run.get("user_message_id"),
                "assistant_message_id": run.get("assistant_message_id"),
                "replayed": True,
            },
        )

        if status == "completed":
            message = await self._get_persisted_message(
                thread_id, user_id, run["assistant_message_id"]
            )
            if not message:
                raise PersistenceUnavailable(
                    "The persisted chat response is unavailable."
                )
            yield ChatEvent(
                MESSAGE_COMPLETED,
                {"run_id": run_id, "thread_id": thread_id, "message": message},
            )
            yield ChatEvent(
                RUN_COMPLETED,
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "message_id": run.get("assistant_message_id"),
                    "replayed": True,
                },
            )
            return

        yield ChatEvent(
            RUN_FAILED,
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "code": run.get("error_code") or status or "run_failed",
                "message": run.get("error_message") or "The prior run failed.",
                "retryable": status in {"cancelled"},
                "replayed": True,
            },
        )

    async def stream_stateless(
        self,
        *,
        content: str,
        history: Optional[List[Dict[str, str]]] = None,
        user_id: Optional[str] = None,
    ) -> AsyncIterator[ChatEvent]:
        """Compatibility entry point using the canonical agent without persistence."""
        content = content.strip()
        if not content:
            raise InvalidChatRequest("Message content cannot be empty.")

        route = route_message(content)
        run_id = str(uuid4())
        thread_id = str(uuid4())
        assistant_id = str(uuid4())
        messages = [
            {"role": row.get("role"), "content": row.get("content", "")}
            for row in (history or [])
            if row.get("role") in {"user", "assistant"}
            and str(row.get("content", "")).strip()
        ]
        if not (
            messages
            and messages[-1]["role"] == "user"
            and messages[-1]["content"].strip() == content
        ):
            messages.append({"role": "user", "content": content})

        yield ChatEvent(
            RUN_STARTED,
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "assistant_message_id": assistant_id,
                "stateless": True,
            },
        )
        try:
            final = ""
            async with asyncio.timeout(self._run_timeout_seconds):
                from db.attribution import bind_agent, reset_agent

                tokens = bind_agent(route.runtime)
                try:
                    async for event in self._stream_turn(
                        history=messages,
                        current_content=route.content,
                        route=route,
                        user_id=user_id,
                        run_id=run_id,
                        thread_id=thread_id,
                        assistant_message_id=assistant_id,
                    ):
                        if event.event == MESSAGE_COMPLETED:
                            final = event.data["message"]["content"]
                        else:
                            yield event
                finally:
                    reset_agent(tokens)
            if not final:
                raise ChatError("The model returned an empty response.")
            message = {
                "message_id": assistant_id,
                "role": "assistant",
                "content": final,
            }
            yield ChatEvent(
                MESSAGE_COMPLETED,
                {"run_id": run_id, "thread_id": thread_id, "message": message},
            )
            yield ChatEvent(
                RUN_COMPLETED,
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "message_id": assistant_id,
                },
            )
        except asyncio.TimeoutError:
            yield ChatEvent(
                RUN_FAILED,
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "code": "run_timeout",
                    "message": "The research agent timed out.",
                    "retryable": True,
                },
            )
        except ChatError as exc:
            yield ChatEvent(
                RUN_FAILED,
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": exc.retryable,
                },
            )
        except Exception:
            logger.exception("Stateless chat run %s failed", run_id)
            yield ChatEvent(
                RUN_FAILED,
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "code": "agent_error",
                    "message": "The research agent could not complete this request.",
                    "retryable": True,
                },
            )

    async def _stream_turn(
        self,
        *,
        history: List[Dict[str, Any]],
        current_content: str,
        route: AgentRoute | None = None,
        user_id: Optional[str],
        run_id: str,
        thread_id: str,
        assistant_message_id: str,
    ) -> AsyncIterator[ChatEvent]:
        route = route or route_message(current_content)
        selected_runtime = route.runtime
        route_data = {
            "run_id": run_id,
            "thread_id": thread_id,
            "runtime": selected_runtime,
            "agent": "Hermes" if selected_runtime == "hermes" else "DeepAgents",
            "requested_runtime": route.requested_runtime,
            "fallback": False,
        }
        yield ChatEvent(AGENT_ROUTE, route_data)

        hermes_help_requested = (
            selected_runtime == "hermes"
            and route.requested_runtime == "hermes"
            and current_content.lower() in {"help", "h", "?"}
        )
        if route.requested_runtime and (not current_content or hermes_help_requested):
            if hermes_help_requested:
                from .prompts import HERMES_HELP
                help_text = HERMES_HELP
            else:
                help_text = (
                    f"Use `/{'hermes' if selected_runtime == 'hermes' else 'deepagent'} "
                    "<question>`. Unprefixed messages use DeepAgents."
                )
            yield ChatEvent(MESSAGE_DELTA, {"run_id": run_id, "thread_id": thread_id,
                "message_id": assistant_message_id, "delta": help_text})
            yield ChatEvent(MESSAGE_COMPLETED, {"run_id": run_id, "thread_id": thread_id,
                "message": {"message_id": assistant_message_id, "role": "assistant",
                "content": help_text, "metadata": route_data}})
            return

        if current_content.lower() == "/usage":
            if not user_id:
                usage_text = "Sign in to view your AI usage."
            elif not self.repository.persistent:
                usage_text = (
                    "# AI usage today\n\nUsage accounting requires PostgreSQL persistence."
                )
            else:
                from utils.auth import get_provider_key_status
                from .usage import get_usage_status, render_usage_status
                provider = (os.getenv("MODEL_PROVIDER") or "openai").lower()
                key_status = await get_provider_key_status(user_id, "xai")
                status = await get_usage_status(
                    user_id,
                    has_byok=bool(key_status["configured"]) and provider == "xai",
                )
                usage_text = render_usage_status(status)
            usage_meta = {**route_data, "runtime": "command", "agent": "Usage"}
            yield ChatEvent(MESSAGE_DELTA, {"run_id": run_id, "thread_id": thread_id,
                "message_id": assistant_message_id, "delta": usage_text})
            yield ChatEvent(MESSAGE_COMPLETED, {"run_id": run_id, "thread_id": thread_id,
                "message": {"message_id": assistant_message_id, "role": "assistant",
                "content": usage_text, "metadata": usage_meta}})
            return

        command_output = None
        command = await self._command_router.run(current_content, user_id)
        if command is not None:
            tool_call_id = str(uuid4())
            yield ChatEvent(
                TOOL_STARTED,
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "tool_call_id": tool_call_id,
                    "name": f"command:{command.command}",
                    "args": {},
                },
            )
            yield ChatEvent(
                TOOL_COMPLETED,
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "tool_call_id": tool_call_id,
                    "name": f"command:{command.command}",
                },
            )
            if not route.requested_runtime:
                yield ChatEvent(
                    MESSAGE_DELTA,
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "message_id": assistant_message_id,
                        "delta": command.content,
                    },
                )
                yield ChatEvent(
                    MESSAGE_COMPLETED,
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "message": {
                            "message_id": assistant_message_id,
                            "role": "assistant",
                            "content": command.content,
                            "metadata": route_data,
                        },
                    },
                )
                return

            # Explicit agent selection runs the deterministic command first,
            # then asks that agent to interpret the exact same source result.
            source_result = command.content[:12000]
            command_output = command.content
            current_content = (
                f"The user ran `{current_content}`. Analyze the PolyTrade result below. "
                "Keep every reported figure unchanged and base the response only on "
                "the supplied evidence. Use four concise sections: Key findings, "
                "Interpretation, Risks and limitations, and Confidence (Low, Medium, "
                "or High, with a short reason). Do not claim personal experience or "
                "certainty that the data does not support.\n\n"
                f"{source_result}"
            )

        from langchain_core.messages import AIMessage, HumanMessage

        langchain_messages = []
        for index, message in enumerate(history):
            text = str(message.get("content") or "").strip()
            if not text:
                continue
            if message.get("role") == "user":
                if index == len(history) - 1:
                    text = current_content
                langchain_messages.append(HumanMessage(content=text))
            elif message.get("role") == "assistant":
                langchain_messages.append(AIMessage(content=text))

        from .usage import (
            TokenUsage, allowance_warning, authorize_query, budget_warning,
            extract_usage, record_usage, refund_query,
        )
        provider = (os.getenv("MODEL_PROVIDER") or "openai").lower()
        model_name = os.getenv("MODEL") or "gpt-4.1-mini"
        user_api_key = None
        if (self.repository.persistent and user_id
                and selected_runtime == "deepagents" and provider == "xai"):
            from utils.auth import get_provider_api_key
            user_api_key = await get_provider_api_key(user_id, "xai")
        authorization = (
            await authorize_query(
                user_id, has_byok=bool(user_api_key) and selected_runtime == "deepagents"
            )
            if self.repository.persistent else None
        )
        measured = TokenUsage()

        def collect_usage(raw: Any) -> None:
            nonlocal measured
            found = extract_usage(raw)
            if found.total_tokens:
                measured.input_tokens += found.input_tokens
                measured.output_tokens += found.output_tokens
                measured.total_tokens += found.total_tokens
                measured.quality = "measured"

        streamed_content = ""
        authoritative_content = ""
        fallback = False
        if command_output:
            analysis_heading = "\n\n### Analysis & confidence\n\n"
            yield ChatEvent(MESSAGE_DELTA, {"run_id": run_id,
                "thread_id": thread_id, "message_id": assistant_message_id,
                "delta": command_output + analysis_heading})
        while True:
            agent = get_runtime_agent(
                selected_runtime, user_id=user_id, thread_id=thread_id,
                deepagent_factory=self._agent_factory,
                api_key=user_api_key if selected_runtime == "deepagents" else None,
            )
            try:
                raw_events = agent.astream_events(
                    {"messages": langchain_messages}, version="v2"
                )
                async for raw in raw_events:
                    kind = raw.get("event", "")
                    data = raw.get("data", {}) or {}

                    if kind == "on_chat_model_stream":
                        chunk = data.get("chunk")
                        collect_usage(chunk)
                        delta = _text_content(getattr(chunk, "content", ""))
                        if delta:
                            streamed_content += delta
                            yield ChatEvent(MESSAGE_DELTA, {"run_id": run_id,
                                "thread_id": thread_id, "message_id": assistant_message_id,
                                "delta": delta})
                    elif kind == "on_tool_start":
                        tool_call_id = str(raw.get("run_id") or uuid4())
                        yield ChatEvent(TOOL_STARTED, {"run_id": run_id,
                            "thread_id": thread_id, "tool_call_id": tool_call_id,
                            "name": raw.get("name") or "tool",
                            "args": _redacted_args(data.get("input") or {})})
                    elif kind == "on_tool_end":
                        tool_call_id = str(raw.get("run_id") or "")
                        yield ChatEvent(TOOL_COMPLETED, {"run_id": run_id,
                            "thread_id": thread_id, "tool_call_id": tool_call_id,
                            "name": raw.get("name") or "tool"})
                    elif kind == "on_chain_end":
                        collect_usage(data.get("usage"))
                        output = data.get("output")
                        if isinstance(output, dict):
                            output_messages = output.get("messages") or []
                            if output_messages:
                                candidate = _text_content(
                                    getattr(output_messages[-1], "content", "")
                                )
                                if candidate:
                                    authoritative_content = candidate
                break
            except Exception:
                if selected_runtime != "hermes" or streamed_content:
                    await refund_query(user_id, authorization)
                    raise
                selected_runtime = "deepagents"
                fallback = True
                from db.attribution import agent_framework, agent_name
                agent_framework.set("deepagents")
                agent_name.set("DeepAgents (Hermes unavailable)")
                route_data = {**route_data, "runtime": "deepagents",
                    "agent": "DeepAgents (Hermes unavailable)", "fallback": True}
                yield ChatEvent(AGENT_ROUTE, route_data)
                continue

        agent_content = authoritative_content or streamed_content
        if not agent_content:
            await refund_query(user_id, authorization)
            raise ChatError("The model returned an empty response.")
        if command_output:
            final_content = (
                command_output
                + "\n\n### Analysis & confidence\n\n"
                + agent_content
            )
        else:
            final_content = agent_content
        if user_id and authorization is not None:
            try:
                await record_usage(
                    user_id=user_id, thread_id=thread_id, request_id=run_id,
                    agent=route_data["runtime"],
                    provider="xai" if route_data["runtime"] == "hermes" else provider,
                    model=(os.getenv("HERMES_API_MODEL", "hermes-agent")
                           if route_data["runtime"] == "hermes" else model_name),
                    funding_source=authorization.funding_source,
                    prompt=current_content, response=final_content, usage=measured,
                )
                warnings = [allowance_warning(authorization),
                            await budget_warning(authorization.funding_source)]
                warning_text = "\n\n".join(
                    f"> **AI usage warning:** {item}" for item in warnings if item
                )
                if warning_text:
                    suffix = "\n\n" + warning_text + " Use `/usage` for details."
                    final_content += suffix
                    yield ChatEvent(MESSAGE_DELTA, {"run_id": run_id,
                        "thread_id": thread_id, "message_id": assistant_message_id,
                        "delta": suffix})
            except Exception:
                logger.warning("Could not persist LLM usage", exc_info=True)
        yield ChatEvent(MESSAGE_COMPLETED, {"run_id": run_id,
            "thread_id": thread_id, "message": {"message_id": assistant_message_id,
            "role": "assistant", "content": final_content,
            "metadata": {**route_data, "fallback": fallback}}})


@lru_cache(maxsize=1)
def get_chat_service() -> ChatService:
    return ChatService()
