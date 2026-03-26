#!/usr/bin/env python
"""
Setup script — creates a 'polycode' SCHEMA inside the existing finespresso_db
and initialises all required tables.

This does NOT create a new database — it creates a separate namespace
so our tables never collide with existing ones.

Run once before starting the application:
    python scripts/setup_polycode_db.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2-binary not installed.  Run:  pip install psycopg2-binary")
    sys.exit(1)

# ── Connection URL ────────────────────────────────────────────────────────────
DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://finespresso:mlfpass2026@72.62.114.124:5432/finespresso_db",
)

m = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", DB_URL)
if not m:
    print(f"ERROR: Could not parse DATABASE_URL: {DB_URL}")
    sys.exit(1)

DB_USER, DB_PASS, DB_HOST, DB_PORT, DB_NAME = (
    m.group(1), m.group(2), m.group(3), m.group(4), m.group(5),
)
SCHEMA = "polycode"

# ── SQL — schema + tables ─────────────────────────────────────────────────────
SCHEMA_SQL = f"""
-- Create the polycode schema (leaves all existing tables untouched)
CREATE SCHEMA IF NOT EXISTS {SCHEMA};

-- Enable UUID generation (safe if already enabled)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Work inside the new schema
SET search_path TO {SCHEMA}, public;

-- ── runs ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.runs (
    run_id        UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    query         TEXT          NOT NULL,
    model         VARCHAR(100),
    provider      VARCHAR(50),
    status        VARCHAR(20)   NOT NULL DEFAULT 'running',
    iterations    INT           DEFAULT 0,
    tool_calls    JSONB         DEFAULT '[]',
    started_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    error_message TEXT
);

