import type { AlertChannelKind, AlertEventKind, PaperStrategyAction } from "@polytrade/contracts";
import type { Pool } from "pg";

export interface AlertChannelRecord {
  channelId: string;
  principalId: string;
  kind: AlertChannelKind;
  label: string;
  encryptedTarget: string;
  targetHint: string;
  eventKinds: AlertEventKind[];
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface AlertDeliveryContext {
  marketQuestion: string | null;
  outcome: string | null;
  side: "BUY" | "SELL" | null;
  price: string | null;
}

export interface AlertDeliveryRecord {
  deliveryId: string;
  channelId: string;
  /** Present on claimed rows: the channel owner, needed for decrypt association data. */
  principalId?: string;
  /** Joined from alert_channels for claimed and listed rows. */
  channelLabel: string;
  channelKind: AlertChannelKind;
  /** Present on claimed rows so delivery never needs a second channel read. */
  encryptedTarget?: string;
  eventSeq: number;
  action: PaperStrategyAction;
  message: string;
  context: AlertDeliveryContext;
  status: "pending" | "delivered" | "failed";
  attempts: number;
  maxAttempts: number;
  lastError: string | null;
  createdAt: string;
  deliveredAt: string | null;
}

export interface AlertStore {
  listChannels(principalId: string): Promise<AlertChannelRecord[]>;
  getChannel(principalId: string, channelId: string): Promise<AlertChannelRecord | undefined>;
  countChannels(principalId: string): Promise<number>;
  createChannel(channel: AlertChannelRecord): Promise<void>;
  deleteChannel(principalId: string, channelId: string): Promise<boolean>;
  fanOutNewEvents(now: Date, limit: number): Promise<number>;
  claimDeliveries(owner: string, now: Date, leaseUntil: Date, limit: number): Promise<AlertDeliveryRecord[]>;
  markDelivered(deliveryId: string, owner: string, deliveredAt: Date): Promise<boolean>;
  markRetry(deliveryId: string, owner: string, error: string, nextAttemptAt: Date, now: Date): Promise<boolean>;
  markExhausted(deliveryId: string, owner: string, error: string, now: Date): Promise<boolean>;
  listDeliveries(principalId: string, limit: number): Promise<AlertDeliveryRecord[]>;
  pruneDeliveries(now: Date, olderThanDays: number): Promise<void>;
}

/**
 * Advisory lock for the alert fan-out cursor. Distinct from bootstrap's
 * 812704001 so schema application and event fan-out never contend.
 */
const FAN_OUT_LOCK_KEY = 812704002;

export class PostgresAlertStore implements AlertStore {
  constructor(private readonly pool: Pool) {}

  async listChannels(principalId: string): Promise<AlertChannelRecord[]> {
    const result = await this.pool.query(
      `SELECT * FROM polytrade.alert_channels
       WHERE principal_id=$1
       ORDER BY created_at DESC, channel_id DESC`,
      [principalId],
    );
    return result.rows.map((row: Record<string, unknown>) => channelFromRow(row));
  }

  async getChannel(principalId: string, channelId: string): Promise<AlertChannelRecord | undefined> {
    const result = await this.pool.query(
      `SELECT * FROM polytrade.alert_channels
       WHERE principal_id=$1 AND channel_id=$2`,
      [principalId, channelId],
    );
    const row = result.rows[0] as Record<string, unknown> | undefined;
    return row ? channelFromRow(row) : undefined;
  }

  async countChannels(principalId: string): Promise<number> {
    const result = await this.pool.query(
      "SELECT count(*)::int AS count FROM polytrade.alert_channels WHERE principal_id=$1",
      [principalId],
    );
    return Number(result.rows[0]?.count ?? 0);
  }

  async createChannel(channel: AlertChannelRecord): Promise<void> {
    await this.pool.query(
      `INSERT INTO polytrade.alert_channels
       (channel_id, principal_id, kind, label, encrypted_target, target_hint, event_kinds, enabled, created_at, updated_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9)`,
      [channel.channelId, channel.principalId, channel.kind, channel.label,
        channel.encryptedTarget, channel.targetHint, channel.eventKinds, channel.enabled,
        channel.createdAt],
    );
  }

  async deleteChannel(principalId: string, channelId: string): Promise<boolean> {
    const result = await this.pool.query(
      "DELETE FROM polytrade.alert_channels WHERE principal_id=$1 AND channel_id=$2",
      [principalId, channelId],
    );
    return (result.rowCount ?? 0) > 0;
  }

