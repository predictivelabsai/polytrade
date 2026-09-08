import { createHash, randomUUID } from "node:crypto";

import type {
  PaperFill,
  PaperFillsResponse,
  PaperMarkStatus,
  PaperPortfolio,
  PaperQuote,
} from "@polytrade/contracts";
import { Decimal } from "decimal.js";
import type { Pool, PoolClient } from "pg";

import { money, paperLiquidationValue, price, shares } from "./paper-pricing.js";

const INITIAL_CASH = new Decimal("10000");

export interface PaperExecutionInput {
  principalId: string;
  idempotencyKey: string;
  requestHash: string;
  quote: PaperQuote;
  createdAt: Date;
  strategyGuard?: {
    strategyId: string;
    scanId: string;
    leaseOwner: string;
    maxPosition: string;
  };
}

export type PaperExecutionResult =
  | { state: "created" | "replayed"; fill: PaperFill }
  | { state: "key_mismatch" }
  | { state: "identity_conflict" }
  | { state: "insufficient_cash" }
  | { state: "insufficient_shares" }
  | { state: "strategy_stopped" }
  | { state: "strategy_limit" };

export type PaperRefreshInstruction =
  | {
      kind: "mark";
      conditionId: string;
      tokenId: string;
      bestBid: string;
      feeRate: string;
      observedAt: Date;
    }
  | {
      kind: "stale";
      conditionId: string;
      tokenId: string;
    }
  | {
      kind: "settlement";
      conditionId: string;
      tokenId: string;
      resolutionPrice: "0" | "1";
      observedAt: Date;
    };

export interface PaperStore {
  portfolio(principalId: string, warnings?: string[]): Promise<PaperPortfolio>;
  replay(principalId: string, idempotencyKey: string, requestHash: string): Promise<PaperExecutionResult | null>;
  execute(input: PaperExecutionInput): Promise<PaperExecutionResult>;
  refresh(principalId: string, instructions: PaperRefreshInstruction[]): Promise<void>;
  fills(principalId: string, limit: number, offset: number): Promise<PaperFillsResponse>;
}

export class PostgresPaperStore implements PaperStore {
  constructor(
    private readonly pool: Pool,
    private readonly now: () => Date = () => new Date(),
  ) {}

  async portfolio(principalId: string, warnings: string[] = []): Promise<PaperPortfolio> {
    const [accountResult, positionResult, totalsResult] = await Promise.all([
      this.pool.query(
        "SELECT initial_cash, cash FROM polytrade.paper_accounts WHERE principal_id=$1",
        [principalId],
      ),
      this.pool.query(
        `SELECT condition_id, token_id, market_question, outcome, shares, cost_basis,
                best_bid, liquidation_value, mark_status, marked_at
         FROM polytrade.paper_positions
         WHERE principal_id=$1
         ORDER BY created_at, token_id`,
        [principalId],
      ),
      this.pool.query(
        `SELECT COALESCE(sum(realized_pnl), 0) AS realized_pnl,
                COALESCE(sum(fee), 0) AS total_fees
         FROM polytrade.paper_fills
         WHERE principal_id=$1`,
        [principalId],
      ),
    ]);

    const account = accountResult.rows[0] as Record<string, unknown> | undefined;
    const initialCash = new Decimal(account?.initial_cash ? String(account.initial_cash) : INITIAL_CASH);
    const cash = new Decimal(account?.cash ? String(account.cash) : INITIAL_CASH);
    const positions = positionResult.rows.map((row: Record<string, unknown>) => {
      const quantity = new Decimal(String(row.shares));
      const costBasis = new Decimal(String(row.cost_basis));
      const liquidationValue = new Decimal(String(row.liquidation_value));
      return {
        conditionId: String(row.condition_id),
        tokenId: String(row.token_id),
        marketQuestion: String(row.market_question),
        outcome: String(row.outcome),
        shares: shares(quantity),
        costBasis: money(costBasis),
        averageCost: money(costBasis.div(quantity)),
        bestBid: row.best_bid === null ? null : price(new Decimal(String(row.best_bid))),
        liquidationValue: money(liquidationValue),
        unrealizedPnl: money(liquidationValue.minus(costBasis)),
        markStatus: String(row.mark_status) as PaperMarkStatus,
        markedAt: row.marked_at ? iso(row.marked_at) : null,
      };
    });
    const positionsValue = positions.reduce(
      (total, position) => total.plus(position.liquidationValue),
      new Decimal(0),
    );
    const unrealizedPnl = positions.reduce(
      (total, position) => total.plus(position.unrealizedPnl),
      new Decimal(0),
    );
    const totals = totalsResult.rows[0] as Record<string, unknown> | undefined;
    const realizedPnl = new Decimal(String(totals?.realized_pnl ?? 0));
    const totalFees = new Decimal(String(totals?.total_fees ?? 0));
    const equity = cash.plus(positionsValue);

    return {
      initialCash: money(initialCash),
      cash: money(cash),
      positionsValue: money(positionsValue),
      equity: money(equity),
      realizedPnl: money(realizedPnl),
      unrealizedPnl: money(unrealizedPnl),
      totalPnl: money(equity.minus(initialCash)),
      totalFees: money(totalFees),
      positions,
      warnings,
      observedAt: this.now().toISOString(),
    };
  }

