"""Owner-scoped experiments with an atomic outbox and fenced simulation checkpoints."""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .experiments import (
    CreateExperimentRequest,
    ExperimentEnvelope,
    ExperimentResult,
    ExperimentRun,
    SimulationCheckpoint,
)
from .market import HistoricalDataset, decode_dataset
from .repository import (
    ActiveRunLimitReached,
    BacktestNotFound,
    BacktestRepository,
    IdempotencyMismatch,
)


@dataclass(frozen=True)
class ExperimentClaim:
    experiment_id: UUID
    token: UUID
    request: CreateExperimentRequest
    dataset_hashes: dict[str, str]
    retries: int


class ExperimentRepository:
    def __init__(self, backtests: BacktestRepository):
        self.backtests = backtests
        self.pool = backtests.pool

    async def create(
        self, owner: str, request: CreateExperimentRequest, key: str, fingerprint: str
    ) -> tuple[ExperimentRun, bool]:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"backtest-create:{owner}",),
                )
                existing = await connection.execute(
                    "SELECT * FROM polytrade_backtest.backtest_experiments "
                    "WHERE principal_id = %s AND idempotency_key = %s",
                    (owner, key),
                )
                row = await existing.fetchone()
                if row:
                    if row["request_hash"] != fingerprint:
                        raise IdempotencyMismatch
                    return _run(row), False
                count = await connection.execute(
                    """SELECT (
                        SELECT count(*) FROM polytrade_backtest.backtest_runs
                        WHERE principal_id = %s AND status IN ('queued', 'running')
                    ) + (
                        SELECT count(*) FROM polytrade_backtest.backtest_experiments
                        WHERE principal_id = %s AND status IN ('queued', 'running')
                    ) AS count""",
                    (owner, owner),
                )
                if (await count.fetchone())["count"] >= self.backtests.max_active_runs_per_owner:
                    raise ActiveRunLimitReached(self.backtests.max_active_runs_per_owner)
                cursor = await connection.execute(
                    """INSERT INTO polytrade_backtest.backtest_experiments
                    (experiment_id, principal_id, request, idempotency_key, request_hash,
                     total_simulations, dispatch_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                    (
                        uuid4(),
                        owner,
                        Jsonb(request.model_dump(mode="json", by_alias=True)),
                        key,
                        fingerprint,
                        request.simulation_count(),
                        uuid4(),
                    ),
                )
                return _run(await cursor.fetchone()), True

    async def list(self, owner: str, limit: int) -> list[ExperimentRun]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM polytrade_backtest.backtest_experiments "
                "WHERE principal_id = %s ORDER BY created_at DESC LIMIT %s",
                (owner, limit),
            )
            return [_run(row) for row in await cursor.fetchall()]

    async def get(self, experiment_id: UUID, owner: str) -> ExperimentEnvelope:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM polytrade_backtest.backtest_experiments "
                "WHERE experiment_id = %s AND principal_id = %s",
                (experiment_id, owner),
            )
            row = await cursor.fetchone()
            if not row:
                raise BacktestNotFound
            return ExperimentEnvelope(experiment=_run(row), result=row["result"])

    async def cancel(self, experiment_id: UUID, owner: str) -> ExperimentEnvelope:
        # Cancellation itself is idempotent. Clearing the token fences an in-flight worker.
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE polytrade_backtest.backtest_experiments
                SET status = 'cancelled', message = 'Cancelled',
                    completed_at = now(), claim_token = NULL
                WHERE experiment_id = %s AND principal_id = %s
                    AND status IN ('queued', 'running')""",
                (experiment_id, owner),
            )
        return await self.get(experiment_id, owner)

    async def ready(self) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT experiment_id, dispatch_id FROM polytrade_backtest.backtest_experiments
                WHERE status = 'queued' AND published_at IS NULL AND next_dispatch_at <= now()
                ORDER BY next_dispatch_at LIMIT 20"""
            )
            return await cursor.fetchall()

    async def published(self, experiment_id: UUID, dispatch_id: UUID, success: bool) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE polytrade_backtest.backtest_experiments
                SET published_at = CASE WHEN %s THEN now() ELSE NULL END,
                    next_dispatch_at = now() + interval '5 seconds'
                WHERE experiment_id = %s AND dispatch_id = %s""",
                (success, experiment_id, dispatch_id),
            )

    async def claim(self, experiment_id: UUID) -> ExperimentClaim | None:
        token = uuid4()
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """UPDATE polytrade_backtest.backtest_experiments
                SET status = 'running', claim_token = %s, started_at = COALESCE(started_at, now()),
                    heartbeat_at = now(), message = 'Preparing experiment'
                WHERE experiment_id = %s AND status = 'queued' RETURNING *""",
                (token, experiment_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return ExperimentClaim(
                experiment_id,
                token,
                CreateExperimentRequest.model_validate(row["request"]),
                row["dataset_hashes"],
                row["retries"],
            )

    async def heartbeat(self, claim: ExperimentClaim, message: str) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """UPDATE polytrade_backtest.backtest_experiments
                SET heartbeat_at = now(), message = %s
                WHERE experiment_id = %s AND claim_token = %s AND status = 'running'
                RETURNING experiment_id""",
                (message, claim.experiment_id, claim.token),
            )
            return await cursor.fetchone() is not None

    async def pinned_dataset(self, dataset_hash: str) -> HistoricalDataset:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT payload FROM polytrade_backtest.backtest_datasets WHERE dataset_hash = %s",
                (dataset_hash,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("Pinned dataset is missing")
            return decode_dataset(bytes(row["payload"]))

    async def pin_dataset(self, claim: ExperimentClaim, market_id: str, dataset_hash: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE polytrade_backtest.backtest_experiments
                SET dataset_hashes = dataset_hashes || %s, heartbeat_at = now()
                WHERE experiment_id = %s AND claim_token = %s AND status = 'running'""",
                (Jsonb({market_id: dataset_hash}), claim.experiment_id, claim.token),
            )

    async def checkpoint(self, experiment_id: UUID, key: str) -> SimulationCheckpoint | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT payload FROM polytrade_backtest.backtest_experiment_steps "
                "WHERE experiment_id = %s AND step_key = %s",
                (experiment_id, key),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return SimulationCheckpoint.model_validate_json(gzip.decompress(bytes(row["payload"])))

    async def checkpoints(self, experiment_id: UUID) -> dict[str, SimulationCheckpoint]:
        # Resume in one database round trip, rather than re-reading every earlier
        # simulation on every worker chunk.
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT step_key, payload FROM polytrade_backtest.backtest_experiment_steps "
                "WHERE experiment_id = %s",
                (experiment_id,),
            )
            return {
                row["step_key"]: SimulationCheckpoint.model_validate_json(
                    gzip.decompress(bytes(row["payload"]))
                )
                for row in await cursor.fetchall()
            }

    async def save_checkpoint(
        self, claim: ExperimentClaim, key: str, checkpoint: SimulationCheckpoint
    ) -> bool:
        encoded = gzip.compress(checkpoint.model_dump_json(by_alias=True).encode(), mtime=0)
        async with self.pool.connection() as connection:
            async with connection.transaction():
                owned = await connection.execute(
                    "SELECT experiment_id FROM polytrade_backtest.backtest_experiments "
                    "WHERE experiment_id = %s AND claim_token = %s "
                    "AND status = 'running' FOR UPDATE",
                    (claim.experiment_id, claim.token),
                )
                if await owned.fetchone() is None:
                    return False
                inserted = await connection.execute(
                    """INSERT INTO polytrade_backtest.backtest_experiment_steps
                    (experiment_id, step_key, payload) VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING RETURNING step_key""",
                    (claim.experiment_id, key, encoded),
                )
                if await inserted.fetchone():
                    await connection.execute(
                        """UPDATE polytrade_backtest.backtest_experiments
                        SET completed_simulations = completed_simulations + 1, retries = 0,
                            heartbeat_at = now(), message = 'Evaluating strategies'
                        WHERE experiment_id = %s""",
                        (claim.experiment_id,),
                    )
                return True

    async def finish(self, claim: ExperimentClaim, result: ExperimentResult) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE polytrade_backtest.backtest_experiments
                SET status = 'completed', result = %s, message = 'Completed', completed_at = now(),
                    claim_token = NULL
                WHERE experiment_id = %s AND claim_token = %s AND status = 'running'""",
                (
                    Jsonb(result.model_dump(mode="json", by_alias=True)),
                    claim.experiment_id,
                    claim.token,
                ),
            )

    async def fail(self, claim: ExperimentClaim, code: str, message: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE polytrade_backtest.backtest_experiments
                SET status = 'failed', failure = %s, message = %s,
                    completed_at = now(), claim_token = NULL
                WHERE experiment_id = %s AND claim_token = %s AND status = 'running'""",
                (
                    Jsonb({"code": code, "message": message}),
                    message,
                    claim.experiment_id,
                    claim.token,
                ),
            )

    async def requeue(self, claim: ExperimentClaim, *, retry: bool = False) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE polytrade_backtest.backtest_experiments
                SET status = 'queued', claim_token = NULL, dispatch_id = %s, published_at = NULL,
                    next_dispatch_at = now() + (%s * interval '1 second'),
                    retries = retries + %s, heartbeat_at = now(), message = %s
                WHERE experiment_id = %s AND claim_token = %s AND status = 'running'""",
                (
                    uuid4(),
                    5 if retry else 0,
                    int(retry),
                    "Retrying temporary data failure" if retry else "Continuing experiment",
                    claim.experiment_id,
                    claim.token,
                ),
            )

    async def recover_stale(self, seconds: int, max_retries: int) -> None:
        async with self.pool.connection() as connection:
            # A stale lease is fenced before a replacement worker can write anything.
            await connection.execute(
                """UPDATE polytrade_backtest.backtest_experiments
                SET status = CASE WHEN retries >= %s THEN 'failed' ELSE 'queued' END,
                    completed_at = CASE WHEN retries >= %s THEN now() ELSE NULL END,
                    failure = CASE WHEN retries >= %s THEN
                        '{"code":"WORKER_LOST","message":"Worker recovery limit reached"}'::jsonb
                        ELSE NULL END,
                    claim_token = NULL, published_at = NULL, dispatch_id = %s,
                    next_dispatch_at = now(), heartbeat_at = now(), retries = retries + 1,
                    message = 'Recovering interrupted experiment'
                WHERE (status = 'running' AND heartbeat_at < now() - (%s * interval '1 second'))
                   OR (status = 'queued' AND published_at < now() - (%s * interval '1 second'))""",
                (max_retries, max_retries, max_retries, uuid4(), seconds, seconds),
            )


def _run(row: dict[str, Any]) -> ExperimentRun:
    return ExperimentRun.model_validate({key: row[key] for key in ExperimentRun.model_fields})