  /**
   * Materializes one alert_deliveries row per (subscribed channel × new event)
   * under a global advisory lock, then advances the cursor past the highest
   * event_seq seen. ON CONFLICT DO NOTHING makes re-reads after a crash
   * idempotent, so exactly-once fan-out holds even with multiple replicas.
   */
  async fanOutNewEvents(now: Date, limit: number): Promise<number> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      await client.query("SELECT pg_advisory_xact_lock($1)", [FAN_OUT_LOCK_KEY]);
      const cursor = await client.query(
        `SELECT COALESCE((SELECT last_event_seq FROM polytrade.alert_event_cursor WHERE id), 0) AS last_event_seq`,
      );
      const startSeq = Number(cursor.rows[0]?.last_event_seq ?? 0);

      const events = await client.query(
        `SELECT e.event_seq, e.event_id, e.strategy_id, e.action, e.message, e.side, e.price,
                s.principal_id, s.market_question, s.outcome
         FROM polytrade.paper_strategy_events e
         JOIN polytrade.paper_strategies s ON s.strategy_id=e.strategy_id
         WHERE e.event_seq>$1 AND e.action<>'WAIT'
         ORDER BY e.event_seq
         LIMIT $2`,
        [startSeq, limit],
      );

      for (const event of events.rows as Record<string, unknown>[]) {
        const channels = await client.query(
          `SELECT channel_id FROM polytrade.alert_channels
           WHERE principal_id=$1 AND enabled AND $2=ANY(event_kinds)`,
          [String(event.principal_id), String(event.action)],
        );
        if (channels.rows.length === 0) continue;
        const context = {
          marketQuestion: event.market_question === null || event.market_question === undefined
            ? null
            : String(event.market_question),
          outcome: event.outcome === null || event.outcome === undefined ? null : String(event.outcome),
          side: event.side === null || event.side === undefined ? null : String(event.side),
          price: event.price === null || event.price === undefined ? null : String(event.price),
        };
        await client.query(
          `INSERT INTO polytrade.alert_deliveries
           (delivery_id, channel_id, event_seq, event_id, strategy_id, action, message, context, next_attempt_at, created_at, updated_at)
           SELECT gen_random_uuid(), c.channel_id, $2, $3, $4, $5, $6, $7::jsonb, $8, $8, $8
           FROM unnest($1::uuid[]) AS c(channel_id)
           ON CONFLICT (channel_id, event_seq) DO NOTHING`,
          [
            channels.rows.map((row: Record<string, unknown>) => String(row.channel_id)),
            event.event_seq,
            event.event_id,
            event.strategy_id,
            String(event.action),
            String(event.message),
            JSON.stringify(context),
            now,
          ],
        );
      }

