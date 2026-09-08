import { describe, expect, it } from "vitest";

import { parseConfig } from "../src/config.js";

function environment(overrides: NodeJS.ProcessEnv = {}): NodeJS.ProcessEnv {
  return {
    NODE_ENV: "test",
    DATABASE_URL: "postgresql://test:test@db.example.test:5432/polytrade?sslmode=require",
    CREDENTIALS_KEK_BASE64: Buffer.alloc(32, 3).toString("base64"),
    CLERK_ISSUER: "https://clerk.test",
    CLERK_JWKS_URL: "https://clerk.test/.well-known/jwks.json",
    ...overrides,
  };
}

describe("gateway authentication configuration", () => {
  it("starts with Clerk as the only configured issuer", () => {
    const config = parseConfig(environment({
      ASSETHERO_API_ISSUER: "",
      ASSETHERO_API_JWKS_URL: "",
    }));

    expect(config.CLERK_ISSUER).toBe("https://clerk.test");
    expect(config.ASSETHERO_API_ISSUER).toBeUndefined();
    expect(config.ASSETHERO_API_JWKS_URL).toBeUndefined();
  });

  it("enables AssetHero API trust only when issuer and JWKS are configured together", () => {
    expect(() => parseConfig(environment({
      ASSETHERO_API_ISSUER: "https://auth.assethero.test",
    }))).toThrow(/configured together/);

    const config = parseConfig(environment({
      ASSETHERO_API_ISSUER: "https://auth.assethero.test/",
      ASSETHERO_API_JWKS_URL: "https://auth.assethero.test/.well-known/jwks.json",
    }));
    expect(config.ASSETHERO_API_ISSUER).toBe("https://auth.assethero.test");
    expect(config.ASSETHERO_API_AUDIENCE).toBe("polytrade");
  });
});

describe("gateway upstream proxy configuration", () => {
  it("accepts HTTP service origins and removes trailing slashes", () => {
    const config = parseConfig(environment({
      AGENT_UPSTREAM_URL: "http://agent:8000/",
      BACKTEST_UPSTREAM_URL: "https://backtest.internal:8100/",
      UPSTREAM_PROXY_TIMEOUT_MS: "100",
    }));

    expect(config.AGENT_UPSTREAM_URL).toBe("http://agent:8000");
    expect(config.BACKTEST_UPSTREAM_URL).toBe("https://backtest.internal:8100");
    expect(config.UPSTREAM_PROXY_TIMEOUT_MS).toBe(100);
  });

  it.each([
    "ftp://agent:8000",
    "http://user:secret@agent:8000",
    "http://agent:8000/v1",
    "http://agent:8000?target=backtest",
    "http://agent:8000#fragment",
  ])("rejects an unsafe upstream URL: %s", (url) => {
    expect(() => parseConfig(environment({ AGENT_UPSTREAM_URL: url }))).toThrow();
  });

  it("requires a heartbeat-safe proxy timeout outside tests", () => {
    expect(() => parseConfig(environment({
      NODE_ENV: "production",
      UPSTREAM_PROXY_TIMEOUT_MS: "19999",
    }))).toThrow(/at least 20000/);
  });
});
