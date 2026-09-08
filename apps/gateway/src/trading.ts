import { createHash, randomUUID } from "node:crypto";

import {
  accountOverviewSchema,
  type AccountOverview,
  type CancellationSelector,
  type CreateOrderProposal,
  type OrderIntentResponse,
  type WalletChallengeRequest,
  type WalletChallengeResponse,
  type WalletSessionResponse,
  type WalletSessionStatus,
} from "@polytrade/contracts";
import { Decimal } from "decimal.js";
import { getAddress, verifyTypedData, type Hex } from "viem";

import type { GatewayConfig } from "./config.js";
import { CredentialCipher } from "./crypto.js";
import { AppError, conflict, isDefinitiveRejection, notFound, validation } from "./errors.js";
import { buildL1TypedData, type PolymarketPort } from "./polymarket.js";
import type { TradingStore } from "./store.js";
import type {
  AccountSnapshot,
  ApiKeyCreds,
  OrderIntentRecord,
  Principal,
  WalletSessionRecord,
} from "./types.js";

const PROPOSAL_MAX_AGE_MS = 2 * 60 * 1_000;
const PROPOSAL_CLOCK_SKEW_MS = 30 * 1_000;

export class TradingService {
  constructor(
    private readonly config: GatewayConfig,
    private readonly store: TradingStore,
    private readonly cipher: CredentialCipher,
    private readonly polymarket: PolymarketPort,
    private readonly now: () => Date = () => new Date(),
  ) {}

  async createChallenge(
    principal: Principal,
    request: WalletChallengeRequest,
  ): Promise<WalletChallengeResponse> {
    const walletAddress = getAddress(request.walletAddress);
    const funderAddress = request.funderAddress ? getAddress(request.funderAddress) : undefined;
    if (request.signatureType !== 0 && !funderAddress) {
      throw validation("A funder address is required for proxy, Safe, and EIP-1271 wallets");
    }
    if (request.signatureType === 0 && funderAddress) {
      throw validation("EOA sessions cannot include a funder address");
    }

    const current = this.now();
    const timestampSeconds = Math.floor(current.getTime() / 1_000);
    const expiresAt = new Date(current.getTime() + this.config.WALLET_CHALLENGE_TTL_SECONDS * 1_000);
    const challengeId = randomUUID();
    const typedData = buildL1TypedData(walletAddress, timestampSeconds, 0);
    await this.store.createChallenge({
      id: challengeId,
      principalId: principal.id,
      walletAddress,
      signatureType: request.signatureType,
      ...(funderAddress ? { funderAddress } : {}),
      timestampSeconds,
      nonce: 0,
      typedData,
      expiresAt,
    });
    await this.store.appendAudit(principal.id, "wallet_challenge_created", challengeId, {
      walletAddress,
      signatureType: request.signatureType,
    });
    return { challengeId, typedData, expiresAt: expiresAt.toISOString() };
  }

  async createSession(
    principal: Principal,
    challengeId: string,
    signature: Hex,
  ): Promise<WalletSessionResponse> {
    const current = this.now();
    const challenge = await this.store.consumeChallenge(challengeId, principal.id, current);
    if (!challenge) throw conflict("Wallet challenge is invalid, expired, or already used");

    const valid = await verifyTypedData({
      address: challenge.walletAddress,
      domain: challenge.typedData.domain,
      types: challenge.typedData.types,
      primaryType: challenge.typedData.primaryType,
      message: challenge.typedData.message,
      signature,
    } as never);
    if (!valid) throw validation("Wallet authentication signature is invalid");

    let credentials: ApiKeyCreds;
    try {
      credentials = await this.polymarket.exchangeL1Credentials({
        walletAddress: challenge.walletAddress,
        signature,
        timestampSeconds: challenge.timestampSeconds,
        nonce: challenge.nonce,
      });
    } catch (error) {
      // The consume is only safe to keep once the session actually gets built;
      // a CLOB outage must not burn the user's signed challenge (fail-secure
      // release: the CAS on used_at makes a concurrent consume win instead).
      await this.store.releaseChallenge(challengeId, principal.id, current);
      throw error;
    }
    const sessionId = randomUUID();
    const absoluteExpiresAt = new Date(current.getTime() + this.config.WALLET_SESSION_MAX_SECONDS * 1_000);
    const idleExpiresAt = new Date(
      Math.min(
        absoluteExpiresAt.getTime(),
        current.getTime() + this.config.WALLET_SESSION_IDLE_SECONDS * 1_000,
      ),
    );
    const encryptedCredentials = this.cipher.encrypt(credentials, `${principal.id}:${sessionId}`);
    await this.store.createSession({
      id: sessionId,
      principalId: principal.id,
      walletAddress: challenge.walletAddress,
      signatureType: challenge.signatureType,
      ...(challenge.funderAddress ? { funderAddress: challenge.funderAddress } : {}),
      encryptedCredentials,
      idleExpiresAt,
      absoluteExpiresAt,
      lastUsedAt: current,
    });
    await this.store.appendAudit(principal.id, "wallet_session_created", sessionId, {
      walletAddress: challenge.walletAddress,
      signatureType: challenge.signatureType,
    });
    return walletSessionStatus({
      id: sessionId,
      principalId: principal.id,
      walletAddress: challenge.walletAddress,
      signatureType: challenge.signatureType,
      ...(challenge.funderAddress ? { funderAddress: challenge.funderAddress } : {}),
      encryptedCredentials,
      idleExpiresAt,
      absoluteExpiresAt,
      lastUsedAt: current,
    });
  }

