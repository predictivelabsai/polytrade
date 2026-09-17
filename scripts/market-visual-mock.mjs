// Scratch visual-review mock for the public market pages. Serves the four
// /v1/public endpoints with fixture data so screenshots don't need Gamma, a
// database, or Clerk. Not committed — local harness only.
import { createServer } from "node:http";

const observedAt = new Date().toISOString();

const markets = [
  ["1", "condition-fed", "fed-rates-september", "Will the Fed cut rates in September?", ["Yes", "No"], ["0.435", "0.565"], ["111", "222"], "https://polymarket-upload.s3.amazonaws.com/fed.png", "312400", "50000", "1200000", "2026-09-16T00:00:00.000Z"],
  ["2", "condition-nfl", "eagles-win-week-1", "Will the Eagles win their season opener?", ["Yes", "No"], ["0.62", "0.38"], ["333", "444"], "", "148900", "22000", "640000", "2026-09-08T00:00:00.000Z"],
  ["3", "condition-eth", "eth-above-5k-september", "Will ETH be above $5,000 at any point in September?", ["Yes", "No"], ["0.28", "0.72"], ["555", "666"], "", "96500", "310000", "4100000", "2026-09-30T23:59:00.000Z"],
  ["4", "condition-btc", "btc-above-90k-october", "Will Bitcoin hit $90k by end of October?", ["Yes", "No"], ["0.51", "0.49"], ["777", "888"], "", "88100", "180000", "2600000", "2026-10-31T23:59:00.000Z"],
  ["5", "condition-gov", "government-shutdown-october", "Will there be a government shutdown in October?", ["Yes", "No"], ["0.19", "0.81"], ["999", "aaa"], "", "74300", "96000", "780000", "2026-10-01T00:00:00.000Z"],
  ["6", "condition-nobel", "pope-wins-nobel", "Will the Pope win the 2026 Nobel Peace Prize?", ["Yes", "No"], ["0.04", "0.96"], ["bbb", "ccc"], "", "51200", "14000", "310000", "2026-10-09T00:00:00.000Z"],
];

const [_, fedSlugQuestions] = markets.reduce((acc, m) => acc, [[], new Map()]);
fedSlugQuestions.set("fed-rates-september", "Will the Fed cut rates in September?");

function summary(cols) {
  const [id, conditionId, slug, question, outcomes, outcomePrices, clobTokenIds, icon, volume24hr, liquidity, volume, endDate] = cols;
  return {
    id, conditionId, slug, question, outcomes, outcomePrices, clobTokenIds,
    active: true, closed: false, acceptingOrders: true,
    endDate, liquidity, volume,
    ...(icon ? { icon } : {}),
    volume24hr,
  };
}

function market(cols) {
  return {
    ...summary(cols),
    description:
      "This market will resolve to Yes if the Federal Open Market Committee announces a reduction in the target range for the federal funds rate at its September meeting. Resolution source: Federal Reserve statements.",
    enableOrderBook: true, archived: false, restricted: true,
    minimumOrderSize: "5", minimumTickSize: "0.01",
    startDate: "2026-08-01T00:00:00.000Z", createdAt: null, closedTime: null,
  };
}

const book = (tokenId) => ({
  tokenId,
  minimumOrderSize: "5",
  tickSize: "0.01",
  negativeRisk: false,
  lastTradePrice: tokenId === "111" ? "0.44" : "0.565",
  bids: tokenId === "111"
    ? [{ price: "0.42", size: "500" }, { price: "0.41", size: "1200" }, { price: "0.40", size: "8400" }, { price: "0.39", size: "15000" }, { price: "0.38", size: "26000" }]
    : [{ price: "0.55", size: "300" }, { price: "0.54", size: "900" }, { price: "0.53", size: "2100" }],
  asks: tokenId === "111"
    ? [{ price: "0.46", size: "300" }, { price: "0.47", size: "2400" }, { price: "0.48", size: "5100" }, { price: "0.49", size: "9800" }, { price: "0.50", size: "21000" }]
    : [{ price: "0.58", size: "700" }, { price: "0.59", size: "1800" }, { price: "0.60", size: "4200" }],
  observedAt,
});

const history = (tokenId) => ({
  tokenId,
  interval: "1d",
  points: Array.from({ length: 36 }, (_, i) => {
    const trend = 0.38
      + 0.10 * Math.sin(i / 5)
      + i * 0.0018
      + (i % 7 === 0 ? -0.03 : 0)
      + (i % 11 === 0 ? 0.045 : 0);
    return { timestamp: Date.parse("2026-08-27T00:00:00Z") + i * 8 * 3600_000, price: Math.min(0.97, Math.max(0.03, trend)).toFixed(3) };
  }).filter((_, i) => i % 2 === 0 || tokenId === "111"),
  observedAt,
});

const tokens = { "111": ["Yes", 0.44], "222": ["No", 0.565], "333": ["Yes", 0.62], "444": ["No", 0.38] };

const quotes = (slug) => {
  const cols = markets.find((m) => m[2] === slug) ?? markets[0];
  const yes = cols[2] === "eth-above-5k-september";
  return (yes
    ? [{ outcome: "Yes", tokenId: "555", price: null, bestBid: null, bestAsk: null, source: "gamma" }]
    : [
      { outcome: "Yes", tokenId: "111", price: "0.44", bestBid: "0.42", bestAsk: "0.46", source: "order-book" },
      { outcome: "No", tokenId: "222", price: "0.565", bestBid: "0.55", bestAsk: "0.58", source: "order-book" },
    ]);
};

createServer((req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "*");
  res.setHeader("Content-Type", "application/json");
  if (req.method === "OPTIONS") { res.statusCode = 204; return res.end(); }
  const url = new URL(req.url, "http://localhost");
  const json = (value) => res.end(JSON.stringify(value));

  if (url.pathname === "/v1/public/markets") {
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 12);
    return json({
      markets: markets.slice(offset, offset + limit).map(summary),
      limit, offset, hasMore: offset + limit < markets.length, observedAt,
    });
  }
  const slugMatch = url.pathname.match(/^\/v1\/public\/markets\/([^/]+)$/);
  if (slugMatch) {
    const slug = decodeURIComponent(slugMatch[1]);
    const cols = markets.find((m) => m[2] === slug);
    if (!cols) { res.statusCode = 404; return json({ error: { code: "NOT_FOUND", message: "Public market not found" } }); }
    return json({ market: market(cols), quotes: quotes(slug), observedAt });
  }
  const bookMatch = url.pathname.match(/^\/v1\/public\/order-books\/([^/]+)$/);
  if (bookMatch) {
    const tokenId = decodeURIComponent(bookMatch[1]);
    if (!tokens[tokenId] && tokenId !== "555") { res.statusCode = 404; return json({ error: { code: "NOT_FOUND", message: "Order book not found" } }); }
    if (tokenId === "555") return json(book("555"));
    return json(book(tokenId));
  }
  const histMatch = url.pathname.match(/^\/v1\/public\/price-history\/([^/]+)$/);
  if (histMatch) {
    const tokenId = decodeURIComponent(histMatch[1]);
    return json({ ...history(tokenId), tokenId });
  }
  res.statusCode = 404;
  res.end("{}");
}).listen(4100, () => console.log("mock on :4100"));