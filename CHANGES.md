# PolyCode — Changes & New Features

## Overview of Changes Added

Five major features were added on top of the existing PolyCode codebase:

1. **API Remote Calling** — full agent accessible via REST + SSE
2. **AG-UI Style Chat UI** — FastHTML web chat with direct commands + caching
3. **PnL Tracking in DB** — PostgreSQL `polycode` database with proper schema
4. **FinCode → PolyCode Rebrand** — full rename across all files
5. **Agent Optimization** — removed RAG, added caching, direct command fast path

---

## 1. API Remote Calling (`api/main.py`)

### New Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Server health + model info |
| `GET` | `/agent/tools` | List all 16+ agent tools |
| `POST` | `/agent/run` | Run agent, block until done, return JSON |
| `POST` | `/agent/stream` | Run agent, stream AG-UI SSE events |
| `GET` | `/pnl/summary` | Aggregate PnL stats |
| `GET` | `/pnl/trades` | List trades (filter by status, run_id) |
| `POST` | `/pnl/trades` | Insert/upsert a trade |
| `PUT` | `/pnl/trades/{id}` | Update trade status/exit |
| `POST` | `/pnl/snapshot` | Persist a PnL snapshot |
| `GET` | `/pnl/snapshots` | List historical snapshots |
| `GET` | `/runs` | List agent runs |
| `GET` | `/runs/{run_id}` | Single run detail |

### SSE Event Types (`POST /agent/stream`)

The streaming endpoint emits AG-UI compatible events:

```json
{"type": "RUN_STARTED",        "run_id": "...", "query": "..."}
{"type": "CUSTOM",             "subtype": "agent_thought", "message": "..."}
{"type": "TOOL_CALL_START",    "tool": "get_financials", "args": {...}}
{"type": "TOOL_CALL_END",      "tool": "get_financials", "result": "..."}
{"type": "TEXT_MESSAGE_START"}
{"type": "TEXT_MESSAGE_CHUNK", "chunk": "word "}
{"type": "TEXT_MESSAGE_END"}
{"type": "RUN_FINISHED",       "answer": "...", "iterations": 3}
{"type": "STREAM_END"}
```

### Usage Example

```bash
# Non-streaming
curl -X POST http://localhost:8000/agent/run \
  -H "Content-Type: application/json" \
  -d '{"query": "What is AAPL stock price?", "model": "grok-3", "provider": "xai"}'

# Streaming SSE
curl -N -X POST http://localhost:8000/agent/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "Scan Polymarket weather opportunities"}'
```

---

## 2. AG-UI Style Chat UI (`app.py`)

### New File: `app.py`

FastHTML web application with:
- **Dark theme** (slate-900 palette, same as rl-agent-swarm)
- **WebSocket streaming** — real-time agent thoughts, tool calls, and answer
- **Tool call visualization** — shows 🔧 tool → ✓ done progression
- **Markdown rendering** — full markdown in agent answers
- **Quick-ask chips** — one-click suggestion buttons
- **Trades page** (`/trades`) — view all DB trades with PnL summary cards
- **Auto-scroll** — chat auto-scrolls as content arrives
- **No login required** — single-user, direct access

### Run

```bash
python app.py        # → http://localhost:5001
```

### New Static Files

| File | Purpose |
|------|---------|
| `static/css/styles.css` | Dark theme, bubble styles, animations |
| `static/js/app.js` | Auto-scroll, textarea resize, copy-code |

---

## 3. PnL Tracking in DB (`db/`)

### New Database: `polycode`

Run once to create:
```bash
python scripts/setup_polycode_db.py
```

### Schema

#### `runs` table
Tracks every agent invocation.

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | UUID PK | Unique run identifier |
| `query` | TEXT | User query |
| `model` | VARCHAR | LLM model used |
| `provider` | VARCHAR | LLM provider |
| `status` | VARCHAR | `running` / `completed` / `failed` |
| `iterations` | INT | Agent loop iterations |
| `tool_calls` | JSONB | List of tools used |
| `started_at` | TIMESTAMPTZ | Run start time |
| `finished_at` | TIMESTAMPTZ | Run end time |
| `error_message` | TEXT | Error if failed |

#### `trades` table
One row per individual trade (paper or real).

| Column | Type | Description |
|--------|------|-------------|
| `trade_id` | VARCHAR PK | Trade identifier (`T-{timestamp}`) |
| `run_id` | UUID FK | Links to `runs` |
| `market_id` | VARCHAR | Polymarket market ID |
| `market_question` | TEXT | Market description |
| `trade_side` | VARCHAR | `BUY` / `SELL` |
| `amount` | DECIMAL | USDC amount |
| `entry_price` | DECIMAL | Entry token price |
| `shares` | DECIMAL | Number of shares |
| `status` | VARCHAR | `OPEN` / `SOLD` / `RESOLVED` / `SKIPPED` |
| `exit_price` | DECIMAL | Exit price when closed |
| `payout` | DECIMAL | Total payout received |
| `pnl` | DECIMAL | `payout - amount` |
| `period` | DATE | Weather market date |
| `city` | VARCHAR | City for weather trades |
| `signal` | VARCHAR | `BUY` / `SELL` / `HOLD` / `SKIP` |
| `edge_pct` | DECIMAL | Edge percentage |
| `confidence` | DECIMAL | Confidence score |
| `created_at` | TIMESTAMPTZ | Trade entry time |
| `updated_at` | TIMESTAMPTZ | Last update time |

