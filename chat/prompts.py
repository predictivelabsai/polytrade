"""Canonical prompt and command help for the PolyTrade research chat."""

SYSTEM_PROMPT = (
    "You are PolyTrade, an AI financial research and Polymarket weather trading "
    "assistant. You have tools to look up stock data, news, analyst ratings, and "
    "Polymarket weather markets. Use your tools when users ask about specific "
    "stocks or market data. Be concise and use markdown formatting with tables "
    "where appropriate. Users can type research CLI commands directly in chat "
    "(for example: load AAPL, fa NVDA, poly:weather London, or "
    "poly:backtest Seoul 7) and they will be executed automatically. For stock "
    "queries, always use the appropriate tool to get real data. Never place or "
    "suggest that you placed a real-money order; real trading is outside this "
    "research chat and requires a separate explicitly authorized workflow."
)


HERMES_HELP = """# Hermes agent

- `/hermes <question>` — use Hermes for one message.
- `/hermes poly:weather London` — run a PolyTrade command attributed to Hermes.
- `/hermes help` — show this help.
- Messages without an agent prefix use DeepAgents by default.
"""


COMMAND_HELP = """# PolyTrade Commands

## Choose an agent

- `/usage` shows today's allowance and estimated AI cost without calling a model.
- `/hermes your question` uses Hermes for one message.
- `/deepagent your question` uses DeepAgents for one message.
- `/deepagents your question` is a compatibility alias.
- Unprefixed questions use DeepAgents by default.

## Stock research
- `load AAPL` — Company profile and quote
- `fa NVDA` — Financial analysis
- `anr MSFT` — Analyst recommendations
- `ee TSLA` — Earnings estimates
- `rv GOOG` — Relative valuation
- `own AAPL` — Ownership
- `gp AAPL` — Price graph
- `gip AAPL` — Intraday graph
- `news TSLA` — Latest news
- `quote AAPL` — Current quote

## Weather markets
- `poly:weather London` — Search weather markets
- `scan` — Scan weather opportunities
- `poly:backtest London 7` — Run a backtest
- `poly:predict London 2` — Run a forward prediction

## Paper research
- `poly:simbuy 50 <token_id>` — Simulate price and slippage
- `poly:paperbuy 50 <token_id>` — Add a paper trade
- `poly:paperportfolio` — View the paper portfolio

Real-money buy and sell commands are intentionally unavailable through chat.
"""
