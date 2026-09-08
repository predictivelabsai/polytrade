// Read-only market data for the public market pages (no authentication).
// Everything is cached in-memory so crawler traffic and repeat visits do not
// turn into a stream of upstream Polymarket requests.

import type { PublicMarketDetail, PublicMarketListResponse, PublicOrderBook, PublicPriceHistory } from "@polytrade/contracts";

import { notFound } from "./errors.js";
import type { PolymarketPort } from "./polymarket.js";
import { TtlCache } from "./public-cache.js";

export const PUBLIC_CACHE_TTL_MS = {
  index: 30_000,
  market: 30_000,
  missingMarket: 60_000,
  book: 5_000,
} as const;

const HISTORY_TTL_MS: Record<string, number> = {
  "1h": 60_000,
  "6h": 120_000,
  "1d": 300_000,
  "1w": 600_000,
  max: 3_600_000,
};

export function publicPriceHistoryTtlMs(interval: string): number {
  return HISTORY_TTL_MS[interval] ?? HISTORY_TTL_MS["1d"]!;
}

interface RawMarketDetail {
  market: PublicMarketDetail["market"];
  observedAt: string;
}

interface RawOrderBook {
  bids: Array<{ price: string; size: string }>;
  asks: Array<{ price: string; size: string }>;
  minimumOrderSize: string;
  tickSize: string;
  negativeRisk: boolean;
  lastTradePrice: string;
  observedAt: string;
}

type BookWithoutToken = Omit<PublicOrderBook, "tokenId">;

export class PublicMarketService {
  constructor(
    private readonly polymarket: PolymarketPort,
    private readonly cache: TtlCache,
  ) {}

  async list(input: { limit: number; offset: number; order: "volume24hr" | "liquidity" | "endDate" }): Promise<PublicMarketListResponse> {
    const key = `index:${input.limit}:${input.offset}:${input.order}`;
    return this.cache.load(key, PUBLIC_CACHE_TTL_MS.index, () =>
      this.polymarket.listActiveMarkets(input) as Promise<PublicMarketListResponse>,
    );
  }

  async detail(slug: string): Promise<PublicMarketDetail> {
    if (this.cache.isKnownMissing(`notfound:market:${slug}`)) {
      throw notFound("Public market not found");
    }
    return this.cache.load(`market:${slug}`, PUBLIC_CACHE_TTL_MS.market, () =>
      this.buildDetail(slug).catch((error: unknown) => {
        if (errorStatus(error) === 404) {
          this.cache.markMissing(`notfound:market:${slug}`, PUBLIC_CACHE_TTL_MS.missingMarket);
        }
        throw error;
      }),
    );
  }

  async book(tokenId: string): Promise<PublicOrderBook> {
    const cached = await this.cachedBook(tokenId);
    return { ...cached, tokenId };
  }

  async history(tokenId: string, interval: string): Promise<PublicPriceHistory> {
    const key = `hist:${tokenId}:${interval}`;
    return this.cache.load(key, publicPriceHistoryTtlMs(interval), () =>
      this.polymarket.getPriceHistory(tokenId, interval) as Promise<PublicPriceHistory>,
    );
  }

  private async buildDetail(slug: string): Promise<PublicMarketDetail> {
    const { market } = (await this.polymarket.getPublicMarket(slug)) as RawMarketDetail;
    const tokenIds = market.clobTokenIds;
    const books = await Promise.all(tokenIds.map((tokenId) => this.tryBook(tokenId)));
    const quotes = market.outcomes.map((outcome, index) => {
      const tokenId = tokenIds[index] ?? "";
      const fallbackPrice = market.outcomePrices[index] ?? null;
      const book = books[index];
      if (!book) {
        return { outcome, tokenId, price: fallbackPrice, bestBid: null, bestAsk: null, source: "gamma" as const };
      }
      return {
        outcome,
        tokenId,
        price: book.lastTradePrice ?? book.bids[0]?.price ?? fallbackPrice,
        bestBid: book.bids[0]?.price ?? null,
        bestAsk: book.asks[0]?.price ?? null,
        source: "order-book" as const,
      };
    });
    return { market, quotes, observedAt: new Date().toISOString() };
  }

  private async tryBook(tokenId: string): Promise<BookWithoutToken | undefined> {
    try {
      return await this.cachedBook(tokenId);
    } catch {
      // A failed order book for one outcome degrades to Gamma prices; never 5xx the page.
      return undefined;
    }
  }

  private async cachedBook(tokenId: string): Promise<BookWithoutToken> {
    return this.cache.load(`book:${tokenId}`, PUBLIC_CACHE_TTL_MS.book, async () =>
      normalizeOrderBook((await this.polymarket.getOrderBook(tokenId)) as RawOrderBook),
    );
  }
}

function normalizeOrderBook(raw: RawOrderBook): BookWithoutToken {
  return {
    minimumOrderSize: raw.minimumOrderSize ?? "",
    tickSize: raw.tickSize ?? "",
    negativeRisk: raw.negativeRisk ?? false,
    lastTradePrice: raw.lastTradePrice ? raw.lastTradePrice : null,
    bids: raw.bids ?? [],
    asks: raw.asks ?? [],
    observedAt: raw.observedAt ?? new Date().toISOString(),
  };
}

function errorStatus(error: unknown): number | undefined {
  return error instanceof Error && "statusCode" in error
    ? (error as { statusCode?: number }).statusCode
    : undefined;
}