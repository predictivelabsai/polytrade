## What
- add the Python gateway project with validated settings, strict multi-issuer JWT verification, and AES-GCM secret storage
- port cached public Polymarket reads and normalization to `httpx`
- port paper quote and mark calculations to Python `Decimal` without changing rounding, fee, or depth behavior
- add isolated gateway lint, tests, lockfile, and CI coverage

## Verification
- `uv run --project apps/gateway ruff check apps/gateway/polytrade_gateway apps/gateway/tests_py`
- `uv run --project apps/gateway pytest -q apps/gateway/tests_py`
- `uv run --project packages/contracts pytest -q packages/contracts/tests`

## Notes
- stacked on #44
- this establishes tested gateway primitives; the following stack item ports PostgreSQL services and switches runtime traffic
