import type {
  PaperOrderResponse,
  PaperPortfolio,
  PaperQuote,
  PaperStrategySnapshot,
  PaperStrategyStartRequest,
} from "@polytrade/contracts";
import { describe, expect, it, vi } from "vitest";

import { AppError } from "../src/errors.js";
import type {
  PaperStrategyClaim,
  PaperStrategyScanResult,
  PaperStrategyStartResult,
  PaperStrategyStore,
} from "../src/paper-strategy-store.js";
import {
  PaperStrategyBackgroundRunner,
  PaperStrategyService,
  type PaperStrategyTradingPort,
} from "../src/paper-strategy.js";
import type { Principal } from "../src/types.js";

const now = new Date("2026-08-04T00:00:00.000Z");
const principal: Principal = {
  id: "assethero:strategy-user",
  issuer: "assethero",
  subject: "strategy-user",
  scopes: new Set(["research"]),
};
const request: PaperStrategyStartRequest = {
  conditionId: "0xcondition",
  tokenId: "123",
  entryPrice: "0.350000",
  exitPrice: "0.650000",
  sharesPerOrder: "10.000000",
  maxPosition: "50.000000",
  intervalSeconds: 15,
};

function claim(overrides: Partial<PaperStrategyClaim> = {}): PaperStrategyClaim {
  return {
    strategyId: "11111111-1111-4111-8111-111111111111",
    principalId: principal.id,
    conditionId: request.conditionId,
    tokenId: request.tokenId,
    marketQuestion: "Will the background strategy pass?",
    outcome: "Yes",
    minimumOrderSize: "1.000000",
    entryPrice: request.entryPrice,
    exitPrice: request.exitPrice,
    sharesPerOrder: request.sharesPerOrder,
    maxPosition: request.maxPosition,
    intervalSeconds: request.intervalSeconds,
    status: "RUNNING",
    ordersPlaced: 0,
    scansCompleted: 0,
    lastAction: "STARTED",
    lastMessage: "Started",
    lastQuoteSide: null,
    lastQuotePrice: null,
    lastScannedAt: null,
    nextScanAt: now.toISOString(),
    startedAt: now.toISOString(),
    stoppedAt: null,
    updatedAt: now.toISOString(),
    scanId: "22222222-2222-4222-8222-222222222222",
    leaseOwner: "runner-1",
    ...overrides,
  };
}

function portfolio(positionShares?: string): PaperPortfolio {
  return {
    initialCash: "10000.000000",
    cash: "10000.000000",
    positionsValue: "0.000000",
    equity: "10000.000000",
    realizedPnl: "0.000000",
    unrealizedPnl: "0.000000",
    totalPnl: "0.000000",
    totalFees: "0.000000",
    positions: positionShares ? [{
      conditionId: request.conditionId,
      tokenId: request.tokenId,
      marketQuestion: "Will the background strategy pass?",
      outcome: "Yes",
      shares: positionShares,
      costBasis: "8.000000",
      averageCost: "0.400000",
      bestBid: "0.390000",
      liquidationValue: "7.800000",
      unrealizedPnl: "-0.200000",
      markStatus: "current",
      markedAt: now.toISOString(),
    }] : [],
    warnings: [],
    observedAt: now.toISOString(),
  };
}

function quote(side: "BUY" | "SELL", averagePrice: string): PaperQuote {
  return {
    conditionId: request.conditionId,
    tokenId: request.tokenId,
    marketQuestion: "Will the background strategy pass?",
    outcome: "Yes",
    side,
    shares: "10.000000",
    averagePrice,
    limitPrice: averagePrice,
    grossNotional: "3.000000",
    feeRate: "0.000000",
    fee: "0.00000",
    cashEffect: side === "BUY" ? "-3.000000" : "3.000000",
    observedAt: now.toISOString(),
  };
}

function orderResponse(side: "BUY" | "SELL", averagePrice: string): PaperOrderResponse {
  return {
    fill: {
      fillId: "33333333-3333-4333-8333-333333333333",
      kind: side,
      conditionId: request.conditionId,
      tokenId: request.tokenId,
      marketQuestion: "Will the background strategy pass?",
      outcome: "Yes",
      shares: "10.000000",
      averagePrice,
      grossNotional: "3.000000",
      feeRate: "0.000000",
      fee: "0.00000",
      cashEffect: side === "BUY" ? "-3.000000" : "3.000000",
      realizedPnl: "0.000000",
      observedAt: now.toISOString(),
      createdAt: now.toISOString(),
    },
    portfolio: portfolio(side === "BUY" ? "10.000000" : undefined),
  };
}

