# PolyTrade — Claude Project Memory

## What This Project Is
**PolyTrade** is an AI-powered financial research and prediction-market trading agent.
Core: LangGraph ReAct loop → 14 tools (stock + Polymarket weather) → multiple UIs.
All services (Web Shell, AG-UI Chat, API, CLI) support the same direct commands + poly: commands.

## Running the Project

| Service | Command | Port |
|---------|---------|------|
| REST API (FastAPI) | `python api/main.py` | 4000 |
| Web Shell (FastHTML) | `python web_app.py` | 4002 |
| AG-UI Chat (FastHTML) | `python agui_app.py` | 4003 |
| CLI | `python polycode.py` | — |
| TUI | `python polycode_tui.py` | — |

> Always activate `.venv` first: `.venv\Scripts\activate` (Windows)

## Key Architecture

```
web_app.py              FastHTML web shell — dark CLI-style UI (port 4002)
agui_app.py             FastHTML 3-pane AG-UI chat — sidebar + chat + trace (port 4003)
api/main.py             FastAPI REST + SSE agent endpoint (port 4000)
agent/agent.py          LangGraph ReAct agent (core brain)
agent/tools/            14 tool implementations
model/llm.py            Multi-LLM: xAI/Grok, OpenAI, Anthropic, Google, Ollama
utils/agui/             AG-UI WebSocket chat framework (core, styles)
utils/portfolio_manager.py  Paper trades (JSON + DB)
utils/backtest_engine.py    Historical backtest engine
db/connection.py        asyncpg pool → polycode DB
db/repository.py        CRUD: runs, trades, pnl_snapshots
```

## Deployment

**Domain:** polytrade.chat
**Platform:** Coolify (Docker Compose)

| Service | Dockerfile | Port | Domain |
|---------|-----------|------|--------|
| REST API | Dockerfile.api | 4000 | api.polytrade.chat |
| Web Shell | Dockerfile.fasthtml | 4002 | polytrade.chat |
| AG-UI Chat | Dockerfile.agui | 4003 | chat.polytrade.chat |

See `DEPLOY.md` for full Coolify + IONOS setup instructions.

## Databases

| DB | URL | Purpose |
|----|-----|---------|
| finespresso_db | DATABASE_URL in .env | original / legacy |
| polycode | POLYCODE_DB_URL in .env | runs, trades, PnL (new) |

**Tables in polycode:**
- `runs` — every agent invocation (run_id UUID, query, model, status, tool_calls JSONB)
- `trades` — individual paper/real trades with PnL, period, city, signal
- `pnl_snapshots` — point-in-time PnL aggregates linked to runs

**Setup once:** `python scripts/setup_polycode_db.py`

## LLM Config (.env)
```
MODEL_PROVIDER=xai        # xai | openai | anthropic | google | ollama
MODEL=grok-4-fast-reasoning
```

## API Endpoints (port 4000)
```
GET  /health
GET  /agent/tools
POST /agent/run          → {query, model?, provider?, chat_history?}
POST /agent/stream       → SSE stream of AG-UI events
GET  /pnl/summary
GET  /pnl/trades?status=OPEN&limit=50
POST /pnl/trades
PUT  /pnl/trades/{id}
POST /pnl/snapshot
GET  /pnl/snapshots
GET  /runs
GET  /runs/{run_id}
POST /backtest           → {city, target_date?, lookback_days, v2_mode, is_prediction}
POST /polymarket/search  → {query?, city?}
POST /polymarket/simulate → {amount, market_id}
GET  /polymarket/portfolio
POST /weather
POST /predict
```

## SSE Event Types (AG-UI compatible)
`RUN_STARTED`, `CUSTOM` (thought/tool_error), `TOOL_CALL_START`, `TOOL_CALL_END`,
`TEXT_MESSAGE_START`, `TEXT_MESSAGE_CHUNK`, `TEXT_MESSAGE_END`,
`RUN_FINISHED`, `ERROR`, `STREAM_END`

## Agent Tools (14 total)

**Stock Research:**
`get_financials`, `get_ticker_details`, `get_ownership`,
`get_analyst_recommendations`, `get_earnings_estimates`, `get_relative_valuation`,
`get_price_graph`, `get_intraday_graph`, `get_news`, `web_search`

