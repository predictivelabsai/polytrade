// Scratch walkthrough for the PR C flow: public market page → "Ask the agent"
// → /chat/new?market=… seeds the composer and auto-submits, the agent streams
// a reply. Fulfill gateway + agent endpoints via route interception. Not
// committed — local harness only.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

mkdirSync("shots/market-pages", { recursive: true });

const observedAt = "2026-09-01T12:00:00.000Z";
const THREAD = "3fa85f64-5717-4562-b3fc-2c963f66afa6";

const market = {
  id: "market-1", conditionId: "condition-fed", slug: "fed-rates-september",
  question: "Will the Fed cut rates in September?", description: "Resolution source: Federal Reserve statements.",
  outcomes: ["Yes", "No"], outcomePrices: ["0.435", "0.565"], clobTokenIds: ["111", "222"],
  active: true, closed: false, acceptingOrders: true, enableOrderBook: true, archived: false,
  restricted: false, minimumOrderSize: "5", minimumTickSize: "0.01",
  endDate: "2026-09-16T00:00:00.000Z", startDate: "2026-08-01T00:00:00.000Z",
  createdAt: null, closedTime: null, liquidity: "50000", volume: "1200000",
  volume24hr: "312400",
};

const json = (value) => ({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify(value),
});

const sse = (events) => ({
  status: 200,
  contentType: "text/event-stream",
  body: events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(""),
});

const portfolio = {
  initialCash: "10000.000000", cash: "10000.000000", positionsValue: "0.000000",
  equity: "10000.000000", realizedPnl: "0.000000", unrealizedPnl: "0.000000",
  totalPnl: "0.000000", totalFees: "0.000000", positions: [], warnings: [], observedAt,
};

const overview = {
  walletAddress: "0x0000000000000000000000000000000000000001",
  positions: [], openOrders: [], fills: [], observedAt,
};

let capturedPrompt = null;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });

await page.route("**/v1/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  const method = request.method();

  if (path.startsWith("/v1/public/")) {
    if (path === "/v1/public/markets") {
      return route.fulfill(json({ markets: [market], limit: 12, offset: 0, hasMore: false, observedAt }));
    }
    if (path === "/v1/public/markets/fed-rates-september") {
      return route.fulfill(json({
        market,
        quotes: [
          { outcome: "Yes", tokenId: "111", price: "0.44", bestBid: "0.42", bestAsk: "0.46", source: "order-book" },
          { outcome: "No", tokenId: "222", price: "0.565", bestBid: "0.55", bestAsk: "0.58", source: "order-book" },
        ],
        observedAt,
      }));
    }
    if (path.startsWith("/v1/public/order-books/")) {
      return route.fulfill(json({
        tokenId: "111", minimumOrderSize: "5", tickSize: "0.01", negativeRisk: false,
        lastTradePrice: "0.44",
        bids: [{ price: "0.42", size: "500" }, { price: "0.41", size: "1200" }],
        asks: [{ price: "0.46", size: "300" }, { price: "0.47", size: "2400" }],
        observedAt,
      }));
    }
    if (path.startsWith("/v1/public/price-history/")) {
      return route.fulfill(json({
        tokenId: "111", interval: "1d",
        points: Array.from({ length: 12 }, (_, i) => ({
          timestamp: Date.parse("2026-08-31T00:00:00Z") + i * 2 * 3600_000,
          price: (0.40 + Math.sin(i / 2.5) * 0.05 + i * 0.004).toFixed(3),
        })),
        observedAt,
      }));
    }
  }

  if (path === "/v1/wallet-sessions/current") {
    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "NOT_FOUND", message: "No active wallet session" } }) });
  }
  if (path === "/v1/account/overview") return route.fulfill(json(overview));
  if (path === "/v1/paper/portfolio" || path === "/v1/paper/refresh") return route.fulfill(json(portfolio));
  if (path === "/v1/paper/fills") return route.fulfill(json({ items: [], total: 0, limit: 20, offset: 0 }));
  if (path === "/v1/paper/strategy") return route.fulfill(json({ strategy: null, events: [] }));
  if (path === "/v1/backtests") return route.fulfill(json({ items: [] }));

  if (path === "/v1/agent/threads" && method === "GET") return route.fulfill(json({ items: [] }));
  if (path === "/v1/agent/threads" && method === "POST") {
    return route.fulfill(json({
      threadId: THREAD,
      title: "Fed rates research",
      createdAt: observedAt,
      updatedAt: observedAt,
      expiresAt: "2026-09-08T12:00:00.000Z",
    }));
  }
  if (path.endsWith("/runs/stream")) {
    capturedPrompt = JSON.parse(request.postData() ?? "{}").message ?? null;
    const answer = "Yes is trading around 44¢ with a 42¢/46¢ book — a thin but two-sided market. The tape has drifted up over the last week on dovish commentary.";
    return route.fulfill(sse([
      ["run.started", {}],
      ["message.started", { messageId: "m1" }],
      ["message.delta", { messageId: "m1", textDelta: answer }],
      ["run.completed", {}],
    ]));
  }
  if (path === `/v1/agent/threads/${THREAD}/messages`) return route.fulfill(json({ items: [] }));

  return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
});

// Step 1: public detail page with the Ask button.
await page.goto("http://localhost:5176/markets/fed-rates-september", { waitUntil: "networkidle" });
await page.waitForTimeout(500);
await page.screenshot({ path: "shots/market-pages/ask-button.png" });

// Step 2: click Ask the agent → /chat/new?market=… seeds + auto-submits.
await page.click("role=button[name=\"Ask the agent\"]");
await page.waitForURL("**/chat/**", { timeout: 10_000 });
await page.getByRole("heading", { name: "Will the Fed cut rates in September?" }).first();
await page.waitForTimeout(1_200);
await page.screenshot({ path: "shots/market-pages/chat-seeded.png", fullPage: true });

console.log("captured prompt:", capturedPrompt);
console.log("url:", page.url());
console.log(errors.length ? `console errors: ${errors.join(" | ")}` : "console clean");
await browser.close();