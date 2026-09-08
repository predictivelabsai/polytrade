import type { Pool } from "pg";

import { createHash } from "node:crypto";

import type { AccountSnapshot, ChallengeRecord, OrderIntentRecord, WalletSessionRecord } from "./types.js";

export interface TradingStore {
  health(): Promise<void>;
  close(): Promise<void>;
  createChallenge(challenge: ChallengeRecord): Promise<void>;
  consumeChallenge(id: string, principalId: string, now: Date): Promise<ChallengeRecord | undefined>;
  releaseChallenge(id: string, principalId: string, usedAt: Date): Promise<boolean>;
  createSession(session: WalletSessionRecord): Promise<void>;
  getSession(id: string, principalId: string, now: Date, idleSeconds: number): Promise<WalletSessionRecord | undefined>;
  getLatestSession(principalId: string, now: Date, idleSeconds: number): Promise<WalletSessionRecord | undefined>;
  peekLatestSession(principalId: string, now: Date): Promise<WalletSessionRecord | undefined>;
  revokeSession(id: string, principalId: string): Promise<boolean>;
  createIntent(intent: OrderIntentRecord): Promise<OrderIntentRecord>;
  getIntent(id: string, principalId: string): Promise<OrderIntentRecord | undefined>;
  claimIntentSubmission(id: string, principalId: string, signedOrderHash: string): Promise<boolean>;
  setIntentStatus(
    id: string,
    principalId: string,
    status: OrderIntentRecord["status"],
    values?: { signedOrderHash?: string; upstreamResponse?: unknown; submittedAt?: Date },
  ): Promise<void>;
  beginIdempotency(
    principalId: string,
    operation: string,
    key: string,
    requestHash: string,
  ): Promise<IdempotencyClaim>;
  finishIdempotency(
    principalId: string,
    operation: string,
    key: string,
    response: unknown,
  ): Promise<void>;
  releaseIdempotency(principalId: string, operation: string, key: string): Promise<boolean>;
  mirrorAccount(principalId: string, account: AccountSnapshot): Promise<void>;
  appendAudit(principalId: string, action: string, entityId?: string, detail?: unknown): Promise<void>;
}

export type IdempotencyClaim =
  | { state: "claimed" }
  | { state: "pending" }
  | { state: "mismatch" }
  | { state: "complete"; response: unknown };

/**
 * How long an unsettled (or pinned-failure) idempotency row stays
 * authoritative. Gateway operations complete in seconds, so a row this age
 * means the request died before it could finish — the claim is stale and the
 * key becomes re-claimable instead of 409-ing forever.
 */
export const IDEMPOTENCY_STALE_SECONDS = 300;

const challengeFromRow = (row: Record<string, unknown>): ChallengeRecord => ({
  id: String(row.id),
  principalId: String(row.principal_id),
  walletAddress: String(row.wallet_address) as `0x${string}`,
  signatureType: Number(row.signature_type) as 0 | 1 | 2 | 3,
  ...(row.funder_address ? { funderAddress: String(row.funder_address) as `0x${string}` } : {}),
  timestampSeconds: Number(row.timestamp_seconds),
  nonce: Number(row.nonce),
  typedData: row.typed_data as ChallengeRecord["typedData"],
  expiresAt: new Date(String(row.expires_at)),
  ...(row.used_at ? { usedAt: new Date(String(row.used_at)) } : {}),
});

const sessionFromRow = (row: Record<string, unknown>): WalletSessionRecord => ({
  id: String(row.id),
  principalId: String(row.principal_id),
  walletAddress: String(row.wallet_address) as `0x${string}`,
  signatureType: Number(row.signature_type) as 0 | 1 | 2 | 3,
  ...(row.funder_address ? { funderAddress: String(row.funder_address) as `0x${string}` } : {}),
  encryptedCredentials: String(row.encrypted_credentials),
  idleExpiresAt: new Date(String(row.idle_expires_at)),
  absoluteExpiresAt: new Date(String(row.absolute_expires_at)),
  lastUsedAt: new Date(String(row.last_used_at)),
  ...(row.revoked_at ? { revokedAt: new Date(String(row.revoked_at)) } : {}),
});

