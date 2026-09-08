import cors from "@fastify/cors";
import helmet from "@fastify/helmet";
import rateLimit from "@fastify/rate-limit";
import swagger from "@fastify/swagger";
import swaggerUi from "@fastify/swagger-ui";
import {
  accountOverviewSchema,
  agentPredictionHitRateSchema,
  agentPredictionRecordSchema,
  agentPredictionRequestSchema,
  alertChannelListSchema,
  alertChannelSchema,
  alertCreateChannelRequestSchema,
  alertDeliveryListSchema,
  alertTestSendResponseSchema,
  cancelRequestSchema,
  createIntentRequestSchema,
  paperFillsResponseSchema,
  paperOrderRequestSchema,
  paperOrderResponseSchema,
  paperPortfolioSchema,
  paperQuoteRequestSchema,
  paperQuoteSchema,
  paperShareManageRequestSchema,
  paperShareStatusSchema,
  paperStrategySnapshotSchema,
  paperStrategyStartRequestSchema,
  publicTrackRecordSchema,
  strategyTemplateListSchema,
  strategyTemplates,
  submitIntentRequestSchema,
  walletChallengeRequestSchema,
  walletSessionRequestSchema,
  walletSessionStatusSchema,
} from "@polytrade/contracts";
import Fastify, { type FastifyRequest } from "fastify";
import { createHash } from "node:crypto";
import {
  jsonSchemaTransform,
  serializerCompiler,
  validatorCompiler,
} from "fastify-type-provider-zod";
import type { Hex } from "viem";
import { z } from "zod";

import type { AlertService } from "./alert-service.js";
import { ACCURACY_CACHE_TTL_MS, type PublicAgentAccuracyService } from "./agent-accuracy.js";
import type { JwtVerifier } from "./auth.js";
import type { GatewayConfig } from "./config.js";
import { AppError, conflict, forbidden, validation } from "./errors.js";
import type { PaperStrategyService } from "./paper-strategy.js";
import type { PaperTradingService } from "./paper.js";
import type { PolymarketPort } from "./polymarket.js";
import { publicPriceHistoryTtlMs, PUBLIC_CACHE_TTL_MS, type PublicMarketService } from "./public-market.js";
import { registerServiceProxies } from "./proxy.js";
import type { TradingStore } from "./store.js";
import { TRACK_RECORD_CACHE_TTL_MS, type PublicTrackRecordService } from "./track-record.js";
import type { Principal } from "./types.js";
import type { TradingService } from "./trading.js";

export interface AppDependencies {
  config: GatewayConfig;
  verifier: JwtVerifier;
  store: TradingStore;
  polymarket: PolymarketPort;
  trading: TradingService;
  paper: PaperTradingService;
  paperStrategy: PaperStrategyService;
  publicMarkets: PublicMarketService;
  trackRecords: PublicTrackRecordService;
  agentAccuracy: PublicAgentAccuracyService;
  paperStrategyRunner?: { close(): Promise<void> };
  alerts: AlertService;
  alertsRunner?: { close(): Promise<void> };
  predictionGrader?: { close(): Promise<void> };
}

const marketQuery = z.object({
  query: z.string().min(1).max(200),
  limit: z.coerce.number().int().min(1).max(25).default(10),
  state: z.enum(["active", "resolved"]).default("active"),
});
const singleMarketQuery = z.object({ identifier: z.string().min(1).max(200), identifierType: z.enum(["id", "slug"]).default("slug") });
const historyQuery = z.object({ interval: z.enum(["1h", "6h", "1d", "1w", "max"]).default("1d") });
const tokenParams = z.object({ tokenId: z.string().regex(/^\d+$/) });
const conditionParams = z.object({ conditionId: z.string().min(1).max(200) });
const sessionParams = z.object({ sessionId: z.string().uuid() });
const intentParams = z.object({ intentId: z.string().uuid() });
const batchIntentSchema = z.object({ sessionId: z.string().uuid(), proposals: z.array(createIntentRequestSchema.shape.proposal).min(1).max(15) });
const batchSubmitSchema = z.object({ items: z.array(z.object({ intentId: z.string().uuid(), signature: submitIntentRequestSchema.shape.signature })).min(1).max(15) });
const paperFillsQuery = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(20),
  offset: z.coerce.number().int().min(0).default(0),
});
const alertChannelParams = z.object({ channelId: z.string().uuid() });
const alertDeliveriesQuery = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(20),
});
const publicMarketListQuery = z.object({
  limit: z.coerce.number().int().min(1).max(24).default(12),
  offset: z.coerce.number().int().min(0).max(1_000).default(0),
  order: z.enum(["volume24hr", "liquidity", "endDate"]).default("volume24hr"),
});
const slugParams = z.object({ slug: z.string().min(1).max(200) });
const publicHistoryQuery = z.object({ interval: z.enum(["1h", "6h", "1d", "1w", "max"]).default("1d") });
const trackRecordParams = z.object({ token: z.string().regex(/^[A-Za-z0-9_-]{32,64}$/) });

