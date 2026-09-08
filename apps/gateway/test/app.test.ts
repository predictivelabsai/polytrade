import type { FastifyInstance } from "fastify";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { createHash } from "node:crypto";
import type { AddressInfo } from "node:net";
import { describe, expect, it } from "vitest";
import { strategyTemplateListSchema } from "@polytrade/contracts";

import { agentPredictionHitRateSchema } from "@polytrade/contracts";

import { AlertSender } from "../src/alert-sender.js";
import { AlertService } from "../src/alert-service.js";
import { buildApp } from "../src/app.js";
import { parseConfig } from "../src/config.js";
import { CredentialCipher } from "../src/crypto.js";
import { AppError, unauthorized } from "../src/errors.js";
import { PublicAgentAccuracyService } from "../src/agent-accuracy.js";
import { PublicMarketService } from "../src/public-market.js";
import { TtlCache } from "../src/public-cache.js";
import { PublicTrackRecordService } from "../src/track-record.js";
import type { Principal } from "../src/types.js";
import { FakePolymarket, MemoryAgentPredictionStore, MemoryAlertStore, MemoryTrackRecordStore, MemoryTradingStore, publicMarketSummaryFixture, trackRecordSnapshotFixture } from "./fakes.js";

const principal: Principal = {
  id: "assethero:user-1",
  issuer: "assethero",
  subject: "user-1",
  scopes: new Set(["research", "trade"]),
};

