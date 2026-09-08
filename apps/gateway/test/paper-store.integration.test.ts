import { createHash, randomUUID } from "node:crypto";

import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { bootstrapDatabase } from "../src/bootstrap.js";
import { PostgresPaperStore, type PaperExecutionInput } from "../src/paper-store.js";

const connectionString = process.env.TEST_DATABASE_URL;
const integration = connectionString ? describe : describe.skip;

integration("PostgresPaperStore", () => {
  let pool: pg.Pool;
  let store: PostgresPaperStore;
  const observedAt = new Date("2026-08-03T00:00:00.000Z");

  beforeAll(async () => {
    await bootstrapDatabase(connectionString!);
    pool = new pg.Pool({
      connectionString,
      options: "-c search_path=polytrade,public",
      max: 4,
    });
    store = new PostgresPaperStore(pool, () => observedAt);
  });

  afterAll(async () => {
    await pool?.end();
  });

  it("keeps cost basis, realized P&L, idempotency, and ownership consistent", async () => {
    const principalId = `paper-integration:${randomUUID()}`;
    const first = execution(principalId, "BUY", "10", "0.4", "4", "-4", "buy-1");
    const firstResult = await store.execute(first);
    expect(firstResult.state).toBe("created");
    expect((await store.execute(first)).state).toBe("replayed");

    await store.execute(execution(principalId, "BUY", "10", "0.6", "6", "-6", "buy-2"));
    const sale = await store.execute(execution(principalId, "SELL", "5", "0.8", "4", "4", "sell-1"));
    expect(sale).toMatchObject({ state: "created", fill: { realizedPnl: "1.500000" } });

    const portfolio = await store.portfolio(principalId);
    expect(portfolio).toMatchObject({
      cash: "9994.000000",
      realizedPnl: "1.500000",
      positions: [{ shares: "15.000000", costBasis: "7.500000", averageCost: "0.500000" }],
    });
    expect(await store.portfolio(`other:${randomUUID()}`)).toMatchObject({ cash: "10000.000000", positions: [] });

    const changed = { ...first, requestHash: "f".repeat(64) };
    expect((await store.execute(changed)).state).toBe("key_mismatch");
  });

  it("serializes concurrent spending so virtual cash cannot become negative", async () => {
    const principalId = `paper-concurrency:${randomUUID()}`;
    const results = await Promise.all([
      store.execute(execution(principalId, "BUY", "10000", "0.6", "6000", "-6000", "large-1")),
      store.execute(execution(principalId, "BUY", "10000", "0.6", "6000", "-6000", "large-2")),
    ]);

    expect(results.map((result) => result.state).sort()).toEqual(["created", "insufficient_cash"]);
    expect((await store.portfolio(principalId)).cash).toBe("4000.000000");
  });

  it("marks holdings and settles a resolved token exactly once", async () => {
    const principalId = `paper-settlement:${randomUUID()}`;
    await store.execute(execution(principalId, "BUY", "10", "0.4", "4", "-4", "buy-settle"));
    await store.refresh(principalId, [{
      kind: "mark",
      conditionId: "0xcondition",
      tokenId: "123",
      bestBid: "0.5",
      feeRate: "0.04",
      observedAt,
    }]);
    expect((await store.portfolio(principalId)).positions[0]).toMatchObject({
      bestBid: "0.500000",
      liquidationValue: "4.900000",
      markStatus: "current",
    });

    const settlement = [{
      kind: "settlement" as const,
      conditionId: "0xcondition",
      tokenId: "123",
      resolutionPrice: "1" as const,
      observedAt,
    }];
    await store.refresh(principalId, settlement);
    await store.refresh(principalId, settlement);

    const portfolio = await store.portfolio(principalId);
    expect(portfolio).toMatchObject({ cash: "10006.000000", realizedPnl: "6.000000", positions: [] });
    const fills = await store.fills(principalId, 20, 0);
    expect(fills.items.filter((fill) => fill.kind === "SETTLEMENT")).toHaveLength(1);
  });
});

function execution(
  principalId: string,
  side: "BUY" | "SELL",
  quantity: string,
  averagePrice: string,
  grossNotional: string,
  cashEffect: string,
  suffix: string,
): PaperExecutionInput {
  const idempotencyKey = `integration-${suffix}-${principalId}`;
  return {
    principalId,
    idempotencyKey,
    requestHash: createHash("sha256").update(idempotencyKey).digest("hex"),
    createdAt: new Date("2026-08-03T00:00:00.000Z"),
    quote: {
      conditionId: "0xcondition",
      tokenId: "123",
      marketQuestion: "Will the paper repository stay consistent?",
      outcome: "Yes",
      side,
      shares: Number(quantity).toFixed(6),
      averagePrice: Number(averagePrice).toFixed(6),
      limitPrice: Number(averagePrice).toFixed(6),
      grossNotional: Number(grossNotional).toFixed(6),
      feeRate: "0.000000",
      fee: "0.00000",
      cashEffect: Number(cashEffect).toFixed(6),
      observedAt: "2026-08-03T00:00:00.000Z",
    },
  };
}