  async currentSession(principal: Principal): Promise<WalletSessionStatus> {
    const session = await this.store.peekLatestSession(principal.id, this.now());
    if (!session) throw notFound("No active wallet session");
    return walletSessionStatus(session);
  }

  async revokeSession(principal: Principal, sessionId: string): Promise<void> {
    if (!(await this.store.revokeSession(sessionId, principal.id))) throw notFound("Wallet session not found");
    await this.store.appendAudit(principal.id, "wallet_session_revoked", sessionId);
  }

  async createIntent(
    principal: Principal,
    sessionId: string,
    proposal: CreateOrderProposal,
    idempotencyKey: string,
  ): Promise<OrderIntentResponse> {
    const current = this.now();
    const observedAt = Date.parse(proposal.observedAt);
    if (!Number.isFinite(observedAt) || observedAt > current.getTime() + PROPOSAL_CLOCK_SKEW_MS) {
      throw validation("Proposal observation time is invalid");
    }
    if (observedAt < current.getTime() - PROPOSAL_MAX_AGE_MS) {
      throw validation("Proposal market data is stale; ask the agent to refresh it");
    }
    if (proposal.execution === "GTD") {
      if (proposal.expiration === undefined || proposal.expiration <= Math.floor(current.getTime() / 1_000) + 60) {
        throw validation("GTD expiration must be at least 60 seconds in the future");
      }
    }
    const session = await this.requireSession(sessionId, principal.id, current);
    const credentials = this.credentials(session);
    await this.polymarket.preflight(session, credentials, proposal);
    const built = await this.polymarket.buildOrderIntent(session, proposal);
    const expiresAt = new Date(current.getTime() + this.config.ORDER_INTENT_TTL_SECONDS * 1_000);
    const candidate: OrderIntentRecord = {
      id: randomUUID(),
      principalId: principal.id,
      sessionId,
      idempotencyKey,
      proposal,
      orderType: proposal.execution,
      postOnly: proposal.postOnly,
      ...built,
      status: "PENDING",
      expiresAt,
    };
    const intent = await this.store.createIntent(candidate);
    await this.store.appendAudit(principal.id, "proposal_accepted", intent.id, {
      action: "create",
      orderType: intent.orderType,
      marketId: proposal.marketId,
      tokenId: proposal.tokenId,
      side: proposal.side,
    });
    await this.store.appendAudit(principal.id, "order_intent_created", intent.id, {
      orderType: intent.orderType,
      tokenId: proposal.tokenId,
      side: proposal.side,
    });
    return intentResponse(intent);
  }

