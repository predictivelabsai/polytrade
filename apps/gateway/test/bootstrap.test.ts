import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const schemaPath = fileURLToPath(new URL("../bootstrap/schema.sql", import.meta.url));

describe("empty-database bootstrap", () => {
  it("creates only the three application schemas without databases or roles", async () => {
    const sql = await readFile(schemaPath, "utf8");

    expect(sql).toMatch(/CREATE SCHEMA IF NOT EXISTS polytrade;/);
    expect(sql).toMatch(/CREATE SCHEMA IF NOT EXISTS polytrade_agent;/);
    expect(sql).toMatch(/CREATE SCHEMA IF NOT EXISTS polytrade_backtest;/);
    expect(sql).not.toMatch(/CREATE\s+DATABASE/i);
    expect(sql).not.toMatch(/CREATE\s+(USER|ROLE)/i);
    expect(sql).not.toMatch(/polycode/i);
    expect(sql).not.toMatch(/CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+public\./i);
  });

  it("adds chat titles compatibly for existing agent thread tables", async () => {
    const sql = await readFile(schemaPath, "utf8");

    expect(sql).toMatch(/ALTER TABLE polytrade_agent\.agent_threads\s+ADD COLUMN IF NOT EXISTS title text NOT NULL DEFAULT 'New chat'/);
    expect(sql).toMatch(/CHECK \(char_length\(title\) BETWEEN 1 AND 80\)/);
  });

  it("replaces the single-active-backtest index with bounded multi-run support", async () => {
    const sql = await readFile(schemaPath, "utf8");

    expect(sql).toMatch(/DROP INDEX IF EXISTS polytrade_backtest\.backtest_runs_one_active_owner_idx/);
    expect(sql).toMatch(/CREATE INDEX IF NOT EXISTS backtest_runs_owner_active_idx/);
    expect(sql).not.toMatch(/CREATE UNIQUE INDEX IF NOT EXISTS backtest_runs_one_active_owner_idx/);
  });

  it("bootstraps an immutable paper ledger without a reset path", async () => {
    const sql = await readFile(schemaPath, "utf8");

    expect(sql).toMatch(/CREATE TABLE IF NOT EXISTS polytrade\.paper_accounts/);
    expect(sql).toMatch(/CHECK \(initial_cash = 10000\.000000\)/);
    expect(sql).toMatch(/CREATE TABLE IF NOT EXISTS polytrade\.paper_positions/);
    expect(sql).toMatch(/CREATE TABLE IF NOT EXISTS polytrade\.paper_fills/);
    expect(sql).toMatch(/CREATE TRIGGER paper_fills_no_update/);
    expect(sql).toMatch(/CREATE TABLE IF NOT EXISTS polytrade\.paper_strategies/);
    expect(sql).toMatch(/CREATE UNIQUE INDEX IF NOT EXISTS paper_strategies_one_running_owner_idx/);
    expect(sql).toMatch(/CREATE TABLE IF NOT EXISTS polytrade\.paper_strategy_events/);
    expect(sql).toMatch(/CREATE TRIGGER paper_strategy_events_no_update/);
  });

  it("bootstraps opt-in paper share links keyed to the paper account", async () => {
    const sql = await readFile(schemaPath, "utf8");

    expect(sql).toMatch(/CREATE TABLE IF NOT EXISTS polytrade\.paper_share_links/);
    expect(sql).toMatch(/share_token text NOT NULL UNIQUE CHECK \(share_token ~ '\^\[A-Za-z0-9_-\]\{32,64\}\$'\)/);
    expect(sql).toMatch(/CREATE INDEX IF NOT EXISTS paper_share_links_token_idx/);
  });
});
