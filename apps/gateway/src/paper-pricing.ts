import type { PaperQuote, PaperQuoteRequest } from "@polytrade/contracts";
import { Decimal } from "decimal.js";
import { z } from "zod";

const levelSchema = z.object({
  price: z.union([z.string(), z.number()]),
  size: z.union([z.string(), z.number()]),
});

const orderBookSchema = z.object({
  bids: z.array(levelSchema),
  asks: z.array(levelSchema),
  observedAt: z.string().datetime(),
});

export type PaperPricingFailure =
  | "MALFORMED_BOOK"
  | "INSUFFICIENT_LIQUIDITY"
  | "PRICE_MOVED";

export class PaperPricingError extends Error {
  constructor(readonly reason: PaperPricingFailure, message: string) {
    super(message);
    this.name = "PaperPricingError";
  }
}

export interface PaperMarketIdentity {
  conditionId: string;
  tokenId: string;
  marketQuestion: string;
  outcome: string;
}

export function bestBidFromOrderBook(rawBook: unknown): { price: string; observedAt: string } | null {
  const book = orderBookSchema.safeParse(rawBook);
  if (!book.success) {
    throw new PaperPricingError("MALFORMED_BOOK", "Polymarket returned a malformed order book");
  }
  const bids = book.data.bids.map((level) => {
    const bidPrice = decimal(level.price, "book price");
    const bidSize = decimal(level.size, "book size");
    if (bidPrice.lte(0) || bidPrice.gt(1) || bidSize.lte(0)) {
      throw new PaperPricingError("MALFORMED_BOOK", "Polymarket returned an invalid order-book level");
    }
    return bidPrice;
  }).sort((left, right) => right.comparedTo(left));
  return bids[0] ? { price: price(bids[0]), observedAt: book.data.observedAt } : null;
}

export function quotePaperOrder(
  request: PaperQuoteRequest,
  identity: PaperMarketIdentity,
  rawBook: unknown,
  rawFeeRate: string,
  limitPrice?: string,
): PaperQuote {
  const book = orderBookSchema.safeParse(rawBook);
  if (!book.success) {
    throw new PaperPricingError("MALFORMED_BOOK", "Polymarket returned a malformed order book");
  }

  const requestedShares = decimal(request.shares, "share quantity");
  const feeRate = decimal(rawFeeRate, "fee rate");
  if (requestedShares.lte(0) || feeRate.lt(0) || feeRate.gt(1)) {
    throw new PaperPricingError("MALFORMED_BOOK", "Polymarket returned invalid pricing data");
  }

  const sideLevels = request.side === "BUY" ? book.data.asks : book.data.bids;
  const levels = sideLevels.map((level) => {
    const price = decimal(level.price, "book price");
    const size = decimal(level.size, "book size");
    if (price.lte(0) || price.gt(1) || size.lte(0)) {
      throw new PaperPricingError("MALFORMED_BOOK", "Polymarket returned an invalid order-book level");
    }
    return { price, size };
  }).sort((left, right) => (
    request.side === "BUY" ? left.price.comparedTo(right.price) : right.price.comparedTo(left.price)
  ));

  const bound = limitPrice === undefined ? undefined : decimal(limitPrice, "limit price");
  if (bound && (bound.lte(0) || bound.gt(1))) {
    throw new PaperPricingError("MALFORMED_BOOK", "Paper price bound is invalid");
  }
  const allowed = bound === undefined
    ? levels
    : levels.filter(({ price }) => request.side === "BUY" ? price.lte(bound) : price.gte(bound));

  const totalDepth = levels.reduce((total, level) => total.plus(level.size), new Decimal(0));
  const allowedDepth = allowed.reduce((total, level) => total.plus(level.size), new Decimal(0));
  if (allowedDepth.lt(requestedShares)) {
    if (bound !== undefined && totalDepth.gte(requestedShares)) {
      throw new PaperPricingError("PRICE_MOVED", "The paper quote moved beyond its confirmed price");
    }
    throw new PaperPricingError("INSUFFICIENT_LIQUIDITY", "The visible order book cannot fill the complete paper order");
  }

  let remaining = requestedShares;
  let gross = new Decimal(0);
  let rawFee = new Decimal(0);
  let worstPrice = new Decimal(0);
  for (const level of allowed) {
    if (remaining.lte(0)) break;
    const quantity = Decimal.min(remaining, level.size);
    gross = gross.plus(quantity.mul(level.price));
    rawFee = rawFee.plus(quantity.mul(feeRate).mul(level.price).mul(new Decimal(1).minus(level.price)));
    worstPrice = level.price;
    remaining = remaining.minus(quantity);
  }

  const roundedGross = gross.toDecimalPlaces(6, Decimal.ROUND_HALF_UP);
  const fee = rawFee.toDecimalPlaces(5, Decimal.ROUND_HALF_UP);
  const averagePrice = gross.div(requestedShares);
  const cashEffect = request.side === "BUY"
    ? roundedGross.plus(fee).negated()
    : roundedGross.minus(fee);

  return {
    ...identity,
    side: request.side,
    shares: shares(requestedShares),
    averagePrice: price(averagePrice),
    limitPrice: price(worstPrice),
    grossNotional: money(roundedGross),
    feeRate: money(feeRate),
    fee: fee.toFixed(5),
    cashEffect: money(cashEffect),
    observedAt: book.data.observedAt,
  };
}

export function paperLiquidationValue(
  rawShares: string | Decimal,
  rawBestBid: string | Decimal,
  rawFeeRate: string | Decimal,
): string {
  const quantity = new Decimal(rawShares);
  const bestBid = new Decimal(rawBestBid);
  const feeRate = new Decimal(rawFeeRate);
  const gross = quantity.mul(bestBid);
  const fee = quantity
    .mul(feeRate)
    .mul(bestBid)
    .mul(new Decimal(1).minus(bestBid))
    .toDecimalPlaces(5, Decimal.ROUND_HALF_UP);
  return money(Decimal.max(0, gross.minus(fee)));
}

export function money(value: Decimal): string {
  return value.toDecimalPlaces(6, Decimal.ROUND_HALF_UP).toFixed(6);
}

export function shares(value: Decimal): string {
  return value.toDecimalPlaces(6, Decimal.ROUND_DOWN).toFixed(6);
}

export function price(value: Decimal): string {
  return value.toDecimalPlaces(6, Decimal.ROUND_HALF_UP).toFixed(6);
}

function decimal(value: string | number, label: string): Decimal {
  try {
    const parsed = new Decimal(value);
    if (!parsed.isFinite()) throw new Error("not finite");
    return parsed;
  } catch {
    throw new PaperPricingError("MALFORMED_BOOK", `Invalid ${label}`);
  }
}
