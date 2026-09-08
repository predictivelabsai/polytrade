import { describe, expect, it, vi } from "vitest";

import { TtlCache } from "../src/public-cache.js";

describe("TtlCache", () => {
  it("returns cached values until they expire, then reloads", async () => {
    vi.useFakeTimers();
    vi.setSystemTime("2026-09-01T00:00:00Z");
    const cache = new TtlCache();
    let loads = 0;

    const first = await cache.load("k", 1_000, async () => `v-${(loads += 1)}`);
    const second = await cache.load("k", 1_000, async () => `v-${(loads += 1)}`);
    expect(first).toBe("v-1");
    expect(second).toBe("v-1");
    expect(cache.stats().misses).toBe(1);
    expect(cache.stats().hits).toBe(1);

    vi.advanceTimersByTime(1_500);
    const third = await cache.load("k", 1_000, async () => `v-${(loads += 1)}`);
    expect(third).toBe("v-2");
    expect(loads).toBe(2);
    vi.useRealTimers();
  });

  it("shares a single loader run between concurrent misses", async () => {
    const cache = new TtlCache();
    let loads = 0;
    const loader = async () => {
      loads += 1;
      await new Promise((resolve) => setTimeout(resolve, 5));
      return "shared";
    };
    const [left, right] = await Promise.all([
      cache.load("k", 60_000, loader),
      cache.load("k", 60_000, loader),
    ]);
    expect(loads).toBe(1);
    expect(left).toBe("shared");
    expect(right).toBe("shared");
  });

  it("evicts the oldest entry once the cache is over capacity", async () => {
    const cache = new TtlCache(2);
    await cache.load("a", 60_000, async () => 1);
    await cache.load("b", 60_000, async () => 2);
    await cache.load("c", 60_000, async () => 3);
    expect(cache.stats().entries).toBe(2);
    expect(cache.stats().misses).toBe(3);
  });

  it("remembers known-missing keys only for their negative TTL", async () => {
    vi.useFakeTimers();
    vi.setSystemTime("2026-09-01T00:00:00Z");
    const cache = new TtlCache();

    cache.markMissing("missing-slug", 500);
    expect(cache.isKnownMissing("missing-slug")).toBe(true);
    expect(cache.isKnownMissing("other-slug")).toBe(false);

    vi.advanceTimersByTime(600);
    expect(cache.isKnownMissing("missing-slug")).toBe(false);
    vi.useRealTimers();
  });

  it("does not cache loader failures or remember rejects as missing", async () => {
    const cache = new TtlCache();
    let attempts = 0;
    const failing = async () => {
      attempts += 1;
      throw new Error("upstream down");
    };
    await expect(cache.load("k", 60_000, failing)).rejects.toThrow("upstream down");
    await expect(cache.load("k", 60_000, failing)).rejects.toThrow("upstream down");
    expect(attempts).toBe(2);
  });
});