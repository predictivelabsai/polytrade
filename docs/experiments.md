# Strategy comparisons and walk-forward experiments

[Watch the 67-second real app walkthrough](demos/strategy-comparison-walk-forward.mp4)
or read the [recording notes](demos/README.md).

Use the research chat to choose resolved binary Polymarket markets, then ask:

> Compare momentum, mean reversion, and breakout on the selected markets.
> Sweep take-profit by 0.01 and 0.015 token-price units and maximum holding time
> by 60, 120, and 180 minutes. Use 10,000 USDC independently on each market.

Follow up with “Run walk-forward validation on that experiment.” The agent uses
the original markets, periods, and **whole candidate grid**, not its full-history
winner. Open the experiment from chat Activity or Backtests → Experiments.
The view updates progress, supports cancellation, and retains results on reload.
It separates in-sample rankings from out-of-sample summaries, equity, and folds.

Thresholds, take-profit, stop-loss, and slippage use **absolute token-price
changes**; a value of `0.01` is one cent. `positionSizePct` uses fractions (0.10
means 10%). Maximum holding time is an exit deadline, not a minimum holding
period. These simulations do not place orders or run live paper strategies.

## API

All routes require the existing `research` bearer scope. Ownership is enforced
for reads, cancellation, and follow-ups. Creation and walk-forward follow-ups
require an `Idempotency-Key` (8–200 characters); reuse it when retrying the same
request. Same-key/different-body requests return 409.

- `POST /v1/backtests/experiments`: create; 202 for a new job, 200 for a repeat.
- `GET /v1/backtests/experiments?limit=50`: owned jobs and their progress.
- `GET /v1/backtests/experiments/{id}`: definition, status, failure, and results.
- `POST /v1/backtests/experiments/{id}/cancel`: idempotently cancel queued/running work.
- `POST /v1/backtests/experiments/{id}/walk-forward`: create a validation job from
  the source definition; body `{}` uses the default windows. Supply `folds`,
  `trainMinutes`, and `testMinutes` to override (the two durations must be paired).

Example create body for an 18-candidate grid:

```json
{
  "marketIds": ["REPLACE_WITH_SELECTED_CONDITION_ID"],
  "initialCapital": "10000",
  "mode": "grid",
  "strategies": [{
    "baseConfig": {"strategy": "mean_reversion_v1"},
    "parameters": {
      "reversionThreshold": ["0.03", "0.05", "0.07"],
      "takeProfit": ["0.01", "0.015"],
      "maxHoldMinutes": [1440, 2880, 4320]
    }
  }]
}
```

Supported strategy IDs are `momentum_v1`, `mean_reversion_v1`, and `breakout_v1`.
`baseConfig` uses the existing single-run strategy configuration. Its strategy
ID, capital, and dates cannot be grid axes: capital belongs at the experiment
level and dates belong in `periods`. Each optional period has a unique `label`
and timezone-aware `startAt`/`endAt`. Omitted periods use available history.
Data outside the provider's available history or gaps exceeding fill tolerance
fail explicitly; no synthetic prices or silent range shortening are used.

## Evaluation

Grid results rank net return after fees/slippage, then lower drawdown, then a
stable candidate ID. Zero-trade configurations remain visible. All candidates
start with the same capital; each market and period is evaluated independently.

Walk-forward defaults to a rolling training window spanning half the selected
period and five consecutive test windows, each spanning one tenth. Explicit
window durations begin at the period start; unused trailing history is not part
of the test result. Each fold trains on earlier data, chooses a candidate, then
freezes it on the next unseen window. Positions close at window boundaries and
test cash carries forward. Training always starts with equal initial capital.

Results include all fold dates, selected configurations, training/test metrics,
and combined **test-only** metrics. YES/NO benchmarks cover the same test windows,
reinvest between folds, and include their own costs. Charts are downsampled to
at most 1,000 points; metrics use every original observation. Dataset hashes and
exact configurations identify the recorded inputs. Full simulation checkpoints
remain in PostgreSQL.

These are hypothetical results. One-minute prices do not reconstruct depth,
spreads, partial fills, or queue position. Retrospectively choosing markets,
strategies, or repeatedly changing window layouts can bias even walk-forward
results. Returns are period returns, not promises or annualized estimates.

## Verification

Run `pnpm test`, `pnpm typecheck`, `pnpm test:agent`, and `pnpm test:backtest`.
Database-backed tests additionally require an isolated `TEST_DATABASE_URL` with
the gateway bootstrap schema applied. The experiment integration suite checks
HTTP contracts, ownership/capacity, cancellation fencing, retry checkpoints,
and an 18-candidate grid followed by walk-forward evaluation. It uses synthetic
historical fixtures and makes no live market requests.
