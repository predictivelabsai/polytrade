import type {
  AgentPredictionRequest,
  AgentPredictionStatus,
} from "@polytrade/contracts";
import type { Pool } from "pg";

import { iso } from "./paper-store.js";

export interface AgentPredictionRecord {
  predictionId: string;
  conditionId: string;
  tokenId: string | null;
  marketQuestion: string;
  predictedOutcome: string;
  confidence: string | null;
  category: string | null;
  status: AgentPredictionStatus;
  gradedOutcome: string | null;
  hit: boolean | null;
  voidReason: string | null;
  madeAt: string;
  gradedAt: string | null;
}

export interface PendingPrediction {
  predictionId: string;
  conditionId: string;
  tokenId: string | null;
  marketQuestion: string;
  predictedOutcome: string;
  madeAt: Date;
  gradeAttempts: number;
}

export interface AccuracySnapshot {
  totals: {
    graded: number;
    hits: number;
    hitRatePct: string | null;
    pending: number;
    voided: number;
    lastGradedAt: string | null;
  };
  byCategory: Array<{
    category: string;
    graded: number;
    hits: number;
    hitRatePct: string | null;
  }>;
  recent: Array<{
    marketQuestion: string;
    predictedOutcome: string;
    gradedOutcome: string | null;
    hit: boolean | null;
    madeAt: string;
    gradedAt: string | null;
    category: string | null;
  }>;
}

export interface PredictionGrade {
  status: "GRADED";
  gradedOutcome: string;
  hit: boolean;
  category: string | null;
  tags: string[];
  marketSlug: string | null;
  resolutionPrices: string[];
  closedTime: Date | null;
  gradedAt: Date;
}

export interface AgentPredictionStore {
  record(
    principalId: string,
    input: AgentPredictionRequest,
    now: Date,
  ): Promise<AgentPredictionRecord>;
  claimPending(
    owner: string,
    now: Date,
    leaseUntil: Date,
    graceMs: number,
    limit: number,
  ): Promise<PendingPrediction[]>;
  grade(predictionId: string, owner: string, grade: PredictionGrade, now: Date): Promise<void>;
  voidOut(predictionId: string, owner: string, reason: string, now: Date): Promise<void>;
  reschedule(
    predictionId: string,
    owner: string,
    attempts: number,
    nextGradeAt: Date,
    now: Date,
  ): Promise<void>;
  releaseClaim(predictionId: string, owner: string, nextGradeAt: Date, now: Date): Promise<void>;
  accuracySnapshot(recentLimit: number): Promise<AccuracySnapshot>;
}

export class PostgresAgentPredictionStore implements AgentPredictionStore {
  constructor(
    private readonly pool: Pool,
    private readonly now: () => Date = () => new Date(),
  ) {}

  async record(
    principalId: string,
    input: AgentPredictionRequest,
    now: Date,
  ): Promise<AgentPredictionRecord> {
    // The partial unique index on open claims makes duplicate calls for the
    // same (principal, market, outcome) no-ops: insert, then read back the
    // surviving row so tool retries return the original prediction.
    await this.pool.query(
      `INSERT INTO polytrade_agent.agent_predictions
         (principal_id, condition_id, token_id, market_question, predicted_outcome, confidence, made_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       ON CONFLICT (principal_id, condition_id, lower(predicted_outcome)) WHERE status = 'PENDING'
       DO NOTHING`,
      [
        principalId,
        input.conditionId,
        input.tokenId ?? null,
        input.marketQuestion,
        input.predictedOutcome,
        input.confidence ?? null,
        now,
      ],
    );
    const result = await this.pool.query(
      `SELECT prediction_id, condition_id, token_id, market_question, predicted_outcome,
              confidence, category, status, graded_outcome, hit, void_reason, made_at, graded_at
       FROM polytrade_agent.agent_predictions
       WHERE principal_id=$1 AND condition_id=$2 AND lower(predicted_outcome)=lower($3)
         AND status='PENDING'`,
      [principalId, input.conditionId, input.predictedOutcome],
    );
    return predictionRecord(result.rows[0] as Record<string, unknown>);
  }

