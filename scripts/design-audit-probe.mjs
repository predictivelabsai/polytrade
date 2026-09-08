// Keep the paper workspace contained across responsive breakpoints. Tables may
// scroll inside .table-scroll, but no panel may widen the document or overlap.
import { chromium } from "playwright-core";

const observedAt = "2026-09-02T00:00:00.000Z";
const WALLET = "0x6326B505c0e0E8815d088b591219Ebf9Cd455bF5";
const json = (value) => ({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
const session = {
  sessionId: "3f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d01", walletAddress: WALLET, signatureType: 0,
  idleExpiresAt: "2026-09-02T04:00:00.000Z", expiresAt: "2026-09-02T12:00:00.000Z",
};
const paperPortfolio = {
  initialCash: "10000.000000", cash: "8320.500000", positionsValue: "2107.800000",
  equity: "10428.300000", realizedPnl: "128.400000", unrealizedPnl: "299.400000",
  totalPnl: "428.300000", totalFees: "18.600000",
  positions: [
    { conditionId: "0xcond-a", tokenId: "111", marketQuestion: "Will the Fed hold rates in September?", outcome: "Yes", shares: "400.000000", averageCost: "0.520000", bestBid: "0.610000", costBasis: "208.000000", liquidationValue: "244.000000", unrealizedPnl: "36.000000", markStatus: "current", markedAt: observedAt },
  ],
  warnings: [], observedAt,
};
const paperFills = { items: [], total: 0, offset: 0, limit: 20 };
const runningStrategy = {
  strategy: {
    strategyId: "77777777-7777-4777-8777-777777777777", conditionId: "0xcond-a", tokenId: "111",
    marketQuestion: "Will the Fed hold rates in September?", outcome: "Yes",
    entryPrice: "0.520000", exitPrice: "0.610000", sharesPerOrder: "100.000000", maxPosition: "400.000000",
    intervalSeconds: 15, status: "RUNNING", ordersPlaced: 4, scansCompleted: 96,
    lastAction: "WAIT", lastMessage: "Quote outside the band — waiting for the next scan.",
    lastQuoteSide: null, lastQuotePrice: null, lastScannedAt: "2026-09-02T00:00:05.000Z",
    nextScanAt: "2026-09-02T00:00:20.000Z", startedAt: "2026-09-01T23:00:00.000Z", stoppedAt: null,
    updatedAt: "2026-09-02T00:00:05.000Z",
  },
  events: [],
};
const searchResponse = json({
  query: "", state: "active", observedAt,
  events: [{ id: "e1", slug: "rates", title: "Rates", description: "Fixture event", endDate: null, liquidity: "100000", volume: "500000", markets: [] }],
});

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
await page.route("**/v1/**", async (route) => {
  const path = new URL(route.request().url()).pathname;
  if (path === "/v1/wallet-sessions/current") return route.fulfill(json(session));
  if (path === "/v1/account/overview") return route.fulfill(json({ walletAddress: WALLET, positions: [], openOrders: [], fills: [], observedAt }));
  if (path === "/v1/agent/threads") return route.fulfill(json({ items: [] }));
  if (path === "/v1/paper/portfolio" || path === "/v1/paper/refresh") return route.fulfill(json(paperPortfolio));
  if (path.startsWith("/v1/paper/fills")) return route.fulfill(json(paperFills));
  if (path === "/v1/paper/strategy") return route.fulfill(json(runningStrategy));
  if (path === "/v1/paper/share") return route.fulfill(json({ token: null, enabled: false, createdAt: null, updatedAt: null }));
  if (path.startsWith("/v1/research/markets")) return route.fulfill(searchResponse);
  return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
});
await page.route("https://polymarket.com/api/geoblock", (route) => route.fulfill(json({ blocked: false, country: "US", region: "NY" })));
const reports = [];
for (const width of [320, 390, 767, 768, 1024, 1199, 1200, 1440, 1920]) {
  await page.setViewportSize({ width, height: 844 });
  await page.goto("http://localhost:5173/paper", { waitUntil: "networkidle" });
  await page.waitForTimeout(400);

  const report = await page.evaluate(() => {
    const selectors = [
      ".paper-market-panel",
      ".paper-ticket",
      ".paper-strategy",
      ".paper-holdings-panel",
      ".paper-fills-panel",
      ".share-card",
    ];
    const panels = selectors.flatMap((selector) => {
      const element = document.querySelector(selector);
      if (!element) return [];
      const rect = element.getBoundingClientRect();
      return [{ selector, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom }];
    });
    const collisions = [];
    for (let index = 0; index < panels.length; index += 1) {
      for (let other = index + 1; other < panels.length; other += 1) {
        const a = panels[index];
        const b = panels[other];
        if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1
          && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1) {
          collisions.push(`${a.selector} × ${b.selector}`);
        }
      }
    }
    const root = document.documentElement;
    return {
      viewport: root.clientWidth,
      scrollWidth: root.scrollWidth,
      collisions,
      resizerCount: document.querySelectorAll(".paper-grid-resizer").length,
    };
  });
  reports.push(report);
}

console.log(JSON.stringify(reports, null, 2));
const failures = reports.filter((report) => (
  report.scrollWidth > report.viewport || report.collisions.length || report.resizerCount !== 0
));
if (failures.length) {
  throw new Error(`Paper layout regression:\n${JSON.stringify(failures, null, 2)}`);
}
await browser.close();
