# Security notes

## Authentication and ownership

The standalone web application authenticates through PolyTrade's required Clerk
issuer. Gateway, agent, and backtest APIs can additionally trust AssetHero's
separate RS256 issuer when `ASSETHERO_API_ISSUER` and
`ASSETHERO_API_JWKS_URL` are configured together. AssetHero tokens are capped at
five minutes. Backtest routes require `research`; account and order routes
retain their stronger trading and wallet requirements. Every backtest lookup
includes the canonical namespaced principal, so a foreign UUID and an unknown
UUID both return `404`.

The gateway is the only public API service. Its agent and backtest proxies
forward bearer tokens over the private Compose network, leave authorization to
the owning upstream, stream SSE without buffering, and replace transport errors
with sanitized `502` or `504` responses. Agent and backtest container ports are
not published on the host.

Geographic eligibility is enforced only in the browser, which queries
Polymarket's geoblock endpoint from the user's own connection. The gateway
carries no server-side geographic check, so this gate is client-controlled and
is bypassed by any caller that skips the web application.

Creation and cancellation require idempotency keys. PostgreSQL enforces one
queued/running run per principal, and a task UUID is atomically claimed before
work begins. Public failures contain stable codes and safe messages rather than
Redis, SQL, HTTP, or stack details.

## Queue and worker isolation

Celery messages contain only a run UUID. PostgreSQL contains the owner,
configuration, progress, data hash, results, and cancellation state. Redis is a
transport and expiring cache, never the system of record.

The backtest package imports no wallet signing or order-submission code. Workers
call only public Gamma market metadata, CLOB fee-rate, and CLOB price-history
endpoints. They model hypothetical taker fills locally and cannot observe or
change a user's wallet, positions, allowances, orders, or cancellations.

Paper routes use the research scope and public market data only. They write a
namespaced virtual ledger, not a wallet or CLOB account. Paper execution has its
own atomic idempotency record in the append-only fill row, serializes cash and
position updates with an account lock, and rejects negative cash, short sales,
partial fills, stale price bounds, and mismatched market/token identities.
Persistent paper strategies keep the same boundary. PostgreSQL leases prevent
multiple gateway replicas from running one scan, deterministic scan keys prevent
duplicate fills after lease recovery, and the strategy's running state and
maximum position are rechecked in the ledger transaction. The background runner
receives no wallet credentials and cannot call live-order code.

Immutable normalized datasets use canonical JSON, deterministic gzip metadata,
and a SHA-256 key. Decimal calculations avoid binary floating-point execution
drift. The UI labels results hypothetical and keeps replay controls outside the
real-order ticket.

## Database model

All services intentionally share one database identity. Schemas provide naming
and lifecycle separation, not privilege isolation. Repository statements are
schema-qualified and pools set their expected search path. The bootstrap creates
no database or role, and no application table belongs to `public`.

Agent checkpoint payloads remain authenticated-encrypted. Request JWTs are
request-local, absent from graph state and Celery payloads, and never persisted
as thread items. L2 CLOB credentials remain gateway-only and encrypted with
principal/session associated data.

## Automated checks

CI validates fresh/repeated/concurrent bootstrap, all three schema locations,
absence of application tables in `public`, and one `current_database()` across
service pools. Tests cover deterministic signals and fills, fees, slippage,
sizing, exits, settlement, stale observations, owner isolation, idempotency,
dataset reuse, cancellation, public errors, typed events, and Redis publish
failure. Gateway tests also cover proxy path and header preservation, upstream
failures, CORS ownership, and incremental SSE delivery. All upstream history
and trading interactions are mocked; CI never
requires a wallet or contacts a live trading endpoint.