  async claimPending(
    owner: string,
    now: Date,
    leaseUntil: Date,
    graceMs: number,
    limit: number,
  ): Promise<PendingPrediction[]> {
    const result = await this.pool.query(
      `WITH due AS (
         SELECT prediction_id
         FROM polytrade_agent.agent_predictions
         WHERE status='PENDING' AND next_grade_at<=$2
           AND made_at<=$3
           AND (lease_until IS NULL OR lease_until<=$2)
         ORDER BY next_grade_at, made_at, prediction_id
         FOR UPDATE SKIP LOCKED
         LIMIT $5
       )
       UPDATE polytrade_agent.agent_predictions AS prediction
       SET lease_owner=$1, lease_until=$4, updated_at=$2
       FROM due
       WHERE prediction.prediction_id=due.prediction_id
       RETURNING prediction.prediction_id, prediction.condition_id, prediction.token_id,
                 prediction.market_question, prediction.predicted_outcome, prediction.made_at,
                 prediction.grade_attempts`,
      [owner, now, new Date(now.getTime() - graceMs), leaseUntil, limit],
    );
    return result.rows.map((row: Record<string, unknown>) => ({
      predictionId: String(row.prediction_id),
      conditionId: String(row.condition_id),
      tokenId: row.token_id === null ? null : String(row.token_id),
      marketQuestion: String(row.market_question),
      predictedOutcome: String(row.predicted_outcome),
      madeAt: new Date(String(row.made_at)),
      gradeAttempts: Number(row.grade_attempts ?? 0),
    }));
  }

  async grade(predictionId: string, owner: string, grade: PredictionGrade, now: Date): Promise<void> {
    await this.pool.query(
      `UPDATE polytrade_agent.agent_predictions
       SET status='GRADED', graded_outcome=$3, hit=$4, category=$5, tags=$6, market_slug=$7,
           resolution_prices=$8, closed_time=$9, graded_at=$10, lease_owner=NULL, lease_until=NULL,
           next_grade_at=$10, updated_at=$2
       WHERE prediction_id=$1 AND lease_owner=$11`,
      [
        predictionId,
        now,
        grade.gradedOutcome,
        grade.hit,
        grade.category,
        JSON.stringify(grade.tags),
        grade.marketSlug,
        JSON.stringify(grade.resolutionPrices),
        grade.closedTime,
        grade.gradedAt,
        owner,
      ],
    );
  }

  async voidOut(predictionId: string, owner: string, reason: string, now: Date): Promise<void> {
    await this.pool.query(
      `UPDATE polytrade_agent.agent_predictions
       SET status='VOID', void_reason=$3, graded_at=$4, lease_owner=NULL, lease_until=NULL,
           next_grade_at=$4, updated_at=$4
       WHERE prediction_id=$1 AND lease_owner=$2`,
      [predictionId, owner, reason, now],
    );
  }

  async reschedule(
    predictionId: string,
    owner: string,
    attempts: number,
    nextGradeAt: Date,
    now: Date,
  ): Promise<void> {
    await this.pool.query(
      `UPDATE polytrade_agent.agent_predictions
       SET grade_attempts=$3, next_grade_at=$4, lease_owner=NULL, lease_until=NULL, updated_at=$5
       WHERE prediction_id=$1 AND lease_owner=$2`,
      [predictionId, owner, attempts, nextGradeAt, now],
    );
  }

  async releaseClaim(
    predictionId: string,
    owner: string,
    nextGradeAt: Date,
    now: Date,
  ): Promise<void> {
    await this.pool.query(
      `UPDATE polytrade_agent.agent_predictions
       SET next_grade_at=$3, lease_owner=NULL, lease_until=NULL, updated_at=$4
       WHERE prediction_id=$1 AND lease_owner=$2`,
      [predictionId, owner, nextGradeAt, now],
    );
  }

