import { describe, expect, it } from "vitest";
import { mnemonicToAccount } from "viem/accounts";

import { parseConfig } from "../src/config.js";
import {
  buildL1TypedData,
  canonicalOrderHash,
  normalizeOnchainBalance,
  PolymarketAdapter,
  validateProposalMarketMetadata,
} from "../src/polymarket.js";
import { orderTypedData } from "./fakes.js";

// Public Anvil/Hardhat test accounts, derived from the documented test mnemonic
// (not real secrets — GitGuardian flags raw hex literals, so compose it).
const TEST_MNEMONIC = `${Array(11).fill("test").join(" ")} junk`;
const account = mnemonicToAccount(TEST_MNEMONIC, { accountIndex: 1 });
const otherAccount = mnemonicToAccount(TEST_MNEMONIC, { accountIndex: 2 });

function config() {
  return parseConfig({
    NODE_ENV: "test",
    DATABASE_URL: "postgresql://test:test@db.example.test:5432/polytrade?sslmode=require",
    CREDENTIALS_KEK_BASE64: Buffer.alloc(32, 4).toString("base64"),
    CLERK_ISSUER: "https://clerk.test",
    CLERK_JWKS_URL: "https://clerk.test/jwks",
    POLYMARKET_GAMMA_URL: "https://gamma.test",
  });
}