      // Advance past every fetched event, not only the matched ones — the
      // cursor must never stall on a tail of channelless events.
      const maxSeenSeq = (events.rows as Record<string, unknown>[])
        .reduce((max, event) => Math.max(max, Number(event.event_seq)), startSeq);
      await client.query(
        `INSERT INTO polytrade.alert_event_cursor (id, last_event_seq, updated_at)
         VALUES (true, $1, $2)
         ON CONFLICT (id) DO UPDATE SET last_event_seq=$1, updated_at=$2`,
        [maxSeenSeq, now],
      );
      await client.query("COMMIT");
      return events.rows.length;
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      throw error;
    } finally {
      client.release();
    }
  }

  async claimDeliveries(owner: string, now: Date, leaseUntil: Date, limit: number): Promise<AlertDeliveryRecord[]> {
    const result = await this.pool.query(
      `WITH due AS (
         SELECT d.delivery_id, c.principal_id, c.label AS channel_label,
                c.kind AS channel_kind, c.encrypted_target
         FROM polytrade.alert_deliveries d
         JOIN polytrade.alert_channels c ON c.channel_id=d.channel_id
         WHERE d.status='pending' AND d.next_attempt_at<=$2
           AND (d.lease_until IS NULL OR d.lease_until<=$2)
           AND c.enabled
         ORDER BY d.next_attempt_at, d.delivery_id
         FOR UPDATE OF d SKIP LOCKED
         LIMIT $4
       )
       UPDATE polytrade.alert_deliveries AS delivery
       SET attempts=delivery.attempts+1, lease_owner=$1, lease_until=$3, updated_at=$2
       FROM due
       WHERE delivery.delivery_id=due.delivery_id
       RETURNING delivery.*, due.principal_id, due.channel_label, due.channel_kind, due.encrypted_target`,
      [owner, now, leaseUntil, limit],
    );
    return result.rows.map((row: Record<string, unknown>) => deliveryFromRow(row));
  }

  async markDelivered(deliveryId: string, owner: string, deliveredAt: Date): Promise<boolean> {
    const result = await this.pool.query(
      `UPDATE polytrade.alert_deliveries
       SET status='delivered', delivered_at=$3, lease_owner=NULL, lease_until=NULL,
           last_error=NULL, updated_at=$3
       WHERE delivery_id=$1 AND lease_owner=$2 AND status='pending'`,
      [deliveryId, owner, deliveredAt],
    );
    return (result.rowCount ?? 0) > 0;
  }

  async markRetry(deliveryId: string, owner: string, error: string, nextAttemptAt: Date, now: Date): Promise<boolean> {
    const result = await this.pool.query(
      `UPDATE polytrade.alert_deliveries
       SET next_attempt_at=$4, last_error=$5, lease_owner=NULL, lease_until=NULL, updated_at=$3
       WHERE delivery_id=$1 AND lease_owner=$2 AND status='pending'`,
      [deliveryId, owner, now, nextAttemptAt, error],
    );
    return (result.rowCount ?? 0) > 0;
  }

  async markExhausted(deliveryId: string, owner: string, error: string, now: Date): Promise<boolean> {
    const result = await this.pool.query(
      `UPDATE polytrade.alert_deliveries
       SET status='failed', last_error=$4, lease_owner=NULL, lease_until=NULL, updated_at=$3
       WHERE delivery_id=$1 AND lease_owner=$2 AND status='pending'`,
      [deliveryId, owner, now, error],
    );
    return (result.rowCount ?? 0) > 0;
  }

  async listDeliveries(principalId: string, limit: number): Promise<AlertDeliveryRecord[]> {
    const result = await this.pool.query(
      `SELECT d.*, c.label AS channel_label, c.kind AS channel_kind
       FROM polytrade.alert_deliveries d
       JOIN polytrade.alert_channels c ON c.channel_id=d.channel_id
       WHERE c.principal_id=$1
       ORDER BY d.created_at DESC, d.delivery_id DESC
       LIMIT $2`,
      [principalId, limit],
    );
    return result.rows.map((row: Record<string, unknown>) => deliveryFromRow(row));
  }

  async pruneDeliveries(now: Date, olderThanDays: number): Promise<void> {
    await this.pool.query(
      `DELETE FROM polytrade.alert_deliveries
       WHERE created_at < $1
         AND status <> 'pending'`,
      [new Date(now.getTime() - olderThanDays * 86_400_000)],
    );
  }
}

function channelFromRow(row: Record<string, unknown>): AlertChannelRecord {
  return {
    channelId: String(row.channel_id),
    principalId: String(row.principal_id),
    kind: String(row.kind) as AlertChannelKind,
    label: String(row.label),
    encryptedTarget: String(row.encrypted_target),
    targetHint: String(row.target_hint),
    eventKinds: (row.event_kinds as string[]).map((kind) => kind as AlertEventKind),
    enabled: Boolean(row.enabled),
    createdAt: iso(row.created_at),
    updatedAt: iso(row.updated_at),
  };
}

function deliveryFromRow(row: Record<string, unknown>): AlertDeliveryRecord {
  const context = (row.context ?? {}) as Record<string, unknown>;
  return {
    deliveryId: String(row.delivery_id),
    channelId: String(row.channel_id),
    principalId: row.principal_id === null || row.principal_id === undefined
      ? undefined
      : String(row.principal_id),
    channelLabel: row.channel_label === null || row.channel_label === undefined
      ? ""
      : String(row.channel_label),
    channelKind: String(row.channel_kind) as AlertChannelKind,
    encryptedTarget: row.encrypted_target === null || row.encrypted_target === undefined
      ? undefined
      : String(row.encrypted_target),
    eventSeq: Number(row.event_seq),
    action: String(row.action) as PaperStrategyAction,
    message: String(row.message),
    context: {
      marketQuestion: context.marketQuestion === null || context.marketQuestion === undefined
        ? null
        : String(context.marketQuestion),
      outcome: context.outcome === null || context.outcome === undefined ? null : String(context.outcome),
      side: context.side === null || context.side === undefined
        ? null
        : String(context.side) as "BUY" | "SELL",
      price: context.price === null || context.price === undefined ? null : String(context.price),
    },
    status: String(row.status) as AlertDeliveryRecord["status"],
    attempts: Number(row.attempts),
    maxAttempts: Number(row.max_attempts),
    lastError: row.last_error === null || row.last_error === undefined ? null : String(row.last_error),
    createdAt: iso(row.created_at),
    deliveredAt: nullableIso(row.delivered_at),
  };
}

function nullableIso(value: unknown): string | null {
  return value === null || value === undefined ? null : iso(value);
}

function iso(value: unknown): string {
  return (value instanceof Date ? value : new Date(String(value))).toISOString();
}