function runnerSetup(value: PaperStrategyClaim, account: PaperPortfolio) {
  let claimed = false;
  const completed: PaperStrategyScanResult[] = [];
  const failed: string[] = [];
  const strategyStore: PaperStrategyStore = {
    start: vi.fn(),
    snapshot: vi.fn(),
    stop: vi.fn(),
    claimDue: vi.fn(async () => {
      if (claimed) return [];
      claimed = true;
      return [value];
    }),
    completeClaim: vi.fn(async (_claim, result) => {
      completed.push(result);
      return true;
    }),
    failClaim: vi.fn(async (_claim, message) => {
      failed.push(message);
      return true;
    }),
    pruneEvents: vi.fn(async () => undefined),
  };
  const paper: PaperStrategyTradingPort = {
    portfolio: vi.fn(async () => account),
    quote: vi.fn(async (_principal, order) => quote(order.side, order.side === "BUY" ? "0.300000" : "0.700000")),
    strategyOrder: vi.fn(async (_principal, order) => orderResponse(order.side, order.side === "BUY" ? "0.300000" : "0.700000")),
    refresh: vi.fn(async () => account),
  };
  const runner = new PaperStrategyBackgroundRunner(strategyStore, paper, { now: () => now });
  return { completed, failed, paper, runner, strategyStore };
}

