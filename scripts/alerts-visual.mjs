// Scratch visual review for the Settings "Strategy alerts" card. Not committed.
// Requires the vite dev server on :5173 with VITE_E2E_AUTH_BYPASS=1 (.env.local).
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const OUT_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "test-results", "alerts-visual");
mkdirSync(OUT_DIR, { recursive: true });

const CHANNEL_ID = "7f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d07";
const NOW = "2026-09-01T12:00:00.000Z";
const WALLET = "0x6326B505c0e0E8815d088b591219Ebf9Cd455bF5";

const createdChannels = [
  {
    channelId: CHANNEL_ID,
    kind: "discord",
    label: "Trading Discord",
    eventKinds: ["BUY", "SELL", "ERROR"],
    enabled: true,
    targetHint: "discord.com/api/webhooks/…ghij",
    createdAt: NOW,
    updatedAt: NOW,
  },
];

const deliveries = [
  {
    deliveryId: "8f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d08",
    channelId: CHANNEL_ID,
    channelLabel: "Trading Discord",
    channelKind: "discord",
    action: "BUY",
    message: "Bought 10.000000 shares at 0.420000 (limit 0.420000)",
    context: { marketQuestion: "Will the Fed cut rates in September?", outcome: "Yes", side: "BUY", price: "0.420000" },
    status: "delivered",
    attempts: 1,
    lastError: null,
    createdAt: "2026-09-01T11:58:00.000Z",
    deliveredAt: "2026-09-01T11:58:01.000Z",
  },
  {
    deliveryId: "8f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d09",
    channelId: CHANNEL_ID,
    channelLabel: "Trading Discord",
    channelKind: "discord",
    action: "ERROR",
    message: "Scan failed: upstream price feed timed out",
    context: { marketQuestion: "Will the Fed cut rates in September?", outcome: "Yes", side: null, price: null },
    status: "pending",
    attempts: 2,
    lastError: "discord delivery failed with status 500",
    createdAt: "2026-09-01T11:44:00.000Z",
    deliveredAt: null,
  },
];

const json = (value, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(value),
});

const session = {
  sessionId: "3f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d01",
  walletAddress: WALLET,
  signatureType: 0,
  idleExpiresAt: "2026-08-31T15:00:00.000Z",
  expiresAt: "2026-09-07T15:00:00.000Z",
};

const portfolio = {
  initialCash: "10000.000000", cash: "10000.000000", positionsValue: "0.000000",
  equity: "10000.000000", realizedPnl: "0.000000", unrealizedPnl: "0.000000",
  totalPnl: "0.000000", totalFees: "0.000000", positions: [], warnings: [], observedAt: NOW,
};

const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1460, height: 900 }, deviceScaleFactor: 1 });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });

const createdRequests = [];
await context.route("https://api.polytrade.chat/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  const method = request.method();

  if (path === "/v1/wallet-sessions/current") {
    return route.fulfill(json({
      sessionId: session.sessionId,
      walletAddress: WALLET,
      signatureType: 0,
      idleExpiresAt: session.idleExpiresAt,
      expiresAt: session.expiresAt,
    }));
  }
  if (path === "/v1/account/overview") {
    return route.fulfill(json({ walletAddress: WALLET, positions: [], openOrders: [], fills: [], observedAt: NOW }));
  }
  if (path === "/v1/paper/portfolio" || path === "/v1/paper/refresh") return route.fulfill(json(portfolio));
  if (path === "/v1/paper/strategy") return route.fulfill(json({ strategy: null, events: [] }));
  if (path === "/v1/paper/fills") return route.fulfill(json({ items: [], total: 0, limit: 20, offset: 0 }));
  if (path === "/v1/backtests") return route.fulfill(json({ items: [] }));
  if (path === "/v1/agent/threads") return route.fulfill(json({ items: [] }));

  if (path === "/v1/alerts/channels" && request.method() === "GET") {
    return route.fulfill(json({ items: createdChannels }));
  }
  if (path === "/v1/alerts/channels" && request.method() === "POST") {
    const body = JSON.parse(request.postData() ?? "{}");
    createdRequests.push(body);
    const created = {
      channelId: "9f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d09",
      kind: body.kind,
      label: body.label,
      eventKinds: body.eventKinds,
      enabled: true,
      targetHint: body.kind === "telegram" ? `chat ${body.target}` : `discord.com/api/webhooks/…${body.target.slice(-4)}`,
      createdAt: NOW,
      updatedAt: NOW,
    };
    createdChannels.unshift(created);
    return route.fulfill(json(created));
  }
  if (path.endsWith("/test")) {
    return route.fulfill(json({ status: "sent", error: null }));
  }
  if (request.method() === "DELETE" && path.startsWith("/v1/alerts/channels/")) {
    const channelId = path.split("/").at(-1);
    const index = createdChannels.findIndex((channel) => channel.channelId === channelId);
    if (index >= 0) createdChannels.splice(index, 1);
    return route.fulfill({ status: 204, contentType: "application/json", body: "" });
  }
  if (path === "/v1/alerts/deliveries") return route.fulfill(json({ items: deliveries, limit: 20 }));

  return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
});

await page.goto("http://localhost:5173/settings", { waitUntil: "networkidle" });
await page.waitForTimeout(700);
await page.screenshot({ path: join(OUT_DIR, "alerts-settings.png"), fullPage: true });

// Exercise the add-channel flow end to end.
await page.selectOption("select[aria-label='Alert channel type']", "telegram");
await page.fill("input[aria-label='Alert channel label']", "Phone Telegram");
await page.fill("input[aria-label='Alert channel target']", "-1001234567890");
await page.click("role=button[name=/Add channel/i]");
await page.waitForTimeout(700);
await page.screenshot({ path: join(OUT_DIR, "alerts-after-add.png"), fullPage: true });

// Add-form validation: invalid discord target shows the hint and no request.
await page.selectOption("select[aria-label='Alert channel type']", "discord");
await page.fill("input[aria-label='Alert channel label']", "Bad channel");
await page.fill("input[aria-label='Alert channel target']", "https://evil.example/x");
await page.click("role=button[name=/Add channel/i]");
await page.waitForTimeout(400);
await page.screenshot({ path: join(OUT_DIR, "alerts-invalid-target.png"), fullPage: true });

console.log("created requests:", JSON.stringify(createdRequests));
console.log(errors.length ? `console errors: ${errors.join(" | ")}` : "console clean");
await browser.close();