from celery import Celery

from .config import get_settings

settings = get_settings()

celery_app = Celery(
    "polytrade_backtest",
    broker=settings.REDIS_BROKER_URL.get_secret_value(),
    backend=settings.REDIS_RESULT_URL.get_secret_value(),
    include=["polytrade_backtest.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=settings.BACKTEST_WORKER_CONCURRENCY,
    task_track_started=True,
    task_soft_time_limit=settings.BACKTEST_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=settings.BACKTEST_HARD_TIME_LIMIT_SECONDS,
    broker_transport_options={
        "visibility_timeout": settings.BACKTEST_VISIBILITY_TIMEOUT_SECONDS,
    },
    result_expires=3_600,
    result_extended=False,
    broker_heartbeat=30,
    broker_heartbeat_checkrate=2,
    broker_connection_retry_on_startup=True,
    task_send_sent_event=True,
    worker_send_task_events=True,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)
