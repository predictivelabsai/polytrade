from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .config import BacktestSettings
from .market import HistoricalDataset, decode_dataset
from .schemas import (
    BacktestConfig,
    BacktestFailure,
    BacktestMetrics,
    BacktestResult,
    BacktestRun,
    BacktestRunEnvelope,
    BacktestSeriesPoint,
    BacktestSeriesResponse,
    BacktestTrade,
    BacktestTradesResponse,
    parse_backtest_config,
)

ASSUMPTIONS = [
    "Hypothetical results use one-minute price observations, not historical order-book depth.",
    "Entries and early exits are taker fills with configured adverse slippage and recorded fees.",
    "Partial fills, queue position, unavailable spreads, and maker rebates are not modeled.",
]


class BacktestNotFound(LookupError):
    pass


class IdempotencyMismatch(ValueError):
    pass


class ActiveRunLimitReached(RuntimeError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"At most {limit} backtests can be active for one owner")
        self.limit = limit


@dataclass(frozen=True)
class RunClaim:
    run_id: UUID
    principal_id: str
    market_id: str
    config: BacktestConfig


@dataclass(frozen=True)
class CreateRunResult:
    run: BacktestRun
    created: bool


class BacktestRepository:
    def __init__(self, pool: AsyncConnectionPool, max_active_runs_per_owner: int) -> None:
        self.pool = pool
        self.max_active_runs_per_owner = max_active_runs_per_owner

    @classmethod
    async def open(cls, settings: BacktestSettings) -> BacktestRepository:
        pool = AsyncConnectionPool(
            conninfo=settings.DATABASE_URL.get_secret_value(),
            min_size=1,
            max_size=max(4, settings.BACKTEST_WORKER_CONCURRENCY + 2),
            kwargs={
                "autocommit": True,
                "application_name": "polytrade-backtest",
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "options": "-c search_path=polytrade_backtest,public",
            },
            open=False,
        )
        await pool.open(wait=True, timeout=15)
        return cls(pool, settings.BACKTEST_MAX_ACTIVE_RUNS_PER_OWNER)

    async def close(self) -> None:
        await self.pool.close()

    async def health(self) -> None:
        async with self.pool.connection() as connection:
            await connection.execute("SELECT 1")

    async def schema_ready(self) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT
                    to_regclass('polytrade_backtest.backtest_runs') IS NOT NULL
                    AND to_regclass('polytrade_backtest.backtest_dispatch_outbox') IS NOT NULL
                    AND to_regclass('polytrade_backtest.backtest_datasets') IS NOT NULL
                    AND to_regclass('polytrade_backtest.backtest_trades') IS NOT NULL
                    AND to_regclass('polytrade_backtest.backtest_metrics') IS NOT NULL
                    AND to_regclass('polytrade_backtest.backtest_series') IS NOT NULL
                    AS exists
                """
            )
            row = await cursor.fetchone()
            return bool(row and row["exists"])

    async def create_run(
        self,
        principal_id: str,
        market_id: str,
        config: BacktestConfig,
        idempotency_key: str,
        request_hash: str,
    ) -> CreateRunResult:
        run_id = uuid4()
        try:
            async with self.pool.connection() as connection:
                async with connection.transaction():
                    await connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (f"backtest-create:{principal_id}",),
                    )
                    existing = await self._existing_create(
                        connection, principal_id, idempotency_key
                    )
                    if existing is not None:
                        if existing["request_hash"] != request_hash:
                            raise IdempotencyMismatch
                        return CreateRunResult(run=_run_from_row(existing), created=False)
                    cursor = await connection.execute(
                        """
                        SELECT count(*) AS count FROM polytrade_backtest.backtest_runs
                        WHERE principal_id = %s AND status IN ('queued', 'running')
                        """,
                        (principal_id,),
                    )
                    active = await cursor.fetchone()
                    if active is None:
                        raise RuntimeError("Unable to count active backtests")
                    if active["count"] >= self.max_active_runs_per_owner:
                        raise ActiveRunLimitReached(self.max_active_runs_per_owner)
                    cursor = await connection.execute(
                        """
                        INSERT INTO polytrade_backtest.backtest_runs (
                            run_id, principal_id, market_id, status, phase, progress,
                            config, idempotency_key, request_hash
                        ) VALUES (%s, %s, %s, 'queued', 'queued', 0, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            run_id,
                            principal_id,
                            market_id,
                            Jsonb(config.model_dump(mode="json", by_alias=True)),
                            idempotency_key,
                            request_hash,
                        ),
                    )
                    row = await cursor.fetchone()
                    await connection.execute(
                        """
                        INSERT INTO polytrade_backtest.backtest_dispatch_outbox (run_id)
                        VALUES (%s)
                        """,
                        (run_id,),
                    )
                    await connection.execute(
                        """
                        INSERT INTO polytrade_backtest.backtest_progress_events
                            (run_id, phase, progress, message)
                        VALUES (%s, 'queued', 0, 'Waiting for a worker')
                        """,
                        (run_id,),
                    )
                    if row is None:
                        raise RuntimeError("Backtest run was not created")
                    return CreateRunResult(run=_run_from_row(row), created=True)
        except UniqueViolation:
            return await self._resolve_create_race(principal_id, idempotency_key, request_hash)

    async def _resolve_create_race(
        self, principal_id: str, idempotency_key: str, request_hash: str
    ) -> CreateRunResult:
        async with self.pool.connection() as connection:
            existing = await self._existing_create(connection, principal_id, idempotency_key)
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyMismatch
                return CreateRunResult(run=_run_from_row(existing), created=False)
        raise RuntimeError("Unable to reconcile concurrent backtest creation")

    async def _existing_create(
        self, connection: Any, principal_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        cursor = await connection.execute(
            """
            SELECT * FROM polytrade_backtest.backtest_runs
            WHERE principal_id = %s AND idempotency_key = %s
            """,
            (principal_id, idempotency_key),
        )
        return await cursor.fetchone()

    async def list_runs(self, principal_id: str, limit: int = 50) -> list[BacktestRun]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM polytrade_backtest.backtest_runs
                WHERE principal_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (principal_id, limit),
            )
            return [_run_from_row(row) for row in await cursor.fetchall()]

    async def active_run_count(self, principal_id: str) -> int:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT count(*) AS count
                FROM polytrade_backtest.backtest_runs
                WHERE principal_id = %s AND status IN ('queued', 'running')
                """,
                (principal_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("Unable to count active backtests")
            return row["count"]

    async def get_run(self, run_id: UUID, principal_id: str) -> BacktestRun:
        row = await self._get_owned_row(run_id, principal_id)
        return _run_from_row(row)

    async def get_envelope(self, run_id: UUID, principal_id: str) -> BacktestRunEnvelope:
        row = await self._get_owned_row(run_id, principal_id)
        result = None
        if row.get("result_summary"):
            result = BacktestResult.model_validate(row["result_summary"])
        return BacktestRunEnvelope(run=_run_from_row(row), result=result)

    async def _get_owned_row(self, run_id: UUID, principal_id: str) -> dict[str, Any]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM polytrade_backtest.backtest_runs
                WHERE run_id = %s AND principal_id = %s
                """,
                (run_id, principal_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise BacktestNotFound
        return row

    async def claim_run(self, run_id: UUID) -> RunClaim | None:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE polytrade_backtest.backtest_runs
                    SET status = 'running', phase = 'fetching', progress = 5,
                        started_at = COALESCE(started_at, now()), heartbeat_at = now()
                    WHERE run_id = %s AND status = 'queued' AND cancel_requested = false
                    RETURNING *
                    """,
                    (run_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    await self._cancel_queued(connection, run_id)
                    return None
                await connection.execute(
                    """
                    INSERT INTO polytrade_backtest.backtest_progress_events
                        (run_id, phase, progress, message)
                    VALUES (%s, 'fetching', 5, 'Fetching resolved market history')
                    """,
                    (run_id,),
                )
                return RunClaim(
                    run_id=run_id,
                    principal_id=row["principal_id"],
                    market_id=row["market_id"],
                    config=parse_backtest_config(row["config"]),
                )

    async def _cancel_queued(self, connection: Any, run_id: UUID) -> None:
        cursor = await connection.execute(
            """
            UPDATE polytrade_backtest.backtest_runs
            SET status = 'cancelled', phase = 'cancelled', progress = 100,
                completed_at = now(), heartbeat_at = now()
            WHERE run_id = %s AND status = 'queued' AND cancel_requested = true
            RETURNING run_id
            """,
            (run_id,),
        )
        if await cursor.fetchone() is not None:
            await connection.execute(
                "DELETE FROM polytrade_backtest.backtest_dispatch_outbox WHERE run_id = %s",
                (run_id,),
            )

    async def set_market_metadata(self, run_id: UUID, dataset: HistoricalDataset) -> None:
        snapshot = dataset.snapshot
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE polytrade_backtest.backtest_runs
                SET market_question = %s, yes_token_id = %s, no_token_id = %s,
                    resolved_outcome = %s, dataset_hash = %s, heartbeat_at = now()
                WHERE run_id = %s AND status = 'running'
                """,
                (
                    snapshot.question,
                    snapshot.yes_token_id,
                    snapshot.no_token_id,
                    snapshot.resolved_outcome,
                    dataset.dataset_hash,
                    run_id,
                ),
            )

    async def progress(self, run_id: UUID, phase: str, progress: int, message: str) -> None:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE polytrade_backtest.backtest_runs
                    SET phase = %s, progress = %s, heartbeat_at = now()
                    WHERE run_id = %s AND status = 'running'
                    """,
                    (phase, progress, run_id),
                )
                await connection.execute(
                    """
                    INSERT INTO polytrade_backtest.backtest_progress_events
                        (run_id, phase, progress, message)
                    SELECT %s, %s, %s, %s
                    WHERE EXISTS (
                        SELECT 1 FROM polytrade_backtest.backtest_runs
                        WHERE run_id = %s AND status = 'running'
                    )
                    """,
                    (run_id, phase, progress, message, run_id),
                )

    async def cancellation_requested(self, run_id: UUID) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT cancel_requested FROM polytrade_backtest.backtest_runs
                WHERE run_id = %s
                """,
                (run_id,),
            )
            row = await cursor.fetchone()
            return bool(row and row["cancel_requested"])

    async def cancel_run(
        self,
        run_id: UUID,
        principal_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> BacktestRun:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT * FROM polytrade_backtest.backtest_runs
                    WHERE run_id = %s AND principal_id = %s
                    FOR UPDATE
                    """,
                    (run_id, principal_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise BacktestNotFound
                existing = await connection.execute(
                    """
                    SELECT request_hash FROM polytrade_backtest.backtest_idempotency
                    WHERE principal_id = %s AND operation = 'cancel' AND idempotency_key = %s
                    """,
                    (principal_id, idempotency_key),
                )
                existing_row = await existing.fetchone()
                if existing_row is not None and existing_row["request_hash"] != request_hash:
                    raise IdempotencyMismatch
                if existing_row is None:
                    inserted = await connection.execute(
                        """
                        INSERT INTO polytrade_backtest.backtest_idempotency
                            (principal_id, operation, idempotency_key, request_hash)
                        VALUES (%s, 'cancel', %s, %s)
                        ON CONFLICT (principal_id, operation, idempotency_key) DO NOTHING
                        RETURNING request_hash
                        """,
                        (principal_id, idempotency_key, request_hash),
                    )
                    if await inserted.fetchone() is None:
                        collision = await connection.execute(
                            """
                            SELECT request_hash
                            FROM polytrade_backtest.backtest_idempotency
                            WHERE principal_id = %s AND operation = 'cancel'
                              AND idempotency_key = %s
                            """,
                            (principal_id, idempotency_key),
                        )
                        collision_row = await collision.fetchone()
                        if collision_row is None or collision_row["request_hash"] != request_hash:
                            raise IdempotencyMismatch
                if row["status"] == "queued":
                    cursor = await connection.execute(
                        """
                        UPDATE polytrade_backtest.backtest_runs
                        SET cancel_requested = true, status = 'cancelled', phase = 'cancelled',
                            progress = 100, completed_at = now(), heartbeat_at = now()
                        WHERE run_id = %s
                        RETURNING *
                        """,
                        (run_id,),
                    )
                    await connection.execute(
                        "DELETE FROM polytrade_backtest.backtest_dispatch_outbox WHERE run_id = %s",
                        (run_id,),
                    )
                elif row["status"] == "running":
                    cursor = await connection.execute(
                        """
                        UPDATE polytrade_backtest.backtest_runs
                        SET cancel_requested = true, heartbeat_at = now()
                        WHERE run_id = %s
                        RETURNING *
                        """,
                        (run_id,),
                    )
                else:
                    cursor = await connection.execute(
                        "SELECT * FROM polytrade_backtest.backtest_runs WHERE run_id = %s",
                        (run_id,),
                    )
                updated = await cursor.fetchone()
                if updated is None:
                    raise BacktestNotFound
                await connection.execute(
                    """
                    UPDATE polytrade_backtest.backtest_idempotency
                    SET response = %s
                    WHERE principal_id = %s AND operation = 'cancel' AND idempotency_key = %s
                    """,
                    (Jsonb({"runId": str(run_id)}), principal_id, idempotency_key),
                )
                return _run_from_row(updated)

    async def finish_cancelled(self, run_id: UUID) -> None:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE polytrade_backtest.backtest_runs
                    SET status = 'cancelled', phase = 'cancelled', progress = 100,
                        completed_at = now(), heartbeat_at = now()
                    WHERE run_id = %s AND status = 'running'
                    RETURNING run_id
                    """,
                    (run_id,),
                )
                if await cursor.fetchone() is not None:
                    await connection.execute(
                        """
                        INSERT INTO polytrade_backtest.backtest_progress_events
                            (run_id, phase, progress, message)
                        VALUES (%s, 'cancelled', 100, 'Backtest cancelled')
                        """,
                        (run_id,),
                    )

    async def fail_run(self, run_id: UUID, code: str, message: str) -> None:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE polytrade_backtest.backtest_runs
                    SET status = 'failed', phase = 'failed', progress = 100,
                        failure_code = %s, failure_message = %s,
                        completed_at = now(), heartbeat_at = now()
                    WHERE run_id = %s AND status IN ('queued', 'running')
                    RETURNING run_id
                    """,
                    (code, message, run_id),
                )
                if await cursor.fetchone() is not None:
                    await connection.execute(
                        """
                        INSERT INTO polytrade_backtest.backtest_progress_events
                            (run_id, phase, progress, message)
                        VALUES (%s, 'failed', 100, %s)
                        """,
                        (run_id, message),
                    )

    async def requeue_for_retry(self, run_id: UUID, message: str) -> None:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE polytrade_backtest.backtest_runs
                    SET status = 'queued', phase = 'queued', progress = 0,
                        started_at = NULL, heartbeat_at = now()
                    WHERE run_id = %s AND status = 'running' AND cancel_requested = false
                    RETURNING run_id
                    """,
                    (run_id,),
                )
                if await cursor.fetchone() is not None:
                    await connection.execute(
                        """
                        INSERT INTO polytrade_backtest.backtest_progress_events
                            (run_id, phase, progress, message)
                        VALUES (%s, 'queued', 0, %s)
                        """,
                        (run_id, message),
                    )

    async def save_dataset(self, dataset: HistoricalDataset) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO polytrade_backtest.backtest_datasets (
                    dataset_hash, condition_id, metadata, payload, point_count, start_at, end_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (dataset_hash) DO NOTHING
                """,
                (
                    dataset.dataset_hash,
                    dataset.snapshot.condition_id,
                    Jsonb(dataset.metadata),
                    dataset.payload,
                    dataset.point_count,
                    dataset.start_at,
                    dataset.end_at,
                ),
            )

    async def find_dataset(
        self,
        condition_id: str,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> HistoricalDataset | None:
        start_value = start_at.isoformat() if start_at else None
        end_value = end_at.isoformat() if end_at else None
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT payload FROM polytrade_backtest.backtest_datasets
                WHERE condition_id = %s
                  AND metadata->>'requestStartAt' IS NOT DISTINCT FROM %s
                  AND metadata->>'requestEndAt' IS NOT DISTINCT FROM %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (condition_id, start_value, end_value),
            )
            row = await cursor.fetchone()
        return decode_dataset(bytes(row["payload"])) if row else None

    async def save_result(
        self,
        run_id: UUID,
        metrics: BacktestMetrics,
        trades: list[BacktestTrade],
        series: list[BacktestSeriesPoint],
    ) -> None:
        result = BacktestResult(metrics=metrics, assumptions=ASSUMPTIONS)
        encoded_series = json.dumps(
            [point.model_dump(mode="json", by_alias=True) for point in series],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        compressed = gzip.compress(encoded_series, mtime=0)
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "DELETE FROM polytrade_backtest.backtest_trades WHERE run_id = %s",
                    (run_id,),
                )
                if trades:
                    async with connection.cursor() as cursor:
                        await cursor.executemany(
                            """
                            INSERT INTO polytrade_backtest.backtest_trades (
                                run_id, trade_index, outcome, entry_at, exit_at,
                                entry_price, exit_price, shares, entry_fee, exit_fee,
                                pnl, exit_reason
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            [
                                (
                                    run_id,
                                    trade.trade_index,
                                    trade.outcome,
                                    trade.entry_at,
                                    trade.exit_at,
                                    trade.entry_price,
                                    trade.exit_price,
                                    trade.shares,
                                    trade.entry_fee,
                                    trade.exit_fee,
                                    trade.pnl,
                                    trade.exit_reason,
                                )
                                for trade in trades
                            ],
                        )
                await connection.execute(
                    """
                    INSERT INTO polytrade_backtest.backtest_metrics (run_id, metrics)
                    VALUES (%s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET metrics = EXCLUDED.metrics
                    """,
                    (run_id, Jsonb(metrics.model_dump(mode="json", by_alias=True))),
                )
                await connection.execute(
                    """
                    INSERT INTO polytrade_backtest.backtest_series
                        (run_id, payload, point_count)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE
                    SET payload = EXCLUDED.payload, point_count = EXCLUDED.point_count
                    """,
                    (run_id, compressed, len(series)),
                )
                cursor = await connection.execute(
                    """
                    UPDATE polytrade_backtest.backtest_runs
                    SET status = 'completed', phase = 'completed', progress = 100,
                        result_summary = %s, failure_code = NULL, failure_message = NULL,
                        completed_at = now(), heartbeat_at = now()
                    WHERE run_id = %s AND status = 'running' AND cancel_requested = false
                    RETURNING run_id
                    """,
                    (Jsonb(result.model_dump(mode="json", by_alias=True)), run_id),
                )
                if await cursor.fetchone() is None:
                    raise RuntimeError("Backtest run was no longer active while saving")
                await connection.execute(
                    """
                    INSERT INTO polytrade_backtest.backtest_progress_events
                        (run_id, phase, progress, message)
                    VALUES (%s, 'completed', 100, 'Backtest completed')
                    """,
                    (run_id,),
                )

    async def get_trades(
        self,
        run_id: UUID,
        principal_id: str,
        offset: int,
        limit: int,
    ) -> BacktestTradesResponse:
        await self._get_owned_row(run_id, principal_id)
        async with self.pool.connection() as connection:
            total_cursor = await connection.execute(
                """
                SELECT count(*) AS count
                FROM polytrade_backtest.backtest_trades
                WHERE run_id = %s
                """,
                (run_id,),
            )
            total_row = await total_cursor.fetchone()
            cursor = await connection.execute(
                """
                SELECT * FROM polytrade_backtest.backtest_trades
                WHERE run_id = %s
                ORDER BY trade_index
                OFFSET %s LIMIT %s
                """,
                (run_id, offset, limit),
            )
            items = [_trade_from_row(row) for row in await cursor.fetchall()]
        return BacktestTradesResponse(
            run_id=run_id,
            items=items,
            total=int(total_row["count"] if total_row else 0),
            offset=offset,
            limit=limit,
        )

    async def get_series(
        self,
        run_id: UUID,
        principal_id: str,
        maximum_points: int = 2_000,
    ) -> BacktestSeriesResponse:
        await self._get_owned_row(run_id, principal_id)
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT payload FROM polytrade_backtest.backtest_series WHERE run_id = %s",
                (run_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return BacktestSeriesResponse(run_id=run_id, points=[])
        raw = json.loads(gzip.decompress(bytes(row["payload"])))
        points = [BacktestSeriesPoint.model_validate(item) for item in raw]
        return BacktestSeriesResponse(
            run_id=run_id,
            points=_downsample(points, maximum_points),
        )

    async def delete_run(self, run_id: UUID, principal_id: str) -> None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM polytrade_backtest.backtest_runs
                WHERE run_id = %s AND principal_id = %s
                  AND status IN ('completed', 'failed', 'cancelled')
                RETURNING run_id
                """,
                (run_id, principal_id),
            )
            if await cursor.fetchone() is None:
                check = await connection.execute(
                    """
                    SELECT 1 FROM polytrade_backtest.backtest_runs
                    WHERE run_id = %s AND principal_id = %s
                    """,
                    (run_id, principal_id),
                )
                if await check.fetchone() is None:
                    raise BacktestNotFound
                raise RuntimeError("Only terminal backtests can be deleted")

    async def ready_outbox(self, limit: int = 20) -> list[UUID]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT outbox.run_id
                FROM polytrade_backtest.backtest_dispatch_outbox AS outbox
                JOIN polytrade_backtest.backtest_runs AS run USING (run_id)
                WHERE outbox.published_at IS NULL
                  AND outbox.next_attempt_at <= now()
                  AND run.status = 'queued'
                  AND run.cancel_requested = false
                ORDER BY outbox.created_at
                LIMIT %s
                """,
                (limit,),
            )
            return [row["run_id"] for row in await cursor.fetchall()]

    async def mark_published(self, run_id: UUID) -> None:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE polytrade_backtest.backtest_dispatch_outbox
                    SET published_at = now(), attempts = attempts + 1, last_error = NULL
                    WHERE run_id = %s
                    """,
                    (run_id,),
                )
                await connection.execute(
                    """
                    UPDATE polytrade_backtest.backtest_runs
                    SET heartbeat_at = now() WHERE run_id = %s AND status = 'queued'
                    """,
                    (run_id,),
                )

    async def mark_publish_failed(self, run_id: UUID, error_type: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE polytrade_backtest.backtest_dispatch_outbox
                SET attempts = attempts + 1,
                    last_error = %s,
                    next_attempt_at = now() + make_interval(
                        secs => LEAST(60, (2 ^ LEAST(attempts, 5))::int)
                    )
                WHERE run_id = %s
                """,
                (error_type, run_id),
            )

    async def recover_stale(self, stale_seconds: int) -> int:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cancelled = await connection.execute(
                    """
                    UPDATE polytrade_backtest.backtest_runs
                    SET status = 'cancelled', phase = 'cancelled', progress = 100,
                        completed_at = now(), heartbeat_at = now()
                    WHERE status = 'running' AND cancel_requested = true
                      AND heartbeat_at < now() - make_interval(secs => %s)
                    """,
                    (stale_seconds,),
                )
                reset = await connection.execute(
                    """
                    UPDATE polytrade_backtest.backtest_runs
                    SET status = 'queued', phase = 'queued', progress = 0,
                        started_at = NULL, heartbeat_at = now()
                    WHERE status = 'running' AND cancel_requested = false
                      AND heartbeat_at < now() - make_interval(secs => %s)
                    RETURNING run_id
                    """,
                    (stale_seconds,),
                )
                reset_rows = await reset.fetchall()
                queued = await connection.execute(
                    """
                    SELECT run_id FROM polytrade_backtest.backtest_runs
                    WHERE status = 'queued' AND cancel_requested = false
                      AND heartbeat_at < now() - make_interval(secs => %s)
                    """,
                    (stale_seconds,),
                )
                queued_rows = await queued.fetchall()
                recovered_ids = {row["run_id"] for row in [*reset_rows, *queued_rows]}
                if recovered_ids:
                    async with connection.cursor() as cursor:
                        await cursor.executemany(
                            """
                            INSERT INTO polytrade_backtest.backtest_dispatch_outbox (run_id)
                            VALUES (%s)
                            ON CONFLICT (run_id) DO UPDATE
                            SET published_at = NULL, next_attempt_at = now(), last_error = NULL
                            """,
                            [(run_id,) for run_id in recovered_ids],
                        )
                return (cancelled.rowcount or 0) + len(recovered_ids)


