// Scratch screenshot pass for the public market pages. Pairs with
// market-visual-mock.mjs. Not committed — local harness only.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

mkdirSync("shots/market-pages", { recursive: true });

const base = "http://localhost:5176";
const targets = [
  { name: "browse-desktop", path: "/markets", width: 1440, height: 1000 },
  { name: "browse-mobile", path: "/markets", width: 390, height: 844 },
  { name: "detail-desktop", path: "/markets/fed-rates-september", width: 1440, height: 1400 },
  { name: "detail-mobile", path: "/markets/fed-rates-september", width: 390, height: 1400 },
  { name: "missing", path: "/markets/not-a-real-market", width: 1440, height: 800 },
];

const browser = await chromium.launch();
for (const target of targets) {
  const page = await browser.newPage({ viewport: { width: target.width, height: target.height } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.goto(`${base}${target.path}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(700);
  await page.screenshot({ path: `shots/market-pages/${target.name}.png`, fullPage: true });
  console.log(target.name, errors.length ? `console errors: ${errors.join(" | ")}` : "clean");
  await page.close();
}
await browser.close();