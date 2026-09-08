import assert from "node:assert/strict";

import pg from "pg";

import { bootstrapDatabase } from "../src/bootstrap.js";

const connectionString = process.env.DATABASE_URL;
if (!connectionString) throw new Error("DATABASE_URL is required for the bootstrap integration test");

// Concurrent calls prove the advisory lock serializes initialization; the
// third call proves the final bootstrap remains idempotent after completion.
await Promise.all([
  bootstrapDatabase(connectionString),
  bootstrapDatabase(connectionString),
]);
await bootstrapDatabase(connectionString);

const client = new pg.Client({
  connectionString,
  options: "-c search_path=polytrade,public",
});
await client.connect();

try {
  const invariants = await client.query<{
    database_name: string;
    gateway_tables: string;
    agent_tables: string;
    backtest_tables: string;
    public_tables: string;
    owned_schemas: string;
    trigger_count: string;
    paper_trigger_count: string;
    paper_strategy_trigger_count: string;
    unqualified_audit: string;
  }>(`
    SELECT
      current_database() AS database_name,
      (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'polytrade') AS gateway_tables,
      (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'polytrade_agent') AS agent_tables,
      (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'polytrade_backtest') AS backtest_tables,
      (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public') AS public_tables,
      (SELECT count(*) FROM information_schema.schemata
       WHERE schema_name IN ('polytrade', 'polytrade_agent', 'polytrade_backtest')
         AND schema_owner = current_user) AS owned_schemas,
      (SELECT count(*) FROM pg_trigger WHERE tgname = 'trading_audit_no_update') AS trigger_count,
      (SELECT count(*) FROM pg_trigger WHERE tgname = 'paper_fills_no_update') AS paper_trigger_count,
      (SELECT count(*) FROM pg_trigger WHERE tgname = 'paper_strategy_events_no_update') AS paper_strategy_trigger_count,
      to_regclass('trading_audit')::text AS unqualified_audit
  `);
  const row = invariants.rows[0];
  assert.ok(row?.database_name, "current_database() was empty");
  assert.deepEqual(row, {
    database_name: row.database_name,
    gateway_tables: "12",
    agent_tables: "6",
    backtest_tables: "8",
    public_tables: "0",
    owned_schemas: "3",
    trigger_count: "1",
    paper_trigger_count: "1",
    paper_strategy_trigger_count: "1",
    unqualified_audit: "trading_audit",
  });

  const inserted = await client.query<{ id: string }>(`
    INSERT INTO polytrade.trading_audit (principal_id, action)
    VALUES ('bootstrap:test', 'created')
    RETURNING id
  `);
  const auditId = inserted.rows[0]?.id;
  assert.ok(auditId);

  let updateRejected = false;
  try {
    await client.query("UPDATE polytrade.trading_audit SET action = 'changed' WHERE id = $1", [auditId]);
  } catch (error) {
    assert.match(error instanceof Error ? error.message : String(error), /trading_audit is append-only/);
    updateRejected = true;
  }
  assert.equal(updateRejected, true, "append-only audit trigger accepted an update");
} finally {
  await client.end();
}