const cacheHeader = (ttlMs: number) => `public, max-age=${Math.max(1, Math.min(60, Math.floor(ttlMs / 1000)))}`;

export async function buildApp(deps: AppDependencies) {
  const app = Fastify({
    trustProxy: deps.config.trustedProxies,
    logger: {
      level: deps.config.NODE_ENV === "test" ? "silent" : "info",
      redact: {
        paths: [
          "req.headers.authorization",
          "req.body.signature",
          "req.body.items[*].signature",
          "*.encryptedCredentials",
          "*.encryptedTarget",
          "*.target",
          "*.botToken",
          "*.secret",
          "*.passphrase",
          "*.reasoning_content",
        ],
        censor: "[REDACTED]",
      },
    },
  });
  app.setValidatorCompiler(validatorCompiler);
  app.setSerializerCompiler(serializerCompiler);
  await app.register(cors, {
    origin: (origin, callback) => {
      if (!origin || deps.config.corsOrigins.includes(origin)) callback(null, true);
      else callback(forbidden("Origin is not allowed"), false);
    },
    credentials: false,
    allowedHeaders: ["Authorization", "Content-Type", "Idempotency-Key"],
    methods: ["GET", "POST", "DELETE", "OPTIONS"],
  });
  await app.register(helmet, { contentSecurityPolicy: false });
  await app.register(rateLimit, { max: 120, timeWindow: "1 minute" });
  await app.register(swagger, {
    transform: jsonSchemaTransform,
    openapi: {
      info: { title: "PolyTrade Gateway", version: "4.0.0" },
      components: { securitySchemes: { bearerAuth: { type: "http", scheme: "bearer", bearerFormat: "JWT" } } },
      security: [{ bearerAuth: [] }],
    },
  });
  await app.register(swaggerUi, { routePrefix: "/docs" });

  const authenticate = (scope: "research" | "trade") => async (request: FastifyRequest) => {
    request.principal = await deps.verifier.verifyAuthorization(request.headers.authorization, scope);
  };
  const principal = (request: FastifyRequest): Principal => {
    if (!request.principal) throw new AppError(500, "AUTH_CONTEXT_MISSING", "Authentication context missing");
    return request.principal;
  };
  const idempotency = (request: FastifyRequest): string => {
    const value = request.headers["idempotency-key"];
    if (typeof value !== "string" || !/^[A-Za-z0-9._:-]{8,200}$/.test(value)) {
      throw validation("Idempotency-Key header must be 8-200 safe characters");
    }
    return value;
  };
  const runIdempotent = async <T>(
    request: FastifyRequest,
    operation: string,
    payload: unknown,
    action: () => Promise<T> | T,
  ): Promise<T> => {
    const owner = principal(request);
    const key = idempotency(request);
    const requestHash = createHash("sha256").update(canonicalJson(payload)).digest("hex");
    const claim = await deps.store.beginIdempotency(owner.id, operation, key, requestHash);
    if (claim.state === "mismatch") {
      throw conflict("Idempotency-Key was already used with a different request");
    }
    if (claim.state === "pending") {
      throw conflict("The earlier request is still pending or requires reconciliation");
    }
    if (claim.state === "complete") return replayIdempotent<T>(claim.response);

    try {
      const value = await action();
      await deps.store.finishIdempotency(owner.id, operation, key, { ok: true, value });
      return value;
    } catch (error) {
      // Failures are never saved into the replay cache — a stored 503 would be
      // replayed forever, and a pinned 4xx would keep a valid Idempotency-Key
      // from ever succeeding after the underlying cause (e.g. a low balance)
      // was fixed. Release the claim so the retry re-executes; every
      // operation underneath carries its own idempotency/ownership guards.
      await deps.store.releaseIdempotency(owner.id, operation, key);
      throw error;
    }
  };

  app.setErrorHandler((error, _request, reply) => {
    if (error instanceof AppError) {
      return reply.status(error.statusCode).send({ error: { code: error.code, message: error.message, details: error.details } });
    }
    if (error instanceof z.ZodError) {
      return reply.status(400).send({ error: { code: "VALIDATION_ERROR", message: "Invalid request", details: error.issues } });
    }
    app.log.error({ err: error }, "request failed");
    return reply.status(500).send({ error: { code: "INTERNAL_ERROR", message: "Internal server error" } });
  });

  await registerServiceProxies(app, deps.config);

  app.get("/health", { schema: { security: [] } }, async (_request, reply) => {
    try {
      await deps.store.health();
      return { status: "ok", version: "4.0.0" };
    } catch {
      return reply.status(503).send({ status: "unhealthy", version: "4.0.0" });
    }
  });

  app.get("/v1/research/markets", { preHandler: authenticate("research"), schema: { tags: ["research"], querystring: marketQuery } }, (request) => {
    const query = marketQuery.parse(request.query);
    return deps.polymarket.searchMarkets(query.query, query.limit, query.state);
  });
  app.get("/v1/research/market", { preHandler: authenticate("research"), schema: { tags: ["research"], querystring: singleMarketQuery } }, (request) => {
    const query = singleMarketQuery.parse(request.query);
    return deps.polymarket.getMarket(query.identifier, query.identifierType);
  });
  app.get("/v1/research/order-books/:tokenId", { preHandler: authenticate("research"), schema: { tags: ["research"], params: tokenParams } }, (request) => {
    const { tokenId } = tokenParams.parse(request.params);
    return deps.polymarket.getOrderBook(tokenId);
  });
  app.get("/v1/research/price-history/:tokenId", { preHandler: authenticate("research"), schema: { tags: ["research"], params: tokenParams, querystring: historyQuery } }, (request) => {
    const { tokenId } = tokenParams.parse(request.params);
    const { interval } = historyQuery.parse(request.query);
    return deps.polymarket.getPriceHistory(tokenId, interval);
  });
  app.get("/v1/research/trades/:conditionId", { preHandler: authenticate("research"), schema: { tags: ["research"], params: conditionParams } }, (request) => {
    const { conditionId } = conditionParams.parse(request.params);
    return deps.polymarket.getRecentTrades(conditionId);
  });

  // Public market pages: unauthenticated read-only Polymarket data. Each route
  // caps its own rate so crawlers cannot exhaust the shared global limit.
  const publicRouteOptions = { config: { rateLimit: { max: 30, timeWindow: "1 minute" } } };
  app.get("/v1/public/markets", {
    ...publicRouteOptions,
    schema: { tags: ["public"], security: [], querystring: publicMarketListQuery },
  }, async (request, reply) => {
    const query = publicMarketListQuery.parse(request.query);
    reply.header("Cache-Control", cacheHeader(PUBLIC_CACHE_TTL_MS.index));
    return deps.publicMarkets.list(query);
  });
  app.get("/v1/public/markets/:slug", {
    ...publicRouteOptions,
    schema: { tags: ["public"], security: [], params: slugParams },
  }, async (request, reply) => {
    const { slug } = slugParams.parse(request.params);
    reply.header("Cache-Control", cacheHeader(PUBLIC_CACHE_TTL_MS.market));
    return deps.publicMarkets.detail(slug);
  });
  app.get("/v1/public/order-books/:tokenId", {
    ...publicRouteOptions,
    schema: { tags: ["public"], security: [], params: tokenParams },
  }, async (request, reply) => {
    const { tokenId } = tokenParams.parse(request.params);
    reply.header("Cache-Control", cacheHeader(PUBLIC_CACHE_TTL_MS.book));
    return deps.publicMarkets.book(tokenId);
  });
  app.get("/v1/public/price-history/:tokenId", {
    ...publicRouteOptions,
    schema: { tags: ["public"], security: [], params: tokenParams, querystring: publicHistoryQuery },
  }, async (request, reply) => {
    const { tokenId } = tokenParams.parse(request.params);
    const { interval } = publicHistoryQuery.parse(request.query);
    reply.header("Cache-Control", cacheHeader(publicPriceHistoryTtlMs(interval)));
    return deps.publicMarkets.history(tokenId, interval);
  });

  // Public track-record share pages: unauthenticated read-only paper trading
  // data, addressed by a secret opt-in token instead of the principal.
  app.get("/v1/public/track-records/:token", {
    ...publicRouteOptions,
    schema: { tags: ["public"], security: [], params: trackRecordParams, response: { 200: publicTrackRecordSchema } },
  }, async (request, reply) => {
    const { token } = trackRecordParams.parse(request.params);
    reply.header("Cache-Control", cacheHeader(TRACK_RECORD_CACHE_TTL_MS.detail));
    return deps.trackRecords.detail(token);
  });

  // Strategy templates are a static constant from contracts — no store, no
  // per-principal data, so this route just serializes the list.
  app.get("/v1/public/strategy-templates", {
    ...publicRouteOptions,
    schema: { tags: ["public"], security: [], response: { 200: strategyTemplateListSchema } },
  }, async (_request, reply) => {
    reply.header("Cache-Control", cacheHeader(60_000));
    return { items: strategyTemplates };
  });

  // Agent prediction capture: the chat agent records a falsifiable call on a
  // live market through its delegated research bearer. Responses are public-
  // safe by construction — no principal or thread fields exist on the record.
  app.post("/v1/agent/predictions", {
    preHandler: authenticate("research"),
    schema: {
      tags: ["agent"],
      body: agentPredictionRequestSchema,
      response: { 200: agentPredictionRecordSchema },
    },
  }, (request) => {
    const body = agentPredictionRequestSchema.parse(request.body);
    return runIdempotent(request, "agent-prediction.record", body, () =>
      deps.agentAccuracy.record(principal(request).id, body));
  });

  // Public accuracy scorecard: aggregate hit rates only — the aggregate query
  // never selects principal or thread columns.
  app.get("/v1/public/agent-accuracy", {
    ...publicRouteOptions,
    schema: { tags: ["public"], security: [], response: { 200: agentPredictionHitRateSchema } },
  }, async (_request, reply) => {
    reply.header("Cache-Control", cacheHeader(ACCURACY_CACHE_TTL_MS));
    return deps.agentAccuracy.snapshot();
  });

  app.get("/v1/paper/portfolio", {
    preHandler: authenticate("research"),
    schema: { tags: ["paper"], response: { 200: paperPortfolioSchema } },
  }, (request) => deps.paper.portfolio(principal(request)));
  app.post("/v1/paper/quotes", {
    preHandler: authenticate("research"),
    schema: { tags: ["paper"], body: paperQuoteRequestSchema, response: { 200: paperQuoteSchema } },
  }, (request) => deps.paper.quote(principal(request), paperQuoteRequestSchema.parse(request.body)));
  app.post("/v1/paper/orders", {
    preHandler: authenticate("research"),
    schema: { tags: ["paper"], body: paperOrderRequestSchema, response: { 200: paperOrderResponseSchema } },
  }, (request) => deps.paper.order(
    principal(request),
    paperOrderRequestSchema.parse(request.body),
    idempotency(request),
  ));
  app.post("/v1/paper/refresh", {
    preHandler: authenticate("research"),
    schema: { tags: ["paper"], response: { 200: paperPortfolioSchema } },
  }, (request) => deps.paper.refresh(principal(request)));
  app.get("/v1/paper/fills", {
    preHandler: authenticate("research"),
    schema: { tags: ["paper"], querystring: paperFillsQuery, response: { 200: paperFillsResponseSchema } },
  }, (request) => {
    const query = paperFillsQuery.parse(request.query);
    return deps.paper.fills(principal(request), query.limit, query.offset);
  });
  app.get("/v1/paper/strategy", {
    preHandler: authenticate("research"),
    schema: { tags: ["paper"], response: { 200: paperStrategySnapshotSchema } },
  }, (request) => deps.paperStrategy.snapshot(principal(request)));
  app.post("/v1/paper/strategy", {
    preHandler: authenticate("research"),
    schema: {
      tags: ["paper"],
      body: paperStrategyStartRequestSchema,
      response: { 200: paperStrategySnapshotSchema },
    },
  }, (request) => deps.paperStrategy.start(
    principal(request),
    paperStrategyStartRequestSchema.parse(request.body),
    idempotency(request),
  ));
  app.post("/v1/paper/strategy/stop", {
    preHandler: authenticate("research"),
    schema: { tags: ["paper"], response: { 200: paperStrategySnapshotSchema } },
  }, (request) => deps.paperStrategy.stop(principal(request)));

  app.get("/v1/paper/share", {
    preHandler: authenticate("research"),
    schema: { tags: ["paper"], response: { 200: paperShareStatusSchema } },
  }, (request) => deps.trackRecords.status(principal(request).id));

  app.post("/v1/paper/share", {
    preHandler: authenticate("research"),
    schema: {
      tags: ["paper"],
      body: paperShareManageRequestSchema,
      response: { 200: paperShareStatusSchema },
    },
  }, (request) => {
    const body = paperShareManageRequestSchema.parse(request.body);
    return body.rotate
      ? runIdempotent(request, "paper-share.rotate", body, () =>
          deps.trackRecords.rotate(principal(request).id))
      : runIdempotent(request, "paper-share.enable", body, () =>
          deps.trackRecords.enable(principal(request).id));
  });

  app.delete("/v1/paper/share", {
    preHandler: authenticate("research"),
    schema: { tags: ["paper"], response: { 200: paperShareStatusSchema } },
  }, (request) => runIdempotent(request, "paper-share.disable", {}, () =>
    deps.trackRecords.disable(principal(request).id)));

  app.get("/v1/alerts/channels", {
    preHandler: authenticate("research"),
    schema: { tags: ["alerts"], response: { 200: alertChannelListSchema } },
  }, (request) => deps.alerts.listChannels(principal(request).id));

  app.post("/v1/alerts/channels", {
    preHandler: authenticate("research"),
    schema: {
      tags: ["alerts"],
      body: alertCreateChannelRequestSchema,
      response: { 200: alertChannelSchema },
    },
  }, (request) => {
    const body = alertCreateChannelRequestSchema.parse(request.body);
    return runIdempotent(request, "alert-channel.create", body, () =>
      deps.alerts.createChannel(principal(request).id, body));
  });

  app.delete("/v1/alerts/channels/:channelId", {
    preHandler: authenticate("research"),
    schema: { tags: ["alerts"], params: alertChannelParams },
  }, async (request, reply) => {
    const { channelId } = alertChannelParams.parse(request.params);
    await runIdempotent(request, "alert-channel.delete", { channelId }, () =>
      deps.alerts.deleteChannel(principal(request).id, channelId));
    return reply.status(204).send();
  });

  app.post("/v1/alerts/channels/:channelId/test", {
    preHandler: authenticate("research"),
    schema: { tags: ["alerts"], params: alertChannelParams, response: { 200: alertTestSendResponseSchema } },
  }, (request) => {
    const { channelId } = alertChannelParams.parse(request.params);
    return runIdempotent(request, "alert-channel.test", { channelId }, () =>
      deps.alerts.testSend(principal(request).id, channelId));
  });

  app.get("/v1/alerts/deliveries", {
    preHandler: authenticate("research"),
    schema: { tags: ["alerts"], querystring: alertDeliveriesQuery, response: { 200: alertDeliveryListSchema } },
  }, (request) => {
    const query = alertDeliveriesQuery.parse(request.query);
    return deps.alerts.listDeliveries(principal(request).id, query.limit);
  });

  app.post("/v1/wallet-sessions/challenge", { preHandler: authenticate("trade"), schema: { tags: ["wallet"], body: walletChallengeRequestSchema } }, (request) => {
    const body = walletChallengeRequestSchema.parse(request.body);
    return runIdempotent(request, "wallet-session.challenge", body, () =>
      deps.trading.createChallenge(principal(request), body));
  });
  app.post("/v1/wallet-sessions", { preHandler: authenticate("trade"), schema: { tags: ["wallet"], body: walletSessionRequestSchema } }, (request) => {
    const body = walletSessionRequestSchema.parse(request.body);
    return runIdempotent(request, "wallet-session.create", body, () =>
      deps.trading.createSession(principal(request), body.challengeId, body.signature as Hex));
  });
  app.get("/v1/wallet-sessions/current", {
    preHandler: authenticate("trade"),
    schema: { tags: ["wallet"], response: { 200: walletSessionStatusSchema } },
  }, (request) => deps.trading.currentSession(principal(request)));
  app.delete("/v1/wallet-sessions/:sessionId", { preHandler: authenticate("trade"), schema: { tags: ["wallet"], params: sessionParams } }, async (request, reply) => {
    const { sessionId } = sessionParams.parse(request.params);
    await runIdempotent(request, "wallet-session.delete", { sessionId }, () =>
      deps.trading.revokeSession(principal(request), sessionId));
    return reply.status(204).send();
  });

  app.get("/v1/account/snapshot", { preHandler: authenticate("trade"), schema: { tags: ["account"] } }, (request) => deps.trading.account(principal(request)));
  app.get("/v1/account/overview", {
    preHandler: authenticate("trade"),
    schema: { tags: ["account"], response: { 200: accountOverviewSchema } },
  }, (request) => deps.trading.accountOverview(principal(request)));
  app.get("/v1/orders", { preHandler: authenticate("trade"), schema: { tags: ["account"] } }, async (request) => (await deps.trading.account(principal(request))).openOrders);
  app.get("/v1/trades", { preHandler: authenticate("trade"), schema: { tags: ["account"] } }, async (request) => (await deps.trading.account(principal(request))).trades);

  app.post("/v1/order-intents", { preHandler: authenticate("trade"), schema: { tags: ["orders"], body: createIntentRequestSchema } }, (request) => {
    const body = createIntentRequestSchema.parse(request.body);
    const key = idempotency(request);
    return runIdempotent(request, "order-intent.create", body, () =>
      deps.trading.createIntent(principal(request), body.sessionId, body.proposal, `single:${key}`));
  });
  app.post("/v1/order-intents/batch", { preHandler: authenticate("trade"), schema: { tags: ["orders"], body: batchIntentSchema } }, async (request) => {
    const body = batchIntentSchema.parse(request.body);
    const key = idempotency(request);
    return runIdempotent(request, "order-intent.batch-create", body, async () => {
      const intents = [];
      for (const [index, proposal] of body.proposals.entries()) {
        intents.push(await deps.trading.createIntent(principal(request), body.sessionId, proposal, `batch:${key}:${index}`));
      }
      return { intents };
    });
  });
  app.post("/v1/order-intents/:intentId/submit", { preHandler: authenticate("trade"), schema: { tags: ["orders"], params: intentParams, body: submitIntentRequestSchema } }, (request) => {
    const { intentId } = intentParams.parse(request.params);
    const body = submitIntentRequestSchema.parse(request.body);
    return runIdempotent(request, "order-intent.submit", { intentId, ...body }, () =>
      deps.trading.submitIntent(principal(request), intentId, body.signature as Hex));
  });
  app.post("/v1/order-intents/batch/submit", { preHandler: authenticate("trade"), schema: { tags: ["orders"], body: batchSubmitSchema } }, async (request) => {
    const body = batchSubmitSchema.parse(request.body);
    return runIdempotent(request, "order-intent.batch-submit", body, async () => {
      const results = [];
      for (const item of body.items) {
        results.push({ intentId: item.intentId, result: await deps.trading.submitIntent(principal(request), item.intentId, item.signature as Hex) });
      }
      return { results };
    });
  });
  app.post("/v1/cancellations", { preHandler: authenticate("trade"), schema: { tags: ["orders"], body: cancelRequestSchema } }, (request) => {
    const body = cancelRequestSchema.parse(request.body);
    return runIdempotent(request, "cancellation.create", body, () =>
      deps.trading.cancel(principal(request), body.sessionId, body.selector));
  });

  app.addHook("onClose", async () => {
    await deps.predictionGrader?.close();
    await deps.alertsRunner?.close();
    await deps.paperStrategyRunner?.close();
    await deps.store.close();
  });
  return app;
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .sort(([left], [right]) => left.localeCompare(right));
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

function replayIdempotent<T>(value: unknown): T {
  if (!value || typeof value !== "object") throw new AppError(500, "IDEMPOTENCY_CORRUPT", "Stored idempotency result is invalid");
  const result = value as Record<string, unknown>;
  if (result.ok === true) return result.value as T;
  throw new AppError(
    typeof result.status === "number" ? result.status : 500,
    typeof result.code === "string" ? result.code : "INTERNAL_ERROR",
    typeof result.message === "string" ? result.message : "Stored request failed",
    result.details,
  );
}
