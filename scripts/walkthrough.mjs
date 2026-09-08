// Playwright hand-on review walkthrough for apps/web — run with:
//   node scripts/walkthrough.mjs [scenario ...]
// Requires the vite dev server on :5173 with VITE_E2E_AUTH_BYPASS=1 (.env.local).
// Gateway endpoints are mocked per scenario from the @polytrade/contracts shapes.
// Prints a per-scenario verdict (assertions + request/console evidence) and saves
// screenshots to test-results/walkthrough/.
// Paper scenarios also capture three responsive layouts in shots/paper-bottom-fix/.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { paperFillsResponseSchema, paperShareStatusSchema, paperStrategySnapshotSchema } from "../packages/contracts/dist/index.js";

const APP_URL = process.env.WALKTHROUGH_APP_URL ?? "http://localhost:5173";
const OUT_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "test-results", "walkthrough");
const NOW = "2026-08-30T12:00:00.000Z";
const SESSION_ID = "3f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d01";
const THREAD_ID = "4f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d02";
const RUN_ID = "5f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d03";
const WALLET = "0x6326B505c0e0E8815d088b591219Ebf9Cd455bF5";

const SESSION = {
  sessionId: SESSION_ID,
  walletAddress: WALLET,
  signatureType: 0,
  idleExpiresAt: "2026-08-31T15:00:00.000Z",
  expiresAt: "2026-09-07T15:00:00.000Z",
};

const PORTFOLIO = {
  initialCash: "10000.000000",
  cash: "8350.420000",
  positionsValue: "1642.750000",
  equity: "9993.170000",
  realizedPnl: "-6.250000",
  unrealizedPnl: "12.500000",
  totalPnl: "6.250000",
  totalFees: "1.330000",
  positions: [
    {
      conditionId: "0xcond-a1", tokenId: "445400001",
      marketQuestion: "Will Alpha FC win the 2026 league title?", outcome: "YES",
      shares: "150.000000", costBasis: "118.500000", averageCost: "0.790000",
      bestBid: "0.840000", liquidationValue: "126.000000",
      unrealizedPnl: "7.500000", markStatus: "current", markedAt: NOW,
    },
    {
      conditionId: "0xcond-b2", tokenId: "450000002",
      marketQuestion: "Will the Fed cut rates in September?", outcome: "NO",
      shares: "600.000000", costBasis: "500.000000", averageCost: "0.833333",
      bestBid: null, liquidationValue: "0.000000",
      unrealizedPnl: "5.000000", markStatus: "unpriced", markedAt: null,
    },
  ],
  warnings: ["Mark price unavailable for one position."],
  observedAt: NOW,
};

function paperFills(total = 24, offset = 0, limit = 20) {
  const items = [];
  for (let i = 0; i < Math.min(limit, Math.max(0, total - offset)); i++) {
    const index = offset + i;
    items.push({
      fillId: "df0d2b64-6d4e-4e8f-9a1c-" + String(index).padStart(12, "0"),
      kind: index % 3 === 0 ? "SELL" : "BUY",
      origin: "manual",
      conditionId: "0xcond-a1",
      tokenId: "445400001",
      marketQuestion: "Will Alpha FC win the 2026 league title?",
      outcome: "YES",
      shares: "50.000000",
      averagePrice: "0.790000",
      grossNotional: "39.500000",
      feeRate: "0.000000",
      fee: "0.000000",
      cashEffect: index % 3 === 0 ? "39.500000" : "-39.500000",
      realizedPnl: "0.000000",
      observedAt: NOW,
      createdAt: NOW,
    });
  }
  return { items, total, offset, limit };
}

