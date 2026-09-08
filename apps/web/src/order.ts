import type { CreateOrderProposal } from "@polytrade/contracts";

export const DEFAULT_CASH_EXPOSURE_LIMIT = 100;
export const STALE_MARKET_OBSERVATION_MS = 2 * 60_000;

export interface OrderRiskSummary {
  cashExposure: number | null;
  maximumExposure: string;
  priceProtection: string;
  stale: boolean;
  worstCase: string;
}

export function maximumExposure(proposal: CreateOrderProposal): string {
  if ("price" in proposal) {
    if (proposal.side === "SELL") return `${proposal.size} shares`;
    const value = Number(proposal.price) * Number(proposal.size);
    return Number.isFinite(value) ? `${formatDecimal(value)} USDC` : "Invalid";
  }
  return `${proposal.amount} ${proposal.side === "BUY" ? "USDC" : "shares"}`;
}

export function orderRiskSummary(proposal: CreateOrderProposal, now = Date.now()): OrderRiskSummary {
  const stale = !Number.isFinite(Date.parse(proposal.observedAt)) || now - Date.parse(proposal.observedAt) > STALE_MARKET_OBSERVATION_MS;
  if ("price" in proposal) {
    if (proposal.side === "SELL") {
      return {
        cashExposure: null,
        maximumExposure: `${proposal.size} shares`,
        priceProtection: `No fill below ${proposal.price}`,
        stale,
        worstCase: `Up to ${proposal.size} shares can rest or fill at ${proposal.price} or better.`,
      };
    }
    const exposure = Number(proposal.price) * Number(proposal.size);
    return {
      cashExposure: Number.isFinite(exposure) ? exposure : null,
      maximumExposure: maximumExposure(proposal),
      priceProtection: `No fill above ${proposal.price}`,
      stale,
      worstCase: `Up to ${maximumExposure(proposal)} can be committed at ${proposal.price} or better.`,
    };
  }

  if (proposal.side === "SELL") {
    return {
      cashExposure: null,
      maximumExposure: `${proposal.amount} shares`,
      priceProtection: `No fill below ${proposal.limitPrice}`,
      stale,
      worstCase: `Up to ${proposal.amount} shares can fill at ${proposal.limitPrice} or better.`,
    };
  }
  const exposure = Number(proposal.amount);
  return {
    cashExposure: Number.isFinite(exposure) ? exposure : null,
    maximumExposure: maximumExposure(proposal),
    priceProtection: `No fill above ${proposal.limitPrice}`,
    stale,
    worstCase: `Up to ${maximumExposure(proposal)} can fill at ${proposal.limitPrice} or better.`,
  };
}

export function parseCashExposureLimit(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 && parsed <= 1_000_000 ? parsed : null;
}

function formatDecimal(value: number): string {
  // Risk-copy is also persisted and asserted across clients, so keep its
  // decimal representation stable instead of inheriting the host locale.
  return value.toLocaleString("en-US", { maximumFractionDigits: 6 });
}
