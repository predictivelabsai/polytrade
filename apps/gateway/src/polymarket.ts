import {
  AssetType,
  ApiError,
  Chain,
  ClobClient,
  OrderType,
  Side,
  SignatureTypeV2,
  type ApiKeyCreds,
  type OrderResponse,
  type SignedOrder,
} from "@polymarket/clob-client-v2";
import {
  isBacktestEligibleMarket,
  marketSearchMarketSchema,
  resolvedBinaryMarketWinner,
  type CancellationSelector,
  type CreateOrderProposal,
  type MarketSearchMarket,
  type TypedData,
} from "@polytrade/contracts";
import { Decimal } from "decimal.js";
import { getAddress, hashTypedData, verifyTypedData, type Address, type Hex } from "viem";
import { z, type ZodType } from "zod";

import type { GatewayConfig } from "./config.js";
import { notFound, unavailable, validation } from "./errors.js";
import type {
  AccountSnapshot,
  BuiltOrderIntent,
  WalletSessionRecord,
} from "./types.js";

const L1_MESSAGE = "This message attests that I control the given wallet";
const DUMMY_SIGNATURE = `0x${"00".repeat(65)}` as Hex;
const objectRecord = z.record(z.string(), z.unknown());
const gammaSearchResponse = z.object({ events: z.array(objectRecord) }).passthrough();
const gammaMarketResponse = objectRecord;
const gammaMarketMetadataSchema = z
  .object({
    conditionId: z.string().min(1),
    question: z.string().min(1),
    outcomes: z.union([z.array(z.string()), z.string().min(1)]),
    clobTokenIds: z.union([z.array(z.string()), z.string().min(1)]),
    active: z.boolean(),
    closed: z.boolean(),
    acceptingOrders: z.boolean(),
  })
  .passthrough();
const gammaMarketsResponse = z.array(gammaMarketMetadataSchema);
const feeRateResponse = z.object({ base_fee: z.number().int().nonnegative() });
const positionsResponse = z.array(objectRecord);
const gammaEventTagsResponse = z
  .object({
    category: z.string().nullish(),
    tags: z.array(z.object({ label: z.string().min(1) }).passthrough()).nullish(),
  })
  .passthrough();

export interface MarketResolution {
  market: MarketSearchMarket;
  winner: string | null;
  closedTime: string | null;
  category: string | null;
  tags: string[];
  observedAt: string;
}

/**
 * Pick the scorecard category from an event's Gamma tags. The tag list is
 * ordered specific -> generic, so the first non-"All" label is the most
 * informative; the event's own category string is the fallback.
 */
export function pickCategory(tags: string[], eventCategory: string | null): string | null {
  const tag = tags.find((label) => label.trim().toLowerCase() !== "all" && label.trim().length > 0);
  if (tag) return tag.trim();
  const category = eventCategory?.trim();
  return category ? category : null;
}

type GammaMarketMetadata = z.infer<typeof gammaMarketMetadataSchema>;
type ProposalMarketIdentity = Pick<
  CreateOrderProposal,
  "marketId" | "marketQuestion" | "outcome" | "tokenId"
>;
type ClobMarketMetadata = {
  c: string;
  t: ReadonlyArray<{ t: string; o: string }>;
  ao?: boolean;
};

