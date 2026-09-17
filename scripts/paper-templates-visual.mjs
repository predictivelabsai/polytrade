// Visual review for the strategy-templates /paper wiring. Requires the vite
// dev server on :5173 with VITE_E2E_AUTH_BYPASS=1 (.env.local). Gateway calls
// are mocked; screenshots land in shots/templates/.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

mkdirSync("shots/templates", { recursive: true });

const observedAt = "2026-09-02T00:00:00.000Z";

const portfolio = {
  initialCash: "10000.000000", cash: "9900.000000", positionsValue: "5.200000",
  equity: "9905.200000", realizedPnl: "10.000000", unrealizedPnl: "-0.200000",
  totalPnl: "-4.800000", totalFees: "1.000000",
  positions: [{
    conditionId: "0xcondition", tokenId: "111", marketQuestion: "Will the Fed hold rates in September?",
    outcome: "Yes", shares: "10.000000", averageCost: "0.500000", bestBid: "0.520000",
    costBasis: "5.000000", liquidationValue: "5.200000", unrealizedPnl: "0.200000",
    markStatus: "current", markedAt: observedAt,
  }],
  warnings: [], observedAt,
};

const searchMarket = (conditionId, tokenId, question, yesPrice) => ({
  id: `market-${tokenId}`, conditionId, slug: `market-${tokenId}`, question,
  description: "Fixture market", outcomes: ["Yes", "No"],
  outcomePrices: [yesPrice, (1 - yesPrice).toFixed(2)],
  clobTokenIds: [tokenId, String(Number(tokenId) + 1)],
  active: true, closed: false, acceptingOrders: true, enableOrderBook: true,
  archived: false, restricted: false, minimumOrderSize: "5", minimumTickSize: "0.01",
  endDate: null, startDate: null, createdAt: null, closedTime: null,
  liquidity: "100000", volume: "500000",
});

const json = (value) => ({ status: 200, contentType: "application/json", body: JSON.stringify(value) });

const searchResponse = json({
  query: "", state: "active", observedAt,
  events: [{
    id: "e1", slug: "rates", title: "Rates", description: "Fixture event",
    endDate: null, liquidity: "100000", volume: "500000",
    markets: [searchMarket("0xcond-a", "100", "Will the Fed hold rates in September?", "0.90")],
  }],
});

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text());
});

await page.route("**/v1/**", async (route) => {
  const path = new URL(route.request().url()).pathname;
  if (path === "/v1/paper/portfolio" || path === "/v1/paper/refresh") return route.fulfill(json(portfolio));
  if (path.startsWith("/v1/paper/fills")) return route.fulfill(json({ items: [], total: 0, offset: 0, limit: 20 }));
  if (path === "/v1/paper/strategy") return route.fulfill(json({ strategy: null, events: [] }));
  if (path === "/v1/paper/share") return route.fulfill(json({ token: null, enabled: false, createdAt: null, updatedAt: null }));
  if (path.startsWith("/v1/research/markets")) {
    return route.fulfill(searchResponse);
  }
  if (path === "/v1/wallet-sessions/current") {
    return route.fulfill(json({ sessionId: "3f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d01", walletAddress: "0x6326B505c0e0E8815d088b591219Ebf9Cd455bF5", signatureType: 0, idleExpiresAt: observedAt, expiresAt: observedAt }));
  }
  if (path === "/v1/account/overview") {
    return route.fulfill(json({ walletAddress: "0x6326B505c0e0E8815d088b591219Ebf9Cd455bF5", positions: [], openOrders: [], fills: [], observedAt }));
  }
  if (path === "/v1/agent/threads") return route.fulfill(json({ items: [] }));
  console.log(`unmocked endpoint: ${route.request().method()} ${path}`);
  return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "MOCKED", message: "mocked" } }) });
});

// 1. /paper with the template card row between ledger and grid.
await page.goto("http://localhost:5173/paper", { waitUntil: "networkidle" });
await page.waitForTimeout(800);
let shot = "shots/templates/paper-grid.png";
await page.screenshot({ path: shot, fullPage: true });
console.log(`saved ${shot}`);

