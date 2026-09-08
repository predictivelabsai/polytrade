import { describe, expect, it } from "vitest";

import { maximumExposure, orderRiskSummary, parseCashExposureLimit } from "./order";

describe("maximumExposure", () => {
  it("calculates resting buy exposure without changing decimal inputs", () => {
    expect(maximumExposure({
      action: "create",
      execution: "GTC",
      tokenId: "1",
      marketId: "market",
      marketQuestion: "Question?",
      outcome: "Yes",
      side: "BUY",
      rationale: "",
      observedAt: "2026-08-03T00:00:00.000Z",
      price: "0.42",
      size: "10",
      postOnly: false,
    })).toBe("4.2 USDC");
  });

  it("labels an immediate sell in shares", () => {
    expect(maximumExposure({
      action: "create",
      execution: "FAK",
      tokenId: "1",
      marketId: "market",
      marketQuestion: "Question?",
      outcome: "No",
      side: "SELL",
      rationale: "",
      observedAt: "2026-08-03T00:00:00.000Z",
      amount: "7.5",
      limitPrice: "0.3",
      postOnly: false,
    })).toBe("7.5 shares");
  });

  it("summarizes the signed buy worst case and detects stale market data", () => {
    const proposal = {
      action: "create" as const,
      execution: "FAK" as const,
      tokenId: "1",
      marketId: "market",
      marketQuestion: "Question?",
      outcome: "Yes",
      side: "BUY" as const,
      rationale: "",
      observedAt: "2026-08-03T00:00:00.000Z",
      amount: "25",
      limitPrice: "0.3",
      postOnly: false as const,
    };
    const risk = orderRiskSummary(proposal, Date.parse("2026-08-03T00:03:00.000Z"));

    expect(risk.cashExposure).toBe(25);
    expect(risk.worstCase).toBe("Up to 25 USDC can fill at 0.3 or better.");
    expect(risk.stale).toBe(true);
  });

  it("accepts only a bounded positive browser cash guard", () => {
    expect(parseCashExposureLimit("100.5")).toBe(100.5);
    expect(parseCashExposureLimit("0")).toBeNull();
    expect(parseCashExposureLimit("1000001")).toBeNull();
  });
});
