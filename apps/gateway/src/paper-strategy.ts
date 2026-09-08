import { createHash, randomUUID } from "node:crypto";

import type {
  PaperOrderRequest,
  PaperPortfolio,
  PaperQuote,
  PaperQuoteRequest,
  PaperStrategySnapshot,
  PaperStrategyStartRequest,
} from "@polytrade/contracts";
import { Decimal } from "decimal.js";

import { AppError, validation } from "./errors.js";
import type { PaperTradingService } from "./paper.js";
import type {
  PaperStrategyClaim,
  PaperStrategyScanResult,
  PaperStrategyStore,
} from "./paper-strategy-store.js";
import type { Principal } from "./types.js";

const DEFAULT_POLL_INTERVAL_MS = 1_000;
const DEFAULT_LEASE_MS = 300_000;
const DEFAULT_BATCH_SIZE = 10;
// A strategy that keeps hitting transient errors must not write an event row
// every interval: the retry delay doubles per consecutive failure up to this cap.
const MAX_RETRY_BACKOFF_SECONDS = 600;
const DEFAULT_PRUNE_INTERVAL_MS = 6 * 60 * 60_000;
const DEFAULT_EVENT_RETAIN_PER_STRATEGY = 200;
const DEFAULT_EVENT_MAX_AGE_DAYS = 30;

export class PaperStrategyService {
  constructor(
    private readonly store: PaperStrategyStore,
    private readonly paper: Pick<PaperTradingService, "strategyTarget">,
    private readonly now: () => Date = () => new Date(),
  ) {}

  snapshot(principal: Principal): Promise<PaperStrategySnapshot> {
    return this.store.snapshot(principal.id);
  }

  async start(
    principal: Principal,
    request: PaperStrategyStartRequest,
    idempotencyKey: string,
  ): Promise<PaperStrategySnapshot> {
    const identity = await this.paper.strategyTarget(request.conditionId, request.tokenId);
    const minimum = decimal(identity.minimumOrderSize, "minimum order size");
    if (decimal(request.sharesPerOrder, "shares per order").lt(minimum)) {
      throw validation(`Strategy orders require at least ${minimum.toString()} shares`);
    }
    if (decimal(request.maxPosition, "maximum position").lt(request.sharesPerOrder)) {
      throw validation("Strategy maximum position must allow one complete order");
    }

    const requestHash = createHash("sha256")
      .update([
        request.conditionId,
        request.tokenId,
        request.entryPrice,
        request.exitPrice,
        request.sharesPerOrder,
        request.maxPosition,
        String(request.intervalSeconds),
      ].join("\u0000"))
      .digest("hex");
    const result = await this.store.start({
      strategyId: randomUUID(),
      principalId: principal.id,
      idempotencyKey,
      requestHash,
      request,
      marketQuestion: identity.marketQuestion,
      outcome: identity.outcome,
      minimumOrderSize: identity.minimumOrderSize,
      startedAt: this.now(),
    });
    if (result.state === "key_mismatch") {
      throw conflict("PAPER_STRATEGY_IDEMPOTENCY_MISMATCH", "Idempotency-Key was already used with different strategy settings");
    }
    if (result.state === "already_running") {
      throw conflict("PAPER_STRATEGY_RUNNING", "Stop the active paper strategy before starting another one");
    }
    return this.store.snapshot(principal.id);
  }

  stop(principal: Principal): Promise<PaperStrategySnapshot> {
    return this.store.stop(principal.id, this.now());
  }
}

export interface PaperStrategyTradingPort {
  portfolio(principal: Principal): Promise<PaperPortfolio>;
  quote(principal: Principal, request: PaperQuoteRequest): Promise<PaperQuote>;
  strategyOrder(
    principal: Principal,
    request: PaperOrderRequest,
    idempotencyKey: string,
    strategyGuard: {
      strategyId: string;
      scanId: string;
      leaseOwner: string;
      maxPosition: string;
    },
  ): ReturnType<PaperTradingService["order"]>;
  refresh(principal: Principal): Promise<PaperPortfolio>;
}

export class PaperStrategyBackgroundRunner {
  private readonly owner = randomUUID();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pruneTimer: ReturnType<typeof setTimeout> | null = null;
  private closing = false;
  private currentRun: Promise<number> | null = null;
  private readonly inFlight = new Set<Promise<void>>();
  private readonly retryStreaks = new Map<string, number>();

  constructor(
    private readonly store: PaperStrategyStore,
    private readonly paper: PaperStrategyTradingPort,
    private readonly options: {
      pollIntervalMs?: number;
      leaseMs?: number;
      batchSize?: number;
      pruneIntervalMs?: number;
      retainEventsPerStrategy?: number;
      eventMaxAgeDays?: number;
      now?: () => Date;
      onError?: (error: unknown) => void;
    } = {},
  ) {}

  start(): void {
    if (this.closing || this.timer) return;
    this.schedule(0);
    this.schedulePrune(this.options.pruneIntervalMs ?? DEFAULT_PRUNE_INTERVAL_MS);
  }

