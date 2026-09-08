-- Record which chat runtime initiated every chat, backtest, paper, or trade run.
BEGIN;

ALTER TABLE polycode.chat_runs
    ADD COLUMN IF NOT EXISTS agent_framework VARCHAR(32) NOT NULL DEFAULT 'deepagents',
    ADD COLUMN IF NOT EXISTS agent_name VARCHAR(80) NOT NULL DEFAULT 'DeepAgents',
    ADD COLUMN IF NOT EXISTS requested_runtime VARCHAR(32);

ALTER TABLE polycode.runs
    ADD COLUMN IF NOT EXISTS agent_framework VARCHAR(32) NOT NULL DEFAULT 'system',
    ADD COLUMN IF NOT EXISTS agent_name VARCHAR(80) NOT NULL DEFAULT 'Polytrade';

ALTER TABLE polycode.trades
    ADD COLUMN IF NOT EXISTS agent_framework VARCHAR(32) NOT NULL DEFAULT 'system',
    ADD COLUMN IF NOT EXISTS agent_name VARCHAR(80) NOT NULL DEFAULT 'Polytrade';

CREATE INDEX IF NOT EXISTS idx_chat_runs_agent_started
    ON polycode.chat_runs(agent_framework, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_agent_started
    ON polycode.runs(agent_framework, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_agent_created
    ON polycode.trades(agent_framework, created_at DESC);

COMMIT;

