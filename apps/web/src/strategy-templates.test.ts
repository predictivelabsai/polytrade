import { describe, expect, it } from "vitest";

import type { MarketSearchMarket } from "@polytrade/contracts";
import { strategyTemplates } from "@polytrade/contracts";

import { resolveTemplateTokenId, templateDraft } from "./strategy-templates";

function market(outcomePrices: string[], overrides: Partial<MarketSearchMarket> = {}): MarketSearchMarket {
  return {
    conditionId: "0xcondition",
    question: "Test market?",
    outcomes: outcomePrices.map((_, index) => (index === 0 ? "Yes" : "No")),
    outcomePrices,
    clobTokenIds: outcomePrices.map((_, index) => String(100 + index)),
    active: true,
    closed: false,
    acceptingOrders: true,
    enableOrderBook: true,
    archived: false,
    restricted: false,
    minimumOrderSize: "5",
    minimumTickSize: "0.01",
    ...overrides,
  } as MarketSearchMarket;
}

const longshot = strategyTemplates.find((template) => template.id === "longshot-fade")!;
const sniper = strategyTemplates.find((template) => template.id === "ev-sniping")!;

describe("resolveTemplateTokenId", () => {
  it("picks the highest-priced outcome for higher_price templates", () => {
    expect(resolveTemplateTokenId(longshot, market(["0.90", "0.10"]))).toBe("100");
    expect(resolveTemplateTokenId(longshot, market(["0.20", "0.80"]))).toBe("101");
  });

  it("picks the lowest-priced outcome for lower_price templates", () => {
    expect(resolveTemplateTokenId(sniper, market(["0.90", "0.10"]))).toBe("101");
    expect(resolveTemplateTokenId(sniper, market(["0.30", "0.70"]))).toBe("100");
  });

  it("breaks ties toward index 0", () => {
    expect(resolveTemplateTokenId(longshot, market(["0.50", "0.50"]))).toBe("100");
    expect(resolveTemplateTokenId(sniper, market(["0.50", "0.50"]))).toBe("100");
  });

  it("returns null for malformed markets", () => {
    expect(resolveTemplateTokenId(longshot, market(["0.50"]))).toBeNull();
    expect(resolveTemplateTokenId(longshot, market(["x", "0.50"]))).toBeNull();
  });
});

describe("templateDraft", () => {
  it("translates offsets against the reference price at the midpoint", () => {
    const draft = templateDraft(longshot, market(["0.90", "0.10"]), "100")!;
    expect(draft.entryPrice).toBe("0.88");
    expect(draft.exitPrice).toBe("0.93");
    expect(draft.sharesPerOrder).toBe("20");
    expect(draft.maxPosition).toBe("80");
    expect(draft.intervalSeconds).toBe("30");
  });

  it("clamps prices at both extremes", () => {
    // Reference 0.99: the exit offset would run past the ceiling.
    const high = templateDraft(longshot, market(["0.99", "0.01"]), "100")!;
    expect(Number(high.entryPrice)).toBeLessThanOrEqual(0.99);
    expect(Number(high.exitPrice)).toBeLessThanOrEqual(0.99);
    // Reference 0.01: the entry offset would run under the floor, and the
    // fallback must keep the band valid.
    const low = templateDraft(sniper, market(["0.99", "0.01"]), "101")!;
    expect(Number(low.entryPrice)).toBeGreaterThanOrEqual(0.01);
    expect(Number(low.exitPrice)).toBeGreaterThan(Number(low.entryPrice));
  });

  it("returns null when clamping collapses the band at the ceiling", () => {
    // Entry clamps to 0.99; no valid exit exists above it, so the caller
    // falls back to the default auto-fit.
    const template = { ...longshot, band: { ...longshot.band, entryOffset: "0.05", exitOffset: "0.06" } };
    expect(templateDraft(template, market(["0.97", "0.03"]), "100")).toBeNull();
  });

  it("keeps the template order size when it clears the market minimum", () => {
    const draft = templateDraft(sniper, market(["0.60", "0.40"]), "101")!;
    expect(draft.sharesPerOrder).toBe("10");
  });

  it("raises the minimum when the market demands a larger order", () => {
    const draft = templateDraft(sniper, market(["0.60", "0.40"], { minimumOrderSize: "50" }), "101")!;
    expect(draft.sharesPerOrder).toBe("50");
    expect(Number(draft.maxPosition)).toBe(50 * Number(sniper.band.positionMultiplier));
  });

  it("returns null when the token is not in the market", () => {
    expect(templateDraft(longshot, market(["0.90", "0.10"]), "999")).toBeNull();
  });
});