  async close(): Promise<void> {
    this.closing = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (this.pruneTimer) clearTimeout(this.pruneTimer);
    this.pruneTimer = null;
    await Promise.allSettled([
      ...(this.currentRun ? [this.currentRun] : []),
      ...this.inFlight,
    ]);
  }

  runOnce(): Promise<number> {
    if (this.currentRun || this.closing) return Promise.resolve(0);
    const run = this.executeRun();
    this.currentRun = run;
    const clear = () => {
      if (this.currentRun === run) this.currentRun = null;
    };
    void run.then(clear, clear);
    return run;
  }

  private async executeRun(): Promise<number> {
    const now = this.now();
    const claims = await this.store.claimDue(
      this.owner,
      now,
      new Date(now.getTime() + (this.options.leaseMs ?? DEFAULT_LEASE_MS)),
      this.options.batchSize ?? DEFAULT_BATCH_SIZE,
    );
    const work = claims.map((claim) => {
      const pending = this.processClaim(claim).catch((error) => this.options.onError?.(error));
      this.inFlight.add(pending);
      void pending.finally(() => this.inFlight.delete(pending));
      return pending;
    });
    await Promise.all(work);
    return claims.length;
  }

  private schedule(delay: number): void {
    if (this.closing) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.runOnce()
        .catch((error) => this.options.onError?.(error))
        .finally(() => this.schedule(this.options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS));
    }, delay);
    this.timer.unref?.();
  }

  // paper_strategy_events grows one row per scan, so without pruning a
  // long-lived WAIT-every-5s strategy would accumulate ~17k rows per day.
  private schedulePrune(delay: number): void {
    if (this.closing || this.pruneTimer) return;
    this.pruneTimer = setTimeout(() => {
      this.pruneTimer = null;
      void this.store
        .pruneEvents({
          retainPerStrategy: this.options.retainEventsPerStrategy ?? DEFAULT_EVENT_RETAIN_PER_STRATEGY,
          maxAgeDays: this.options.eventMaxAgeDays ?? DEFAULT_EVENT_MAX_AGE_DAYS,
        })
        .catch((error) => this.options.onError?.(error))
        .finally(() => this.schedulePrune(this.options.pruneIntervalMs ?? DEFAULT_PRUNE_INTERVAL_MS));
    }, Math.min(delay, 2_147_000_000));
    this.pruneTimer.unref?.();
  }

  private async processClaim(claim: PaperStrategyClaim): Promise<void> {
    const principal = backgroundPrincipal(claim.principalId);
    const scannedAt = this.now();
    try {
      const portfolio = await this.paper.portfolio(principal);
      const held = decimal(
        portfolio.positions.find((position) => position.tokenId === claim.tokenId)?.shares ?? "0",
        "held shares",
      );
      const minimum = decimal(claim.minimumOrderSize, "minimum order size");
      const observations: string[] = [];
      let lastQuote: PaperQuote | undefined;

      if (held.gte(minimum) && held.gt(0)) {
        const sellQuantity = boundedQuantity(claim.sharesPerOrder, held);
        if (sellQuantity.gte(minimum)) {
          const request: PaperQuoteRequest = {
            conditionId: claim.conditionId,
            tokenId: claim.tokenId,
            side: "SELL",
            shares: sellQuantity.toFixed(6),
          };
          const quote = await this.paper.quote(principal, request);
          lastQuote = quote;
          if (decimal(quote.averagePrice, "sell quote").gte(claim.exitPrice)) {
            const result = await this.paper.strategyOrder(
              principal,
              { ...request, limitPrice: quote.limitPrice },
              strategyOrderKey(claim, "SELL"),
              strategyGuard(claim),
            );
            await this.refreshMarks(principal).catch(() => undefined);
            await this.complete(claim, {
              action: "SELL",
              message: `Sold ${result.fill.shares} ${result.fill.outcome} shares at ${result.fill.averagePrice}.`,
              side: "SELL",
              price: result.fill.averagePrice,
              fillId: result.fill.fillId,
            }, scannedAt);
            return;
          }
          observations.push(`exit ${quote.averagePrice} is below ${claim.exitPrice}`);
        }
      } else if (held.gt(0)) {
        observations.push(`holding ${held.toString()} shares, below the ${minimum.toString()} share sell minimum`);
      }

      const capacity = Decimal.max(0, decimal(claim.maxPosition, "maximum position").minus(held));
      const buyQuantity = boundedQuantity(claim.sharesPerOrder, capacity);
      if (buyQuantity.gte(minimum) && buyQuantity.gt(0)) {
        const request: PaperQuoteRequest = {
          conditionId: claim.conditionId,
          tokenId: claim.tokenId,
          side: "BUY",
          shares: buyQuantity.toFixed(6),
        };
        const quote = await this.paper.quote(principal, request);
        lastQuote = quote;
        if (decimal(quote.averagePrice, "buy quote").lte(claim.entryPrice)) {
          const result = await this.paper.strategyOrder(
            principal,
            { ...request, limitPrice: quote.limitPrice },
            strategyOrderKey(claim, "BUY"),
            strategyGuard(claim),
          );
          await this.refreshMarks(principal).catch(() => undefined);
          await this.complete(claim, {
            action: "BUY",
            message: `Bought ${result.fill.shares} ${result.fill.outcome} shares at ${result.fill.averagePrice}.`,
            side: "BUY",
            price: result.fill.averagePrice,
            fillId: result.fill.fillId,
          }, scannedAt);
          return;
        }
        observations.push(`entry ${quote.averagePrice} is above ${claim.entryPrice}`);
      } else {
        observations.push(`position limit reached at ${held.toString()} shares`);
      }

      await this.refreshMarks(principal).catch(() => undefined);
      await this.complete(claim, {
        action: "WAIT",
        message: `No trade: ${observations.join("; ") || "thresholds were not crossed"}.`,
        side: lastQuote?.side,
        price: lastQuote?.averagePrice,
      }, scannedAt);
    } catch (error) {
      if (strategyStopError(error)) {
        this.retryStreaks.delete(claim.strategyId);
        await this.store.failClaim(claim, stopMessage(error), scannedAt);
        return;
      }
      await this.refreshMarks(principal).catch(() => undefined);
      const message = error instanceof Error && error.message ? error.message : "Background strategy scan failed";
      const streak = (this.retryStreaks.get(claim.strategyId) ?? 0) + 1;
      this.retryStreaks.set(claim.strategyId, streak);
      // Transient failure (moved book, upstream hiccup): retry, but back off so
      // a persistently failing strategy does not emit an event row per interval.
      const backoffSeconds = Math.min(
        claim.intervalSeconds * 2 ** (streak - 1),
        MAX_RETRY_BACKOFF_SECONDS,
      );
      await this.store.completeClaim(
        claim,
        { action: "ERROR", message: `${message}. The background runner will retry.` },
        scannedAt,
        new Date(scannedAt.getTime() + Math.max(claim.intervalSeconds, backoffSeconds) * 1_000),
      );
    }
  }

  private complete(
    claim: PaperStrategyClaim,
    result: PaperStrategyScanResult,
    scannedAt: Date,
  ): Promise<boolean> {
    this.retryStreaks.delete(claim.strategyId);
    return this.store.completeClaim(
      claim,
      result,
      scannedAt,
      new Date(scannedAt.getTime() + claim.intervalSeconds * 1_000),
    );
  }

  private async refreshMarks(principal: Principal): Promise<void> {
    await this.paper.refresh(principal);
  }

  private now(): Date {
    return this.options.now?.() ?? new Date();
  }
}

