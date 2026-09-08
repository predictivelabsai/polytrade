import { describe, expect, it } from "vitest";

import {
  AgentPredictionGrader,
  gradePrediction,
  normalizeOutcome,
} from "../src/agent-accuracy.js";
import { notFound } from "../src/errors.js";
import type { MarketResolution } from "../src/polymarket.js";
import { FakePolymarket, MemoryAgentPredictionStore } from "./fakes.js";

// Dates sit in the past so claimPending's grace predicate (made_at <= now)
// admits the rows against the real clock.
const MADE_AT = new Date("2026-08-10T00:00:00.000Z");
const CLOSED_AT = "2026-08-16T02:00:00.000Z";

function resolutionFixture(overrides: Partial<MarketResolution> = {}): MarketResolution {
  return {
    market: {
      id: "market-1",
      conditionId: "0xcondition",
      slug: "fed-rates-september",
      question: "Will the Fed hold rates in September?",
      description: "",
      outcomes: ["Yes", "No"],
      outcomePrices: ["1", "0"],
      clobTokenIds: ["123", "456"],
      active: false,
      closed: true,
      acceptingOrders: false,
      enableOrderBook: true,
      archived: false,
      restricted: false,
      minimumOrderSize: "5",
      minimumTickSize: "0.01",
      endDate: "2026-09-16T00:00:00.000Z",
      startDate: "2026-08-01T00:00:00.000Z",
      createdAt: "2026-08-01T00:00:00.000Z",
      closedTime: CLOSED_AT,
      liquidity: "1000",
      volume: "5000",
    },
    winner: "Yes",
    closedTime: CLOSED_AT,
    category: "Economics",
    tags: ["Economics", "All"],
    observedAt: "2026-09-17T00:00:00.000Z",
    ...overrides,
  };
}

async function claimedPrediction(
  store: MemoryAgentPredictionStore,
  overrides: { predictedOutcome?: string; madeAt?: Date; conditionId?: string } = {},
) {
  const record = await store.record(
    "assethero:user-1",
    {
      conditionId: overrides.conditionId ?? "0xcondition",
      marketQuestion: "Will the Fed hold rates in September?",
      predictedOutcome: overrides.predictedOutcome ?? "Yes",
    },
    overrides.madeAt ?? MADE_AT,
  );
  return record.predictionId;
}

describe("normalizeOutcome", () => {
  it("trims, collapses whitespace, and casefolds", () => {
    expect(normalizeOutcome("  Kamala   Harris ")).toBe("kamala harris");
    expect(normalizeOutcome("YES")).toBe(normalizeOutcome("yes"));
  });
});

describe("gradePrediction", () => {
  it("grades a hit case-insensitively and a miss", () => {
    const resolution = resolutionFixture();
    expect(gradePrediction(resolution, { predictedOutcome: "yes", madeAt: MADE_AT }))
      .toEqual({ kind: "graded", hit: true, winner: "Yes" });
    expect(gradePrediction(resolution, { predictedOutcome: "NO", madeAt: MADE_AT }))
      .toEqual({ kind: "graded", hit: false, winner: "Yes" });
  });

  it("keeps the prediction open while the market is still live", () => {
    const open = resolutionFixture({
      market: {
        ...(resolutionFixture().market),
        closed: false,
        acceptingOrders: true,
        outcomePrices: ["0.4", "0.6"],
      },
      winner: null,
      closedTime: null,
    });
    expect(gradePrediction(open, { predictedOutcome: "Yes", madeAt: MADE_AT })).toEqual({ kind: "open" });
  });

  it("voids a call recorded after the market resolved", () => {
    const resolution = resolutionFixture();
    const late = new Date(new Date(CLOSED_AT).getTime() + 60_000);
    expect(gradePrediction(resolution, { predictedOutcome: "Yes", madeAt: late }))
      .toEqual({ kind: "void", reason: "Recorded after the market resolved" });
  });

  it("voids a closed market with no clear binary winner", () => {
    const ambiguous = resolutionFixture({
      market: { ...(resolutionFixture().market), outcomePrices: ["0.5", "0.5"] },
      winner: null,
    });
    expect(gradePrediction(ambiguous, { predictedOutcome: "Yes", madeAt: MADE_AT }).kind).toBe("void");
  });
});

