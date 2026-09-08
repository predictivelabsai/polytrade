// Design-audit screenshots for every workspace page. Requires the vite dev
// server on :5173 with VITE_E2E_AUTH_BYPASS=1 (.env.local). All gateway calls
// are mocked; usage: node scripts/design-audit-shots.mjs <before|after>
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const phase = process.argv[2] ?? "before";
const outDir = `shots/audit/${phase}`;
mkdirSync(outDir, { recursive: true });

const observedAt = "2026-09-02T00:00:00.000Z";
const WALLET = "0x6326B505c0e0E8815d088b591219Ebf9Cd455bF5";
const THREAD = "11111111-1111-4111-8111-111111111111";
const RUN_DONE = "33333333-3333-4333-8333-333333333333";
const RUN_LIVE = "44444444-4444-4444-8444-444444444444";

const json = (value) => ({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
const notFound = () => route => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "NOT_FOUND", message: "No active wallet session" } }) });

const session = {
  sessionId: "3f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d01", walletAddress: WALLET, signatureType: 0,
  idleExpiresAt: "2026-09-02T04:00:00.000Z", expiresAt: "2026-09-02T12:00:00.000Z",
};

const accountOverview = {
  walletAddress: WALLET,
  positions: [
    { positionId: "p1", conditionId: "0xcond-a", assetId: "111", marketTitle: "Will the Fed hold rates in September?", outcome: "Yes", size: "120", averagePrice: "0.6200", currentPrice: "0.7400", currentValue: "88.80", cashPnl: "14.40", percentPnl: "19.35", redeemable: false },
    { positionId: "p2", conditionId: "0xcond-b", assetId: "222", marketTitle: "Will Bitcoin close above $70k in October?", outcome: "No", size: "50", averagePrice: "0.4100", currentPrice: "0.3800", currentValue: "19.00", cashPnl: "-1.50", percentPnl: "-7.32", redeemable: false },
  ],
  openOrders: [
    { orderId: "o1", marketId: "0xcond-a", assetId: "111", outcome: "Yes", side: "BUY", originalSize: "40", matchedSize: "12", remainingSize: "28", price: "0.5500", orderType: "GTC", status: "LIVE", createdAt: "2026-09-01T14:20:00.000Z", expiration: null },
  ],
  fills: [
    { tradeId: "t1", marketId: "0xcond-a", assetId: "111", outcome: "Yes", side: "BUY", size: "120", price: "0.6200", status: "MATCHED", matchedAt: "2026-09-01T13:02:00.000Z", traderSide: "TAKER", transactionHash: "0xabc123def4567890abc123def4567890abc123de" },
    { tradeId: "t2", marketId: "0xcond-b", assetId: "222", outcome: "No", side: "SELL", size: "25", price: "0.3900", status: "MATCHED", matchedAt: "2026-08-31T09:44:00.000Z", traderSide: "MAKER", transactionHash: "0xdef456abc123def456abc123def456abc123def4" },
  ],
  observedAt,
};

const paperPortfolio = {
  initialCash: "10000.000000", cash: "8320.500000", positionsValue: "2107.800000",
  equity: "10428.300000", realizedPnl: "128.400000", unrealizedPnl: "299.400000",
  totalPnl: "428.300000", totalFees: "18.600000",
  positions: [
    { conditionId: "0xcond-a", tokenId: "111", marketQuestion: "Will the Fed hold rates in September?", outcome: "Yes", shares: "400.000000", averageCost: "0.520000", bestBid: "0.610000", costBasis: "208.000000", liquidationValue: "244.000000", unrealizedPnl: "36.000000", markStatus: "current", markedAt: observedAt },
    { conditionId: "0xcond-b", tokenId: "222", marketQuestion: "Will Bitcoin close above $70k in October?", outcome: "No", shares: "2500.000000", averageCost: "0.385000", bestBid: "0.412000", costBasis: "962.500000", liquidationValue: "1030.000000", unrealizedPnl: "67.500000", markStatus: "current", markedAt: observedAt },
  ],
  warnings: [], observedAt,
};

