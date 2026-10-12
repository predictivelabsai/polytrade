import os
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_experiments import dataset, evaluate, request
from test_server import FakeDispatcher, FakeVerifier

from polytrade_backtest.config import get_settings
from polytrade_backtest.experiment_repository import ExperimentRepository
from polytrade_backtest.experiment_tasks import execute_experiment
from polytrade_backtest.experiments import CreateExperimentRequest, StrategyGrid
from polytrade_backtest.repository import (
    ActiveRunLimitReached,
    BacktestRepository,
    request_fingerprint,
)
from polytrade_backtest.schemas import MomentumBacktestConfig
from polytrade_backtest.server import BacktestServices, create_app


@pytest.fixture
async def repositories():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    settings = get_settings().model_copy(
        update={"DATABASE_URL": SecretStr(url), "BACKTEST_MAX_ACTIVE_RUNS_PER_OWNER": 3}
    )
    backtests = await BacktestRepository.open(settings)
    owner = f"clerk:experiment-test:{uuid4()}"
    try:
        yield settings, backtests, ExperimentRepository(backtests), owner
    finally:
        async with backtests.pool.connection() as connection:
            for table in ("backtest_runs", "backtest_experiments"):
                from psycopg import sql

                await connection.execute(
                    sql.SQL(
                        "DELETE FROM polytrade_backtest.{} WHERE principal_id = ANY(%s)"
                    ).format(sql.Identifier(table)),
                    ([owner, f"{owner}:foreign"],),
                )
        await backtests.close()


