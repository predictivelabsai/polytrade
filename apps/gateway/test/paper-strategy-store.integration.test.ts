import { createHash, randomUUID } from "node:crypto";

import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { bootstrapDatabase } from "../src/bootstrap.js";
import { PostgresPaperStore, type PaperExecutionInput } from "../src/paper-store.js";
import {
  PostgresPaperStrategyStore,
  type PaperStrategyStartInput,
} from "../src/paper-strategy-store.js";

const connectionString = process.env.TEST_DATABASE_URL;
const integration = connectionString ? describe : describe.skip;

integration("PostgresPaperStrategyStore", () => {
  let pool: pg.Pool;
  let store: PostgresPaperStrategyStore;
  let paperStore: PostgresPaperStore;
  const startedAt = new Date("2026-08-04T00:00:00.000Z");

  beforeAll(async () => {
    await bootstrapDatabase(connectionString!);
    pool = new pg.Pool({ connectionString, options: "-c search_path=polytrade,public", max: 4 });
    store = new PostgresPaperStrategyStore(pool);
    paperStore = new PostgresPaperStore(pool, () => startedAt);
  });

  afterAll(async () => {
    await pool?.end();
  });

  it("allows one active strategy per owner and replays the start key atomically", async () => {
    const principalId = `strategy-start:${randomUUID()}`;
    const first = startInput(principalId, "start-key-1");
    expect((await store.start(first)).state).toBe("created");
    expect((await store.start(first)).state).toBe("replayed");
    expect((await store.start({ ...startInput(principalId, "start-key-2"), strategyId: randomUUID() })).state)
      .toBe("already_running");

    const mismatch = await store.start({ ...first, requestHash: "f".repeat(64) });
    expect(mismatch.state).toBe("key_mismatch");
  });

  it("leases each due scan to one runner and records completion once", async () => {
    const principalId = `strategy-lease:${randomUUID()}`;
    await store.start(startInput(principalId, "lease-key-1"));
    const claimTime = new Date(startedAt.getTime() + 1_000);
    const [left, right] = await Promise.all([
      store.claimDue("runner-left", claimTime, new Date(claimTime.getTime() + 60_000), 10),
      store.claimDue("runner-right", claimTime, new Date(claimTime.getTime() + 60_000), 10),
    ]);
    expect([...left, ...right]).toHaveLength(1);
    const claim = [...left, ...right][0]!;
    expect(await store.completeClaim(claim, {
      action: "WAIT",
      message: "No thresholds crossed.",
    }, claimTime, new Date(claimTime.getTime() + 15_000))).toBe(true);
    expect(await store.completeClaim(claim, {
      action: "WAIT",
      message: "Duplicate completion.",
    }, claimTime, new Date(claimTime.getTime() + 15_000))).toBe(false);

    const snapshot = await store.snapshot(principalId);
    expect(snapshot.strategy).toMatchObject({ status: "RUNNING", scansCompleted: 1, lastAction: "WAIT" });
    expect(snapshot.events.filter((event) => event.action === "WAIT")).toHaveLength(1);
  });

  it("stopping a leased strategy blocks its atomic paper fill guard", async () => {
    const principalId = `strategy-stop:${randomUUID()}`;
    await store.start(startInput(principalId, "stop-key-1"));
    const claimTime = new Date(startedAt.getTime() + 1_000);
    const claim = (await store.claimDue("runner-stop", claimTime, new Date(claimTime.getTime() + 60_000), 1))[0]!;
    await store.stop(principalId, new Date(claimTime.getTime() + 2_000));

    const result = await paperStore.execute(execution(principalId, claim));
    expect(result.state).toBe("strategy_stopped");
    expect((await paperStore.portfolio(principalId)).cash).toBe("10000.000000");
    expect((await store.snapshot(principalId)).strategy?.status).toBe("STOPPED");
  });

  it("prunes event history but always keeps the newest rows per strategy", async () => {
    const principalId = `strategy-prune:${randomUUID()}`;
    await store.start(startInput(principalId, "prune-key-1"));
    const claimTime = new Date(startedAt.getTime() + 1_000);
    for (let index = 0; index < 4; index++) {
      const claim = (await store.claimDue("runner-prune", new Date(claimTime.getTime() + index * 1_000),
        new Date(claimTime.getTime() + (index + 1) * 60_000), 1))[0]!;
      expect(await store.completeClaim(claim, {
        action: "WAIT",
        message: `Scan ${index + 1}.`,
      }, new Date(claimTime.getTime() + index * 1_000),
        new Date(claimTime.getTime() + (index + 1) * 16_000))).toBe(true);
    }

    // Keep the newest 2 per strategy; everything else (older than 30 days) goes.
    await store.pruneEvents({ retainPerStrategy: 2, maxAgeDays: 30 });
    const afterPrune = await store.snapshot(principalId);
    const waitEvents = afterPrune.events.filter((event) => event.action === "WAIT");
    expect(waitEvents).toHaveLength(2);
    expect(waitEvents.map((event) => event.message).sort()).toEqual(["Scan 3.", "Scan 4."].sort());
  });
});

function startInput(principalId: string, key: string): PaperStrategyStartInput {
  return {
    strategyId: randomUUID(),
    principalId,
    idempotencyKey: key,
    requestHash: createHash("sha256").update(key).digest("hex"),
    request: {
      conditionId: "0xcondition",
      tokenId: "123",
      entryPrice: "0.350000",
      exitPrice: "0.650000",
      sharesPerOrder: "10.000000",
      maxPosition: "50.000000",
      intervalSeconds: 15,
    },
    marketQuestion: "Will the persistent strategy pass?",
    outcome: "Yes",
    minimumOrderSize: "1.000000",
    startedAt: new Date("2026-08-04T00:00:00.000Z"),
  };
}

function execution(
  principalId: string,
  claim: Awaited<ReturnType<PostgresPaperStrategyStore["claimDue"]>>[number],
): PaperExecutionInput {
  return {
    principalId,
    idempotencyKey: `strategy:${claim.strategyId}:${claim.scanId}:BUY`,
    requestHash: createHash("sha256").update(claim.scanId).digest("hex"),
    createdAt: new Date("2026-08-04T00:00:03.000Z"),
    strategyGuard: {
      strategyId: claim.strategyId,
      scanId: claim.scanId,
      leaseOwner: claim.leaseOwner,
      maxPosition: claim.maxPosition,
    },
    quote: {
      conditionId: "0xcondition",
      tokenId: "123",
      marketQuestion: "Will the persistent strategy pass?",
      outcome: "Yes",
      side: "BUY",
      shares: "10.000000",
      averagePrice: "0.300000",
      limitPrice: "0.300000",
      grossNotional: "3.000000",
      feeRate: "0.000000",
      fee: "0.00000",
      cashEffect: "-3.000000",
      observedAt: "2026-08-04T00:00:03.000Z",
    },
  };
}
