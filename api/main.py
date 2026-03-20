"""
PolyTrade FastAPI Server
======================
Endpoints
---------
GET  /                  → health check (short)
GET  /health            → detailed health info
GET  /agent/tools       → list all agent tools
POST /agent/run         → run agent, block until done, return JSON
POST /agent/stream      → run agent, stream AG-UI compatible SSE events
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
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.agent import Agent
from agent.types import (
    AgentConfig,
    AnswerChunkEvent,
    AnswerStartEvent,
    DoneEvent,
    LogEvent,
    ToolEndEvent,
    ToolErrorEvent,
    ToolStartEvent,
)
from agent.tools.polymarket_tool import PolymarketClient
from agent.tools.visual_crossing_client import VisualCrossingClient
from utils.backtest_engine import BacktestEngine

app = FastAPI(
    title="PolyTrade API",
    description="Financial Research Agent — remote API",
    version="2.0.0",
)

# Allow all origins so the FastHTML chat UI (different port) can call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────

class AgentQueryRequest(BaseModel):
    query: str
    model: Optional[str] = None
    provider: Optional[str] = None
    chat_history: Optional[List[Dict[str, str]]] = []
    run_id: Optional[str] = None          # attach to an existing DB run


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


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build agent config
# ─────────────────────────────────────────────────────────────────────────────

def _make_config(req: AgentQueryRequest) -> AgentConfig:
    return AgentConfig(
        model=req.model or os.getenv("MODEL"),
        model_provider=req.provider or os.getenv("MODEL_PROVIDER"),
    )


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
    return {
        "status": "healthy",
        "model": os.getenv("MODEL", ""),
        "provider": os.getenv("MODEL_PROVIDER", ""),
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/agent/tools")
async def list_tools():
    """List all tools available to the agent."""
    agent = Agent.create(AgentConfig())
    return {
        "tools": [{"name": t.name, "description": t.description} for t in agent.tools],
        "count": len(agent.tools),
    }


@app.post("/agent/run")
async def run_agent(req: AgentQueryRequest):
    """
    Run the agent synchronously and return the full answer.
    Optionally records the run in the DB if POLYCODE_DB_URL is set.
    """
    config = _make_config(req)
    agent  = Agent.create(config)

    # Optionally create a DB run record
    run_id = req.run_id
    try:
        from db.repository import create_run, finish_run
        run_id = run_id or await create_run(req.query, config.model, config.model_provider)
    except Exception:
        pass  # DB optional

    final_answer = ""
    iterations   = 0
    tool_calls: List[Dict] = []

    try:
        async for event in agent.run(req.query, req.chat_history):
            if isinstance(event, DoneEvent):
                final_answer = event.answer
                iterations   = event.iterations
                tool_calls   = event.tool_calls

        try:
            from db.repository import finish_run
            await finish_run(run_id, iterations, tool_calls)
        except Exception:
            pass
    except Exception as exc:
        try:
            from db.repository import finish_run
            await finish_run(run_id, iterations, tool_calls, error=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "run_id":     run_id,
        "query":      req.query,
        "answer":     final_answer,
        "iterations": iterations,
        "tool_calls": tool_calls,
    }


@app.post("/agent/stream")
async def stream_agent(req: AgentQueryRequest):
    """
    Run the agent and stream AG-UI compatible Server-Sent Events.

    AG-UI event types emitted:
      RUN_STARTED        — agent has begun
      TEXT_MESSAGE_START — assistant message starts
      TEXT_MESSAGE_CHUNK — incremental token chunk
      TEXT_MESSAGE_END   — assistant message complete
      TOOL_CALL_START    — tool execution begins
      TOOL_CALL_END      — tool execution ends
      CUSTOM             — thought / log events
      RUN_FINISHED       — agent done (includes full answer)
      ERROR              — unhandled exception
    """
    config = _make_config(req)

    # Create DB run record (non-fatal if DB unavailable)
    run_id = req.run_id
    try:
        from db.repository import create_run
        run_id = run_id or await create_run(req.query, config.model, config.model_provider)
    except Exception:
        pass

    async def event_gen():
        agent      = Agent.create(config)
        iterations = 0
        tool_calls: List[Dict] = []

        yield _sse({"type": "RUN_STARTED", "run_id": run_id, "query": req.query})

        try:
            async for event in agent.run(req.query, req.chat_history):
                if isinstance(event, LogEvent):
                    yield _sse({
                        "type":    "CUSTOM",
                        "subtype": f"agent_{event.level}",
                        "message": event.message,
                    })

                elif isinstance(event, ToolStartEvent):
                    tool_calls.append({"tool": event.tool, "args": event.args})
                    yield _sse({
                        "type": "TOOL_CALL_START",
                        "tool": event.tool,
                        "args": event.args,
                    })

                elif isinstance(event, ToolEndEvent):
                    yield _sse({
                        "type":   "TOOL_CALL_END",
                        "tool":   event.tool,
                        "result": event.result,
                    })
                    # Save trade results to DB
                    try:
                        result = {}
                        if isinstance(event.result, str) and event.result.startswith("{"):
                            result = json.loads(event.result)
                        elif isinstance(event.result, dict):
                            result = event.result
                        if event.tool == "simulate_polymarket_trade" and result:
                            from db.repository import upsert_trade
                            price = float(result.get("vwap", 0))
                            amount = float(result.get("amount_executed", 0))
                            if price > 0 and amount > 0:
                                await upsert_trade({
                                    "trade_id": f"P-{result.get('market_id','')[:20]}-{int(datetime.now().timestamp())}",
                                    "run_id": run_id, "market_id": str(result.get("market_id", "")),
                                    "trade_side": "BUY", "amount": amount, "entry_price": price,
                                    "shares": float(result.get("shares_bought", 0)),
                                    "status": "OPEN", "trade_type": "paper",
                                })
                        elif event.tool == "place_real_order" and result.get("status") == "success":
                            from db.repository import upsert_trade
                            await upsert_trade({
                                "trade_id": f"R-{int(datetime.now().timestamp())}",
                                "run_id": run_id, "market_id": str(result.get("token_id", "")),
                                "trade_side": result.get("side", "BUY"),
                                "amount": float(result.get("amount", 0)),
                                "entry_price": 0, "shares": 0,
                                "status": "OPEN", "trade_type": "real",
                            })
                    except Exception:
                        pass

                elif isinstance(event, ToolErrorEvent):
                    yield _sse({
                        "type":  "CUSTOM",
                        "subtype": "tool_error",
                        "tool":  event.tool,
                        "error": event.error,
                    })

                elif isinstance(event, AnswerStartEvent):
                    yield _sse({"type": "TEXT_MESSAGE_START"})

                elif isinstance(event, AnswerChunkEvent):
                    yield _sse({"type": "TEXT_MESSAGE_CHUNK", "chunk": event.chunk})

                elif isinstance(event, DoneEvent):
                    iterations = event.iterations
                    yield _sse({"type": "TEXT_MESSAGE_END"})
                    yield _sse({
                        "type":       "RUN_FINISHED",
                        "run_id":     run_id,
                        "answer":     event.answer,
                        "iterations": event.iterations,
                        "tool_calls": event.tool_calls,
                    })
                    # Persist to DB
                    try:
                        from db.repository import finish_run, save_pnl_snapshot
                        await finish_run(run_id, event.iterations, event.tool_calls)
                        await save_pnl_snapshot(run_id=run_id)
                    except Exception:
                        pass

        except Exception as exc:
            yield _sse({"type": "ERROR", "error": str(exc)})
            try:
                from db.repository import finish_run
                await finish_run(run_id, iterations, tool_calls, error=str(exc))
            except Exception:
                pass
        finally:
            yield _sse({"type": "STREAM_END"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "Connection":       "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# PnL / Trades
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/pnl/summary")
async def get_pnl_summary_endpoint():
    try:
        from db.repository import get_pnl_summary
        return await get_pnl_summary()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.get("/pnl/trades")
async def get_trades_endpoint(
    status:     Optional[str] = Query(None),
    limit:      int           = Query(50, ge=1, le=500),
    offset:     int           = Query(0,  ge=0),
    run_id:     Optional[str] = Query(None),
    trade_type: Optional[str] = Query(None, description="paper | backtest | real"),
):
    try:
        from db.repository import get_trades
        trades = await get_trades(
            status=status, limit=limit, offset=offset,
            run_id=run_id, trade_type=trade_type,
        )
        return {"trades": trades, "count": len(trades)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.post("/pnl/trades")
async def create_trade_endpoint(trade: TradeRequest):
    try:
        from db.repository import upsert_trade
        return await upsert_trade(trade.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.put("/pnl/trades/{trade_id}")
async def update_trade_endpoint(trade_id: str, update: TradeUpdateRequest):
    try:
        from db.repository import update_trade_status
        await update_trade_status(
            trade_id=trade_id,
            status=update.status,
            exit_price=update.exit_price,
            payout=update.payout,
            pnl=update.pnl,
        )
        return {"message": "Trade updated", "trade_id": trade_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.post("/pnl/snapshot")
async def save_snapshot_endpoint(run_id: Optional[str] = Query(None)):
    try:
        from db.repository import save_pnl_snapshot
        return await save_pnl_snapshot(run_id=run_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.get("/pnl/snapshots")
async def get_snapshots_endpoint(limit: int = Query(100, ge=1, le=1000)):
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
async def list_runs(limit: int = Query(20, ge=1, le=200)):
    try:
        from db.repository import get_runs
        return {"runs": await get_runs(limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")


@app.get("/runs/{run_id}")
async def get_run_detail(run_id: str):
    try:
        from db.repository import get_run
        run = await get_run(run_id)
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
async def run_backtest_endpoint(req: BacktestRequest):
    """Run a weather backtest/prediction and save trades to DB."""
    from datetime import timedelta

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
            "backtest_engine", "local",
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
            saved = await save_backtest_trades(run_id, result, req.city)
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
async def search_weather_markets_endpoint(req: WeatherSearchRequest):
    """Search Polymarket weather markets (same as poly:weather CLI command)."""
    config = AgentConfig()
    agent = Agent.create(config)
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
async def simulate_trade_endpoint(req: SimulateTradeRequest):
    """Simulate a Polymarket trade (same as poly:simbuy CLI command)."""
    config = AgentConfig()
    agent = Agent.create(config)
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
async def get_portfolio_endpoint():
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
async def get_weather(request: ForecastRequest):
    vc_client = VisualCrossingClient()
    try:
        forecast = await vc_client.get_forecast(request.city)
        return {"city": request.city, "forecast": forecast}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await vc_client.close()


@app.post("/predict")
async def run_prediction(request: PredictRequest):
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