const intentFromRow = (row: Record<string, unknown>): OrderIntentRecord => ({
  id: String(row.id),
  principalId: String(row.principal_id),
  sessionId: String(row.session_id),
  idempotencyKey: String(row.idempotency_key),
  proposal: row.proposal as OrderIntentRecord["proposal"],
  orderType: String(row.order_type) as OrderIntentRecord["orderType"],
  postOnly: Boolean(row.post_only),
  typedData: row.typed_data as OrderIntentRecord["typedData"],
  unsignedOrder: row.unsigned_order as Record<string, unknown>,
  ...(row.signature_suffix ? { signatureSuffix: String(row.signature_suffix) } : {}),
  status: String(row.status) as OrderIntentRecord["status"],
  ...(row.signed_order_hash ? { signedOrderHash: String(row.signed_order_hash) } : {}),
  ...(row.upstream_response ? { upstreamResponse: row.upstream_response } : {}),
  expiresAt: new Date(String(row.expires_at)),
  ...(row.submitted_at ? { submittedAt: new Date(String(row.submitted_at)) } : {}),
});

export class PostgresTradingStore implements TradingStore {
  constructor(private readonly pool: Pool) {}

  async health(): Promise<void> {
    await this.pool.query("SELECT 1");
  }

  async close(): Promise<void> {
    await this.pool.end();
  }

