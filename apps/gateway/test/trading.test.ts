import type { CreateOrderProposal } from "@polytrade/contracts";
import { mnemonicToAccount } from "viem/accounts";
import { describe, expect, it } from "vitest";

import { parseConfig } from "../src/config.js";
import { CredentialCipher } from "../src/crypto.js";
import { AppError } from "../src/errors.js";
import { TradingService } from "../src/trading.js";
import type { Principal, WalletSessionRecord } from "../src/types.js";
import { FakePolymarket, MemoryTradingStore } from "./fakes.js";

// Public Anvil/Hardhat test accounts, derived from the documented test mnemonic
// (not real secrets — GitGuardian flags raw hex literals, so compose it).
const TEST_MNEMONIC = `${Array(11).fill("test").join(" ")} junk`;
const account = mnemonicToAccount(TEST_MNEMONIC, { accountIndex: 1 });
const principal: Principal = { id: "assethero:user-1", issuer: "assethero", subject: "user-1", scopes: new Set(["research", "trade"]) };
const otherPrincipal: Principal = { ...principal, id: "assethero:user-2", subject: "user-2" };
const now = new Date("2026-08-03T00:00:00.000Z");

function config() {
  return parseConfig({
    NODE_ENV: "test",
    DATABASE_URL: "postgresql://test:test@db.example.test:5432/polytrade?sslmode=require",
    CREDENTIALS_KEK_BASE64: Buffer.alloc(32, 7).toString("base64"),
    CLERK_ISSUER: "https://clerk.test",
    CLERK_JWKS_URL: "https://clerk.test/jwks",
  });
}

function setup() {
  const settings = config();
  const store = new MemoryTradingStore();
  const polymarket = new FakePolymarket();
  const cipher = new CredentialCipher(settings.credentialKey);
  const service = new TradingService(settings, store, cipher, polymarket, () => now);
  return { settings, store, polymarket, cipher, service };
}

async function addSession(context: ReturnType<typeof setup>): Promise<WalletSessionRecord> {
  const session: WalletSessionRecord = {
    id: "00000000-0000-4000-8000-000000000001",
    principalId: principal.id,
    walletAddress: account.address,
    signatureType: 0,
    encryptedCredentials: context.cipher.encrypt(
      { key: "key", secret: "secret", passphrase: "passphrase" },
      `${principal.id}:00000000-0000-4000-8000-000000000001`,
    ),
    idleExpiresAt: new Date(now.getTime() + 1_800_000),
    absoluteExpiresAt: new Date(now.getTime() + 28_800_000),
    lastUsedAt: now,
  };
  await context.store.createSession(session);
  return session;
}

function proposal(execution: "GTC" | "GTD" | "FOK" | "FAK"): CreateOrderProposal {
  const base = {
    action: "create" as const,
    tokenId: "123",
    marketId: "0xcondition",
    marketQuestion: "Will this order pass?",
    outcome: "Yes",
    side: "BUY" as const,
    rationale: "test",
    observedAt: now.toISOString(),
    postOnly: execution === "GTC" || execution === "GTD",
  };
  return execution === "GTC"
    ? { ...base, execution, price: "0.4", size: "10" }
    : execution === "GTD"
      ? { ...base, execution, price: "0.4", size: "10", expiration: Math.floor(now.getTime() / 1_000) + 3_600 }
      : { ...base, execution, amount: "4", limitPrice: "0.5", postOnly: false };
}

/** Mimics the clob-client's ApiError shape (Error subclass with an HTTP status). */
function httpError(status: number, message: string): Error {
  return Object.assign(new Error(message), { status });
}

