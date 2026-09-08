# Activity, AI usage, and daily limits

PolyTrade records tenant-scoped chat activity, agent jobs, token usage, and
configured cost estimates in the `polycode` schema. Credential values are never
written to logging tables.

## User behavior

- Signed-in users receive five platform-funded AI requests per database day.
- The allowance resets automatically because counters are keyed by `CURRENT_DATE`.
- The shared platform-funded AI budget defaults to $5 per day.
- `/usage` reports the current allowance and estimated spend without calling a model.
- Raw commands such as `poly:weather London` remain deterministic and free of LLM cost.
- Prefixed commands such as `/hermes poly:weather London` call the selected agent for
  analysis and therefore consume one funded query.
- A saved xAI key funds DeepAgents calls. Hermes remains platform-funded because its
  private sidecar owns its provider connection.

## Deployment variables

```text
ENCRYPTION_KEY=<Fernet key>
FREE_PLATFORM_QUERY_LIMIT=5
PLATFORM_LLM_DAILY_BUDGET_USD=5
LLM_INPUT_USD_PER_MILLION=1.25
LLM_OUTPUT_USD_PER_MILLION=2.50
HERMES_INPUT_USD_PER_MILLION=2
HERMES_OUTPUT_USD_PER_MILLION=6
```

Generate the encryption key once and keep it stable:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Apply the idempotent schema migration before deploying application containers:

```bash
python scripts/setup_polycode_db.py
```

To grant an existing account administrator visibility, run this with the desired
email substituted as a SQL parameter/value:

```sql
UPDATE polycode.users SET is_admin=TRUE WHERE email='admin@example.com';
```

Administrators can filter all tenants at `/admin/logging`. Other signed-in users
are forcibly limited to their own rows. Users manage their encrypted xAI key at
`/settings`.

## Tables

- `polycode.user_logging`: redacted requests, responses, runtime, status and safe errors.
- `polycode.agent_logging`: chat, backtest, paper-trade and trade lifecycle records.
- `polycode.llm_usage_logging`: provider/model, funding source, tokens, cost and quality.
- `polycode.user_ai_daily_allowances`: atomic daily platform query counters.
- `polycode.user_provider_credentials`: encrypted credentials and display-safe hints only.
