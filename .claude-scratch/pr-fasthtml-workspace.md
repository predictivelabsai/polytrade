## What

- add a server-side authenticated workspace shell with native responsive navigation
- migrate chat history, agent submission, conversation rendering, account tables, order cancellation, settings, and session disconnect into FastHTML routes/forms
- add an authenticated gateway client that forwards bearer cookies and normalizes upstream failures into safe page states
- keep the existing dark workspace CSS and add a no-JavaScript mobile menu using native `details`/`summary`

## Verification

- `uv run --project apps/web ruff check apps/web/polytrade_web apps/web/tests_py`
- `uv run --project apps/web pytest -q apps/web/tests_py` (8 passed)
- browser sweep at 1440px and 390px: zero script elements and zero horizontal overflow
- mobile menu opens natively and exposes Chat, Trades, Paper, and Backtests

## Notes

- stacked on #46; merge #44, #45, #46, then this PR
- paper and backtest-specific workflows are the next migration slices
- React remains available temporarily until all routes are switched atomically