  async replay(
    principalId: string,
    idempotencyKey: string,
    requestHash: string,
  ): Promise<PaperExecutionResult | null> {
    const result = await this.pool.query(
      "SELECT * FROM polytrade.paper_fills WHERE principal_id=$1 AND idempotency_key=$2",
      [principalId, idempotencyKey],
    );
    const existing = result.rows[0] as Record<string, unknown> | undefined;
    if (!existing) return null;
    return String(existing.request_hash) === requestHash
      ? { state: "replayed", fill: fillFromRow(existing) }
      : { state: "key_mismatch" };
  }

  async execute(input: PaperExecutionInput): Promise<PaperExecutionResult> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      await ensureAccount(client, input.principalId);
      const accountResult = await client.query(
        "SELECT cash FROM polytrade.paper_accounts WHERE principal_id=$1 FOR UPDATE",
        [input.principalId],
      );
      const existingResult = await client.query(
        "SELECT * FROM polytrade.paper_fills WHERE principal_id=$1 AND idempotency_key=$2",
        [input.principalId, input.idempotencyKey],
      );
      const existing = existingResult.rows[0] as Record<string, unknown> | undefined;
      if (existing) {
        await client.query("COMMIT");
        return String(existing.request_hash) === input.requestHash
          ? { state: "replayed", fill: fillFromRow(existing) }
          : { state: "key_mismatch" };
      }

      const positionResult = await client.query(
        "SELECT * FROM polytrade.paper_positions WHERE principal_id=$1 AND token_id=$2 FOR UPDATE",
        [input.principalId, input.quote.tokenId],
      );
      const position = positionResult.rows[0] as Record<string, unknown> | undefined;
      if (position && String(position.condition_id) !== input.quote.conditionId) {
        await client.query("ROLLBACK");
        return { state: "identity_conflict" };
      }

      if (input.strategyGuard) {
        const strategy = await client.query(
          `SELECT strategy_id FROM polytrade.paper_strategies
           WHERE strategy_id=$1 AND principal_id=$2 AND status='RUNNING'
             AND scan_id=$3 AND lease_owner=$4 AND lease_until>$5
           FOR UPDATE`,
          [input.strategyGuard.strategyId, input.principalId, input.strategyGuard.scanId,
            input.strategyGuard.leaseOwner, input.createdAt],
        );
        if ((strategy.rowCount ?? 0) === 0) {
          await client.query("ROLLBACK");
          return { state: "strategy_stopped" };
        }
      }

      const accountCash = new Decimal(String(accountResult.rows[0]!.cash));
      const quantity = new Decimal(input.quote.shares);
      const cashEffect = new Decimal(input.quote.cashEffect);
      let realizedPnl = new Decimal(0);

