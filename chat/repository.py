"""Conversation persistence with ownership and idempotency guarantees."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Protocol, Tuple
from uuid import UUID, uuid4

from .errors import InvalidChatRequest, ThreadAccessDenied, ThreadBusy, ThreadNotFound


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value: Optional[str]) -> Optional[UUID]:
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def _serialise(record: Any) -> Dict[str, Any]:
    if not record:
        return {}
    result: Dict[str, Any] = {}
    for key, value in dict(record).items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


class ChatRepository(Protocol):
    persistent: bool

    async def create_thread(
        self,
        user_id: Optional[str],
        title: str = "New chat",
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    async def ensure_thread(
        self,
        thread_id: str,
        user_id: Optional[str],
        title: Optional[str] = None,
        create_if_missing: bool = True,
    ) -> Dict[str, Any]: ...

    async def list_threads(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> List[Dict[str, Any]]: ...

    async def delete_thread(self, thread_id: str, user_id: str) -> bool: ...

    async def load_messages(
        self,
        thread_id: str,
        user_id: Optional[str],
        limit: int = 50,
    ) -> List[Dict[str, Any]]: ...

    async def save_message(
        self,
        thread_id: str,
        user_id: Optional[str],
        role: str,
        content: str,
        message_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]: ...

    async def start_run(
        self,
        thread_id: str,
        user_id: Optional[str],
        idempotency_key: str,
        request_fingerprint: str,
        user_message_id: str,
        assistant_message_id: str,
        agent_framework: str = "deepagents",
        requested_runtime: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]: ...

    async def complete_run(
        self,
        run_id: str,
        user_id: Optional[str],
        assistant_message_id: str,
        agent_framework: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> None: ...

    async def fail_run(
        self,
        run_id: str,
        user_id: Optional[str],
        code: str,
        message: str,
        status: str = "failed",
    ) -> None: ...

    async def get_run(
        self, run_id: str, user_id: Optional[str]
    ) -> Optional[Dict[str, Any]]: ...

    async def get_message(
        self, thread_id: str, user_id: Optional[str], message_id: str
    ) -> Optional[Dict[str, Any]]: ...


class MemoryChatRepository:
    """Process-local development/test store used only when no DB URL is configured."""

    persistent = False

    def __init__(self) -> None:
        self._threads: Dict[str, Dict[str, Any]] = {}
        self._messages: Dict[str, List[Dict[str, Any]]] = {}
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _owns(thread: Dict[str, Any], user_id: Optional[str]) -> bool:
        return thread.get("user_id") == user_id

    def _owned_thread(self, thread_id: str, user_id: Optional[str]) -> Dict[str, Any]:
        thread = self._threads.get(str(thread_id))
        if not thread:
            raise ThreadNotFound("Thread not found.")
        if not self._owns(thread, user_id):
            raise ThreadAccessDenied("Thread not found.")
        return thread

    async def create_thread(
        self,
        user_id: Optional[str],
        title: str = "New chat",
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with self._lock:
            tid = str(thread_id or uuid4())
            if tid in self._threads:
                raise ThreadAccessDenied("Thread already exists.")
            now = _now().isoformat()
            row = {
                "thread_id": tid,
                "user_id": user_id,
                "title": title[:200] or "New chat",
                "created_at": now,
                "updated_at": now,
            }
            self._threads[tid] = row
            self._messages[tid] = []
            return dict(row)

    async def ensure_thread(
        self,
        thread_id: str,
        user_id: Optional[str],
        title: Optional[str] = None,
        create_if_missing: bool = True,
    ) -> Dict[str, Any]:
        async with self._lock:
            existing = self._threads.get(str(thread_id))
            if existing:
                if not self._owns(existing, user_id):
                    raise ThreadAccessDenied("Thread not found.")
                return dict(existing)
            if not create_if_missing:
                raise ThreadNotFound("Thread not found.")
            now = _now().isoformat()
            row = {
                "thread_id": str(thread_id),
                "user_id": user_id,
                "title": (title or "New chat")[:200],
                "created_at": now,
                "updated_at": now,
            }
            self._threads[str(thread_id)] = row
            self._messages[str(thread_id)] = []
            return dict(row)

    async def list_threads(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> List[Dict[str, Any]]:
        rows = [t for t in self._threads.values() if t.get("user_id") == user_id]
        rows.sort(key=lambda row: row["updated_at"], reverse=True)
        return [dict(row) for row in rows[offset : offset + limit]]

    async def delete_thread(self, thread_id: str, user_id: str) -> bool:
        async with self._lock:
            self._owned_thread(thread_id, user_id)
            stale_before = _now().timestamp() - max(
                60, int(os.getenv("CHAT_STALE_RUN_SECONDS", "600"))
            )
            for row in self._runs.values():
                if (
                    row["thread_id"] == str(thread_id)
                    and row["status"] == "running"
                    and datetime.fromisoformat(row["started_at"]).timestamp()
                    < stale_before
                ):
                    row.update(
                        status="abandoned",
                        error_code="stale_run",
                        error_message="Run was abandoned after a server interruption.",
                        finished_at=_now().isoformat(),
                    )
            if any(
                row["thread_id"] == str(thread_id) and row["status"] == "running"
                for row in self._runs.values()
            ):
                raise ThreadBusy("Cannot delete a thread with an active run.")
            self._threads.pop(str(thread_id), None)
            self._messages.pop(str(thread_id), None)
            for run_id in [
                rid
                for rid, row in self._runs.items()
                if row["thread_id"] == str(thread_id)
            ]:
                self._runs.pop(run_id, None)
            return True

    async def load_messages(
        self,
        thread_id: str,
        user_id: Optional[str],
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        self._owned_thread(thread_id, user_id)
        return [
            dict(message)
            for message in self._messages.get(str(thread_id), [])[-limit:]
        ]

    async def save_message(
        self,
        thread_id: str,
        user_id: Optional[str],
        role: str,
        content: str,
        message_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        async with self._lock:
            thread = self._owned_thread(thread_id, user_id)
            existing = next(
                (
                    item
                    for item in self._messages[str(thread_id)]
                    if item["message_id"] == str(message_id)
                ),
                None,
            )
            if existing:
                raise InvalidChatRequest(
                    "This client message ID has already been used."
                )
            row = {
                "message_id": str(message_id),
                "thread_id": str(thread_id),
                "role": role,
                "content": content,
                "metadata": metadata,
                "created_at": _now().isoformat(),
            }
            self._messages[str(thread_id)].append(row)
            thread["updated_at"] = row["created_at"]
            return dict(row)

    async def start_run(
        self,
        thread_id: str,
        user_id: Optional[str],
        idempotency_key: str,
        request_fingerprint: str,
        user_message_id: str,
        assistant_message_id: str,
        agent_framework: str = "deepagents",
        requested_runtime: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        async with self._lock:
            self._owned_thread(thread_id, user_id)
            stale_before = _now().timestamp() - max(
                60, int(os.getenv("CHAT_STALE_RUN_SECONDS", "600"))
            )
            for row in self._runs.values():
                started = datetime.fromisoformat(row["started_at"]).timestamp()
                if (
                    row["thread_id"] == str(thread_id)
                    and row["status"] == "running"
                    and started < stale_before
                ):
                    row.update(
                        status="abandoned",
                        error_code="stale_run",
                        error_message="Run was abandoned after a server interruption.",
                        finished_at=_now().isoformat(),
                    )
            for row in self._runs.values():
                if (
                    row["thread_id"] == str(thread_id)
                    and row["idempotency_key"] == idempotency_key
                ):
                    return dict(row), False
            if any(
                row["thread_id"] == str(thread_id) and row["status"] == "running"
                for row in self._runs.values()
            ):
                raise ThreadBusy("This thread already has an active run.")
            run_id = str(uuid4())
            row = {
                "run_id": run_id,
                "thread_id": str(thread_id),
                "user_id": user_id,
                "idempotency_key": idempotency_key,
                "request_fingerprint": request_fingerprint,
                "user_message_id": str(user_message_id),
                "assistant_message_id": str(assistant_message_id),
                "agent_framework": agent_framework,
                "agent_name": "Hermes" if agent_framework == "hermes" else "DeepAgents",
                "requested_runtime": requested_runtime,
                "status": "running",
                "error_code": None,
                "error_message": None,
                "started_at": _now().isoformat(),
                "finished_at": None,
            }
            self._runs[run_id] = row
            return dict(row), True

    async def complete_run(
        self,
        run_id: str,
        user_id: Optional[str],
        assistant_message_id: str,
        agent_framework: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> None:
        async with self._lock:
            row = self._runs.get(str(run_id))
            if not row or row.get("user_id") != user_id:
                raise ThreadNotFound("Run not found.")
            row.update(
                status="completed",
                assistant_message_id=str(assistant_message_id),
                finished_at=_now().isoformat(),
            )
            if agent_framework:
                row["agent_framework"] = agent_framework
            if agent_name:
                row["agent_name"] = agent_name

    async def fail_run(
        self,
        run_id: str,
        user_id: Optional[str],
        code: str,
        message: str,
        status: str = "failed",
    ) -> None:
        async with self._lock:
            row = self._runs.get(str(run_id))
            if not row or row.get("user_id") != user_id:
                return
            row.update(
                status=status,
                error_code=code,
                error_message=message[:1000],
                finished_at=_now().isoformat(),
            )

    async def get_run(
        self, run_id: str, user_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        row = self._runs.get(str(run_id))
        if not row or row.get("user_id") != user_id:
            return None
        return dict(row)

    async def get_message(
        self, thread_id: str, user_id: Optional[str], message_id: str
    ) -> Optional[Dict[str, Any]]:
        self._owned_thread(thread_id, user_id)
        row = next(
            (
                item
                for item in self._messages.get(str(thread_id), [])
                if item["message_id"] == str(message_id)
            ),
            None,
        )
        return dict(row) if row else None


class PostgresChatRepository:
    """PostgreSQL repository. The schema is created by setup_polycode_db.py."""

    persistent = True

    async def _pool(self):
        from db.connection import get_pool

        return await get_pool()

    @staticmethod
    def _owner_sql(user_id: Optional[str], index: int = 2) -> str:
        return f"user_id = ${index}" if user_id else "user_id IS NULL"

    async def create_thread(
        self,
        user_id: Optional[str],
        title: str = "New chat",
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        pool = await self._pool()
        tid = _uuid(thread_id) or uuid4()
        uid = _uuid(user_id)
        row = await pool.fetchrow(
            """
            INSERT INTO polycode.chat_conversations(thread_id, user_id, title)
            VALUES ($1, $2, $3)
            RETURNING thread_id, user_id, title, created_at, updated_at
            """,
            tid,
            uid,
            (title or "New chat")[:200],
        )
        return _serialise(row)

    async def ensure_thread(
        self,
        thread_id: str,
        user_id: Optional[str],
        title: Optional[str] = None,
        create_if_missing: bool = True,
    ) -> Dict[str, Any]:
        pool = await self._pool()
        tid, uid = _uuid(thread_id), _uuid(user_id)
        existing = await pool.fetchrow(
            """
            SELECT thread_id, user_id, title, created_at, updated_at
            FROM polycode.chat_conversations WHERE thread_id = $1
            """,
            tid,
        )
        if existing:
            existing_uid = existing["user_id"]
            if existing_uid != uid:
                raise ThreadAccessDenied("Thread not found.")
            stale_seconds = max(
                60, int(os.getenv("CHAT_STALE_RUN_SECONDS", "600"))
            )
            await pool.execute(
                """
                UPDATE polycode.chat_runs
                SET status='abandoned',
                    error_code='stale_run',
                    error_message='Run was abandoned after a server interruption.',
                    finished_at=NOW()
                WHERE thread_id=$1 AND user_id IS NOT DISTINCT FROM $2
                  AND status='running'
                  AND started_at < NOW() - ($3 * INTERVAL '1 second')
                """,
                tid,
                uid,
                stale_seconds,
            )
            return _serialise(existing)
        if not create_if_missing:
            raise ThreadNotFound("Thread not found.")
        import asyncpg

        try:
            return await self.create_thread(user_id, title or "New chat", thread_id)
        except asyncpg.UniqueViolationError:
            # Resolve an insert race without leaking a different owner's thread.
            existing = await pool.fetchrow(
                """
                SELECT thread_id, user_id, title, created_at, updated_at
                FROM polycode.chat_conversations WHERE thread_id = $1
                """,
                tid,
            )
            if existing and existing["user_id"] == uid:
                return _serialise(existing)
            raise ThreadAccessDenied("Thread not found.")

    async def list_threads(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> List[Dict[str, Any]]:
        pool = await self._pool()
        rows = await pool.fetch(
            """
            SELECT thread_id, user_id, title, created_at, updated_at
            FROM polycode.chat_conversations
            WHERE user_id = $1
            ORDER BY updated_at DESC
            LIMIT $2 OFFSET $3
            """,
            _uuid(user_id),
            limit,
            offset,
        )
        return [_serialise(row) for row in rows]

    async def delete_thread(self, thread_id: str, user_id: str) -> bool:
        pool = await self._pool()
        tid, uid = _uuid(thread_id), _uuid(user_id)
        await pool.execute(
            """
            UPDATE polycode.chat_runs
            SET status='abandoned',
                error_code='stale_run',
                error_message='Run was abandoned after a server interruption.',
                finished_at=NOW()
            WHERE thread_id=$1 AND user_id=$2 AND status='running'
              AND started_at < NOW() - ($3 * INTERVAL '1 second')
            """,
            tid,
            uid,
            max(60, int(os.getenv("CHAT_STALE_RUN_SECONDS", "600"))),
        )
        result = await pool.execute(
            """
            DELETE FROM polycode.chat_conversations
            WHERE thread_id = $1 AND user_id = $2
              AND NOT EXISTS (
                  SELECT 1 FROM polycode.chat_runs r
                  WHERE r.thread_id = $1 AND r.status = 'running'
              )
            """,
            tid,
            uid,
        )
        if result.endswith("0"):
            state = await pool.fetchrow(
                """
                SELECT
                    EXISTS (
                        SELECT 1 FROM polycode.chat_conversations c
                        WHERE c.thread_id=$1 AND c.user_id=$2
                    ) AS owned,
                    EXISTS (
                        SELECT 1
                        FROM polycode.chat_conversations c
                        JOIN polycode.chat_runs r ON r.thread_id = c.thread_id
                        WHERE c.thread_id=$1 AND c.user_id=$2
                          AND r.status='running'
                    ) AS active
                """,
                tid,
                uid,
            )
            if state and (state["active"] or state["owned"]):
                raise ThreadBusy("Cannot delete a thread with an active run.")
            raise ThreadNotFound("Thread not found.")
        return True

    async def load_messages(
        self,
        thread_id: str,
        user_id: Optional[str],
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        pool = await self._pool()
        tid, uid = _uuid(thread_id), _uuid(user_id)
        owner_clause = "c.user_id = $2" if uid else "c.user_id IS NULL"
        params = [tid, uid, limit] if uid else [tid, limit]
        limit_param = "$3" if uid else "$2"
        rows = await pool.fetch(
            f"""
            SELECT * FROM (
                SELECT m.id, m.thread_id, m.message_id, m.role, m.content,
                       m.metadata, m.created_at
                FROM polycode.chat_messages m
                JOIN polycode.chat_conversations c ON c.thread_id = m.thread_id
                WHERE m.thread_id = $1 AND {owner_clause}
                ORDER BY m.id DESC
                LIMIT {limit_param}
            ) recent
            ORDER BY id ASC
            """,
            *params,
        )
        if not rows:
            thread = await pool.fetchrow(
                f"""
                SELECT thread_id FROM polycode.chat_conversations c
                WHERE thread_id = $1 AND {owner_clause}
                """,
                *([tid, uid] if uid else [tid]),
            )
            if not thread:
                raise ThreadNotFound("Thread not found.")
        return [_serialise(row) for row in rows]

    async def save_message(
        self,
        thread_id: str,
        user_id: Optional[str],
        role: str,
        content: str,
        message_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        pool = await self._pool()
        tid, uid, mid = _uuid(thread_id), _uuid(user_id), _uuid(message_id)
        owner_clause = "user_id = $2" if uid else "user_id IS NULL"
        owner_params = [tid, uid] if uid else [tid]
        owner_offset = 2 if uid else 1
        row = await pool.fetchrow(
            f"""
            INSERT INTO polycode.chat_messages(
                thread_id, message_id, role, content, metadata
            )
            SELECT thread_id, ${owner_offset + 1}, ${owner_offset + 2},
                   ${owner_offset + 3}, ${owner_offset + 4}::jsonb
            FROM polycode.chat_conversations
            WHERE thread_id = $1 AND {owner_clause}
            ON CONFLICT (thread_id, message_id) DO NOTHING
            RETURNING id, thread_id, message_id, role, content, metadata, created_at
            """,
            *owner_params,
            mid,
            role,
            content,
            json.dumps(metadata) if metadata else None,
        )
        if not row:
            existing = await self.get_message(thread_id, user_id, message_id)
            if existing:
                raise InvalidChatRequest(
                    "This client message ID has already been used."
                )
            raise ThreadNotFound("Thread not found.")
        await pool.execute(
            "UPDATE polycode.chat_conversations SET updated_at=NOW() WHERE thread_id=$1",
            tid,
        )
        return _serialise(row)

    async def start_run(
        self,
        thread_id: str,
        user_id: Optional[str],
        idempotency_key: str,
        request_fingerprint: str,
        user_message_id: str,
        assistant_message_id: str,
        agent_framework: str = "deepagents",
        requested_runtime: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        pool = await self._pool()
        tid, uid = _uuid(thread_id), _uuid(user_id)
        existing = await pool.fetchrow(
            """
            SELECT * FROM polycode.chat_runs
            WHERE thread_id=$1 AND idempotency_key=$2
            """,
            tid,
            idempotency_key,
        )
        if existing:
            if existing["user_id"] != uid:
                raise ThreadAccessDenied("Thread not found.")
            return _serialise(existing), False
        import asyncpg

        try:
            row = await pool.fetchrow(
                """
                INSERT INTO polycode.chat_runs(
                    run_id, thread_id, user_id, idempotency_key, request_fingerprint,
                    user_message_id, assistant_message_id, status, agent_framework,
                    agent_name, requested_runtime
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,'running',$8,$9,$10)
                RETURNING *
                """,
                uuid4(),
                tid,
                uid,
                idempotency_key,
                request_fingerprint,
                _uuid(user_message_id),
                _uuid(assistant_message_id),
                agent_framework,
                "Hermes" if agent_framework == "hermes" else "DeepAgents",
                requested_runtime,
            )
            return _serialise(row), True
        except asyncpg.UniqueViolationError:
            existing = await pool.fetchrow(
                """
                SELECT * FROM polycode.chat_runs
                WHERE thread_id=$1 AND idempotency_key=$2
                """,
                tid,
                idempotency_key,
            )
            if existing:
                if existing["user_id"] != uid:
                    raise ThreadAccessDenied("Thread not found.")
                return _serialise(existing), False
            active = await pool.fetchrow(
                """
                SELECT run_id FROM polycode.chat_runs
                WHERE thread_id=$1 AND status='running'
                """,
                tid,
            )
            if active:
                raise ThreadBusy("This thread already has an active run.")
            raise

    async def complete_run(
        self,
        run_id: str,
        user_id: Optional[str],
        assistant_message_id: str,
        agent_framework: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> None:
        pool = await self._pool()
        uid = _uuid(user_id)
        owner_clause = "user_id=$5" if uid else "user_id IS NULL"
        params = [_uuid(run_id), _uuid(assistant_message_id), agent_framework, agent_name]
        if uid:
            params.append(uid)
        await pool.execute(
            f"""
            UPDATE polycode.chat_runs
            SET status='completed', assistant_message_id=$2, finished_at=NOW(),
                agent_framework=COALESCE($3, agent_framework),
                agent_name=COALESCE($4, agent_name)
            WHERE run_id=$1 AND {owner_clause}
            """,
            *params,
        )

    async def fail_run(
        self,
        run_id: str,
        user_id: Optional[str],
        code: str,
        message: str,
        status: str = "failed",
    ) -> None:
        pool = await self._pool()
        uid = _uuid(user_id)
        owner_clause = "user_id=$5" if uid else "user_id IS NULL"
        params = [_uuid(run_id), status, code, message[:1000]]
        if uid:
            params.append(uid)
        await pool.execute(
            f"""
            UPDATE polycode.chat_runs
            SET status=$2, error_code=$3, error_message=$4, finished_at=NOW()
            WHERE run_id=$1 AND {owner_clause}
            """,
            *params,
        )

    async def get_run(
        self, run_id: str, user_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        pool = await self._pool()
        uid = _uuid(user_id)
        owner_clause = "user_id=$2" if uid else "user_id IS NULL"
        params = [_uuid(run_id), uid] if uid else [_uuid(run_id)]
        row = await pool.fetchrow(
            f"SELECT * FROM polycode.chat_runs WHERE run_id=$1 AND {owner_clause}",
            *params,
        )
        return _serialise(row) if row else None

    async def get_message(
        self, thread_id: str, user_id: Optional[str], message_id: str
    ) -> Optional[Dict[str, Any]]:
        pool = await self._pool()
        tid, uid = _uuid(thread_id), _uuid(user_id)
        owner_clause = "c.user_id=$3" if uid else "c.user_id IS NULL"
        params = [tid, _uuid(message_id), uid] if uid else [tid, _uuid(message_id)]
        row = await pool.fetchrow(
            f"""
            SELECT m.id, m.thread_id, m.message_id, m.role, m.content,
                   m.metadata, m.created_at
            FROM polycode.chat_messages m
            JOIN polycode.chat_conversations c ON c.thread_id=m.thread_id
            WHERE m.thread_id=$1 AND m.message_id=$2 AND {owner_clause}
            """,
            *params,
        )
        return _serialise(row) if row else None


@lru_cache(maxsize=1)
def get_chat_repository() -> ChatRepository:
    """Select durable storage when configured, otherwise an explicit dev store."""
    dsn = os.getenv("POLYCODE_DB_URL") or os.getenv("DATABASE_URL")
    if dsn:
        return PostgresChatRepository()
    if os.getenv("CHAT_REQUIRE_DATABASE", "").lower() in {"1", "true", "yes"}:
        raise RuntimeError(
            "CHAT_REQUIRE_DATABASE is enabled but no database URL is configured."
        )
    return MemoryChatRepository()
