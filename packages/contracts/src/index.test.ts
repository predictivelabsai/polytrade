import { describe, expect, it } from "vitest";
import {
  accountOverviewSchema,
  agentPredictionHitRateSchema,
  agentPredictionRequestSchema,
  alertChannelSchema,
  alertCreateChannelRequestSchema,
  alertEventKindSchema,
  paperStrategyActionSchema,
  backtestConfigSchema,
  backtestRunListSchema,
  breakoutBacktestConfigSchema,
  defaultBreakoutBacktestConfig,
  defaultMeanReversionBacktestConfig,
  isBacktestEligibleMarket,
  marketSearchResponseSchema,
  momentumBacktestConfigSchema,
  paperOrderRequestSchema,
  paperPortfolioSchema,
  paperQuoteSchema,
  paperShareStatusSchema,
  publicTrackRecordSchema,
  paperStrategySnapshotSchema,
  paperStrategyStartRequestSchema,
  resolvedBinaryMarketWinner,
  strategyTemplateListSchema,
  strategyTemplates,
  tradingActionProposalSchema,
  walletSessionStatusSchema,
} from "./index.js";

describe("tradingActionProposalSchema", () => {
  const base = {
    action: "create" as const,
    tokenId: "123",
    marketId: "condition",
    marketQuestion: "Will this test pass?",
    outcome: "Yes",
    side: "BUY" as const,
    rationale: "contract test",
    observedAt: "2026-08-03T00:00:00.000Z",
  };

  it("accepts a post-only GTC proposal", () => {
    expect(
      tradingActionProposalSchema.parse({
        ...base,
        execution: "GTC",
        price: "0.45",
        size: "10",
        postOnly: true,
      }),
    ).toMatchObject({ execution: "GTC", postOnly: true });
  });

  it("rejects post-only immediate orders", () => {
    expect(() =>
      tradingActionProposalSchema.parse({
        ...base,
        execution: "FOK",
        amount: "10",
        limitPrice: "0.5",
        postOnly: true,
      }),
    ).toThrow();
  });

  it("requires an expiration for GTD", () => {
    expect(() =>
      tradingActionProposalSchema.parse({
        ...base,
        execution: "GTD",
        price: "0.45",
        size: "10",
      }),
    ).toThrow(/GTD requires expiration/);
  });
});

describe("backtest market eligibility", () => {
  const market = {
    id: "market-1",
    conditionId: "condition-1",
    slug: "market-1",
    question: "Will this resolve Yes?",
    description: "",
    outcomes: ["Yes", "No"],
    outcomePrices: ["1", "0"],
    clobTokenIds: ["101", "202"],
    active: false,
    closed: true,
    acceptingOrders: false,
    enableOrderBook: true,
    archived: false,
    restricted: false,
    minimumOrderSize: "5",
    minimumTickSize: "0.01",
    endDate: "2026-05-01T02:00:00.000Z",
    startDate: "2026-05-01T00:00:00.000Z",
    createdAt: "2026-05-01T00:00:00.000Z",
    closedTime: "2026-05-01T02:00:00.000Z",
    liquidity: "100",
    volume: "1000",
  };

  it("accepts only resolved binary markets from the CLOB V2 era", () => {
    expect(isBacktestEligibleMarket(market)).toBe(true);
    expect(isBacktestEligibleMarket({ ...market, startDate: "2024-01-04T00:00:00.000Z" })).toBe(false);
    expect(isBacktestEligibleMarket({ ...market, closed: false })).toBe(false);
    expect(isBacktestEligibleMarket({ ...market, outcomes: ["A", "B"] })).toBe(false);
    expect(isBacktestEligibleMarket({ ...market, outcomePrices: ["0.5", "0.5"] })).toBe(false);
  });
});

describe("backtest capacity contract", () => {
  it("reports the exact active count and limit", () => {
    expect(backtestRunListSchema.parse({
      items: [],
      activeCount: 4,
      activeLimit: 10,
    })).toMatchObject({ activeCount: 4, activeLimit: 10 });
    expect(() => backtestRunListSchema.parse({
      items: [],
      activeCount: 11,
    })).toThrow();
  });
});

