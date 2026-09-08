from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import AgentSettings

RunStatus = Literal["completed", "failed", "cancelled", "interrupted"]


@dataclass(frozen=True)
class ThreadRecord:
    thread_id: UUID
    principal_id: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    title: str = "New chat"


class ThreadLease:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        connection: AsyncConnection,
        key: int,
    ) -> None:
        self._pool = pool
        self._connection = connection
        self._key = key
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            await self._connection.execute("SELECT pg_advisory_unlock(%s)", (self._key,))
        finally:
            await self._pool.putconn(self._connection)


class AgentRepository:
    def __init__(self, pool: AsyncConnectionPool, thread_ttl_days: int) -> None:
        self.pool = pool
        self.thread_ttl = timedelta(days=thread_ttl_days)

    async def schema_ready(self) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT
                    to_regclass('polytrade_agent.agent_threads') IS NOT NULL
                    AND to_regclass('polytrade_agent.agent_runs') IS NOT NULL
                    AND to_regclass('polytrade_agent.checkpoint_migrations') IS NOT NULL
                    AND to_regclass('polytrade_agent.checkpoints') IS NOT NULL
                    AND to_regclass('polytrade_agent.checkpoint_blobs') IS NOT NULL
                    AND to_regclass('polytrade_agent.checkpoint_writes') IS NOT NULL
                    AND EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'polytrade_agent'
                          AND table_name = 'agent_threads'
                          AND column_name = 'title'
                    )
                    AS exists
                """
            )
            row = await cursor.fetchone()
            if not row or not row["exists"]:
                return False
            cursor = await connection.execute(
                """
                SELECT COALESCE(MAX(v), -1) AS checkpoint_version
                FROM polytrade_agent.checkpoint_migrations
                """
            )
            row = await cursor.fetchone()
            return bool(row and row["checkpoint_version"] >= len(AsyncPostgresSaver.MIGRATIONS) - 1)

    async def create_thread(self, principal_id: str) -> ThreadRecord:
        now = datetime.now(UTC)
        record = ThreadRecord(
            thread_id=uuid4(),
            principal_id=principal_id,
            created_at=now,
            updated_at=now,
            expires_at=now + self.thread_ttl,
            title="New chat",
        )
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO polytrade_agent.agent_threads (
                    thread_id, principal_id, created_at, updated_at, expires_at, title
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    record.thread_id,
                    record.principal_id,
                    record.created_at,
                    record.updated_at,
                    record.expires_at,
                    record.title,
                ),
            )
        return record

    async def get_owned_thread(
        self,
        thread_id: UUID,
        principal_id: str,
    ) -> ThreadRecord | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT thread_id, principal_id, created_at, updated_at, expires_at, title
                FROM polytrade_agent.agent_threads
                WHERE thread_id = %s AND principal_id = %s AND expires_at > now()
                """,
                (thread_id, principal_id),
            )
            row = await cursor.fetchone()
        return ThreadRecord(**row) if row else None

    async def list_owned_threads(
        self,
        principal_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ThreadRecord]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT thread_id, principal_id, created_at, updated_at, expires_at, title
                FROM polytrade_agent.agent_threads
                WHERE principal_id = %s AND expires_at > now()
                ORDER BY updated_at DESC, thread_id DESC
                LIMIT %s OFFSET %s
                """,
                (principal_id, limit, offset),
            )
            rows = await cursor.fetchall()
        return [ThreadRecord(**row) for row in rows]

    async def set_initial_title(
        self,
        thread_id: UUID,
        principal_id: str,
        title: str,
    ) -> None:
        now = datetime.now(UTC)
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE polytrade_agent.agent_threads
                SET title = %s, updated_at = %s, expires_at = %s
                WHERE thread_id = %s AND principal_id = %s AND title = 'New chat'
                """,
                (title[:80], now, now + self.thread_ttl, thread_id, principal_id),
            )

    async def delete_owned_thread(self, thread_id: UUID, principal_id: str) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM polytrade_agent.agent_threads
                WHERE thread_id = %s AND principal_id = %s
                RETURNING thread_id
                """,
                (thread_id, principal_id),
            )
            return await cursor.fetchone() is not None

    async def delete_thread(self, thread_id: UUID) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM polytrade_agent.agent_threads
                WHERE thread_id = %s RETURNING thread_id
                """,
                (thread_id,),
            )
            return await cursor.fetchone() is not None

    async def touch_thread(self, thread_id: UUID, principal_id: str) -> None:
        now = datetime.now(UTC)
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE polytrade_agent.agent_threads
                SET updated_at = %s, expires_at = %s
                WHERE thread_id = %s AND principal_id = %s
                """,
                (now, now + self.thread_ttl, thread_id, principal_id),
            )

    async def create_run(self, thread_id: UUID, principal_id: str) -> UUID:
        run_id = uuid4()
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO polytrade_agent.agent_runs (
                    run_id, thread_id, principal_id, status, started_at
                ) VALUES (%s, %s, %s, 'running', %s)
                """,
                (run_id, thread_id, principal_id, datetime.now(UTC)),
            )
        return run_id

    async def finish_run(
        self,
        run_id: UUID,
        status: RunStatus,
        error_code: str | None = None,
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE polytrade_agent.agent_runs
                SET status = %s, error_code = %s, completed_at = %s
                WHERE run_id = %s AND status = 'running'
                """,
                (status, error_code, datetime.now(UTC), run_id),
            )

    async def commit_completed_run(
        self,
        run_id: UUID,
        thread_id: UUID,
        principal_id: str,
    ) -> None:
        """Promote an isolated checkpoint atomically without per-table network round trips."""
        now = datetime.now(UTC)
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                WITH promotion_input AS MATERIALIZED (
                    SELECT %s::uuid AS run_id,
                           %s::uuid AS thread_id,
                           %s::text AS principal_id,
                           %s::timestamptz AS completed_at,
                           %s::timestamptz AS expires_at
                ), eligible AS MATERIALIZED (
                    SELECT promotion_input.*
                    FROM promotion_input
                    JOIN polytrade_agent.agent_runs AS run
                      ON run.run_id = promotion_input.run_id
                     AND run.thread_id = promotion_input.thread_id
                     AND run.principal_id = promotion_input.principal_id
                    JOIN polytrade_agent.agent_threads AS thread
                      ON thread.thread_id = promotion_input.thread_id
                     AND thread.principal_id = promotion_input.principal_id
                    WHERE run.status = 'running'
                      AND EXISTS (
                          SELECT 1
                          FROM polytrade_agent.checkpoints
                          WHERE thread_id = promotion_input.run_id::text
                      )
                    FOR UPDATE OF run, thread
                ), upsert_checkpoints AS (
                    INSERT INTO polytrade_agent.checkpoints (
                        thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
                        type, checkpoint, metadata
                    )
                    SELECT eligible.thread_id::text,
                           source.checkpoint_ns,
                           source.checkpoint_id,
                           source.parent_checkpoint_id,
                           source.type,
                           source.checkpoint,
                           source.metadata
                    FROM polytrade_agent.checkpoints AS source
                    JOIN eligible ON source.thread_id = eligible.run_id::text
                    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id) DO UPDATE
                    SET parent_checkpoint_id = EXCLUDED.parent_checkpoint_id,
                        type = EXCLUDED.type,
                        checkpoint = EXCLUDED.checkpoint,
                        metadata = EXCLUDED.metadata
                ), delete_stale_checkpoints AS (
                    DELETE FROM polytrade_agent.checkpoints AS target
                    USING eligible
                    WHERE target.thread_id = eligible.thread_id::text
                      AND NOT EXISTS (
                          SELECT 1
                          FROM polytrade_agent.checkpoints AS source
                          WHERE source.thread_id = eligible.run_id::text
                            AND source.checkpoint_ns = target.checkpoint_ns
                            AND source.checkpoint_id = target.checkpoint_id
                      )
                ), upsert_blobs AS (
                    INSERT INTO polytrade_agent.checkpoint_blobs (
                        thread_id, checkpoint_ns, channel, version, type, blob
                    )
                    SELECT eligible.thread_id::text,
                           source.checkpoint_ns,
                           source.channel,
                           source.version,
                           source.type,
                           source.blob
                    FROM polytrade_agent.checkpoint_blobs AS source
                    JOIN eligible ON source.thread_id = eligible.run_id::text
                    ON CONFLICT (thread_id, checkpoint_ns, channel, version) DO UPDATE
                    SET type = EXCLUDED.type,
                        blob = EXCLUDED.blob
                ), delete_stale_blobs AS (
                    DELETE FROM polytrade_agent.checkpoint_blobs AS target
                    USING eligible
                    WHERE target.thread_id = eligible.thread_id::text
                      AND NOT EXISTS (
                          SELECT 1
                          FROM polytrade_agent.checkpoint_blobs AS source
                          WHERE source.thread_id = eligible.run_id::text
                            AND source.checkpoint_ns = target.checkpoint_ns
                            AND source.channel = target.channel
                            AND source.version = target.version
                      )
                ), upsert_writes AS (
                    INSERT INTO polytrade_agent.checkpoint_writes (
                        thread_id, checkpoint_ns, checkpoint_id, task_id,
                        task_path, idx, channel, type, blob
                    )
                    SELECT eligible.thread_id::text,
                           source.checkpoint_ns,
                           source.checkpoint_id,
                           source.task_id,
                           source.task_path,
                           source.idx,
                           source.channel,
                           source.type,
                           source.blob
                    FROM polytrade_agent.checkpoint_writes AS source
                    JOIN eligible ON source.thread_id = eligible.run_id::text
                    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) DO UPDATE
                    SET task_path = EXCLUDED.task_path,
                        channel = EXCLUDED.channel,
                        type = EXCLUDED.type,
                        blob = EXCLUDED.blob
                ), delete_stale_writes AS (
                    DELETE FROM polytrade_agent.checkpoint_writes AS target
                    USING eligible
                    WHERE target.thread_id = eligible.thread_id::text
                      AND NOT EXISTS (
                          SELECT 1
                          FROM polytrade_agent.checkpoint_writes AS source
                          WHERE source.thread_id = eligible.run_id::text
                            AND source.checkpoint_ns = target.checkpoint_ns
                            AND source.checkpoint_id = target.checkpoint_id
                            AND source.task_id = target.task_id
                            AND source.idx = target.idx
                      )
                ), delete_runtime_writes AS (
                    DELETE FROM polytrade_agent.checkpoint_writes AS target
                    USING eligible
                    WHERE target.thread_id = eligible.run_id::text
                ), delete_runtime_blobs AS (
                    DELETE FROM polytrade_agent.checkpoint_blobs AS target
                    USING eligible
                    WHERE target.thread_id = eligible.run_id::text
                ), delete_runtime_checkpoints AS (
                    DELETE FROM polytrade_agent.checkpoints AS target
                    USING eligible
                    WHERE target.thread_id = eligible.run_id::text
                ), completed_run AS (
                    UPDATE polytrade_agent.agent_runs AS target
                    SET status = 'completed',
                        error_code = NULL,
                        completed_at = eligible.completed_at
                    FROM eligible
                    WHERE target.run_id = eligible.run_id
                      AND target.status = 'running'
                    RETURNING target.run_id
                ), updated_thread AS (
                    UPDATE polytrade_agent.agent_threads AS target
                    SET updated_at = eligible.completed_at,
                        expires_at = eligible.expires_at
                    FROM eligible
                    WHERE target.thread_id = eligible.thread_id
                      AND target.principal_id = eligible.principal_id
                      AND EXISTS (SELECT 1 FROM completed_run)
                    RETURNING target.thread_id
                )
                SELECT completed_run.run_id
                FROM completed_run
                WHERE EXISTS (SELECT 1 FROM updated_thread)
                """,
                (run_id, thread_id, principal_id, now, now + self.thread_ttl),
            )
            if await cursor.fetchone() is not None:
                return

            cursor = await connection.execute(
                """
                SELECT EXISTS (
                           SELECT 1
                           FROM polytrade_agent.agent_runs
                           WHERE run_id = %s
                             AND thread_id = %s
                             AND principal_id = %s
                             AND status = 'running'
                       ) AS active,
                       EXISTS (
                           SELECT 1
                           FROM polytrade_agent.checkpoints
                           WHERE thread_id = %s
                       ) AS checkpoint_exists
                """,
                (run_id, thread_id, principal_id, str(run_id)),
            )
            state = await cursor.fetchone()
            if not state or not state["active"]:
                raise RuntimeError("Agent run is no longer active")
            if not state["checkpoint_exists"]:
                raise RuntimeError("Agent run produced no checkpoint")
            raise RuntimeError("Agent run could not be completed")

    async def mark_interrupted_runs(self) -> None:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    await connection.execute(
                        f"""DELETE FROM polytrade_agent.{table} AS checkpoint_row
                            USING polytrade_agent.agent_runs AS run
                            WHERE run.status = 'running'
                              AND checkpoint_row.thread_id = run.run_id::text
                        """,  # noqa: S608
                    )
                await connection.execute(
                    """
                    UPDATE polytrade_agent.agent_runs
                    SET status = 'interrupted',
                        error_code = 'agent_restarted',
                        completed_at = now()
                    WHERE status = 'running'
                    """
                )

    async def try_acquire_thread(self, thread_id: UUID) -> ThreadLease | None:
        key = int.from_bytes(thread_id.bytes[:8], byteorder="big", signed=True)
        connection = await self.pool.getconn()
        try:
            cursor = await connection.execute(
                "SELECT pg_try_advisory_lock(%s) AS locked",
                (key,),
            )
            row = await cursor.fetchone()
            if not row or not row["locked"]:
                await self.pool.putconn(connection)
                return None
            return ThreadLease(self.pool, connection, key)
        except BaseException:
            await self.pool.putconn(connection)
            raise

    async def expired_thread_ids(self, limit: int = 100) -> list[UUID]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT thread_id
                FROM polytrade_agent.agent_threads
                WHERE expires_at <= now()
                ORDER BY expires_at
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [row["thread_id"] for row in rows]


@dataclass
class AgentStorage:
    pool: AsyncConnectionPool
    repository: AgentRepository
    checkpointer: AsyncPostgresSaver

    async def close(self) -> None:
        await self.pool.close()


def encrypted_serializer(settings: AgentSettings) -> EncryptedSerializer:
    return EncryptedSerializer.from_pycryptodome_aes(
        key=settings.LANGGRAPH_AES_KEY.get_secret_value().encode("utf-8")
    )


async def open_storage(settings: AgentSettings) -> AgentStorage:
    pool = AsyncConnectionPool(
        conninfo=settings.DATABASE_URL.get_secret_value(),
        min_size=1,
        max_size=max(4, settings.AGENT_MAX_CONCURRENT_RUNS + 2),
        kwargs={
            "autocommit": True,
            "application_name": "polytrade-agent",
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": "-c search_path=polytrade_agent,public",
        },
        open=False,
    )
    await pool.open(wait=True, timeout=15)
    return AgentStorage(
        pool=pool,
        repository=AgentRepository(pool, settings.AGENT_THREAD_TTL_DAYS),
        checkpointer=AsyncPostgresSaver(pool, serde=encrypted_serializer(settings)),
    )
