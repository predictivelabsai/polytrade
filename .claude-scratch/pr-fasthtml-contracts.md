## What
- add Pydantic equivalents for the shared trading, paper, market, alert, backtest, and prediction contracts
- preserve public JSON field names, validation bounds, redaction behavior, and built-in strategy templates
- add an isolated Python contracts project and CI job as the base for the gateway and FastHTML ports

## Verification
- `uv run --project packages/contracts ruff check packages/contracts`
- `uv run --project packages/contracts pytest -q packages/contracts/tests`
- `pnpm --filter @polytrade/contracts test`

## Notes
- this is the first PR in the JS/TS-to-FastHTML migration stack and does not switch production traffic yet
- based on current `main` at `476e930`
