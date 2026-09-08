from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from .auth import AuthenticatedPrincipal, TokenVerifier
from .celery_app import celery_app
from .config import BacktestSettings, get_settings
from .repository import (
    ActiveRunLimitReached,
    BacktestNotFound,
    BacktestRepository,
    IdempotencyMismatch,
    request_fingerprint,
)
from .schemas import (
    BacktestRunEnvelope,
    BacktestRunList,
    BacktestSeriesResponse,
    BacktestTradesResponse,
    CreateBacktestRequest,
)

logger = logging.getLogger("polytrade.backtest.api")


class OutboxDispatcher:
    def __init__(self, repository: BacktestRepository, settings: BacktestSettings) -> None:
        self.repository = repository
        self.settings = settings
        self._wake = asyncio.Event()

    def wake(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        while True:
            try:
                await self.repository.recover_stale(self.settings.BACKTEST_STALE_SECONDS)
                await self.dispatch_ready()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - background loop remains available
                logger.error("outbox dispatch failed error_type=%s", type(exc).__name__)
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.settings.BACKTEST_OUTBOX_INTERVAL_SECONDS
                )
                self._wake.clear()
            except TimeoutError:
                pass

    async def dispatch_ready(self) -> None:
        for run_id in await self.repository.ready_outbox():
            try:
                await asyncio.to_thread(
                    celery_app.send_task,
                    "polytrade_backtest.run",
                    args=[str(run_id)],
                    task_id=str(run_id),
                )
                await self.repository.mark_published(run_id)
            except Exception as exc:  # noqa: BLE001 - persisted for bounded retry
                await self.repository.mark_publish_failed(run_id, type(exc).__name__)


@dataclass
class BacktestServices:
    settings: BacktestSettings
    repository: BacktestRepository
    verifier: TokenVerifier
    dispatcher: OutboxDispatcher


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = application.state.configured_settings
    repository = await BacktestRepository.open(settings)
    dispatcher_task: asyncio.Task[None] | None = None
    try:
        if not await repository.schema_ready():
            raise RuntimeError("Backtest database schema is not ready")
        dispatcher = OutboxDispatcher(repository, settings)
        application.state.services = BacktestServices(
            settings=settings,
            repository=repository,
            verifier=TokenVerifier(settings),
            dispatcher=dispatcher,
        )
        dispatcher_task = asyncio.create_task(dispatcher.run())
        yield
    finally:
        application.state.services = None
        if dispatcher_task is not None:
            dispatcher_task.cancel()
            with suppress(asyncio.CancelledError):
                await dispatcher_task
        await repository.close()


def create_app(settings: BacktestSettings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    application = FastAPI(
        title="PolyTrade Backtest API",
        version="1.0.0",
        lifespan=_lifespan,
    )
    application.state.configured_settings = resolved
    application.state.services = None
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        max_age=600,
    )

    @application.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/readyz")
    async def readiness(request: Request, response: Response) -> dict[str, Any]:
        services = getattr(request.app.state, "services", None)
        ready = False
        if isinstance(services, BacktestServices):
            try:
                ready = await services.repository.schema_ready()
            except Exception as exc:  # noqa: BLE001 - readiness fails closed
                logger.error("database readiness failed error_type=%s", type(exc).__name__)
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if ready else "not_ready", "database": ready}

    @application.post(
        "/v1/backtests",
        response_model=BacktestRunEnvelope,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_backtest(
        body: CreateBacktestRequest,
        request: Request,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> BacktestRunEnvelope:
        key = _idempotency_key(idempotency_key)
        services = get_services(request)
        payload = body.model_dump(mode="json", by_alias=True)
        try:
            created = await services.repository.create_run(
                principal.identity,
                body.market_id,
                body.config,
                key,
                request_fingerprint(payload),
            )
        except IdempotencyMismatch as exc:
            raise HTTPException(status_code=409, detail="Idempotency-Key payload mismatch") from exc
        except ActiveRunLimitReached as exc:
            raise HTTPException(
                status_code=409,
                detail=f"At most {exc.limit} backtests can be queued or running at once",
            ) from exc
        if not created.created:
            response.status_code = status.HTTP_200_OK
        else:
            services.dispatcher.wake()
        return BacktestRunEnvelope(run=created.run)

    @application.get("/v1/backtests", response_model=BacktestRunList)
    async def list_backtests(
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> BacktestRunList:
        services = get_services(request)
        return BacktestRunList(
            items=await services.repository.list_runs(principal.identity, limit),
            active_count=await services.repository.active_run_count(principal.identity),
            active_limit=services.settings.BACKTEST_MAX_ACTIVE_RUNS_PER_OWNER,
        )

    @application.get("/v1/backtests/{run_id}", response_model=BacktestRunEnvelope)
    async def get_backtest(
        run_id: UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    ) -> BacktestRunEnvelope:
        try:
            return await get_services(request).repository.get_envelope(run_id, principal.identity)
        except BacktestNotFound as exc:
            raise HTTPException(status_code=404, detail="Backtest not found") from exc

    @application.get("/v1/backtests/{run_id}/trades", response_model=BacktestTradesResponse)
    async def get_backtest_trades(
        run_id: UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> BacktestTradesResponse:
        try:
            return await get_services(request).repository.get_trades(
                run_id, principal.identity, offset, limit
            )
        except BacktestNotFound as exc:
            raise HTTPException(status_code=404, detail="Backtest not found") from exc

    @application.get("/v1/backtests/{run_id}/series", response_model=BacktestSeriesResponse)
    async def get_backtest_series(
        run_id: UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    ) -> BacktestSeriesResponse:
        try:
            return await get_services(request).repository.get_series(run_id, principal.identity)
        except BacktestNotFound as exc:
            raise HTTPException(status_code=404, detail="Backtest not found") from exc

    @application.post("/v1/backtests/{run_id}/cancel", response_model=BacktestRunEnvelope)
    async def cancel_backtest(
        run_id: UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> BacktestRunEnvelope:
        key = _idempotency_key(idempotency_key)
        try:
            run = await get_services(request).repository.cancel_run(
                run_id,
                principal.identity,
                key,
                request_fingerprint({"runId": str(run_id)}),
            )
            return BacktestRunEnvelope(run=run)
        except BacktestNotFound as exc:
            raise HTTPException(status_code=404, detail="Backtest not found") from exc
        except IdempotencyMismatch as exc:
            raise HTTPException(status_code=409, detail="Idempotency-Key payload mismatch") from exc

    @application.delete("/v1/backtests/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_backtest(
        run_id: UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    ) -> Response:
        try:
            await get_services(request).repository.delete_run(run_id, principal.identity)
        except BacktestNotFound as exc:
            raise HTTPException(status_code=404, detail="Backtest not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return application


def get_services(request: Request) -> BacktestServices:
    services = getattr(request.app.state, "services", None)
    if not isinstance(services, BacktestServices):
        raise HTTPException(status_code=503, detail="Backtest service is not ready")
    return services


async def require_principal(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AuthenticatedPrincipal:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or separator != " " or not token or " " in token:
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        return await get_services(request).verifier.verify(token, required_scope="research")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _idempotency_key(value: str | None) -> str:
    if value is None or not 8 <= len(value) <= 200:
        raise HTTPException(status_code=400, detail="Idempotency-Key must be 8-200 characters")
    if not all(character.isalnum() or character in "._:-" for character in value):
        raise HTTPException(status_code=400, detail="Idempotency-Key contains invalid characters")
    return value


app = create_app()
