import { createHash } from "node:crypto";

import type {
  MarketSearchMarket,
  PaperFillsResponse,
  PaperOrderRequest,
  PaperOrderResponse,
  PaperPortfolio,
  PaperQuote,
  PaperQuoteRequest,
} from "@polytrade/contracts";
import { Decimal } from "decimal.js";

import { AppError, unavailable, validation } from "./errors.js";
import {
  bestBidFromOrderBook,
  PaperPricingError,
  quotePaperOrder,
} from "./paper-pricing.js";
import type {
  PaperExecutionResult,
  PaperRefreshInstruction,
  PaperStore,
} from "./paper-store.js";
import type { PolymarketPort } from "./polymarket.js";
import type { Principal } from "./types.js";

export class PaperTradingService {
  constructor(
    private readonly store: PaperStore,
    private readonly polymarket: PolymarketPort,
    private readonly now: () => Date = () => new Date(),
  ) {}

  portfolio(principal: Principal): Promise<PaperPortfolio> {
    return this.store.portfolio(principal.id);
  }

  fills(principal: Principal, limit: number, offset: number): Promise<PaperFillsResponse> {
    return this.store.fills(principal.id, limit, offset);
  }

  quote(principal: Principal, request: PaperQuoteRequest): Promise<PaperQuote> {
    return this.buildQuote(principal, request);
  }

  async order(
    principal: Principal,
    request: PaperOrderRequest,
    idempotencyKey: string,
  ): Promise<PaperOrderResponse> {
    return this.executeOrder(principal, request, idempotencyKey);
  }

  async strategyOrder(
    principal: Principal,
    request: PaperOrderRequest,
    idempotencyKey: string,
    strategyGuard: NonNullable<Parameters<PaperStore["execute"]>[0]["strategyGuard"]>,
  ): Promise<PaperOrderResponse> {
    return this.executeOrder(principal, request, idempotencyKey, strategyGuard);
  }

  async strategyTarget(conditionId: string, tokenId: string): Promise<{
    conditionId: string;
    tokenId: string;
    marketQuestion: string;
    outcome: string;
    minimumOrderSize: string;
  }> {
    const snapshot = await this.polymarket.getMarketByCondition(conditionId);
    const identity = validatePaperMarket(snapshot.market, {
      conditionId,
      tokenId,
      side: "BUY",
      shares: "1",
    });
    return {
      ...identity,
      minimumOrderSize: decimalMarketValue(snapshot.market.minimumOrderSize, "minimum order size").toFixed(6),
    };
  }

  private async executeOrder(
    principal: Principal,
    request: PaperOrderRequest,
    idempotencyKey: string,
    strategyGuard?: NonNullable<Parameters<PaperStore["execute"]>[0]["strategyGuard"]>,
  ): Promise<PaperOrderResponse> {
    const requestHash = paperRequestHash(request);
    const replay = await this.store.replay(principal.id, idempotencyKey, requestHash);
    if (replay) return this.orderResult(principal, replay);

    const quote = await this.buildQuote(principal, request, request.limitPrice);
    const result = await this.store.execute({
      principalId: principal.id,
      idempotencyKey,
      requestHash,
      quote,
      createdAt: this.now(),
      strategyGuard,
    });
    return this.orderResult(principal, result);
  }

