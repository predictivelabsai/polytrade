import { randomUUID } from "node:crypto";

import type {
  PaperStrategy,
  PaperStrategyAction,
  PaperStrategyEvent,
  PaperStrategySnapshot,
  PaperStrategyStartRequest,
} from "@polytrade/contracts";
import { Decimal } from "decimal.js";
import type { Pool, PoolClient } from "pg";

import { money, price, shares } from "./paper-pricing.js";

export interface PaperStrategyStartInput {
  strategyId: string;
  principalId: string;
  idempotencyKey: string;
  requestHash: string;
  request: PaperStrategyStartRequest;
  marketQuestion: string;
  outcome: string;
  minimumOrderSize: string;
  startedAt: Date;
}

export type PaperStrategyStartResult =
  | { state: "created" | "replayed"; strategy: PaperStrategy }
  | { state: "key_mismatch" }
  | { state: "already_running"; strategy: PaperStrategy };

export interface PaperStrategyClaim extends PaperStrategy {
  principalId: string;
  minimumOrderSize: string;
  scanId: string;
  leaseOwner: string;
}

export interface PaperStrategyScanResult {
  action: Exclude<PaperStrategyAction, "STARTED" | "STOPPED">;
  message: string;
  side?: "BUY" | "SELL";
  price?: string;
  fillId?: string;
}

export interface PaperStrategyStore {
  start(input: PaperStrategyStartInput): Promise<PaperStrategyStartResult>;
  snapshot(principalId: string, eventLimit?: number): Promise<PaperStrategySnapshot>;
  stop(principalId: string, stoppedAt: Date): Promise<PaperStrategySnapshot>;
  claimDue(owner: string, now: Date, leaseUntil: Date, limit: number): Promise<PaperStrategyClaim[]>;
  completeClaim(claim: PaperStrategyClaim, result: PaperStrategyScanResult, scannedAt: Date, nextScanAt: Date): Promise<boolean>;
  failClaim(claim: PaperStrategyClaim, message: string, failedAt: Date): Promise<boolean>;
  /** Bound strategy event history: keep the newest rows per strategy and drop rows older than maxAgeDays. */
  pruneEvents(options: { retainPerStrategy: number; maxAgeDays: number }): Promise<void>;
}

export class PostgresPaperStrategyStore implements PaperStrategyStore {
  constructor(private readonly pool: Pool) {}

  async start(input: PaperStrategyStartInput): Promise<PaperStrategyStartResult> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      await ensureAccount(client, input.principalId);
      await client.query(
        "SELECT principal_id FROM polytrade.paper_accounts WHERE principal_id=$1 FOR UPDATE",
        [input.principalId],
      );

      const replay = await client.query(
        "SELECT * FROM polytrade.paper_strategies WHERE principal_id=$1 AND idempotency_key=$2",
        [input.principalId, input.idempotencyKey],
      );
      const existing = replay.rows[0] as Record<string, unknown> | undefined;
      if (existing) {
        await client.query("COMMIT");
        return String(existing.request_hash) === input.requestHash
          ? { state: "replayed", strategy: strategyFromRow(existing) }
          : { state: "key_mismatch" };
      }

      const running = await client.query(
        "SELECT * FROM polytrade.paper_strategies WHERE principal_id=$1 AND status='RUNNING' FOR UPDATE",
        [input.principalId],
      );
      const active = running.rows[0] as Record<string, unknown> | undefined;
      if (active) {
        await client.query("COMMIT");
        return { state: "already_running", strategy: strategyFromRow(active) };
      }