**Polymarket Weather:**
`scan_weather_opportunities`, `place_real_order`, `simulate_polymarket_trade`,
`search_weather_markets`

## Direct Commands (bypass LLM — same across all UIs)

**Stock commands:**
```
load AAPL    → get_ticker_details   (cached after first call)
fa NVDA      → get_financials
anr MSFT     → get_analyst_recommendations
ee TSLA      → get_earnings_estimates
rv GOOG      → get_relative_valuation
own AAPL     → get_ownership
gp AAPL      → get_price_graph
gip AAPL     → get_intraday_graph
news TSLA    → get_news
quote AAPL   → get_ticker_details
scan         → scan_weather_opportunities
```

**Poly: commands (Polymarket weather + trading):**
```
poly:weather London       → search weather markets by city
poly:backtest Seoul 7     → run backtest (city, days, optional date)
poly:backtestv2 Seoul 7   → cross-sectional YES/NO backtest
poly:predict London 2     → prediction (forward-looking)
poly:simbuy 50 <id>       → simulate trade
poly:buy 50 <id>          → real USDC buy order
poly:sell 50 <id>         → real sell order
poly:portfolio            → on-chain portfolio
poly:paperportfolio       → paper portfolio
```

## Trading Strategy Params (.env)
```
MIN_LIQUIDITY=50.0   MIN_EDGE=0.15   MAX_PRICE=0.10
MIN_CONFIDENCE=0.60  INITIAL_CAPITAL=197.0
CITIES=London,New York,Seoul
```

## Test Commands
```bash
# API health
curl http://localhost:4000/health

# Stream agent (SSE)
curl -N -X POST http://localhost:4000/agent/stream \
  -H "Content-Type: application/json" \
  -d '{"query":"What is AAPL stock price?"}'

# PnL summary
curl http://localhost:4000/pnl/summary

# Run DB setup
python scripts/setup_polycode_db.py

# Run tests
pytest tests/ -v

# Run full regression suite (all backend, tools, agent, DB, chat)
pytest tests/regression_suite.py -v --tb=short

# Run specific test groups
pytest tests/regression_suite.py -v -k "stock"      # stock tools only
pytest tests/regression_suite.py -v -k "weather"     # weather + Polymarket
pytest tests/regression_suite.py -v -k "agent"       # agent core
pytest tests/regression_suite.py -v -k "db"          # database ops
pytest tests/regression_suite.py -v -k "chat"        # chat persistence
pytest tests/regression_suite.py -v -k "backtest"    # backtest engine
pytest tests/regression_suite.py -v -k "strategy"    # trading strategy

# Docker Compose (all 3 services)
docker-compose up --build
```

## Common Patterns

**Adding a new tool:**
1. Create `agent/tools/my_tool.py`
2. Register in `agent/agent.py` in `Agent.create()`
3. Add `StructuredTool(name=..., description=..., func=...)`

**Checking DB from Python:**
```python
from db.repository import get_pnl_summary
import asyncio
print(asyncio.run(get_pnl_summary()))
```

## File Map
```
web_app.py              FastHTML web shell (dark CLI theme, port 4002)
agui_app.py             FastHTML 3-pane AG-UI chat (port 4003)
api/main.py             FastAPI REST + SSE server (port 4000)
agent/agent.py          Core LangGraph agent
agent/types.py          Event types (AgentEvent subclasses)
agent/prompts/          System + iteration + final-answer prompts
agent/tools/            All tool implementations
model/llm.py            LLM factory (multi-provider)
utils/agui/             AG-UI WebSocket chat framework
utils/agui/core.py      WebSocket handler, LangGraph streaming, command interceptor
utils/agui/styles.py    Light-theme chat CSS
components/cli.py       Rich CLI interface
components/command_processor.py  Bloomberg-style commands
utils/backtest_engine.py  Historical backtest
utils/portfolio_manager.py  Paper trade tracker
db/connection.py        asyncpg pool
db/repository.py        CRUD
scripts/setup_polycode_db.py  DB init (run once)
Dockerfile.api          Docker image for API
Dockerfile.fasthtml     Docker image for Web Shell
Dockerfile.agui         Docker image for AG-UI Chat
docker-compose.yaml     3-service compose for Coolify
DEPLOY.md               Deployment guide (Coolify + IONOS)
```