// 2. Armed template via ?template=longshot-fade, market selected, runner shows the chip.
await page.goto("http://localhost:5173/paper?template=longshot-fade", { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await page.getByRole("button", { name: /Will the Fed hold rates/ }).first().click();
await page.waitForTimeout(600);
const chip = await page.getByText(/Template · Longshot fade/).count();
const buyValue = await page.getByLabel("Strategy buy price").inputValue();
shot = "shots/templates/paper-armed.png";
await page.screenshot({ path: shot, fullPage: true });
console.log(`saved ${shot} (chip=${chip}, buy=${buyValue})`);

// 3. Running strategy blocks the grid.
await page.unroute("**/v1/**");
await page.route("**/v1/**", async (route) => {
  const path = new URL(route.request().url()).pathname;
  if (path === "/v1/paper/strategy") {
    return route.fulfill(json({
      strategy: {
        strategyId: "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f", conditionId: "0xcond-a", tokenId: "100",
        marketQuestion: "Will the Fed hold rates in September?", outcome: "Yes", entryPrice: "0.880000",
        exitPrice: "0.930000", sharesPerOrder: "20.000000", maxPosition: "80.000000", intervalSeconds: 30,
        status: "RUNNING", ordersPlaced: 1, scansCompleted: 12, lastAction: "BUY", lastMessage: "Bought 20 shares at 0.880000.",
        lastQuoteSide: "BUY", lastQuotePrice: "0.880000", lastScannedAt: observedAt, nextScanAt: observedAt,
        startedAt: observedAt, stoppedAt: null, updatedAt: observedAt,
      },
      events: [],
    }));
  }
  if (path === "/v1/paper/portfolio" || path === "/v1/paper/refresh") return route.fulfill(json(portfolio));
  if (path.startsWith("/v1/paper/fills")) return route.fulfill(json({ items: [], total: 0, offset: 0, limit: 20 }));
  if (path === "/v1/paper/share") return route.fulfill(json({ token: null, enabled: false, createdAt: null, updatedAt: null }));
  if (path.startsWith("/v1/research/markets")) return route.fulfill(json({ query: "", state: "active", observedAt, events: [{ id: "e1", slug: "rates", title: "Rates", description: "Fixture event", endDate: null, liquidity: "100000", volume: "500000", markets: [] }] }));
  if (path === "/v1/wallet-sessions/current") return route.fulfill(json({ sessionId: "3f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d01", walletAddress: "0x6326B505c0e0E8815d088b591219Ebf9Cd455bF5", signatureType: 0, idleExpiresAt: observedAt, expiresAt: observedAt }));
  if (path === "/v1/account/overview") return route.fulfill(json({ walletAddress: "0x6326B505c0e0E8815d088b591219Ebf9Cd455bF5", positions: [], openOrders: [], fills: [], observedAt }));
  if (path === "/v1/agent/threads") return route.fulfill(json({ items: [] }));
  return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "MOCKED", message: "mocked" } }) });
});
await page.goto("http://localhost:5173/paper", { waitUntil: "networkidle" });
await page.waitForTimeout(800);
const blocked = await page.getByText("Stop the running strategy to deploy a template.").count();
shot = "shots/templates/paper-running-blocked.png";
await page.screenshot({ path: shot, fullPage: true });
console.log(`saved ${shot} (blocked-note=${blocked})`);

await browser.close();
if (!chip) {
  console.error("FAIL: template chip missing on armed runner");
  process.exit(1);
}
if (buyValue !== "0.88") {
  console.error(`FAIL: template buy price expected 0.88, got ${buyValue}`);
  process.exit(1);
}
if (!blocked) {
  console.error("FAIL: running-strategy block note missing");
  process.exit(1);
}
if (errors.length) {
  console.error(`page errors:\n${errors.join("\n")}`);
  process.exit(1);
}
console.log("visual review assertions passed");