      const result = await client.query(
        `INSERT INTO polytrade.paper_strategies
         (strategy_id, principal_id, idempotency_key, request_hash, condition_id, token_id,
          market_question, outcome, minimum_order_size, entry_price, exit_price,
          shares_per_order, max_position, interval_seconds, status, last_action,
          last_message, next_scan_at, started_at, updated_at)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'RUNNING','STARTED',$15,$16,$16,$16)
         RETURNING *`,
        [input.strategyId, input.principalId, input.idempotencyKey, input.requestHash,
          input.request.conditionId, input.request.tokenId, input.marketQuestion, input.outcome,
          input.minimumOrderSize, input.request.entryPrice, input.request.exitPrice,
          input.request.sharesPerOrder, input.request.maxPosition, input.request.intervalSeconds,
          "Strategy started. Waiting for the background runner.", input.startedAt],
      );
      await client.query(
        `INSERT INTO polytrade.paper_strategy_events
         (event_id, strategy_id, action, message, created_at)
         VALUES ($1,$2,'STARTED',$3,$4)`,
        [randomUUID(), input.strategyId,
          `Watching ${input.outcome}: buy at or below ${input.request.entryPrice}, sell at or above ${input.request.exitPrice}.`,
          input.startedAt],
      );
      await client.query("COMMIT");
      return { state: "created", strategy: strategyFromRow(result.rows[0] as Record<string, unknown>) };
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      throw error;
    } finally {
      client.release();
    }
  }

  async snapshot(principalId: string, eventLimit = 20): Promise<PaperStrategySnapshot> {
    const strategyResult = await this.pool.query(
      `SELECT * FROM polytrade.paper_strategies
       WHERE principal_id=$1
       ORDER BY started_at DESC, strategy_id DESC
       LIMIT 1`,
      [principalId],
    );
    const row = strategyResult.rows[0] as Record<string, unknown> | undefined;
    if (!row) return { strategy: null, events: [] };
    const events = await this.pool.query(
      `SELECT * FROM polytrade.paper_strategy_events
       WHERE strategy_id=$1
       ORDER BY created_at DESC, event_id DESC
       LIMIT $2`,
      [row.strategy_id, eventLimit],
    );
    return {
      strategy: strategyFromRow(row),
      events: events.rows.map((event: Record<string, unknown>) => eventFromRow(event)),
    };
  }

  async stop(principalId: string, stoppedAt: Date): Promise<PaperStrategySnapshot> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const result = await client.query(
        `UPDATE polytrade.paper_strategies
         SET status='STOPPED', last_action='STOPPED', last_message=$2,
             next_scan_at=NULL, scan_id=NULL, lease_owner=NULL, lease_until=NULL,
             stopped_at=$3, updated_at=$3
         WHERE strategy_id=(
           SELECT strategy_id FROM polytrade.paper_strategies
           WHERE principal_id=$1 AND status='RUNNING'
           ORDER BY started_at DESC LIMIT 1 FOR UPDATE
         )
         RETURNING strategy_id`,
        [principalId, "Strategy stopped by the user.", stoppedAt],
      );
      const strategyId = result.rows[0]?.strategy_id as string | undefined;
      if (strategyId) {
        await client.query(
          `INSERT INTO polytrade.paper_strategy_events
           (event_id, strategy_id, action, message, created_at)
           VALUES ($1,$2,'STOPPED',$3,$4)`,
          [randomUUID(), strategyId, "Strategy stopped by the user.", stoppedAt],
        );
      }
      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      throw error;
    } finally {
      client.release();
    }
    return this.snapshot(principalId);
  }

  async claimDue(owner: string, now: Date, leaseUntil: Date, limit: number): Promise<PaperStrategyClaim[]> {
    const result = await this.pool.query(
      `WITH due AS (
         SELECT strategy_id
         FROM polytrade.paper_strategies
         WHERE status='RUNNING' AND next_scan_at<=$2
           AND (lease_until IS NULL OR lease_until<=$2)
         ORDER BY next_scan_at, strategy_id
         FOR UPDATE SKIP LOCKED
         LIMIT $4
       )
       UPDATE polytrade.paper_strategies AS strategy
       SET lease_owner=$1, lease_until=$3, scan_id=COALESCE(strategy.scan_id, gen_random_uuid()), updated_at=$2
       FROM due
       WHERE strategy.strategy_id=due.strategy_id
       RETURNING strategy.*`,
      [owner, now, leaseUntil, limit],
    );
    return result.rows.map((row: Record<string, unknown>) => claimFromRow(row));
  }

  async completeClaim(
    claim: PaperStrategyClaim,
    result: PaperStrategyScanResult,
    scannedAt: Date,
    nextScanAt: Date,
  ): Promise<boolean> {
    const client = await this.pool.connect();
    const eventMessage = result.message.slice(0, 2_000);
    try {
      await client.query("BEGIN");
      const update = await client.query(
        `UPDATE polytrade.paper_strategies
         SET orders_placed=orders_placed+$4,
             scans_completed=scans_completed+1,
             last_action=$5, last_message=$6, last_quote_side=$7, last_quote_price=$8,
             last_scanned_at=$9, next_scan_at=$10,
             scan_id=NULL, lease_owner=NULL, lease_until=NULL, updated_at=$9
         WHERE strategy_id=$1 AND status='RUNNING' AND scan_id=$2 AND lease_owner=$3
         RETURNING strategy_id`,
        [claim.strategyId, claim.scanId, claim.leaseOwner,
          result.action === "BUY" || result.action === "SELL" ? 1 : 0,
          result.action, eventMessage, result.side ?? null, result.price ?? null,
          scannedAt, nextScanAt],
      );
      if ((update.rowCount ?? 0) === 0) {
        await client.query("ROLLBACK");
        return false;
      }
      await client.query(
        `INSERT INTO polytrade.paper_strategy_events
         (event_id, strategy_id, scan_id, action, message, side, price, fill_id, created_at)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
         ON CONFLICT (strategy_id, scan_id) DO NOTHING`,
        [randomUUID(), claim.strategyId, claim.scanId, result.action, eventMessage,
          result.side ?? null, result.price ?? null, result.fillId ?? null, scannedAt],
      );
      await client.query("COMMIT");
      return true;
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      throw error;
    } finally {
      client.release();
    }
  }

  async failClaim(claim: PaperStrategyClaim, message: string, failedAt: Date): Promise<boolean> {
    const client = await this.pool.connect();
    const eventMessage = message.slice(0, 2_000);
    try {
      await client.query("BEGIN");
      const update = await client.query(
        `UPDATE polytrade.paper_strategies
         SET status='FAILED', scans_completed=scans_completed+1,
             last_action='ERROR', last_message=$4, last_scanned_at=$5,
             next_scan_at=NULL, scan_id=NULL, lease_owner=NULL, lease_until=NULL,
             stopped_at=$5, updated_at=$5
         WHERE strategy_id=$1 AND status='RUNNING' AND scan_id=$2 AND lease_owner=$3
         RETURNING strategy_id`,
        [claim.strategyId, claim.scanId, claim.leaseOwner, eventMessage, failedAt],
      );
      if ((update.rowCount ?? 0) === 0) {
        await client.query("ROLLBACK");
        return false;
      }
      await client.query(
        `INSERT INTO polytrade.paper_strategy_events
         (event_id, strategy_id, scan_id, action, message, created_at)
         VALUES ($1,$2,$3,'ERROR',$4,$5)
         ON CONFLICT (strategy_id, scan_id) DO NOTHING`,
        [randomUUID(), claim.strategyId, claim.scanId, eventMessage, failedAt],
      );
      await client.query("COMMIT");
      return true;
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      throw error;
    } finally {
      client.release();
    }
  }

  async pruneEvents({ retainPerStrategy, maxAgeDays }: { retainPerStrategy: number; maxAgeDays: number }): Promise<void> {
    await this.pool.query(
      `DELETE FROM polytrade.paper_strategy_events
       WHERE created_at < now() - make_interval(days => $2::int)
          OR event_id IN (
            SELECT event_id FROM (
              SELECT event_id,
                     row_number() OVER (PARTITION BY strategy_id ORDER BY created_at DESC, event_id DESC) AS position
              FROM polytrade.paper_strategy_events
            ) ranked
            WHERE ranked.position > $1::int
          )`,
      [retainPerStrategy, maxAgeDays],
    );
  }
}

