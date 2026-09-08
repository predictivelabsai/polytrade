CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE SCHEMA IF NOT EXISTS polytrade;
CREATE SCHEMA IF NOT EXISTS polytrade_agent;
CREATE SCHEMA IF NOT EXISTS polytrade_backtest;

CREATE TABLE IF NOT EXISTS polytrade.wallet_challenges (
    id uuid PRIMARY KEY,
    principal_id text NOT NULL,
    wallet_address text NOT NULL,
    signature_type smallint NOT NULL CHECK (signature_type BETWEEN 0 AND 3),
    funder_address text,
    timestamp_seconds bigint NOT NULL,
    nonce integer NOT NULL,
    typed_data jsonb NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS wallet_challenges_owner_idx
    ON polytrade.wallet_challenges (principal_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS polytrade.wallet_sessions (
    id uuid PRIMARY KEY,
    principal_id text NOT NULL,
    wallet_address text NOT NULL,
    signature_type smallint NOT NULL CHECK (signature_type BETWEEN 0 AND 3),
    funder_address text,
    encrypted_credentials text NOT NULL,
    idle_expires_at timestamptz NOT NULL,
    absolute_expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    last_used_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS wallet_sessions_active_owner_idx
    ON polytrade.wallet_sessions (principal_id, created_at DESC)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS polytrade.order_intents (
    id uuid PRIMARY KEY,
    principal_id text NOT NULL,
    session_id uuid NOT NULL REFERENCES polytrade.wallet_sessions(id),
    idempotency_key text NOT NULL,
    proposal jsonb NOT NULL,
    order_type text NOT NULL CHECK (order_type IN ('GTC', 'GTD', 'FOK', 'FAK')),
    post_only boolean NOT NULL,
    typed_data jsonb NOT NULL,
    unsigned_order jsonb NOT NULL,
    signature_suffix text,
    status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'SUBMITTING', 'SUBMITTED', 'REJECTED', 'AMBIGUOUS', 'EXPIRED')),
    signed_order_hash text,
    upstream_response jsonb,
    expires_at timestamptz NOT NULL,
    submitted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (principal_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS order_intents_owner_created_idx
    ON polytrade.order_intents (principal_id, created_at DESC);

CREATE TABLE IF NOT EXISTS polytrade.clob_order_mirror (
    principal_id text NOT NULL,
    order_id text NOT NULL,
    wallet_address text NOT NULL,
    payload jsonb NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, order_id)
);

CREATE TABLE IF NOT EXISTS polytrade.clob_fill_mirror (
    principal_id text NOT NULL,
    trade_id text NOT NULL,
    wallet_address text NOT NULL,
    payload jsonb NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, trade_id)
);

CREATE TABLE IF NOT EXISTS polytrade.idempotency_records (
    principal_id text NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    response jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, operation, idempotency_key)
);