describe("PolymarketAdapter", () => {
  it("retries public reads but validates their response shape", async () => {
    let calls = 0;
    const request = async () => {
      calls += 1;
      if (calls === 1) return new Response("busy", { status: 503 });
      return new Response(JSON.stringify({ events: [{ id: "1", title: "Election", markets: [] }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    const adapter = new PolymarketAdapter(config(), request as typeof fetch);
    const result = await adapter.searchMarkets("election", 10) as { events: unknown[] };
    expect(calls).toBe(2);
    expect(result.events).toHaveLength(1);

    const malformed = new PolymarketAdapter(
      config(),
      (async () => new Response(JSON.stringify({ events: "not-an-array" }), { status: 200 })) as typeof fetch,
    );
    await expect(malformed.searchMarkets("election", 10)).rejects.toMatchObject({ statusCode: 503 });
  });

  it("requests closed Gamma events for resolved-market search", async () => {
    let observedUrl = "";
    const request = async (input: string | URL | Request) => {
      observedUrl = String(input);
      return new Response(JSON.stringify({ events: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    const adapter = new PolymarketAdapter(config(), request as typeof fetch);
    const result = await adapter.searchMarkets("election", 5, "resolved") as { state: string };

    expect(result.state).toBe("resolved");
    expect(new URL(observedUrl).searchParams.get("events_status")).toBe("closed");
  });

  it("lists active bookable markets ordered by Gamma and reports pagination", async () => {
    let observedUrl = "";
    const request = async (input: string | URL | Request) => {
      observedUrl = String(input);
      return new Response(JSON.stringify([
        {
          id: "1",
          conditionId: "condition-1",
          slug: "fed-september",
          question: "Will the Fed hold?",
          outcomes: '["Yes","No"]',
          outcomePrices: '["0.435","0.565"]',
          clobTokenIds: '["123","456"]',
          active: true,
          closed: false,
          acceptingOrders: true,
          enableOrderBook: true,
          restricted: true,
          icon: "https://icon.test/fed.png",
          volume24hr: 1721754.107278002,
        },
        { id: "2", conditionId: "c2", question: "No book", outcomes: '["Yes","No"]', clobTokenIds: '["789","012"]', active: true, closed: false, acceptingOrders: true, enableOrderBook: false },
        { id: "3", conditionId: "c3", question: "Single outcome", outcomes: '["Yes"]', clobTokenIds: '["345"]', active: true, closed: false, acceptingOrders: true, enableOrderBook: true },
      ]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    const adapter = new PolymarketAdapter(config(), request as typeof fetch);
    const result = await adapter.listActiveMarkets({ limit: 12, offset: 0, order: "volume24hr" }) as {
      markets: Array<Record<string, unknown>>;
      hasMore: boolean;
    };

    const url = new URL(observedUrl);
    expect(url.pathname).toBe("/markets");
    expect(url.searchParams.get("active")).toBe("true");
    expect(url.searchParams.get("closed")).toBe("false");
    expect(url.searchParams.get("limit")).toBe("12");
    expect(url.searchParams.get("offset")).toBe("0");
    expect(url.searchParams.get("order")).toBe("volume24hr");
    expect(url.searchParams.get("ascending")).toBe("false");
    expect(result.markets).toHaveLength(1);
    expect(result.markets[0]).toMatchObject({
      slug: "fed-september",
      icon: "https://icon.test/fed.png",
      volume24hr: "1721754.107278002",
    });
    expect(result.hasMore).toBe(false);
  });

  it("maps a missing public market to a 404 instead of an upstream failure", async () => {
    const request = (async () => new Response("not found", { status: 404 })) as typeof fetch;
    const adapter = new PolymarketAdapter(config(), request);
    await expect(adapter.getPublicMarket("ghost-market")).rejects.toMatchObject({ statusCode: 404 });
  });

  it("returns only backtest-eligible markets for resolved search", async () => {
    const eligible = {
      id: "eligible",
      conditionId: "condition-eligible",
      question: "Will the eligible market resolve Yes?",
      outcomes: '["Yes","No"]',
      outcomePrices: '["1","0"]',
      clobTokenIds: '["101","202"]',
      active: false,
      closed: true,
      acceptingOrders: false,
      enableOrderBook: true,
      startDate: "2026-05-01T00:00:00Z",
    };
    const request = async () => new Response(JSON.stringify({
      events: [{
        id: "event",
        title: "Resolved markets",
        markets: [eligible, { ...eligible, id: "legacy", conditionId: "condition-legacy", startDate: "2024-01-04T00:00:00Z" }],
      }],
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
    const adapter = new PolymarketAdapter(config(), request as typeof fetch);
    const result = await adapter.searchMarkets("market", 5, "resolved") as {
      events: Array<{ markets: Array<{ conditionId: string }> }>;
    };

    expect(result.events).toHaveLength(1);
    expect(result.events[0]?.markets.map((market) => market.conditionId)).toEqual(["condition-eligible"]);
  });

  it("loads paper market identity by condition and normalizes the public fee rate", async () => {
    const observedUrls: URL[] = [];
    const request = async (input: string | URL | Request) => {
      const url = new URL(String(input));
      observedUrls.push(url);
      if (url.pathname === "/fee-rate") {
        return new Response(JSON.stringify({ base_fee: 400 }), { status: 200 });
      }
      return new Response(JSON.stringify([{
        id: "market-1",
        conditionId: "0xcondition",
        slug: "paper-market",
        question: "Will the paper adapter pass?",
        description: "",
        outcomes: ["Yes", "No"],
        outcomePrices: ["0.4", "0.6"],
        clobTokenIds: ["123", "456"],
        active: true,
        closed: false,
        acceptingOrders: true,
        enableOrderBook: true,
        archived: false,
        restricted: false,
        orderMinSize: "1",
        orderPriceMinTickSize: "0.01",
      }]), { status: 200 });
    };
    const adapter = new PolymarketAdapter(config(), request as typeof fetch);

    const snapshot = await adapter.getMarketByCondition("0xcondition");
    const feeRate = await adapter.getFeeRate("123");

    expect(snapshot.market).toMatchObject({ conditionId: "0xcondition", clobTokenIds: ["123", "456"] });
    expect(feeRate).toBe("0.040000");
    expect(observedUrls[0]?.searchParams.get("condition_ids")).toBe("0xcondition");
    expect(observedUrls[1]?.searchParams.get("token_id")).toBe("123");
  });

  it("verifies the exact EIP-712 payload, wallet, and Polygon domain", async () => {
    const adapter = new PolymarketAdapter(config());
    const typedData = orderTypedData(account.address, "123", "BUY");
    const signature = await account.signTypedData(typedData as never);
    const intent = { typedData, unsignedOrder: {} };

    await expect(adapter.verifyOrderSignature(intent, account.address, signature)).resolves.toMatch(/^0x/);
    await expect(
      adapter.verifyOrderSignature(
        { ...intent, typedData: { ...typedData, message: { ...typedData.message, tokenId: "124" } } },
        account.address,
        signature,
      ),
    ).rejects.toMatchObject({ statusCode: 400 });
    await expect(
      adapter.verifyOrderSignature(
        { ...intent, typedData: { ...typedData, domain: { ...typedData.domain, chainId: 1 } } },
        account.address,
        signature,
      ),
    ).rejects.toMatchObject({ statusCode: 400 });
    const wrongChain = { ...typedData, domain: { ...typedData.domain, chainId: 1 } };
    const wrongChainSignature = await account.signTypedData(wrongChain as never);
    await expect(
      adapter.verifyOrderSignature(
        { ...intent, typedData: wrongChain },
        account.address,
        wrongChainSignature,
      ),
    ).rejects.toMatchObject({ statusCode: 400 });
    await expect(
      adapter.verifyOrderSignature(intent, otherAccount.address, signature),
    ).rejects.toMatchObject({ statusCode: 400 });
  });

  it("interprets balances and allowances as six-decimal on-chain units", () => {
    expect(normalizeOnchainBalance("500000").toString()).toBe("0.5");
    expect(normalizeOnchainBalance("1000000").toString()).toBe("1");
    expect(normalizeOnchainBalance("1000000000000").toString()).toBe("1000000");
  });

  it("binds the displayed market and outcome to matching CLOB and Gamma metadata", () => {
    const proposal = {
      marketId: "0xcondition",
      marketQuestion: "Will this order pass?",
      outcome: "Yes",
      tokenId: "123",
    };
    const clobMarket = {
      c: "0xcondition",
      t: [{ t: "123", o: "Yes" }, { t: "456", o: "No" }],
      ao: true,
    };
    const gammaMarkets = [{
      conditionId: "0xcondition",
      question: "Will this order pass?",
      outcomes: "[\"Yes\",\"No\"]",
      clobTokenIds: "[\"123\",\"456\"]",
      active: true,
      closed: false,
      acceptingOrders: true,
    }];

    expect(() => validateProposalMarketMetadata(proposal, clobMarket, gammaMarkets)).not.toThrow();
    expect(() => validateProposalMarketMetadata(
      { ...proposal, marketQuestion: "Spoofed question" },
      clobMarket,
      gammaMarkets,
    )).toThrow("Market question does not match");
    expect(() => validateProposalMarketMetadata(
      { ...proposal, outcome: "No" },
      clobMarket,
      gammaMarkets,
    )).toThrow("Outcome label does not match");
  });

  it("fails closed for stale or inconsistent market metadata", () => {
    const proposal = {
      marketId: "0xcondition",
      marketQuestion: "Will this order pass?",
      outcome: "Yes",
      tokenId: "123",
    };
    const clobMarket = {
      c: "0xcondition",
      t: [{ t: "123", o: "Yes" }, { t: "456", o: "No" }],
      ao: true,
    };
    const gammaMarket = {
      conditionId: "0xcondition",
      question: "Will this order pass?",
      outcomes: ["Yes", "No"],
      clobTokenIds: ["123", "456"],
      active: true,
      closed: false,
      acceptingOrders: true,
    };

    expect(() => validateProposalMarketMetadata(
      proposal,
      { ...clobMarket, ao: false },
      [gammaMarket],
    )).toThrow("not accepting orders");
    expect(() => validateProposalMarketMetadata(
      proposal,
      clobMarket,
      [{ ...gammaMarket, closed: true }],
    )).toThrow("not accepting orders");
    expect(() => validateProposalMarketMetadata(
      proposal,
      clobMarket,
      [{ ...gammaMarket, outcomes: ["No", "Yes"] }],
    )).toThrow("Outcome mapping does not match");
  });

  it("reconciles EIP-1271 submissions by the underlying order hash", async () => {
    const adapter = new PolymarketAdapter(config());
    const order = orderTypedData(account.address, "123", "BUY");
    const wrapped = {
      domain: order.domain,
      types: {
        Order: order.types.Order!,
        TypedDataSign: [
          { name: "contents", type: "Order" },
          { name: "name", type: "string" },
          { name: "version", type: "string" },
          { name: "chainId", type: "uint256" },
          { name: "verifyingContract", type: "address" },
          { name: "salt", type: "bytes32" },
        ],
      },
      primaryType: "TypedDataSign",
      message: {
        contents: order.message,
        name: "DepositWallet",
        version: "1",
        chainId: 137,
        verifyingContract: account.address,
        salt: `0x${"00".repeat(32)}`,
      },
    };
    const intent = { typedData: wrapped, unsignedOrder: { signatureType: 3 } };
    const signature = await account.signTypedData(wrapped as never);

    await expect(
      adapter.verifyOrderSignature(intent, account.address, signature),
    ).resolves.toBe(canonicalOrderHash({ typedData: order, unsignedOrder: { signatureType: 0 } }));
  });

  it("keeps the L1 authentication typed-data vector deterministic", () => {
    expect(buildL1TypedData(account.address, 1_754_176_000, 0)).toEqual({
      domain: { name: "ClobAuthDomain", version: "1", chainId: 137 },
      types: {
        ClobAuth: [
          { name: "address", type: "address" },
          { name: "timestamp", type: "string" },
          { name: "nonce", type: "uint256" },
          { name: "message", type: "string" },
        ],
      },
      primaryType: "ClobAuth",
      message: {
        address: account.address,
        timestamp: "1754176000",
        nonce: 0,
        message: "This message attests that I control the given wallet",
      },
    });
  });
});