      if (input.quote.side === "BUY") {
        const debit = cashEffect.negated();
        const heldShares = new Decimal(position ? String(position.shares) : 0);
        if (input.strategyGuard && heldShares.plus(quantity).gt(input.strategyGuard.maxPosition)) {
          await client.query("ROLLBACK");
          return { state: "strategy_limit" };
        }
        if (debit.lte(0) || accountCash.lt(debit)) {
          await client.query("ROLLBACK");
          return { state: "insufficient_cash" };
        }
        await client.query(
          "UPDATE polytrade.paper_accounts SET cash=$2, updated_at=$3 WHERE principal_id=$1",
          [input.principalId, money(accountCash.minus(debit)), input.createdAt],
        );
        await upsertBoughtPosition(client, input, position, quantity, debit);
      } else {
        if (!position) {
          await client.query("ROLLBACK");
          return { state: "insufficient_shares" };
        }
        const heldShares = new Decimal(String(position.shares));
        if (cashEffect.lt(0) || heldShares.lt(quantity)) {
          await client.query("ROLLBACK");
          return { state: "insufficient_shares" };
        }
        const heldCost = new Decimal(String(position.cost_basis));
        const allocatedCost = heldShares.eq(quantity)
          ? heldCost
          : heldCost.mul(quantity).div(heldShares).toDecimalPlaces(6, Decimal.ROUND_HALF_UP);
        realizedPnl = cashEffect.minus(allocatedCost);
        await client.query(
          "UPDATE polytrade.paper_accounts SET cash=$2, updated_at=$3 WHERE principal_id=$1",
          [input.principalId, money(accountCash.plus(cashEffect)), input.createdAt],
        );
        await reduceSoldPosition(client, input, position, quantity, allocatedCost);
      }

