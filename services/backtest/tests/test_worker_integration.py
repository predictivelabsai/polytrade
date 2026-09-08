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
from polytrade_backtest.repository import BacktestRepository, request_fingerprint
from polytrade_backtest.schemas import (
    BacktestConfig,
    BreakoutBacktestConfig,
    MeanReversionBacktestConfig,
    MomentumBacktestConfig,
)
from polytrade_backtest.tasks import PolymarketHistoryClient, run_backtest_task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        MomentumBacktestConfig(slippage="0"),
        MeanReversionBacktestConfig(slippage="0"),
        BreakoutBacktestConfig(
            breakout_window_minutes=60,
            breakout_threshold="0.05",
            slippage="0",
        ),
    ],
)
async def test_mocked_celery_task_persists_a_complete_display_envelope(
    monkeypatch, config: BacktestConfig
) -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    settings = get_settings().model_copy(update={"DATABASE_URL": SecretStr(database_url)})
    repository = await BacktestRepository.open(settings)
    suffix = uuid4().hex
    owner = f"assethero:worker-{suffix}"
    market_id = f"condition-{suffix}"
    dataset = _worker_dataset(market_id, config.strategy)

    async def fetch_dataset(_client, condition_id, requested_config):
        assert condition_id == market_id
        assert requested_config == config
        return dataset

    monkeypatch.setattr(PolymarketHistoryClient, "fetch_dataset", fetch_dataset)
    created = await repository.create_run(
        owner,
        market_id,
        config,
        "worker-create-key",
        request_fingerprint({"marketId": market_id}),
    )
    try:
        await asyncio.to_thread(run_backtest_task.run, str(created.run.run_id))

        envelope = await repository.get_envelope(created.run.run_id, owner)
        trades = await repository.get_trades(created.run.run_id, owner, 0, 50)
        series = await repository.get_series(created.run.run_id, owner)
        assert envelope.run.status == "completed"
        assert envelope.run.phase == "completed"
        assert envelope.run.progress == 100
        assert envelope.run.dataset_hash == dataset.dataset_hash
        assert envelope.result is not None
        assert envelope.result.metrics.trade_count == 1
        assert trades.total == 1
        assert series.points
    finally:
        async with repository.pool.connection() as connection:
            await connection.execute(
                "DELETE FROM polytrade_backtest.backtest_runs WHERE principal_id = %s",
                (owner,),
            )
            await connection.execute(
                "DELETE FROM polytrade_backtest.backtest_datasets WHERE dataset_hash = %s",
                (dataset.dataset_hash,),
            )
        await repository.close()


def _worker_dataset(condition_id: str, strategy: str):
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = start + timedelta(minutes=70)
    starting_price = "0.50" if strategy == "mean_reversion_v1" else "0.40"
    signal_price = "0.40" if strategy == "mean_reversion_v1" else "0.46"
    yes = [
        PriceObservation(start + timedelta(minutes=minute), Decimal(starting_price))
        for minute in range(71)
    ]
    for minute in range(60, 71):
        yes[minute] = PriceObservation(
            start + timedelta(minutes=minute), Decimal(signal_price)
        )
    no = [
        PriceObservation(start + timedelta(minutes=minute), Decimal("0.60"))
        for minute in range(71)
    ]
    return build_dataset(
        MarketSnapshot(
            condition_id=condition_id,
            question="Will the mocked worker complete?",
            yes_token_id=f"yes-{condition_id}",
            no_token_id=f"no-{condition_id}",
            resolved_outcome="YES",
            fee_rate=Decimal("0.04"),
            start_at=start,
            closed_at=end,
        ),
        {"YES": yes, "NO": no},
        request_start_at=None,
        request_end_at=None,
    )
