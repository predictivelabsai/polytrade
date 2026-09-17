## What

- Replace the placeholder FastAPI gateway with the Python runtime in `apps/gateway/polytrade_gateway`.
- Preserve authenticated agent/backtest proxying and expose research, public market, paper, wallet, account, alerts, and share routes.
- Run both gateway and FastHTML web services from Python containers; update compose health checks and runtime configuration.

## Verification

- `uv run --project apps/gateway ruff check apps/gateway/polytrade_gateway apps/gateway/tests_py`
- `uv run --project apps/gateway pytest -q apps/gateway/tests_py` (8 passed)
- `docker compose -f docker-compose.yaml config --quiet`

## Notes

- This branch is based on the FastHTML backtests stack and is intended to merge after PR #49.
- Paper and session state currently use the runtime store abstraction while the remaining JS/TS source is removed in the follow-up cutover PR.
