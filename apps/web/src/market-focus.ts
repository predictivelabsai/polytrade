import { marketSearchMarketSchema, type MarketSearchMarket } from "@polytrade/contracts";

const STORAGE_KEY = "polytrade.market-focus";

export function saveMarketFocus(market: MarketSearchMarket): void {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(market));
}

export function loadMarketFocus(conditionId?: string | null): MarketSearchMarket | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = marketSearchMarketSchema.safeParse(JSON.parse(raw));
    if (!parsed.success) return null;
    return !conditionId || parsed.data.conditionId === conditionId ? parsed.data : null;
  } catch {
    return null;
  }
}

export function clearMarketFocus(): void {
  window.localStorage.removeItem(STORAGE_KEY);
}

export function focusHref(path: string, market: MarketSearchMarket): string {
  const query = new URLSearchParams({ focus: market.conditionId });
  return `${path}?${query}`;
}

export function researchPrompt(market: MarketSearchMarket): string {
  return `Review “${market.question}”. Summarize the current market, liquidity, key risks, and what would invalidate a paper-trading thesis.`;
}
