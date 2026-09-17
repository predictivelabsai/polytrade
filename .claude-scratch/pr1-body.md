## What

Foundation for the public agent accuracy scorecard: the gateway can now record the chat agent's falsifiable market calls, grade them against Polymarket resolutions in the background, and publish an aggregate hit rate — with no UI yet.

- **DDL**: `polytrade_agent.agent_predictions` (status PENDING/GRADED/VOID, grade-attempt lease columns, partial unique index `(principal_id, condition_id, lower(predicted_outcome)) WHERE status='PENDING'` so duplicate open calls collapse into one row). **Deploy requires a schema re-apply.**
- **Contracts**: `agentPredictionRequest/Record/HitRate` schemas plus a pure `resolvedBinaryMarketWinner` helper (winner = the outcome priced exactly 1 on a closed binary market).
- **Gateway capture**: `POST /v1/agent/predictions` on `research` auth via the standard idempotency path; responses are public-safe by construction (no principal/thread fields).
- **Grading runner**: a lease-batched background worker (mirrors the alert delivery runner) claims due predictions, fetches each market's resolution + event tags from Gamma, and grades / voids / backs off:
  - hit = normalized predicted outcome matches the winner
  - market still open → retry in 1h
  - call recorded after the market resolved → VOID (anti-gaming)
  - ambiguous 50-50 resolution → VOID
  - metadata unavailable → exponential backoff (60s → 24h cap), VOID after 30 attempts
  - category comes from the event's first non-"All" Gamma tag, degrading to "Other"
- **Public read**: `GET /v1/public/agent-accuracy` — totals, by-category (max 8), recent graded (max 25), 60s cache header.

## Verification

- `pnpm --filter @polytrade/contracts build` + tests (30 pass)
- `pnpm --filter @polytrade/gateway lint` clean
- `pnpm --filter @polytrade/gateway test`: 146 passed; new grader suite covers hit/miss, open-market retry, post-resolution VOID, 50-50 VOID, backoff + attempt cap, batch cap, lease reclaim; app tests assert research scope, idempotent re-record returning the same row, and that the public aggregate never leaks principal/thread fields.

The agent tool + prompt that start populating the table land in the next PR; the public `/accuracy` page after that.