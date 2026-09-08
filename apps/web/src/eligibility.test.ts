import { describe, expect, it, vi } from "vitest";

import {
  checkBrowserEligibility,
  POLYMARKET_BROWSER_GEOBLOCK_URL,
} from "./eligibility";

describe("browser eligibility", () => {
  it("normalizes a valid Polymarket response", async () => {
    const request = vi.fn(async () => new Response(JSON.stringify({
      blocked: true,
      ip: "203.0.113.8",
      country: "au",
      region: "vic",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(checkBrowserEligibility(request as typeof fetch)).resolves.toMatchObject({
      blocked: true,
      verified: true,
      country: "AU",
      region: "VIC",
    });
    expect(request).toHaveBeenCalledWith(POLYMARKET_BROWSER_GEOBLOCK_URL, expect.objectContaining({
      credentials: "omit",
    }));
  });

  it("accepts the minimal response when optional diagnostic fields are absent", async () => {
    const request = vi.fn(async () => new Response(JSON.stringify({
      blocked: false,
      country: "nz",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(checkBrowserEligibility(request as typeof fetch)).resolves.toMatchObject({
      blocked: false,
      verified: true,
      country: "NZ",
      region: "",
    });
  });

  it.each([
    ["an upstream error", vi.fn(async () => new Response("unavailable", { status: 503 }))],
    ["a malformed response", vi.fn(async () => new Response(JSON.stringify({ blocked: false, country: "New Zealand" }), { status: 200 }))],
    ["a network failure", vi.fn(async () => { throw new TypeError("network unavailable"); })],
  ])("fails closed for %s", async (_label, request) => {
    await expect(checkBrowserEligibility(request as typeof fetch)).resolves.toMatchObject({
      blocked: true,
      verified: false,
      country: "",
      region: "",
    });
  });
});