export interface PolymarketPort {
  searchMarkets(query: string, limit: number, state: "active" | "resolved"): Promise<unknown>;
  listActiveMarkets(input: { limit: number; offset: number; order: "volume24hr" | "liquidity" | "endDate" }): Promise<unknown>;
  getMarket(identifier: string, kind: "id" | "slug"): Promise<unknown>;
  getPublicMarket(slug: string): Promise<unknown>;
  getMarketByCondition(conditionId: string): Promise<{ market: MarketSearchMarket; observedAt: string }>;
  getMarketResolution(conditionId: string): Promise<MarketResolution>;
  getOrderBook(tokenId: string): Promise<unknown>;
  getFeeRate(tokenId: string): Promise<string>;
  getPriceHistory(tokenId: string, interval: string): Promise<unknown>;
  getRecentTrades(conditionId: string): Promise<unknown>;
  exchangeL1Credentials(challenge: {
    walletAddress: Address;
    signature: Hex;
    timestampSeconds: number;
    nonce: number;
  }): Promise<ApiKeyCreds>;
  buildOrderIntent(session: WalletSessionRecord, proposal: CreateOrderProposal): Promise<BuiltOrderIntent>;
  preflight(session: WalletSessionRecord, credentials: ApiKeyCreds, proposal: CreateOrderProposal): Promise<void>;
  reconcileOrder(
    session: WalletSessionRecord,
    credentials: ApiKeyCreds,
    signedOrderHash: string,
  ): Promise<unknown | undefined>;
  verifyOrderSignature(intent: BuiltOrderIntent, walletAddress: Address, signature: Hex): Promise<string>;
  submitOrder(
    session: WalletSessionRecord,
    credentials: ApiKeyCreds,
    intent: BuiltOrderIntent,
    signature: Hex,
    orderType: "GTC" | "GTD" | "FOK" | "FAK",
    postOnly: boolean,
  ): Promise<OrderResponse>;
  getAccount(session: WalletSessionRecord, credentials: ApiKeyCreds): Promise<AccountSnapshot>;
  cancel(
    session: WalletSessionRecord,
    credentials: ApiKeyCreds,
    selector: CancellationSelector,
  ): Promise<unknown>;
}

class AddressOnlySigner {
  constructor(private readonly address: Address) {}
  async getAddress(): Promise<string> {
    return this.address;
  }
  async _signTypedData(
    _domain: Record<string, unknown>,
    _types: Record<string, Array<{ name: string; type: string }>>,
    _value: Record<string, unknown>,
  ): Promise<string> {
    throw new Error("This signer cannot sign typed data");
  }
}

class CapturingSigner extends AddressOnlySigner {
  captured?: TypedData;

  override async _signTypedData(
    domain: Record<string, unknown>,
    types: Record<string, Array<{ name: string; type: string }>>,
    value: Record<string, unknown>,
  ): Promise<string> {
    this.captured = jsonSafe({
      domain,
      types,
      primaryType: types.TypedDataSign ? "TypedDataSign" : "Order",
      message: value,
    }) as TypedData;
    return DUMMY_SIGNATURE;
  }
}

export function buildL1TypedData(walletAddress: Address, timestampSeconds: number, nonce: number): TypedData {
  return {
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
      address: walletAddress,
      timestamp: String(timestampSeconds),
      nonce,
      message: L1_MESSAGE,
    },
  };
}

export class PolymarketAdapter implements PolymarketPort {
  private readonly publicClient: ClobClient;
  private readonly chain: Chain;

  constructor(
    private readonly config: GatewayConfig,
    private readonly request: typeof fetch = fetch,
  ) {
    if (config.POLYMARKET_CHAIN_ID !== Chain.POLYGON) {
      throw new Error("Production gateway supports Polygon mainnet only");
    }
    this.chain = Chain.POLYGON;
    this.publicClient = new ClobClient({
      host: config.POLYMARKET_CLOB_URL.replace(/\/$/, ""),
      chain: this.chain,
      throwOnError: true,
      retryOnError: true,
    });
  }

  async searchMarkets(
    query: string,
    limit: number,
    state: "active" | "resolved" = "active",
  ): Promise<unknown> {
    const url = new URL("/public-search", this.config.POLYMARKET_GAMMA_URL);
    url.searchParams.set("q", query);
    url.searchParams.set("limit_per_type", String(limit));
    url.searchParams.set("events_status", state === "active" ? "active" : "closed");
    url.searchParams.set("search_profiles", "false");
    const raw = await this.fetchJson(url, gammaSearchResponse);
    const normalizedEvents = raw.events.map(normalizeEvent);
    const events = state === "resolved"
      ? normalizedEvents
          .map((event) => ({
            ...event,
            markets: event.markets.filter(isBacktestEligibleMarket),
          }))
          .filter((event) => event.markets.length > 0)
      : normalizedEvents;
    return {
      query,
      state,
      observedAt: new Date().toISOString(),
      events: events.slice(0, limit),
    };
  }

