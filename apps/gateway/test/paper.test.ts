import type { PaperFill, PaperPortfolio } from "@polytrade/contracts";
import { describe, expect, it, vi } from "vitest";

import { PaperTradingService } from "../src/paper.js";
import type { PaperStore } from "../src/paper-store.js";
import type { Principal } from "../src/types.js";
import { FakePolymarket } from "./fakes.js";

const principal: Principal = {
  id: "assethero:paper-user",
  issuer: "assethero",
  subject: "paper-user",
  scopes: new Set(["research"]),
};
const now = new Date("2026-08-03T00:00:00.000Z");

function emptyPortfolio(overrides: Partial<PaperPortfolio> = {}): PaperPortfolio {
  return {
    initialCash: "10000.000000",
    cash: "10000.000000",
    positionsValue: "0.000000",
    equity: "10000.000000",
    realizedPnl: "0.000000",
    unrealizedPnl: "0.000000",
    totalPnl: "0.000000",
    totalFees: "0.000000",
    positions: [],
    warnings: [],
    observedAt: now.toISOString(),
    ...overrides,
  };
}

function setup(portfolio = emptyPortfolio()) {
  const polymarket = new FakePolymarket();
  polymarket.paperOrderBooks.set("123", {
    bids: [{ price: "0.39", size: "100" }],
    asks: [{ price: "0.40", size: "5" }, { price: "0.42", size: "100" }],
    observedAt: now.toISOString(),
  });
  polymarket.paperFeeRates.set("123", "0.04");
  const refresh = vi.fn<PaperStore["refresh"]>(async () => undefined);
  const store: PaperStore = {
    portfolio: vi.fn(async (_principalId: string, warnings: string[] = []) => ({ ...portfolio, warnings })),
    replay: vi.fn(async () => null),
    execute: vi.fn(async () => ({ state: "insufficient_cash" as const })),
    refresh,
    fills: vi.fn(async (_principalId: string, limit: number, offset: number) => ({ items: [], total: 0, limit, offset })),
  };
  return { polymarket, store, refresh, service: new PaperTradingService(store, polymarket, () => now) };
}

describe("PaperTradingService", () => {
  it("derives market identity server-side and previews a complete fee-aware sweep", async () => {
    const context = setup();
    const quote = await context.service.quote(principal, {
      conditionId: "0xcondition",
      tokenId: "123",
      side: "BUY",
      shares: "10",
    });

    expect(quote).toMatchObject({
      marketQuestion: "Will this paper trade pass?",
      outcome: "Yes",
      averagePrice: "0.410000",
      limitPrice: "0.420000",
      grossNotional: "4.100000",
    });
  });

  it("checks owned shares before offering a sell preview", async () => {
    const context = setup();
    await expect(context.service.quote(principal, {
      conditionId: "0xcondition",
      tokenId: "123",
      side: "SELL",
      shares: "1",
    })).rejects.toMatchObject({ code: "PAPER_INSUFFICIENT_SHARES", statusCode: 409 });
  });

  it("maps a moved execution bound to a stable paper conflict", async () => {
    const context = setup();
    await expect(context.service.order(principal, {
      conditionId: "0xcondition",
      tokenId: "123",
      side: "BUY",
      shares: "10",
      limitPrice: "0.40",
    }, "paper-order-key-1")).rejects.toMatchObject({ code: "PAPER_PRICE_MOVED", statusCode: 409 });
  });

  it("turns final 1/0 outcome prices into settlement instructions", async () => {
    const position = {
      conditionId: "0xcondition",
      tokenId: "123",
      marketQuestion: "Will this paper trade pass?",
      outcome: "Yes",
      shares: "10.000000",
      costBasis: "4.100000",
      averageCost: "0.410000",
      bestBid: "0.390000",
      liquidationValue: "3.806400",
      unrealizedPnl: "-0.293600",
      markStatus: "current" as const,
      markedAt: now.toISOString(),
    };
    const context = setup(emptyPortfolio({ positions: [position] }));
    context.polymarket.paperMarket = {
      ...context.polymarket.paperMarket,
      active: false,
      closed: true,
      acceptingOrders: false,
      outcomePrices: ["1", "0"],
    };

    await context.service.refresh(principal);

    expect(context.refresh).toHaveBeenCalledWith(principal.id, [{
      kind: "settlement",
      conditionId: "0xcondition",
      tokenId: "123",
      resolutionPrice: "1",
      observedAt: now,
    }]);
  });

  it("keeps the last mark stale when current book data is unavailable", async () => {
    const position = {
      conditionId: "0xcondition",
      tokenId: "123",
      marketQuestion: "Will this paper trade pass?",
      outcome: "Yes",
      shares: "10.000000",
      costBasis: "4.100000",
      averageCost: "0.410000",
      bestBid: "0.390000",
      liquidationValue: "3.806400",
      unrealizedPnl: "-0.293600",
      markStatus: "current" as const,
      markedAt: now.toISOString(),
    };
    const context = setup(emptyPortfolio({ positions: [position] }));
    context.polymarket.paperOrderBooks.set("123", { malformed: true });

    const refreshed = await context.service.refresh(principal);

    expect(context.refresh).toHaveBeenCalledWith(principal.id, [{ kind: "stale", conditionId: "0xcondition", tokenId: "123" }]);
    expect(refreshed.warnings[0]).toContain("malformed order book");
  });

  it("returns an idempotent fill before touching changing market data", async () => {
    const context = setup();
    const fill: PaperFill = {
      fillId: "55555555-5555-4555-8555-555555555555",
      kind: "BUY",
      conditionId: "0xcondition",
      tokenId: "123",
      marketQuestion: "Will this paper trade pass?",
      outcome: "Yes",
      shares: "10.000000",
      averagePrice: "0.410000",
      grossNotional: "4.100000",
      feeRate: "0.040000",
      fee: "0.09840",
      cashEffect: "-4.198400",
      realizedPnl: "0.000000",
      observedAt: now.toISOString(),
      createdAt: now.toISOString(),
    };
    vi.mocked(context.store.replay).mockResolvedValue({ state: "replayed", fill });
    context.polymarket.paperMarket = { ...context.polymarket.paperMarket, closed: true, active: false, acceptingOrders: false };

    const response = await context.service.order(principal, {
      conditionId: "0xcondition",
      tokenId: "123",
      side: "BUY",
      shares: "10",
      limitPrice: "0.42",
    }, "paper-order-key-1");

    expect(response.fill).toEqual(fill);
    expect(context.store.execute).not.toHaveBeenCalled();
  });
});