      const fillId = randomUUID();
      const fill = await insertFill(client, {
        id: fillId,
        principalId: input.principalId,
        idempotencyKey: input.idempotencyKey,
        requestHash: input.requestHash,
        kind: input.quote.side,
        conditionId: input.quote.conditionId,
        tokenId: input.quote.tokenId,
        marketQuestion: input.quote.marketQuestion,
        outcome: input.quote.outcome,
        shares: input.quote.shares,
        averagePrice: input.quote.averagePrice,
        grossNotional: input.quote.grossNotional,
        feeRate: input.quote.feeRate,
        fee: input.quote.fee,
        cashEffect: input.quote.cashEffect,
        realizedPnl: money(realizedPnl),
        observedAt: new Date(input.quote.observedAt),
        createdAt: input.createdAt,
      });
      await client.query("COMMIT");
      return { state: "created", fill };
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      throw error;
    } finally {
      client.release();
    }
  }

  async refresh(principalId: string, instructions: PaperRefreshInstruction[]): Promise<void> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      await ensureAccount(client, principalId);
      await client.query(
        "SELECT principal_id FROM polytrade.paper_accounts WHERE principal_id=$1 FOR UPDATE",
        [principalId],
      );
      for (const instruction of instructions) {
        const result = await client.query(
          "SELECT * FROM polytrade.paper_positions WHERE principal_id=$1 AND token_id=$2 FOR UPDATE",
          [principalId, instruction.tokenId],
        );
        const position = result.rows[0] as Record<string, unknown> | undefined;
        if (!position || String(position.condition_id) !== instruction.conditionId) continue;

        if (instruction.kind === "stale") {
          await client.query(
            `UPDATE polytrade.paper_positions
             SET mark_status=CASE WHEN marked_at IS NULL THEN 'unpriced' ELSE 'stale' END,
                 updated_at=$3
             WHERE principal_id=$1 AND token_id=$2`,
            [principalId, instruction.tokenId, this.now()],
          );
          continue;
        }
        if (instruction.kind === "mark") {
          const liquidationValue = paperLiquidationValue(
            String(position.shares),
            instruction.bestBid,
            instruction.feeRate,
          );
          await client.query(
            `UPDATE polytrade.paper_positions
             SET best_bid=$3, liquidation_value=$4, mark_status='current',
                 marked_at=$5, updated_at=$5
             WHERE principal_id=$1 AND token_id=$2`,
            [principalId, instruction.tokenId, instruction.bestBid, liquidationValue, instruction.observedAt],
          );
          continue;
        }

        const idempotencyKey = `settlement:${instruction.conditionId}:${instruction.tokenId}`;
        const existing = await client.query(
          "SELECT id FROM polytrade.paper_fills WHERE principal_id=$1 AND idempotency_key=$2",
          [principalId, idempotencyKey],
        );
        if ((existing.rowCount ?? 0) > 0) continue;
        const quantity = new Decimal(String(position.shares));
        const resolutionPrice = new Decimal(instruction.resolutionPrice);
        const payout = quantity.mul(resolutionPrice).toDecimalPlaces(6, Decimal.ROUND_HALF_UP);
        const realizedPnl = payout.minus(String(position.cost_basis));
        await client.query(
          "UPDATE polytrade.paper_accounts SET cash=cash+$2, updated_at=$3 WHERE principal_id=$1",
          [principalId, money(payout), instruction.observedAt],
        );
        await insertFill(client, {
          id: randomUUID(),
          principalId,
          idempotencyKey,
          requestHash: createHash("sha256").update(`${idempotencyKey}:${instruction.resolutionPrice}`).digest("hex"),
          kind: "SETTLEMENT",
          conditionId: String(position.condition_id),
          tokenId: String(position.token_id),
          marketQuestion: String(position.market_question),
          outcome: String(position.outcome),
          shares: shares(quantity),
          averagePrice: price(resolutionPrice),
          grossNotional: money(payout),
          feeRate: "0.000000",
          fee: "0.00000",
          cashEffect: money(payout),
          realizedPnl: money(realizedPnl),
          observedAt: instruction.observedAt,
          createdAt: instruction.observedAt,
        });
        await client.query(
          "DELETE FROM polytrade.paper_positions WHERE principal_id=$1 AND token_id=$2",
          [principalId, instruction.tokenId],
        );
      }
      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      throw error;
    } finally {
      client.release();
    }
  }

  async fills(principalId: string, limit: number, offset: number): Promise<PaperFillsResponse> {
    const [items, count] = await Promise.all([
      this.pool.query(
        `SELECT * FROM polytrade.paper_fills
         WHERE principal_id=$1
         ORDER BY created_at DESC, id DESC
         LIMIT $2 OFFSET $3`,
        [principalId, limit, offset],
      ),
      this.pool.query(
        "SELECT count(*) AS total FROM polytrade.paper_fills WHERE principal_id=$1",
        [principalId],
      ),
    ]);
    return {
      items: items.rows.map((row: Record<string, unknown>) => fillFromRow(row)),
      total: Number(count.rows[0]?.total ?? 0),
      offset,
      limit,
    };
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

async function upsertBoughtPosition(
  client: PoolClient,
  input: PaperExecutionInput,
  current: Record<string, unknown> | undefined,
  quantity: Decimal,
  debit: Decimal,
): Promise<void> {
  if (!current) {
    await client.query(
      `INSERT INTO polytrade.paper_positions
       (principal_id, condition_id, token_id, market_question, outcome, shares, cost_basis,
        liquidation_value, mark_status, created_at, updated_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,0,'unpriced',$8,$8)`,
      [input.principalId, input.quote.conditionId, input.quote.tokenId,
        input.quote.marketQuestion, input.quote.outcome, shares(quantity), money(debit), input.createdAt],
    );
    return;
  }
  const heldShares = new Decimal(String(current.shares));
  const nextShares = heldShares.plus(quantity);
  const nextLiquidation = heldShares.gt(0)
    ? new Decimal(String(current.liquidation_value)).mul(nextShares).div(heldShares)
    : new Decimal(0);
  await client.query(
    `UPDATE polytrade.paper_positions
     SET market_question=$3, outcome=$4, shares=$5, cost_basis=$6,
         liquidation_value=$7,
         mark_status=CASE WHEN marked_at IS NULL THEN 'unpriced' ELSE 'stale' END,
         updated_at=$8
     WHERE principal_id=$1 AND token_id=$2`,
    [input.principalId, input.quote.tokenId, input.quote.marketQuestion, input.quote.outcome,
      shares(nextShares), money(new Decimal(String(current.cost_basis)).plus(debit)),
      money(nextLiquidation), input.createdAt],
  );
}

async function reduceSoldPosition(
  client: PoolClient,
  input: PaperExecutionInput,
  current: Record<string, unknown>,
  quantity: Decimal,
  allocatedCost: Decimal,
): Promise<void> {
  const heldShares = new Decimal(String(current.shares));
  const nextShares = heldShares.minus(quantity);
  if (nextShares.eq(0)) {
    await client.query(
      "DELETE FROM polytrade.paper_positions WHERE principal_id=$1 AND token_id=$2",
      [input.principalId, input.quote.tokenId],
    );
    return;
  }
  const nextLiquidation = new Decimal(String(current.liquidation_value)).mul(nextShares).div(heldShares);
  await client.query(
    `UPDATE polytrade.paper_positions
     SET shares=$3, cost_basis=$4, liquidation_value=$5,
         mark_status=CASE WHEN marked_at IS NULL THEN 'unpriced' ELSE 'stale' END,
         updated_at=$6
     WHERE principal_id=$1 AND token_id=$2`,
    [input.principalId, input.quote.tokenId, shares(nextShares),
      money(new Decimal(String(current.cost_basis)).minus(allocatedCost)),
      money(nextLiquidation), input.createdAt],
  );
}

interface FillInsert {
  id: string;
  principalId: string;
  idempotencyKey: string;
  requestHash: string;
  kind: PaperFill["kind"];
  conditionId: string;
  tokenId: string;
  marketQuestion: string;
  outcome: string;
  shares: string;
  averagePrice: string;
  grossNotional: string;
  feeRate: string;
  fee: string;
  cashEffect: string;
  realizedPnl: string;
  observedAt: Date;
  createdAt: Date;
}

async function insertFill(client: PoolClient, value: FillInsert): Promise<PaperFill> {
  const result = await client.query(
    `INSERT INTO polytrade.paper_fills
     (id, principal_id, idempotency_key, request_hash, kind, condition_id, token_id,
      market_question, outcome, shares, average_price, gross_notional, fee_rate, fee,
      cash_effect, realized_pnl, observed_at, created_at)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
     RETURNING *`,
    [value.id, value.principalId, value.idempotencyKey, value.requestHash, value.kind,
      value.conditionId, value.tokenId, value.marketQuestion, value.outcome, value.shares,
      value.averagePrice, value.grossNotional, value.feeRate, value.fee, value.cashEffect,
      value.realizedPnl, value.observedAt, value.createdAt],
  );
  return fillFromRow(result.rows[0] as Record<string, unknown>);
}

export function fillFromRow(row: Record<string, unknown>): PaperFill {
  return {
    fillId: String(row.id),
    kind: String(row.kind) as PaperFill["kind"],
    conditionId: String(row.condition_id),
    tokenId: String(row.token_id),
    marketQuestion: String(row.market_question),
    outcome: String(row.outcome),
    shares: shares(new Decimal(String(row.shares))),
    averagePrice: price(new Decimal(String(row.average_price))),
    grossNotional: money(new Decimal(String(row.gross_notional))),
    feeRate: money(new Decimal(String(row.fee_rate))),
    fee: new Decimal(String(row.fee)).toFixed(5),
    cashEffect: money(new Decimal(String(row.cash_effect))),
    realizedPnl: money(new Decimal(String(row.realized_pnl))),
    observedAt: iso(row.observed_at),
    createdAt: iso(row.created_at),
  };
}

export function iso(value: unknown): string {
  return (value instanceof Date ? value : new Date(String(value))).toISOString();
}
