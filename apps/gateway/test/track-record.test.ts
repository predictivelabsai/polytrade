import { describe, expect, it } from "vitest";

import { PublicTrackRecordService, TRACK_RECORD_CACHE_TTL_MS } from "../src/track-record.js";
import { TtlCache } from "../src/public-cache.js";
import { MemoryTrackRecordStore, trackRecordSnapshotFixture } from "./fakes.js";

const PRINCIPAL = "assethero:user-1";

function service(store: MemoryTrackRecordStore, now: () => Date = () => new Date("2026-09-02T00:00:00.000Z")) {
  return new PublicTrackRecordService(store, new TtlCache(), now);
}

async function sharedRecord(store: MemoryTrackRecordStore = new MemoryTrackRecordStore()) {
  store.snapshots[PRINCIPAL] = trackRecordSnapshotFixture();
  const link = await store.enable(PRINCIPAL);
  return { store, token: link.token! };
}

describe("PublicTrackRecordService manage", () => {
  it("reports a null status before the link exists", async () => {
    const store = new MemoryTrackRecordStore();
    expect(await service(store).status(PRINCIPAL)).toEqual({
      token: null,
      enabled: false,
      createdAt: null,
      updatedAt: null,
    });
  });

  it("keeps the token across disable and re-enable, rotating only on rotate", async () => {
    const store = new MemoryTrackRecordStore();
    const trackRecords = service(store);
    const created = await trackRecords.enable(PRINCIPAL);
    const disabled = await trackRecords.disable(PRINCIPAL);
    expect(disabled.token).toBe(created.token);
    expect(disabled.enabled).toBe(false);
    const reEnabled = await trackRecords.enable(PRINCIPAL);
    expect(reEnabled.token).toBe(created.token);
    expect(reEnabled.enabled).toBe(true);
    const rotated = await trackRecords.rotate(PRINCIPAL);
    expect(rotated.token).not.toBe(created.token);
  });
});

describe("PublicTrackRecordService.detail", () => {
  it("replays the fill-anchored curve and reconciles with live equity", async () => {
    const { store, token } = await sharedRecord();
    const record = await service(store).detail(token);

    // cash 9500, window sum −2 → baseline 9502; points 9497 → 9500 → live 9505.20
    expect(record.equityCurve.map((point) => point.equity)).toEqual([
      "9497.000000",
      "9500.000000",
      "9505.200000",
    ]);
    expect(record.equityCurve.at(-1)?.t).toBe("2026-09-02T00:00:00.000Z");
    expect(record.stats).toEqual({
      initialCash: "10000.000000",
      cash: "9500.000000",
      equity: "9505.200000",
      totalPnl: "-494.800000",
      realizedPnl: "10.000000",
      unrealizedPnl: "0.200000",
      totalFees: "1.000000",
      tradeCount: 2,
      winRate: "100.00",
    });
  });

  it("starts the curve at the initial cash when there are no fills", async () => {
    const { store, token } = await sharedRecord();
    store.snapshots[PRINCIPAL] = trackRecordSnapshotFixture({
      account: { initialCash: "10000.000000", cash: "10000.000000" },
      positions: [],
      totals: { realizedPnl: "0.000000", totalFees: "0.000000", tradeCount: 0, wins: 0, closed: 0 },
      recentFills: [],
      curve: { totalCashEffect: "0.000000", fills: [] },
    });
    const record = await service(store).detail(token);
    expect(record.equityCurve).toEqual([
      { t: "2026-08-01T00:00:00.000Z", equity: "10000.000000" },
      { t: "2026-09-02T00:00:00.000Z", equity: "10000.000000" },
    ]);
    expect(record.stats.winRate).toBeNull();
  });

  it("folds fills outside the visible window into the baseline", async () => {
    const { store, token } = await sharedRecord();
    // 505 fills of -10 each: cash 4950, only the last 500 are in the window.
    const fills = Array.from({ length: 505 }, (_, index) => ({
      createdAt: new Date(Date.UTC(2026, 7, 1, 0, index)).toISOString(),
      cashEffect: "-10.000000",
    }));
    store.snapshots[PRINCIPAL] = trackRecordSnapshotFixture({
      account: { initialCash: "10000.000000", cash: "4950.000000" },
      positions: [],
      curve: { totalCashEffect: "-5050.000000", fills: fills.slice(-500) },
      recentFills: [],
      totals: { realizedPnl: "0.000000", totalFees: "0.000000", tradeCount: 505, wins: 0, closed: 0 },
    });
    const record = await service(store).detail(token);

    expect(record.equityCurve).toHaveLength(501);
    // baseline = 4950 − (−5000) = 9950 = initial cash + the 5 hidden fills.
    expect(record.equityCurve[0]!.equity).toBe("9940.000000");
    expect(record.equityCurve.at(-1)?.equity).toBe("4950.000000");
    expect(record.equityCurve.at(-1)?.t).toBe("2026-09-02T00:00:00.000Z");
  });

  it("caches a resolved record and negatively caches unknown tokens", async () => {
    const { store, token } = await sharedRecord();
    const trackRecords = service(store);

    await trackRecords.detail(token);
    await trackRecords.detail(token);
    expect(store.snapshotCount).toBe(1);
    expect(store.resolveCount).toBe(1);

    await expect(trackRecords.detail("a".repeat(32))).rejects.toThrow("Track record not found");
    await expect(trackRecords.detail("a".repeat(32))).rejects.toThrow("Track record not found");
    // Second lookup is answered by the negative cache, never the store.
    expect(store.resolveCount).toBe(2);
  });

  it("never exposes the principal id in the public payload", async () => {
    const { store, token } = await sharedRecord();
    const record = await service(store).detail(token);
    expect(JSON.stringify(record)).not.toContain(PRINCIPAL);
  });
});

describe("TRACK_RECORD_CACHE_TTL_MS", () => {
  it("matches the clamped Cache-Control header budget", () => {
    expect(TRACK_RECORD_CACHE_TTL_MS.detail).toBeLessThanOrEqual(60_000);
    expect(TRACK_RECORD_CACHE_TTL_MS.missing).toBeGreaterThan(TRACK_RECORD_CACHE_TTL_MS.detail);
  });
});