  async listActiveMarkets(
    input: { limit: number; offset: number; order: "volume24hr" | "liquidity" | "endDate" },
  ): Promise<unknown> {
    const url = new URL("/markets", this.config.POLYMARKET_GAMMA_URL);
    url.searchParams.set("active", "true");
    url.searchParams.set("closed", "false");
    url.searchParams.set("limit", String(input.limit));
    url.searchParams.set("offset", String(input.offset));
    url.searchParams.set("order", input.order);
    url.searchParams.set("ascending", "false");
    const raw = await this.fetchJson(url, gammaMarketsResponse);
    const markets = raw
      .filter((market) => market.enableOrderBook !== false)
      .map(normalizePublicMarket)
      .filter((market) => market.clobTokenIds.length === 2);
    return {
      markets,
      hasMore: raw.length === input.limit,
      observedAt: new Date().toISOString(),
    };
  }

  async getMarket(identifier: string, kind: "id" | "slug"): Promise<unknown> {
    const path = kind === "slug" ? `/markets/slug/${encodeURIComponent(identifier)}` : `/markets/${encodeURIComponent(identifier)}`;
    const raw = await this.fetchJson(
      new URL(path, this.config.POLYMARKET_GAMMA_URL),
      gammaMarketResponse,
    );
    return { market: normalizeMarket(raw), observedAt: new Date().toISOString() };
  }

  async getPublicMarket(slug: string): Promise<unknown> {
    const raw = await this.fetchJson(
      new URL(`/markets/slug/${encodeURIComponent(slug)}`, this.config.POLYMARKET_GAMMA_URL),
      gammaMarketResponse,
      { notFoundMessage: "Public market not found" },
    );
    return { market: normalizePublicMarket(raw), observedAt: new Date().toISOString() };
  }

  async getMarketByCondition(conditionId: string): Promise<{ market: MarketSearchMarket; observedAt: string }> {
    const { market, observedAt } = await this.fetchMarketByCondition(conditionId);
    return { market, observedAt };
  }

  private async fetchMarketByCondition(conditionId: string): Promise<{
    market: MarketSearchMarket;
    raw: Record<string, unknown>;
    observedAt: string;
  }> {
    const url = new URL("/markets", this.config.POLYMARKET_GAMMA_URL);
    url.searchParams.set("condition_ids", conditionId);
    url.searchParams.set("limit", "2");
    const matches = (await this.fetchJson(url, gammaMarketsResponse))
      .filter((market) => market.conditionId === conditionId);
    if (matches.length === 0) throw validation("The paper market is not listed by Polymarket");
    if (matches.length !== 1) throw unavailable("Polymarket returned ambiguous market metadata");
    const market = marketSearchMarketSchema.safeParse(normalizeMarket(matches[0]!));
    if (!market.success) throw unavailable("Polymarket returned malformed market metadata");
    return { market: market.data, raw: matches[0]!, observedAt: new Date().toISOString() };
  }

  /**
   * Resolution + category lookup for the prediction scorecard. Gamma prices
   * the winning binary outcome at exactly 1. The /markets payload carries no
   * tags, so the category needs a second event-slug fetch; a failure there
   * degrades to a null category instead of failing the grade.
   */
  async getMarketResolution(conditionId: string): Promise<MarketResolution> {
    const { market, raw, observedAt } = await this.fetchMarketByCondition(conditionId);
    const winner = resolvedBinaryMarketWinner(market);
    let tags: string[] = [];
    let category: string | null = null;
    const events = raw.events;
    const eventSlug = Array.isArray(events)
      ? String((events[0] as Record<string, unknown> | undefined)?.slug ?? "")
      : "";
    if (eventSlug) {
      try {
        const event = await this.fetchJson(
          new URL(
            `/events/slug/${encodeURIComponent(eventSlug)}?include_tags=true`,
            this.config.POLYMARKET_GAMMA_URL,
          ),
          gammaEventTagsResponse,
        );
        tags = (event.tags ?? []).map((tag) => tag.label);
        category = pickCategory(tags, event.category ?? null);
      } catch {
        // Tags are optional enrichment; grading proceeds without a category.
      }
    }
    return { market, winner, closedTime: market.closedTime, category, tags, observedAt };
  }

