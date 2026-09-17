## What

- migrate the backtest run library and replay detail view to FastHTML
- render queued, running, failed, cancelled, and completed states with the existing visual hierarchy
- replace the browser chart runtime with a static, accessible equity-curve SVG
- add server-side launch, cancel, duplicate, delete, resolved-market search, and configuration form flows

## Verification

- `uv run --project apps/web ruff check apps/web/polytrade_web apps/web/tests_py`
- `uv run --project apps/web pytest -q apps/web/tests_py` (12 passed)
- browser sweep at 1440px and 390px: zero script elements and zero horizontal overflow

## Notes

- stacked on #48; merge the migration PRs in order through this one
- all backtest API requests remain bearer-authenticated server-side
- chart and replay updates are full-page refreshes until the final client runtime is removed