@pytest.mark.asyncio
async def test_experiment_http_auth_budget_idempotency_followup_and_cancellation(repositories):
    settings, backtests, experiments, owner = repositories

    class Verifier(FakeVerifier):
        async def verify(self, token, required_scope):
            from dataclasses import replace

            return replace(await super().verify(token, required_scope), identity=owner)

    app = create_app(settings)
    app.state.services = BacktestServices(
        settings, backtests, Verifier(), FakeDispatcher(), experiments
    )
    headers = {"Authorization": "Bearer example", "Idempotency-Key": f"experiment-{uuid4()}"}
    definition = request().model_dump(mode="json", by_alias=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/v1/backtests/experiments")).status_code == 401
        assert (
            await client.post(
                "/v1/backtests/experiments",
                json=definition,
                headers={"Authorization": "Bearer example"},
            )
        ).status_code == 400
        created = await client.post("/v1/backtests/experiments", json=definition, headers=headers)
        assert created.status_code == 202, created.text
        eid = created.json()["experiment"]["experimentId"]
        repeated = await client.post("/v1/backtests/experiments", json=definition, headers=headers)
        assert repeated.status_code == 200
        assert repeated.json()["experiment"]["experimentId"] == eid
        mismatch = await client.post(
            "/v1/backtests/experiments",
            json={**definition, "initialCapital": "20"},
            headers=headers,
        )
        assert mismatch.status_code == 409
        assert (
            await client.get(f"/v1/backtests/experiments/{uuid4()}", headers=headers)
        ).status_code == 404
        foreign, _ = await experiments.create(
            f"{owner}:foreign", request(), str(uuid4()), request_fingerprint({})
        )
        assert (
            await client.get(f"/v1/backtests/experiments/{foreign.experiment_id}", headers=headers)
        ).status_code == 404
        assert (
            await client.post(
                f"/v1/backtests/experiments/{foreign.experiment_id}/cancel", headers=headers
            )
        ).status_code == 404
        following = await client.post(
            f"/v1/backtests/experiments/{eid}/walk-forward",
            json={},
            headers={**headers, "Idempotency-Key": f"followup-{uuid4()}"},
        )
        assert following.status_code == 202, following.text
        follow = following.json()["experiment"]
        assert (
            follow["request"]["strategies"] == created.json()["experiment"]["request"]["strategies"]
        )
        assert follow["totalSimulations"] == 25
        assert follow["request"]["mode"] == "walk_forward"
        for id_ in [eid, follow["experimentId"]]:
            cancel = await client.post(f"/v1/backtests/experiments/{id_}/cancel", headers=headers)
            assert cancel.json()["experiment"]["status"] == "cancelled"
            assert (
                await client.post(f"/v1/backtests/experiments/{id_}/cancel", headers=headers)
            ).json() == cancel.json()
        too_big = {
            **definition,
            "marketIds": [f"market-{i}" for i in range(50)],
            "mode": "walk_forward",
        }
        assert (
            await client.post(
                "/v1/backtests/experiments",
                json=too_big,
                headers={**headers, "Idempotency-Key": f"large-{uuid4()}"},
            )
        ).status_code == 422


@pytest.mark.asyncio
async def test_experiments_share_capacity_and_fence_cancelled_or_stale_workers(repositories):
    settings, backtests, experiments, owner = repositories
    req = request()
    run, _ = await experiments.create(owner, req, str(uuid4()), request_fingerprint({}))
    for _ in range(2):
        await backtests.create_run(
            owner, "test", MomentumBacktestConfig(), str(uuid4()), request_fingerprint({})
        )
    assert await backtests.active_run_count(owner) == 3
    with pytest.raises(ActiveRunLimitReached):
        await experiments.create(owner, req, str(uuid4()), request_fingerprint({}))
    with pytest.raises(ActiveRunLimitReached):
        await backtests.create_run(
            owner, "test", MomentumBacktestConfig(), str(uuid4()), request_fingerprint({})
        )
    old = await experiments.claim(run.experiment_id)
    assert await experiments.claim(run.experiment_id) is None
    _, steps = evaluate(req, dataset())
    key, cp = steps[0][0].key, steps[0][1]
    assert await experiments.save_checkpoint(old, key, cp)
    assert await experiments.save_checkpoint(old, key, cp)
    assert (await experiments.get(run.experiment_id, owner)).experiment.completed_simulations == 1
    async with backtests.pool.connection() as connection:
        await connection.execute(
            "UPDATE polytrade_backtest.backtest_experiments "
            "SET heartbeat_at = now() - interval '2 hours' WHERE experiment_id = %s",
            (run.experiment_id,),
        )
    await experiments.recover_stale(960, 5)
    replacement = await experiments.claim(run.experiment_id)
    assert replacement.token != old.token
    assert await experiments.save_checkpoint(old, "late-step", cp) is False
    assert await experiments.checkpoint(run.experiment_id, key) == cp
    await experiments.cancel(run.experiment_id, owner)
    assert await experiments.save_checkpoint(replacement, "cancelled-step", cp) is False
    await experiments.finish(replacement, evaluate(req, dataset())[0])
    assert (await experiments.get(run.experiment_id, owner)).experiment.status == "cancelled"


@pytest.mark.asyncio
async def test_worker_resumes_18_variant_grid_and_walk_forward_without_duplicate_work(
    repositories, monkeypatch
):
    settings, backtests, experiments, owner = repositories
    import polytrade_backtest.experiment_tasks as tasks

    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    data = dataset()
    await backtests.save_dataset(data)
    req = CreateExperimentRequest(
        market_ids=["market-1"],
        strategies=[
            StrategyGrid(
                base_config=MomentumBacktestConfig(momentum_window_minutes=5),
                parameters={
                    "momentumThreshold": ["0.01", "0.02", "0.03"],
                    "takeProfit": ["0.01", "0.02"],
                    "maxHoldMinutes": [5, 10, 15],
                },
            )
        ],
    )
    original = tasks.run_backtest
    calls = []

    def record(*args, **kwargs):
        calls.append(kwargs["config"])
        return original(*args, **kwargs)

    monkeypatch.setattr(tasks, "run_backtest", record)
    for definition in [req, request(mode="walk_forward")]:
        run, _ = await experiments.create(owner, definition, str(uuid4()), request_fingerprint({}))
        for _ in range(6):
            await execute_experiment(run.experiment_id)
            envelope = await experiments.get(run.experiment_id, owner)
            if envelope.experiment.status != "queued":
                break
        assert envelope.experiment.status == "completed", envelope.experiment
        assert envelope.experiment.completed_simulations == definition.simulation_count()
        expected, _ = evaluate(definition, data)
        assert envelope.result == expected
        count = len(calls)
        await execute_experiment(run.experiment_id)
        assert len(calls) == count
    assert len(calls) == 18 + 25


@pytest.mark.asyncio
async def test_experiment_data_retries_are_bounded_and_late_publish_is_fenced(
    repositories, monkeypatch
):
    settings, backtests, experiments, owner = repositories
    import polytrade_backtest.experiment_tasks as tasks
    from polytrade_backtest.market import MarketDataError

    settings = settings.model_copy(update={"BACKTEST_MAX_RETRIES": 1})
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    definition = request().model_copy(update={"market_ids": [f"missing-{uuid4()}"]})
    run, _ = await experiments.create(owner, definition, str(uuid4()), request_fingerprint({}))
    original_dispatch = next(
        row for row in await experiments.ready() if row["experiment_id"] == run.experiment_id
    )

    async def unavailable(*_args, **_kwargs):
        raise MarketDataError(
            "DATA_UNAVAILABLE", "History is temporarily unavailable", retryable=True
        )

    monkeypatch.setattr(tasks.PolymarketHistoryClient, "fetch_dataset", unavailable)
    await execute_experiment(run.experiment_id)
    queued = await experiments.get(run.experiment_id, owner)
    assert queued.experiment.status == "queued"
    assert queued.experiment.completed_simulations == 0
    # A publish acknowledgement from the previous chunk must not hide the retry outbox.
    await experiments.published(run.experiment_id, original_dispatch["dispatch_id"], True)
    async with backtests.pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT published_at FROM polytrade_backtest.backtest_experiments "
            "WHERE experiment_id = %s",
            (run.experiment_id,),
        )
        assert (await cursor.fetchone())["published_at"] is None
    await execute_experiment(run.experiment_id)
    failed = await experiments.get(run.experiment_id, owner)
    assert failed.experiment.status == "failed"
    assert failed.experiment.failure.code == "DATA_UNAVAILABLE"
