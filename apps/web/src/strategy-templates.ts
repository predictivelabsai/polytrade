import type { MarketSearchMarket, StrategyTemplate } from "@polytrade/contracts";

import { strategyTemplateById, strategyTemplates } from "@polytrade/contracts";

export { strategyTemplateById, strategyTemplates };

/** The strategy runner's draft fields — same string shape the runner edits. */
export interface TemplateDraft {
  entryPrice: string;
  exitPrice: string;
  sharesPerOrder: string;
  maxPosition: string;
  intervalSeconds: string;
}

const PRICE_FLOOR = 0.01;
const PRICE_CEILING = 0.99;

/**
 * Picks the clob token the template's band applies to: the highest-priced
 * outcome for "higher_price", the lowest for "lower_price". Ties go to index 0.
 */
export function resolveTemplateTokenId(template: StrategyTemplate, market: MarketSearchMarket): string | null {
  if (market.outcomePrices.length < 2 || market.clobTokenIds.length < 2) return null;
  const prices = market.outcomePrices.map((value) => Number(value));
  if (prices.some((price) => !Number.isFinite(price))) return null;
  let target = 0;
  for (let index = 1; index < prices.length; index += 1) {
    const better = template.outcomePick === "higher_price"
      ? prices[index]! > prices[target]!
      : prices[index]! < prices[target]!;
    if (better) target = index;
  }
  return market.clobTokenIds[target] ?? null;
}

/**
 * Translates a template's market-relative band into the absolute draft the
 * strategy runner shows. Prices clamp to [0.01, 0.99]; sizing clamps up to
 * the market's minimum order size. When clamping would collapse the band
 * (entry >= exit), the exit is pushed two ticks above entry — and if even
 * that cannot produce a valid band, returns null so the caller can fall back
 * to the default auto-fit.
 */
export function templateDraft(
  template: StrategyTemplate,
  market: MarketSearchMarket,
  tokenId: string,
): TemplateDraft | null {
  const index = market.clobTokenIds.indexOf(tokenId);
  if (index < 0) return null;
  const reference = Number(market.outcomePrices[index] ?? "");
  if (!Number.isFinite(reference)) return null;
  const tick = Math.max(Number(market.minimumTickSize) || 0.01, 0.001);

  const entry = clamp(reference + Number(template.band.entryOffset));
  let exit = clamp(reference + Number(template.band.exitOffset));
  if (exit <= entry) exit = clamp(entry + 2 * tick);
  if (exit <= entry) return null;

  const sharesPerOrder = Math.max(Number(template.band.sharesPerOrder), Number(market.minimumOrderSize) || 0);
  const maxPosition = sharesPerOrder * Number(template.band.positionMultiplier);

  return {
    entryPrice: formatInputPrice(entry),
    exitPrice: formatInputPrice(exit),
    sharesPerOrder: formatInputShares(sharesPerOrder),
    maxPosition: formatInputShares(maxPosition),
    intervalSeconds: String(template.band.intervalSeconds),
  };
}

function clamp(value: number): number {
  return Math.min(PRICE_CEILING, Math.max(PRICE_FLOOR, value));
}

function formatInputPrice(value: number): string {
  return value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

function formatInputShares(value: number): string {
  return value.toFixed(6).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
}