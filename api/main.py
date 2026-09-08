"""
PolyTrade FastAPI Server
======================
Endpoints
---------
GET  /                  → health check (short)
GET  /health            → detailed health info
POST /v1/auth/token     → exchange user credentials for an API access token
POST /v1/threads        → create an owned conversation
GET  /v1/threads        → list owned conversations
POST /v1/threads/{id}/messages → run canonical chat as JSON or SSE
GET  /v1/runs/{run_id} → recover a persisted chat run
GET  /agent/tools       → deprecated authenticated tool listing
POST /agent/run         → deprecated stateless compatibility adapter
POST /agent/stream      → deprecated SSE compatibility adapter
GET  /pnl/summary       → aggregate PnL stats
GET  /pnl/trades        → list trades (filterable)
POST /pnl/trades        → insert / upsert a trade
PUT  /pnl/trades/{id}   → update trade status / exit
POST /pnl/snapshot      → persist a PnL snapshot
GET  /pnl/snapshots     → list snapshots
GET  /runs              → list agent runs
GET  /runs/{run_id}     → single run detail
POST /weather           → weather forecast (existing)
POST /predict           → market prediction (existing)
"""
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv()

from agent.tools.polymarket_tool import PolymarketClient
from agent.tools.visual_crossing_client import VisualCrossingClient
from api.routes.auth import router as auth_router
from api.routes.chat import router as chat_router
from api.routes.channels import router as channels_router
from api.security import (
    Principal,
    require_admin,
    require_chat_reader,
    require_chat_writer,
    require_run_reader,
    require_trade_reader,
    resolve_run_attribution,
    resolve_run_filters,
)
from chat.events import (
    MESSAGE_COMPLETED,
    MESSAGE_DELTA,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_STARTED,
    TOOL_COMPLETED,
    TOOL_STARTED,
)
from chat.agent_factory import get_chat_tools, get_tool_registry
from chat.service import get_chat_service
from utils.backtest_engine import BacktestEngine

app = FastAPI(
    title="PolyTrade API",
    description="Shared PolyTrade research chat and data API",
    version="3.0.0",
)

_default_origins = (
    "http://localhost:4002,http://localhost:4003,"
    "http://127.0.0.1:4002,http://127.0.0.1:4003"
)
_cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", _default_origins).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-User-Id",
        "X-User-Source",
    ],
    expose_headers=["Idempotency-Key", "X-Persistence-Mode"],
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(channels_router)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────

class AgentHistoryMessage(BaseModel):
    role: str = Field(max_length=20)
    content: str = Field(max_length=20_000)


class AgentQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    model: Optional[str] = Field(default=None, max_length=200)
    provider: Optional[str] = Field(default=None, max_length=50)
    chat_history: List[AgentHistoryMessage] = Field(
        default_factory=list,
        max_length=50,
    )
    run_id: Optional[str] = Field(default=None, max_length=100)


class ForecastRequest(BaseModel):
    city: str
    days: int = 7


class PredictRequest(BaseModel):
    city: str
    days: int = 7
    lookback_days: int = 7


class TradeRequest(BaseModel):
    trade_id: str
    run_id: Optional[str] = None
    market_id: str
    market_question: Optional[str] = None
    trade_side: Optional[str] = None
    amount: float
    entry_price: float
    shares: Optional[float] = None
    status: str = "OPEN"
    period: Optional[str] = None
    city: Optional[str] = None
    signal: Optional[str] = None
    edge_pct: Optional[float] = None
    confidence: Optional[float] = None
    trade_type: str = "paper"


class BacktestRequest(BaseModel):
    city: str
    target_date: Optional[str] = None  # defaults to today
    lookback_days: int = 7
    v2_mode: bool = False
    is_prediction: bool = False


class WeatherSearchRequest(BaseModel):
    query: str = "temperature"
    city: Optional[str] = None


class SimulateTradeRequest(BaseModel):
    amount: float
    market_id: str


class TradeUpdateRequest(BaseModel):
    status: str
    exit_price: Optional[float] = None
    payout: Optional[float] = None
    pnl: Optional[float] = None


def _sse(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"message": "PolyTrade API is running", "status": "active"}


