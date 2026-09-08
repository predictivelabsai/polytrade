import { describe, expect, it } from "vitest";

import {
  bestBidFromOrderBook,
  paperLiquidationValue,
  PaperPricingError,
  quotePaperOrder,
} from "../src/paper-pricing.js";

const identity = {
  conditionId: "0xcondition",
  tokenId: "123",
  marketQuestion: "Will pricing remain deterministic?",
  outcome: "Yes",
};

const observedAt = "2026-08-03T00:00:00.000Z";

describe("paper pricing", () => {
  it("sorts and sweeps asks while applying the nonlinear market fee per level", () => {
    const quote = quotePaperOrder(
      { conditionId: "0xcondition", tokenId: "123", side: "BUY", shares: "4" },
      identity,
      {
        bids: [{ price: "0.39", size: "20" }],
        asks: [{ price: "0.50", size: "3" }, { price: "0.40", size: "2" }],
        observedAt,
      },
      "0.04",
    );

    expect(quote).toMatchObject({
      averagePrice: "0.450000",
      limitPrice: "0.500000",
      grossNotional: "1.800000",
      fee: "0.03920",
      cashEffect: "-1.839200",
    });
  });

  it("sorts bids descending for sells and reports the current best bid", () => {
    const book = {
      bids: [{ price: "0.30", size: "10" }, { price: "0.45", size: "2" }, { price: "0.40", size: "5" }],
      asks: [],
      observedAt,
    };
    const quote = quotePaperOrder(
      { conditionId: "0xcondition", tokenId: "123", side: "SELL", shares: "4" },
      identity,
      book,
      "0",
    );

    expect(quote).toMatchObject({ averagePrice: "0.425000", limitPrice: "0.400000", cashEffect: "1.700000" });
    expect(bestBidFromOrderBook(book)).toEqual({ price: "0.450000", observedAt });
  });

  it("distinguishes a moved price bound from genuinely insufficient depth", () => {
    const book = {
      bids: [],
      asks: [{ price: "0.40", size: "2" }, { price: "0.50", size: "3" }],
      observedAt,
    };
    expect(() => quotePaperOrder(
      { conditionId: "0xcondition", tokenId: "123", side: "BUY", shares: "4" },
      identity,
      book,
      "0",
      "0.45",
    )).toThrowError(expect.objectContaining<Partial<PaperPricingError>>({ reason: "PRICE_MOVED" }));
    expect(() => quotePaperOrder(
      { conditionId: "0xcondition", tokenId: "123", side: "BUY", shares: "6" },
      identity,
      book,
      "0",
    )).toThrowError(expect.objectContaining<Partial<PaperPricingError>>({ reason: "INSUFFICIENT_LIQUIDITY" }));
  });

  it("rejects malformed levels and calculates fee-aware liquidation value", () => {
    expect(() => quotePaperOrder(
      { conditionId: "0xcondition", tokenId: "123", side: "BUY", shares: "1" },
      identity,
      { bids: [], asks: [{ price: "1.2", size: "1" }], observedAt },
      "0",
    )).toThrowError(expect.objectContaining<Partial<PaperPricingError>>({ reason: "MALFORMED_BOOK" }));
    expect(paperLiquidationValue("10", "0.50", "0.04")).toBe("4.900000");
  });
});
