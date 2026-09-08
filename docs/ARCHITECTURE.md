# Architecture

## Services and data ownership

PolyTrade runs a React client, Fastify gateway, FastAPI agent, FastAPI backtest
control service, Redis, and Celery backtest workers. All persistent services use
the same externally provisioned PostgreSQL database through the same
`DATABASE_URL`; production Compose never provisions a database. The schemas
`polytrade`, `polytrade_agent`, and `polytrade_backtest` are namespaces, not
security boundaries; one application identity intentionally owns all three.

The React client and optional AssetHero caller use one public API origin. The
gateway serves its native routes and forwards `/v1/agent` to the private agent
service and `/v1/backtests` to the private backtest control service without
changing paths or response contracts. Agent SSE bodies remain streaming. The
proxy does not authenticate on behalf of either upstream; each service verifies
the forwarded bearer token and required scope.

## Authentication boundary

PolyTrade's standalone React client always authenticates through PolyTrade's
Clerk instance. The APIs trust that Clerk issuer and can optionally trust a
second issuer belonging to AssetHero, a separate application. AssetHero access
is API-only: there is no AssetHero frontend mode or browser bridge in PolyTrade.

When enabled, AssetHero sends short-lived, scoped JWTs for the `polytrade`
audience. API ownership remains namespaced by issuer, so `assethero:<sub>` and
`clerk:<sub>` are separate principals even when they represent the same person.
The optional issuer and JWKS URL must be configured together on the gateway,
agent, and backtest services.

The gateway is the only startup bootstrapper. Its entrypoint takes an advisory
lock, enables required extensions, and creates the complete final schema before
the gateway health check can pass. The other services set their connection
search path and also schema-qualify persistent repository SQL. This prevents a
reused pooled connection from writing to a neighboring schema.

## Agent boundary

The agent uses `deepagents==0.7.1` and open-source LangGraph. Its fixed tool
allowlist contains normalized Polymarket reads, resolved-market search, owned
backtest creation/status reads, and unsigned order drafting. Filesystem, shell,
delegation, browser, general search, wallet, and order-mutation tools are blocked.

For a backtest request the agent searches resolved markets first. It presents
candidates when the request is ambiguous and never invents a condition ID. Once
the exact market is known, it sends the user's request-scoped research JWT and
the complete configuration to the backtest API. A successful tool result is
persisted as a `backtest` thread item and emitted as a typed `backtest.created`
SSE event. When the user requests all strategies for one selected market, the
agent creates one run per strategy and reports every run ID and exact
configuration. Before a multi-run request it reads the owner's active count and
limit. A batch that exceeds ten runs or the remaining active capacity is rejected
before any run in that batch starts, and the agent reports the exact requested and
available counts. The bearer token is never graph state or checkpoint data.

## Backtest lifecycle

1. `POST /v1/backtests` validates the research JWT, owner, idempotency key, and
   configuration.
2. One PostgreSQL transaction takes a per-owner advisory lock, checks the
   configured active-run limit, and inserts the queued run, initial progress,
   and dispatch-outbox record. The default limit is ten queued/running runs per
   owner, which supports multi-market strategy suites while keeping queue use bounded.
3. The API's dispatcher publishes the committed run UUID to Celery using the
   UUID as the task ID. No JWT, market configuration, or user data enters Redis.
4. A worker atomically changes that UUID from queued to running. Duplicate or
   late delivery therefore has no second effect.
5. The worker validates one resolved binary CLOB V2 market, fee rate, date
   range, and both one-minute histories. It stores an immutable, canonical,
   gzip-compressed dataset keyed by SHA-256.
6. The Decimal engine dispatches the selected versioned strategy, then one transaction stores
   trades, metrics, downsampleable chart series, and the completed run.

The API polls PostgreSQL for display state. Redis result data is optional and
short-lived. The outbox loop reconciles undispatched and stale queued/running
runs after broker loss, duplicate delivery, or worker termination. Celery uses
late acknowledgement, worker-loss rejection, prefetch one, bounded data retries,
heartbeats, and soft/hard limits with a longer visibility timeout.

