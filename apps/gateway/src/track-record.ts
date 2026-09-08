// Opt-in public track records: an unauthenticated read-only projection of one
// paper account, addressed by a random share token instead of the principal.
// Responses are cached in-memory (same pattern as the public market reads) so a
// shared link collapsing under traffic costs a handful of Postgres reads.

import type { PublicTrackRecord, PaperShareStatus } from "@polytrade/contracts";
import { Decimal } from "decimal.js";

import { notFound } from "./errors.js";
import { money } from "./paper-pricing.js";
import { TtlCache } from "./public-cache.js";
import type { TrackRecordStore } from "./track-record-store.js";

export const TRACK_RECORD_CACHE_TTL_MS = {
  detail: 15_000,
  missing: 60_000,
} as const;

export class PublicTrackRecordService {
  constructor(
    private readonly store: TrackRecordStore,
    private readonly cache: TtlCache,
    private readonly now: () => Date = () => new Date(),
  ) {}

  status(principalId: string): Promise<PaperShareStatus> {
    return this.store.status(principalId);
  }

  enable(principalId: string): Promise<PaperShareStatus> {
    return this.store.enable(principalId);
  }

  rotate(principalId: string): Promise<PaperShareStatus> {
    return this.store.rotate(principalId);
  }

  disable(principalId: string): Promise<PaperShareStatus> {
    return this.store.disable(principalId);
  }

  async detail(token: string): Promise<PublicTrackRecord> {
    const missingKey = `notfound:track-record:${token}`;
    if (this.cache.isKnownMissing(missingKey)) {
      throw notFound("Track record not found");
    }
    return this.cache.load(`track-record:${token}`, TRACK_RECORD_CACHE_TTL_MS.detail, async () => {
      const principalId = await this.store.resolvePrincipal(token);
      if (!principalId) {
        this.cache.markMissing(missingKey, TRACK_RECORD_CACHE_TTL_MS.missing);
        throw notFound("Track record not found");
      }
      return this.buildDetail(principalId);
    });
  }

  private async buildDetail(principalId: string): Promise<PublicTrackRecord> {
    const snapshot = await this.store.snapshot(principalId);
    const cash = new Decimal(snapshot.account.cash);
    const liveEquity = snapshot.positions.reduce(
      (total, position) => total.plus(position.liquidationValue),
      cash,
    );
    const unrealizedPnl = snapshot.positions.reduce(
      (total, position) => total.plus(position.unrealizedPnl),
      new Decimal(0),
    );

    // The curve replays settled cash: fills outside the visible window collapse
    // into the baseline so the first visible point reconciles with the account.
    const windowSum = snapshot.curve.fills.reduce(
      (total, fill) => total.plus(fill.cashEffect),
      new Decimal(0),
    );
    let running = cash.minus(windowSum);
    const points = snapshot.curve.fills.map((fill) => {
      running = running.plus(fill.cashEffect);
      return { t: fill.createdAt, equity: money(running) };
    });
    if (points.length === 0) {
      points.push({ t: snapshot.profile.startedAt, equity: snapshot.account.initialCash });
    }
    points.push({ t: this.now().toISOString(), equity: money(liveEquity) });

    return {
      profile: {
        displayName: "Paper account",
        startedAt: snapshot.profile.startedAt,
      },
      stats: {
        initialCash: snapshot.account.initialCash,
        cash: snapshot.account.cash,
        equity: money(liveEquity),
        totalPnl: money(liveEquity.minus(snapshot.account.initialCash)),
        realizedPnl: snapshot.totals.realizedPnl,
        unrealizedPnl: money(unrealizedPnl),
        totalFees: snapshot.totals.totalFees,
        tradeCount: snapshot.totals.tradeCount,
        winRate: snapshot.totals.closed === 0
          ? null
          : new Decimal(snapshot.totals.wins * 100).div(snapshot.totals.closed).toFixed(2),
      },
      equityCurve: points,
      positions: snapshot.positions,
      fills: snapshot.recentFills.map((fill) => ({
        fillId: fill.fillId,
        kind: fill.kind,
        marketQuestion: fill.marketQuestion,
        outcome: fill.outcome,
        shares: fill.shares,
        averagePrice: fill.averagePrice,
        fee: fill.fee,
        cashEffect: fill.cashEffect,
        realizedPnl: fill.realizedPnl,
        createdAt: fill.createdAt,
      })),
      observedAt: this.now().toISOString(),
    };
  }
}