  async refresh(principal: Principal): Promise<PaperPortfolio> {
    const current = await this.store.portfolio(principal.id);
    const grouped = new Map<string, typeof current.positions>();
    for (const position of current.positions) {
      const positions = grouped.get(position.conditionId) ?? [];
      positions.push(position);
      grouped.set(position.conditionId, positions);
    }

    const instructions: PaperRefreshInstruction[] = [];
    const warnings: string[] = [];
    for (const [conditionId, positions] of grouped) {
      let market: MarketSearchMarket;
      let marketObservedAt: Date;
      try {
        const snapshot = await this.polymarket.getMarketByCondition(conditionId);
        market = snapshot.market;
        marketObservedAt = new Date(snapshot.observedAt);
      } catch (error) {
        for (const position of positions) {
          instructions.push({ kind: "stale", conditionId, tokenId: position.tokenId });
        }
        warnings.push(`${positions[0]?.marketQuestion ?? conditionId}: ${safeMessage(error)}`);
        continue;
      }

      const resolution = resolvedPrices(market);
      if (resolution) {
        for (const position of positions) {
          const resolutionPrice = resolution.get(position.tokenId);
          if (resolutionPrice === undefined) {
            instructions.push({ kind: "stale", conditionId, tokenId: position.tokenId });
            warnings.push(`${position.marketQuestion}: held outcome is missing from final market data.`);
          } else {
            instructions.push({
              kind: "settlement",
              conditionId,
              tokenId: position.tokenId,
              resolutionPrice,
              observedAt: marketObservedAt,
            });
          }
        }
        continue;
      }

      if (!market.active || market.closed || !market.acceptingOrders || !market.enableOrderBook) {
        for (const position of positions) {
          instructions.push({ kind: "stale", conditionId, tokenId: position.tokenId });
        }
        warnings.push(`${market.question}: the market is closed but does not have a final 1/0 resolution yet.`);
        continue;
      }

      for (const position of positions) {
        try {
          const [book, feeRate] = await Promise.all([
            this.polymarket.getOrderBook(position.tokenId),
            this.polymarket.getFeeRate(position.tokenId),
          ]);
          const bestBid = bestBidFromOrderBook(book);
          if (!bestBid) {
            instructions.push({ kind: "stale", conditionId, tokenId: position.tokenId });
            warnings.push(`${position.marketQuestion} · ${position.outcome}: no current bid is available.`);
            continue;
          }
          instructions.push({
            kind: "mark",
            conditionId,
            tokenId: position.tokenId,
            bestBid: bestBid.price,
            feeRate,
            observedAt: new Date(bestBid.observedAt),
          });
        } catch (error) {
          instructions.push({ kind: "stale", conditionId, tokenId: position.tokenId });
          warnings.push(`${position.marketQuestion} · ${position.outcome}: ${safeMessage(error)}`);
        }
      }
    }

    await this.store.refresh(principal.id, instructions);
    return this.store.portfolio(principal.id, warnings);
  }

  private async buildQuote(
    principal: Principal,
    request: PaperQuoteRequest,
    limitPrice?: string,
  ): Promise<PaperQuote> {
    const snapshot = await this.polymarket.getMarketByCondition(request.conditionId);
    const identity = validatePaperMarket(snapshot.market, request);
    const minimumOrderSize = decimalMarketValue(snapshot.market.minimumOrderSize, "minimum order size");
    if (minimumOrderSize.gt(0) && new Decimal(request.shares).lt(minimumOrderSize)) {
      throw validation(`Paper orders require at least ${minimumOrderSize.toString()} shares`);
    }

    const [book, feeRate] = await Promise.all([
      this.polymarket.getOrderBook(request.tokenId),
      this.polymarket.getFeeRate(request.tokenId),
    ]);
    let quote: PaperQuote;
    try {
      quote = quotePaperOrder(request, identity, book, feeRate, limitPrice);
    } catch (error) {
      throw pricingError(error);
    }

    const portfolio = await this.store.portfolio(principal.id);
    if (request.side === "BUY") {
      const debit = new Decimal(quote.cashEffect).negated();
      if (new Decimal(portfolio.cash).lt(debit)) {
        throw paperConflict("PAPER_INSUFFICIENT_CASH", "The paper account does not have enough cash for this fill");
      }
    } else {
      const position = portfolio.positions.find((item) => item.tokenId === request.tokenId);
      if (!position || position.conditionId !== request.conditionId || new Decimal(position.shares).lt(request.shares)) {
        throw paperConflict("PAPER_INSUFFICIENT_SHARES", "The paper account does not own enough shares to sell");
      }
    }
    return quote;
  }

