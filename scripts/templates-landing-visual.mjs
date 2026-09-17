// Visual review for the public /templates landing page and the
// /backtests/new?template= prefill. Requires the vite dev server on :5173
// with VITE_E2E_AUTH_BYPASS=1 (.env.local). Gateway calls are mocked;
// screenshots land in shots/templates/.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

mkdirSync("shots/templates", { recursive: true });

const observedAt = "2026-09-02T00:00:00.000Z";
const json = (value) => ({ status: 200, contentType: "application/json", body: JSON.stringify(value) });

const resolvedSearch = json({
  query: "crypto above", state: "resolved", observedAt,
  events: [{
    id: "e1", slug: "crypto", title: "Crypto", description: "Fixture event",
    endDate: null, liquidity: "100000", volume: "500000",
    markets: [{
      id: "market-1", conditionId: "0xcond-1", slug: "market-1",
      question: "Will ETH be above $5000 on December 31?", description: "Fixture market",
      outcomes: ["Yes", "No"], outcomePrices: ["0.60", "0.40"],
      clobTokenIds: ["100", "101"], active: false, closed: true,
      acceptingOrders: false, enableOrderBook: true, archived: false,
      restricted: false, minimumOrderSize: "5", minimumTickSize: "0.01",
      endDate: null, startDate: "2026-05-01T00:00:00.000Z", createdAt: null,
      closedTime: "2026-08-01T00:00:00.000Z", liquidity: "100000", volume: "500000",
    }],
  }],
});

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text());
});

const portfolio = {
  initialCash: "10000.000000", cash: "10000.000000", positionsValue: "0.000000",
  equity: "10000.000000", realizedPnl: "0.000000", unrealizedPnl: "0.000000",
  totalPnl: "0.000000", totalFees: "0.000000",
  positions: [], warnings: [], observedAt,
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

const activeSearch = json({
  query: "", state: "active", observedAt,
  events: [{
    id: "e2", slug: "rates", title: "Rates", description: "Fixture event",
    endDate: null, liquidity: "100000", volume: "500000",
    markets: [searchMarket("0xcond-a", "100", "Will the Fed hold rates in September?", "0.90")],
  }],
});

await page.route("**/v1/**", async (route) => {
  const path = new URL(route.request().url()).pathname;
  if (path.startsWith("/v1/research/markets")) {
    const query = new URL(route.request().url()).searchParams.get("query") ?? "";
    return route.fulfill(query === "fed decision" ? activeSearch : resolvedSearch);
  }
  if (path === "/v1/paper/portfolio" || path === "/v1/paper/refresh") return route.fulfill(json(portfolio));
  if (path.startsWith("/v1/paper/fills")) return route.fulfill(json({ items: [], total: 0, offset: 0, limit: 20 }));
  if (path === "/v1/paper/strategy") return route.fulfill(json({ strategy: null, events: [] }));
  if (path === "/v1/paper/share") return route.fulfill(json({ token: null, enabled: false, createdAt: null, updatedAt: null }));
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

// 1. /templates desktop, signed out.
await page.goto("http://localhost:5173/templates", { waitUntil: "networkidle" });
await page.waitForTimeout(600);
const hero = await page.getByRole("heading", { name: "Start paper trading in two minutes" }).count();
const cards = await page.locator(".template-card").count();
const deployLinks = await page.getByRole("link", { name: /Deploy to paper/ }).count();
let shot = "shots/templates/landing-desktop.png";
await page.screenshot({ path: shot, fullPage: true });
console.log(`saved ${shot} (hero=${hero}, cards=${cards}, deployLinks=${deployLinks})`);

// 2. 390px mobile.
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(400);
shot = "shots/templates/landing-mobile.png";
await page.screenshot({ path: shot, fullPage: true });
console.log(`saved ${shot}`);

// 3. Click Deploy from the landing page → lands on /paper with the template armed.
await page.setViewportSize({ width: 1440, height: 1000 });
await page.getByRole("link", { name: /Deploy to paper/ }).first().click();
await page.waitForURL("**/paper?template=*", { timeout: 10_000 });
await page.waitForTimeout(1_200);
const chipOnPaper = await page.getByText(/Template · Base-rate divergence/).count();
shot = "shots/templates/landing-deploy-clickthrough.png";
await page.screenshot({ path: shot, fullPage: true });
console.log(`saved ${shot} (url=${page.url()}, chip=${chipOnPaper})`);

// 4. /backtests/new?template=ev-sniping prefill.
await page.goto("http://localhost:5173/backtests/new?template=ev-sniping", { waitUntil: "networkidle" });
await page.waitForTimeout(1_200);
const chip = await page.getByText("Pre-filled from template · EV sniping").count();
const meanReversionChecked = await page.getByRole("radio", { name: /Mean reversion/i }).isChecked();
const queryValue = await page.getByRole("textbox", { name: "Search resolved markets" }).inputValue();
shot = "shots/templates/backtest-prefilled.png";
await page.screenshot({ path: shot, fullPage: true });
console.log(`saved ${shot} (chip=${chip}, meanReversion=${meanReversionChecked}, query=${queryValue})`);

await browser.close();
let failed = false;
if (!hero) { console.error("FAIL: landing hero missing"); failed = true; }
if (cards !== 5) { console.error(`FAIL: expected 5 template cards, got ${cards}`); failed = true; }
if (deployLinks !== 5) { console.error(`FAIL: expected 5 deploy links, got ${deployLinks}`); failed = true; }
if (!chipOnPaper) { console.error("FAIL: template chip missing after landing deploy click-through"); failed = true; }
if (!chip) { console.error("FAIL: backtest prefill chip missing"); failed = true; }
if (!meanReversionChecked) { console.error("FAIL: mean reversion radio not pre-selected"); failed = true; }
if (queryValue !== "crypto above") { console.error(`FAIL: backtest query expected 'crypto above', got '${queryValue}'`); failed = true; }
if (errors.length) { console.error(`page errors:\n${errors.join("\n")}`); failed = true; }
if (failed) process.exit(1);
console.log("visual review assertions passed");