async function requestBody(request: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function startUpstream(
  handler: (request: IncomingMessage, response: ServerResponse) => Promise<void> | void,
) {
  const server = createServer((request, response) => {
    void Promise.resolve(handler(request, response)).catch(() => {
      if (!response.headersSent) response.statusCode = 500;
      response.end();
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
  const address = server.address() as AddressInfo;
  return {
    origin: `http://127.0.0.1:${address.port}`,
    close: async () => {
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => {
        server.close((error) => error ? reject(error) : resolve());
      });
    },
  };
}

function config(
  trustedProxies = "127.0.0.1/32,::1/128",
  overrides: NodeJS.ProcessEnv = {},
) {
  return parseConfig({
    NODE_ENV: "test",
    DATABASE_URL: "postgresql://test:test@db.example.test:5432/polytrade?sslmode=require",
    CREDENTIALS_KEK_BASE64: Buffer.alloc(32, 9).toString("base64"),
    CORS_ORIGINS: "https://app.assethero.test,https://polytrade.test",
    TRUSTED_PROXIES: trustedProxies,
    CLERK_ISSUER: "https://clerk.test",
    CLERK_JWKS_URL: "https://clerk.test/jwks",
    ...overrides,
  });
}

async function setup(trustedProxies?: string, configOverrides: NodeJS.ProcessEnv = {}) {
  const store = new MemoryTradingStore();
  const polymarket = new FakePolymarket();
  let challengeCalls = 0;
  const verifiedScopes: string[] = [];
  const createdIntents: unknown[] = [];
  const submittedIntents: Array<{ intentId: string; signature: string }> = [];
  const trading = {
    createChallenge: async () => {
      challengeCalls += 1;
      return {
        challengeId: "00000000-0000-4000-8000-000000000010",
        expiresAt: "2026-08-03T00:05:00.000Z",
        typedData: { domain: {}, types: {}, primaryType: "Challenge", message: {} },
      };
    },
    createSession: async () => ({}),
    currentSession: async () => ({
      sessionId: "00000000-0000-4000-8000-000000000001",
      walletAddress: "0x0000000000000000000000000000000000000001",
      signatureType: 0,
      idleExpiresAt: "2026-08-03T00:30:00.000Z",
      expiresAt: "2026-08-03T08:00:00.000Z",
    }),
    revokeSession: async () => {},
    account: async () => ({ positions: [], openOrders: [], trades: [] }),
    accountOverview: async () => ({
      walletAddress: "0x0000000000000000000000000000000000000001",
      positions: [],
      openOrders: [],
      fills: [],
      observedAt: "2026-08-03T00:00:00.000Z",
    }),
    createIntent: async (
      _principal: Principal,
      _sessionId: string,
      proposal: unknown,
    ) => {
      createdIntents.push(proposal);
      return { intentId: `00000000-0000-4000-8000-${String(createdIntents.length).padStart(12, "0")}` };
    },
    submitIntent: async (
      _principal: Principal,
      intentId: string,
      signature: string,
    ) => {
      submittedIntents.push({ intentId, signature });
      return { success: true, orderID: `order-${submittedIntents.length}` };
    },
    cancel: async () => ({}),
  };
  const emptyPaperPortfolio = {
    initialCash: "10000.000000",
    cash: "10000.000000",
    positionsValue: "0.000000",
    equity: "10000.000000",
    realizedPnl: "0.000000",
    unrealizedPnl: "0.000000",
    totalPnl: "0.000000",
    totalFees: "0.000000",
    positions: [],
    warnings: [],
    observedAt: "2026-08-03T00:00:00.000Z",
  };
  const paper = {
    portfolio: async () => emptyPaperPortfolio,
    quote: async () => ({
      conditionId: "0xcondition",
      tokenId: "123",
      marketQuestion: "Will paper routes pass?",
      outcome: "Yes",
      side: "BUY",
      shares: "10.000000",
      averagePrice: "0.400000",
      limitPrice: "0.400000",
      grossNotional: "4.000000",
      feeRate: "0.000000",
      fee: "0.00000",
      cashEffect: "-4.000000",
      observedAt: "2026-08-03T00:00:00.000Z",
    }),
    order: async () => ({
      fill: {
        fillId: "55555555-5555-4555-8555-555555555555",
        kind: "BUY",
        conditionId: "0xcondition",
        tokenId: "123",
        marketQuestion: "Will paper routes pass?",
        outcome: "Yes",
        shares: "10.000000",
        averagePrice: "0.400000",
        grossNotional: "4.000000",
        feeRate: "0.000000",
        fee: "0.00000",
        cashEffect: "-4.000000",
        realizedPnl: "0.000000",
        observedAt: "2026-08-03T00:00:00.000Z",
        createdAt: "2026-08-03T00:00:00.000Z",
      },
      portfolio: emptyPaperPortfolio,
    }),
    refresh: async () => emptyPaperPortfolio,
    fills: async (_principal: Principal, limit: number, offset: number) => ({ items: [], total: 0, limit, offset }),
  };
  const emptyPaperStrategy = { strategy: null, events: [] };
  const paperStrategy = {
    snapshot: async () => emptyPaperStrategy,
    start: async () => emptyPaperStrategy,
    stop: async () => emptyPaperStrategy,
  };
  const alertStore = new MemoryAlertStore();
  const alertRequests: Array<{ url: string; body: string }> = [];
  const alertSender = new AlertSender(config(trustedProxies, configOverrides), async (url, init) => {
    alertRequests.push({ url: String(url), body: String(init?.body ?? "") });
    return new Response(null, { status: 204 });
  });
  const alerts = new AlertService(
    alertStore,
    new CredentialCipher(config(trustedProxies, configOverrides).credentialKey),
    alertSender,
  );
  const trackRecordStore = new MemoryTrackRecordStore();
  trackRecordStore.snapshots[principal.id] = trackRecordSnapshotFixture();
  const trackRecords = new PublicTrackRecordService(trackRecordStore, new TtlCache());
  const predictionStore = new MemoryAgentPredictionStore();
  const agentAccuracy = new PublicAgentAccuracyService(predictionStore, new TtlCache());
  const app = await buildApp({
    config: config(trustedProxies, configOverrides),
    verifier: { verifyAuthorization: async (header: string | undefined, scope: string) => {
      // Mimic JwtVerifier.verifyAuthorization: reject missing Bearer headers.
      if (!header?.startsWith("Bearer ")) throw unauthorized("Bearer token required");
      verifiedScopes.push(scope);
      return principal;
    } } as never,
    store,
    polymarket,
    trading: trading as never,
    paper: paper as never,
    paperStrategy: paperStrategy as never,
    alerts,
    publicMarkets: new PublicMarketService(polymarket, new TtlCache()),
    trackRecords,
    agentAccuracy,
  });
  await app.ready();
  return {
    app,
    store,
    trading,
    challengeCalls: () => challengeCalls,
    createdIntents,
    submittedIntents,
    verifiedScopes,
    alertStore,
    alertRequests,
    trackRecordStore,
    predictionStore,
    polymarket,
  };
}

describe("gateway HTTP boundary", () => {
  it("allows only exact configured origins", async () => {
    const { app } = await setup();
    const allowed = await app.inject({
      method: "GET",
      url: "/v1/research/markets?query=election",
      headers: { authorization: "Bearer token", origin: "https://app.assethero.test" },
    });
    expect(allowed.statusCode).toBe(200);
    expect(allowed.headers["access-control-allow-origin"]).toBe("https://app.assethero.test");

    const denied = await app.inject({
      method: "GET",
      url: "/v1/research/markets?query=election",
      headers: { authorization: "Bearer token", origin: "https://evil.test" },
    });
    expect(denied.statusCode).toBe(403);
    expect(denied.headers["access-control-allow-origin"]).toBeUndefined();
    await app.close();
  });

  it("serves public market reads without authentication and keeps research routes guarded", async () => {
    const { app, polymarket } = await setup();
    polymarket.publicActiveMarkets = [
      publicMarketSummaryFixture(),
      publicMarketSummaryFixture({ id: "market-2", conditionId: "0xcondition-2", slug: "other", question: "Other market?" }),
    ];
    polymarket.publicMarketDetail = {
      ...publicMarketSummaryFixture(),
      description: "Fed policy details",
      archived: false,
      restricted: false,
      enableOrderBook: true,
      minimumOrderSize: "5",
      minimumTickSize: "0.01",
      startDate: null,
      createdAt: null,
      closedTime: null,
      icon: "https://cdn.example.test/icon.png",
      volume24hr: "1721754.1072",
    };
    polymarket.paperOrderBooks.set("123", {
      bids: [{ price: "0.42", size: "5" }],
      asks: [{ price: "0.46", size: "5" }],
      minimumOrderSize: "5",
      tickSize: "0.01",
      negativeRisk: false,
      lastTradePrice: "0.44",
      observedAt: "2026-08-03T00:00:00.000Z",
    });

    const browse = await app.inject({ method: "GET", url: "/v1/public/markets" });
    expect(browse.statusCode).toBe(200);
    expect(browse.headers["cache-control"]).toContain("public, max-age=30");
    expect(browse.json().markets.slice(0, 2).map((market: { slug: string }) => market.slug))
      .toEqual(["fed-rates-september", "other"]);

    const browseAgain = await app.inject({ method: "GET", url: "/v1/public/markets" });
    expect(browseAgain.statusCode).toBe(200);
    expect(polymarket.requestCounts.list).toBe(1);

    const detail = await app.inject({ method: "GET", url: "/v1/public/markets/fed-rates-september" });
    expect(detail.statusCode).toBe(200);
    expect(detail.json().quotes).toEqual([
      { outcome: "Yes", tokenId: "123", price: "0.44", bestBid: "0.42", bestAsk: "0.46", source: "order-book" },
      { outcome: "No", tokenId: "456", price: "0.565", bestBid: null, bestAsk: null, source: "order-book" },
    ]);

    const missing = await app.inject({ method: "GET", url: "/v1/public/markets/does-not-exist" });
    const missingAgain = await app.inject({ method: "GET", url: "/v1/public/markets/does-not-exist" });
    expect(missing.statusCode).toBe(404);
    expect(missingAgain.statusCode).toBe(404);
    expect(polymarket.requestCounts.detail).toBe(2);

    const guarded = await app.inject({ method: "GET", url: "/v1/research/markets?query=election" });
    expect(guarded.statusCode).toBe(401);
    await app.close();
  });

  it("serves an opt-in track record publicly and keeps share management guarded", async () => {
    const { app, trackRecordStore } = await setup();
    const link = await trackRecordStore.enable(principal.id);
    const token = link.token!;

    const before = trackRecordStore.resolveCount;
    const record = await app.inject({ method: "GET", url: `/v1/public/track-records/${token}` });
    expect(record.statusCode).toBe(200);
    expect(record.headers["cache-control"]).toContain("public, max-age=15");
    expect(record.json().profile).toEqual({ displayName: "Paper account", startedAt: "2026-08-01T00:00:00.000Z" });
    expect(record.json().stats.equity).toBe("9505.200000");
    // The payload is identity-free: no principal id may leak.
    expect(record.body).not.toContain(principal.id);

    // Repeat visit is served from the in-memory cache, not the store.
    await app.inject({ method: "GET", url: `/v1/public/track-records/${token}` });
    expect(trackRecordStore.snapshotCount).toBe(1);
    expect(trackRecordStore.resolveCount).toBe(before + 1);

    const missing = await app.inject({ method: "GET", url: `/v1/public/track-records/${"x".repeat(32)}` });
    const missingAgain = await app.inject({ method: "GET", url: `/v1/public/track-records/${"x".repeat(32)}` });
    expect(missing.statusCode).toBe(404);
    expect(missingAgain.statusCode).toBe(404);

    const disabled = await trackRecordStore.disable(principal.id);
    expect(disabled.enabled).toBe(false);
    expect(await trackRecordStore.resolvePrincipal(token)).toBeNull();

    const manageGuarded = await app.inject({ method: "GET", url: "/v1/paper/share" });
    expect(manageGuarded.statusCode).toBe(401);
    const createGuarded = await app.inject({ method: "POST", url: "/v1/paper/share", payload: { rotate: false } });
    expect(createGuarded.statusCode).toBe(401);
    const deleteGuarded = await app.inject({ method: "DELETE", url: "/v1/paper/share" });
    expect(deleteGuarded.statusCode).toBe(401);
    await app.close();
  });

  it("serves the strategy template list publicly without authentication", async () => {
    const { app } = await setup();

    const response = await app.inject({ method: "GET", url: "/v1/public/strategy-templates" });
    expect(response.statusCode).toBe(200);
    expect(response.headers["cache-control"]).toContain("max-age=");
    const parsed = strategyTemplateListSchema.parse(response.json());
    expect(parsed.items.length).toBeGreaterThanOrEqual(5);
    expect(new Set(parsed.items.map((template) => template.id)).size).toBe(parsed.items.length);
    // The payload is a static constant: no per-principal data can appear.
    expect(response.body).not.toContain(principal.id);
    await app.close();
  });

  it("creates, rotates, and disables a share link for the authenticated owner", async () => {
    const { app, trackRecordStore } = await setup();

    const created = await app.inject({
      method: "POST",
      url: "/v1/paper/share",
      headers: { authorization: "Bearer token", "idempotency-key": "share-create-1" },
      payload: { rotate: false },
    });
    expect(created.statusCode).toBe(200);
    expect(created.json().token).toMatch(/^[A-Za-z0-9_-]{32,64}$/);
    expect(created.json().enabled).toBe(true);

    // GET needs no Idempotency-Key and reports the same link.
    const status = await app.inject({
      method: "GET",
      url: "/v1/paper/share",
      headers: { authorization: "Bearer token" },
    });
    expect(status.statusCode).toBe(200);
    expect(status.json().token).toBe(created.json().token);

    const rotated = await app.inject({
      method: "POST",
      url: "/v1/paper/share",
      headers: { authorization: "Bearer token", "idempotency-key": "share-rotate-1" },
      payload: { rotate: true },
    });
    expect(rotated.statusCode).toBe(200);
    expect(rotated.json().token).not.toBe(created.json().token);

    const replay = await app.inject({
      method: "POST",
      url: "/v1/paper/share",
      headers: { authorization: "Bearer token", "idempotency-key": "share-rotate-1" },
      payload: { rotate: true },
    });
    expect(replay.statusCode).toBe(200);
    expect(replay.json().token).toBe(rotated.json().token);

    const disabled = await app.inject({
      method: "DELETE",
      url: "/v1/paper/share",
      headers: { authorization: "Bearer token", "idempotency-key": "share-disable-1" },
    });
    expect(disabled.statusCode).toBe(200);
    expect(disabled.json().enabled).toBe(false);
    expect(disabled.json().token).toBe(rotated.json().token);
    expect(trackRecordStore.links.get(principal.id)?.enabled).toBe(false);
    await app.close();
  });

  it("honors forwarded client IPs only from configured proxies", async () => {
    const remaining = async (app: FastifyInstance, forwardedFor: string) => {
      const response = await app.inject({
        method: "GET",
        url: "/health",
        headers: { "x-forwarded-for": forwardedFor },
      });
      return Number(response.headers["x-ratelimit-remaining"]);
    };

    const trusted = await setup();
    const trustedFirst = await remaining(trusted.app, "203.0.113.8");
    expect(trustedFirst).toBeGreaterThan(0);
    expect(await remaining(trusted.app, "203.0.113.9")).toBe(trustedFirst);
    await trusted.app.close();

    const untrusted = await setup("10.0.0.0/8");
    const untrustedFirst = await remaining(untrusted.app, "203.0.113.8");
    expect(untrustedFirst).toBe(trustedFirst);
    expect(await remaining(untrusted.app, "203.0.113.9")).toBe(untrustedFirst - 1);
    await untrusted.app.close();
  });

  it("deduplicates writes and rejects key reuse with changed payload", async () => {
    const { app, challengeCalls } = await setup();
    const headers = {
      authorization: "Bearer token",
      "content-type": "application/json",
      "idempotency-key": "challenge-key-1",
    };
    const body = { walletAddress: "0x0000000000000000000000000000000000000001", signatureType: 0 };
    const first = await app.inject({ method: "POST", url: "/v1/wallet-sessions/challenge", headers, payload: body });
    const replay = await app.inject({ method: "POST", url: "/v1/wallet-sessions/challenge", headers, payload: body });
    expect(first.statusCode).toBe(200);
    expect(replay.json()).toEqual(first.json());
    expect(challengeCalls()).toBe(1);

    const changed = await app.inject({
      method: "POST",
      url: "/v1/wallet-sessions/challenge",
      headers,
      payload: { ...body, walletAddress: "0x0000000000000000000000000000000000000002" },
    });
    expect(changed.statusCode).toBe(409);
    await app.close();
  });

  it("releases the idempotency claim when a request fails so the retry re-executes", async () => {
    const { app, trading, challengeCalls } = await setup();
    const headers = {
      authorization: "Bearer token",
      "content-type": "application/json",
      "idempotency-key": "retry-after-failure-key-1",
    };
    const body = { walletAddress: "0x0000000000000000000000000000000000000001", signatureType: 0 };
    const gate = { enabled: true };
    const original = trading.createChallenge.bind(trading);
    trading.createChallenge = async (...args: Parameters<typeof trading.createChallenge>) => {
      if (gate.enabled) throw new AppError(503, "UPSTREAM_UNAVAILABLE", "Polymarket is unavailable");
      return original(...args);
    };

    const failed = await app.inject({ method: "POST", url: "/v1/wallet-sessions/challenge", headers, payload: body });
    expect(failed.statusCode).toBe(503);
    expect(challengeCalls()).toBe(0);

    gate.enabled = false;
    const retry = await app.inject({ method: "POST", url: "/v1/wallet-sessions/challenge", headers, payload: body });
    expect(retry.statusCode).toBe(200);
    expect(challengeCalls()).toBe(1);
    await app.close();
  });

  it("re-claims idempotency keys orphaned by a crash after the stale window", async () => {
    const { app, store, challengeCalls } = await setup();
    const headers = {
      authorization: "Bearer token",
      "content-type": "application/json",
      "idempotency-key": "orphaned-claim-key-1",
    };
    const body = { walletAddress: "0x0000000000000000000000000000000000000001", signatureType: 0 };
    const requestHash = createHash("sha256").update('{"signatureType":0,"walletAddress":"0x0000000000000000000000000000000000000001"}').digest("hex");

    // A fresh orphaned claim still guards the in-flight request.
    store.idempotency.set("assethero:user-1:wallet-session.challenge:orphaned-claim-key-1", {
      hash: requestHash, createdAt: new Date(), response: undefined,
    });
    const pending = await app.inject({ method: "POST", url: "/v1/wallet-sessions/challenge", headers, payload: body });
    expect(pending.statusCode).toBe(409);
    expect(challengeCalls()).toBe(0);

    // Past the stale window the crashed claim no longer blocks the key forever.
    store.idempotency.set("assethero:user-1:wallet-session.challenge:orphaned-claim-key-1", {
      hash: requestHash, createdAt: new Date(Date.now() - 6 * 60_000), response: undefined,
    });
    const recovered = await app.inject({ method: "POST", url: "/v1/wallet-sessions/challenge", headers, payload: body });
    expect(recovered.statusCode).toBe(200);
    expect(challengeCalls()).toBe(1);
    await app.close();
  });

  it("re-claims a previously pinned failure response once it goes stale", async () => {
    const { app, store, challengeCalls } = await setup();
    const headers = {
      authorization: "Bearer token",
      "content-type": "application/json",
      "idempotency-key": "legacy-pinned-failure-key-1",
    };
    const body = { walletAddress: "0x0000000000000000000000000000000000000001", signatureType: 0 };
    const requestHash = createHash("sha256").update('{"signatureType":0,"walletAddress":"0x0000000000000000000000000000000000000001"}').digest("hex");
    store.idempotency.set("assethero:user-1:wallet-session.challenge:legacy-pinned-failure-key-1", {
      hash: requestHash, createdAt: new Date(Date.now() - 6 * 60_000),
      response: { ok: false, status: 503, code: "UPSTREAM_UNAVAILABLE", message: "stored long ago" },
    });

    const recovered = await app.inject({ method: "POST", url: "/v1/wallet-sessions/challenge", headers, payload: body });
    expect(recovered.statusCode).toBe(200);
    expect(challengeCalls()).toBe(1);
    await app.close();
  });

  it("publishes versioned request schemas in OpenAPI", async () => {
    const { app } = await setup();
    const specification = app.swagger() as { paths: Record<string, Record<string, { requestBody?: unknown }>> };
    expect(specification.paths["/v1/order-intents"]?.post?.requestBody).toBeDefined();
    expect(specification.paths["/v1/cancellations"]?.post?.requestBody).toBeDefined();
    expect(specification.paths["/v1/wallet-sessions/current"]?.get).toBeDefined();
    expect(specification.paths["/v1/account/overview"]?.get).toBeDefined();
    expect(specification.paths["/v1/paper/quotes"]?.post?.requestBody).toBeDefined();
    expect(specification.paths["/v1/paper/orders"]?.post?.requestBody).toBeDefined();
    expect(specification.paths["/v1/paper/portfolio"]?.get).toBeDefined();
    expect(specification.paths["/v1/paper/strategy"]?.get).toBeDefined();
    expect(specification.paths["/v1/paper/strategy"]?.post?.requestBody).toBeDefined();
    expect(specification.paths["/v1/paper/strategy/stop"]?.post).toBeDefined();
    await app.close();
  });

  it("keeps paper routes on research auth without invoking wallet flows", async () => {
    const { app, challengeCalls, verifiedScopes } = await setup();
    const headers = { authorization: "Bearer token", "content-type": "application/json" };

    const portfolio = await app.inject({ method: "GET", url: "/v1/paper/portfolio", headers });
    const quote = await app.inject({
      method: "POST",
      url: "/v1/paper/quotes",
      headers,
      payload: { conditionId: "0xcondition", tokenId: "123", side: "BUY", shares: "10" },
    });
    const order = await app.inject({
      method: "POST",
      url: "/v1/paper/orders",
      headers: { ...headers, "idempotency-key": "paper-order-key-1" },
      payload: { conditionId: "0xcondition", tokenId: "123", side: "BUY", shares: "10", limitPrice: "0.4" },
    });
    const refresh = await app.inject({
      method: "POST",
      url: "/v1/paper/refresh",
      headers: { authorization: "Bearer token" },
    });
    const fills = await app.inject({ method: "GET", url: "/v1/paper/fills?limit=10&offset=0", headers });
    const strategy = await app.inject({ method: "GET", url: "/v1/paper/strategy", headers });
    const strategyStart = await app.inject({
      method: "POST",
      url: "/v1/paper/strategy",
      headers: { ...headers, "idempotency-key": "paper-strategy-key-1" },
      payload: {
        conditionId: "0xcondition",
        tokenId: "123",
        entryPrice: "0.35",
        exitPrice: "0.65",
        sharesPerOrder: "10",
        maxPosition: "50",
        intervalSeconds: 15,
      },
    });
    const strategyStop = await app.inject({
      method: "POST",
      url: "/v1/paper/strategy/stop",
      headers: { authorization: "Bearer token" },
    });

    expect([portfolio, quote, order, refresh, fills, strategy, strategyStart, strategyStop]
      .map((response) => response.statusCode)).toEqual([200, 200, 200, 200, 200, 200, 200, 200]);
    expect(verifiedScopes).toEqual(["research", "research", "research", "research", "research", "research", "research", "research"]);
    expect(challengeCalls()).toBe(0);
    await app.close();
  });

  it("serves stable wallet-session and account-overview DTOs", async () => {
    const { app } = await setup();
    const headers = { authorization: "Bearer token" };

    const session = await app.inject({ method: "GET", url: "/v1/wallet-sessions/current", headers });
    expect(session.statusCode).toBe(200);
    expect(session.json()).toMatchObject({
      sessionId: "00000000-0000-4000-8000-000000000001",
      signatureType: 0,
    });
    expect(session.json()).not.toHaveProperty("encryptedCredentials");

    const overview = await app.inject({ method: "GET", url: "/v1/account/overview", headers });
    expect(overview.statusCode).toBe(200);
    expect(overview.json()).toMatchObject({ positions: [], openOrders: [], fills: [] });
    await app.close();
  });

  it("requires and forwards a separate wallet signature for every batch member", async () => {
    const { app, createdIntents, submittedIntents } = await setup();
    const headers = {
      authorization: "Bearer token",
      "content-type": "application/json",
      "idempotency-key": "batch-order-key-1",
    };
    const base = {
      action: "create",
      tokenId: "123",
      marketId: "0xcondition",
      marketQuestion: "Will this batch pass?",
      outcome: "Yes",
      side: "BUY",
      rationale: "test",
      observedAt: "2026-08-03T00:00:00.000Z",
    };
    const created = await app.inject({
      method: "POST",
      url: "/v1/order-intents/batch",
      headers,
      payload: {
        sessionId: "00000000-0000-4000-8000-000000000001",
        proposals: [
          { ...base, execution: "GTC", price: "0.4", size: "10", postOnly: true },
          { ...base, tokenId: "456", outcome: "No", execution: "FAK", amount: "4", limitPrice: "0.5", postOnly: false },
        ],
      },
    });
    expect(created.statusCode).toBe(200);
    expect(createdIntents).toHaveLength(2);

    const submitted = await app.inject({
      method: "POST",
      url: "/v1/order-intents/batch/submit",
      headers: { ...headers, "idempotency-key": "batch-submit-key-1" },
      payload: {
        items: [
          { intentId: "00000000-0000-4000-8000-000000000001", signature: "0x11" },
          { intentId: "00000000-0000-4000-8000-000000000002", signature: "0x22" },
        ],
      },
    });
    expect(submitted.statusCode).toBe(200);
    expect(submittedIntents).toEqual([
      { intentId: "00000000-0000-4000-8000-000000000001", signature: "0x11" },
      { intentId: "00000000-0000-4000-8000-000000000002", signature: "0x22" },
    ]);
    await app.close();
  });

  it("proxies agent and backtest requests without changing their HTTP contracts", async () => {
    const observed: Array<{
      body: string;
      headers: IncomingMessage["headers"];
      method: string | undefined;
      url: string | undefined;
    }> = [];
    const handler = async (request: IncomingMessage, response: ServerResponse) => {
      observed.push({
        body: await requestBody(request),
        headers: request.headers,
        method: request.method,
        url: request.url,
      });
      const url = new URL(request.url ?? "/", "http://upstream.test");
      const requestedStatus = Number(url.searchParams.get("status") ?? 0);
      response.setHeader("Access-Control-Allow-Origin", "https://wrong-upstream-origin.test");
      if (request.method === "DELETE") {
        response.statusCode = 204;
        response.end();
        return;
      }
      response.statusCode = requestedStatus || (request.method === "POST" ? 202 : 200);
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ method: request.method, path: request.url }));
    };
    const agent = await startUpstream(handler);
    const backtest = await startUpstream(handler);
    const { app } = await setup(undefined, {
      AGENT_UPSTREAM_URL: agent.origin,
      BACKTEST_UPSTREAM_URL: backtest.origin,
    });

    try {
      const agentResponse = await app.inject({
        method: "POST",
        url: "/v1/agent/threads?status=418&offset=10",
        headers: {
          accept: "application/json",
          authorization: "Bearer agent-token",
          "content-type": "application/json",
          "idempotency-key": "agent-request-key",
          origin: "https://polytrade.test",
        },
        payload: { message: "hello" },
      });
      expect(agentResponse.statusCode).toBe(418);
      expect(agentResponse.json()).toEqual({
        method: "POST",
        path: "/v1/agent/threads?status=418&offset=10",
      });
      expect(agentResponse.headers["access-control-allow-origin"]).toBe("https://polytrade.test");
      expect(observed[0]).toMatchObject({
        body: '{"message":"hello"}',
        method: "POST",
        url: "/v1/agent/threads?status=418&offset=10",
      });
      expect(observed[0]?.headers).toMatchObject({
        accept: "application/json",
        authorization: "Bearer agent-token",
        "content-type": "application/json",
        "idempotency-key": "agent-request-key",
      });
      expect(observed[0]?.headers.origin).toBeUndefined();

      const requestsBeforePreflight = observed.length;
      const preflight = await app.inject({
        method: "OPTIONS",
        url: "/v1/backtests",
        headers: {
          origin: "https://polytrade.test",
          "access-control-request-method": "POST",
          "access-control-request-headers": "authorization,idempotency-key,content-type",
        },
      });
      expect(preflight.statusCode).toBe(204);
      expect(preflight.headers["access-control-allow-origin"]).toBe("https://polytrade.test");
      expect(preflight.headers["access-control-allow-methods"]).toContain("POST");
      expect(observed).toHaveLength(requestsBeforePreflight);

      const created = await app.inject({
        method: "POST",
        url: "/v1/backtests?source=web",
        headers: {
          authorization: "Bearer backtest-token",
          "content-type": "application/json",
          "idempotency-key": "backtest-request-key",
        },
        payload: { marketId: "market-1" },
      });
      expect(created.statusCode).toBe(202);
      expect(created.json()).toEqual({ method: "POST", path: "/v1/backtests?source=web" });
      expect(observed[1]).toMatchObject({
        body: '{"marketId":"market-1"}',
        method: "POST",
        url: "/v1/backtests?source=web",
      });
      expect(observed[1]?.headers).toMatchObject({
        authorization: "Bearer backtest-token",
        "idempotency-key": "backtest-request-key",
      });

      const failed = await app.inject({
        method: "GET",
        url: "/v1/backtests/run-1?status=503",
      });
      expect(failed.statusCode).toBe(503);
      expect(failed.json()).toEqual({
        method: "GET",
        path: "/v1/backtests/run-1?status=503",
      });

      const deleted = await app.inject({ method: "DELETE", url: "/v1/backtests/run-1" });
      expect(deleted.statusCode).toBe(204);
      expect(deleted.payload).toBe("");
    } finally {
      await app.close();
      await Promise.all([agent.close(), backtest.close()]);
    }
  });

  it("returns sanitized gateway errors when an upstream is unavailable or times out", async () => {
    const stalled = await startUpstream(() => undefined);
    const unavailable = await setup(undefined, {
      AGENT_UPSTREAM_URL: "http://127.0.0.1:1",
      BACKTEST_UPSTREAM_URL: stalled.origin,
      UPSTREAM_PROXY_TIMEOUT_MS: "100",
    });

    try {
      const missing = await unavailable.app.inject({ method: "GET", url: "/v1/agent/threads" });
      expect(missing.statusCode).toBe(502);
      expect(missing.json()).toEqual({
        error: {
          code: "UPSTREAM_UNAVAILABLE",
          message: "Agent service is unavailable",
        },
      });
      expect(missing.payload).not.toContain("127.0.0.1");

      const timedOut = await unavailable.app.inject({ method: "GET", url: "/v1/backtests" });
      expect(timedOut.statusCode).toBe(504);
      expect(timedOut.json()).toEqual({
        error: {
          code: "UPSTREAM_TIMEOUT",
          message: "Backtest service timed out",
        },
      });
    } finally {
      await unavailable.app.close();
      await stalled.close();
    }
  });

  it("streams agent SSE events through the gateway without buffering", async () => {
    let releaseStream: () => void = () => undefined;
    const continueStream = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    const agent = await startUpstream(async (request, response) => {
      await requestBody(request);
      response.writeHead(200, {
        "Cache-Control": "no-store",
        "Content-Type": "text/event-stream",
        "X-Accel-Buffering": "no",
      });
      response.flushHeaders();
      response.write("event: run.started\ndata: {}\n\n");
      await continueStream;
      response.end("event: run.completed\ndata: {}\n\n");
    });
    const { app } = await setup(undefined, {
      AGENT_UPSTREAM_URL: agent.origin,
      UPSTREAM_PROXY_TIMEOUT_MS: "1000",
    });

    try {
      const gatewayOrigin = await app.listen({ host: "127.0.0.1", port: 0 });
      const response = await fetch(`${gatewayOrigin}/v1/agent/threads/thread-1/runs/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Origin: "https://polytrade.test",
        },
        body: JSON.stringify({ message: "stream" }),
      });
      expect(response.status).toBe(200);
      expect(response.headers.get("content-type")).toContain("text/event-stream");
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(response.headers.get("x-accel-buffering")).toBe("no");
      expect(response.headers.get("access-control-allow-origin")).toBe("https://polytrade.test");

      const reader = response.body?.getReader();
      expect(reader).toBeDefined();
      const first = await reader!.read();
      const firstText = new TextDecoder().decode(first.value);
      expect(firstText).toContain("event: run.started");
      expect(firstText).not.toContain("event: run.completed");

      releaseStream();
      let remainder = "";
      while (true) {
        const chunk = await reader!.read();
        if (chunk.done) break;
        remainder += new TextDecoder().decode(chunk.value);
      }
      expect(remainder).toContain("event: run.completed");
    } finally {
      releaseStream();
      await app.close();
      await agent.close();
    }
  });
});

describe("agent prediction boundary", () => {
  const headers = {
    authorization: "Bearer token",
    "content-type": "application/json",
    "idempotency-key": "agent-prediction-key-1",
  };
  const body = {
    conditionId: "0xcondition",
    tokenId: "123",
    marketQuestion: "Will the Fed hold rates in September?",
    predictedOutcome: "Yes",
    confidence: "0.62",
  };

  it("requires research auth to record a prediction", async () => {
    const { app, predictionStore, verifiedScopes } = await setup();
    const unauth = await app.inject({ method: "POST", url: "/v1/agent/predictions", payload: body });
    expect(unauth.statusCode).toBe(401);
    expect(predictionStore.predictions.size).toBe(0);

    const recorded = await app.inject({ method: "POST", url: "/v1/agent/predictions", headers, payload: body });
    expect(recorded.statusCode).toBe(200);
    expect(verifiedScopes).toEqual(["research"]);
    const record = recorded.json();
    expect(record).toMatchObject({ conditionId: "0xcondition", predictedOutcome: "Yes", status: "PENDING", category: null });
    expect(record.predictionId).toBeDefined();
    // The response is public-safe by construction: no principal or thread fields.
    expect(recorded.body).not.toContain(principal.id);
    expect(recorded.body).not.toContain("principalId");
    expect(recorded.body).not.toContain("threadId");
    expect(predictionStore.predictions.size).toBe(1);
    await app.close();
  });

  it("returns the original row when the same open call is recorded twice", async () => {
    const { app, predictionStore } = await setup();
    const first = await app.inject({
      method: "POST", url: "/v1/agent/predictions",
      headers, payload: body,
    });
    const retry = await app.inject({
      method: "POST", url: "/v1/agent/predictions",
      headers: { ...headers, "idempotency-key": "agent-prediction-key-2" },
      payload: { ...body, confidence: "0.90" },
    });
    expect(first.statusCode).toBe(200);
    expect(retry.statusCode).toBe(200);
    expect(retry.json().predictionId).toBe(first.json().predictionId);
    expect(predictionStore.predictions.size).toBe(1);
    await app.close();
  });

  it("serves an empty accuracy aggregate before anything is recorded", async () => {
    const { app } = await setup();
    const response = await app.inject({ method: "GET", url: "/v1/public/agent-accuracy" });
    expect(response.statusCode).toBe(200);
    expect(response.headers["cache-control"]).toBe("public, max-age=60");
    expect(agentPredictionHitRateSchema.parse(response.json()).totals).toEqual({
      graded: 0, hits: 0, hitRatePct: null, pending: 0, voided: 0, lastGradedAt: null,
    });
    await app.close();
  });

  it("serves the public accuracy aggregate without any identity fields", async () => {
    const { app, predictionStore, polymarket } = await setup();
    const recorded = await app.inject({ method: "POST", url: "/v1/agent/predictions", headers, payload: body });
    expect(recorded.statusCode).toBe(200);
    const [row] = [...predictionStore.predictions.values()];
    row!.status = "GRADED";
    row!.gradedOutcome = "Yes";
    row!.hit = true;
    row!.gradedAt = new Date("2026-09-02T12:00:00.000Z");
    row!.category = "Economics";
    polymarket.requestCounts.resolution = 0;

    const after = await app.inject({ method: "GET", url: "/v1/public/agent-accuracy" });
    expect(after.statusCode).toBe(200);
    expect(after.headers["cache-control"]).toBe("public, max-age=60");
    const parsed = agentPredictionHitRateSchema.parse(after.json());
    expect(parsed.totals).toMatchObject({ graded: 1, hits: 1, pending: 0 });
    expect(parsed.byCategory).toEqual([{ category: "Economics", graded: 1, hits: 1, hitRatePct: "100.00" }]);
    expect(parsed.recent).toHaveLength(1);
    // Aggregate never carries the caller identity.
    expect(after.body).not.toContain(principal.id);
    expect(after.body).not.toContain("principalId");
    expect(after.body).not.toContain("threadId");
    // Served from the TTL cache: no store re-read, no extra work.
    const repeat = await app.inject({ method: "GET", url: "/v1/public/agent-accuracy" });
    expect(repeat.json().totals.graded).toBe(1);
    expect(polymarket.requestCounts.resolution).toBe(0);
    await app.close();
  });
});

describe("alert channel boundary", () => {
  const headers = { authorization: "Bearer token", "content-type": "application/json" };
  const discordBody = {
    kind: "discord",
    label: "Trading Discord",
    target: "https://discord.com/api/webhooks/1234/abcdefghij",
    eventKinds: ["BUY", "SELL"],
  };

  it("requires research auth for alert routes", async () => {
    const { app } = await setup();
    const unauth = await app.inject({ method: "GET", url: "/v1/alerts/channels" });
    expect(unauth.statusCode).toBe(401);
    const unauthCreate = await app.inject({
      method: "POST",
      url: "/v1/alerts/channels",
      headers: { "content-type": "application/json", "idempotency-key": "alert-create-key-1" },
      payload: discordBody,
    });
    expect(unauthCreate.statusCode).toBe(401);
    const unauthTest = await app.inject({
      method: "POST",
      url: `/v1/alerts/channels/${"00000000-0000-4000-8000-000000000000"}/test`,
      headers: { "idempotency-key": "alert-test-key-1" },
    });
    expect(unauthTest.statusCode).toBe(401);
    await app.close();
  });

  it("creates, lists, tests, and deletes a channel without ever exposing the target", async () => {
    const { app, alertStore, alertRequests } = await setup();
    const created = await app.inject({
      method: "POST",
      url: "/v1/alerts/channels",
      headers: { ...headers, "idempotency-key": "alert-create-key-1" },
      payload: discordBody,
    });
    expect(created.statusCode).toBe(200);
    const channel = created.json() as { channelId: string; targetHint: string };
    expect(channel.targetHint).toContain("ghij");
    expect(created.body).not.toContain("abcdefghij");
    expect(created.body).not.toContain("encryptedTarget");

    const listed = await app.inject({ method: "GET", url: "/v1/alerts/channels", headers });
    expect(listed.statusCode).toBe(200);
    expect(listed.json()).toEqual({ items: [created.json()] });
    expect(listed.body).not.toContain("abcdefghij");

    const tested = await app.inject({
      method: "POST",
      url: `/v1/alerts/channels/${channel.channelId}/test`,
      headers: { authorization: "Bearer token", "idempotency-key": "alert-test-key-1" },
    });
    expect(tested.statusCode, tested.body).toBe(200);
    expect(tested.json()).toEqual({ status: "sent", error: null });
    expect(alertRequests).toEqual([{
      url: "https://discord.com/api/webhooks/1234/abcdefghij",
      body: JSON.stringify({ content: "PolyTrade test alert — strategy alert delivery is wired up." }),
    }]);

    const deliveries = await app.inject({ method: "GET", url: "/v1/alerts/deliveries", headers });
    expect(deliveries.statusCode).toBe(200);
    expect(deliveries.json()).toEqual({ items: [], limit: 20 });

    const deleted = await app.inject({
      method: "DELETE",
      url: `/v1/alerts/channels/${channel.channelId}`,
      headers: { authorization: "Bearer token", "idempotency-key": "alert-delete-key-1" },
    });
    expect(deleted.statusCode).toBe(204);
    expect(alertStore.channels.size).toBe(0);
    await app.close();
  });

  it("replays an idempotent channel creation instead of duplicating it", async () => {
    const { app, alertStore } = await setup();
    const first = await app.inject({
      method: "POST",
      url: "/v1/alerts/channels",
      headers: { ...headers, "idempotency-key": "alert-create-key-2" },
      payload: discordBody,
    });
    const replay = await app.inject({
      method: "POST",
      url: "/v1/alerts/channels",
      headers: { ...headers, "idempotency-key": "alert-create-key-2" },
      payload: discordBody,
    });
    expect(first.statusCode).toBe(200);
    expect(replay.statusCode).toBe(200);
    expect(replay.body).toBe(first.body);
    expect(alertStore.channels.size).toBe(1);
    await app.close();
  });

  it("rejects a duplicate label with 409 and unknown deletes with 404", async () => {
    const { app } = await setup();
    await app.inject({
      method: "POST",
      url: "/v1/alerts/channels",
      headers: { ...headers, "idempotency-key": "alert-create-key-3" },
      payload: discordBody,
    });
    const duplicate = await app.inject({
      method: "POST",
      url: "/v1/alerts/channels",
      headers: { ...headers, "idempotency-key": "alert-create-key-4" },
      payload: { ...discordBody, target: "https://discord.com/api/webhooks/999/zzzz" },
    });
    expect(duplicate.statusCode).toBe(409);

    const missing = await app.inject({
      method: "DELETE",
      url: `/v1/alerts/channels/${"00000000-0000-4000-8000-000000000000"}`,
      headers: { authorization: "Bearer token", "idempotency-key": "alert-delete-key-1" },
    });
    expect(missing.statusCode).toBe(404);
    await app.close();
  });

  it("surfaces a clear failed test send when the Telegram bot token is missing", async () => {
    const { app } = await setup("127.0.0.1/32,::1/128", { TELEGRAM_BOT_TOKEN: undefined });
    const created = await app.inject({
      method: "POST",
      url: "/v1/alerts/channels",
      headers: { ...headers, "idempotency-key": "alert-tg-key-1" },
      payload: {
        kind: "telegram",
        label: "Phone",
        target: "-1001234567890",
        eventKinds: ["ERROR"],
      },
    });
    expect(created.statusCode).toBe(200);
    const channel = created.json() as { channelId: string; targetHint: string };
    expect(channel.targetHint).toBe("chat -1001234567890");

    const tested = await app.inject({
      method: "POST",
      url: `/v1/alerts/channels/${channel.channelId}/test`,
      headers: { authorization: "Bearer token", "idempotency-key": "alert-tg-test-1" },
    });
    expect(tested.statusCode).toBe(200);
    expect(tested.json()).toEqual({
      status: "failed",
      error: "TELEGRAM_BOT_TOKEN is not configured on the gateway",
    });
    await app.close();
  });
});