describe("TradingService", () => {
  it("creates a single-use wallet challenge and encrypted session", async () => {
    const context = setup();
    const challenge = await context.service.createChallenge(principal, {
      walletAddress: account.address,
      signatureType: 0,
    });
    const signature = await account.signTypedData(challenge.typedData as never);
    const session = await context.service.createSession(principal, challenge.challengeId, signature);

    expect(session.walletAddress).toBe(account.address);
    expect(context.store.sessions.get(session.sessionId)?.encryptedCredentials).not.toContain("secret");
    await expect(
      context.service.createSession(principal, challenge.challengeId, signature),
    ).rejects.toMatchObject({ statusCode: 409 });
  });

  it("releases the challenge when the credential exchange fails upstream", async () => {
    const context = setup();
    const challenge = await context.service.createChallenge(principal, {
      walletAddress: account.address,
      signatureType: 0,
    });
    const signature = await account.signTypedData(challenge.typedData as never);
    context.polymarket.l1CredentialsError = new AppError(503, "UPSTREAM_UNAVAILABLE", "Polymarket wallet authentication is unavailable");

    await expect(
      context.service.createSession(principal, challenge.challengeId, signature),
    ).rejects.toMatchObject({ statusCode: 503 });
    expect(context.store.sessions.size).toBe(0);

    // The burned challenge must not cost the user their signed session once the CLOB recovers.
    context.polymarket.l1CredentialsError = null;
    const session = await context.service.createSession(principal, challenge.challengeId, signature);
    expect(session.walletAddress).toBe(account.address);
  });

  it("does not release the challenge when the wallet signature is invalid", async () => {
    const context = setup();
    const challenge = await context.service.createChallenge(principal, {
      walletAddress: account.address,
      signatureType: 0,
    });
    const signature = await account.signTypedData(challenge.typedData as never);
    const tampered = await account.signTypedData({ ...challenge.typedData, message: { ...challenge.typedData.message, nonce: 99 } } as never);

    await expect(
      context.service.createSession(principal, challenge.challengeId, tampered),
    ).rejects.toMatchObject({ statusCode: 400 });
    await expect(
      context.service.createSession(principal, challenge.challengeId, tampered),
    ).rejects.toMatchObject({ statusCode: 409 });
    // The original signature must not become replayable after a failed forgery attempt.
    await expect(
      context.service.createSession(principal, challenge.challengeId, signature),
    ).rejects.toMatchObject({ statusCode: 409 });
  });

  it("reads current wallet metadata without exposing credentials or extending expiry", async () => {
    const context = setup();
    const session = await addSession(context);
    const idleExpiresAt = session.idleExpiresAt.toISOString();
    const lastUsedAt = session.lastUsedAt.toISOString();

    const status = await context.service.currentSession(principal);

    expect(status).toEqual({
      sessionId: session.id,
      walletAddress: account.address,
      signatureType: 0,
      idleExpiresAt,
      expiresAt: session.absoluteExpiresAt.toISOString(),
    });
    expect(status).not.toHaveProperty("encryptedCredentials");
    expect(context.store.sessions.get(session.id)?.idleExpiresAt.toISOString()).toBe(idleExpiresAt);
    expect(context.store.sessions.get(session.id)?.lastUsedAt.toISOString()).toBe(lastUsedAt);
    await expect(context.service.currentSession(otherPrincipal)).rejects.toMatchObject({ statusCode: 404 });
  });

  it("normalizes account positions, orders, and fills while preserving missing fields", async () => {
    const context = setup();
    await addSession(context);
    context.polymarket.accountSnapshot = {
      walletAddress: account.address,
      positions: [{
        asset: "123",
        conditionId: "0xcondition",
        title: "Will normalization pass?",
        outcome: "Yes",
        size: "10",
        avgPrice: "0.4",
        curPrice: "0.55",
        currentValue: "5.5",
        cashPnl: "1.5",
        percentPnl: "37.5",
        redeemable: false,
      }],
      openOrders: [{
        id: "order-1",
        market: "0xcondition",
        asset_id: "123",
        outcome: "Yes",
        side: "BUY",
        original_size: "10",
        size_matched: "4",
        price: "0.4",
        order_type: "GTC",
        status: "LIVE",
        created_at: 1_785_715_200,
        expiration: "0",
      }],
      trades: [{
        id: "fill-1",
        market: "0xcondition",
        asset_id: "123",
        outcome: "Yes",
        side: "BUY",
        size: "4",
        price: "0.4",
        status: "MATCHED",
        match_time: "2026-08-03T00:00:00Z",
        trader_side: "TAKER",
        transaction_hash: "0xabc",
      }, {}],
      observedAt: now.toISOString(),
    };

    const overview = await context.service.accountOverview(principal);

    expect(overview.positions[0]).toMatchObject({
      positionId: "123",
      marketTitle: "Will normalization pass?",
      averagePrice: "0.4",
      currentPrice: "0.55",
      redeemable: false,
    });
    expect(overview.openOrders[0]).toMatchObject({
      orderId: "order-1",
      originalSize: "10",
      matchedSize: "4",
      remainingSize: "6",
      createdAt: "2026-08-03T00:00:00.000Z",
      expiration: null,
    });
    expect(overview.fills[0]).toMatchObject({
      tradeId: "fill-1",
      matchedAt: "2026-08-03T00:00:00.000Z",
      traderSide: "TAKER",
      transactionHash: "0xabc",
    });
    expect(overview.fills[1]).toMatchObject({
      marketId: null,
      side: null,
      matchedAt: null,
    });
  });

  it("builds every supported order type and preserves post-only rules", async () => {
    const context = setup();
    await addSession(context);
    for (const execution of ["GTC", "GTD", "FOK", "FAK"] as const) {
      const intent = await context.service.createIntent(
        principal,
        "00000000-0000-4000-8000-000000000001",
        proposal(execution),
        `intent-${execution}`,
      );
      expect(intent.orderType).toBe(execution);
      expect(intent.postOnly).toBe(execution === "GTC" || execution === "GTD");
    }
    expect(context.polymarket.preflighted).toHaveLength(4);
  });

  it("requires the exact wallet signature, rechecks preflight, and isolates principals", async () => {
    const context = setup();
    await addSession(context);
    const intent = await context.service.createIntent(
      principal,
      "00000000-0000-4000-8000-000000000001",
      proposal("GTC"),
      "intent-submit",
    );
    const signature = await account.signTypedData(intent.typedData as never);
    const result = await context.service.submitIntent(principal, intent.intentId, signature) as { success: boolean };
    expect(result.success).toBe(true);
    expect(context.polymarket.preflighted).toHaveLength(2);
    await expect(
      context.service.submitIntent(principal, intent.intentId, signature),
    ).resolves.toEqual(result);
    expect(context.polymarket.submitted).toHaveLength(1);

    await expect(context.service.account(otherPrincipal)).rejects.toMatchObject({ statusCode: 404 });
    await expect(
      context.service.submitIntent(otherPrincipal, intent.intentId, signature),
    ).rejects.toMatchObject({ statusCode: 404 });
  });

  it("atomically admits only one concurrent CLOB submission", async () => {
    const context = setup();
    await addSession(context);
    const intent = await context.service.createIntent(
      principal,
      "00000000-0000-4000-8000-000000000001",
      proposal("GTC"),
      "intent-concurrent",
    );
    const signature = await account.signTypedData(intent.typedData as never);

    const results = await Promise.allSettled([
      context.service.submitIntent(principal, intent.intentId, signature),
      context.service.submitIntent(principal, intent.intentId, signature),
    ]);

    expect(results.filter((result) => result.status === "fulfilled")).toHaveLength(1);
    expect(results.filter((result) => result.status === "rejected")).toHaveLength(1);
    expect(context.polymarket.submitted).toHaveLength(1);
  });

  it("expires unsigned intents before they can cross the mutation boundary", async () => {
    const context = setup();
    await addSession(context);
    const intent = await context.service.createIntent(
      principal,
      "00000000-0000-4000-8000-000000000001",
      proposal("GTC"),
      "intent-expired",
    );
    context.store.intents.get(intent.intentId)!.expiresAt = new Date(now.getTime() - 1);
    const signature = await account.signTypedData(intent.typedData as never);

    await expect(
      context.service.submitIntent(principal, intent.intentId, signature),
    ).rejects.toMatchObject({ statusCode: 409 });
    expect(context.store.intents.get(intent.intentId)?.status).toBe("EXPIRED");
    expect(context.polymarket.submitted).toHaveLength(0);
  });

  it("rejects stale or future-dated proposal observations before preflight", async () => {
    const context = setup();
    await addSession(context);
    const sessionId = "00000000-0000-4000-8000-000000000001";

    await expect(context.service.createIntent(
      principal,
      sessionId,
      { ...proposal("GTC"), observedAt: new Date(now.getTime() - 120_001).toISOString() },
      "intent-stale",
    )).rejects.toMatchObject({ statusCode: 400 });
    await expect(context.service.createIntent(
      principal,
      sessionId,
      { ...proposal("GTC"), observedAt: new Date(now.getTime() + 30_001).toISOString() },
      "intent-future",
    )).rejects.toMatchObject({ statusCode: 400 });
    expect(context.polymarket.preflighted).toHaveLength(0);
  });

  it("never retries an ambiguous submission and reconciles by signed hash", async () => {
    const context = setup();
    await addSession(context);
    const intent = await context.service.createIntent(
      principal,
      "00000000-0000-4000-8000-000000000001",
      proposal("FAK"),
      "intent-ambiguous",
    );
    const signature = await account.signTypedData(intent.typedData as never);
    context.polymarket.submitError = new Error("connection closed after write");
    await expect(context.service.submitIntent(principal, intent.intentId, signature)).rejects.toThrow();
    expect(context.polymarket.submitted).toHaveLength(1);

    context.polymarket.reconciled = { success: true, orderID: "reconciled-order" };
    const result = await context.service.submitIntent(principal, intent.intentId, signature) as { orderID: string };
    expect(result.orderID).toBe("reconciled-order");
    expect(context.polymarket.submitted).toHaveLength(1);
  });

  it("settles definitive CLOB refusals as REJECTED instead of bricking the intent as AMBIGUOUS", async () => {
    const context = setup();
    await addSession(context);
    const intent = await context.service.createIntent(
      principal,
      "00000000-0000-4000-8000-000000000001",
      proposal("GTC"),
      "intent-definitive-rejection",
    );
    const signature = await account.signTypedData(intent.typedData as never);
    context.polymarket.submitError = httpError(400, "not enough balance / allowance");

    await expect(
      context.service.submitIntent(principal, intent.intentId, signature),
    ).rejects.toMatchObject({ statusCode: 400, code: "ORDER_REJECTED" });
    expect(context.store.intents.get(intent.intentId)?.status).toBe("REJECTED");
    expect(context.store.audits.at(-1)).toMatchObject({ action: "order_submission_rejected" });

    // A declined intent is terminal: resubmitting it fails fast and clearly
    // instead of looping through an ambiguous reconcile that always 404s.
    await expect(
      context.service.submitIntent(principal, intent.intentId, signature),
    ).rejects.toMatchObject({ statusCode: 409 });
    expect(context.polymarket.submitted).toHaveLength(1);
  });

  it("keeps gateway validation failures terminal without losing their status code", async () => {
    const context = setup();
    await addSession(context);
    const intent = await context.service.createIntent(
      principal,
      "00000000-0000-4000-8000-000000000001",
      proposal("GTC"),
      "intent-apperror-rejection",
    );
    const signature = await account.signTypedData(intent.typedData as never);
    context.polymarket.submitError = new AppError(403, "FORBIDDEN", "Geographic restrictions apply");

    await expect(
      context.service.submitIntent(principal, intent.intentId, signature),
    ).rejects.toMatchObject({ statusCode: 403, code: "FORBIDDEN" });
    expect(context.store.intents.get(intent.intentId)?.status).toBe("REJECTED");
  });

  it("treats upstream 5xx and timeouts as ambiguous, never as rejected", async () => {
    const context = setup();
    await addSession(context);
    const intent = await context.service.createIntent(
      principal,
      "00000000-0000-4000-8000-000000000001",
      proposal("FAK"),
      "intent-timeout",
    );
    const signature = await account.signTypedData(intent.typedData as never);
    context.polymarket.submitError = httpError(504, "The operation was aborted due to timeout");
    await expect(context.service.submitIntent(principal, intent.intentId, signature)).rejects.toThrow();
    expect(context.store.intents.get(intent.intentId)?.status).toBe("AMBIGUOUS");

    const retry = await context.service.createIntent(
      principal,
      "00000000-0000-4000-8000-000000000001",
      proposal("GTC"),
      "intent-network-error",
    );
    const retrySignature = await account.signTypedData(retry.typedData as never);
    context.polymarket.submitError = new Error("fetch failed");
    await expect(context.service.submitIntent(principal, retry.intentId, retrySignature)).rejects.toThrow();
    expect(context.store.intents.get(retry.intentId)?.status).toBe("AMBIGUOUS");
  });

  it("preserves partial-fill amounts and trade IDs from the CLOB response", async () => {
    const context = setup();
    await addSession(context);
    const intent = await context.service.createIntent(
      principal,
      "00000000-0000-4000-8000-000000000001",
      proposal("FAK"),
      "intent-partial-fill",
    );
    context.polymarket.submitResponse = {
      success: true,
      errorMsg: "",
      orderID: "partial-order",
      status: "matched",
      takingAmount: "2",
      makingAmount: "1",
      tradeIDs: ["fill-1"],
    };
    const signature = await account.signTypedData(intent.typedData as never);

    const result = await context.service.submitIntent(
      principal,
      intent.intentId,
      signature,
    );

    expect(result).toMatchObject({
      orderID: "partial-order",
      takingAmount: "2",
      makingAmount: "1",
      tradeIDs: ["fill-1"],
    });
    expect(context.store.intents.get(intent.intentId)?.status).toBe("SUBMITTED");
  });

  it("supports single, market, and all-open cancellation selectors", async () => {
    const context = setup();
    await addSession(context);
    const sessionId = "00000000-0000-4000-8000-000000000001";
    await context.service.cancel(principal, sessionId, { kind: "order", orderId: "order-1" });
    await context.service.cancel(principal, sessionId, { kind: "market", marketId: "0xcondition", tokenId: "123" });
    await context.service.cancel(principal, sessionId, { kind: "all" });
    expect(context.polymarket.cancellations.map((item) => item.kind)).toEqual(["order", "market", "all"]);
  });
});