function backgroundPrincipal(principalId: string): Principal {
  const separator = principalId.indexOf(":");
  const issuer = principalId.slice(0, separator) === "clerk" ? "clerk" : "assethero";
  return {
    id: principalId,
    issuer,
    subject: separator >= 0 ? principalId.slice(separator + 1) : principalId,
    scopes: new Set(["research"]),
  };
}

function boundedQuantity(requested: string, capacity: Decimal): Decimal {
  return Decimal.min(decimal(requested, "shares per order"), capacity)
    .toDecimalPlaces(6, Decimal.ROUND_DOWN);
}

function strategyOrderKey(claim: PaperStrategyClaim, side: "BUY" | "SELL"): string {
  return `strategy:${claim.strategyId}:${claim.scanId}:${side}`;
}

function strategyGuard(claim: PaperStrategyClaim) {
  return {
    strategyId: claim.strategyId,
    scanId: claim.scanId,
    leaseOwner: claim.leaseOwner,
    maxPosition: claim.maxPosition,
  };
}

function strategyStopError(error: unknown): boolean {
  return error instanceof AppError && [
    "PAPER_MARKET_CLOSED",
    "PAPER_STRATEGY_STOPPED",
    "VALIDATION_ERROR",
    // A scan cannot fix these by retrying: cash only changes when the user
    // trades or tops up, and a book without depth stays that way until the
    // market moves. Stop the strategy with a clear message instead of
    // emitting an identical error row every scan.
    "PAPER_INSUFFICIENT_CASH",
    "PAPER_INSUFFICIENT_LIQUIDITY",
  ].includes(error.code);
}

function stopMessage(error: unknown): string {
  const detail = error instanceof Error && error.message ? error.message : "The scan could not be completed";
  if (error instanceof AppError && error.code === "PAPER_INSUFFICIENT_CASH") {
    return `Stopped: the paper account does not have enough cash for this strategy's orders (${detail}).`;
  }
  if (error instanceof AppError && error.code === "PAPER_INSUFFICIENT_LIQUIDITY") {
    return `Stopped: the order book currently cannot fill orders of this size (${detail}). Reduce shares per order or restart the strategy later.`;
  }
  return detail;
}

function decimal(value: string, label: string): Decimal {
  try {
    const parsed = new Decimal(value);
    if (!parsed.isFinite() || parsed.lt(0)) throw new Error("invalid");
    return parsed;
  } catch {
    throw validation(`Invalid ${label}`);
  }
}

function conflict(code: string, message: string): AppError {
  return new AppError(409, code, message);
}
