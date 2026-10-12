"""Bounded experiment worker; each successful simulation is a durable checkpoint."""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from uuid import UUID

from billiard.exceptions import SoftTimeLimitExceeded

from .celery_app import celery_app
from .config import get_settings
from .engine import run_backtest
from .experiment_repository import ExperimentRepository
from .experiments import SimulationCheckpoint, expand_candidates, experiment_plan
from .market import MarketDataError, PolymarketHistoryClient, dataset_covers_warmup
from .repository import BacktestRepository
from .schemas import strategy_lookback_minutes

logger = logging.getLogger("polytrade.backtest.experiments")


@celery_app.task(name="polytrade_backtest.experiment", acks_late=True, reject_on_worker_lost=True)
def run_experiment_task(experiment_id: str) -> None:
    asyncio.run(execute_experiment(UUID(experiment_id)))


async def execute_experiment(experiment_id: UUID) -> None:
    settings = get_settings()
    backtests = await BacktestRepository.open(settings)
    repository = ExperimentRepository(backtests)
    claim = None
    try:
        claim = await repository.claim(experiment_id)
        if claim is None:
            return
        request = claim.request
        request.validate_budget(settings.BACKTEST_MAX_EXPERIMENT_SIMULATIONS)
        # Fetch once per market, covering all requested periods and the longest warm-up.
        configurations = [config for _, config in expand_candidates(request)]
        fetch_config = max(configurations, key=strategy_lookback_minutes)
        starts = [period.start_at for period in request.periods]
        ends = [period.end_at for period in request.periods]
        fetch_config = fetch_config.model_copy(
            update={
                "max_fill_delay_minutes": max(c.max_fill_delay_minutes for c in configurations),
                "start_at": min(starts) if all(starts) else None,
                "end_at": max(ends) if all(ends) else None,
            }
        )
        datasets = {}
        for market_id in request.market_ids:
            if not await repository.heartbeat(claim, f"Loading history for {market_id}"):
                return
            if market_id in claim.dataset_hashes:
                dataset = await repository.pinned_dataset(claim.dataset_hashes[market_id])
            else:
                dataset = await backtests.find_dataset(
                    market_id, fetch_config.start_at, fetch_config.end_at
                )
                if dataset is None or not dataset_covers_warmup(dataset, fetch_config):
                    dataset = await PolymarketHistoryClient(settings).fetch_dataset(
                        market_id, fetch_config
                    )
                    await backtests.save_dataset(dataset)
                await repository.pin_dataset(claim, market_id, dataset.dataset_hash)
            datasets[market_id] = dataset
        plan = experiment_plan(request, datasets)
        checkpoints = await repository.checkpoints(experiment_id)
        started = time.monotonic()
        computed = 0
        try:
            job = next(plan)
            while True:
                checkpoint = checkpoints.get(job.key)
                if checkpoint is None:
                    if not await repository.heartbeat(claim, f"Evaluating {job.market_id}"):
                        return
                    # Yield between chunks so a large grid does not monopolize a worker.
                    # Replaying checkpoints alone must never exhaust the next chunk.
                    if computed >= 10 or (computed > 0 and time.monotonic() - started > 120):
                        await repository.requeue(claim)
                        return
                    dataset = datasets[job.market_id]
                    calculation = asyncio.create_task(
                        asyncio.to_thread(
                            run_backtest,
                            dataset.histories,
                            config=job.config,
                            fee_rate=dataset.snapshot.fee_rate,
                            resolved_outcome=dataset.snapshot.resolved_outcome,
                            settlement_at=dataset.snapshot.closed_at,
                            benchmark_capital=Decimal(request.initial_capital),
                        )
                    )
                    while not calculation.done():
                        await asyncio.wait({calculation}, timeout=10)
                        if not await repository.heartbeat(claim, f"Simulating {job.market_id}"):
                            # The calculation has no side effects; cancellation fences its result.
                            calculation.cancel()
                            return
                    checkpoint = SimulationCheckpoint.from_output(
                        calculation.result(), job.keep_path
                    )
                    if not await repository.save_checkpoint(claim, job.key, checkpoint):
                        return
                    computed += 1
                job = plan.send(checkpoint)
        except StopIteration as completed:
            await repository.finish(claim, completed.value)
    except MarketDataError as exc:
        if claim is not None:
            if exc.retryable and claim.retries < settings.BACKTEST_MAX_RETRIES:
                await repository.requeue(claim, retry=True)
            else:
                await repository.fail(claim, exc.code, exc.public_message)
    except SoftTimeLimitExceeded:
        if claim is not None:
            if claim.retries < settings.BACKTEST_MAX_RETRIES:
                await repository.requeue(claim, retry=True)
            else:
                await repository.fail(claim, "TIME_LIMIT", "Simulation exceeded its time limit")
    except Exception as exc:  # noqa: BLE001 - never persist credentials or provider response bodies
        logger.exception("experiment failed id=%s error_type=%s", experiment_id, type(exc).__name__)
        if claim is not None:
            await repository.fail(claim, "INTERNAL_ERROR", "Experiment could not be completed")
    finally:
        await backtests.close()