const paperFill = (n, kind, question, outcome, shares, price, gross, cash, pnl) => ({
  fillId: `77777777-7777-4777-8777-77777777777${n}`, kind, conditionId: "0xcond-a", tokenId: "111",
  marketQuestion: question, outcome, shares, averagePrice: price, grossNotional: gross,
  feeRate: "0.000000", fee: "0.500000", cashEffect: cash, realizedPnl: pnl ?? "0.000000",
  observedAt: `2026-09-0${n}T10:0${n}:00.000Z`, createdAt: `2026-09-0${n}T10:0${n}:00.000Z`,
});

const paperFills = {
  items: [
    paperFill(1, "BUY", "Will the Fed hold rates in September?", "Yes", "400.000000", "0.520000", "208.000000", "-208.500000", null),
    paperFill(2, "SELL", "Will Bitcoin close above $70k in October?", "No", "800.000000", "0.401000", "320.800000", "320.300000", "12.800000"),
  ],
  total: 2, offset: 0, limit: 20,
};

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
  events: [
    { eventId: "11111111-1111-4111-8111-111111111111", action: "STARTED", message: "Strategy started. Watching Yes in the background.", side: null, price: null, fillId: null, createdAt: "2026-09-01T23:00:00.000Z" },
    { eventId: "22222222-2222-4222-8222-222222222222", action: "BUY", message: "Bought 100 shares at 0.520.", side: "BUY", price: "0.520000", fillId: "77777777-7777-4777-8777-777777777771", createdAt: "2026-09-01T23:02:00.000Z" },
    { eventId: "33333333-3333-4333-8333-333333333333", action: "WAIT", message: "Best ask 0.615 above the exit band.", side: null, price: null, fillId: null, createdAt: "2026-09-01T23:05:00.000Z" },
  ],
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

const searchResponse = json({
  query: "", state: "active", observedAt,
  events: [{
    id: "e1", slug: "rates", title: "Rates", description: "Fixture event",
    endDate: null, liquidity: "100000", volume: "500000",
    markets: [
      searchMarket("0xcond-a", "100", "Will the Fed hold rates in September?", "0.61"),
      searchMarket("0xcond-b", "200", "Will Bitcoin close above $70k in October?", "0.41"),
    ],
  }],
});

const momentumConfig = {
  strategy: "momentum_v1", initialCapital: "10000", positionSizePct: "0.10",
  momentumWindowMinutes: 60, momentumThreshold: "0.05", takeProfit: "0.10", stopLoss: "0.05",
  maxHoldMinutes: 1440, cooldownMinutes: 60, slippage: "0.01", maxFillDelayMinutes: 5,
};
const meanRevConfig = {
  ...momentumConfig, strategy: "mean_reversion_v1",
  reversionWindowMinutes: 90, reversionThreshold: "0.04",
};
delete meanRevConfig.momentumWindowMinutes;
delete meanRevConfig.momentumThreshold;

const runDone = {
  runId: RUN_DONE, marketId: "0xcond-a", marketQuestion: "Will the Fed hold rates in September?",
  status: "completed", phase: "completed", progress: 100, config: momentumConfig,
  resolvedOutcome: "YES", datasetHash: "a".repeat(64), cancelRequested: false, failure: null,
  warnings: [], createdAt: "2026-09-01T09:00:00.000Z", startedAt: "2026-09-01T09:00:02.000Z", completedAt: "2026-09-01T09:03:41.000Z",
};
const runLive = {
  ...runDone, runId: RUN_LIVE, marketId: "0xcond-b", marketQuestion: "Will Bitcoin close above $70k in October?",
  status: "running", phase: "simulating", progress: 65, config: meanRevConfig, resolvedOutcome: null, datasetHash: null,
  completedAt: null,
};

const metrics = {
  initialCapital: "10000", finalEquity: "10842.30", pnl: "842.30", returnPct: "8.42",
  maxDrawdownPct: "3.10", tradeCount: 47, winRatePct: "57.45", profitFactor: "1.62",
  averageHoldingSeconds: "1840", exposurePct: "41.20", fees: "23.40", skippedSignals: 12,
  yesBuyHoldReturnPct: "12.30", noBuyHoldReturnPct: "-4.10",
};