  async getOrderBook(tokenId: string): Promise<unknown> {
    const book = (await this.publicClient.getOrderBook(tokenId)) as unknown as Record<string, unknown>;
    return {
      tokenId,
      market: book.market,
      bids: normalizeLevels(book.bids),
      asks: normalizeLevels(book.asks ?? book.tasks),
      minimumOrderSize: String(book.min_order_size ?? ""),
      tickSize: String(book.tick_size ?? ""),
      negativeRisk: Boolean(book.neg_risk),
      lastTradePrice: String(book.last_trade_price ?? ""),
      observedAt: new Date().toISOString(),
    };
  }

  async getFeeRate(tokenId: string): Promise<string> {
    const url = new URL("/fee-rate", this.config.POLYMARKET_CLOB_URL);
    url.searchParams.set("token_id", tokenId);
    const payload = await this.fetchJson(url, feeRateResponse);
    return new Decimal(payload.base_fee).div(10_000).toFixed(6);
  }

  async getPriceHistory(tokenId: string, interval: string): Promise<unknown> {
    const history = await this.publicClient.getPricesHistory({ market: tokenId, interval: interval as never });
    return {
      tokenId,
      interval,
      points: history.map((point) => ({ timestamp: point.t, price: String(point.p) })),
      observedAt: new Date().toISOString(),
    };
  }

  async getRecentTrades(conditionId: string): Promise<unknown> {
    const trades = await this.publicClient.getMarketTradesEvents(conditionId);
    return { conditionId, trades: jsonSafe(trades), observedAt: new Date().toISOString() };
  }

  async exchangeL1Credentials(challenge: {
    walletAddress: Address;
    signature: Hex;
    timestampSeconds: number;
    nonce: number;
  }): Promise<ApiKeyCreds> {
    const headers = {
      Accept: "application/json",
      "Content-Type": "application/json",
      POLY_ADDRESS: challenge.walletAddress,
      POLY_SIGNATURE: challenge.signature,
      POLY_TIMESTAMP: String(challenge.timestampSeconds),
      POLY_NONCE: String(challenge.nonce),
    };
    const create = await this.request(`${this.config.POLYMARKET_CLOB_URL.replace(/\/$/, "")}/auth/api-key`, {
      method: "POST",
      headers,
      signal: AbortSignal.timeout(this.config.POLYMARKET_REQUEST_TIMEOUT_MS),
    });
    if (create.ok) return normalizeCredentials(await create.json());
    if (create.status !== 400 && create.status !== 409) {
      throw unavailable("Polymarket wallet authentication is unavailable");
    }

    const derive = await this.request(`${this.config.POLYMARKET_CLOB_URL.replace(/\/$/, "")}/auth/derive-api-key`, {
      method: "GET",
      headers,
      signal: AbortSignal.timeout(this.config.POLYMARKET_REQUEST_TIMEOUT_MS),
    });
    if (!derive.ok) throw unavailable("Polymarket rejected wallet authentication");
    return normalizeCredentials(await derive.json());
  }