CREATE TABLE IF NOT EXISTS polytrade.trading_audit (
    id bigserial PRIMARY KEY,
    principal_id text NOT NULL,
    action text NOT NULL,
    entity_id text,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS trading_audit_owner_created_idx
    ON polytrade.trading_audit (principal_id, created_at DESC);

CREATE OR REPLACE FUNCTION polytrade.prevent_audit_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'trading_audit is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trading_audit_no_update ON polytrade.trading_audit;
CREATE TRIGGER trading_audit_no_update
BEFORE UPDATE OR DELETE ON polytrade.trading_audit
FOR EACH ROW EXECUTE FUNCTION polytrade.prevent_audit_mutation();

CREATE TABLE IF NOT EXISTS polytrade.paper_accounts (
    principal_id text PRIMARY KEY,
    initial_cash numeric(24, 6) NOT NULL DEFAULT 10000.000000
        CHECK (initial_cash = 10000.000000),
    cash numeric(24, 6) NOT NULL DEFAULT 10000.000000 CHECK (cash >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS polytrade.paper_positions (
    principal_id text NOT NULL REFERENCES polytrade.paper_accounts(principal_id) ON DELETE CASCADE,
    condition_id text NOT NULL,
    token_id text NOT NULL,
    market_question text NOT NULL,
    outcome text NOT NULL,
    shares numeric(24, 6) NOT NULL CHECK (shares > 0),
    cost_basis numeric(24, 6) NOT NULL CHECK (cost_basis >= 0),
    best_bid numeric(12, 6) CHECK (best_bid > 0 AND best_bid <= 1),
    liquidation_value numeric(24, 6) NOT NULL DEFAULT 0 CHECK (liquidation_value >= 0),
    mark_status text NOT NULL DEFAULT 'unpriced'
        CHECK (mark_status IN ('current', 'stale', 'unpriced')),
    marked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, token_id)
);

CREATE INDEX IF NOT EXISTS paper_positions_owner_condition_idx
    ON polytrade.paper_positions (principal_id, condition_id);

CREATE TABLE IF NOT EXISTS polytrade.paper_fills (
    id uuid PRIMARY KEY,
    principal_id text NOT NULL REFERENCES polytrade.paper_accounts(principal_id) ON DELETE CASCADE,
    idempotency_key text NOT NULL,
    request_hash char(64) NOT NULL,
    kind text NOT NULL CHECK (kind IN ('BUY', 'SELL', 'SETTLEMENT')),
    condition_id text NOT NULL,
    token_id text NOT NULL,
    market_question text NOT NULL,
    outcome text NOT NULL,
    shares numeric(24, 6) NOT NULL CHECK (shares > 0),
    average_price numeric(12, 6) NOT NULL CHECK (average_price >= 0 AND average_price <= 1),
    gross_notional numeric(24, 6) NOT NULL CHECK (gross_notional >= 0),
    fee_rate numeric(12, 6) NOT NULL CHECK (fee_rate >= 0 AND fee_rate <= 1),
    fee numeric(24, 5) NOT NULL CHECK (fee >= 0),
    cash_effect numeric(24, 6) NOT NULL,
    realized_pnl numeric(24, 6) NOT NULL,
    observed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (principal_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS paper_fills_owner_created_idx
    ON polytrade.paper_fills (principal_id, created_at DESC, id DESC);

CREATE OR REPLACE FUNCTION polytrade.prevent_paper_fill_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'paper_fills is append-only';
END;
$$;

DROP TRIGGER IF EXISTS paper_fills_no_update ON polytrade.paper_fills;
CREATE TRIGGER paper_fills_no_update
BEFORE UPDATE OR DELETE ON polytrade.paper_fills
FOR EACH ROW EXECUTE FUNCTION polytrade.prevent_paper_fill_mutation();

CREATE TABLE IF NOT EXISTS polytrade.paper_share_links (
    principal_id text PRIMARY KEY REFERENCES polytrade.paper_accounts(principal_id) ON DELETE CASCADE,
    share_token text NOT NULL UNIQUE CHECK (share_token ~ '^[A-Za-z0-9_-]{32,64}$'),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS paper_share_links_token_idx
    ON polytrade.paper_share_links (share_token) WHERE enabled;

CREATE TABLE IF NOT EXISTS polytrade.paper_strategies (
    strategy_id uuid PRIMARY KEY,
    principal_id text NOT NULL REFERENCES polytrade.paper_accounts(principal_id) ON DELETE CASCADE,
    idempotency_key text NOT NULL,
    request_hash char(64) NOT NULL,
    condition_id text NOT NULL,
    token_id text NOT NULL,
    market_question text NOT NULL,
    outcome text NOT NULL,
    minimum_order_size numeric(24, 6) NOT NULL CHECK (minimum_order_size >= 0),
    entry_price numeric(12, 6) NOT NULL CHECK (entry_price > 0 AND entry_price <= 1),
    exit_price numeric(12, 6) NOT NULL CHECK (exit_price > 0 AND exit_price <= 1),
    shares_per_order numeric(24, 6) NOT NULL CHECK (shares_per_order > 0),
    max_position numeric(24, 6) NOT NULL CHECK (max_position > 0),
    interval_seconds integer NOT NULL CHECK (interval_seconds BETWEEN 5 AND 3600),
    status text NOT NULL CHECK (status IN ('RUNNING', 'STOPPED', 'FAILED')),
    orders_placed integer NOT NULL DEFAULT 0 CHECK (orders_placed >= 0),
    scans_completed integer NOT NULL DEFAULT 0 CHECK (scans_completed >= 0),
    last_action text NOT NULL DEFAULT 'STARTED'
        CHECK (last_action IN ('STARTED', 'WAIT', 'BUY', 'SELL', 'ERROR', 'STOPPED')),
    last_message text NOT NULL,
    last_quote_side text CHECK (last_quote_side IN ('BUY', 'SELL')),
    last_quote_price numeric(12, 6) CHECK (last_quote_price > 0 AND last_quote_price <= 1),
    last_scanned_at timestamptz,
    next_scan_at timestamptz,
    scan_id uuid,
    lease_owner text,
    lease_until timestamptz,
    started_at timestamptz NOT NULL,
    stopped_at timestamptz,
    updated_at timestamptz NOT NULL,
    UNIQUE (principal_id, idempotency_key),
    CHECK (entry_price < exit_price),
    CHECK (shares_per_order <= max_position),
    CHECK (
        (status = 'RUNNING' AND stopped_at IS NULL AND next_scan_at IS NOT NULL)
        OR (status <> 'RUNNING' AND stopped_at IS NOT NULL AND next_scan_at IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS paper_strategies_one_running_owner_idx
    ON polytrade.paper_strategies (principal_id)
    WHERE status = 'RUNNING';

CREATE INDEX IF NOT EXISTS paper_strategies_due_idx
    ON polytrade.paper_strategies (next_scan_at)
    WHERE status = 'RUNNING';

CREATE TABLE IF NOT EXISTS polytrade.paper_strategy_events (
    event_id uuid PRIMARY KEY,
    strategy_id uuid NOT NULL REFERENCES polytrade.paper_strategies(strategy_id) ON DELETE CASCADE,
    scan_id uuid,
    action text NOT NULL CHECK (action IN ('STARTED', 'WAIT', 'BUY', 'SELL', 'ERROR', 'STOPPED')),
    message text NOT NULL,
    side text CHECK (side IN ('BUY', 'SELL')),
    price numeric(12, 6) CHECK (price > 0 AND price <= 1),
    fill_id uuid REFERENCES polytrade.paper_fills(id),
    created_at timestamptz NOT NULL,
    UNIQUE (strategy_id, scan_id)
);

CREATE INDEX IF NOT EXISTS paper_strategy_events_strategy_created_idx
    ON polytrade.paper_strategy_events (strategy_id, created_at DESC, event_id DESC);

CREATE OR REPLACE FUNCTION polytrade.prevent_paper_strategy_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'paper_strategy_events is append-only';
END;
$$;

DROP TRIGGER IF EXISTS paper_strategy_events_no_update ON polytrade.paper_strategy_events;
CREATE TRIGGER paper_strategy_events_no_update
BEFORE UPDATE OR DELETE ON polytrade.paper_strategy_events
FOR EACH ROW EXECUTE FUNCTION polytrade.prevent_paper_strategy_event_mutation();

-- Monotonic reader cursor for the alerts fan-out. event_id is a random UUID, so
-- the delivery runner needs a strictly increasing sequence to page events from.
ALTER TABLE polytrade.paper_strategy_events
    ADD COLUMN IF NOT EXISTS event_seq bigint GENERATED BY DEFAULT AS IDENTITY;

CREATE INDEX IF NOT EXISTS paper_strategy_events_seq_idx
    ON polytrade.paper_strategy_events (event_seq);

CREATE TABLE IF NOT EXISTS polytrade.alert_channels (
    channel_id uuid PRIMARY KEY,
    principal_id text NOT NULL REFERENCES polytrade.paper_accounts(principal_id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('discord', 'telegram')),
    label text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 80),
    encrypted_target text NOT NULL,
    target_hint text NOT NULL,
    event_kinds text[] NOT NULL
        CHECK (
            cardinality(event_kinds) BETWEEN 1 AND 5
            AND event_kinds <@ ARRAY['STARTED', 'BUY', 'SELL', 'ERROR', 'STOPPED']::text[]
        ),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (principal_id, label)
);

CREATE INDEX IF NOT EXISTS alert_channels_owner_idx
    ON polytrade.alert_channels (principal_id, created_at DESC);

CREATE TABLE IF NOT EXISTS polytrade.alert_deliveries (
    delivery_id uuid PRIMARY KEY,
    channel_id uuid NOT NULL REFERENCES polytrade.alert_channels(channel_id) ON DELETE CASCADE,
    event_seq bigint NOT NULL,
    event_id uuid NOT NULL,
    strategy_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('STARTED', 'WAIT', 'BUY', 'SELL', 'ERROR', 'STOPPED')),
    message text NOT NULL,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 5,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_until timestamptz,
    delivered_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (channel_id, event_seq)
);

CREATE INDEX IF NOT EXISTS alert_deliveries_ready_idx
    ON polytrade.alert_deliveries (next_attempt_at, delivery_id)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS alert_deliveries_channel_created_idx
    ON polytrade.alert_deliveries (channel_id, created_at DESC);

-- Single-row, global fan-out cursor over paper_strategy_events.event_seq.
CREATE TABLE IF NOT EXISTS polytrade.alert_event_cursor (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    last_event_seq bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS polytrade_agent.agent_threads (
    thread_id uuid PRIMARY KEY,
    principal_id text NOT NULL,
    title text NOT NULL DEFAULT 'New chat',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    UNIQUE (thread_id, principal_id),
    CONSTRAINT agent_threads_title_length CHECK (char_length(title) BETWEEN 1 AND 80)
);

ALTER TABLE polytrade_agent.agent_threads
    ADD COLUMN IF NOT EXISTS title text NOT NULL DEFAULT 'New chat';
ALTER TABLE polytrade_agent.agent_threads
    DROP CONSTRAINT IF EXISTS agent_threads_title_length;
ALTER TABLE polytrade_agent.agent_threads
    ADD CONSTRAINT agent_threads_title_length CHECK (char_length(title) BETWEEN 1 AND 80);

CREATE INDEX IF NOT EXISTS agent_threads_owner_updated_idx
    ON polytrade_agent.agent_threads (principal_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS agent_threads_expiry_idx
    ON polytrade_agent.agent_threads (expires_at);

CREATE TABLE IF NOT EXISTS polytrade_agent.agent_runs (
    run_id uuid PRIMARY KEY,
    thread_id uuid NOT NULL,
    principal_id text NOT NULL,
    status text NOT NULL CHECK (
        status IN ('running', 'completed', 'failed', 'cancelled', 'interrupted')
    ),
    error_code text,
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    FOREIGN KEY (thread_id, principal_id)
        REFERENCES polytrade_agent.agent_threads(thread_id, principal_id) ON DELETE CASCADE,
    CHECK (
        (status = 'running' AND completed_at IS NULL)
        OR (status <> 'running' AND completed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS agent_runs_thread_started_idx
    ON polytrade_agent.agent_runs (thread_id, started_at DESC);

-- LangGraph's final checkpoint schema is bootstrapped here so the agent never
-- needs its own database or setup container.
CREATE TABLE IF NOT EXISTS polytrade_agent.checkpoint_migrations (
    v integer PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS polytrade_agent.checkpoints (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    parent_checkpoint_id text,
    type text,
    checkpoint jsonb NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS polytrade_agent.checkpoint_blobs (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    channel text NOT NULL,
    version text NOT NULL,
    type text NOT NULL,
    blob bytea,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE TABLE IF NOT EXISTS polytrade_agent.checkpoint_writes (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    task_id text NOT NULL,
    task_path text NOT NULL DEFAULT '',
    idx integer NOT NULL,
    channel text NOT NULL,
    type text,
    blob bytea NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx
    ON polytrade_agent.checkpoints(thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx
    ON polytrade_agent.checkpoint_blobs(thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx
    ON polytrade_agent.checkpoint_writes(thread_id);

INSERT INTO polytrade_agent.checkpoint_migrations (v)
SELECT version FROM generate_series(0, 9) AS version
ON CONFLICT (v) DO NOTHING;

-- Agent prediction scorecard: falsifiable directional calls the agent records
-- about specific markets, graded against Gamma resolution data. principal_id
-- and thread_id are for auth and provenance only and must never appear in any
-- public payload; the public aggregate exposes only the market question, the
-- agent's outcome call, and the resolution.
CREATE TABLE IF NOT EXISTS polytrade_agent.agent_predictions (
    prediction_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id text NOT NULL,
    thread_id uuid,
    condition_id text NOT NULL,
    token_id text,
    market_question text NOT NULL CHECK (char_length(market_question) BETWEEN 1 AND 1000),
    predicted_outcome text NOT NULL CHECK (char_length(predicted_outcome) BETWEEN 1 AND 200),
    confidence numeric(6, 4) CHECK (confidence >= 0 AND confidence <= 1),
    category text,
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'GRADED', 'VOID')),
    graded_outcome text,
    hit boolean,
    void_reason text,
    tags jsonb,
    market_slug text,
    resolution_prices jsonb,
    closed_time timestamptz,
    made_at timestamptz NOT NULL DEFAULT now(),
    graded_at timestamptz,
    grade_attempts integer NOT NULL DEFAULT 0,
    next_grade_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'GRADED') = (graded_outcome IS NOT NULL)),
    CHECK (status <> 'VOID' OR void_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS agent_predictions_pending_idx
    ON polytrade_agent.agent_predictions (next_grade_at, made_at)
    WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS agent_predictions_graded_idx
    ON polytrade_agent.agent_predictions (status, graded_at DESC);
CREATE INDEX IF NOT EXISTS agent_predictions_owner_idx
    ON polytrade_agent.agent_predictions (principal_id, made_at DESC);
-- One open call per (principal, market, outcome): tool retries are no-ops and
-- a pending claim can't be duplicated while it is still ungraded.
CREATE UNIQUE INDEX IF NOT EXISTS agent_predictions_open_claim_idx
    ON polytrade_agent.agent_predictions (principal_id, condition_id, lower(predicted_outcome))
    WHERE status = 'PENDING';

CREATE TABLE IF NOT EXISTS polytrade_backtest.backtest_runs (
    run_id uuid PRIMARY KEY,
    principal_id text NOT NULL,
    market_id text NOT NULL,
    market_question text,
    yes_token_id text,
    no_token_id text,
    resolved_outcome text CHECK (resolved_outcome IN ('YES', 'NO')),
    status text NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'completed', 'failed', 'cancelled')
    ),
    phase text NOT NULL DEFAULT 'queued' CHECK (
        phase IN ('queued', 'fetching', 'simulating', 'saving', 'completed', 'failed', 'cancelled')
    ),
    progress smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    config jsonb NOT NULL,
    idempotency_key text NOT NULL,
    request_hash char(64) NOT NULL,
    dataset_hash char(64),
    result_summary jsonb,
    cancel_requested boolean NOT NULL DEFAULT false,
    failure_code text,
    failure_message text,
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    UNIQUE (principal_id, idempotency_key),
    CHECK (
        (status IN ('queued', 'running') AND completed_at IS NULL)
        OR (status IN ('completed', 'failed', 'cancelled') AND completed_at IS NOT NULL)
    )
);

DROP INDEX IF EXISTS polytrade_backtest.backtest_runs_one_active_owner_idx;
CREATE INDEX IF NOT EXISTS backtest_runs_owner_active_idx
    ON polytrade_backtest.backtest_runs (principal_id, status)
    WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS backtest_runs_owner_created_idx
    ON polytrade_backtest.backtest_runs (principal_id, created_at DESC);
CREATE INDEX IF NOT EXISTS backtest_runs_recovery_idx
    ON polytrade_backtest.backtest_runs (status, heartbeat_at)
    WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS polytrade_backtest.backtest_dispatch_outbox (
    run_id uuid PRIMARY KEY REFERENCES polytrade_backtest.backtest_runs(run_id) ON DELETE CASCADE,
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS backtest_dispatch_ready_idx
    ON polytrade_backtest.backtest_dispatch_outbox (next_attempt_at, created_at)
    WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS polytrade_backtest.backtest_idempotency (
    principal_id text NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash char(64) NOT NULL,
    response jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, operation, idempotency_key)
);

CREATE TABLE IF NOT EXISTS polytrade_backtest.backtest_progress_events (
    event_id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES polytrade_backtest.backtest_runs(run_id) ON DELETE CASCADE,
    phase text NOT NULL CHECK (
        phase IN ('queued', 'fetching', 'simulating', 'saving', 'completed', 'failed', 'cancelled')
    ),
    progress smallint NOT NULL CHECK (progress BETWEEN 0 AND 100),
    message text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS backtest_progress_run_event_idx
    ON polytrade_backtest.backtest_progress_events (run_id, event_id);

CREATE TABLE IF NOT EXISTS polytrade_backtest.backtest_datasets (
    dataset_hash char(64) PRIMARY KEY,
    condition_id text NOT NULL,
    metadata jsonb NOT NULL,
    payload bytea NOT NULL,
    point_count integer NOT NULL CHECK (point_count > 0),
    start_at timestamptz NOT NULL,
    end_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (end_at >= start_at)
);

CREATE INDEX IF NOT EXISTS backtest_datasets_condition_range_idx
    ON polytrade_backtest.backtest_datasets (condition_id, start_at, end_at);

ALTER TABLE polytrade_backtest.backtest_runs
    DROP CONSTRAINT IF EXISTS backtest_runs_dataset_hash_fkey;
ALTER TABLE polytrade_backtest.backtest_runs
    ADD CONSTRAINT backtest_runs_dataset_hash_fkey
    FOREIGN KEY (dataset_hash)
    REFERENCES polytrade_backtest.backtest_datasets(dataset_hash);

CREATE TABLE IF NOT EXISTS polytrade_backtest.backtest_trades (
    run_id uuid NOT NULL REFERENCES polytrade_backtest.backtest_runs(run_id) ON DELETE CASCADE,
    trade_index integer NOT NULL CHECK (trade_index >= 0),
    outcome text NOT NULL CHECK (outcome IN ('YES', 'NO')),
    entry_at timestamptz NOT NULL,
    exit_at timestamptz NOT NULL,
    entry_price numeric(18, 8) NOT NULL,
    exit_price numeric(18, 8) NOT NULL,
    shares numeric(24, 6) NOT NULL,
    entry_fee numeric(24, 5) NOT NULL,
    exit_fee numeric(24, 5) NOT NULL,
    pnl numeric(24, 8) NOT NULL,
    exit_reason text NOT NULL CHECK (
        exit_reason IN ('take_profit', 'stop_loss', 'max_hold', 'settlement')
    ),
    PRIMARY KEY (run_id, trade_index),
    CHECK (exit_at >= entry_at)
);

CREATE TABLE IF NOT EXISTS polytrade_backtest.backtest_metrics (
    run_id uuid PRIMARY KEY REFERENCES polytrade_backtest.backtest_runs(run_id) ON DELETE CASCADE,
    metrics jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS polytrade_backtest.backtest_series (
    run_id uuid PRIMARY KEY REFERENCES polytrade_backtest.backtest_runs(run_id) ON DELETE CASCADE,
    encoding text NOT NULL DEFAULT 'gzip-json-v1' CHECK (encoding = 'gzip-json-v1'),
    payload bytea NOT NULL,
    point_count integer NOT NULL CHECK (point_count > 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
