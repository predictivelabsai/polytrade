// One-off visual probe: /chat with mocked usage + stream to see the runtime picker.
import { chromium } from "playwright-core";
import assert from "node:assert/strict";

const APP_URL = "http://localhost:5199";
const THREAD_ID = "4f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d02";
const NOW = "2026-09-09T12:00:00.000Z";

const json = (status, body) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const err = (code, message) => ({ error: { code, message } });

const browser = await chromium.launch({ channel: "chrome" });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const streamRequests = [];
page.on("request", (request) => {
  if (new URL(request.url()).pathname.endsWith("/runs/stream") && request.method() === "POST") {
    const body = request.postData();
    streamRequests.push(JSON.parse(body));
    console.log("SSE request postData:", body);
  }
});

try {
await context.route("https://api.polytrade.chat/**", (route) => {
  const url = new URL(route.request().url());
  const p = url.pathname;
  if (p === "/v1/wallet-sessions/current") {
    return route.fulfill(json(404, err("NOT_FOUND", "No active wallet session")));
  }
  if (p === "/v1/paper/refresh") return route.fulfill(json(200, err("SKIP", "skip")));
  if (p === "/v1/paper/portfolio") return route.fulfill(json(200, err("SKIP", "skip")));
  if (p === "/v1/paper/fills") return route.fulfill(json(200, { items: [], total: 0 }));
  if (p === "/v1/paper/strategy") return route.fulfill(json(200, null));
  if (p === "/v1/agent/usage") {
    return route.fulfill(json(200, {
      fundingSource: "platform", used: 2, limit: 5, remaining: 3,
      costUsd: "0.012345", platformCostUsd: "0.012345", platformBudgetUsd: "5",
    }));
  }
  if (p === "/v1/agent/threads" && route.request().method() === "POST") {
    return route.fulfill(json(201, {
      threadId: THREAD_ID, title: "New chat", createdAt: NOW, updatedAt: NOW,
      expiresAt: "2026-10-09T00:00:00.000Z",
    }));
  }
  if (p.endsWith("/runs/stream")) {
    const body = [
      `event: run.started\ndata: {"runId":"r1","threadId":"${THREAD_ID}"}\n\n`,
      `event: usage.notice\ndata: {"kind":"quota_warning","message":"You have used 4 of 5 platform-funded queries today."}\n\n`,
      `event: runtime.fallback\ndata: {"from":"hermes","to":"deepseek","reason":"hermes_unavailable"}\n\n`,
      'event: message.started\ndata: {"messageId":"m1"}\n\n',
      'event: message.delta\ndata: {"messageId":"m1","textDelta":"DeepSeek answered after Hermes fell back."}\n\n',
      'event: run.completed\ndata: {"runId":"r1"}\n\n',
    ].join("");
    return route.fulfill({ status: 200, contentType: "text/event-stream", body });
  }
  return route.fulfill(json(200, { items: [], total: 0 }));
});

await page.goto(`${APP_URL}/chat`, { waitUntil: "domcontentloaded" });
const deepseekButton = page.getByRole("button", { name: "DeepSeek", exact: true });
const hermesButton = page.getByRole("button", { name: "Hermes", exact: true });
await deepseekButton.waitFor({ state: "visible" });
await hermesButton.waitFor({ state: "visible" });
await page.getByText("2 / 5 queries used today", { exact: true }).waitFor({ state: "visible" });
console.log("PASS: runtime toggle visible; 2 / 5 queries used today renders");
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: "test-results/walkthrough/chat-runtime-picker.png", fullPage: true });
console.log("saved test-results/walkthrough/chat-runtime-picker.png");

// Send with hermes picked
await hermesButton.click();
await page.getByRole("button", { name: "Hermes", exact: true, pressed: true }).waitFor({ state: "visible" });
assert.equal(await deepseekButton.getAttribute("aria-pressed"), "false");
console.log("PASS: Hermes selectable (aria-pressed=true)");
await page.screenshot({ path: "test-results/walkthrough/chat-hermes-selected.png", fullPage: false });
console.log("saved test-results/walkthrough/chat-hermes-selected.png");

const composer = page.getByRole("textbox", { name: "Ask PolyTrade", exact: true });
const message = "Check weather markets for London";
await composer.fill(message);
await composer.press("Enter");
await page.getByText("Hermes was unavailable, so DeepSeek answered.", { exact: true }).waitFor({ state: "visible" });
await page.getByText("DeepSeek answered after Hermes fell back.", { exact: true }).waitFor({ state: "visible" });
console.log("PASS: fallback notice and answer render");
assert.equal(streamRequests.length, 1);
assert.equal(streamRequests[0].message, message);
assert.equal(streamRequests[0].runtime, "hermes");
console.log("PASS: outgoing SSE request contains the message and runtime=hermes");
await page.screenshot({ path: "test-results/walkthrough/chat-after-fallback.png", fullPage: true });
console.log("saved test-results/walkthrough/chat-after-fallback.png");

} finally {
  await browser.close();
}