  async createChallenge(value: ChallengeRecord): Promise<void> {
    await this.pool.query(
      `INSERT INTO polytrade.wallet_challenges
       (id, principal_id, wallet_address, signature_type, funder_address,
        timestamp_seconds, nonce, typed_data, expires_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
      [value.id, value.principalId, value.walletAddress, value.signatureType,
        value.funderAddress ?? null, value.timestampSeconds, value.nonce,
        value.typedData, value.expiresAt],
    );
  }

  async consumeChallenge(id: string, principalId: string, now: Date): Promise<ChallengeRecord | undefined> {
    const result = await this.pool.query(
      `UPDATE polytrade.wallet_challenges SET used_at=$3
       WHERE id=$1 AND principal_id=$2 AND used_at IS NULL AND expires_at>$3
       RETURNING *`,
      [id, principalId, now],
    );
    return result.rows[0] ? challengeFromRow(result.rows[0]) : undefined;
  }

  /** Undo a consume when the session could not be created upstream (e.g. the CLOB is down). */
  async releaseChallenge(id: string, principalId: string, usedAt: Date): Promise<boolean> {
    const result = await this.pool.query(
      `UPDATE polytrade.wallet_challenges SET used_at=NULL
       WHERE id=$1 AND principal_id=$2 AND used_at=$3`,
      [id, principalId, usedAt],
    );
    return (result.rowCount ?? 0) === 1;
  }

  async createSession(value: WalletSessionRecord): Promise<void> {
    await this.pool.query(
      `INSERT INTO polytrade.wallet_sessions
       (id, principal_id, wallet_address, signature_type, funder_address,
        encrypted_credentials, idle_expires_at, absolute_expires_at, last_used_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
      [value.id, value.principalId, value.walletAddress, value.signatureType,
        value.funderAddress ?? null, value.encryptedCredentials, value.idleExpiresAt,
        value.absoluteExpiresAt, value.lastUsedAt],
    );
  }

  private async selectSession(
    where: string,
    params: unknown[],
    now: Date,
    idleSeconds: number,
  ): Promise<WalletSessionRecord | undefined> {
    const result = await this.pool.query(
      `UPDATE polytrade.wallet_sessions
       SET last_used_at=$${params.length + 1},
           idle_expires_at=LEAST(
             absolute_expires_at,
             $${params.length + 1} + make_interval(secs => $${params.length + 2}::int)
           )
       WHERE ${where} AND revoked_at IS NULL
         AND idle_expires_at>$${params.length + 1} AND absolute_expires_at>$${params.length + 1}
       RETURNING *`,
      [...params, now, idleSeconds],
    );
    return result.rows[0] ? sessionFromRow(result.rows[0]) : undefined;
  }

  getSession(id: string, principalId: string, now: Date, idleSeconds: number) {
    return this.selectSession("id=$1 AND principal_id=$2", [id, principalId], now, idleSeconds);
  }

  getLatestSession(principalId: string, now: Date, idleSeconds: number) {
    return this.selectSession(
      "id=(SELECT id FROM polytrade.wallet_sessions WHERE principal_id=$1 AND revoked_at IS NULL AND idle_expires_at>$2 AND absolute_expires_at>$2 ORDER BY created_at DESC LIMIT 1) AND principal_id=$1",
      [principalId],
      now,
      idleSeconds,
    );
  }

  async peekLatestSession(principalId: string, now: Date) {
    const result = await this.pool.query(
      `SELECT *
       FROM polytrade.wallet_sessions
       WHERE principal_id=$1 AND revoked_at IS NULL
         AND idle_expires_at>$2 AND absolute_expires_at>$2
       ORDER BY created_at DESC
       LIMIT 1`,
      [principalId, now],
    );
    return result.rows[0] ? sessionFromRow(result.rows[0]) : undefined;
  }

  async revokeSession(id: string, principalId: string): Promise<boolean> {
    const result = await this.pool.query(
      "UPDATE polytrade.wallet_sessions SET revoked_at=now() WHERE id=$1 AND principal_id=$2 AND revoked_at IS NULL",
      [id, principalId],
    );
    return (result.rowCount ?? 0) > 0;
  }

  async createIntent(value: OrderIntentRecord): Promise<OrderIntentRecord> {
    const result = await this.pool.query(
      `INSERT INTO polytrade.order_intents
       (id, principal_id, session_id, idempotency_key, proposal, order_type,
        post_only, typed_data, unsigned_order, signature_suffix, status, expires_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
       ON CONFLICT (principal_id, idempotency_key) DO UPDATE
         SET idempotency_key=EXCLUDED.idempotency_key
       RETURNING *`,
      [value.id, value.principalId, value.sessionId, value.idempotencyKey,
        value.proposal, value.orderType, value.postOnly, value.typedData,
        value.unsignedOrder, value.signatureSuffix ?? null, value.status, value.expiresAt],
    );
    return intentFromRow(result.rows[0]);
  }

  async getIntent(id: string, principalId: string): Promise<OrderIntentRecord | undefined> {
    const result = await this.pool.query(
      "SELECT * FROM polytrade.order_intents WHERE id=$1 AND principal_id=$2",
      [id, principalId],
    );
    return result.rows[0] ? intentFromRow(result.rows[0]) : undefined;
  }

  async claimIntentSubmission(
    id: string,
    principalId: string,
    signedOrderHash: string,
  ): Promise<boolean> {
    const result = await this.pool.query(
      `UPDATE polytrade.order_intents
       SET status='SUBMITTING', signed_order_hash=$3
       WHERE id=$1 AND principal_id=$2 AND status='PENDING'
       RETURNING id`,
      [id, principalId, signedOrderHash],
    );
    return (result.rowCount ?? 0) === 1;
  }

  async setIntentStatus(
    id: string,
    principalId: string,
    status: OrderIntentRecord["status"],
    values: { signedOrderHash?: string; upstreamResponse?: unknown; submittedAt?: Date } = {},
  ): Promise<void> {
    await this.pool.query(
      `UPDATE polytrade.order_intents SET status=$3,
       signed_order_hash=COALESCE($4,signed_order_hash),
       upstream_response=COALESCE($5,upstream_response),
       submitted_at=COALESCE($6,submitted_at)
       WHERE id=$1 AND principal_id=$2`,
      [id, principalId, status, values.signedOrderHash ?? null,
        values.upstreamResponse ?? null, values.submittedAt ?? null],
    );
  }

  async beginIdempotency(
    principalId: string,
    operation: string,
    key: string,
    requestHash: string,
  ): Promise<IdempotencyClaim> {
    const inserted = await this.pool.query(
      `INSERT INTO polytrade.idempotency_records
       (principal_id, operation, idempotency_key, request_hash)
       VALUES ($1,$2,$3,$4)
       ON CONFLICT (principal_id, operation, idempotency_key) DO UPDATE
       SET request_hash=EXCLUDED.request_hash, response=NULL, created_at=now()
       WHERE polytrade.idempotency_records.created_at < now() - make_interval(secs => $5::int)
         AND (polytrade.idempotency_records.response IS NULL
              OR polytrade.idempotency_records.response->>'ok' = 'false')
       RETURNING principal_id`,
      [principalId, operation, key, requestHash, IDEMPOTENCY_STALE_SECONDS],
    );
    if ((inserted.rowCount ?? 0) === 1) return { state: "claimed" };

    const existing = await this.pool.query(
      `SELECT request_hash, response FROM polytrade.idempotency_records
       WHERE principal_id=$1 AND operation=$2 AND idempotency_key=$3`,
      [principalId, operation, key],
    );
    const row = existing.rows[0] as Record<string, unknown> | undefined;
    if (!row || row.request_hash !== requestHash) return { state: "mismatch" };
    if (row.response === null || row.response === undefined) return { state: "pending" };
    return { state: "complete", response: row.response };
  }

  async finishIdempotency(
    principalId: string,
    operation: string,
    key: string,
    response: unknown,
  ): Promise<void> {
    await this.pool.query(
      `UPDATE polytrade.idempotency_records SET response=$4
       WHERE principal_id=$1 AND operation=$2 AND idempotency_key=$3 AND response IS NULL`,
      [principalId, operation, key, response],
    );
  }

  /** Release an unsettled claim so the caller's retry re-executes instead of 409-ing forever. */
  async releaseIdempotency(
    principalId: string,
    operation: string,
    key: string,
  ): Promise<boolean> {
    const result = await this.pool.query(
      `DELETE FROM polytrade.idempotency_records
       WHERE principal_id=$1 AND operation=$2 AND idempotency_key=$3 AND response IS NULL`,
      [principalId, operation, key],
    );
    return (result.rowCount ?? 0) === 1;
  }

  async mirrorAccount(principalId: string, account: AccountSnapshot): Promise<void> {
    for (const item of account.openOrders) {
      const payload = asRecord(item);
      const id = mirrorIdentifier(payload, ["id", "orderID", "order_id"]);
      await this.pool.query(
        `INSERT INTO polytrade.clob_order_mirror
         (principal_id, order_id, wallet_address, payload)
         VALUES ($1,$2,$3,$4)
         ON CONFLICT (principal_id, order_id) DO UPDATE
         SET payload=EXCLUDED.payload, last_seen_at=now()`,
        [principalId, id, account.funderAddress ?? account.walletAddress, payload],
      );
    }
    for (const item of account.trades) {
      const payload = asRecord(item);
      const id = mirrorIdentifier(payload, ["id", "tradeID", "trade_id"]);
      await this.pool.query(
        `INSERT INTO polytrade.clob_fill_mirror
         (principal_id, trade_id, wallet_address, payload)
         VALUES ($1,$2,$3,$4)
         ON CONFLICT (principal_id, trade_id) DO UPDATE
         SET payload=EXCLUDED.payload, last_seen_at=now()`,
        [principalId, id, account.funderAddress ?? account.walletAddress, payload],
      );
    }
  }

  async appendAudit(principalId: string, action: string, entityId?: string, detail: unknown = {}): Promise<void> {
    await this.pool.query(
      "INSERT INTO polytrade.trading_audit (principal_id, action, entity_id, detail) VALUES ($1,$2,$3,$4)",
      [principalId, action, entityId ?? null, detail],
    );
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : { value };
}

function mirrorIdentifier(value: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate) return candidate;
  }
  return `sha256:${createHash("sha256").update(JSON.stringify(value)).digest("hex")}`;
}