describe("PaperStrategyBackgroundRunner", () => {
  it("buys below the entry threshold and records the guarded background fill", async () => {
    const context = runnerSetup(claim(), portfolio());

    expect(await context.runner.runOnce()).toBe(1);

    expect(context.paper.strategyOrder).toHaveBeenCalledWith(
      expect.objectContaining({ id: principal.id }),
      expect.objectContaining({ side: "BUY", shares: "10.000000", limitPrice: "0.300000" }),
      "strategy:11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222:BUY",
      expect.objectContaining({ strategyId: "11111111-1111-4111-8111-111111111111", maxPosition: "50.000000" }),
    );
    expect(context.completed).toEqual([expect.objectContaining({ action: "BUY", side: "BUY" })]);
    expect(context.paper.refresh).toHaveBeenCalled();
  });

  it("checks the exit first and sells a held position above the exit threshold", async () => {
    const context = runnerSetup(claim(), portfolio("20.000000"));

    await context.runner.runOnce();

    expect(context.paper.quote).toHaveBeenCalledTimes(1);
    expect(context.paper.strategyOrder).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ side: "SELL", shares: "10.000000" }),
      expect.stringMatching(/:SELL$/),
      expect.anything(),
    );
    expect(context.completed[0]).toMatchObject({ action: "SELL", side: "SELL" });
  });

  it("records a wait and schedules another scan when the band is not crossed", async () => {
    const context = runnerSetup(claim(), portfolio());
    vi.mocked(context.paper.quote).mockResolvedValue(quote("BUY", "0.500000"));

    await context.runner.runOnce();

    expect(context.paper.strategyOrder).not.toHaveBeenCalled();
    expect(context.completed[0]).toMatchObject({ action: "WAIT" });
    expect(context.completed[0]?.message).toContain("entry 0.500000 is above 0.350000");
  });

  it("fails a strategy permanently when the market closes", async () => {
    const context = runnerSetup(claim(), portfolio());
    vi.mocked(context.paper.quote).mockRejectedValue(new AppError(409, "PAPER_MARKET_CLOSED", "Market closed"));

    await context.runner.runOnce();

    expect(context.completed).toEqual([]);
    expect(context.failed).toEqual(["Market closed"]);
  });

  it("fails a strategy permanently when the account cannot cover the buy", async () => {
    const context = runnerSetup(claim(), portfolio());
    vi.mocked(context.paper.quote).mockRejectedValue(
      new AppError(409, "PAPER_INSUFFICIENT_CASH", "The paper account does not have enough cash for this fill"),
    );

    await context.runner.runOnce();

    expect(context.completed).toEqual([]);
    expect(context.failed).toEqual([
      "Stopped: the paper account does not have enough cash for this strategy's orders (The paper account does not have enough cash for this fill).",
    ]);
  });

  it("fails a strategy permanently when the book cannot fill its size", async () => {
    const context = runnerSetup(claim(), portfolio());
    vi.mocked(context.paper.quote).mockRejectedValue(
      new AppError(409, "PAPER_INSUFFICIENT_LIQUIDITY", "The order book cannot fill 10 shares at the price band"),
    );

    await context.runner.runOnce();

    expect(context.completed).toEqual([]);
    expect(context.failed[0]).toContain("Stopped: the order book currently cannot fill orders of this size");
  });

  it("retries transient scan failures with a growing backoff instead of one row per interval", async () => {
    const context = runnerSetup(claim(), portfolio());
    vi.mocked(context.paper.quote).mockRejectedValue(
      new AppError(503, "PAPER_UNAVAILABLE", "Polymarket pricing is temporarily unavailable"),
    );
    vi.mocked(context.strategyStore.claimDue).mockImplementation(async () => [claim()]);

    await context.runner.runOnce();
    await context.runner.runOnce();

    expect(context.failed).toEqual([]);
    expect(context.completed).toHaveLength(2);
    expect(context.completed[0]).toMatchObject({ action: "ERROR" });
    // interval is 15s: the second consecutive failure doubles the retry delay.
    expect(context.strategyStore.completeClaim).toHaveBeenNthCalledWith(
      1, expect.anything(), expect.anything(), expect.anything(),
      new Date(now.getTime() + 15_000),
    );
    expect(context.strategyStore.completeClaim).toHaveBeenNthCalledWith(
      2, expect.anything(), expect.anything(), expect.anything(),
      new Date(now.getTime() + 30_000),
    );
  });

  it("resets the retry backoff after a successful scan", async () => {
    const context = runnerSetup(claim(), portfolio());
    vi.mocked(context.strategyStore.claimDue).mockImplementation(async () => [claim()]);
    let failNext = true;
    vi.mocked(context.paper.quote).mockImplementation(async () => {
      if (failNext) throw new AppError(503, "PAPER_UNAVAILABLE", "Polymarket pricing is temporarily unavailable");
      return quote("BUY", "0.500000");
    });

    await context.runner.runOnce();
    failNext = false;
    await context.runner.runOnce();
    failNext = true;
    await context.runner.runOnce();

    // The third scan failed again but as if it were the first: 15s, not 60s.
    expect(context.strategyStore.completeClaim).toHaveBeenNthCalledWith(
      3, expect.anything(), expect.anything(), expect.anything(),
      new Date(now.getTime() + 15_000),
    );
  });

  it("prunes strategy event history on the configured cadence", async () => {
    vi.useFakeTimers();
    try {
      const context = runnerSetup(claim(), portfolio());
      const runner = new PaperStrategyBackgroundRunner(context.strategyStore, context.paper, {
        now: () => now,
        pruneIntervalMs: 50,
        retainEventsPerStrategy: 200,
        eventMaxAgeDays: 30,
      });
      runner.start();
      await vi.advanceTimersByTimeAsync(80);
      expect(context.strategyStore.pruneEvents).toHaveBeenCalledWith({ retainPerStrategy: 200, maxAgeDays: 30 });
      await runner.close();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("PaperStrategyService", () => {
  const emptySnapshot: PaperStrategySnapshot = { strategy: null, events: [] };

  function setup(startResult: PaperStrategyStartResult = { state: "created", strategy: claim() }) {
    const store: PaperStrategyStore = {
      start: vi.fn(async () => startResult),
      snapshot: vi.fn(async () => emptySnapshot),
      stop: vi.fn(async () => emptySnapshot),
      claimDue: vi.fn(async () => []),
      completeClaim: vi.fn(async () => true),
      failClaim: vi.fn(async () => true),
      pruneEvents: vi.fn(async () => undefined),
    };
    const paper = {
      strategyTarget: vi.fn(async () => ({
        conditionId: request.conditionId,
        tokenId: request.tokenId,
        marketQuestion: "Will the background strategy pass?",
        outcome: "Yes",
        minimumOrderSize: "5.000000",
      })),
    };
    return { store, paper, service: new PaperStrategyService(store, paper, () => now) };
  }

  it("derives identity server-side and creates an idempotent persistent run", async () => {
    const context = setup();

    await expect(context.service.start(principal, request, "strategy-key-1")).resolves.toEqual(emptySnapshot);
    expect(context.store.start).toHaveBeenCalledWith(expect.objectContaining({
      principalId: principal.id,
      idempotencyKey: "strategy-key-1",
      marketQuestion: "Will the background strategy pass?",
      outcome: "Yes",
    }));
  });

  it("rejects a dynamic market minimum and a second active strategy", async () => {
    const tooSmall = setup();
    await expect(tooSmall.service.start(principal, { ...request, sharesPerOrder: "1" }, "strategy-key-1"))
      .rejects.toMatchObject({ code: "VALIDATION_ERROR" });

    const alreadyRunning = setup({ state: "already_running", strategy: claim() });
    await expect(alreadyRunning.service.start(principal, request, "strategy-key-2"))
      .rejects.toMatchObject({ code: "PAPER_STRATEGY_RUNNING", statusCode: 409 });
  });
});
