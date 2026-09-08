from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from billiard.exceptions import SoftTimeLimitExceeded
from celery import Task

from .celery_app import celery_app
from .config import get_settings
from .engine import run_backtest
from .market import MarketDataError, PolymarketHistoryClient, validate_dataset_history
from .repository import BacktestRepository

logger = logging.getLogger("polytrade.backtest.worker")


@celery_app.task(
    bind=True,
    base=Task,
    name="polytrade_backtest.run",
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_backtest_task(self: Task, run_id: str) -> None:
    try:
        asyncio.run(_execute(UUID(run_id)))
    except MarketDataError as exc:
        if exc.retryable and self.request.retries < get_settings().BACKTEST_MAX_RETRIES:
            asyncio.run(_prepare_retry(UUID(run_id)))
            raise self.retry(
                exc=exc,
                countdown=min(60, 2 ** (self.request.retries + 1)),
                max_retries=get_settings().BACKTEST_MAX_RETRIES,
            ) from exc
        asyncio.run(_fail(UUID(run_id), exc.code, exc.public_message))
    except SoftTimeLimitExceeded:
        asyncio.run(_fail(UUID(run_id), "TIME_LIMIT", "Backtest exceeded its time limit"))
    except Exception as exc:  # noqa: BLE001 - task boundary is sanitized before persistence
        logger.exception("backtest failed run_id=%s error_type=%s", run_id, type(exc).__name__)
        asyncio.run(_fail(UUID(run_id), "INTERNAL_ERROR", "Backtest could not be completed"))


async def _execute(run_id: UUID) -> None:
    settings = get_settings()
    repository = await BacktestRepository.open(settings)
    try:
        claim = await repository.claim_run(run_id)
        if claim is None:
            return
        dataset = await repository.find_dataset(
            claim.market_id, claim.config.start_at, claim.config.end_at
        )
        if dataset is None:
            dataset = await PolymarketHistoryClient(settings).fetch_dataset(
                claim.market_id, claim.config
            )
            await repository.save_dataset(dataset)
        validate_dataset_history(dataset.histories, claim.config)
        await repository.set_market_metadata(run_id, dataset)
        if await repository.cancellation_requested(run_id):
            await repository.finish_cancelled(run_id)
            return
        strategy_name = {
            "momentum_v1": "momentum",
            "mean_reversion_v1": "mean-reversion",
            "breakout_v1": "breakout",
        }[claim.config.strategy]
        await repository.progress(
            run_id, "simulating", 55, f"Replaying {strategy_name} strategy"
        )
        output = run_backtest(
            dataset.histories,
            resolved_outcome=dataset.snapshot.resolved_outcome,
            fee_rate=dataset.snapshot.fee_rate,
            config=claim.config,
            settlement_at=dataset.snapshot.closed_at,
        )
        if await repository.cancellation_requested(run_id):
            await repository.finish_cancelled(run_id)
            return
        await repository.progress(run_id, "saving", 90, "Saving reproducible results")
        await repository.save_result(run_id, output.metrics, output.trades, output.series)
    finally:
        await repository.close()


async def _prepare_retry(run_id: UUID) -> None:
    repository = await BacktestRepository.open(get_settings())
    try:
        await repository.requeue_for_retry(run_id, "Retrying temporary Polymarket data failure")
    finally:
        await repository.close()


async def _fail(run_id: UUID, code: str, message: str) -> None:
    repository = await BacktestRepository.open(get_settings())
    try:
        if await repository.cancellation_requested(run_id):
            await repository.finish_cancelled(run_id)
        else:
            await repository.fail_run(run_id, code, message)
    finally:
        await repository.close()