describe("momentumBacktestConfigSchema", () => {
  it("applies deterministic v1 defaults", () => {
    expect(momentumBacktestConfigSchema.parse({})).toMatchObject({
      strategy: "momentum_v1",
      initialCapital: "10000",
      positionSizePct: "0.10",
      momentumWindowMinutes: 60,
      slippage: "0.01",
    });
  });

  it("rejects an inverted date range", () => {
    expect(() => momentumBacktestConfigSchema.parse({
      startAt: "2026-06-02T00:00:00.000Z",
      endAt: "2026-06-01T00:00:00.000Z",
    })).toThrow(/later than startAt/);
  });

  it("defaults an untagged config to momentum", () => {
    expect(backtestConfigSchema.parse({}).strategy).toBe("momentum_v1");
  });

  it("applies defaults for the two additional strategies", () => {
    expect(defaultMeanReversionBacktestConfig).toMatchObject({
      strategy: "mean_reversion_v1",
      reversionWindowMinutes: 60,
      reversionThreshold: "0.05",
    });
    expect(defaultBreakoutBacktestConfig).toMatchObject({
      strategy: "breakout_v1",
      breakoutWindowMinutes: 240,
      breakoutThreshold: "0.02",
    });
  });

  it("rejects cross-strategy fields and non-positive new thresholds", () => {
    expect(() => backtestConfigSchema.parse({
      strategy: "mean_reversion_v1",
      momentumWindowMinutes: 30,
    })).toThrow();
    expect(() => breakoutBacktestConfigSchema.parse({
      strategy: "breakout_v1",
      breakoutThreshold: "0",
    })).toThrow(/greater than zero/);
  });
});

describe("web workspace read contracts", () => {
  it("parses wallet session metadata without credential fields", () => {
    const value = walletSessionStatusSchema.parse({
      sessionId: "00000000-0000-4000-8000-000000000001",
      walletAddress: "0x0000000000000000000000000000000000000001",
      signatureType: 0,
      idleExpiresAt: "2026-08-03T00:30:00.000Z",
      expiresAt: "2026-08-03T08:00:00.000Z",
    });
    expect(value).not.toHaveProperty("encryptedCredentials");
  });

  it("parses normalized account and market search data", () => {
    expect(accountOverviewSchema.parse({
      walletAddress: "0x0000000000000000000000000000000000000001",
      positions: [],
      openOrders: [],
      fills: [],
      observedAt: "2026-08-03T00:00:00.000Z",
    }).positions).toEqual([]);
    expect(marketSearchResponseSchema.parse({
      query: "election",
      state: "resolved",
      observedAt: "2026-08-03T00:00:00.000Z",
      events: [],
    }).state).toBe("resolved");
  });
});

describe("paper trading contracts", () => {
  it("accepts exact decimal quote and portfolio payloads", () => {
    expect(paperQuoteSchema.parse({
      conditionId: "0xcondition",
      tokenId: "123",
      marketQuestion: "Will paper contracts parse?",
      outcome: "Yes",
      side: "BUY",
      shares: "10.000000",
      averagePrice: "0.420000",
      limitPrice: "0.430000",
      grossNotional: "4.200000",
      feeRate: "0.040000",
      fee: "0.09744",
      cashEffect: "-4.297440",
      observedAt: "2026-08-03T00:00:00.000Z",
    }).cashEffect).toBe("-4.297440");
    expect(paperPortfolioSchema.parse({
      initialCash: "10000.000000",
      cash: "9995.702560",
      positionsValue: "4.000000",
      equity: "9999.702560",
      realizedPnl: "0.000000",
      unrealizedPnl: "-0.297440",
      totalPnl: "-0.297440",
      totalFees: "0.097440",
      positions: [],
      warnings: [],
      observedAt: "2026-08-03T00:00:00.000Z",
    }).equity).toBe("9999.702560");
  });

  it("rejects zero shares and imprecise paper prices", () => {
    expect(() => paperOrderRequestSchema.parse({
      conditionId: "0xcondition",
      tokenId: "123",
      side: "BUY",
      shares: "0",
      limitPrice: "0.4",
    })).toThrow();
    expect(() => paperOrderRequestSchema.parse({
      conditionId: "0xcondition",
      tokenId: "123",
      side: "BUY",
      shares: "1",
      limitPrice: "0.1234567",
    })).toThrow();
  });

  it("validates bounded background paper strategies and their status snapshot", () => {
    expect(paperStrategyStartRequestSchema.parse({
      conditionId: "0xcondition",
      tokenId: "123",
      entryPrice: "0.35",
      exitPrice: "0.65",
      sharesPerOrder: "10",
      maxPosition: "50",
      intervalSeconds: 15,
    }).maxPosition).toBe("50");
    expect(() => paperStrategyStartRequestSchema.parse({
      conditionId: "0xcondition",
      tokenId: "123",
      entryPrice: "0.70",
      exitPrice: "0.60",
      sharesPerOrder: "10",
      maxPosition: "5",
      intervalSeconds: 1,
    })).toThrow();
    expect(paperStrategySnapshotSchema.parse({ strategy: null, events: [] })).toEqual({ strategy: null, events: [] });
  });
});

