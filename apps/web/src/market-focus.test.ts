/** @vitest-environment jsdom */

import { afterEach, describe, expect, it } from "vitest";

import { clearMarketFocus, loadMarketFocus, saveMarketFocus } from "./market-focus";

const market = {
  id: "market-1",
  conditionId: "0xcondition",
  slug: "fed-holds",
  question: "Will the Fed hold rates?",
  description: "Fixture market",
  outcomes: ["Yes", "No"],
  outcomePrices: ["0.62", "0.38"],
  clobTokenIds: ["100", "101"],
  active: true,
  closed: false,
  acceptingOrders: true,
  enableOrderBook: true,
  archived: false,
  restricted: false,
  minimumOrderSize: "5",
  minimumTickSize: "0.01",
  endDate: null,
  startDate: null,
  createdAt: null,
  closedTime: null,
  liquidity: "100000",
  volume: "500000",
};

afterEach(() => window.localStorage.clear());

describe("market focus", () => {
  it("stores a validated market and retrieves it only for its focus identifier", () => {
    saveMarketFocus(market);

    expect(loadMarketFocus("0xcondition")).toEqual(market);
    expect(loadMarketFocus("other-condition")).toBeNull();
  });

  it("fails closed on invalid storage and clears the focus", () => {
    window.localStorage.setItem("polytrade.market-focus", "not-json");
    expect(loadMarketFocus()).toBeNull();

    saveMarketFocus(market);
    clearMarketFocus();
    expect(loadMarketFocus()).toBeNull();
  });
});