## Deterministic model

The backtest config is a discriminated union keyed by a versioned strategy ID. An omitted config,
an empty config, or an untagged config keeps the original `momentum_v1` defaults.

| Strategy | Default entry rule |
| --- | --- |
| `momentum_v1` | Buy the outcome whose price rises at least 0.05 over 60 minutes. |
| `mean_reversion_v1` | Buy the outcome at least 0.05 below the arithmetic mean of its prior 60-minute window. |
| `breakout_v1` | Buy the outcome at least 0.02 above the maximum in its prior 240-minute window. |

Mean-reversion and breakout windows exclude the current observation, require at least two prior
observations, and require a window-start anchor within the configured fill-delay tolerance. Every
strategy watches YES and NO independently, chooses the larger qualifying signal, and uses YES as
the deterministic tie-breaker. Signals fill at that outcome's next observation and expire after
the configured delay. Entries and early exits use taker fills with configurable adverse slippage.
Fees use `shares × feeRate × price × (1 − price)`, rounded to five decimals. Shares are rounded down
to six decimals and sizing includes entry fees.

The default position is 10% of current cash/equity, with one open position,
$0.10 take-profit, $0.05 stop-loss, a 24-hour maximum hold, and a 60-minute
post-exit cooldown. Positions left open settle at the binary $1/$0 resolution.
The result includes strategy metrics, YES/NO buy-and-hold benchmarks, the exact
configuration, assumptions, warnings, trades, and an equity/price replay series.

## Real-order boundary

The gateway remains the only process that can exchange wallet authority for L2
CLOB credentials or submit/cancel an order. The browser must review and sign an
exact EIP-712 intent. Backtest components have no dependency on wallet packages
or gateway mutation routes, and the web Backtests workspace contains no wallet
or real-order controls.

Geographic eligibility is a browser-side check and the single authoritative
source. The web application calls Polymarket's geoblock endpoint over the user's
own connection and gates wallet verification and new-order controls on that
result; a failed or unreachable check leaves them disabled. The gateway performs
no geographic check and never rejects a request on geography, so a client that
does not run the browser check is not geographically restricted by PolyTrade.

## Paper-trading boundary

Paper trading is an authenticated gateway feature backed by five tables in the
`polytrade` schema: one fixed 10,000-USDC account per namespaced principal,
aggregated long-only outcome positions, an append-only fill ledger, persistent
price-band strategy state, and an append-only strategy event tape. It uses only
the existing `research` scope and public Gamma/CLOB reads; it never opens a
wallet session, signs an intent, or calls a live-order mutation endpoint.

Quotes sweep visible asks for buys or bids for sells and are all-or-none. The
preview's worst consumed price becomes a fill-or-kill bound for execution, which
refetches market metadata, the order book, and token fee rate. Account locking,
the fill's idempotency key, the cash update, and the position update share one
PostgreSQL transaction.

The `/paper` workspace refreshes positions on entry and on explicit request.
Active positions are marked at the best bid net of an estimated exit fee; a
failed read retains the prior mark as stale. A final 1/0 market result credits
winning shares at one virtual USDC, credits losing shares at zero, and records
one idempotent settlement fill. There is no reset, deposit, withdrawal, or open
paper order.

A user can start one persistent price-band strategy for a selected outcome.
The gateway runner claims due scans with PostgreSQL `FOR UPDATE SKIP LOCKED`
leases, checks exits before entries, and routes every trade through the same
server-derived quote, fill-or-kill price bound, fee, account lock, and append-only
fill transaction as a manual paper order. The strategy maximum position is
rechecked inside that transaction. A stable scan UUID makes a reclaimed lease's
fill idempotent, and Stop clears future scans; a transaction already committing
may finish. Each scan refreshes marks and resolution state. The browser polls
strategy state, portfolio, and fills while `/paper` is open, but the gateway run
continues when it is closed.
