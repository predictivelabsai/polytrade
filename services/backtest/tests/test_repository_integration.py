import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr

from polytrade_backtest.config import get_settings
from polytrade_backtest.engine import PriceObservation
from polytrade_backtest.market import MarketSnapshot, build_dataset
from polytrade_backtest.repository import (
    ActiveRunLimitReached,
    BacktestNotFound,
    BacktestRepository,
    IdempotencyMismatch,
    request_fingerprint,
)
from polytrade_backtest.schemas import (
    BreakoutBacktestConfig,
    MeanReversionBacktestConfig,
    MomentumBacktestConfig,
)


@pytest.mark.asyncio
async def test_repository_schema_ownership_idempotency_and_dataset_deduplication() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    settings = get_settings().model_copy(
        update={
            "DATABASE_URL": SecretStr(database_url),
            "BACKTEST_MAX_ACTIVE_RUNS_PER_OWNER": 3,
        }
    )
    repository = await BacktestRepository.open(settings)
    suffix = uuid4().hex
    owner = f"assethero:backtest-{suffix}"
    other_owner = f"clerk:foreign-{suffix}"
    market_id = f"condition-{suffix}"
    config = MomentumBacktestConfig()
    create_hash = request_fingerprint({"marketId": market_id, "config": {}})
    dataset = _dataset(market_id)
    run_id = None
    try:
        assert await repository.schema_ready()
        async with repository.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT current_database() AS database_name,
                       current_schema() AS schema_name,
                       to_regclass('backtest_runs')::text AS own_table,
                       to_regclass('agent_threads')::text AS foreign_unqualified
                """
            )
            namespace = await cursor.fetchone()
        assert namespace["database_name"]
        assert namespace["schema_name"] == "polytrade_backtest"
        assert namespace["own_table"] == "backtest_runs"
        assert namespace["foreign_unqualified"] is None

        created = await repository.create_run(
            owner, market_id, config, "create-key-0001", create_hash
        )
        run_id = created.run.run_id
        assert created.created is True
        repeated = await repository.create_run(
            owner, market_id, config, "create-key-0001", create_hash
        )
        assert repeated.created is False
        assert repeated.run.run_id == run_id

        with pytest.raises(IdempotencyMismatch):
            await repository.create_run(
                owner, market_id, config, "create-key-0001", "0" * 64
            )
        mean_reversion, breakout = await asyncio.gather(
            repository.create_run(
                owner,
                market_id,
                MeanReversionBacktestConfig(),
                "create-key-0002",
                request_fingerprint({"marketId": market_id, "strategy": "mean_reversion_v1"}),
            ),
            repository.create_run(
                owner,
                market_id,
                BreakoutBacktestConfig(),
                "create-key-0003",
                request_fingerprint({"marketId": market_id, "strategy": "breakout_v1"}),
            ),
        )
        assert {mean_reversion.run.config.strategy, breakout.run.config.strategy} == {
            "mean_reversion_v1",
            "breakout_v1",
        }
        assert await repository.active_run_count(owner) == 3
        with pytest.raises(ActiveRunLimitReached, match="At most 3"):
            await repository.create_run(
                owner, market_id, config, "create-key-0004", create_hash
            )
        with pytest.raises(BacktestNotFound):
            await repository.get_run(run_id, other_owner)

        await repository.mark_published(run_id)
        claim = await repository.claim_run(run_id)
        assert claim is not None
        assert claim.run_id == run_id
        assert await repository.claim_run(run_id) is None
        async with repository.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE polytrade_backtest.backtest_runs
                SET heartbeat_at = now() - interval '10 minutes'
                WHERE run_id = %s
                """,
                (run_id,),
            )
        assert await repository.recover_stale(120) == 1
        recovered = await repository.get_run(run_id, owner)
        assert recovered.status == "queued"
        assert run_id in await repository.ready_outbox()

        cancel_hash = request_fingerprint({"runId": str(run_id)})
        cancelled = await repository.cancel_run(
            run_id, owner, "cancel-key-0001", cancel_hash
        )
        assert cancelled.status == "cancelled"
        replayed_cancel = await repository.cancel_run(
            run_id, owner, "cancel-key-0001", cancel_hash
        )
        assert replayed_cancel.status == "cancelled"

        await repository.save_dataset(dataset)
        await repository.save_dataset(dataset)
        restored = await repository.find_dataset(market_id, None, None)
        assert restored is not None
        assert restored.dataset_hash == dataset.dataset_hash
        async with repository.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT count(*) AS count
                FROM polytrade_backtest.backtest_datasets
                WHERE dataset_hash = %s
                """,
                (dataset.dataset_hash,),
            )
            assert (await cursor.fetchone())["count"] == 1

        await repository.delete_run(run_id, owner)
        run_id = None
        with pytest.raises(BacktestNotFound):
            await repository.get_run(cancelled.run_id, owner)
    finally:
        async with repository.pool.connection() as connection:
            await connection.execute(
                "DELETE FROM polytrade_backtest.backtest_runs WHERE principal_id = %s",
                (owner,),
            )
            await connection.execute(
                "DELETE FROM polytrade_backtest.backtest_idempotency WHERE principal_id = %s",
                (owner,),
            )
            await connection.execute(
                "DELETE FROM polytrade_backtest.backtest_datasets WHERE dataset_hash = %s",
                (dataset.dataset_hash,),
            )
        await repository.close()


def _dataset(condition_id: str):
    started_at = datetime(2026, 5, 1, tzinfo=UTC)
    closed_at = started_at + timedelta(hours=2)
    snapshot = MarketSnapshot(
        condition_id=condition_id,
        question="Will the repository integration test pass?",
        yes_token_id=f"yes-{condition_id}",
        no_token_id=f"no-{condition_id}",
        resolved_outcome="YES",
        fee_rate=Decimal("0.02"),
        start_at=started_at,
        closed_at=closed_at,
    )
    histories = {
        "YES": [
            PriceObservation(started_at, Decimal("0.40")),
            PriceObservation(closed_at, Decimal("1")),
        ],
        "NO": [
            PriceObservation(started_at, Decimal("0.60")),
            PriceObservation(closed_at, Decimal("0")),
        ],
    }
    return build_dataset(snapshot, histories, request_start_at=None, request_end_at=None)
