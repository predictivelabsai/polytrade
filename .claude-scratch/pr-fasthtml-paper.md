## What

- migrate the paper dashboard to FastHTML with server-rendered ledger, templates, market search, positions, fills, and strategy controls
- replace client-side quote/order state with native POST forms and a server-side preview/confirm flow
- preserve paper-only guardrails, price protection copy, and the existing responsive paper layout
- add native strategy start/stop routes and safe gateway error states

## Verification

- `uv run --project apps/web ruff check apps/web/polytrade_web apps/web/tests_py`
- `uv run --project apps/web pytest -q apps/web/tests_py` (10 passed)
- browser sweep at 1440px: zero script elements and zero horizontal overflow

## Notes

- stacked on #47; merge #44 through #47, then this PR
- forms keep paper execution server-side and do not expose wallet keys or browser providers
- backtests and the final wallet signing flow remain in subsequent migration slices