  async buildOrderIntent(
    session: WalletSessionRecord,
    proposal: CreateOrderProposal,
  ): Promise<BuiltOrderIntent> {
    const signer = new CapturingSigner(getAddress(session.walletAddress));
    const client = new ClobClient({
      host: this.config.POLYMARKET_CLOB_URL.replace(/\/$/, ""),
      chain: this.chain,
      signer,
      signatureType: session.signatureType as SignatureTypeV2,
      funderAddress: session.funderAddress,
      throwOnError: true,
      retryOnError: true,
    });
    const side = proposal.side === "BUY" ? Side.BUY : Side.SELL;
    let signed: SignedOrder;
    try {
      if ("price" in proposal) {
        signed = await client.createOrder({
          tokenID: proposal.tokenId,
          price: Number(proposal.price),
          size: Number(proposal.size),
          side,
          ...(proposal.expiration ? { expiration: proposal.expiration } : {}),
        });
      } else {
        signed = await client.createMarketOrder({
          tokenID: proposal.tokenId,
          amount: Number(proposal.amount),
          price: Number(proposal.limitPrice),
          side,
          orderType: proposal.execution === "FOK" ? OrderType.FOK : OrderType.FAK,
        });
      }
    } catch (error) {
      if (error instanceof ApiError) throw unavailable("Polymarket order validation is unavailable");
      throw validation(error instanceof Error ? error.message : "Invalid Polymarket order parameters");
    }
    if (!signer.captured) throw new Error("Official order builder did not request a signature");

    const order = jsonSafe(signed) as Record<string, unknown>;
    const dummy = String(order.signature ?? "");
    delete order.signature;
    let signatureSuffix: string | undefined;
    if (session.signatureType === SignatureTypeV2.POLY_1271) {
      if (!dummy.startsWith(DUMMY_SIGNATURE)) {
        throw new Error("Official order builder returned an unexpected EIP-1271 signature envelope");
      }
      signatureSuffix = dummy.slice(DUMMY_SIGNATURE.length);
    }
    return {
      typedData: signer.captured,
      unsignedOrder: order,
      ...(signatureSuffix ? { signatureSuffix } : {}),
    };
  }

  async preflight(
    session: WalletSessionRecord,
    credentials: ApiKeyCreds,
    proposal: CreateOrderProposal,
  ): Promise<void> {
    const client = this.authenticatedClient(session, credentials, true);
    let market;
    let gammaMarkets;
    try {
      const gammaUrl = new URL("/markets", this.config.POLYMARKET_GAMMA_URL);
      gammaUrl.searchParams.set("condition_ids", proposal.marketId);
      gammaUrl.searchParams.set("limit", "2");
      [market, gammaMarkets] = await Promise.all([
        this.publicClient.getClobMarketInfo(proposal.marketId),
        this.fetchJson(gammaUrl, gammaMarketsResponse),
      ]);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        throw validation("The proposal market is not an active CLOB condition");
      }
      if (error instanceof Error && "statusCode" in error) throw error;
      throw unavailable("Polymarket market validation is unavailable");
    }
    validateProposalMarketMetadata(proposal, market, gammaMarkets);

    const minimumSize = new Decimal(market.mos ?? 0);
    const proposedShares = "price" in proposal
      ? new Decimal(proposal.size)
      : proposal.side === "SELL"
        ? new Decimal(proposal.amount)
        : new Decimal(proposal.amount).div(proposal.limitPrice);
    if (minimumSize.gt(0) && proposedShares.lt(minimumSize)) {
      throw validation(`Order is below the ${minimumSize.toString()} share minimum`);
    }