const emptyFills = { items: [], total: 0, offset: 0, limit: 20 };
const noStrategy = { strategy: null, events: [] };
const runningStrategy = paperStrategySnapshotSchema.parse({
  strategy: {
    strategyId: "77777777-7777-4777-8777-777777777777", conditionId: "0xcond-a1", tokenId: "445400001",
    marketQuestion: PORTFOLIO.positions[0].marketQuestion, outcome: "YES",
    entryPrice: "0.790000", exitPrice: "0.840000", sharesPerOrder: "50.000000", maxPosition: "400.000000",
    intervalSeconds: 15, status: "RUNNING", ordersPlaced: 4, scansCompleted: 96,
    lastAction: "WAIT", lastMessage: "Quote outside the band — waiting for the next scan.",
    lastQuoteSide: null, lastQuotePrice: null, lastScannedAt: NOW,
    nextScanAt: NOW, startedAt: NOW, stoppedAt: null, updatedAt: NOW,
  },
  events: [
    { eventId: "11111111-1111-4111-8111-111111111111", action: "WAIT", message: "Best ask above the entry band. Waiting for the next scan.", side: null, price: null, fillId: null, createdAt: NOW },
    { eventId: "22222222-2222-4222-8222-222222222222", action: "BUY", message: "Bought 50 YES shares at 0.790.", side: "BUY", price: "0.790000", fillId: paperFills().items[1].fillId, createdAt: NOW },
    { eventId: "33333333-3333-4333-8333-333333333333", action: "STARTED", message: "Strategy started. Watching YES in the background.", side: null, price: null, fillId: null, createdAt: NOW },
  ],
});
const enabledShare = paperShareStatusSchema.parse({
  token: "paper_bottom_fix_0123456789abcdef0123456789abc", enabled: true, createdAt: NOW, updatedAt: NOW,
});
paperFillsResponseSchema.parse(paperFills());
const OVERVIEW = {
  walletAddress: WALLET,
  positions: [], openOrders: [], fills: [],
  observedAt: NOW,
};
const THREADS = {
  items: [
    { threadId: THREAD_ID, title: "Rate cut odds research", createdAt: NOW, updatedAt: NOW, expiresAt: "2026-09-06" + "T12:00:00.000Z" },
  ],
};
const THREAD_ITEMS = {
  threadId: THREAD_ID,
  items: [
    { kind: "message", id: "u1", role: "user", text: "What are the odds of a September rate cut?" },
    { kind: "message", id: "a1", role: "assistant", text: "Currently the NO-SEP-CUT contract trades near 0.62. Ask me for a paper order and I can prepare one." },
  ],
};
const RUN = {
  runId: RUN_ID,
  marketId: "fed-cuts-september",
  marketQuestion: "Will the Fed cut rates in September?",
  status: "completed",
  phase: "completed",
  progress: 100,
  cancelRequested: false,
  warnings: [],
  config: {
    strategy: "momentum_v1", initialCapital: "10000", positionSizePct: "0.10",
    takeProfit: "0.10", stopLoss: "0.05", maxHoldMinutes: 1440, cooldownMinutes: 60,
    slippage: "0.01", maxFillDelayMinutes: 5, momentumWindowMinutes: 60, momentumThreshold: "0.05",
  },
  resolvedOutcome: "YES",
  datasetHash: "a".repeat(64),
  failure: null,
  createdAt: "2026-08-29T10:00:00.000Z",
  startedAt: "2026-08-29T10:00:05.000Z",
  completedAt: "2026-08-29T10:03:00.000Z",
};
const METRICS = {
  initialCapital: "10000.00000000", finalEquity: "10412.50000000",
  pnl: "412.50000000", returnPct: "4.12500000", maxDrawdownPct: "3.20000000",
  tradeCount: 12, winRatePct: "58.30000000", profitFactor: "1.42000000",
  averageHoldingSeconds: "5400.00000000", exposurePct: "42.50000000",
  fees: "9.75000000", skippedSignals: 3,
  yesBuyHoldReturnPct: "8.10000000", noBuyHoldReturnPct: "-8.40000000",
};
const RUN_ENVELOPE = {
  run: RUN,
  result: {
    metrics: METRICS,
    assumptions: ["Filled at the mid price with slippage applied.", "Fees charged at 20 bps."],
  },
};
const EMPTY_LIST = { items: [], activeCount: 0, activeLimit: 3 };
const TRADES = {
  runId: RUN_ID, total: 3, offset: 0, limit: 50,
  items: [0, 1, 2].map((i) => ({
    tradeIndex: i, outcome: i === 2 ? "NO" : "YES",
    entryAt: "2026-08-28T0" + (i + 1) + ":00:00.000Z",
    exitAt: "2026-08-28T0" + (i + 1) + ":30:00.000Z",
    entryPrice: "0.62000000", exitPrice: "0.71000000",
    shares: "1200.00000000", entryFee: "0.74400000", exitFee: "0.85200000",
    pnl: "80.00000000", exitReason: i === 2 ? "stop_loss" : "take_profit",
  })),
};
const SERIES = {
  runId: RUN_ID,
  points: [0, 1, 2, 3, 4, 5].map((i) => ({
    timestamp: "2026-08-28T0" + i + ":00:00.000Z",
    yesPrice: (0.6 + i * 0.03).toFixed(2),
    noPrice: (0.4 - i * 0.02).toFixed(2),
    equity: (10000 + i * 15).toFixed(2),
  })),
};
const PROPOSAL = {
  action: "create", execution: "GTC", tokenId: "445400001",
  marketId: "alpha-fc-cuts-september", marketQuestion: "Will the Fed cut rates in September?",
  outcome: "YES", side: "BUY", rationale: "Mid-market momentum favors a cut before the meeting.",
  observedAt: NOW, price: "0.34", size: "100", postOnly: false,
};
const SSE_PROPOSAL = [
  "event: run.started\ndata: {}\n\n",
  "event: message.started\ndata: {\"messageId\":\"m1\"}\n\n",
  "event: message.delta\ndata: {\"messageId\":\"m1\",\"textDelta\":\"I can prepare an order. Here is a first draft for review.\"}\n\n",
  "event: proposal.created\ndata: " + JSON.stringify({ proposalId: "p1", envelope: { proposal: PROPOSAL, expiresAt: "2026-08-30T12:10:00.000Z" } }) + "\n\n",
  "event: run.completed\ndata: {}\n\n",
].join("");