const equityPoints = Array.from({ length: 60 }, (_, i) => {
  const drift = Math.sin(i / 9) * 40 + i * 14;
  const equity = 10000 + drift;
  const yes = 0.55 + Math.sin(i / 14) * 0.08;
  return { timestamp: new Date(Date.parse("2026-08-01T00:00:00Z") + i * 3_600_000).toISOString(), equity: equity.toFixed(4), yesPrice: yes.toFixed(4), noPrice: (1 - yes).toFixed(4) };
});

const backtestTrade = (i, pnl, reason) => ({
  tradeIndex: i, outcome: i % 2 ? "NO" : "YES",
  entryAt: new Date(Date.parse("2026-08-01T00:00:00Z") + i * 7_200_000).toISOString(),
  exitAt: new Date(Date.parse("2026-08-01T00:00:00Z") + i * 7_200_000 + 5_400_000).toISOString(),
  entryPrice: "0.5200", exitPrice: "0.5800", shares: "150.0000", entryFee: "0.7800", exitFee: "0.8700",
  pnl, exitReason: reason,
});

const thread = {
  threadId: THREAD, title: "Fed order book", createdAt: "2026-09-01T10:00:00.000Z",
  updatedAt: "2026-09-01T10:05:00.000Z", expiresAt: "2026-10-01T10:00:00.000Z",
};

const threadMessages = {
  threadId: THREAD,
  items: [
    { kind: "message", id: "m1", role: "user", text: "What does the order book on the Fed September meeting look like right now?" },
    { kind: "message", id: "m2", role: "assistant", text: "The Yes side is bid-heavy: top of book 0.61 / 0.62 with about 4,200 shares within two ticks. Implied probability has drifted up three points since this morning, mostly on the latest CPI print. Liquidity thins out past 0.66 — sweeping to 0.70 would move the mid by roughly a cent and a half." },
    { kind: "backtest", id: "b1", backtest: { kind: "backtest_run", runId: RUN_DONE, marketId: "0xcond-a", marketQuestion: "Will the Fed hold rates in September?", strategy: "mean_reversion_v1", status: "completed", phase: "completed", progress: 100, createdAt: "2026-09-01T10:04:00.000Z" } },
  ],
};

const alertChannel = {
  items: [{
    channelId: "77777777-7777-4777-8777-777777777777", kind: "discord", label: "Trading Discord",
    eventKinds: ["BUY", "SELL", "ERROR"], enabled: true, targetHint: "discord.com/api/webhooks/…ghij",
    createdAt: "2026-08-04T00:00:00.000Z", updatedAt: "2026-08-04T00:00:00.000Z",
  }],
};
const alertDeliveries = {
  items: [{
    deliveryId: "88888888-8888-4888-8888-888888888888", channelId: "77777777-7777-4777-8777-777777777777",
    channelLabel: "Trading Discord", channelKind: "discord", action: "BUY",
    message: "Bought 100 Yes shares at 0.520 on the paper ledger.",
    context: { marketQuestion: "Will the Fed hold rates in September?", outcome: "Yes", side: "BUY", price: "0.520" },
    status: "delivered", attempts: 1, lastError: null,
    createdAt: "2026-09-01T23:02:01.000Z", deliveredAt: "2026-09-01T23:02:01.400Z",
  }], limit: 20,
};

