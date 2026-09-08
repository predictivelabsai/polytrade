from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from polytrade_backtest.auth import AuthenticatedPrincipal
from polytrade_backtest.config import get_settings
from polytrade_backtest.repository import (
    ActiveRunLimitReached,
    BacktestNotFound,
    CreateRunResult,
)
from polytrade_backtest.schemas import BacktestRun, BacktestRunEnvelope, BacktestRunList
from polytrade_backtest.server import BacktestServices, create_app


class FakeVerifier:
    async def verify(self, token: str, required_scope: str) -> AuthenticatedPrincipal:
        assert required_scope == "research"
        if token == "without-research":  # noqa: S105 - inert test bearer
            raise PermissionError("Missing research scope")
        return AuthenticatedPrincipal(
            identity="assethero:user-1", issuer="assethero", scopes=("research",)
        )


class FakeDispatcher:
    def __init__(self) -> None:
        self.wakes = 0

    def wake(self) -> None:
        self.wakes += 1


class FakeRepository:
    def __init__(self) -> None:
        self.run: BacktestRun | None = None

    async def create_run(self, principal_id, market_id, config, idempotency_key, request_hash):
        assert principal_id == "assethero:user-1"
        assert idempotency_key == "create-key-0001"
        assert len(request_hash) == 64
        self.run = BacktestRun(
            run_id=UUID("11111111-1111-4111-8111-111111111111"),
            market_id=market_id,
            status="queued",
            phase="queued",
            progress=0,
            config=config,
            created_at=datetime(2026, 8, 4, tzinfo=UTC),
        )
        return CreateRunResult(run=self.run, created=True)

    async def list_runs(self, principal_id: str, limit: int):
        assert principal_id == "assethero:user-1"
        return [self.run] if self.run else []

    async def active_run_count(self, principal_id: str):
        assert principal_id == "assethero:user-1"
        return 1 if self.run else 0

    async def get_envelope(self, run_id: UUID, principal_id: str):
        if self.run is None or run_id != self.run.run_id or principal_id != "assethero:user-1":
            raise BacktestNotFound
        return BacktestRunEnvelope(run=self.run)

    async def get_trades(self, run_id, principal_id, offset, limit):
        raise BacktestNotFound

    async def get_series(self, run_id, principal_id):
        raise BacktestNotFound

    async def cancel_run(self, run_id, principal_id, idempotency_key, request_hash):
        raise BacktestNotFound

    async def delete_run(self, run_id, principal_id):
        raise BacktestNotFound


class LimitedFakeRepository(FakeRepository):
    async def create_run(self, principal_id, market_id, config, idempotency_key, request_hash):
        raise ActiveRunLimitReached(10)


@pytest.mark.asyncio
async def test_http_contract_requires_research_auth_and_idempotency() -> None:
    settings = get_settings()
    application = create_app(settings)
    repository = FakeRepository()
    dispatcher = FakeDispatcher()
    application.state.services = BacktestServices(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        verifier=FakeVerifier(),  # type: ignore[arg-type]
        dispatcher=dispatcher,  # type: ignore[arg-type]
    )
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://backtest.test") as client:
        unauthenticated = await client.get("/v1/backtests")
        assert unauthenticated.status_code == 401

        forbidden = await client.get(
            "/v1/backtests", headers={"Authorization": "Bearer without-research"}
        )
        assert forbidden.status_code == 403

        missing_key = await client.post(
            "/v1/backtests",
            headers={"Authorization": "Bearer valid"},
            json={"marketId": "condition-1", "config": {}},
        )
        assert missing_key.status_code == 400

        created = await client.post(
            "/v1/backtests",
            headers={
                "Authorization": "Bearer valid",
                "Idempotency-Key": "create-key-0001",
            },
            json={"marketId": "condition-1", "config": {}},
        )
        assert created.status_code == 202
        assert created.json()["run"]["runId"] == "11111111-1111-4111-8111-111111111111"
        assert created.json()["run"]["config"]["initialCapital"] == "10000"
        assert dispatcher.wakes == 1

        listed = await client.get(
            "/v1/backtests", headers={"Authorization": "Bearer valid"}
        )
        assert BacktestRunList.model_validate(listed.json()).items[0].market_id == "condition-1"
        assert listed.json()["activeCount"] == 1
        assert listed.json()["activeLimit"] == 10

        unknown = await client.get(
            "/v1/backtests/22222222-2222-4222-8222-222222222222",
            headers={"Authorization": "Bearer valid"},
        )
        assert unknown.status_code == 404
        assert unknown.json() == {"detail": "Backtest not found"}


@pytest.mark.asyncio
async def test_create_reports_the_bounded_active_run_limit() -> None:
    settings = get_settings()
    application = create_app(settings)
    application.state.services = BacktestServices(
        settings=settings,
        repository=LimitedFakeRepository(),  # type: ignore[arg-type]
        verifier=FakeVerifier(),  # type: ignore[arg-type]
        dispatcher=FakeDispatcher(),  # type: ignore[arg-type]
    )
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://backtest.test") as client:
        response = await client.post(
            "/v1/backtests",
            headers={
                "Authorization": "Bearer valid",
                "Idempotency-Key": "suite-over-limit",
            },
            json={"marketId": "condition-1", "config": {}},
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "At most 10 backtests can be queued or running at once"
    }


@pytest.mark.asyncio
async def test_cancel_also_requires_an_idempotency_key() -> None:
    settings = get_settings()
    application = create_app(settings)
    application.state.services = BacktestServices(
        settings=settings,
        repository=FakeRepository(),  # type: ignore[arg-type]
        verifier=FakeVerifier(),  # type: ignore[arg-type]
        dispatcher=FakeDispatcher(),  # type: ignore[arg-type]
    )
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://backtest.test") as client:
        response = await client.post(
            "/v1/backtests/11111111-1111-4111-8111-111111111111/cancel",
            headers={"Authorization": "Bearer valid"},
        )
    assert response.status_code == 400