  async submitIntent(
    principal: Principal,
    intentId: string,
    signature: Hex,
  ): Promise<unknown> {
    const current = this.now();
    const intent = await this.store.getIntent(intentId, principal.id);
    if (!intent) throw notFound("Order intent not found");
    if (intent.status === "SUBMITTED") return intent.upstreamResponse;
    if (intent.status === "SUBMITTING" || intent.status === "AMBIGUOUS") {
      if (!intent.signedOrderHash) throw conflict("Order submission requires manual reconciliation");
      const session = await this.requireSession(intent.sessionId, principal.id, current);
      const reconciled = await this.polymarket.reconcileOrder(
        session,
        this.credentials(session),
        intent.signedOrderHash,
      );
      if (!reconciled) {
        throw conflict("Order state is ambiguous; no retry was sent to Polymarket");
      }
      await this.store.setIntentStatus(intent.id, principal.id, "SUBMITTED", {
        upstreamResponse: reconciled,
        submittedAt: current,
      });
      await this.store.appendAudit(principal.id, "order_submission_reconciled", intent.id, {
        signedOrderHash: intent.signedOrderHash,
      });
      return reconciled;
    }
    if (intent.status !== "PENDING") throw conflict(`Order intent is ${intent.status.toLowerCase()}`);
    if (intent.expiresAt <= current) {
      await this.store.setIntentStatus(intent.id, principal.id, "EXPIRED");
      throw conflict("Order intent has expired; review current market data again");
    }
    const session = await this.requireSession(intent.sessionId, principal.id, current);
    const credentials = this.credentials(session);
    await this.polymarket.preflight(session, credentials, intent.proposal);
    const built = {
      typedData: intent.typedData,
      unsignedOrder: intent.unsignedOrder,
      ...(intent.signatureSuffix ? { signatureSuffix: intent.signatureSuffix } : {}),
    };
    const signedOrderHash = await this.polymarket.verifyOrderSignature(
      built,
      session.walletAddress,
      signature,
    );
    const claimed = await this.store.claimIntentSubmission(intent.id, principal.id, signedOrderHash);
    if (!claimed) {
      throw conflict("Order submission is already in progress; retry only to reconcile its signed hash");
    }
    await this.store.appendAudit(principal.id, "order_signature_verified", intent.id, {
      signedOrderHash,
    });
    try {
      const response = await this.polymarket.submitOrder(
        session,
        credentials,
        built,
        signature,
        intent.orderType,
        intent.postOnly,
      );
      const status = response.success ? "SUBMITTED" : "REJECTED";
      await this.store.setIntentStatus(intent.id, principal.id, status, {
        signedOrderHash,
        upstreamResponse: response,
        submittedAt: current,
      });
      await this.store.appendAudit(principal.id, "order_submission_resolved", intent.id, {
        status,
        orderId: response.orderID,
      });
      return response;
    } catch (error) {
      // A 4xx from the CLOB means Polymarket processed the request and refused
      // it — the signed order was never accepted, so the intent can settle
      // terminally as REJECTED instead of stuck in an ambiguous reconcile loop.
      // Anything else (timeouts, 5xx, network errors) stays AMBIGUOUS because
      // the order may still have landed.
      if (isDefinitiveRejection(error)) {
        await this.store.setIntentStatus(intent.id, principal.id, "REJECTED", { signedOrderHash });
        await this.store.appendAudit(principal.id, "order_submission_rejected", intent.id, {
          signedOrderHash,
          reason: error instanceof Error ? error.message : "Polymarket rejected the order",
        });
        throw definiteRejection(error);
      }
      await this.store.setIntentStatus(intent.id, principal.id, "AMBIGUOUS", { signedOrderHash });
      await this.store.appendAudit(principal.id, "order_submission_ambiguous", intent.id, {
        signedOrderHash,
      });
      throw error;
    }
  }

  async account(principal: Principal): Promise<AccountSnapshot> {
    const session = await this.store.getLatestSession(
      principal.id,
      this.now(),
      this.config.WALLET_SESSION_IDLE_SECONDS,
    );
    if (!session) throw notFound("No active wallet session");
    const account = await this.polymarket.getAccount(session, this.credentials(session));
    await this.store.mirrorAccount(principal.id, account);
    return account;
  }

  async accountOverview(principal: Principal): Promise<AccountOverview> {
    return normalizeAccountOverview(await this.account(principal));
  }

  async cancel(
    principal: Principal,
    sessionId: string,
    selector: CancellationSelector,
  ): Promise<unknown> {
    const session = await this.requireSession(sessionId, principal.id, this.now());
    await this.store.appendAudit(principal.id, "proposal_accepted", sessionId, {
      action: "cancel",
      selector,
    });
    const result = await this.polymarket.cancel(session, this.credentials(session), selector);
    await this.store.appendAudit(principal.id, "orders_cancelled", sessionId, { selector });
    return result;
  }

  private credentials(session: WalletSessionRecord): ApiKeyCreds {
    return this.cipher.decrypt<ApiKeyCreds>(
      session.encryptedCredentials,
      `${session.principalId}:${session.id}`,
    );
  }

  private async requireSession(id: string, principalId: string, now: Date): Promise<WalletSessionRecord> {
    const session = await this.store.getSession(
      id,
      principalId,
      now,
      this.config.WALLET_SESSION_IDLE_SECONDS,
    );
    if (!session) throw notFound("Wallet session is missing, expired, or revoked");
    return session;
  }
}

/**
 * Surface a definitive rejection with its upstream status instead of the raw
 * third-party error (which the error handler would otherwise map to a generic
 * 500). AppErrors already carry their own status and keep it.
 */
function definiteRejection(error: unknown): unknown {
  if (error instanceof AppError) return error;
  const message = error instanceof Error && error.message ? error.message : "Polymarket rejected the order";
  return new AppError(400, "ORDER_REJECTED", message);
}

function intentResponse(intent: OrderIntentRecord): OrderIntentResponse {
  return {
    intentId: intent.id,
    expiresAt: intent.expiresAt.toISOString(),
    orderType: intent.orderType,
    postOnly: intent.postOnly,
    typedData: intent.typedData,
    order: intent.unsignedOrder,
  };
}