    const isBuy = proposal.side === "BUY";
    let balance;
    try {
      balance = await client.getBalanceAllowance({
        asset_type: isBuy ? AssetType.COLLATERAL : AssetType.CONDITIONAL,
        ...(isBuy ? {} : { token_id: proposal.tokenId }),
      });
    } catch {
      throw unavailable("Polymarket balance and allowance validation is unavailable");
    }
    const available = normalizeOnchainBalance(balance.balance);
    const required = "price" in proposal
      ? new Decimal(proposal.size).mul(isBuy ? proposal.price : 1)
      : new Decimal(proposal.amount);
    if (available.lt(required)) throw validation("Insufficient Polymarket balance for this order");
    if (!Object.values(balance.allowances ?? {}).some((value) => normalizeOnchainBalance(value).gte(required))) {
      throw validation("Existing Polymarket allowance is insufficient; allowance setup is out of scope");
    }
  }

  async reconcileOrder(
    session: WalletSessionRecord,
    credentials: ApiKeyCreds,
    signedOrderHash: string,
  ): Promise<unknown | undefined> {
    try {
      return jsonSafe(
        await this.authenticatedClient(session, credentials, true).getOrder(signedOrderHash),
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return undefined;
      throw unavailable("Unable to reconcile the signed order with Polymarket");
    }
  }

  async verifyOrderSignature(intent: BuiltOrderIntent, walletAddress: Address, signature: Hex): Promise<string> {
    if (Number(intent.typedData.domain.chainId) !== this.chain) {
      throw validation("Order intent must use the Polygon mainnet domain");
    }
    const valid = await verifyTypedData({
      address: getAddress(walletAddress),
      domain: intent.typedData.domain,
      types: intent.typedData.types,
      primaryType: intent.typedData.primaryType,
      message: intent.typedData.message,
      signature,
    } as never);
    if (!valid) throw validation("Wallet signature does not match the order intent");
    return canonicalOrderHash(intent);
  }

  async submitOrder(
    session: WalletSessionRecord,
    credentials: ApiKeyCreds,
    intent: BuiltOrderIntent,
    signature: Hex,
    orderType: "GTC" | "GTD" | "FOK" | "FAK",
    postOnly: boolean,
  ): Promise<OrderResponse> {
    const finalSignature = `${signature}${intent.signatureSuffix ?? ""}` as Hex;
    const signedOrder = { ...intent.unsignedOrder, signature: finalSignature } as SignedOrder;
    return this.authenticatedClient(session, credentials).postOrder(
      signedOrder,
      OrderType[orderType],
      postOnly,
    );
  }

  async getAccount(session: WalletSessionRecord, credentials: ApiKeyCreds): Promise<AccountSnapshot> {
    const client = this.authenticatedClient(session, credentials, true);
    const user = session.funderAddress ?? session.walletAddress;
    const positionsUrl = new URL("/positions", this.config.POLYMARKET_DATA_URL);
    positionsUrl.searchParams.set("user", user);
    const [positions, openOrders, trades] = await Promise.all([
      this.fetchJson(positionsUrl, positionsResponse),
      client.getOpenOrders(),
      client.getTrades({}, true),
    ]);
    return {
      walletAddress: session.walletAddress,
      ...(session.funderAddress ? { funderAddress: session.funderAddress } : {}),
      positions,
      openOrders: jsonSafe(openOrders) as unknown[],
      trades: jsonSafe(trades) as unknown[],
      observedAt: new Date().toISOString(),
    };
  }

  async cancel(
    session: WalletSessionRecord,
    credentials: ApiKeyCreds,
    selector: CancellationSelector,
  ): Promise<unknown> {
    const client = this.authenticatedClient(session, credentials);
    if (selector.kind === "order") return client.cancelOrder({ orderID: selector.orderId });
    if (selector.kind === "market") {
      return client.cancelMarketOrders({
        market: selector.marketId,
        ...(selector.tokenId ? { asset_id: selector.tokenId } : {}),
      });
    }
    return client.cancelAll();
  }

  private authenticatedClient(
    session: WalletSessionRecord,
    credentials: ApiKeyCreds,
    retryOnError = false,
  ): ClobClient {
    return new ClobClient({
      host: this.config.POLYMARKET_CLOB_URL.replace(/\/$/, ""),
      chain: this.chain,
      signer: new AddressOnlySigner(getAddress(session.walletAddress)),
      creds: credentials,
      signatureType: session.signatureType as SignatureTypeV2,
      funderAddress: session.funderAddress,
      throwOnError: true,
      retryOnError,
    });
  }

  private async fetchJson<T>(url: URL, schema: ZodType<T>, options: { notFoundMessage?: string } = {}): Promise<T> {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const response = await this.request(url, {
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(this.config.POLYMARKET_REQUEST_TIMEOUT_MS),
        });
        if (!response.ok) {
          if (response.status === 404 && options.notFoundMessage) {
            throw notFound(options.notFoundMessage);
          }
          if ((response.status === 429 || response.status >= 500) && attempt < 2) {
            await delay(100 * (attempt + 1));
            continue;
          }
          throw unavailable(`Polymarket data request failed (${response.status})`);
        }
        const parsed = schema.safeParse(await response.json());
        if (!parsed.success) throw unavailable("Polymarket returned malformed data");
        return parsed.data;
      } catch (error) {
        if (error instanceof Error && "statusCode" in error) throw error;
        if (attempt < 2) {
          await delay(100 * (attempt + 1));
          continue;
        }
        throw unavailable("Polymarket data is temporarily unavailable");
      }
    }
    throw unavailable("Polymarket data is temporarily unavailable");
  }
}