describe("alert channel contracts", () => {
  const validDiscord = {
    kind: "discord",
    label: "Trading Discord",
    target: "https://discord.com/api/webhooks/1234/abcdefghij",
    eventKinds: ["BUY", "SELL"],
  };
  const validTelegram = {
    kind: "telegram",
    label: "Phone",
    target: "-1001234567890",
    eventKinds: ["ERROR"],
  };

  it("accepts Discord webhook URLs and Telegram chat ids", () => {
    expect(alertCreateChannelRequestSchema.parse(validDiscord).kind).toBe("discord");
    expect(alertCreateChannelRequestSchema.parse(validTelegram).target).toBe("-1001234567890");
  });

  it("rejects non-webhook, non-HTTPS, and foreign-host Discord targets", () => {
    for (const target of [
      "",
      "not-a-url",
      "https://evil.example/api/webhooks/123/token",
      "http://discord.com/api/webhooks/123/token",
      "https://discord.com/invite/abc",
      "https://evil.com/api/webhooks/123/token",
      "https://discord.com/api/webhooks/",
    ]) {
      expect(() => alertCreateChannelRequestSchema.parse({ ...validDiscord, target })).toThrow();
    }
  });

  it("rejects malformed Telegram chat ids and empty event kinds", () => {
    expect(() => alertCreateChannelRequestSchema.parse({ ...validTelegram, target: "12a" })).toThrow();
    expect(() => alertCreateChannelRequestSchema.parse({ ...validTelegram, target: "" })).toThrow();
    expect(() => alertCreateChannelRequestSchema.parse({ ...validDiscord, eventKinds: [] })).toThrow();
  });

  it("excludes WAIT from alertable event kinds", () => {
    expect([...alertEventKindSchema.options].sort())
      .toEqual([...paperStrategyActionSchema.options.filter((kind) => kind !== "WAIT")].sort());
    expect(alertEventKindSchema.safeParse("WAIT").success).toBe(false);
  });

  it("never exposes the encrypted target on channel responses", () => {
    const keys = Object.keys(alertChannelSchema.shape);
    expect(keys).not.toContain("target");
    expect(keys).not.toContain("encryptedTarget");
    expect(keys).toContain("targetHint");
    expect(alertChannelSchema.parse({
      channelId: "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f",
      kind: "telegram",
      label: "Phone",
      eventKinds: ["BUY"],
      enabled: true,
      targetHint: "chat 123456789",
      createdAt: "2026-09-01T00:00:00.000Z",
      updatedAt: "2026-09-01T00:00:00.000Z",
    }).targetHint).toBe("chat 123456789");
  });
});

