import { randomBytes } from "node:crypto";

import type { PaperMarkStatus, PaperShareStatus } from "@polytrade/contracts";
import { Decimal } from "decimal.js";
import type { Pool } from "pg";

import { notFound } from "./errors.js";
import { fillFromRow, iso } from "./paper-store.js";
import { money, shares } from "./paper-pricing.js";

export interface TrackRecordTotals {
  realizedPnl: string;
  totalFees: string;
  tradeCount: number;
  wins: number;
  closed: number;
}

export interface TrackRecordCurveFill {
  createdAt: string;
  cashEffect: string;
}

export interface TrackRecordSnapshot {
  profile: { startedAt: string };
  account: { initialCash: string; cash: string };
  positions: Array<{
    marketQuestion: string;
    outcome: string;
    shares: string;
    averageCost: string;
    liquidationValue: string;
    unrealizedPnl: string;
    markStatus: PaperMarkStatus;
  }>;
  totals: TrackRecordTotals;
  recentFills: ReturnType<typeof fillFromRow>[];
  curve: { totalCashEffect: string; fills: TrackRecordCurveFill[] };
}

// One opt-in share link per paper account. The token is a random URL-safe
// secret — never the raw principal id — so a shared URL leaks nothing about
// the underlying identity.
export interface TrackRecordStore {
  status(principalId: string): Promise<PaperShareStatus>;
  enable(principalId: string): Promise<PaperShareStatus>;
  rotate(principalId: string): Promise<PaperShareStatus>;
  disable(principalId: string): Promise<PaperShareStatus>;
  resolvePrincipal(token: string): Promise<string | null>;
  snapshot(principalId: string): Promise<TrackRecordSnapshot>;
}

export class PostgresTrackRecordStore implements TrackRecordStore {
  constructor(
    private readonly pool: Pool,
    private readonly now: () => Date = () => new Date(),
  ) {}

  async status(principalId: string): Promise<PaperShareStatus> {
    const result = await this.pool.query(
      "SELECT share_token, enabled, created_at, updated_at FROM polytrade.paper_share_links WHERE principal_id=$1",
      [principalId],
    );
    return shareStatus(result.rows[0] as Record<string, unknown> | undefined);
  }

  async enable(principalId: string): Promise<PaperShareStatus> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      await client.query(
        `INSERT INTO polytrade.paper_share_links (principal_id, share_token)
         VALUES ($1, $2)
         ON CONFLICT (principal_id) DO NOTHING`,
        [principalId, newToken()],
      );
      const updated = await client.query(
        `UPDATE polytrade.paper_share_links
         SET enabled=true, updated_at=$2
         WHERE principal_id=$1
         RETURNING share_token, enabled, created_at, updated_at`,
        [principalId, this.now()],
      );
      await client.query("COMMIT");
      return shareStatus(updated.rows[0] as Record<string, unknown>);
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  async rotate(principalId: string): Promise<PaperShareStatus> {
    const result = await this.pool.query(
      `UPDATE polytrade.paper_share_links
       SET share_token=$2, updated_at=$3
       WHERE principal_id=$1
       RETURNING share_token, enabled, created_at, updated_at`,
      [principalId, newToken(), this.now()],
    );
    if (result.rows[0]) return shareStatus(result.rows[0] as Record<string, unknown>);
    // Rotating before the link exists is equivalent to creating it.
    return this.enable(principalId);
  }

  async disable(principalId: string): Promise<PaperShareStatus> {
    await this.pool.query(
      "UPDATE polytrade.paper_share_links SET enabled=false, updated_at=$2 WHERE principal_id=$1",
      [principalId, this.now()],
    );
    return this.status(principalId);
  }

  async resolvePrincipal(token: string): Promise<string | null> {
    const result = await this.pool.query(
      "SELECT principal_id FROM polytrade.paper_share_links WHERE share_token=$1 AND enabled",
      [token],
    );
    const row = result.rows[0] as Record<string, unknown> | undefined;
    return row ? String(row.principal_id) : null;
  }