async function mockRoutes(page, { sessionActive }) {
  await page.route("**/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/v1/wallet-sessions/current") return sessionActive ? route.fulfill(json(session)) : notFound()(route);
    if (path === "/v1/account/overview") return sessionActive ? route.fulfill(json(accountOverview)) : notFound()(route);
    if (path === "/v1/agent/threads") return route.fulfill(json({ items: [thread] }));
    if (path === `/v1/agent/threads/${THREAD}/messages`) return route.fulfill(json(threadMessages));
    if (path === "/v1/paper/portfolio" || path === "/v1/paper/refresh") return route.fulfill(json(paperPortfolio));
    if (path.startsWith("/v1/paper/fills")) return route.fulfill(json(paperFills));
    if (path === "/v1/paper/strategy") return route.fulfill(json(runningStrategy));
    if (path === "/v1/paper/share") return route.fulfill(json({ token: null, enabled: false, createdAt: null, updatedAt: null }));
    if (path.startsWith("/v1/research/markets")) return route.fulfill(searchResponse);
    if (path === "/v1/backtests") return route.fulfill(json({ items: [runDone, runLive], activeCount: 1, activeLimit: 10 }));
    if (path === `/v1/backtests/${RUN_DONE}`) return route.fulfill(json({ run: runDone, result: { metrics, assumptions: ["One-minute CLOB history; maker fee 0 bps, taker fee 0 bps.", "Fills at the touched price with up to 1% slippage.", "No partial-fill modelling; positions settle at the resolved outcome."] } }));
    if (path === `/v1/backtests/${RUN_LIVE}`) return route.fulfill(json({ run: runLive, result: null }));
    if (path.startsWith("/v1/backtests") && path.endsWith("/trades")) return route.fulfill(json({ runId: RUN_DONE, items: [backtestTrade(1, "42.30", "take_profit"), backtestTrade(2, "-18.10", "stop_loss"), backtestTrade(3, "27.65", "take_profit")], total: 47, offset: 0, limit: 100 }));
    if (path.startsWith("/v1/backtests") && path.endsWith("/series")) return route.fulfill(json({ runId: RUN_DONE, points: equityPoints }));
    if (path === "/v1/alerts/channels") return route.fulfill(json(alertChannel));
    if (path.startsWith("/v1/alerts/deliveries")) return route.fulfill(json(alertDeliveries));
    console.log(`unmocked endpoint: ${route.request().method()} ${path}`);
    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "MOCKED", message: "mocked" } }) });
  });
  await page.route("https://polymarket.com/api/geoblock", (route) =>
    route.fulfill(json({ blocked: false, country: "US", region: "NY" })));
}

const browser = await chromium.launch({ channel: "chrome", headless: true });
const errors = [];

async function shoot(page, path, name, { fullPage = true, width = 1440, height = 1000, wait = 900 } = {}) {
  await page.setViewportSize({ width, height });
  await page.goto(`http://localhost:5173${path}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(wait);
  const file = `${outDir}/${name}${width < 800 ? "-mobile" : ""}.png`;
  await page.screenshot({ path: file, fullPage });
  console.log(`saved ${file}`);
}

// Context 1: signed in with an active wallet session and loaded account.
const ctx1 = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await ctx1.newPage();
page.on("pageerror", (error) => errors.push(String(error)));
page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
await mockRoutes(page, { sessionActive: true });

await shoot(page, "/chat", "chat-new");
await shoot(page, `/chat/${THREAD}`, "chat-thread");
await shoot(page, "/trades", "trades");
await shoot(page, "/paper", "paper");
await shoot(page, "/backtests", "backtests-list");
await shoot(page, `/backtests/${RUN_DONE}`, "backtest-run");
await shoot(page, "/settings", "settings");
await shoot(page, "/nope", "not-found");

// Mobile pass on the densest pages.
await shoot(page, "/paper", "paper", { width: 390, height: 844 });
await shoot(page, "/trades", "trades", { width: 390, height: 844 });
await shoot(page, "/chat", "chat-new", { width: 390, height: 844 });
await shoot(page, "/settings", "settings", { width: 390, height: 844 });
await ctx1.close();

// Context 2: expired/absent wallet session — the connect and empty states.
const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page2 = await ctx2.newPage();
page2.on("pageerror", (error) => errors.push(String(error)));
page2.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
await mockRoutes(page2, { sessionActive: false });
await shoot(page2, "/settings", "settings-connect");
await shoot(page2, "/trades", "trades-empty");
await ctx2.close();

// Public pages (no auth wrapper at all).
const ctx3 = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page3 = await ctx3.newPage();
page3.on("pageerror", (error) => errors.push(String(error)));
await shoot(page3, "/templates", "templates-landing");
await shoot(page3, "/templates", "templates-landing", { width: 390, height: 844 });
await ctx3.close();

await browser.close();
if (errors.length) {
  console.log("PAGE ERRORS:");
  for (const error of errors) console.log(`  ${error}`);
  process.exitCode = 1;
}
