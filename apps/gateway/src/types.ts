import type { ApiKeyCreds } from "@polymarket/clob-client-v2";
import type { CreateOrderProposal, TypedData } from "@polytrade/contracts";

export interface Principal {
  id: string;
  issuer: "assethero" | "clerk";
  subject: string;
  scopes: ReadonlySet<string>;
}

export interface ChallengeRecord {
  id: string;
  principalId: string;
  walletAddress: `0x${string}`;
  signatureType: 0 | 1 | 2 | 3;
  funderAddress?: `0x${string}`;
  timestampSeconds: number;
  nonce: number;
  typedData: TypedData;
  expiresAt: Date;
  usedAt?: Date;
}

export interface WalletSessionRecord {
  id: string;
  principalId: string;
  walletAddress: `0x${string}`;
  signatureType: 0 | 1 | 2 | 3;
  funderAddress?: `0x${string}`;
  encryptedCredentials: string;
  idleExpiresAt: Date;
  absoluteExpiresAt: Date;
  lastUsedAt: Date;
  revokedAt?: Date;
}

export interface BuiltOrderIntent {
  typedData: TypedData;
  unsignedOrder: Record<string, unknown>;
  signatureSuffix?: string;
}

export interface OrderIntentRecord extends BuiltOrderIntent {
  id: string;
  principalId: string;
  sessionId: string;
  idempotencyKey: string;
  proposal: CreateOrderProposal;
  orderType: "GTC" | "GTD" | "FOK" | "FAK";
  postOnly: boolean;
  status: "PENDING" | "SUBMITTING" | "SUBMITTED" | "REJECTED" | "AMBIGUOUS" | "EXPIRED";
  signedOrderHash?: string;
  upstreamResponse?: unknown;
  expiresAt: Date;
  submittedAt?: Date;
}

export interface AccountSnapshot {
  walletAddress: string;
  funderAddress?: string;
  positions: unknown[];
  openOrders: unknown[];
  trades: unknown[];
  observedAt: string;
}

export type { ApiKeyCreds };
