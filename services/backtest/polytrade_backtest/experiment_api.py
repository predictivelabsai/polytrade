"""Static experiment routes are registered before /v1/backtests/{run_id}."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query, Request, Response

from .auth import AuthenticatedPrincipal
from .experiments import (
    CreateExperimentRequest,
    ExperimentEnvelope,
    ExperimentList,
    WalkForwardConfig,
)
from .repository import (
    ActiveRunLimitReached,
    BacktestNotFound,
    IdempotencyMismatch,
    request_fingerprint,
)


def register_experiment_routes(app, get_services, require_principal, idempotency_key):
    def repository(request):
        repo = get_services(request).experiments
        if repo is None:
            raise HTTPException(503, "Experiment service is not ready")
        return repo

    async def create(body, request, response, principal, key):
        key = idempotency_key(key)
        try:
            body.validate_budget(get_services(request).settings.BACKTEST_MAX_EXPERIMENT_SIMULATIONS)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            run, created = await repository(request).create(
                principal.identity,
                body,
                key,
                request_fingerprint(body.model_dump(mode="json", by_alias=True)),
            )
        except IdempotencyMismatch as exc:
            raise HTTPException(409, "Idempotency-Key payload mismatch") from exc
        except ActiveRunLimitReached as exc:
            raise HTTPException(
                409, f"At most {exc.limit} backtests or experiments can be active"
            ) from exc
        response.status_code = 202 if created else 200
        if created:
            get_services(request).dispatcher.wake()
        return ExperimentEnvelope(experiment=run)

    @app.post("/v1/backtests/experiments", response_model=ExperimentEnvelope, status_code=202)
    async def create_experiment(
        body: CreateExperimentRequest,
        request: Request,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
        key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        return await create(body, request, response, principal, key)

    @app.get("/v1/backtests/experiments", response_model=ExperimentList)
    async def list_experiments(
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        return ExperimentList(items=await repository(request).list(principal.identity, limit))

    @app.get("/v1/backtests/experiments/{experiment_id}", response_model=ExperimentEnvelope)
    async def get_experiment(
        experiment_id: UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    ):
        try:
            return await repository(request).get(experiment_id, principal.identity)
        except BacktestNotFound as exc:
            raise HTTPException(404, "Experiment not found") from exc

    @app.post("/v1/backtests/experiments/{experiment_id}/cancel", response_model=ExperimentEnvelope)
    async def cancel_experiment(
        experiment_id: UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    ):
        try:
            return await repository(request).cancel(experiment_id, principal.identity)
        except BacktestNotFound as exc:
            raise HTTPException(404, "Experiment not found") from exc

    @app.post(
        "/v1/backtests/experiments/{experiment_id}/walk-forward",
        response_model=ExperimentEnvelope,
        status_code=202,
    )
    async def walk_forward_experiment(
        experiment_id: UUID,
        body: WalkForwardConfig,
        request: Request,
        response: Response,
        principal: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
        key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        try:
            source = await repository(request).get(experiment_id, principal.identity)
        except BacktestNotFound as exc:
            raise HTTPException(404, "Experiment not found") from exc
        # Preserve the whole candidate set. Never use the full-history winner to tune WFO.
        definition = source.experiment.request.model_copy(
            update={"mode": "walk_forward", "walk_forward": body}
        )
        return await create(definition, request, response, principal, key)