  async accuracySnapshot(recentLimit: number): Promise<AccuracySnapshot> {
    const [totalsResult, categoryResult, recentResult] = await Promise.all([
      this.pool.query(
        `SELECT count(*) FILTER (WHERE status='GRADED') AS graded,
                count(*) FILTER (WHERE status='GRADED' AND hit) AS hits,
                count(*) FILTER (WHERE status='PENDING') AS pending,
                count(*) FILTER (WHERE status='VOID') AS voided,
                max(graded_at) FILTER (WHERE status='GRADED') AS last_graded_at
         FROM polytrade_agent.agent_predictions`,
      ),
      this.pool.query(
        `SELECT COALESCE(NULLIF(category, ''), 'Other') AS category,
                count(*) FILTER (WHERE status='GRADED') AS graded,
                count(*) FILTER (WHERE status='GRADED' AND hit) AS hits
         FROM polytrade_agent.agent_predictions
         GROUP BY COALESCE(NULLIF(category, ''), 'Other')
         HAVING count(*) FILTER (WHERE status='GRADED') > 0
         ORDER BY graded DESC, category
         LIMIT 8`,
      ),
      this.pool.query(
        `SELECT market_question, predicted_outcome, graded_outcome, hit, made_at, graded_at, category
         FROM polytrade_agent.agent_predictions
         WHERE status='GRADED'
         ORDER BY graded_at DESC, prediction_id
         LIMIT $1`,
        [recentLimit],
      ),
    ]);
    const totals = totalsResult.rows[0] as Record<string, unknown>;
    const graded = Number(totals.graded ?? 0);
    const hits = Number(totals.hits ?? 0);
    return {
      totals: {
        graded,
        hits,
        hitRatePct: hitRatePct(graded, hits),
        pending: Number(totals.pending ?? 0),
        voided: Number(totals.voided ?? 0),
        lastGradedAt: totals.last_graded_at ? iso(totals.last_graded_at) : null,
      },
      byCategory: (categoryResult.rows as Record<string, unknown>[]).map((row) => {
        const categoryGraded = Number(row.graded ?? 0);
        const categoryHits = Number(row.hits ?? 0);
        return {
          category: String(row.category),
          graded: categoryGraded,
          hits: categoryHits,
          hitRatePct: hitRatePct(categoryGraded, categoryHits),
        };
      }),
      recent: (recentResult.rows as Record<string, unknown>[]).map((row) => ({
        marketQuestion: String(row.market_question),
        predictedOutcome: String(row.predicted_outcome),
        gradedOutcome: row.graded_outcome === null ? null : String(row.graded_outcome),
        hit: row.hit === null ? null : Boolean(row.hit),
        madeAt: iso(row.made_at),
        gradedAt: row.graded_at ? iso(row.graded_at) : null,
        category: row.category === null ? null : String(row.category),
      })),
    };
  }
}

export function hitRatePct(graded: number, hits: number): string | null {
  if (graded <= 0) return null;
  return ((hits * 100) / graded).toFixed(2);
}

function predictionRecord(row: Record<string, unknown>): AgentPredictionRecord {
  return {
    predictionId: String(row.prediction_id),
    conditionId: String(row.condition_id),
    tokenId: row.token_id === null ? null : String(row.token_id),
    marketQuestion: String(row.market_question),
    predictedOutcome: String(row.predicted_outcome),
    confidence: row.confidence === null || row.confidence === undefined ? null : String(row.confidence),
    category: row.category === null || row.category === undefined ? null : String(row.category),
    status: String(row.status) as AgentPredictionStatus,
    gradedOutcome: row.graded_outcome === null || row.graded_outcome === undefined ? null : String(row.graded_outcome),
    hit: row.hit === null || row.hit === undefined ? null : Boolean(row.hit),
    voidReason: row.void_reason === null || row.void_reason === undefined ? null : String(row.void_reason),
    madeAt: iso(row.made_at),
    gradedAt: row.graded_at === null || row.graded_at === undefined ? null : iso(row.graded_at),
  };
}