def request_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _run_from_row(row: dict[str, Any]) -> BacktestRun:
    failure = None
    if row.get("failure_code"):
        failure = BacktestFailure(
            code=str(row["failure_code"]),
            message=str(row.get("failure_message") or "Backtest failed"),
        )
    return BacktestRun(
        run_id=row["run_id"],
        market_id=row["market_id"],
        market_question=row.get("market_question"),
        status=row["status"],
        phase=row["phase"],
        progress=row["progress"],
        config=parse_backtest_config(row["config"]),
        resolved_outcome=row.get("resolved_outcome"),
        dataset_hash=row.get("dataset_hash"),
        cancel_requested=row.get("cancel_requested", False),
        failure=failure,
        warnings=ASSUMPTIONS,
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
    )


def _trade_from_row(row: dict[str, Any]) -> BacktestTrade:
    return BacktestTrade(
        trade_index=row["trade_index"],
        outcome=row["outcome"],
        entry_at=row["entry_at"],
        exit_at=row["exit_at"],
        entry_price=str(row["entry_price"]),
        exit_price=str(row["exit_price"]),
        shares=str(row["shares"]),
        entry_fee=str(row["entry_fee"]),
        exit_fee=str(row["exit_fee"]),
        pnl=str(row["pnl"]),
        exit_reason=row["exit_reason"],
    )


def _downsample(
    points: list[BacktestSeriesPoint], maximum_points: int
) -> list[BacktestSeriesPoint]:
    if len(points) <= maximum_points:
        return points
    if maximum_points <= 2:
        return [points[0], points[-1]]
    span = len(points) - 1
    indexes = {round(index * span / (maximum_points - 1)) for index in range(maximum_points)}
    return [points[index] for index in sorted(indexes)]
