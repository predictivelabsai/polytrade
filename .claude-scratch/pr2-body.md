## What

Agent-side capture for the public accuracy scorecard: the chat agent can now record its own falsifiable market calls. Companion to the gateway PR that stores, grades, and publishes them (#27) — this PR is **inert until that one lands and its schema is applied**; calls will accrue silently once both are deployed.

- **Schemas** (`schemas.py`): `PredictionInput` (condition_id, optional token_id, market_question ≤1000, predicted_outcome ≤200, optional 0..1 confidence as a ≤4dp decimal string) and `PredictionRecorded` (the gateway response shape, extra="forbid"), both camelCase via the shared `ContractModel`.
- **`_gateway_post` helper** (`tools.py`): mirrors `_gateway_get` — delegated bearer, optional `Idempotency-Key` header.
- **`record_prediction` tool**: builds the camelCase payload from `PredictionInput`, posts to `/v1/agent/predictions` with idempotency key `prediction:{tool_call_id}` (falls back to a random UUID outside a tool call), validates the response into `PredictionRecorded`. The docstring carries the capture rules in the house style: call exactly once in the turn where the directional call is made; never for already-resolved markets, hypotheticals, backtests, or restatements. Appended to `POLYMARKET_TOOLS` — the allowlist middleware derives from that list, so no other change is needed.
- **System prompt** (`graph.py`): one paragraph framed as *measurement bookkeeping, not forecasting* — the tool stores only the market question, the predicted outcome, and the eventual resolution, never chat content, and grants no license to promise returns; probability statements remain hypothetical estimates.

## Verification

- `pnpm test:agent`: 46 passed, 1 skipped. New `test_record_prediction.py` covers schema bounds (confidence/token_id rejection), camelCase + `exclude_none` wire payload, `PredictionRecorded` parsing, and a full agent turn (fake model calls the tool) asserting the bearer, the `prediction:prediction-call-1` idempotency key, the exact camelCase body, the single tool message, and that the bearer never appears in the result repr. `test_tool_boundary.py` inventory now includes `record_prediction`, with prompt assertions on the bookkeeping framing and the hypothetical-disclosure guardrails.

## Notes

- Responses are public-safe by construction: the gateway returns no principal/thread fields, so nothing identity-bearing reaches the agent transcript.
- Deploy order: land #27 first and re-apply the schema; this PR's tool will then start populating `polytrade_agent.agent_predictions` with no further config.