  private async orderResult(
    principal: Principal,
    result: PaperExecutionResult,
  ): Promise<PaperOrderResponse> {
    switch (result.state) {
      case "created":
      case "replayed":
        return { fill: result.fill, portfolio: await this.store.portfolio(principal.id) };
      case "key_mismatch":
        throw paperConflict("PAPER_IDEMPOTENCY_MISMATCH", "Idempotency-Key was already used with a different paper order");
      case "identity_conflict":
        throw validation("The paper position token belongs to a different market");
      case "insufficient_cash":
        throw paperConflict("PAPER_INSUFFICIENT_CASH", "The paper account does not have enough cash for this fill");
      case "insufficient_shares":
        throw paperConflict("PAPER_INSUFFICIENT_SHARES", "The paper account does not own enough shares to sell");
      case "strategy_stopped":
        throw paperConflict("PAPER_STRATEGY_STOPPED", "The paper strategy stopped before this fill could be committed");
      case "strategy_limit":
        throw paperConflict("PAPER_STRATEGY_POSITION_LIMIT", "The strategy maximum position would be exceeded");
    }
  }
}

function validatePaperMarket(
  market: MarketSearchMarket,
  request: PaperQuoteRequest,
): { conditionId: string; tokenId: string; marketQuestion: string; outcome: string } {
  if (market.conditionId !== request.conditionId) {
    throw validation("Paper market condition does not match current Polymarket data");
  }
  if (!market.active || market.closed || !market.acceptingOrders || !market.enableOrderBook) {
    throw paperConflict("PAPER_MARKET_CLOSED", "Polymarket is not accepting orders for this paper market");
  }
  if (market.clobTokenIds.length !== market.outcomes.length) {
    throw unavailable("Polymarket returned inconsistent outcome metadata");
  }
  const tokenIndex = market.clobTokenIds.indexOf(request.tokenId);
  if (tokenIndex < 0 || !market.outcomes[tokenIndex]) {
    throw validation("Paper outcome token does not belong to the selected market");
  }
  return {
    conditionId: request.conditionId,
    tokenId: request.tokenId,
    marketQuestion: market.question,
    outcome: market.outcomes[tokenIndex],
  };
}

function resolvedPrices(market: MarketSearchMarket): Map<string, "0" | "1"> | null {
  if (!market.closed || market.acceptingOrders) return null;
  if (market.clobTokenIds.length === 0 || market.clobTokenIds.length !== market.outcomePrices.length) return null;
  try {
    const prices = market.outcomePrices.map((value) => new Decimal(value));
    if (prices.some((value) => !value.eq(0) && !value.eq(1)) || prices.filter((value) => value.eq(1)).length !== 1) {
      return null;
    }
    return new Map(market.clobTokenIds.map((tokenId, index) => [tokenId, prices[index]!.eq(1) ? "1" : "0"]));
  } catch {
    return null;
  }
}

function decimalMarketValue(value: string, label: string): Decimal {
  try {
    const parsed = new Decimal(value);
    if (!parsed.isFinite() || parsed.lt(0)) throw new Error("invalid");
    return parsed;
  } catch {
    throw unavailable(`Polymarket returned an invalid ${label}`);
  }
}

function pricingError(error: unknown): AppError {
  if (!(error instanceof PaperPricingError)) {
    return unavailable("Polymarket pricing is temporarily unavailable");
  }
  if (error.reason === "PRICE_MOVED") {
    return paperConflict("PAPER_PRICE_MOVED", error.message);
  }
  if (error.reason === "INSUFFICIENT_LIQUIDITY") {
    return paperConflict("PAPER_INSUFFICIENT_LIQUIDITY", error.message);
  }
  return unavailable(error.message);
}

function paperConflict(code: string, message: string): AppError {
  return new AppError(409, code, message);
}

function paperRequestHash(request: PaperOrderRequest): string {
  return createHash("sha256")
    .update([
      request.conditionId,
      request.tokenId,
      request.side,
      request.shares,
      request.limitPrice,
    ].join("\u0000"))
    .digest("hex");
}

function safeMessage(error: unknown): string {
  return error instanceof AppError || error instanceof PaperPricingError
    ? error.message
    : "market data is temporarily unavailable.";
}
