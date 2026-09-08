-- Tenant-safe activity, per-user xAI BYOK, daily query allowance, and cost logs.
-- Credential ciphertext is isolated from all logging tables.
BEGIN;

ALTER TABLE polycode.users
    ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS polycode.user_provider_credentials (
    user_id UUID NOT NULL REFERENCES polycode.users(user_id) ON DELETE CASCADE,
    provider VARCHAR(32) NOT NULL,
    api_key_enc BYTEA NOT NULL,
    api_key_hint VARCHAR(32) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, provider)
);

CREATE TABLE IF NOT EXISTS polycode.user_ai_daily_allowances (
    -- May also be a stable service-principal UUID, so no users FK here.
    user_id UUID NOT NULL,
    usage_date DATE NOT NULL DEFAULT CURRENT_DATE,
    platform_queries_used INTEGER NOT NULL DEFAULT 0 CHECK (platform_queries_used >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, usage_date)
);

CREATE TABLE IF NOT EXISTS polycode.user_logging (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID UNIQUE REFERENCES polycode.chat_runs(run_id) ON DELETE SET NULL,
    user_id UUID NOT NULL,
    thread_id UUID,
    request_text TEXT NOT NULL DEFAULT '',
    response_text TEXT,
    agent_framework VARCHAR(64),
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    error TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT user_logging_status_check
        CHECK (status IN ('pending','completed','failed','blocked','cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_user_logging_owner_created
    ON polycode.user_logging(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS polycode.llm_usage_logging (
    usage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    thread_id UUID,
    request_id VARCHAR(128),
    job_id VARCHAR(128),
    agent_framework VARCHAR(64) NOT NULL,
    provider VARCHAR(32) NOT NULL,
    model_name VARCHAR(128) NOT NULL,
    funding_source VARCHAR(16) NOT NULL,
    input_tokens BIGINT NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens BIGINT NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    total_tokens BIGINT NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    estimated_cost_usd NUMERIC(14,6) NOT NULL DEFAULT 0 CHECK (estimated_cost_usd >= 0),
    usage_quality VARCHAR(16) NOT NULL DEFAULT 'estimated',
    status VARCHAR(24) NOT NULL DEFAULT 'completed',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT llm_usage_funding_check CHECK (funding_source IN ('platform','user_byok')),
    CONSTRAINT llm_usage_quality_check CHECK (usage_quality IN ('measured','estimated','unavailable'))
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_owner_created
    ON polycode.llm_usage_logging(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_daily_platform
    ON polycode.llm_usage_logging(created_at, funding_source, status);

CREATE TABLE IF NOT EXISTS polycode.agent_logging (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    agent_framework VARCHAR(64) NOT NULL DEFAULT 'system',
    operation_type VARCHAR(32) NOT NULL,
    job_id VARCHAR(128),
    run_id VARCHAR(128),
    status VARCHAR(32) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    source_table VARCHAR(32) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_agent_logging_source UNIQUE (source_table, source_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_logging_owner_created
    ON polycode.agent_logging(user_id, created_at DESC);

CREATE OR REPLACE FUNCTION polycode.sync_chat_agent_logging()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO polycode.agent_logging
      (user_id, agent_framework, operation_type, job_id, run_id, status,
       details, error, source_table, source_id, started_at, completed_at,
       created_at, updated_at)
    VALUES
      (NEW.user_id, COALESCE(NEW.agent_framework, 'deepagents'), 'chat',
       NEW.run_id::text, NEW.run_id::text, NEW.status,
       jsonb_strip_nulls(jsonb_build_object('agent_name', NEW.agent_name,
         'requested_runtime', NEW.requested_runtime)), NEW.error_message,
       'chat_runs', NEW.run_id::text, NEW.started_at, NEW.finished_at,
       NEW.started_at, NOW())
    ON CONFLICT (source_table, source_id) DO UPDATE SET
      agent_framework=EXCLUDED.agent_framework,status=EXCLUDED.status,
      details=EXCLUDED.details,error=EXCLUDED.error,
      completed_at=EXCLUDED.completed_at,updated_at=NOW();
    RETURN NEW;
EXCEPTION WHEN OTHERS THEN RETURN NEW;
END; $$;

DROP TRIGGER IF EXISTS trg_chat_agent_logging ON polycode.chat_runs;
CREATE TRIGGER trg_chat_agent_logging AFTER INSERT OR UPDATE ON polycode.chat_runs
FOR EACH ROW EXECUTE FUNCTION polycode.sync_chat_agent_logging();

CREATE OR REPLACE FUNCTION polycode.sync_run_agent_logging()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO polycode.agent_logging
      (user_id, agent_framework, operation_type, job_id, run_id, status,
       details, error, source_table, source_id, started_at, completed_at,
       created_at, updated_at)
    VALUES
      (CASE WHEN NEW.principal_id ~* '^[0-9a-f-]{36}$' THEN NEW.principal_id::uuid ELSE NULL END,
       COALESCE(NEW.agent_framework, 'system'), COALESCE(NEW.source, 'run'),
       NEW.run_id::text, NEW.run_id::text, NEW.status,
       jsonb_strip_nulls(jsonb_build_object('query', LEFT(NEW.query, 500), 'agent_name', NEW.agent_name)),
       NEW.error_message, 'runs', NEW.run_id::text, NEW.started_at, NEW.finished_at,
       NEW.started_at, NOW())
    ON CONFLICT (source_table, source_id) DO UPDATE SET
      agent_framework=EXCLUDED.agent_framework, status=EXCLUDED.status,
      details=EXCLUDED.details, error=EXCLUDED.error,
      completed_at=EXCLUDED.completed_at, updated_at=NOW();
    RETURN NEW;
EXCEPTION WHEN OTHERS THEN RETURN NEW;
END; $$;

DROP TRIGGER IF EXISTS trg_run_agent_logging ON polycode.runs;
CREATE TRIGGER trg_run_agent_logging AFTER INSERT OR UPDATE ON polycode.runs
FOR EACH ROW EXECUTE FUNCTION polycode.sync_run_agent_logging();

CREATE OR REPLACE FUNCTION polycode.sync_trade_agent_logging()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO polycode.agent_logging
      (user_id, agent_framework, operation_type, job_id, run_id, status,
       details, source_table, source_id, started_at, completed_at, created_at, updated_at)
    VALUES
      (NEW.user_id, COALESCE(NEW.agent_framework, 'system'), COALESCE(NEW.trade_type, 'trade'),
       NEW.trade_id, NEW.run_id::text, NEW.status,
       jsonb_strip_nulls(jsonb_build_object('domain', NEW.domain, 'city', NEW.city,
         'market_question', LEFT(NEW.market_question, 500), 'agent_name', NEW.agent_name)),
       'trades', NEW.trade_id, NEW.created_at,
       CASE WHEN NEW.status IN ('CLOSED','SETTLED','FAILED') THEN NEW.updated_at END,
       NEW.created_at, NEW.updated_at)
    ON CONFLICT (source_table, source_id) DO UPDATE SET
      agent_framework=EXCLUDED.agent_framework, status=EXCLUDED.status,
      details=EXCLUDED.details, completed_at=EXCLUDED.completed_at,
      updated_at=EXCLUDED.updated_at;
    RETURN NEW;
EXCEPTION WHEN OTHERS THEN RETURN NEW;
END; $$;

DROP TRIGGER IF EXISTS trg_trade_agent_logging ON polycode.trades;
CREATE TRIGGER trg_trade_agent_logging AFTER INSERT OR UPDATE ON polycode.trades
FOR EACH ROW EXECUTE FUNCTION polycode.sync_trade_agent_logging();

COMMIT;