// ---------- scenario route sets ----------
const json = (status, body) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const err = (status, code, message) => ({ status, code, message });

const base = async (route) => {
  const url = new URL(route.request().url());
  const path = url.pathname;
  if (path === "/v1/wallet-sessions/current") return route.fulfill(json(200, SESSION));
  if (path === "/v1/account/overview") return route.fulfill(json(200, OVERVIEW));
  if (path === "/v1/agent/threads" && route.request().method() === "GET") return route.fulfill(json(200, THREADS));
  if (path === `/v1/agent/threads/${THREAD_ID}/messages`) return route.fulfill(json(200, THREAD_ITEMS));
  return route.fulfill(json(404, err("NOT_FOUND", "not mocked")));
};

async function paperLayoutSnapshots(page, t, name) {
  const outDir = join(dirname(fileURLToPath(import.meta.url)), "..", "shots", "paper-bottom-fix");
  mkdirSync(outDir, { recursive: true });
  await page.locator(".share-pill-on").waitFor();
  await page.evaluate(() => document.fonts.ready);
  for (const viewport of [{ width: 1460, height: 900 }, { width: 1024, height: 800 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const layout = await page.evaluate(() => {
      const data = document.querySelector(".paper-data-column");
      const share = document.querySelector(".share-card");
      const panels = ["market-panel", "ticket", "strategy", "holdings-panel", "fills-panel"].map((name) => document.querySelector(`.paper-${name}`));
      panels.push(share);
      const tops = panels.map((panel) => panel.getBoundingClientRect().top);
      return {
        scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth,
        shareWidth: share.getBoundingClientRect().width,
        shareLastInData: data.lastElementChild === share && share.previousElementSibling.matches(".paper-fills-panel"),
        dataDisplay: getComputedStyle(data).display,
        ticketDisplay: getComputedStyle(document.querySelector(".paper-ticket-column")).display,
        shareArea: getComputedStyle(share).gridArea,
        stackedOrder: tops.every((top, index) => index === 0 || top > tops[index - 1]),
      };
    });
    const label = `${name}-${viewport.width}x${viewport.height}`;
    t.note(`${label}: ${JSON.stringify(layout)}`);
    t.assert(layout.scrollWidth === layout.clientWidth, `${label}: no horizontal overflow`);
    t.assert(layout.shareLastInData, `${label}: share follows fills in the data column`);
    if (viewport.width < 1200) {
      t.assert(layout.dataDisplay === "contents" && layout.ticketDisplay === "contents" && layout.shareArea.startsWith("share") && layout.stackedOrder,
        `${label}: named grid areas preserve market/ticket/strategy/holdings/fills/share order`);
    }
    await page.screenshot({ path: join(outDir, `${label}.png`), fullPage: true });
    t.note(`saved shots/paper-bottom-fix/${label}.png`);
  }
  await page.setViewportSize({ width: 1460, height: 900 });
}

const SCENARIOS = {
  "paper-ok": {
    route: async (route) => {
      const url = new URL(route.request().url());
      const p = url.pathname;
      if (p === "/v1/paper/refresh") return route.fulfill(json(200, PORTFOLIO));
      if (p === "/v1/paper/portfolio") return route.fulfill(json(200, PORTFOLIO));
      if (p === "/v1/paper/fills") return route.fulfill(json(200, paperFills(24, Number(url.searchParams.get("offset") ?? 0))));
      if (p === "/v1/paper/strategy") return route.fulfill(json(200, noStrategy));
      if (p === "/v1/paper/share") return route.fulfill(json(200, enabledShare));
      return base(route);
    },
    steps: async (page, t) => {
      await page.goto(`${APP_URL}/paper`, { waitUntil: "networkidle" });
      const seen = [];
      page.on("request", (r) => { if (r.url().includes("/v1/paper/fills")) seen.push(r.url()); });
      await page.waitForTimeout(400);
      await t.snapshot("paper-ok", "paper dashboard with data");
      await page.click("button[aria-label='Next paper fills']");
      await page.waitForTimeout(600);
      const nextHasOffset = seen.some((u) => u.includes("offset=20"));
      await t.assert(nextHasOffset, "fills Next button requests offset=20", seen.join("\n"));
      await page.click("button[aria-label='Previous paper fills']");
      await page.waitForTimeout(500);
      await t.snapshot("paper-ok", "paper dashboard back on page 1");
      await paperLayoutSnapshots(page, t, "paper-ok");
    },
  },
  "paper-strategy": {
    route: async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/v1/paper/strategy") return route.fulfill(json(200, runningStrategy));
      // A short ledger exposes the tall automation rail and the bottom-left gap.
      if (url.pathname === "/v1/paper/fills") return route.fulfill(json(200, paperFills(4)));
      return SCENARIOS["paper-ok"].route(route);
    },
    steps: async (page, t) => {
      await page.goto(`${APP_URL}/paper`, { waitUntil: "networkidle" });
      await page.locator(".paper-strategy-running").waitFor();
      await paperLayoutSnapshots(page, t, "paper-strategy");
    },
  },
  "paper-error": {
    route: async (route) => {
      const url = new URL(route.request().url());
      const p = url.pathname;
      if (p === "/v1/paper/refresh") return route.fulfill(json(500, err("PAPER_ERROR", "Paper engine request failed")));
      if (p === "/v1/paper/fills") return route.fulfill(json(200, emptyFills));
      if (p === "/v1/paper/strategy") return route.fulfill(json(200, noStrategy));
      return base(route);
    },
    steps: async (page, t) => {
      await page.goto(`${APP_URL}/paper`, { waitUntil: "networkidle" });
      await page.waitForSelector(".paper-load-failed", { timeout: 8000 });
      const body = await page.textContent("body");
      await t.assert(body.includes("Paper ledger could not be loaded"), "failed initial load renders a retryable error panel", "body: " + body.match(/Paper ledger[\s\S]{0,120}/)?.[0]);
      await t.assert(!body.includes("Practice against the live public order book"), "no fake zeroed ledger is rendered behind the failure", "ledger copy must not appear");
      await t.snapshot("paper-error", "paper dashboard after failed initial load");
      // recovery
      await page.route("**/v1/paper/refresh", (r) => r.fulfill(json(200, PORTFOLIO)));
      await page.click(".paper-load-failed button");
      await page.waitForSelector(".paper-page", { timeout: 8000 });
      await t.snapshot("paper-error-recovered", "paper dashboard after successful retry");
    },
  },
  "editor-typing": {
    route: async (route) => {
      const url = new URL(route.request().url());
      const p = url.pathname;
      if (p.endsWith("/runs/stream")) {
        return route.fulfill({ status: 200, contentType: "text/event-stream", body: SSE_PROPOSAL });
      }
      return base(route);
    },
    steps: async (page, t) => {
      await page.goto(`${APP_URL}/chat`, { waitUntil: "networkidle" });
      const composer = page.locator(".composer textarea, .composer input").first();
      await composer.fill("Draft a buy order");
      await page.click(".composer button[type='submit']");
      await page.getByLabel("Limit price").waitFor({ timeout: 8000 });
      await t.snapshot("editor-typing-before", "proposal ticket before editing");
      const price = page.getByLabel("Limit price");
      await price.click();
      await page.keyboard.press("Control+a");
      await page.keyboard.type("0.55", { delay: 120 });
      const value = await price.inputValue();
      await t.assert(value === "0.55", `price field accepts decimal typing; got "${value}"`, `expected 0.55`);
      await t.snapshot("editor-typing-after", `price field value after typing: ${value}`);
      // invalid intermediate state: shows inline error and keeps the sign button gated
      await price.click();
      await page.keyboard.press("Control+a");
      await page.keyboard.type("0.", { delay: 120 });
      const invalidValue = await price.inputValue();
      const signDisabled = await page.getByRole("button", { name: /Sign and place real order/ }).isDisabled();
      const noteVisible = (await page.textContent("body")).includes("Fix the highlighted fields");
      await t.assert(invalidValue === "0." && signDisabled && noteVisible,
        `mid-decimal state kept on screen ("${invalidValue}"), sign gated: ${signDisabled}, note shown: ${noteVisible}`,
        "pre-fix behavior: text snapped back or silently composed a wrong price");
      await t.snapshot("editor-typing-invalid", "invalid mid-decimal state with inline error");
    },
  },
  "backtests-error": {
    route: async (route) => {
      if (new URL(route.request().url()).pathname === "/v1/backtests") return route.fulfill(json(500, err("BACKTEST_ERROR", "Backtest storage unavailable")));
      return base(route);
    },
    steps: async (page, t) => {
      let listRequests = 0;
      page.on("request", (r) => { if (new URL(r.url()).pathname === "/v1/backtests") listRequests++; });
      await page.goto(`${APP_URL}/backtests`, { waitUntil: "networkidle" });
      await page.waitForSelector(".run-empty-error", { timeout: 8000 });
      const body1 = await page.textContent("body");
      await t.assert(body1.includes("Backtests are unreachable"), "list-load failure renders an inline 'Backtests are unreachable' panel", "body: " + body1.match(/Backtests are unreachable[\s\S]{0,120}/)?.[0]);
      await t.assert(!body1.includes("No backtests yet"), "'No backtests yet' is NOT shown while the library is only unreachable", "empty-state copy must not appear");
      await t.snapshot("backtests-error", "backtests page after list load failure");
      const dismiss = page.locator("button[aria-label='Dismiss message']");
      if ((await dismiss.count()) > 0) {
        await dismiss.first().click();
        await page.waitForTimeout(4_000);
        const reCount = await page.locator("button[aria-label='Dismiss message']").count();
        await t.assert(reCount === 0, "dismissed error toast does not return while the failure episode continues", reCount > 0 ? "poll's catch handler re-armed the toast" : "one toast per failure episode");
      } else {
        t.note("no toast dismissal to test re-arm");
      }
      await page.waitForTimeout(5_500);
      const body2 = await page.textContent("body");
      await t.assert(body2.includes("Backtests are unreachable"), "inline panel persists across further poll ticks", "");
      t.note(`list polls in 15s window: ${listRequests}`);
      await t.snapshot("backtests-error-10s", "backtests page after list load failure + 10s");
    },
  },
  "backtests-completed": {
    route: async (route) => {
      const url = new URL(route.request().url());
      const p = url.pathname;
      if (p === "/v1/backtests") return route.fulfill(json(200, { items: [RUN], activeCount: 0, activeLimit: 3 }));
      if (p === `/v1/backtests/${RUN_ID}`) return route.fulfill(json(200, RUN_ENVELOPE));
      if (p === `/v1/backtests/${RUN_ID}/series`) return route.fulfill(json(200, SERIES));
      if (p === `/v1/backtests/${RUN_ID}/trades`) {
        return route.fulfill(json(200, { runId: RUN_ID, items: [], total: 3, offset: Number(url.searchParams.get("offset")), limit: 50 }));
      }
      return base(route);
    },
    steps: async (page, t) => {
      let downloads = 0;
      page.on("request", (r) => { if (new URL(r.url()).pathname.includes(`/v1/backtests/${RUN_ID}/series`)) downloads++; });
      await page.goto(`${APP_URL}/backtests/${RUN_ID}`, { waitUntil: "networkidle" });
      await page.waitForTimeout(7_500);
      await t.assert(downloads === 1, `completed run's series fetched exactly once within 7.5s while idle (got ${downloads})`, downloads > 1 ? "settled run's payloads re-fetched every 3s tick forever" : "settled-run skip took effect");
      await t.snapshot("backtests-completed", "completed backtest detail");
    },
  },
  "backtests-404": {
    route: async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p === "/v1/backtests") return route.fulfill(json(200, EMPTY_LIST));
      if (p.startsWith("/v1/backtests/")) return route.fulfill(json(404, err("NOT_FOUND", "Backtest not found")));
      return base(route);
    },
    steps: async (page, t) => {
      let poll404 = 0;
      page.on("request", (r) => { const p2 = new URL(r.url()).pathname; if (p2.startsWith(`/v1/backtests/${RUN_ID}`)) poll404++; });
      await page.goto(`${APP_URL}/backtests/${RUN_ID}`, { waitUntil: "networkidle" });
      await page.waitForSelector(".stage-missing", { timeout: 8000 });
      const body = await page.textContent("body");
      await t.assert(body.includes("Backtest not found"), "deep-link to a missing run renders a 'Backtest not found' panel", "body: " + body.match(/Backtest not found[\s\S]{0,120}/)?.[0]);
      await page.waitForTimeout(7_000);
      t.note(`detail requests to /v1/backtests/${RUN_ID} in 15s: ${poll404}`);
      await t.assert(poll404 <= 2, `missing run detail requested at most twice in 15s (got ${poll404})`, poll404 > 2 ? "404 re-requested every tick" : "missing-run skip took effect");
      await t.snapshot("backtests-404", "deep link to deleted/foreign backtest");
      await page.click(".stage-missing button");
      await page.waitForURL("**/backtests");
      await t.assert(true, "'Back to all backtests' navigates off the dead runId", "");
    },
  },
  "chat-lost": {
    route: async (route) => {
      const url = new URL(route.request().url());
      const p = url.pathname;
      if (p === "/v1/agent/threads" && route.request().method() === "POST") {
        return route.fulfill(json(503, err("AGENT_UNAVAILABLE", "Agent service is temporarily unavailable")));
      }
      return base(route);
    },
    steps: async (page, t) => {
      await page.goto(`${APP_URL}/chat`, { waitUntil: "networkidle" });
      // /chat deep-links into the latest thread; a first message that must
      // CREATE a thread only happens from the unsaved new-chat route.
      await page.click("text=New chat");
      await page.waitForTimeout(600);
      const composer = page.locator(".composer textarea, .composer input").first();
      const text = "What are the odds of a September rate cut?";
      await composer.fill(text);
      await page.click(".composer button[type='submit']");
      await page.waitForTimeout(2_500);
      const composerValue = await composer.inputValue();
      await t.assert(composerValue === text, `composer restores the typed text when thread creation fails (kept: "${composerValue.slice(0, 40)}…")`, composerValue === "" ? "text was silently discarded" : "text returned to the composer");
      await t.snapshot("chat-lost", "chat after first-message failure");
    },
  },
};