#### `pnl_snapshots` table
Point-in-time PnL aggregates (saved after each agent run).

| Column | Type | Description |
|--------|------|-------------|
| `snapshot_id` | SERIAL PK | Auto-increment ID |
| `run_id` | UUID FK | Links to `runs` |
| `snapshot_time` | TIMESTAMPTZ | When snapshot was taken |
| `total_invested` | DECIMAL | Total capital deployed |
| `total_payout` | DECIMAL | Total payout received |
| `realized_pnl` | DECIMAL | Total realized profit/loss |
| `open_trades` | INT | Currently open positions |
| `closed_trades` | INT | Resolved positions |
| `win_count` | INT | Profitable trades |
| `loss_count` | INT | Loss-making trades |
| `win_rate` | DECIMAL | Win % |
| `total_trades` | INT | Total trade count |
| `roi_pct` | DECIMAL | Return on investment % |

### New Files

| File | Purpose |
|------|---------|
| `db/__init__.py` | Package marker |
| `db/connection.py` | asyncpg connection pool |
| `db/repository.py` | CRUD for runs/trades/pnl_snapshots |
| `scripts/setup_polycode_db.py` | One-time DB + table creation |

### `.env` addition

```
POLYCODE_DB_URL=postgresql://finespresso:mlfpass2026@72.62.114.124:5432/polycode
```

---

## New Dependencies

Added to `requirements.txt`:

| Package | Purpose |
|---------|---------|
| `python-fasthtml` | FastHTML chat UI framework |
| `asyncpg` | Async PostgreSQL driver |
| `psycopg2-binary` | Sync PostgreSQL (DB setup script) |
| `fastapi` | REST API framework (moved from api/requirements.txt) |
| `uvicorn` | ASGI server |

---

## Architecture: How the 3 Services Work Together

### The 3 Entry Points

```
┌─────────────────────────────────────────────────────────────────┐
│                        SHARED CORE                              │
│                                                                 │
│   agent/agent.py ──→ Agent.create() ──→ LangGraph ReAct loop   │
│   agent/tools/    ──→ 16 tools (financials, polymarket, etc.)   │
│   model/llm.py    ──→ LLM factory (xAI, OpenAI, Anthropic)     │
│   db/repository.py──→ CRUD (runs, trades, pnl_snapshots)       │
│                                                                 │
└───────────┬──────────────────┬──────────────────┬───────────────┘
            │                  │                  │
     ┌──────▼──────┐   ┌──────▼──────┐   ┌──────▼──────┐
     │  Chat UI    │   │  REST API   │   │    CLI      │
     │  app.py     │   │ api/main.py │   │ fincode.py  │
     │  port 5001  │   │  port 8000  │   │  terminal   │
     │  WebSocket  │   │  HTTP/SSE   │   │  stdin/out  │
     └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
            │                  │                  │
            └──────────────────┼──────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   PostgreSQL DB     │
                    │   polycode schema   │
                    │   (runs, trades,    │
                    │    pnl_snapshots)   │
                    └─────────────────────┘
```

### How Each Service Works

| | Chat UI (`app.py`) | REST API (`api/main.py`) | CLI (`fincode.py`) |
|---|---|---|---|
| **Port** | 5001 | 8000 | N/A (terminal) |
| **Protocol** | WebSocket (HTMX) | HTTP + SSE | stdin/stdout |
| **Runs agent** | Directly in-process | Directly in-process | Directly in-process |
| **Streaming** | WS → HTML fragments | SSE → AG-UI JSON events | Print to terminal |
| **Saves to DB** | Yes (runs, trades, PnL) | Yes (runs, trades, PnL) | Yes (runs, trades, PnL) |
| **Target user** | Browser user | External clients/frontends | Developer/terminal user |

### Key Point: They Are Independent

- **Chat UI does NOT call the API.** Both import `Agent.create()` directly.
- You can run any one, two, or all three at the same time.
- They share the same agent brain, same tools, same DB — but each is a standalone process.

### How They All Save to the Same DB

All three services use `db/repository.py` which connects to the `polycode` schema:

```
User sends query
       │
       ▼
1. create_run()         →  INSERT into `runs` (status='running')
       │
       ▼
2. Agent runs, calls tools
       │
       ├── simulate_polymarket_trade  →  upsert_trade(trade_type='paper')
       ├── place_real_order           →  upsert_trade(trade_type='real')
       └── run_backtest               →  save_backtest_trades(trade_type='backtest')
       │
       ▼
3. finish_run()         →  UPDATE `runs` (status='completed', iterations, tool_calls)
       │
       ▼
4. save_pnl_snapshot()  →  INSERT into `pnl_snapshots` (aggregated stats)
```