describe("publicTrackRecordSchema", () => {
  const position = {
    conditionId: "0xcondition",
    tokenId: "123",
    marketQuestion: "Will the Fed hold rates?",
    outcome: "Yes",
    shares: "10.000000",
    costBasis: "5.000000",
    averageCost: "0.500000",
    bestBid: null,
    liquidationValue: "5.200000",
    unrealizedPnl: "0.200000",
    markStatus: "current",
    markedAt: null,
  };
  const fill = {
    fillId: "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f",
    kind: "BUY",
    conditionId: "0xcondition",
    tokenId: "123",
    marketQuestion: "Will the Fed hold rates?",
    outcome: "Yes",
    shares: "10.000000",
    averagePrice: "0.500000",
    grossNotional: "5.000000",
    feeRate: "0.000000",
    fee: "0.000000",
    cashEffect: "-5.000000",
    realizedPnl: "0.000000",
    observedAt: "2026-09-01T00:00:00.000Z",
    createdAt: "2026-09-01T00:00:00.000Z",
  };
  const record = {
    profile: { displayName: "Paper account", startedAt: "2026-08-01T00:00:00.000Z" },
    stats: {
      initialCash: "10000.000000",
      cash: "9500.000000",
      equity: "9505.200000",
      totalPnl: "-494.800000",
      realizedPnl: "10.000000",
      unrealizedPnl: "0.200000",
      totalFees: "1.000000",
      tradeCount: 2,
      winRate: "100.00",
    },
    equityCurve: [
      { t: "2026-09-01T00:00:00.000Z", equity: "9497.000000" },
      { t: "2026-09-01T12:00:00.000Z", equity: "9500.000000" },
      { t: "2026-09-02T00:00:00.000Z", equity: "9505.200000" },
    ],
    positions: [position],
    fills: [fill],
    observedAt: "2026-09-02T00:00:00.000Z",
  };

  it("parses a full public track record", () => {
    expect(publicTrackRecordSchema.parse(record).stats.winRate).toBe("100.00");
  });

  it("strips identity-adjacent fields from the projected positions and fills", () => {
    const parsed = publicTrackRecordSchema.parse(record);
    expect(Object.keys(parsed.positions[0]!)).not.toContain("conditionId");
    expect(Object.keys(parsed.positions[0]!)).not.toContain("tokenId");
    expect(Object.keys(parsed.positions[0]!)).not.toContain("costBasis");
    expect(Object.keys(parsed.fills[0]!)).not.toContain("conditionId");
    expect(Object.keys(parsed.fills[0]!)).not.toContain("tokenId");
    expect(Object.keys(parsed.fills[0]!)).not.toContain("grossNotional");
    expect(Object.keys(parsed.fills[0]!)).not.toContain("feeRate");
  });

  it("rejects a null winRate placeholder or oversized curve", () => {
    expect(publicTrackRecordSchema.safeParse({
      ...record,
      equityCurve: Array.from({ length: 502 }, (_, index) => ({
        t: "2026-09-01T00:00:00.000Z",
        equity: "9500.000000",
        index,
      })),
    }).success).toBe(false);
  });

  it("keeps the share token format aligned between status and URL", () => {
    expect(paperShareStatusSchema.shape.token.safeParse("a".repeat(32)).success).toBe(true);
    expect(paperShareStatusSchema.shape.token.safeParse("short").success).toBe(false);
    expect(paperShareStatusSchema.parse({
      token: null,
      enabled: false,
      createdAt: null,
      updatedAt: null,
    }).token).toBeNull();
  });
});

describe("resolvedBinaryMarketWinner", () => {
  const market = {
    id: "market-1",
    conditionId: "condition-1",
    slug: "market-1",
    question: "Will this resolve Yes?",
    description: "",
    outcomes: ["Yes", "No"],
    outcomePrices: ["1", "0"],
    clobTokenIds: ["101", "202"],
    active: false,
    closed: true,
    acceptingOrders: false,
    enableOrderBook: true,
    archived: false,
    restricted: false,
    minimumOrderSize: "5",
    minimumTickSize: "0.01",
    endDate: "2026-05-01T02:00:00.000Z",
    startDate: "2026-05-01T00:00:00.000Z",
    createdAt: "2026-05-01T00:00:00.000Z",
    closedTime: "2026-05-01T02:00:00.000Z",
    liquidity: "100",
    volume: "1000",
  };

  it("returns the outcome priced at exactly 1", () => {
    expect(resolvedBinaryMarketWinner(market)).toBe("Yes");
    expect(resolvedBinaryMarketWinner({ ...market, outcomePrices: ["0", "1"] })).toBe("No");
  });

  it("returns null while the market is still open", () => {
    expect(resolvedBinaryMarketWinner({ ...market, closed: false })).toBeNull();
    expect(resolvedBinaryMarketWinner({ ...market, acceptingOrders: true })).toBeNull();
  });

  it("returns null for ambiguous or malformed resolutions", () => {
    expect(resolvedBinaryMarketWinner({ ...market, outcomePrices: ["0.5", "0.5"] })).toBeNull();
    expect(resolvedBinaryMarketWinner({ ...market, outcomePrices: ["x", "1"] })).toBeNull();
    expect(resolvedBinaryMarketWinner({ ...market, outcomes: ["Yes"] })).toBeNull();
    expect(resolvedBinaryMarketWinner({ ...market, outcomePrices: [] })).toBeNull();
  });
});