async function ensureAccount(client: PoolClient, principalId: string): Promise<void> {
  await client.query(
    `INSERT INTO polytrade.paper_accounts (principal_id)
     VALUES ($1)
     ON CONFLICT (principal_id) DO NOTHING`,
    [principalId],
  );
}

function strategyFromRow(row: Record<string, unknown>): PaperStrategy {
  return {
    strategyId: String(row.strategy_id),
    conditionId: String(row.condition_id),
    tokenId: String(row.token_id),
    marketQuestion: String(row.market_question),
    outcome: String(row.outcome),
    entryPrice: price(new Decimal(String(row.entry_price))),
    exitPrice: price(new Decimal(String(row.exit_price))),
    sharesPerOrder: shares(new Decimal(String(row.shares_per_order))),
    maxPosition: shares(new Decimal(String(row.max_position))),
    intervalSeconds: Number(row.interval_seconds),
    status: String(row.status) as PaperStrategy["status"],
    ordersPlaced: Number(row.orders_placed),
    scansCompleted: Number(row.scans_completed),
    lastAction: String(row.last_action) as PaperStrategy["lastAction"],
    lastMessage: String(row.last_message),
    lastQuoteSide: row.last_quote_side ? String(row.last_quote_side) as "BUY" | "SELL" : null,
    lastQuotePrice: row.last_quote_price === null || row.last_quote_price === undefined
      ? null
      : price(new Decimal(String(row.last_quote_price))),
    lastScannedAt: nullableIso(row.last_scanned_at),
    nextScanAt: nullableIso(row.next_scan_at),
    startedAt: iso(row.started_at),
    stoppedAt: nullableIso(row.stopped_at),
    updatedAt: iso(row.updated_at),
  };
}

function claimFromRow(row: Record<string, unknown>): PaperStrategyClaim {
  return {
    ...strategyFromRow(row),
    principalId: String(row.principal_id),
    minimumOrderSize: money(new Decimal(String(row.minimum_order_size))),
    scanId: String(row.scan_id),
    leaseOwner: String(row.lease_owner),
  };
}

function eventFromRow(row: Record<string, unknown>): PaperStrategyEvent {
  return {
    eventId: String(row.event_id),
    action: String(row.action) as PaperStrategyEvent["action"],
    message: String(row.message),
    side: row.side ? String(row.side) as "BUY" | "SELL" : null,
    price: row.price === null || row.price === undefined
      ? null
      : price(new Decimal(String(row.price))),
    fillId: row.fill_id ? String(row.fill_id) : null,
    createdAt: iso(row.created_at),
  };
}

function nullableIso(value: unknown): string | null {
  return value === null || value === undefined ? null : iso(value);
}

function iso(value: unknown): string {
  return (value instanceof Date ? value : new Date(String(value))).toISOString();
}
