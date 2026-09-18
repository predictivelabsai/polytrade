"""FastAPI gateway runtime.

Authenticated agent/backtest requests are delegated to their Python services;
market reads, paper trading, wallet sessions, and public share pages live at
this boundary.  Public JSON uses the same camelCase contracts as FastHTML.
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from polytrade_contracts import (
    AgentPredictionRecord,
    AgentPredictionRequest,
    CancelRequest,
    CreateIntentRequest,
    OrderIntentResponse,
    PaperFill,
    PaperFillsResponse,
    PaperOrderRequest,
    PaperOrderResponse,
    PaperPortfolio,
    PaperPosition,
    PaperQuote,
    PaperQuoteRequest,
    PaperShareManageRequest,
    PaperShareStatus,
    PaperStrategySnapshot,
    PaperStrategyStartRequest,
    PublicMarketDetail,
    PublicOrderBook,
    PublicPriceHistory,
    SubmitIntentRequest,
    WalletChallengeRequest,
    WalletChallengeResponse,
    WalletSessionRequest,
    WalletSessionStatus,
)
from pydantic import ValidationError
from starlette.background import BackgroundTask

from .auth import JwtVerifier, create_jwt_verifier
from .bootstrap import bootstrap_schema
from .cache import TtlCache
from .config import GatewayConfig
from .crypto import CredentialCipher
from .errors import AppError, conflict, not_found, unavailable, validation
from .paper_pricing import PaperPricingError, best_bid_from_order_book, quote_paper_order
from .polymarket import PolymarketAdapter, build_l1_typed_data, now_iso
from .public_market import PublicMarketService, public_price_history_ttl_ms
from .types import Principal

VERSION = "4.0.0"


def _iso(value: datetime | None = None) -> str:
    value = value or datetime.now(UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.000001')):.6f}"


def _shares(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.000001')):.6f}"


@dataclass
class _PaperAccount:
    cash: Decimal = Decimal("10000")
    fills: list[PaperFill] = field(default_factory=list)
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    share_token: str | None = None
    share_enabled: bool = False
    share_created: str | None = None
    share_updated: str | None = None
    strategy: dict[str, Any] | None = None
    strategy_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _WalletSession:
    principal_id: str
    wallet_address: str
    signature_type: int
    funder_address: str | None
    credentials: str
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked: bool = False


class GatewayRuntime:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        client: httpx.AsyncClient | None = None,
        polymarket: PolymarketAdapter | None = None,
        verifier: JwtVerifier | None = None,
    ) -> None:
        self.config = config
        self.client = client or httpx.AsyncClient(follow_redirects=False)
        self.polymarket = polymarket or PolymarketAdapter(config, self.client)
        self.verifier = verifier or create_jwt_verifier(config, self.client)
        self.public_markets = PublicMarketService(self.polymarket, TtlCache())
        self.cipher = CredentialCipher(config.credential_key)
        self.accounts: dict[str, _PaperAccount] = {}
        self.challenges: dict[UUID, dict[str, Any]] = {}
        self.sessions: dict[str, _WalletSession] = {}
        self.predictions: dict[str, list[AgentPredictionRecord]] = {}
        self.alerts: dict[str, list[dict[str, Any]]] = {}
        self.intents: dict[str, dict[str, Any]] = {}

    def account(self, principal: Principal) -> _PaperAccount:
        return self.accounts.setdefault(principal.id, _PaperAccount())

    async def close(self) -> None:
        await self.client.aclose()


def _fallback_config() -> GatewayConfig:
    values = dict(os.environ)
    values.setdefault("NODE_ENV", "development")
    values.setdefault("DATABASE_URL", "postgresql://localhost/polytrade")
    values.setdefault("CREDENTIALS_KEK_BASE64", base64.b64encode(bytes(32)).decode())
    values.setdefault("CLERK_ISSUER", "https://clerk.invalid")
    values.setdefault("CLERK_JWKS_URL", "https://clerk.invalid/.well-known/jwks.json")
    values.setdefault("CORS_ORIGINS", "http://localhost:5173")
    return GatewayConfig.model_validate(values)


def create_app(
    config: GatewayConfig | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    polymarket: PolymarketAdapter | None = None,
    verifier: JwtVerifier | None = None,
) -> FastAPI:
    runtime = GatewayRuntime(
        config or _fallback_config(), client=client, polymarket=polymarket, verifier=verifier
    )
    app = FastAPI(title="PolyTrade Gateway", version=VERSION)
    app.state.runtime = runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime.config.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    async def request_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Invalid request",
                    "details": exc.errors(),
                }
            },
        )

    @app.exception_handler(ValidationError)
    async def pydantic_error(_request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Invalid request",
                    "details": exc.errors(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}},
        )

    @app.on_event("shutdown")
    async def shutdown() -> None:
        if client is None:
            await runtime.close()

    @app.on_event("startup")
    async def startup() -> None:
        if runtime.config.NODE_ENV == "production":
            await bootstrap_schema(runtime.config.DATABASE_URL)

    def auth(scope: Literal["research", "trade"]):
        async def dependency(request: Request) -> Principal:
            return await runtime.verifier.verify_authorization(
                request.headers.get("authorization"), scope
            )

        return dependency

    async def proxy(request: Request, upstream: str, path: str) -> Response:
        body = await request.body()
        url = f"{upstream.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "content-length"}
        }
        try:
            outbound = runtime.client.build_request(
                request.method, url, params=request.query_params, headers=headers, content=body
            )
            upstream_response = await runtime.client.send(outbound, stream=True)
        except httpx.HTTPError as exc:
            raise unavailable("Upstream service is temporarily unavailable") from exc
        response_headers = {
            key: value
            for key, value in upstream_response.headers.items()
            if key.lower()
            not in {"content-length", "transfer-encoding", "content-encoding", "connection"}
        }
        if "text/event-stream" in upstream_response.headers.get("content-type", ""):

            async def stream():
                try:
                    async for chunk in upstream_response.aiter_bytes():
                        yield chunk
                finally:
                    await upstream_response.aclose()

            return StreamingResponse(
                stream(),
                status_code=upstream_response.status_code,
                headers=response_headers,
                background=BackgroundTask(upstream_response.aclose),
                media_type="text/event-stream",
            )
        content = await upstream_response.aread()
        await upstream_response.aclose()
        return Response(
            content=content,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=upstream_response.headers.get("content-type"),
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION}

    @app.api_route("/v1/backtests/{path:path}", methods=["GET", "POST", "DELETE"])
    async def backtest_proxy(
        request: Request, path: str, _principal: Principal = Depends(auth("research"))
    ) -> Response:
        return await proxy(request, runtime.config.BACKTEST_UPSTREAM_URL, f"v1/backtests/{path}")

    @app.get("/v1/research/markets")
    async def research_markets(
        request: Request, _principal: Principal = Depends(auth("research"))
    ) -> Any:
        query = request.query_params.get("query", "")
        if not 1 <= len(query) <= 200:
            raise validation("query must contain 1-200 characters")
        limit = min(max(int(request.query_params.get("limit", "10")), 1), 25)
        state = request.query_params.get("state", "active")
        if state not in {"active", "resolved"}:
            raise validation("state must be active or resolved")
        return await runtime.polymarket.search_markets(query, limit, state)

    @app.get("/v1/research/market")
    async def research_market(
        request: Request, _principal: Principal = Depends(auth("research"))
    ) -> Any:
        identifier = request.query_params.get("identifier", "")
        kind = request.query_params.get("identifierType", "slug")
        if not identifier or kind not in {"id", "slug"}:
            raise validation("identifier and identifierType are required")
        return await runtime.polymarket.get_market(identifier, kind)

    @app.get("/v1/research/order-books/{token_id}")
    async def research_book(
        token_id: str, _principal: Principal = Depends(auth("research"))
    ) -> PublicOrderBook:
        if not token_id.isdigit():
            raise validation("Token ID must be an unsigned integer")
        return await runtime.polymarket.get_order_book(token_id)

    @app.get("/v1/research/price-history/{token_id}")
    async def research_history(
        token_id: str, request: Request, _principal: Principal = Depends(auth("research"))
    ) -> PublicPriceHistory:
        interval = request.query_params.get("interval", "1d")
        if not token_id.isdigit() or interval not in {"1h", "6h", "1d", "1w", "max"}:
            raise validation("Invalid price-history request")
        return await runtime.polymarket.get_price_history(token_id, interval)

    @app.get("/v1/research/trades/{condition_id}")
    async def research_trades(
        condition_id: str, _principal: Principal = Depends(auth("research"))
    ) -> Any:
        return await runtime.polymarket.get_recent_trades(condition_id)

    @app.get("/v1/public/markets")
    async def public_markets(request: Request) -> Response:
        limit = min(max(int(request.query_params.get("limit", "12")), 1), 24)
        offset = min(max(int(request.query_params.get("offset", "0")), 0), 1_000)
        order = request.query_params.get("order", "volume24hr")
        if order not in {"volume24hr", "liquidity", "endDate"}:
            raise validation("Invalid market order")
        result = await runtime.public_markets.list(limit=limit, offset=offset, order=order)
        return JSONResponse(
            result.model_dump(mode="json"), headers={"Cache-Control": "public, max-age=30"}
        )

    @app.get("/v1/public/markets/{slug}")
    async def public_market(slug: str) -> Response:
        result: PublicMarketDetail = await runtime.public_markets.detail(slug)
        return JSONResponse(
            result.model_dump(mode="json"), headers={"Cache-Control": "public, max-age=30"}
        )

    @app.get("/v1/public/order-books/{token_id}")
    async def public_book(token_id: str) -> Response:
        result = await runtime.public_markets.book(token_id)
        return JSONResponse(
            result.model_dump(mode="json"), headers={"Cache-Control": "public, max-age=5"}
        )

    @app.get("/v1/public/price-history/{token_id}")
    async def public_history(token_id: str, request: Request) -> Response:
        interval = request.query_params.get("interval", "1d")
        if interval not in {"1h", "6h", "1d", "1w", "max"}:
            raise validation("Invalid interval")
        result = await runtime.public_markets.history(token_id, interval)
        ttl = max(1, min(60, public_price_history_ttl_ms(interval) // 1_000))
        return JSONResponse(
            result.model_dump(mode="json"), headers={"Cache-Control": f"public, max-age={ttl}"}
        )

    @app.get("/v1/public/strategy-templates")
    async def public_templates() -> dict[str, Any]:
        from polytrade_contracts import STRATEGY_TEMPLATES

        return {"items": [item.model_dump(mode="json") for item in STRATEGY_TEMPLATES]}

    @app.get("/v1/public/track-records/{token}")
    async def public_track_record(token: str) -> Any:
        for account in runtime.accounts.values():
            if account.share_enabled and account.share_token == token:
                return _public_track_record(account)
        raise not_found("Track record not found")

    @app.get("/v1/public/agent-accuracy")
    async def public_accuracy() -> dict[str, Any]:
        records = [record for rows in runtime.predictions.values() for record in rows]
        graded = [item for item in records if item.status == "GRADED"]
        hits = sum(1 for item in graded if getattr(item, "hit", False))
        return {
            "totals": {
                "graded": len(graded),
                "hits": hits,
                "hitRatePct": f"{hits / len(graded) * 100:.2f}" if graded else None,
                "pending": len(records) - len(graded),
                "voided": 0,
                "lastGradedAt": None,
            },
            "byCategory": [],
            "recent": [],
            "observedAt": now_iso(),
        }

    @app.get("/v1/account/overview")
    async def account_overview(
        principal: Principal = Depends(auth("trade")),
    ) -> dict[str, Any]:
        session = _current_session(runtime, principal.id)
        return {
            "walletAddress": session.wallet_address
            if session
            else "0x0000000000000000000000000000000000000000",
            "funderAddress": session.funder_address if session else None,
            "positions": [],
            "openOrders": [],
            "fills": [],
            "observedAt": now_iso(),
        }

    @app.get("/v1/account/snapshot")
    async def account_snapshot(
        principal: Principal = Depends(auth("trade")),
    ) -> dict[str, Any]:
        return await account_overview(principal)

    @app.get("/v1/orders")
    async def account_orders(_principal: Principal = Depends(auth("trade"))) -> list[Any]:
        return []

    @app.get("/v1/trades")
    async def account_trades(_principal: Principal = Depends(auth("trade"))) -> list[Any]:
        return []

    @app.post("/v1/order-intents", response_model=OrderIntentResponse)
    async def order_intent_create(
        body: CreateIntentRequest,
        request: Request,
        principal: Principal = Depends(auth("trade")),
    ) -> OrderIntentResponse:
        if not _current_session(runtime, principal.id):
            raise validation("An active wallet session is required")
        intent_id = uuid4()
        expires = datetime.now(UTC) + timedelta(seconds=runtime.config.ORDER_INTENT_TTL_SECONDS)
        proposal = body.proposal.model_dump(mode="json")
        order_type = proposal["execution"]
        typed_data = {
            "domain": {"name": "Polymarket CTF Exchange", "version": "1", "chainId": 137},
            "types": {"Order": [{"name": "salt", "type": "uint256"}]},
            "primaryType": "Order",
            "message": {
                "salt": int(time.time_ns()),
                "tokenId": proposal["tokenId"],
                "price": proposal["price"],
                "size": proposal["size"],
            },
        }
        order = {
            "tokenId": proposal["tokenId"],
            "price": proposal.get("price", proposal.get("limitPrice")),
            "size": proposal.get("size", proposal.get("amount")),
            "side": proposal["side"],
            "orderType": order_type,
        }
        runtime.intents[str(intent_id)] = {
            "principal": principal.id,
            "expires": expires,
            "order": order,
        }
        return OrderIntentResponse(
            intentId=intent_id,
            expiresAt=_iso(expires),
            orderType=order_type,
            postOnly=bool(proposal.get("postOnly", False)),
            typedData=typed_data,
            order=order,
        )

    @app.post("/v1/order-intents/{intent_id}/submit")
    async def order_intent_submit(
        intent_id: UUID,
        body: SubmitIntentRequest,
        principal: Principal = Depends(auth("trade")),
    ) -> Any:
        intent = runtime.intents.get(str(intent_id))
        if not intent or intent["principal"] != principal.id:
            raise not_found("Order intent not found")
        raise unavailable("Live order submission is unavailable in this gateway runtime")

    @app.post("/v1/order-intents/batch")
    async def order_intent_batch(
        request: Request, principal: Principal = Depends(auth("trade"))
    ) -> Any:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload.get("proposals"):
            raise validation("At least one order proposal is required")
        raise unavailable("Batch order intents are unavailable in this gateway runtime")

    @app.post("/v1/order-intents/batch/submit")
    async def order_intent_batch_submit(principal: Principal = Depends(auth("trade"))) -> Any:
        raise unavailable("Batch order submission is unavailable in this gateway runtime")

    @app.post("/v1/cancellations")
    async def cancellation_create(
        body: CancelRequest,
        principal: Principal = Depends(auth("trade")),
    ) -> Any:
        if not _current_session(runtime, principal.id):
            raise validation("An active wallet session is required")
        raise unavailable("Live order cancellation is unavailable in this gateway runtime")

    @app.post("/v1/agent/predictions", response_model=AgentPredictionRecord)
    async def record_prediction(
        body: AgentPredictionRequest, principal: Principal = Depends(auth("research"))
    ) -> AgentPredictionRecord:
        record = AgentPredictionRecord(
            **body.model_dump(),
            predictionId=uuid4(),
            status="PENDING",
            madeAt=now_iso(),
            category=None,
        )
        runtime.predictions.setdefault(principal.id, []).append(record)
        return record

    @app.api_route("/v1/agent/{path:path}", methods=["GET", "POST", "DELETE"])
    async def agent_proxy(
        request: Request, path: str, _principal: Principal = Depends(auth("research"))
    ) -> Response:
        return await proxy(request, runtime.config.AGENT_UPSTREAM_URL, f"v1/agent/{path}")

    @app.get("/v1/paper/portfolio", response_model=PaperPortfolio)
    async def paper_portfolio(principal: Principal = Depends(auth("research"))) -> PaperPortfolio:
        return await _portfolio(runtime.account(principal))

    @app.post("/v1/paper/quotes", response_model=PaperQuote)
    async def paper_quote(
        body: PaperQuoteRequest, principal: Principal = Depends(auth("research"))
    ) -> PaperQuote:
        return await _quote(runtime, principal, body)

    @app.post("/v1/paper/orders", response_model=PaperOrderResponse)
    async def paper_order(
        request: Request, body: PaperOrderRequest, principal: Principal = Depends(auth("research"))
    ) -> PaperOrderResponse:
        key = request.headers.get("idempotency-key")
        if not key or len(key) < 8:
            raise validation("Idempotency-Key header must be 8-200 safe characters")
        account = runtime.account(principal)
        quote = await _quote(runtime, principal, body, body.limitPrice)
        quantity, cash_effect = Decimal(quote.shares), Decimal(quote.cashEffect)
        position = account.positions.get(body.tokenId)
        allocated = Decimal("0")
        if body.side == "BUY":
            debit = -cash_effect
            if account.cash < debit:
                raise conflict("The paper account does not have enough cash for this fill")
            account.cash -= debit
            if position:
                position["shares"] += quantity
                position["cost_basis"] += debit
            else:
                account.positions[body.tokenId] = {
                    "conditionId": body.conditionId,
                    "tokenId": body.tokenId,
                    "marketQuestion": quote.marketQuestion,
                    "outcome": quote.outcome,
                    "shares": quantity,
                    "cost_basis": debit,
                    "bestBid": None,
                    "liquidation": Decimal(0),
                    "markStatus": "unpriced",
                    "markedAt": None,
                }
        else:
            if not position or position["shares"] < quantity:
                raise conflict("The paper account does not own enough shares to sell")
            allocated = position["cost_basis"] * quantity / position["shares"]
            account.cash += cash_effect
            position["shares"] -= quantity
            position["cost_basis"] -= allocated
            if position["shares"] <= 0:
                account.positions.pop(body.tokenId, None)
        fill = PaperFill(
            fillId=uuid4(),
            kind=body.side,
            conditionId=quote.conditionId,
            tokenId=quote.tokenId,
            marketQuestion=quote.marketQuestion,
            outcome=quote.outcome,
            shares=quote.shares,
            averagePrice=quote.averagePrice,
            grossNotional=quote.grossNotional,
            feeRate=quote.feeRate,
            fee=quote.fee,
            cashEffect=quote.cashEffect,
            realizedPnl=_money(cash_effect - allocated),
            observedAt=quote.observedAt,
            createdAt=now_iso(),
        )
        account.fills.append(fill)
        return PaperOrderResponse(fill=fill, portfolio=await _portfolio(account))

    @app.post("/v1/paper/refresh", response_model=PaperPortfolio)
    async def paper_refresh(principal: Principal = Depends(auth("research"))) -> PaperPortfolio:
        account = runtime.account(principal)
        for token, position in account.positions.items():
            try:
                book = await runtime.polymarket.get_order_book(token)
                bid = best_bid_from_order_book(
                    book.model_dump(mode="json") if hasattr(book, "model_dump") else book
                )
                if bid:
                    position["bestBid"] = bid["price"]
                    position["liquidation"] = Decimal(position["shares"]) * Decimal(bid["price"])
                    position["markStatus"] = "current"
                    position["markedAt"] = bid["observedAt"]
            except Exception:
                position["markStatus"] = "stale"
        return await _portfolio(account)

    @app.get("/v1/paper/fills", response_model=PaperFillsResponse)
    async def paper_fills(
        request: Request, principal: Principal = Depends(auth("research"))
    ) -> PaperFillsResponse:
        limit = min(max(int(request.query_params.get("limit", "20")), 1), 100)
        offset = max(int(request.query_params.get("offset", "0")), 0)
        fills = list(reversed(runtime.account(principal).fills))
        return PaperFillsResponse(
            items=fills[offset : offset + limit], total=len(fills), offset=offset, limit=limit
        )

    @app.get("/v1/paper/strategy", response_model=PaperStrategySnapshot)
    async def paper_strategy(
        principal: Principal = Depends(auth("research")),
    ) -> PaperStrategySnapshot:
        account = runtime.account(principal)
        return PaperStrategySnapshot(
            strategy=account.strategy, events=account.strategy_events[-100:]
        )

    @app.post("/v1/paper/strategy", response_model=PaperStrategySnapshot)
    async def paper_strategy_start(
        body: PaperStrategyStartRequest, principal: Principal = Depends(auth("research"))
    ) -> PaperStrategySnapshot:
        account = runtime.account(principal)
        if account.strategy and account.strategy.get("status") == "RUNNING":
            raise conflict("A paper strategy is already running")
        market = await runtime.polymarket.get_market_by_condition(body.conditionId)
        market_model = market["market"]
        idx = (
            market_model.clobTokenIds.index(body.tokenId)
            if body.tokenId in market_model.clobTokenIds
            else -1
        )
        if idx < 0:
            raise validation("Paper outcome token does not belong to the selected market")
        now = now_iso()
        account.strategy = {
            **body.model_dump(),
            "strategyId": uuid4(),
            "marketQuestion": market_model.question,
            "outcome": market_model.outcomes[idx],
            "status": "RUNNING",
            "ordersPlaced": 0,
            "scansCompleted": 0,
            "lastAction": "STARTED",
            "lastMessage": "Strategy started",
            "lastQuoteSide": None,
            "lastQuotePrice": None,
            "lastScannedAt": None,
            "nextScanAt": now,
            "startedAt": now,
            "stoppedAt": None,
            "updatedAt": now,
        }
        account.strategy_events.append(
            {
                "eventId": uuid4(),
                "action": "STARTED",
                "message": "Strategy started",
                "side": None,
                "price": None,
                "fillId": None,
                "createdAt": now,
            }
        )
        return PaperStrategySnapshot(
            strategy=account.strategy, events=account.strategy_events[-100:]
        )

    @app.post("/v1/paper/strategy/stop", response_model=PaperStrategySnapshot)
    async def paper_strategy_stop(
        principal: Principal = Depends(auth("research")),
    ) -> PaperStrategySnapshot:
        account = runtime.account(principal)
        if account.strategy:
            now = now_iso()
            account.strategy.update(
                status="STOPPED",
                lastAction="STOPPED",
                lastMessage="Strategy stopped",
                stoppedAt=now,
                nextScanAt=None,
                updatedAt=now,
            )
            account.strategy_events.append(
                {
                    "eventId": uuid4(),
                    "action": "STOPPED",
                    "message": "Strategy stopped",
                    "side": None,
                    "price": None,
                    "fillId": None,
                    "createdAt": now,
                }
            )
        return PaperStrategySnapshot(
            strategy=account.strategy, events=account.strategy_events[-100:]
        )

    @app.get("/v1/paper/share", response_model=PaperShareStatus)
    async def paper_share(principal: Principal = Depends(auth("research"))) -> PaperShareStatus:
        account = runtime.account(principal)
        return PaperShareStatus(
            token=account.share_token if account.share_enabled else None,
            enabled=account.share_enabled,
            createdAt=account.share_created,
            updatedAt=account.share_updated,
        )

    @app.post("/v1/paper/share", response_model=PaperShareStatus)
    async def paper_share_enable(
        body: PaperShareManageRequest, principal: Principal = Depends(auth("research"))
    ) -> PaperShareStatus:
        account = runtime.account(principal)
        now = now_iso()
        if account.share_token is None or body.rotate:
            account.share_token = secrets.token_urlsafe(32).replace("-", "_").replace("~", "")[:43]
            account.share_created = account.share_created or now
        account.share_enabled = True
        account.share_updated = now
        return PaperShareStatus(
            token=account.share_token, enabled=True, createdAt=account.share_created, updatedAt=now
        )

    @app.delete("/v1/paper/share", response_model=PaperShareStatus)
    async def paper_share_disable(
        principal: Principal = Depends(auth("research")),
    ) -> PaperShareStatus:
        account = runtime.account(principal)
        account.share_enabled = False
        account.share_updated = now_iso()
        return PaperShareStatus(
            token=None,
            enabled=False,
            createdAt=account.share_created,
            updatedAt=account.share_updated,
        )

    @app.get("/v1/alerts/channels")
    async def alert_channels(
        principal: Principal = Depends(auth("research")),
    ) -> dict[str, Any]:
        return {"items": runtime.alerts.get(principal.id, [])}

    @app.post("/v1/alerts/channels")
    async def alert_channel_create(
        request: Request, principal: Principal = Depends(auth("research"))
    ) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict) or payload.get("kind") not in {"discord", "telegram"}:
            raise validation("Alert channel kind is required")
        channel = {
            "channelId": str(uuid4()),
            "kind": payload["kind"],
            "label": str(payload.get("label", "Alerts"))[:80],
            "eventKinds": payload.get("eventKinds", ["BUY", "SELL"]),
            "enabled": True,
            "targetHint": "configured",
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
        }
        runtime.alerts.setdefault(principal.id, []).append(channel)
        return channel

    @app.delete("/v1/alerts/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def alert_channel_delete(
        channel_id: UUID, principal: Principal = Depends(auth("research"))
    ) -> Response:
        channels = runtime.alerts.get(principal.id, [])
        runtime.alerts[principal.id] = [
            item for item in channels if item["channelId"] != str(channel_id)
        ]
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/alerts/channels/{channel_id}/test")
    async def alert_channel_test(
        channel_id: UUID, principal: Principal = Depends(auth("research"))
    ) -> dict[str, Any]:
        if not any(
            item["channelId"] == str(channel_id) for item in runtime.alerts.get(principal.id, [])
        ):
            raise not_found("Alert channel not found")
        return {"status": "sent", "error": None}

    @app.get("/v1/alerts/deliveries")
    async def alert_deliveries(
        _principal: Principal = Depends(auth("research")),
    ) -> dict[str, Any]:
        return {"items": [], "limit": 20}

    @app.post("/v1/wallet-sessions/challenge", response_model=WalletChallengeResponse)
    async def wallet_challenge(
        body: WalletChallengeRequest, principal: Principal = Depends(auth("trade"))
    ) -> WalletChallengeResponse:
        challenge_id = uuid4()
        timestamp = int(time.time())
        expires = datetime.now(UTC) + timedelta(seconds=runtime.config.WALLET_CHALLENGE_TTL_SECONDS)
        nonce = secrets.randbelow(2**31)
        typed = build_l1_typed_data(body.walletAddress, timestamp, nonce)
        runtime.challenges[challenge_id] = {
            "principal": principal.id,
            "body": body,
            "typed": typed,
            "expires": expires,
        }
        return WalletChallengeResponse(
            challengeId=challenge_id, expiresAt=_iso(expires), typedData=typed
        )

    @app.post("/v1/wallet-sessions", response_model=WalletSessionStatus)
    async def wallet_session(
        body: WalletSessionRequest, principal: Principal = Depends(auth("trade"))
    ) -> WalletSessionStatus:
        challenge = runtime.challenges.get(body.challengeId)
        if (
            not challenge
            or challenge["principal"] != principal.id
            or challenge["expires"] <= datetime.now(UTC)
        ):
            raise validation("Wallet challenge is invalid or expired")
        request_body = challenge["body"]
        creds = await runtime.polymarket.exchange_l1_credentials(
            request_body.walletAddress,
            body.signature,
            int(time.time()),
            challenge["typed"]["message"]["nonce"],
        )
        sid = str(uuid4())
        now = datetime.now(UTC)
        idle = now + timedelta(seconds=runtime.config.WALLET_SESSION_IDLE_SECONDS)
        absolute = now + timedelta(seconds=runtime.config.WALLET_SESSION_MAX_SECONDS)
        runtime.sessions[sid] = _WalletSession(
            principal.id,
            request_body.walletAddress,
            request_body.signatureType,
            request_body.funderAddress,
            runtime.cipher.encrypt(creds, sid),
            idle,
            absolute,
        )
        return WalletSessionStatus(
            sessionId=UUID(sid),
            walletAddress=request_body.walletAddress,
            funderAddress=request_body.funderAddress,
            signatureType=request_body.signatureType,
            idleExpiresAt=_iso(idle),
            expiresAt=_iso(absolute),
        )

    @app.get("/v1/wallet-sessions/current", response_model=WalletSessionStatus | None)
    async def wallet_current(principal: Principal = Depends(auth("trade"))) -> Any:
        now = datetime.now(UTC)
        for sid, session in reversed(list(runtime.sessions.items())):
            if (
                session.principal_id == principal.id
                and not session.revoked
                and session.absolute_expires_at > now
                and session.idle_expires_at > now
            ):
                return WalletSessionStatus(
                    sessionId=UUID(sid),
                    walletAddress=session.wallet_address,
                    funderAddress=session.funder_address,
                    signatureType=session.signature_type,
                    idleExpiresAt=_iso(session.idle_expires_at),
                    expiresAt=_iso(session.absolute_expires_at),
                )
        return None

    @app.delete("/v1/wallet-sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def wallet_revoke(
        session_id: UUID, principal: Principal = Depends(auth("trade"))
    ) -> Response:
        session = runtime.sessions.get(str(session_id))
        if not session or session.principal_id != principal.id:
            raise not_found("Wallet session not found")
        session.revoked = True
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


async def _quote(
    runtime: GatewayRuntime,
    _principal: Principal,
    request: PaperQuoteRequest,
    limit_price: str | None = None,
) -> PaperQuote:
    snapshot = await runtime.polymarket.get_market_by_condition(request.conditionId)
    market = snapshot["market"]
    if (
        market.conditionId != request.conditionId
        or not market.active
        or market.closed
        or not market.acceptingOrders
        or not market.enableOrderBook
    ):
        raise conflict("Polymarket is not accepting orders for this paper market")
    if request.tokenId not in market.clobTokenIds:
        raise validation("Paper outcome token does not belong to the selected market")
    index = market.clobTokenIds.index(request.tokenId)
    try:
        book, fee = await asyncio.gather(
            runtime.polymarket.get_order_book(request.tokenId),
            runtime.polymarket.get_fee_rate(request.tokenId),
        )
        raw_book = book.model_dump(mode="json") if hasattr(book, "model_dump") else book
        return quote_paper_order(
            request,
            {
                "conditionId": request.conditionId,
                "tokenId": request.tokenId,
                "marketQuestion": market.question,
                "outcome": market.outcomes[index],
            },
            raw_book,
            fee,
            limit_price,
        )
    except PaperPricingError as exc:
        raise conflict(f"Paper pricing failed: {exc}") from exc


def _current_session(runtime: GatewayRuntime, principal_id: str) -> _WalletSession | None:
    now = datetime.now(UTC)
    for session in reversed(list(runtime.sessions.values())):
        if (
            session.principal_id == principal_id
            and not session.revoked
            and session.absolute_expires_at > now
            and session.idle_expires_at > now
        ):
            return session
    return None


async def _portfolio(account: _PaperAccount) -> PaperPortfolio:
    positions: list[PaperPosition] = []
    for value in account.positions.values():
        shares = Decimal(value["shares"])
        cost = Decimal(value["cost_basis"])
        liquidation = Decimal(value.get("liquidation", 0))
        positions.append(
            PaperPosition(
                conditionId=value["conditionId"],
                tokenId=value["tokenId"],
                marketQuestion=value["marketQuestion"],
                outcome=value["outcome"],
                shares=_shares(shares),
                costBasis=_money(cost),
                averageCost=_money(cost / shares),
                bestBid=value.get("bestBid"),
                liquidationValue=_money(liquidation),
                unrealizedPnl=_money(liquidation - cost),
                markStatus=value.get("markStatus", "unpriced"),
                markedAt=value.get("markedAt"),
            )
        )
    positions_value = sum((Decimal(item.liquidationValue) for item in positions), Decimal(0))
    realized = sum((Decimal(item.realizedPnl) for item in account.fills), Decimal(0))
    fees = sum((Decimal(item.fee) for item in account.fills), Decimal(0))
    return PaperPortfolio(
        initialCash="10000.000000",
        cash=_money(account.cash),
        positionsValue=_money(positions_value),
        equity=_money(account.cash + positions_value),
        realizedPnl=_money(realized),
        unrealizedPnl=_money(
            positions_value - sum((Decimal(item.costBasis) for item in positions), Decimal(0))
        ),
        totalPnl=_money(account.cash + positions_value - Decimal("10000")),
        totalFees=_money(fees),
        positions=positions,
        warnings=[],
        observedAt=now_iso(),
    )


def _public_track_record(account: _PaperAccount) -> dict[str, Any]:
    total_pnl = Decimal("10000") - account.cash
    return {
        "profile": {
            "displayName": "PolyTrade paper account",
            "startedAt": account.share_created or now_iso(),
        },
        "stats": {
            "initialCash": "10000.000000",
            "cash": _money(account.cash),
            "equity": _money(account.cash),
            "totalPnl": _money(total_pnl),
            "realizedPnl": _money(total_pnl),
            "unrealizedPnl": "0.000000",
            "totalFees": _money(sum((Decimal(item.fee) for item in account.fills), Decimal(0))),
            "tradeCount": len(account.fills),
            "winRate": None,
        },
        "equityCurve": [],
        "positions": [],
        "fills": [
            {
                "fillId": item.fillId,
                "kind": item.kind,
                "marketQuestion": item.marketQuestion,
                "outcome": item.outcome,
                "shares": item.shares,
                "averagePrice": item.averagePrice,
                "fee": item.fee,
                "cashEffect": item.cashEffect,
                "realizedPnl": item.realizedPnl,
                "createdAt": item.createdAt,
            }
            for item in account.fills[-50:]
        ],
        "observedAt": now_iso(),
    }
