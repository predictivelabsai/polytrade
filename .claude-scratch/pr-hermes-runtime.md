## What

Ports the Hermes runtime from the legacy chat stack into the paper-platform agent service, alongside the existing DeepSeek runtime:

- **Hermes sidecar** in the root `docker-compose.yaml` (`nousresearch/hermes-agent`, OpenAI-compatible `/v1` on 8642), consumed as a plain streaming `ChatOpenAI`. Blank `HERMES_API_SERVER_KEY` keeps the deployment DeepSeek-only.
- **Per-request runtime selection**: `POST /v1/agent/threads/{id}/runs/stream` accepts `"runtime": "deepseek" | "hermes"` (default unchanged). Requesting Hermes when unconfigured returns 400.
- **Silent fallback**: if Hermes errors, times out, or ends before emitting anything, the run transparently replays on DeepSeek (run checkpoint reset once, `runtime.fallback` SSE event, activity metadata records `requested_runtime`/`fallback`). Partial streams never fall back.
- **Usage gating** (ported from legacy `chat/usage.py`): per-principal daily query limit (`FREE_PLATFORM_QUERY_LIMIT`, default 5, atomic slot claim + refund on empty failed runs) and a platform-wide daily LLM budget (`PLATFORM_LLM_DAILY_BUDGET_USD`, default 5). Rejections are HTTP 429 with actionable messages; `usage.notice` SSE warns at 80% quota / 80–90% budget.
- **Telemetry**: three idempotent tables in `apps/gateway/bootstrap/schema.sql` (`agent_activity`, `agent_llm_usage`, `agent_daily_usage`), written through `AgentRepository` with secrets/bearer tokens redacted and metadata restricted to an allowlist. Measured token usage is captured from stream chunks; an estimated row (chars/4) is recorded when a runtime reports none.
- **Web**: DeepSeek/Hermes segmented toggle in the chat composer, a quiet "N / 5 queries used today" line refreshed per turn, typed `usage.notice` / `runtime.fallback` notices rendered inline, and 429s surfaced as composer errors. New `GET /v1/agent/usage` endpoint (zod-validated client).
- **Admin**: `GET /v1/agent/admin/usage` returns per-runtime/per-principal aggregates for identities listed in `ADMIN_PRINCIPAL_IDS` (403 otherwise). Admin web UI deliberately deferred.

Deferred (as agreed): BYOK vault, AssetHero service auth, admin web UI, `HERMES_MAX_ITERATIONS`; `docker-compose.chat.yaml` untouched.

## Verification

- `pnpm typecheck` — clean (contracts, gateway, web)
- `pnpm test` — gateway 149 passed / 7 skipped, web 14 files / 88 tests passed
- `pnpm test:agent` — `uv run pytest tests/ -q` → 78 passed / 1 skipped (integration-guarded); `uv run ruff check` clean
- Bootstrap idempotency: `test:bootstrap` against a throwaway Postgres passes (concurrent + repeat bootstrap, invariant counts, append-only audit trigger). Note: the test's table-count invariants were stale on `main` (paper-strategy tables added without updating it) — this PR updates them to the current 16 gateway / 10 agent tables (7 existing + 3 new).
- `docker compose config` resolves with the hermes service, `hermes-data` volume, and all new env vars.
- Playwright walkthrough of `/chat` with mocked usage/stream: toggle renders and switches `aria-pressed`, "2 / 5 queries used today" shows, fallback + quota notices and the answer render, outgoing SSE body is `{"message", "runtime":"hermes"}` (screenshots in `test-results/walkthrough/`).

## Notes

- Deploy checklist: set `HERMES_API_SERVER_KEY` (leave blank for DeepSeek-only) and `XAI_API_KEY` for the sidecar, then redeploy — bootstrap applies the three new tables automatically. Agent service does not depend on the hermes container and boots fine without it.
- No gateway code changes; the proxy forwards `/v1/agent` transparently and 429 `{"detail": ...}` bodies pass through.