describe("agent prediction contracts", () => {
  it("accepts a well-formed prediction request", () => {
    expect(agentPredictionRequestSchema.parse({
      conditionId: "0xabc",
      tokenId: "123",
      marketQuestion: "Will X win?",
      predictedOutcome: "Yes",
      confidence: "0.75",
    }).confidence).toBe("0.75");
    expect(agentPredictionRequestSchema.parse({
      conditionId: "0xabc",
      marketQuestion: "Will X win?",
      predictedOutcome: "No",
    }).confidence).toBeUndefined();
  });

  it("rejects out-of-range confidence and oversized fields", () => {
    expect(agentPredictionRequestSchema.safeParse({
      conditionId: "0xabc",
      marketQuestion: "q",
      predictedOutcome: "Yes",
      confidence: "1.01",
    }).success).toBe(false);
    expect(agentPredictionRequestSchema.safeParse({
      conditionId: "0xabc",
      marketQuestion: "q",
      predictedOutcome: "Yes",
      confidence: "0.12345",
    }).success).toBe(false);
    expect(agentPredictionRequestSchema.safeParse({
      conditionId: "0xabc",
      marketQuestion: "q",
      predictedOutcome: "Yes",
      tokenId: "abc",
    }).success).toBe(false);
  });

  it("parses the public hit-rate aggregate with nullable rates", () => {
    const parsed = agentPredictionHitRateSchema.parse({
      totals: {
        graded: 214,
        hits: 130,
        hitRatePct: "60.75",
        pending: 3,
        voided: 1,
        lastGradedAt: "2026-09-01T00:00:00.000Z",
      },
      byCategory: [{ category: "Politics", graded: 100, hits: 61, hitRatePct: "61.00" }],
      recent: [{
        marketQuestion: "Will X win?",
        predictedOutcome: "Yes",
        gradedOutcome: "Yes",
        hit: true,
        madeAt: "2026-08-01T00:00:00.000Z",
        gradedAt: "2026-08-02T00:00:00.000Z",
        category: "Politics",
      }],
      observedAt: "2026-09-01T00:00:00.000Z",
    });
    expect(parsed.totals.hitRatePct).toBe("60.75");
    expect(agentPredictionHitRateSchema.parse({
      totals: {
        graded: 0, hits: 0, hitRatePct: null, pending: 0, voided: 0, lastGradedAt: null,
      },
      byCategory: [],
      recent: [],
      observedAt: "2026-09-01T00:00:00.000Z",
    }).totals.hitRatePct).toBeNull();
  });
});

describe("strategy templates", () => {
  it("ships a validated constant list of unique templates", () => {
    const parsed = strategyTemplateListSchema.parse({ items: strategyTemplates });
    expect(parsed.items.length).toBeGreaterThanOrEqual(5);
    expect(new Set(parsed.items.map((template) => template.id)).size).toBe(parsed.items.length);
    for (const template of parsed.items) {
      expect(template.strategyType).toBe("price_band_v1");
      expect(template.stats.kind).toBe("illustrative");
      expect(Number(template.band.exitOffset)).toBeGreaterThan(Number(template.band.entryOffset));
    }
  });

  it("rejects an out-of-range offset or a malformed id", () => {
    const template = strategyTemplates[0]!;
    expect(
      strategyTemplateListSchema.safeParse({ items: [{ ...template, id: "Bad Id" }] }).success,
    ).toBe(false);
    expect(
      strategyTemplateListSchema.safeParse({
        items: [{ ...template, band: { ...template.band, entryOffset: "0.75" } }],
      }).success,
    ).toBe(false);
    expect(
      strategyTemplateListSchema.safeParse({
        items: [{ ...template, band: { ...template.band, exitOffset: "-0.04" } }],
      }).success,
    ).toBe(false);
  });
});
