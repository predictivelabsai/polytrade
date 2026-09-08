// Playwright visual review for apps/web — run with:
//   node scripts/visual-review.mjs [state ...]
// States: connect (default) · blocked · unverified · session
// Requires the vite dev server on :5173 with VITE_E2E_AUTH_BYPASS=1.
// Gateway calls are mocked per state; screenshots land in test-results/.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const APP_URL = process.env.VISUAL_APP_URL ?? "http://localhost:5173";
const OUT_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "test-results", "visual");
const SESSION = {
  sessionId: "3f0d2b64-6d4e-4e8f-9a1c-2f5a7b8c9d01",
  walletAddress: "0x6326B505c0e0E8815d088b591219Ebf9Cd455bF5",
  signatureType: 0,
  idleExpiresAt: "2026-08-31T15:00:00.000Z",
  expiresAt: "2026-09-07T15:00:00.000Z",
};
const STATES = {
  connect: { geoblock: { blocked: false, verified: true, country: "EE", region: "01" } },
  proxy: { geoblock: { blocked: false, verified: true, country: "EE", region: "01" } },
  blocked: { geoblock: { blocked: true, verified: true, country: "AU", region: "VIC" } },
  unverified: { geoblock: { status: 500 } },
  session: { geoblock: { blocked: false, verified: true, country: "EE", region: "01" }, session: SESSION },
};

const stateNames = process.argv.slice(2).length ? process.argv.slice(2) : ["connect", "proxy", "blocked", "unverified", "session"];
mkdirSync(OUT_DIR, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
for (const name of stateNames) {
  const state = STATES[name];
  if (!state) throw new Error(`Unknown state: ${name}. Known: ${Object.keys(STATES).join(", ")}`);
  const context = await browser.newContext({ viewport: { width: 1460, height: 780 }, deviceScaleFactor: 1 });
  await context.route("https://api.polytrade.chat/**", async (route) => {
    const url = new URL(route.request().url());
    if (state.session && url.pathname === "/v1/wallet-sessions/current") {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.session) });
    }
    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "mocked" }) });
  });
  await context.route("https://polymarket.com/api/geoblock", async (route) => {
    const g = state.geoblock;
    if (g.status) return route.fulfill({ status: g.status, contentType: "application/json", body: "{}" });
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(g) });
  });
  const page = await context.newPage();
  await page.goto(`${APP_URL}/settings`, { waitUntil: "networkidle" });
  if (name === "proxy") {
    await page.selectOption("select[aria-label='Wallet type']", "1");
    await page.waitForTimeout(300);
    await page.click(".settings-help summary");
    await page.waitForTimeout(300);
  }
  await page.waitForTimeout(600); // fonts + toast settle
  const shot = join(OUT_DIR, `settings-${name}.png`);
  await page.screenshot({ path: shot, fullPage: true });
  console.log(`saved ${shot}`);
  await context.close();
}
await browser.close();