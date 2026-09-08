// Public agent accuracy scorecard: a 60s-cached read model over the
// prediction store, plus a background grader that resolves pending
// predictions against Polymarket's final outcome prices.

import { randomUUID } from "node:crypto";

import type { AgentPredictionHitRate, AgentPredictionRequest } from "@polytrade/contracts";

import type {
  AccuracySnapshot,
  AgentPredictionRecord,
  AgentPredictionStore,
  PendingPrediction,
} from "./agent-prediction-store.js";
import { AppError } from "./errors.js";
import type { MarketResolution, PolymarketPort } from "./polymarket.js";
import { pickCategory } from "./polymarket.js";
import { TtlCache } from "./public-cache.js";

export const ACCURACY_CACHE_TTL_MS = 60_000;

const DEFAULT_POLL_INTERVAL_MS = 60_000;
const DEFAULT_LEASE_MS = 600_000;
const DEFAULT_BATCH_SIZE = 10;
export const DEFAULT_GRACE_MS = 900_000;
const OPEN_RETRY_MS = 3_600_000;
const BASE_BACKOFF_MS = 60_000;
const MAX_BACKOFF_MS = 24 * 3_600_000;
const DEFAULT_MAX_ATTEMPTS = 30;

export type GradeOutcome =
  | { kind: "graded"; hit: boolean; winner: string }
  | { kind: "open" }
  | { kind: "void"; reason: string };

/** Trim, collapse whitespace, and casefold so "Yes " matches "yes". */
export function normalizeOutcome(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

/**
 * Pure grading rule for one claimed prediction against one market snapshot.
 * The anti-gaming check runs first on resolved markets: a call recorded after
 * the market resolved never counts, whatever it says.
 */
export function gradePrediction(
  resolution: MarketResolution,
  prediction: { predictedOutcome: string; madeAt: Date },
): GradeOutcome {
  if (resolution.winner === null) {
    const { closed, acceptingOrders } = resolution.market;
    if (!closed || acceptingOrders) return { kind: "open" };
    return { kind: "void", reason: "Market resolved without a clear binary winner" };
  }
  const closedTime = resolution.closedTime ? new Date(resolution.closedTime) : null;
  if (closedTime && prediction.madeAt.getTime() > closedTime.getTime()) {
    return { kind: "void", reason: "Recorded after the market resolved" };
  }
  return {
    kind: "graded",
    hit: normalizeOutcome(prediction.predictedOutcome) === normalizeOutcome(resolution.winner),
    winner: resolution.winner,
  };
}

export class PublicAgentAccuracyService {
  constructor(
    private readonly store: AgentPredictionStore,
    private readonly cache: TtlCache,
  ) {}

  record(
    principalId: string,
    input: AgentPredictionRequest,
    now: Date = new Date(),
  ): Promise<AgentPredictionRecord> {
    return this.store.record(principalId, input, now);
  }

  async snapshot(): Promise<AgentPredictionHitRate> {
    const raw = await this.cache.load(`accuracy:${ACCURACY_CACHE_TTL_MS}`, ACCURACY_CACHE_TTL_MS, () =>
      this.store.accuracySnapshot(25),
    );
    return toHitRate(raw);
  }
}

/**
 * Background runner over pending predictions: claims a lease-batched set,
 * fetches each market's resolution, and grades, voids, or backs off.
 * Mirrors AlertDeliveryRunner.
 */
export class AgentPredictionGrader {
  private readonly owner = randomUUID();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private currentRun: Promise<number> | null = null;

  constructor(
    private readonly store: AgentPredictionStore,
    private readonly polymarket: PolymarketPort,
    private readonly options: {
      pollIntervalMs?: number;
      leaseMs?: number;
      batchSize?: number;
      graceMs?: number;
      maxAttempts?: number;
      onError?: (error: unknown) => void;
    } = {},
  ) {}

  start(): void {
    if (this.timer) return;
    this.schedule(0);
  }

  async close(): Promise<void> {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const run = this.currentRun;
    if (run) await run.catch(() => undefined);
  }

  runOnce(): Promise<number> {
    if (this.currentRun) return Promise.resolve(0);
    const run = this.executeRun();
    this.currentRun = run;
    const clear = () => {
      if (this.currentRun === run) this.currentRun = null;
    };
    void run.then(clear, clear);
    return run;
  }

  private async executeRun(): Promise<number> {
    const now = new Date();
    const batch = await this.store.claimPending(
      this.owner,
      now,
      new Date(now.getTime() + (this.options.leaseMs ?? DEFAULT_LEASE_MS)),
      this.options.graceMs ?? DEFAULT_GRACE_MS,
      this.options.batchSize ?? DEFAULT_BATCH_SIZE,
    );
    for (const prediction of batch) {
      await this.gradeOne(prediction);
    }
    return batch.length;
  }

  private schedule(delay: number): void {
    if (this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.runOnce()
        .catch((error) => this.options.onError?.(error))
        .finally(() => this.schedule(this.options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS));
    }, delay);
    this.timer.unref?.();
  }

  private async gradeOne(prediction: PendingPrediction): Promise<void> {
    const now = new Date();
    let resolution: MarketResolution;
    try {
      resolution = await this.polymarket.getMarketResolution(prediction.conditionId);
    } catch (error) {
      await this.backoffOrFail(prediction, error, now);
      return;
    }
    const outcome = gradePrediction(resolution, prediction);
    if (outcome.kind === "open") {
      await this.store.releaseClaim(
        prediction.predictionId,
        this.owner,
        new Date(now.getTime() + OPEN_RETRY_MS),
        now,
      );
      return;
    }
    if (outcome.kind === "void") {
      await this.store.voidOut(prediction.predictionId, this.owner, outcome.reason, now);
      return;
    }
    await this.store.grade(
      prediction.predictionId,
      this.owner,
      {
        status: "GRADED",
        gradedOutcome: outcome.winner,
        hit: outcome.hit,
        category: pickCategory(resolution.tags, resolution.category),
        tags: resolution.tags,
        marketSlug: resolution.market.slug,
        resolutionPrices: [...resolution.market.outcomePrices],
        closedTime: resolution.closedTime ? new Date(resolution.closedTime) : null,
        gradedAt: now,
      },
      now,
    );
  }

  /** Metadata trouble is transient: exponential backoff, then a terminal VOID. */
  private async backoffOrFail(prediction: PendingPrediction, error: unknown, now: Date): Promise<void> {
    const attempts = prediction.gradeAttempts + 1;
    if (attempts >= (this.options.maxAttempts ?? DEFAULT_MAX_ATTEMPTS)) {
      await this.store.voidOut(
        prediction.predictionId,
        this.owner,
        "Market metadata unavailable after repeated attempts",
        now,
      );
      return;
    }
    const delay = Math.min(BASE_BACKOFF_MS * 2 ** (attempts - 1), MAX_BACKOFF_MS);
    await this.store.reschedule(prediction.predictionId, this.owner, attempts, new Date(now.getTime() + delay), now);
    if (error instanceof AppError && (error.statusCode === 404 || error.statusCode === 503)) {
      // notFound/unavailable are the expected Gamma shapes; anything else is
      // worth surfacing to the operator error hook.
      return;
    }
    this.options.onError?.(error);
  }
}

function toHitRate(snapshot: AccuracySnapshot): AgentPredictionHitRate {
  return {
    totals: snapshot.totals,
    byCategory: snapshot.byCategory,
    recent: snapshot.recent,
    observedAt: new Date().toISOString(),
  };
}