export function canonicalOrderHash(intent: BuiltOrderIntent): string {
  if (Number(intent.unsignedOrder.signatureType) !== SignatureTypeV2.POLY_1271) {
    return hashTypedData({
      domain: intent.typedData.domain,
      types: intent.typedData.types,
      primaryType: intent.typedData.primaryType,
      message: intent.typedData.message,
    } as never);
  }

  const contents = intent.typedData.message.contents;
  const orderType = intent.typedData.types.Order;
  if (!contents || typeof contents !== "object" || !orderType) {
    throw validation("EIP-1271 order intent is missing its canonical order payload");
  }
  return hashTypedData({
    domain: intent.typedData.domain,
    types: { Order: orderType },
    primaryType: "Order",
    message: contents,
  } as never);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function normalizeCredentials(raw: unknown): ApiKeyCreds {
  const value = raw as Record<string, unknown>;
  const key = value.apiKey ?? value.key;
  if (typeof key !== "string" || typeof value.secret !== "string" || typeof value.passphrase !== "string") {
    throw unavailable("Polymarket returned invalid API credentials");
  }
  return { key, secret: value.secret, passphrase: value.passphrase };
}

export function normalizeOnchainBalance(value: string): Decimal {
  // Both Polymarket collateral and conditional-token balances are returned in
  // six-decimal on-chain base units. Never guess from magnitude: doing so would
  // dangerously treat a sub-token balance such as "500000" as 500,000 units.
  return new Decimal(value || 0).div(1_000_000);
}

export function validateProposalMarketMetadata(
  proposal: ProposalMarketIdentity,
  clobMarket: ClobMarketMetadata,
  gammaMarkets: GammaMarketMetadata[],
): void {
  if (clobMarket.c !== proposal.marketId) {
    throw validation("Market condition does not match the proposal");
  }

  const clobToken = clobMarket.t.find((token) => token.t === proposal.tokenId);
  if (!clobToken) throw validation("Outcome token does not belong to the proposal market");
  if (clobToken.o !== proposal.outcome) {
    throw validation("Outcome label does not match the proposal token");
  }
  if (clobMarket.ao !== true) {
    throw validation("Polymarket is not accepting orders for this market");
  }

  const matchingMarkets = gammaMarkets.filter((market) => market.conditionId === proposal.marketId);
  if (matchingMarkets.length === 0) {
    throw validation("The proposal condition is not listed by Polymarket Gamma");
  }
  if (matchingMarkets.length !== 1) {
    throw unavailable("Polymarket returned ambiguous market metadata");
  }

  const gammaMarket = matchingMarkets[0]!;
  if (gammaMarket.question !== proposal.marketQuestion) {
    throw validation("Market question does not match current Polymarket metadata");
  }
  if (!gammaMarket.active || gammaMarket.closed || !gammaMarket.acceptingOrders) {
    throw validation("Polymarket is not accepting orders for this market");
  }

  const tokenIds = parseRequiredStringArray(gammaMarket.clobTokenIds, "token IDs");
  const outcomes = parseRequiredStringArray(gammaMarket.outcomes, "outcomes");
  if (tokenIds.length !== outcomes.length) {
    throw unavailable("Polymarket returned inconsistent market metadata");
  }
  const tokenIndex = tokenIds.indexOf(proposal.tokenId);
  if (tokenIndex < 0) throw validation("Outcome token does not belong to the Gamma market");
  if (outcomes[tokenIndex] !== proposal.outcome || outcomes[tokenIndex] !== clobToken.o) {
    throw validation("Outcome mapping does not match current Polymarket metadata");
  }
}

function parseRequiredStringArray(value: string[] | string, label: string): string[] {
  if (Array.isArray(value)) return value;
  try {
    const parsed = JSON.parse(value) as unknown;
    if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string")) return parsed;
  } catch {
    // Converted into a sanitized upstream-data error below.
  }
  throw unavailable(`Polymarket returned malformed ${label}`);
}

function normalizeLevels(raw: unknown): Array<{ price: string; size: string }> {
  if (!Array.isArray(raw)) return [];
  return raw.slice(0, 100).flatMap((entry) => {
    const value = entry as Record<string, unknown>;
    if (value.price === undefined || value.size === undefined) return [];
    return [{ price: String(value.price), size: String(value.size) }];
  });
}

function normalizeEvent(raw: Record<string, unknown>) {
  const markets = Array.isArray(raw.markets) ? raw.markets : [];
  return {
    id: String(raw.id ?? ""),
    slug: String(raw.slug ?? ""),
    title: String(raw.title ?? ""),
    description: String(raw.description ?? ""),
    endDate: normalizeTimestamp(raw.endDate),
    liquidity: String(raw.liquidity ?? ""),
    volume: String(raw.volume ?? ""),
    markets: markets.map((market) => normalizeMarket(market as Record<string, unknown>)),
  };
}

function normalizeMarket(raw: Record<string, unknown>) {
  return {
    id: String(raw.id ?? ""),
    conditionId: String(raw.conditionId ?? ""),
    slug: String(raw.slug ?? ""),
    question: String(raw.question ?? ""),
    description: String(raw.description ?? ""),
    outcomes: parseArray(raw.outcomes).map(String),
    outcomePrices: parseArray(raw.outcomePrices).map(String),
    clobTokenIds: parseArray(raw.clobTokenIds).map(String),
    active: Boolean(raw.active),
    closed: Boolean(raw.closed),
    acceptingOrders: Boolean(raw.acceptingOrders),
    enableOrderBook: Boolean(raw.enableOrderBook),
    archived: Boolean(raw.archived),
    restricted: Boolean(raw.restricted),
    minimumOrderSize: String(raw.orderMinSize ?? ""),
    minimumTickSize: String(raw.orderPriceMinTickSize ?? ""),
    endDate: normalizeTimestamp(raw.endDate),
    startDate: normalizeTimestamp(raw.startDate),
    createdAt: normalizeTimestamp(raw.createdAt),
    closedTime: normalizeTimestamp(raw.closedTime),
    liquidity: String(raw.liquidity ?? ""),
    volume: String(raw.volume ?? ""),
  };
}

function normalizePublicMarket(raw: Record<string, unknown>) {
  return {
    ...normalizeMarket(raw),
    ...(raw.icon ? { icon: String(raw.icon) } : {}),
    ...(raw.volume24hr !== undefined && raw.volume24hr !== null ? { volume24hr: String(raw.volume24hr) } : {}),
  };
}

function parseArray(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string") return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function normalizeTimestamp(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  const date = new Date(typeof value === "number" && value < 1_000_000_000_000 ? value * 1_000 : String(value));
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function jsonSafe<T>(value: T): T {
  return JSON.parse(JSON.stringify(value, (_key, item) => (typeof item === "bigint" ? item.toString() : item))) as T;
}