**Trade types saved:**

| Tool | `trade_type` | Where saved |
|------|-------------|-------------|
| `simulate_polymarket_trade` | `paper` | Chat UI + API |
| `place_real_order` | `real` | Chat UI + API |
| `POST /backtest` | `backtest` | API only |
| Chat UI backtest (via agent) | `backtest` | Chat UI |

### When to Use Which

| Use Case | Service |
|----------|---------|
| Interactive research & trading | **Chat UI** (`python app.py`) |
| Build a custom frontend or mobile app | **REST API** (`python api/main.py`) |
| Quick one-off queries from terminal | **CLI** (`python fincode.py`) |
| Automated scripts / CI pipelines | **REST API** (curl/httpx) |
| View trade history in browser | **Chat UI** → `/trades` page |
| Fetch trade data programmatically | **REST API** → `GET /pnl/trades` |

---

## 4. Agent Optimization & Cleanup

### Removed: RAG / Knowledge Base Tool
- Removed `search_knowledge_base` from agent registration (`agent/agent.py`)
- Removed from `agent/tools/__init__.py` exports
- Removed from system prompt (`agent/prompts/__init__.py`)
- File `agent/tools/knowledge_base.py` kept but not loaded

### Added: Direct Command Fast Path (Chat UI)
Bloomberg-style commands bypass the LLM entirely — tools are called directly:

| Command | Tool | Example |
|---------|------|---------|
| `load AAPL` | `get_ticker_details` | Company profile |
| `des AAPL` | `get_ticker_details` | Same as load |
| `fa NVDA` | `get_financials` | Financial statements |
| `anr MSFT` | `get_analyst_recommendations` | Analyst ratings |
| `ee TSLA` | `get_earnings_estimates` | Earnings forecasts |
| `rv GOOG` | `get_relative_valuation` | Peer comparison |
| `own AAPL` | `get_ownership` | Ownership data |
| `gp AAPL` | `get_price_graph` | Historical price |
| `gip AAPL` | `get_intraday_graph` | Intraday price |
| `news TSLA` | `get_news` | Latest news |
| `quote AAPL` | `get_ticker_details` | Quick quote |
| `scan` | `scan_weather_opportunities` | Weather markets |

### Added: In-Memory TTL Cache (`ToolCache`)
- 5-minute TTL, 256 max entries, LRU eviction
- First `load AAPL` hits API (~2-5s), repeat calls return instantly from cache
- Cache hit shown in UI as "cached ⚡"

### Added: Eager Agent Init
- Agent is created at server startup (not on first request)
- Eliminates cold-start latency on first WebSocket query

### Rewritten: System Prompt
- Lists all 14 actual tools with args
- Focused efficiency rules (1 tool call = enough)
- No more RAG references

### Added: Poly: Commands in Chat UI + API (same as CLI)
All CLI poly: commands now work in Chat UI and have corresponding API endpoints:

| Command | Description | API Endpoint |
|---------|-------------|-------------|
| `poly:weather London` | Search weather markets | `POST /polymarket/search` |
| `poly:backtest Seoul 7` | Run backtest | `POST /backtest` |
| `poly:backtestv2 Seoul 7` | Cross-sectional backtest | `POST /backtest` (v2_mode=true) |
| `poly:predict London 2` | Prediction (forward) | `POST /backtest` (is_prediction=true) |
| `poly:simbuy 50 <id>` | Simulate trade | `POST /polymarket/simulate` |
| `poly:buy 50 <id>` | Real USDC buy | via agent `place_real_order` |
| `poly:sell 50 <id>` | Real sell | via agent `place_real_order` |
| `poly:portfolio` | On-chain portfolio | `GET /polymarket/portfolio` |
| `poly:paperportfolio` | Paper portfolio | local display |

---

## 5. FinCode → PolyCode Rebrand

All branding renamed across 20+ files:
- Classes: `FinCodeCLI` → `PolyCodeCLI`, `FinCodeApp` → `PolyCodeApp`
- Env vars: `FINCODE_DEBUG` → `POLYCODE_DEBUG`
- UI text, prompts, comments, shell scripts

---

## How to Run

```bash
# 1. Activate venv
.venv\Scripts\activate

# 2. Set up DB (once)
python scripts/setup_polycode_db.py

# 3. Start Chat UI
python app.py
# → http://localhost:5001

# 4. Start API server (separate terminal)
python api/main.py
# → http://localhost:8000

# 5. Start CLI
python fincode.py
```

## How to Test

```bash
# API health
curl http://localhost:8000/health

# Agent streaming
curl -N -X POST http://localhost:8000/agent/stream \
  -H "Content-Type: application/json" \
  -d '{"query":"What is AAPL price?"}'

# PnL endpoints
curl http://localhost:8000/pnl/summary
curl http://localhost:8000/pnl/trades

# Chat UI direct commands (type in browser)
load AAPL
fa NVDA
news TSLA
scan

# Trades page
# → http://localhost:5001/trades

# Run tests
pytest tests/ -v
```
