// Visual review for the track-record PR: signed-out /u/:token page (happy +
// 404) and the authed /paper share card. Requires the vite dev server on
// :5173 with VITE_E2E_AUTH_BYPASS=1 (.env.local). Gateway calls are mocked;
// screenshots land in shots/track-record/.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

mkdirSync("shots/track-record", { recursive: true });

const observedAt = "2026-09-02T00:00:00.000Z";
const SHARE_TOKEN = "k7Q4sV2mXz8pR3wTy6N1bC5dJ9hG4fA1"; // 32 url-safe chars

const record = {
  profile: { displayName: "Paper account", startedAt: "2026-08-01T00:00:00.000Z" },
  stats: {
    initialCash: "10000.000000",
    cash: "9500.000000",
    equity: "9505.200000",
    totalPnl: "-494.800000",
    realizedPnl: "10.000000",
    unrealizedPnl: "0.200000",
    totalFees: "1.000000",
    tradeCount: 2,
    winRate: "100.00",
  },
  equityCurve: [
    { t: "2026-08-01T00:00:00.000Z", equity: "10000.000000" },
    { t: "2026-08-03T00:00:00.000Z", equity: "9850.000000" },
    { t: "2026-08-07T00:00:00.000Z", equity: "9497.000000" },
    { t: "2026-08-12T00:00:00.000Z", equity: "9500.000000" },
    { t: "2026-08-20T00:00:00.000Z", equity: "9725.000000" },
    { t: "2026-08-28T00:00:00.000Z", equity: "9410.000000" },
    { t: observedAt, equity: "9505.200000" },
  ],
  positions: [{
    marketQuestion: "Will the Fed hold rates in September?",
    outcome: "Yes",
    shares: "10.000000",
    averageCost: "0.500000",
    liquidationValue: "5.200000",
    unrealizedPnl: "0.200000",
    markStatus: "current",
  }],
  fills: [
    {
      fillId: "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f",
      kind: "SELL",
      marketQuestion: "Will the Fed hold rates in September?",
      outcome: "No",
      shares: "10.000000",
      averagePrice: "0.530000",
      fee: "0.500000",
      cashEffect: "3.000000",
      realizedPnl: "10.000000",
      createdAt: "2026-09-01T12:00:00.000Z",
    },
    {
      fillId: "1f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f",
      kind: "BUY",
      marketQuestion: "Will the Fed hold rates in September?",
      outcome: "Yes",
      shares: "10.000000",
      averagePrice: "0.500000",
      fee: "0.500000",
      cashEffect: "-5.000000",
      realizedPnl: "0.000000",
      createdAt: "2026-09-01T00:00:00.000Z",
    },
  ],
  observedAt,
};

const portfolio = {
  initialCash: "10000.000000", cash: "9500.000000", positionsValue: "5.200000",
  equity: "9505.200000", realizedPnl: "10.000000", unrealizedPnl: "0.200000",
  totalPnl: "10.200000", totalFees: "1.000000",
  positions: record.positions.map((position) => ({
    conditionId: "0xcondition", tokenId: "111", bestBid: "0.520000",
    costBasis: "5.000000", markedAt: "2026-09-02T00:00:00.000Z",
    ...position,
  })),
  warnings: [], observedAt,
};

const paperFillRow = (fill) => ({
  conditionId: "0xcondition", tokenId: "111", grossNotional: "5.000000",
  feeRate: "0.040000", observedAt: fill.createdAt, ...fill,
});

const json = (value) => ({ status: 200, contentType: "application/json", body: JSON.stringify(value) });

const publicHeaders = [];
let shareStatusCalls = 0;

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
page.on("console", (message) => {
  // Deliberate 404 fetches (the unavailable-token state) log as console errors.
  if (message.type() === "error" && !message.text().includes("404")) errors.push(message.text());
});

await page.route("**/v1/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;

  if (path.startsWith("/v1/public/track-records/")) {
    publicHeaders.push(request.headers());
    if (path === `/v1/public/track-records/${SHARE_TOKEN}`) return route.fulfill(json(record));
    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "NOT_FOUND", message: "Track record not found" } }) });
  }

  if (path === "/v1/wallet-sessions/current") {
    return route.fulfill(json({
      sessionId: "3f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d01",
      walletAddress: "0x6326B505c0e0E8815d088b591219Ebf9Cd455bF5",
      signatureType: 0,
      idleExpiresAt: "2026-09-07T15:00:00.000Z",
      expiresAt: "2026-09-07T15:00:00.000Z",
    }));
  }
  if (path === "/v1/paper/share") {
    shareStatusCalls += 1;
    if (request.method() === "GET") {
      return route.fulfill(json({ token: SHARE_TOKEN, enabled: true, createdAt: observedAt, updatedAt: observedAt }));
    }
    return route.fulfill(json({ token: SHARE_TOKEN, enabled: true, createdAt: observedAt, updatedAt: observedAt }));
  }
  if (path === "/v1/paper/portfolio" || path === "/v1/paper/refresh") return route.fulfill(json(portfolio));
  if (path.startsWith("/v1/paper/fills")) {
    return route.fulfill(json({ items: record.fills.map(paperFillRow), total: 2, offset: 0, limit: 20 }));
  }
  if (path === "/v1/paper/strategy") return route.fulfill(json({ strategy: null, events: [] }));
  if (path === "/v1/account/overview") {
    return route.fulfill(json({ walletAddress: "0x6326B505c0e0E8815d088b591219Ebf9Cd455bF5", positions: [], openOrders: [], fills: [], observedAt }));
  }
  if (path === "/v1/agent/threads") return route.fulfill(json([]));
  if (path.startsWith("/v1/research/markets")) {
    return route.fulfill(json({ query: "", state: "active", observedAt, events: [] }));
  }
  console.log(`unmocked endpoint: ${request.method()} ${path}`);
  return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "MOCKED", message: "mocked" } }) });
});

// 1. Signed-out happy path at /u/:token.
await page.goto(`http://localhost:5173/u/${SHARE_TOKEN}`, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
const robots = await page.evaluate(() => document.querySelector('meta[name="robots"]')?.getAttribute("content"));
console.log(`robots meta: ${robots}`);
const trackRecordRequest = publicHeaders.find((headers) => headers.authorization === undefined);
console.log(`public request without Authorization header: ${trackRecordRequest ? "yes" : "NO"}`);
let shot = "shots/track-record/u-happy.png";
await page.screenshot({ path: shot, fullPage: true });
console.log(`saved ${shot}`);

// 2. Unknown / disabled token → 404 card.
await page.goto(`http://localhost:5173/u/${"x".repeat(32)}`, { waitUntil: "networkidle" });
await page.waitForTimeout(400);
shot = "shots/track-record/u-unavailable.png";
await page.screenshot({ path: shot, fullPage: true });
console.log(`saved ${shot}`);

// 3. Authed /paper page with the share card in the ticket column.
await page.goto("http://localhost:5173/paper", { waitUntil: "networkidle" });
await page.waitForTimeout(800);
shot = "shots/track-record/paper-share-card.png";
await page.screenshot({ path: shot, fullPage: true });
console.log(`saved ${shot}`);
console.log(`share status requests: ${shareStatusCalls}`);

await browser.close();
if (robots !== "noindex") {
  console.error("FAIL: robots meta missing");
  process.exit(1);
}
if (!trackRecordRequest) {
  console.error("FAIL: track-record request carried an Authorization header");
  process.exit(1);
}
if (errors.length) {
  console.error(`page errors:\n${errors.join("\n")}`);
  process.exit(1);
}
console.log("visual review assertions passed");