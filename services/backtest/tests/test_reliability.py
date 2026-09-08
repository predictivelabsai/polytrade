from uuid import UUID

import pytest

import polytrade_backtest.tasks as task_module
from polytrade_backtest.celery_app import celery_app
from polytrade_backtest.config import get_settings
from polytrade_backtest.market import MarketDataError
from polytrade_backtest.server import OutboxDispatcher
from polytrade_backtest.tasks import run_backtest_task


class OutboxRepository:
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        self.published: list[UUID] = []
        self.failed: list[tuple[UUID, str]] = []

    async def ready_outbox(self):
        return [self.run_id]

    async def mark_published(self, run_id: UUID):
        self.published.append(run_id)

    async def mark_publish_failed(self, run_id: UUID, error_type: str):
        self.failed.append((run_id, error_type))


@pytest.mark.asyncio
async def test_outbox_publishes_only_the_run_id_with_matching_task_id(monkeypatch) -> None:
    run_id = UUID("11111111-1111-4111-8111-111111111111")
    repository = OutboxRepository(run_id)
    calls: list[tuple[str, list[str], str]] = []

    def send_task(name: str, *, args: list[str], task_id: str):
        calls.append((name, args, task_id))

    monkeypatch.setattr(celery_app, "send_task", send_task)
    dispatcher = OutboxDispatcher(repository, get_settings())  # type: ignore[arg-type]
    await dispatcher.dispatch_ready()

    assert calls == [("polytrade_backtest.run", [str(run_id)], str(run_id))]
    assert repository.published == [run_id]
    assert repository.failed == []


@pytest.mark.asyncio
async def test_outbox_retains_failed_publish_for_postgres_reconciliation(monkeypatch) -> None:
    run_id = UUID("22222222-2222-4222-8222-222222222222")
    repository = OutboxRepository(run_id)

    def unavailable(*_args, **_kwargs):
        raise ConnectionError("secret Redis detail")

    monkeypatch.setattr(celery_app, "send_task", unavailable)
    dispatcher = OutboxDispatcher(repository, get_settings())  # type: ignore[arg-type]
    await dispatcher.dispatch_ready()

    assert repository.published == []
    assert repository.failed == [(run_id, "ConnectionError")]


def test_celery_delivery_and_worker_loss_settings_are_fail_safe() -> None:
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.worker_concurrency == 2
    assert celery_app.conf.task_soft_time_limit < celery_app.conf.task_time_limit
    assert (
        celery_app.conf.broker_transport_options["visibility_timeout"]
        > celery_app.conf.task_time_limit
    )


def test_retry_exhaustion_persists_only_the_public_market_error(monkeypatch) -> None:
    run_id = UUID("33333333-3333-4333-8333-333333333333")
    failures: list[tuple[UUID, str, str]] = []

    async def unavailable(_run_id: UUID) -> None:
        raise MarketDataError(
            "POLYMARKET_UNAVAILABLE",
            "Polymarket history is temporarily unavailable",
            retryable=True,
        )

    async def fail(failed_run_id: UUID, code: str, message: str) -> None:
        failures.append((failed_run_id, code, message))

    monkeypatch.setattr(task_module, "_execute", unavailable)
    monkeypatch.setattr(task_module, "_fail", fail)
    run_backtest_task.push_request(retries=get_settings().BACKTEST_MAX_RETRIES)
    try:
        run_backtest_task.run(str(run_id))
    finally:
        run_backtest_task.pop_request()

    assert failures == [
        (
            run_id,
            "POLYMARKET_UNAVAILABLE",
            "Polymarket history is temporarily unavailable",
        )
    ]