@app.get("/health")
async def health():
    try:
        service = get_chat_service()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "database": "misconfigured",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": "3.0.0",
            },
        )

    database_status = "not_configured"
    if service.persistence_mode == "postgres":
        try:
            from db.connection import get_pool

            pool = await get_pool()
            await pool.fetchval("SELECT 1")
            schema_ready = await pool.fetchval(
                """
                SELECT
                    to_regclass('polycode.chat_conversations') IS NOT NULL
                    AND to_regclass('polycode.chat_messages') IS NOT NULL
                    AND to_regclass('polycode.chat_runs') IS NOT NULL
                    AND EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema='polycode'
                          AND table_name='chat_runs'
                          AND column_name='request_fingerprint'
                    )
                """
            )
            if not schema_ready:
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "unhealthy",
                        "database": "migration_required",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "version": "3.0.0",
                        "chat_persistence": service.persistence_mode,
                    },
                )
            database_status = "healthy"
        except Exception:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "database": "unavailable",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "version": "3.0.0",
                    "chat_persistence": service.persistence_mode,
                },
            )

    return {
        "status": "healthy",
        "model": os.getenv("MODEL", ""),
        "provider": os.getenv("MODEL_PROVIDER", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "3.0.0",
        "chat_persistence": service.persistence_mode,
        "database": database_status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/agent/tools")
async def list_tools(_principal: Principal = Depends(require_chat_reader)):
    """Deprecated research-tool listing. Real-order tools are never exposed."""
    tools = get_chat_tools()
    return {
        "tools": [{"name": t.name, "description": t.description} for t in tools],
        "count": len(tools),
        "deprecated": True,
        "replacement": "/v1/threads/{thread_id}/messages",
    }


@app.post("/agent/run")
async def run_agent(
    req: AgentQueryRequest,
    principal: Principal = Depends(require_chat_writer),
):
    """Deprecated stateless adapter over the canonical research chat backend."""
    service = get_chat_service()
    final_answer = ""
    run_id = None
    tool_calls: List[Dict[str, Any]] = []
    failure = None

    async for event in service.stream_stateless(
        content=req.query,
        history=[message.model_dump() for message in req.chat_history],
        user_id=principal.user_id,
    ):
        if event.event == RUN_STARTED:
            run_id = event.data.get("run_id")
        elif event.event == TOOL_STARTED:
            tool_calls.append(
                {
                    "tool": event.data.get("name"),
                    "args": event.data.get("args", {}),
                }
            )
        elif event.event == MESSAGE_COMPLETED:
            final_answer = event.data.get("message", {}).get("content", "")
        elif event.event == RUN_FAILED:
            failure = event.data

    if failure:
        status_code = 403 if failure.get("code") == "unsafe_command" else 502
        raise HTTPException(status_code=status_code, detail=failure)

    return JSONResponse(
        {
            "run_id": run_id,
            "query": req.query,
            "answer": final_answer,
            "iterations": 1,
            "tool_calls": tool_calls,
        },
        headers={
            "Deprecation": "true",
            "Link": '</v1/threads>; rel="successor-version"',
            "Warning": '299 - "Use the authenticated /v1/threads API"',
        },
    )


@app.post("/agent/stream")
async def stream_agent(
    req: AgentQueryRequest,
    principal: Principal = Depends(require_chat_writer),
):
    """Deprecated AG-UI-shaped SSE adapter over the canonical chat service."""
    async def event_gen():
        answer = ""
        message_started = False
        async for event in get_chat_service().stream_stateless(
            content=req.query,
            history=[message.model_dump() for message in req.chat_history],
            user_id=principal.user_id,
        ):
            if event.event == RUN_STARTED:
                yield _sse(
                    {
                        "type": "RUN_STARTED",
                        "run_id": event.data.get("run_id"),
                        "query": req.query,
                    }
                )
                yield _sse({"type": "TEXT_MESSAGE_START"})
                message_started = True
            elif event.event == MESSAGE_DELTA:
                yield _sse(
                    {
                        "type": "TEXT_MESSAGE_CHUNK",
                        "chunk": event.data.get("delta", ""),
                    }
                )
            elif event.event == TOOL_STARTED:
                yield _sse(
                    {
                        "type": "TOOL_CALL_START",
                        "tool_call_id": event.data.get("tool_call_id"),
                        "tool": event.data.get("name"),
                        "args": event.data.get("args", {}),
                    }
                )
            elif event.event == TOOL_COMPLETED:
                yield _sse(
                    {
                        "type": "TOOL_CALL_END",
                        "tool_call_id": event.data.get("tool_call_id"),
                        "tool": event.data.get("name"),
                    }
                )
            elif event.event == MESSAGE_COMPLETED:
                answer = event.data.get("message", {}).get("content", "")
            elif event.event == RUN_COMPLETED:
                if message_started:
                    yield _sse({"type": "TEXT_MESSAGE_END"})
                    message_started = False
                yield _sse(
                    {
                        "type": "RUN_FINISHED",
                        "run_id": event.data.get("run_id"),
                        "answer": answer,
                        "iterations": 1,
                        "tool_calls": event.data.get("tool_calls", []),
                    }
                )
            elif event.event == RUN_FAILED:
                if message_started:
                    yield _sse({"type": "TEXT_MESSAGE_END"})
                    message_started = False
                yield _sse(
                    {
                        "type": "ERROR",
                        "code": event.data.get("code"),
                        "error": event.data.get("message"),
                    }
                )
        yield _sse({"type": "STREAM_END"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache, no-transform",
            "Connection":       "keep-alive",
            "X-Accel-Buffering": "no",
            "Deprecation": "true",
            "Link": '</v1/threads>; rel="successor-version"',
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# PnL / Trades
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/pnl/summary")
async def get_pnl_summary_endpoint(
    principal: Principal = Depends(require_chat_reader),
):
    try:
        from db.repository import get_pnl_summary
        return await get_pnl_summary(user_id=principal.user_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.get("/pnl/trades")
async def get_trades_endpoint(
    status:     Optional[str] = Query(None),
    limit:      int           = Query(50, ge=1, le=500),
    offset:     int           = Query(0,  ge=0),
    run_id:     Optional[str] = Query(None),
    trade_type: Optional[str] = Query(None, description="paper | backtest | real"),
    principal: Principal = Depends(require_chat_reader),
):
    try:
        from db.repository import get_trades
        trades = await get_trades(
            status=status, limit=limit, offset=offset,
            run_id=run_id, trade_type=trade_type, user_id=principal.user_id,
        )
        return {"trades": trades, "count": len(trades)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.post("/pnl/trades")
async def create_trade_endpoint(
    trade: TradeRequest,
    principal: Principal = Depends(require_chat_writer),
):
    try:
        from db.repository import upsert_trade
        payload = trade.model_dump()
        payload["user_id"] = principal.user_id
        return await upsert_trade(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.put("/pnl/trades/{trade_id}")
async def update_trade_endpoint(
    trade_id: str,
    update: TradeUpdateRequest,
    principal: Principal = Depends(require_chat_writer),
):
    try:
        from db.repository import update_trade_status
        updated = await update_trade_status(
            trade_id=trade_id,
            status=update.status,
            exit_price=update.exit_price,
            payout=update.payout,
            pnl=update.pnl,
            user_id=principal.user_id,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Trade not found")
        return {"message": "Trade updated", "trade_id": trade_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.post("/pnl/snapshot")
async def save_snapshot_endpoint(
    run_id: Optional[str] = Query(None),
    _principal: Principal = Depends(require_admin),
):
    try:
        from db.repository import save_pnl_snapshot
        return await save_pnl_snapshot(run_id=run_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.get("/pnl/snapshots")
async def get_snapshots_endpoint(
    limit: int = Query(100, ge=1, le=1000),
    _principal: Principal = Depends(require_admin),
):
    try:
        from db.repository import get_pnl_snapshots
        snapshots = await get_pnl_snapshots(limit=limit)
        return {"snapshots": snapshots, "count": len(snapshots)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Runs
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/runs")
async def list_runs(
    limit: int = Query(20, ge=1, le=200),
    principal_id: Optional[str] = Query(None, max_length=100),
    source: Optional[str] = Query(None, max_length=20),
    principal: Principal = Depends(require_run_reader),
):
    filters = resolve_run_filters(principal, principal_id, source)
    try:
        from db.repository import get_runs
        return {
            "runs": await get_runs(
                limit=limit,
                principal_id=filters.principal_id,
                source=filters.source,
            )
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.get("/runs/{run_id}")
async def get_run_detail(
    run_id: str,
    principal_id: Optional[str] = Query(None, max_length=100),
    source: Optional[str] = Query(None, max_length=20),
    principal: Principal = Depends(require_run_reader),
):
    filters = resolve_run_filters(principal, principal_id, source)
    try:
        from db.repository import get_run
        run = await get_run(
            run_id,
            principal_id=filters.principal_id,
            source=filters.source,
        )
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Backtest — run + save to DB
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/backtest")
async def run_backtest_endpoint(
    req: BacktestRequest,
    principal: Principal = Depends(require_chat_writer),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_user_source: Optional[str] = Header(None, alias="X-User-Source"),
):
    """Run a weather backtest/prediction and save trades to DB."""
    from datetime import timedelta

    attribution = resolve_run_attribution(principal, x_user_id, x_user_source)

    pm_client = PolymarketClient()
    vc_client = VisualCrossingClient()

    # Include Tomorrow.io client if available
    tm_client = None
    tomorrow_key = os.getenv("TOMORROWIO_API_KEY")
    if tomorrow_key:
        from agent.tools.weather_tool import WeatherClient
        tm_client = WeatherClient(api_key=tomorrow_key)

    engine = BacktestEngine(pm_client, vc_client, tomorrow_client=tm_client)

    target = req.target_date or datetime.now().strftime("%Y-%m-%d")
    if req.is_prediction:
        target = (datetime.now() + timedelta(days=req.lookback_days)).strftime("%Y-%m-%d")

    # Create a DB run record
    mode = "prediction" if req.is_prediction else ("backtestv2" if req.v2_mode else "backtest")
    run_id = None
    try:
        from db.repository import create_run
        run_id = await create_run(
            f"{mode}:{req.city}:{target}:lb{req.lookback_days}",
            "backtest_engine",
            "local",
            attribution.principal_id,
            attribution.source,
        )
    except Exception:
        pass

    try:
        result = await engine.run_backtest(
            city=req.city,
            target_date=target,
            lookback_days=req.lookback_days,
            v2_mode=req.v2_mode,
            is_prediction=req.is_prediction,
        )

        # Save trades to DB
        saved = 0
        try:
            from db.repository import save_backtest_trades, finish_run, save_pnl_snapshot
            saved = await save_backtest_trades(
                run_id, result, req.city, user_id=attribution.principal_id
            )
            if run_id:
                await finish_run(run_id, 1, [{"tool": "backtest_engine"}])
                await save_pnl_snapshot(run_id=run_id)
        except Exception:
            pass

        result["run_id"] = run_id
        result["trades_saved_to_db"] = saved
        return result
    except Exception as exc:
        try:
            from db.repository import finish_run
            if run_id:
                await finish_run(run_id, 0, [], error=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await pm_client.close()
        await vc_client.close()
        if tm_client:
            await tm_client.close()


# ─────────────────────────────────────────────────────────────────────────────
# Polymarket direct endpoints (mirror CLI poly: commands)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/polymarket/search")
async def search_weather_markets_endpoint(
    req: WeatherSearchRequest,
    _principal: Principal = Depends(require_chat_writer),
):
    """Search Polymarket weather markets (same as poly:weather CLI command)."""
    agent = get_tool_registry().command_agent
    tool_name = "search_weather_markets"
    if tool_name not in agent.tool_map:
        raise HTTPException(status_code=400, detail="search_weather_markets tool not available. Check TOMORROWIO_API_KEY.")
    import inspect
    tool = agent.tool_map[tool_name]
    kwargs = {"query": req.query}
    if req.city:
        kwargs["city"] = req.city
    try:
        if inspect.iscoroutinefunction(tool.func):
            result = await tool.func(**kwargs)
        else:
            import asyncio
            result = await asyncio.to_thread(tool.func, **kwargs)
        return {"city": req.city or "all", "markets": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/polymarket/simulate")
async def simulate_trade_endpoint(
    req: SimulateTradeRequest,
    _principal: Principal = Depends(require_chat_writer),
):
    """Simulate a Polymarket trade (same as poly:simbuy CLI command)."""
    agent = get_tool_registry().command_agent
    tool_name = "simulate_polymarket_trade"
    if tool_name not in agent.tool_map:
        raise HTTPException(status_code=400, detail="simulate_polymarket_trade tool not available.")
    import inspect
    tool = agent.tool_map[tool_name]
    try:
        if inspect.iscoroutinefunction(tool.func):
            result = await tool.func(amount=str(req.amount), market_id=req.market_id)
        else:
            import asyncio
            result = await asyncio.to_thread(tool.func, amount=str(req.amount), market_id=req.market_id)
        return {"result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/polymarket/portfolio")
async def get_portfolio_endpoint(
    _principal: Principal = Depends(require_trade_reader),
):
    """Get on-chain Polymarket portfolio (same as poly:portfolio CLI command)."""
    try:
        from agent.tools.polymarket_tool import get_polymarket_client
        pm = await get_polymarket_client()
        data = await pm.get_portfolio()
        return data
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Weather / Predict (existing endpoints kept)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/weather")
async def get_weather(
    request: ForecastRequest,
    _principal: Principal = Depends(require_chat_writer),
):
    vc_client = VisualCrossingClient()
    try:
        forecast = await vc_client.get_forecast(request.city)
        return {"city": request.city, "forecast": forecast}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await vc_client.close()


@app.post("/predict")
async def run_prediction(
    request: PredictRequest,
    _principal: Principal = Depends(require_chat_writer),
):
    pm_client = PolymarketClient()
    vc_client = VisualCrossingClient()
    tm_client = None
    tomorrow_key = os.getenv("TOMORROWIO_API_KEY")
    if tomorrow_key:
        from agent.tools.weather_tool import WeatherClient
        tm_client = WeatherClient(api_key=tomorrow_key)
    engine = BacktestEngine(pm_client, vc_client, tomorrow_client=tm_client)
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        result = await engine.run_backtest(
            city=request.city,
            target_date=today_str,
            lookback_days=request.days,
            is_prediction=True,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await pm_client.close()
        await vc_client.close()
        if tm_client:
            await tm_client.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4000)