  async snapshot(principalId: string): Promise<TrackRecordSnapshot> {
    const [accountResult, positionResult, totalsResult, fillsResult, curveTotalResult, curveWindowResult] =
      await Promise.all([
        this.pool.query(
          "SELECT initial_cash, cash, created_at FROM polytrade.paper_accounts WHERE principal_id=$1",
          [principalId],
        ),
        this.pool.query(
          `SELECT market_question, outcome, shares, cost_basis, liquidation_value, mark_status
           FROM polytrade.paper_positions
           WHERE principal_id=$1
           ORDER BY created_at, token_id`,
          [principalId],
        ),
        this.pool.query(
          `SELECT COALESCE(sum(realized_pnl), 0) AS realized_pnl,
                  COALESCE(sum(fee), 0) AS total_fees,
                  count(*) FILTER (WHERE kind IN ('BUY','SELL')) AS trade_count,
                  count(*) FILTER (WHERE kind IN ('SELL','SETTLEMENT') AND realized_pnl > 0) AS wins,
                  count(*) FILTER (WHERE kind IN ('SELL','SETTLEMENT')) AS closed
           FROM polytrade.paper_fills
           WHERE principal_id=$1`,
          [principalId],
        ),
        this.pool.query(
          `SELECT * FROM polytrade.paper_fills
           WHERE principal_id=$1
           ORDER BY created_at DESC, id DESC
           LIMIT 50`,
          [principalId],
        ),
        this.pool.query(
          "SELECT COALESCE(sum(cash_effect), 0) AS total FROM polytrade.paper_fills WHERE principal_id=$1",
          [principalId],
        ),
        this.pool.query(
          `SELECT created_at, cash_effect
           FROM polytrade.paper_fills
           WHERE principal_id=$1
           ORDER BY created_at DESC, id DESC
           LIMIT 500`,
          [principalId],
        ),
      ]);

    const account = accountResult.rows[0] as Record<string, unknown> | undefined;
    if (!account) throw notFound("Track record not found");
    const totals = totalsResult.rows[0] as Record<string, unknown>;
    const curveFills = (curveWindowResult.rows as Record<string, unknown>[]).map((row) => ({
      createdAt: iso(row.created_at),
      cashEffect: money(new Decimal(String(row.cash_effect))),
    })).reverse();

    return {
      profile: { startedAt: iso(account.created_at) },
      account: {
        initialCash: money(new Decimal(String(account.initial_cash))),
        cash: money(new Decimal(String(account.cash))),
      },
      positions: (positionResult.rows as Record<string, unknown>[]).map((row) => {
        const quantity = new Decimal(String(row.shares));
        const costBasis = new Decimal(String(row.cost_basis));
        return {
          marketQuestion: String(row.market_question),
          outcome: String(row.outcome),
          shares: shares(quantity),
          averageCost: money(costBasis.div(quantity)),
          liquidationValue: money(new Decimal(String(row.liquidation_value))),
          unrealizedPnl: money(new Decimal(String(row.liquidation_value)).minus(costBasis)),
          markStatus: String(row.mark_status) as PaperMarkStatus,
        };
      }),
      totals: {
        realizedPnl: money(new Decimal(String(totals.realized_pnl ?? 0))),
        totalFees: money(new Decimal(String(totals.total_fees ?? 0))),
        tradeCount: Number(totals.trade_count ?? 0),
        wins: Number(totals.wins ?? 0),
        closed: Number(totals.closed ?? 0),
      },
      recentFills: (fillsResult.rows as Record<string, unknown>[]).map((row) => fillFromRow(row)),
      curve: {
        totalCashEffect: money(new Decimal(String(curveTotalResult.rows[0]?.total ?? 0))),
        fills: curveFills,
      },
    };
  }
}

function newToken(): string {
  return randomBytes(24).toString("base64url");
}

function shareStatus(row: Record<string, unknown> | undefined): PaperShareStatus {
  if (!row) return { token: null, enabled: false, createdAt: null, updatedAt: null };
  return {
    token: String(row.share_token),
    enabled: Boolean(row.enabled),
    createdAt: iso(row.created_at),
    updatedAt: iso(row.updated_at),
  };
}