describe("AgentPredictionGrader", () => {
  function grader(store: MemoryAgentPredictionStore, polymarket: FakePolymarket, options = {}) {
    return new AgentPredictionGrader(store, polymarket, {
      graceMs: 0,
      batchSize: 10,
      ...options,
    });
  }

  it("grades a resolved prediction as a hit with category and snapshot", async () => {
    const store = new MemoryAgentPredictionStore();
    const polymarket = new FakePolymarket();
    polymarket.marketResolution = resolutionFixture();
    const id = await claimedPrediction(store);
    const count = await grader(store, polymarket).runOnce();

    expect(count).toBe(1);
    const row = store.predictions.get(id)!;
    expect(row.status).toBe("GRADED");
    expect(row.hit).toBe(true);
    expect(row.gradedOutcome).toBe("Yes");
    expect(row.category).toBe("Economics");
    expect(row.marketSlug).toBe("fed-rates-september");
    expect(row.resolutionPrices).toEqual(["1", "0"]);
  });

  it("records a miss case-insensitively", async () => {
    const store = new MemoryAgentPredictionStore();
    const polymarket = new FakePolymarket();
    polymarket.marketResolution = resolutionFixture({ winner: "No" });
    const id = await claimedPrediction(store, { predictedOutcome: "YES" });
    await grader(store, polymarket).runOnce();

    const row = store.predictions.get(id)!;
    expect(row.status).toBe("GRADED");
    expect(row.hit).toBe(false);
  });

  it("releases and retries later while the market is still open", async () => {
    const store = new MemoryAgentPredictionStore();
    const polymarket = new FakePolymarket();
    polymarket.marketResolution = resolutionFixture({ winner: null, closedTime: null });
    polymarket.marketResolution.market.closed = false;
    const id = await claimedPrediction(store);
    const before = Date.now();
    await grader(store, polymarket).runOnce();

    const row = store.predictions.get(id)!;
    expect(row.status).toBe("PENDING");
    expect(row.nextGradeAt.getTime()).toBeGreaterThanOrEqual(before + 3_500_000);
  });

  it("voids a prediction recorded after the market resolved", async () => {
    const store = new MemoryAgentPredictionStore();
    const polymarket = new FakePolymarket();
    polymarket.marketResolution = resolutionFixture();
    const id = await claimedPrediction(store, { madeAt: new Date("2026-08-16T03:00:00.000Z") });
    await grader(store, polymarket).runOnce();

    const row = store.predictions.get(id)!;
    expect(row.status).toBe("VOID");
    expect(row.voidReason).toBe("Recorded after the market resolved");
  });

  it("voids an ambiguous 50-50 resolution", async () => {
    const store = new MemoryAgentPredictionStore();
    const polymarket = new FakePolymarket();
    polymarket.marketResolution = resolutionFixture({
      winner: null,
      market: { ...(resolutionFixture().market), outcomePrices: ["0.5", "0.5"] },
    });
    const id = await claimedPrediction(store);
    await grader(store, polymarket).runOnce();

    const row = store.predictions.get(id)!;
    expect(row.status).toBe("VOID");
  });

  it("backs off exponentially on unavailable metadata, then voids at the attempt cap", async () => {
    const store = new MemoryAgentPredictionStore();
    const polymarket = new FakePolymarket();
    polymarket.getMarketResolution = async () => {
      throw notFound("The paper market is not listed by Polymarket");
    };
    const id = await claimedPrediction(store);
    const runner = grader(store, polymarket, { maxAttempts: 3 });

    const firstAt = Date.now();
    await runner.runOnce();
    expect(store.predictions.get(id)!.gradeAttempts).toBe(1);
    expect(store.predictions.get(id)!.nextGradeAt.getTime() - firstAt).toBeGreaterThanOrEqual(59_000);

    store.predictions.get(id)!.nextGradeAt = new Date(Date.now() - 1_000);
    await runner.runOnce();
    expect(store.predictions.get(id)!.gradeAttempts).toBe(2);

    store.predictions.get(id)!.nextGradeAt = new Date(Date.now() - 1_000);
    await runner.runOnce();
    const row = store.predictions.get(id)!;
    expect(row.status).toBe("VOID");
    expect(row.voidReason).toBe("Market metadata unavailable after repeated attempts");
    await runner.close();
  });

  it("caps the batch size per run", async () => {
    const store = new MemoryAgentPredictionStore();
    const polymarket = new FakePolymarket();
    polymarket.marketResolution = resolutionFixture();
    for (let index = 0; index < 3; index += 1) {
      await claimedPrediction(store, {
        conditionId: `0xcondition-${index}`,
        predictedOutcome: index === 0 ? "No" : "Yes",
      });
    }
    const count = await grader(store, polymarket, { batchSize: 2 }).runOnce();
    expect(count).toBe(2);
    expect([...store.predictions.values()].filter((row) => row.status === "GRADED")).toHaveLength(2);
  });

  it("reclaims a prediction whose lease has expired", async () => {
    const store = new MemoryAgentPredictionStore();
    const polymarket = new FakePolymarket();
    polymarket.marketResolution = resolutionFixture();
    const id = await claimedPrediction(store);

    // A crashed worker leaves a stale lease behind.
    await store.claimPending("crashed-owner", new Date(), new Date(Date.now() - 60_000), 0, 10);
    const count = await grader(store, polymarket).runOnce();
    expect(count).toBe(1);
    expect(store.predictions.get(id)!.status).toBe("GRADED");
  });
});