function walletSessionStatus(session: WalletSessionRecord): WalletSessionStatus {
  return {
    sessionId: session.id,
    walletAddress: session.walletAddress,
    ...(session.funderAddress ? { funderAddress: session.funderAddress } : {}),
    signatureType: session.signatureType,
    idleExpiresAt: session.idleExpiresAt.toISOString(),
    expiresAt: session.absoluteExpiresAt.toISOString(),
  };
}

export function normalizeAccountOverview(account: AccountSnapshot): AccountOverview {
  return accountOverviewSchema.parse({
    walletAddress: account.walletAddress,
    ...(account.funderAddress ? { funderAddress: account.funderAddress } : {}),
    positions: account.positions.map(normalizePosition),
    openOrders: account.openOrders.map(normalizeOrder),
    fills: account.trades.map(normalizeFill),
    observedAt: account.observedAt,
  });
}

function normalizePosition(value: unknown) {
  const record = asRecord(value);
  return {
    positionId: stableIdentifier("position", record, ["asset", "asset_id", "tokenId", "token_id"]),
    conditionId: stringValue(record, "conditionId", "condition_id", "market"),
    assetId: stringValue(record, "asset", "asset_id", "tokenId", "token_id"),
    marketTitle: stringValue(record, "title", "marketTitle", "question"),
    outcome: stringValue(record, "outcome"),
    size: stringValue(record, "size"),
    averagePrice: stringValue(record, "avgPrice", "averagePrice", "average_price"),
    currentPrice: stringValue(record, "curPrice", "currentPrice", "current_price"),
    currentValue: stringValue(record, "currentValue", "current_value"),
    cashPnl: stringValue(record, "cashPnl", "cash_pnl", "pnl"),
    percentPnl: stringValue(record, "percentPnl", "percent_pnl"),
    redeemable: booleanValue(record.redeemable),
  };
}

function normalizeOrder(value: unknown) {
  const record = asRecord(value);
  const originalSize = stringValue(record, "original_size", "originalSize", "size");
  const matchedSize = stringValue(record, "size_matched", "matchedSize", "matched_size");
  return {
    orderId: stableIdentifier("order", record, ["id", "orderID", "order_id"]),
    marketId: stringValue(record, "market", "marketId", "conditionId", "condition_id"),
    assetId: stringValue(record, "asset_id", "assetId", "tokenId", "token_id"),
    outcome: stringValue(record, "outcome"),
    side: stringValue(record, "side"),
    originalSize,
    matchedSize,
    remainingSize: remainingSize(originalSize, matchedSize),
    price: stringValue(record, "price"),
    orderType: stringValue(record, "order_type", "orderType"),
    status: stringValue(record, "status"),
    createdAt: timestampValue(record.created_at ?? record.createdAt),
    expiration: timestampValue(record.expiration ?? record.expiresAt),
  };
}

function normalizeFill(value: unknown) {
  const record = asRecord(value);
  return {
    tradeId: stableIdentifier("fill", record, ["id", "tradeID", "trade_id"]),
    marketId: stringValue(record, "market", "marketId", "conditionId", "condition_id"),
    assetId: stringValue(record, "asset_id", "assetId", "tokenId", "token_id"),
    outcome: stringValue(record, "outcome"),
    side: stringValue(record, "side"),
    size: stringValue(record, "size"),
    price: stringValue(record, "price"),
    status: stringValue(record, "status"),
    matchedAt: timestampValue(record.match_time ?? record.matchTime ?? record.created_at),
    traderSide: stringValue(record, "trader_side", "traderSide"),
    transactionHash: stringValue(record, "transaction_hash", "transactionHash"),
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function stringValue(record: Record<string, unknown>, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value !== "") return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    if (typeof value === "bigint") return value.toString();
  }
  return null;
}

function stableIdentifier(prefix: string, record: Record<string, unknown>, keys: string[]): string {
  const existing = stringValue(record, ...keys);
  if (existing) return existing;
  const digest = createHash("sha256").update(JSON.stringify(record)).digest("hex");
  return `${prefix}:sha256:${digest}`;
}

function remainingSize(original: string | null, matched: string | null): string | null {
  if (original === null || matched === null) return original;
  try {
    return Decimal.max(new Decimal(original).minus(matched), 0).toString();
  } catch {
    return null;
  }
}

function timestampValue(value: unknown): string | null {
  if (value === null || value === undefined || value === "" || value === "0") return null;
  const numeric = typeof value === "number"
    ? value
    : typeof value === "string" && /^\d+(?:\.\d+)?$/.test(value)
      ? Number(value)
      : null;
  const date = numeric === null
    ? new Date(String(value))
    : new Date(numeric < 1_000_000_000_000 ? numeric * 1_000 : numeric);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function booleanValue(value: unknown): boolean {
  return value === true || value === 1 || value === "1" || value === "true";
}
