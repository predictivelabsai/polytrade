// Small in-process cache for public market reads. It has no timers: expired
// entries are pruned lazily on write, and the oldest inserted entry is evicted
// when the cache is over capacity (Map preserves insertion order). Concurrent
// misses share a single loader ("single-flight") so a burst of crawler traffic
// collapses into one upstream request per key.

interface CacheEntry {
  value: unknown;
  expiresAt: number;
}

export interface TtlCacheStats {
  hits: number;
  misses: number;
  entries: number;
  missing: number;
}

export class TtlCache {
  private readonly entries = new Map<string, CacheEntry>();
  private readonly missing = new Map<string, number>();
  private readonly inflight = new Map<string, Promise<unknown>>();
  private hits = 0;
  private misses = 0;

  constructor(private readonly maxEntries = 500) {}

  async load<T>(key: string, ttlMs: number, loader: () => Promise<T>): Promise<T> {
    const now = Date.now();
    const cached = this.entries.get(key);
    if (cached) {
      if (cached.expiresAt > now) {
        this.hits += 1;
        return cached.value as T;
      }
      this.entries.delete(key);
    }
    const pending = this.inflight.get(key);
    if (pending) {
      this.hits += 1;
      return pending as Promise<T>;
    }
    this.misses += 1;
    const promise = (async () => {
      const value = await loader();
      this.set(key, value, ttlMs);
      return value;
    })();
    this.inflight.set(key, promise);
    try {
      return await promise;
    } finally {
      this.inflight.delete(key);
    }
  }

  isKnownMissing(key: string): boolean {
    const missingUntil = this.missing.get(key);
    if (missingUntil === undefined) return false;
    if (missingUntil <= Date.now()) {
      this.missing.delete(key);
      return false;
    }
    return true;
  }

  markMissing(key: string, ttlMs: number): void {
    if (!this.missing.has(key) && this.missing.size >= this.maxEntries) {
      this.dropFirst(this.missing);
    }
    this.missing.set(key, Date.now() + ttlMs);
  }

  stats(): TtlCacheStats {
    return {
      hits: this.hits,
      misses: this.misses,
      entries: this.entries.size,
      missing: this.missing.size,
    };
  }

  private set(key: string, value: unknown, ttlMs: number): void {
    this.pruneExpired();
    if (!this.entries.has(key) && this.entries.size >= this.maxEntries) {
      this.dropFirst(this.entries);
    }
    this.entries.set(key, { value, expiresAt: Date.now() + ttlMs });
  }

  private pruneExpired(): void {
    const now = Date.now();
    for (const [key, entry] of this.entries) {
      if (entry.expiresAt <= now) this.entries.delete(key);
    }
    for (const [key, missingUntil] of this.missing) {
      if (missingUntil <= now) this.missing.delete(key);
    }
  }

  private dropFirst<V>(map: Map<string, V>): void {
    const oldest = map.keys().next();
    if (!oldest.done) map.delete(oldest.value);
  }
}