-- ── trades ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.trades (
    trade_id        VARCHAR(60)     PRIMARY KEY,
    run_id          UUID            REFERENCES {SCHEMA}.runs(run_id) ON DELETE SET NULL,
    market_id       VARCHAR(500)    NOT NULL,
    market_question TEXT,
    trade_side      VARCHAR(10),
    amount          DECIMAL(14, 4)  NOT NULL,
    entry_price     DECIMAL(10, 6)  NOT NULL,
    shares          DECIMAL(16, 6),
    status          VARCHAR(20)     NOT NULL DEFAULT 'OPEN',
    exit_price      DECIMAL(10, 6),
    payout          DECIMAL(14, 4)  DEFAULT 0,
    pnl             DECIMAL(14, 4)  DEFAULT 0,
    period          DATE,
    city            VARCHAR(100),
    signal          VARCHAR(10),
    edge_pct        DECIMAL(8,  4),
    confidence      DECIMAL(8,  4),
    trade_type      VARCHAR(20)     NOT NULL DEFAULT 'paper',   -- paper | backtest | real
    domain          VARCHAR(20)     NOT NULL DEFAULT 'weather', -- weather | earnings | sports
    user_id         UUID,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ── pnl_snapshots ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.pnl_snapshots (
    snapshot_id     SERIAL          PRIMARY KEY,
    run_id          UUID            REFERENCES {SCHEMA}.runs(run_id) ON DELETE SET NULL,
    snapshot_time   TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    total_invested  DECIMAL(14, 4)  DEFAULT 0,
    total_payout    DECIMAL(14, 4)  DEFAULT 0,
    realized_pnl    DECIMAL(14, 4)  DEFAULT 0,
    unrealized_pnl  DECIMAL(14, 4)  DEFAULT 0,
    open_trades     INT             DEFAULT 0,
    closed_trades   INT             DEFAULT 0,
    win_count       INT             DEFAULT 0,
    loss_count      INT             DEFAULT 0,
    win_rate        DECIMAL(8,  4)  DEFAULT 0,
    total_trades    INT             DEFAULT 0,
    roi_pct         DECIMAL(10, 4)  DEFAULT 0
);

-- ── indexes ─────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_trades_run_id     ON {SCHEMA}.trades(run_id);
CREATE INDEX IF NOT EXISTS idx_trades_status     ON {SCHEMA}.trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_market_id  ON {SCHEMA}.trades(market_id);
CREATE INDEX IF NOT EXISTS idx_trades_created_at ON {SCHEMA}.trades(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_period     ON {SCHEMA}.trades(period);
CREATE INDEX IF NOT EXISTS idx_pnl_run_id        ON {SCHEMA}.pnl_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_pnl_snap_time     ON {SCHEMA}.pnl_snapshots(snapshot_time);
CREATE INDEX IF NOT EXISTS idx_trades_type       ON {SCHEMA}.trades(trade_type);
CREATE INDEX IF NOT EXISTS idx_runs_status       ON {SCHEMA}.runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_started_at   ON {SCHEMA}.runs(started_at);

-- ── chat_conversations ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.chat_conversations (
    thread_id   UUID          PRIMARY KEY,
    user_id     UUID,
    title       VARCHAR(200)  DEFAULT 'New chat',
    created_at  TIMESTAMPTZ   DEFAULT NOW(),
    updated_at  TIMESTAMPTZ   DEFAULT NOW()
);

-- ── chat_messages ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.chat_messages (
    id          BIGSERIAL     PRIMARY KEY,
    thread_id   UUID          NOT NULL REFERENCES {SCHEMA}.chat_conversations(thread_id) ON DELETE CASCADE,
    message_id  UUID          NOT NULL,
    role        VARCHAR(20)   NOT NULL,
    content     TEXT          NOT NULL DEFAULT '',
    metadata    JSONB,
    created_at  TIMESTAMPTZ   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_conv_user     ON {SCHEMA}.chat_conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_conv_updated  ON {SCHEMA}.chat_conversations(updated_at);
CREATE INDEX IF NOT EXISTS idx_chat_msg_thread    ON {SCHEMA}.chat_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_chat_msg_created   ON {SCHEMA}.chat_messages(created_at);

-- ── users ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.users (
    id              SERIAL          PRIMARY KEY,
    user_id         UUID            UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    email           VARCHAR(255)    UNIQUE NOT NULL,
    password_hash   VARCHAR(255),
    display_name    VARCHAR(255),
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_user_id ON {SCHEMA}.users(user_id);
CREATE INDEX IF NOT EXISTS idx_users_email   ON {SCHEMA}.users(email);

-- ── password_reset_tokens ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.password_reset_tokens (
    id          SERIAL          PRIMARY KEY,
    user_id     UUID            NOT NULL REFERENCES {SCHEMA}.users(user_id) ON DELETE CASCADE,
    token       VARCHAR(128)    UNIQUE NOT NULL,
    expires_at  TIMESTAMPTZ     NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pw_reset_token ON {SCHEMA}.password_reset_tokens(token);
CREATE INDEX IF NOT EXISTS idx_pw_reset_user  ON {SCHEMA}.password_reset_tokens(user_id);

-- ── Link chat_conversations.user_id to users (optional FK) ────────────────
-- ALTER TABLE {SCHEMA}.chat_conversations
--   ADD CONSTRAINT fk_chat_conv_user FOREIGN KEY (user_id) REFERENCES {SCHEMA}.users(user_id);
"""


def main():
    print(f"\n=== PolyTrade DB Setup ===")
    print(f"Server  : {DB_HOST}:{DB_PORT}")
    print(f"Database: {DB_NAME}")
    print(f"Schema  : {SCHEMA}\n")

    # ── Step 1: connect to existing database ──────────────────────────────
    print("[1/2] Connecting to existing database …")
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=int(DB_PORT),
            user=DB_USER, password=DB_PASS,
            database=DB_NAME,
        )
    except Exception as exc:
        print(f"ERROR: Could not connect to {DB_NAME}: {exc}")
        sys.exit(1)

    print(f"  Connected to '{DB_NAME}'")

    # ── Step 2: create schema + tables ────────────────────────────────────
    print(f"\n[2/2] Creating schema '{SCHEMA}' and tables …")
    cur = conn.cursor()
    cur.execute(SCHEMA_SQL)
    conn.commit()
    cur.close()
    conn.close()

    print(f"  Schema '{SCHEMA}' ready")
    print(f"  Tables: {SCHEMA}.runs, {SCHEMA}.trades, {SCHEMA}.pnl_snapshots")
    print(f"\n  Done!\n")


if __name__ == "__main__":
    main()
