---
description: Run the full PolyTrade regression test suite
user-invocable: true
---

Run the regression test suite that tests all backend components:

```bash
cd c:/Users/HP/Desktop/Upwork_project/modeling/polycode
.venv/Scripts/python -m pytest tests/regression_suite.py -v --tb=short -x 2>&1
```

The suite covers:
1. Stock tools (9 tools: financials, ticker, ownership, analyst, earnings, valuation, graphs, news)
2. Weather & Polymarket tools (search, scan, simulate)
3. Weather client (Tomorrow.io forecasts, probability calculation)
4. Agent core (simple query, tool usage, tool registration)
5. Command processor (help, load, fa, anr, ee, poly:weather, poly:simbuy)
6. Database operations (runs, trades, PnL snapshots)
7. Chat store (save/load conversations, list, delete)
8. Backtest engine (London 3-day backtest)
9. Trading strategy (signal generation, portfolio simulation)
10. LLM provider (xAI, OpenAI, Anthropic instantiation)
11. Visual Crossing (historical weather)
12. Polymarket CLOB (order book)

If tests fail, investigate and fix the failures. Report results as a summary table.