// ---------- runner ----------
const browser = await chromium.launch({ channel: "chrome", headless: true });
mkdirSync(OUT_DIR, { recursive: true });
const names = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(SCENARIOS);
const failures = [];
for (const name of names) {
  const scenario = SCENARIOS[name];
  if (!scenario) throw new Error(`Unknown scenario: ${name}. Known: ${Object.keys(SCENARIOS).join(", ")}`);
  const results = [];
  const t = {
    assert(ok, finding, evidence) {
      results.push({ pass: ok, finding });
      if (!ok) failures.push(`${name}: ${finding}`);
      console.log(`  ${ok ? "PASS" : "EXPECTED-BUG"} ${finding}`);
    },
    note(text) { console.log(`  note  ${text}`); },
    async snapshot(label, description) {
      await page.screenshot({ path: join(OUT_DIR, `${label}.png`), fullPage: true });
      console.log(`  shot  ${label}.png — ${description}`);
    },
  };
  console.log(`\n== ${name} ==`);
  const context = await browser.newContext({ viewport: { width: 1460, height: 900 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  page.on("pageerror", (e) => console.log(`  jserr ${String(e).slice(0, 160)}`));
  page.on("console", (m) => { if (m.type() === "error") console.log(`  console ${m.text().slice(0, 160)} ${m.location().url}`); });
  await context.route("**/v1/**", scenario.route);
  await context.route("https://polymarket.com/api/geoblock", (r) => r.fulfill(json(200, { blocked: false, verified: true, country: "EE", region: "01" })));
  try {
    await scenario.steps(page, t);
  } catch (e) {
    results.push({ pass: false, finding: "scenario error" });
    failures.push(`${name}: ${e}`);
    console.log(`  ERROR ${e}`);
  }
  await context.close();
}
await browser.close();
console.log(failures.length ? `\n${failures.length} confirmed issue(s).` : "\nAll scenarios behaved as expected.");
