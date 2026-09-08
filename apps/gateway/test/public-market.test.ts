import { describe, expect, it } from "vitest";

import { AppError, notFound } from "../src/errors.js";
import type { PolymarketPort } from "../src/polymarket.js";
import { publicPriceHistoryTtlMs, PublicMarketService } from "../src/public-market.js";
import { TtlCache } from "../src/public-cache.js";
import { publicMarketSummaryFixture } from "./fakes.js";

function port(overrides: Partial<PolymarketPort> = {}): PolymarketPort {
  return {
    searchMarkets: async () => undefined,
    listActiveMarkets: async () => ({ markets: [], hasMore: false }),
    getMarket: async () => undefined,
    getPublicMarket: async () => undefined,
    getMarketByCondition: async () => undefined,
    getOrderBook: async () => undefined,
    getFeeRate: async () => "0.000000",
    getPriceHistory: async () => undefined,
    getRecentTrades: async () => undefined,
    exchangeL1Credentials: async () => ({ key: "k", secret: "s", passphrase: "p" }),
    buildOrderIntent: async () => undefined,
    preflight: async () => undefined,
    reconcileOrder: async () => undefined,
    verifyOrderSignature: async () => "hash",
    submitOrder: async () => undefined,
    getAccount: async () => undefined,
    cancel: async () => undefined,
    ...overrides,
  } as PolymarketPort;
}

function service(polymarket: PolymarketPort) {
  return new PublicMarketService(polymarket, new TtlCache());
}

describe("PublicMarketService", () => {
  it("composes detail quotes from live order books and degrades failed books to Gamma", async () => {
    const polymarket = port({
      getPublicMarket: async () => ({
        market: publicMarketSummaryFixture(),
        observedAt: "2026-08-03T00:00:00.000Z",
      }),
      getOrderBook: async (tokenId: string) => {
        if (tokenId === "123") {
          return {
            tokenId,
            bids: [{ price: "0.42", size: "5" }],
            asks: [{ price: "0.46", size: "5" }],
            minimumOrderSize: "5",
            tickSize: "0.01",
            negativeRisk: false,
            lastTradePrice: "0.44",
            observedAt: "2026-08-03T00:00:00.000Z",
          };
        }
        throw new AppError(503, "UPSTREAM_UNAVAILABLE", "order book failed");
      },
    });

    const detail = await service(polymarket).detail("fed-rates-september");
    expect(detail.quotes).toEqual([
      { outcome: "Yes", tokenId: "123", price: "0.44", bestBid: "0.42", bestAsk: "0.46", source: "order-book" },
      { outcome: "No", tokenId: "456", price: "0.565", bestBid: null, bestAsk: null, source: "gamma" },
    ]);
  });

  it("caches an unknown slug as a 404 without re-reading the adapter", async () => {
    let detailCalls = 0;
    const polymarket = port({
      getPublicMarket: async () => {
        detailCalls += 1;
        throw notFound("Public market not found");
      },
    });
    const markets = service(polymarket);

    await expect(markets.detail("nope")).rejects.toMatchObject({ statusCode: 404 });
    await expect(markets.detail("nope")).rejects.toMatchObject({ statusCode: 404 });
    expect(detailCalls).toBe(1);
  });

  it("normalizes a book and drops an empty last-trade price", async () => {
    const polymarket = port({
      getOrderBook: async () => ({
        tokenId: "123",
        market: "0xcondition",
        bids: [{ price: "0.42", size: "5" }],
        asks: [],
        minimumOrderSize: "",
        tickSize: "0.01",
        negativeRisk: false,
        lastTradePrice: "",
        observedAt: "2026-08-03T00:00:00.000Z",
      }),
    });

    const book = await service(polymarket).book("123");
    expect(book).toMatchObject({ tokenId: "123", lastTradePrice: null, asks: [] });
  });

  it("scales the price-history cache TTL by interval", () => {
    expect(publicPriceHistoryTtlMs("1h")).toBe(60_000);
    expect(publicPriceHistoryTtlMs("1d")).toBe(300_000);
    expect(publicPriceHistoryTtlMs("max")).toBe(3_600_000);
    expect(publicPriceHistoryTtlMs("nonsense")).toBe(300_000);
  });
});