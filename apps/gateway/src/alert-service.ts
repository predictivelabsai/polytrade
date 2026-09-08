import { randomUUID } from "node:crypto";

import type {
  AlertChannel,
  AlertChannelList,
  AlertChannelKind,
  AlertCreateChannelRequest,
  AlertDelivery,
  AlertDeliveryList,
  AlertTestSendResponse,
} from "@polytrade/contracts";

import type { AlertSender } from "./alert-sender.js";
import type { AlertChannelRecord, AlertDeliveryRecord, AlertStore } from "./alert-store.js";
import type { CredentialCipher } from "./crypto.js";
import { conflict, notFound } from "./errors.js";

const MAX_CHANNELS_PER_PRINCIPAL = 10;
const MAX_MESSAGE_CHARS = 1_800;
const RETRY_BASE_DELAY_MS = 30_000;
const RETRY_MAX_DELAY_MS = 900_000;
const DEFAULT_LEASE_MS = 300_000;
const DEFAULT_FAN_OUT_BATCH = 500;
const DEFAULT_DELIVERY_BATCH = 25;
const DEFAULT_POLL_INTERVAL_MS = 1_000;

export class AlertService {
  constructor(
    private readonly store: AlertStore,
    private readonly cipher: CredentialCipher,
    private readonly sender: AlertSender,
    private readonly now: () => Date = () => new Date(),
  ) {}

  async listChannels(principalId: string): Promise<AlertChannelList> {
    const records = await this.store.listChannels(principalId);
    return { items: records.map((record) => channelResponse(record)) };
  }

  async createChannel(
    principalId: string,
    request: AlertCreateChannelRequest,
    channelId = randomUUID(),
  ): Promise<AlertChannel> {
    const existing = await this.store.listChannels(principalId);
    if (existing.length >= MAX_CHANNELS_PER_PRINCIPAL) {
      throw conflict("Alert channel limit reached — remove a channel before adding another");
    }
    if (existing.some((channel) => channel.label === request.label)) {
      throw conflict("You already have a channel with that label");
    }
    const now = this.now();
    const record: AlertChannelRecord = {
      channelId,
      principalId,
      kind: request.kind,
      label: request.label,
      encryptedTarget: this.cipher.encrypt(request.target, associatedData(principalId, channelId)),
      targetHint: targetHint(request.kind, request.target),
      eventKinds: request.eventKinds,
      enabled: true,
      createdAt: now.toISOString(),
      updatedAt: now.toISOString(),
    };
    await this.store.createChannel(record);
    return channelResponse(record);
  }

  async deleteChannel(principalId: string, channelId: string): Promise<void> {
    if (!await this.store.deleteChannel(principalId, channelId)) {
      throw notFound("Alert channel not found");
    }
  }

  async testSend(principalId: string, channelId: string): Promise<AlertTestSendResponse> {
    const channel = await this.store.getChannel(principalId, channelId);
    if (!channel) throw notFound("Alert channel not found");
    const target = this.cipher.decrypt<string>(
      channel.encryptedTarget,
      associatedData(principalId, channel.channelId),
    );
    try {
      await this.sender.send(channel.kind, target, "PolyTrade test alert — strategy alert delivery is wired up.");
      return { status: "sent", error: null };
    } catch (error) {
      return { status: "failed", error: error instanceof Error ? error.message : "Test send failed" };
    }
  }

  async listDeliveries(principalId: string, limit = 20): Promise<AlertDeliveryList> {
    const records = await this.store.listDeliveries(principalId, limit);
    return { items: records.map((record) => deliveryResponse(record)), limit };
  }

  fanOut(limit = DEFAULT_FAN_OUT_BATCH): Promise<number> {
    return this.store.fanOutNewEvents(this.now(), limit);
  }

  async claimAndDeliver(owner: string, leaseMs: number, limit: number): Promise<number> {
    const now = this.now();
    const claimed = await this.store.claimDeliveries(
      owner,
      now,
      new Date(now.getTime() + leaseMs),
      limit,
    );
    await Promise.allSettled(claimed.map((delivery) => this.deliverOne(delivery, owner)));
    return claimed.length;
  }

  private async deliverOne(delivery: AlertDeliveryRecord, owner: string): Promise<void> {
    // The claim row carries the channel's encrypted target and owner, so a
    // channel deleted between fan-out and delivery never reaches the send path.
    if (!delivery.encryptedTarget || !delivery.principalId) {
      await this.store.markExhausted(delivery.deliveryId, owner, "Alert channel is gone or disabled", this.now());
      return;
    }
    try {
      const target = this.cipher.decrypt<string>(
        delivery.encryptedTarget,
        associatedData(delivery.principalId, delivery.channelId),
      );
      await this.sender.send(delivery.channelKind, target, deliveryText(delivery));
      await this.store.markDelivered(delivery.deliveryId, owner, this.now());
    } catch (error) {
      const message = error instanceof Error ? error.message : "Alert delivery failed";
      if (delivery.attempts >= delivery.maxAttempts) {
        await this.store.markExhausted(delivery.deliveryId, owner, message, this.now());
        return;
      }
      const backoff = Math.min(RETRY_BASE_DELAY_MS * 2 ** (delivery.attempts - 1), RETRY_MAX_DELAY_MS);
      await this.store.markRetry(
        delivery.deliveryId,
        owner,
        message,
        new Date(this.now().getTime() + backoff),
        this.now(),
      );
    }
  }
}

/**
 * Background runner over the alert outbox: fans new strategy events out to
 * subscribed channels, then claims and delivers pending deliveries with lease
 * recovery. Mirrors PaperStrategyBackgroundRunner.
 */
export class AlertDeliveryRunner {
  private readonly owner = randomUUID();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private currentRun: Promise<number> | null = null;

  constructor(
    private readonly alerts: AlertService,
    private readonly options: {
      pollIntervalMs?: number;
      leaseMs?: number;
      fanOutBatchSize?: number;
      deliveryBatchSize?: number;
      onError?: (error: unknown) => void;
    } = {},
  ) {}

  start(): void {
    if (this.timer) return;
    this.schedule(0);
  }

  async close(): Promise<void> {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const run = this.currentRun;
    if (run) await run.catch(() => undefined);
  }

  runOnce(): Promise<number> {
    if (this.currentRun) return Promise.resolve(0);
    const run = this.executeRun();
    this.currentRun = run;
    const clear = () => {
      if (this.currentRun === run) this.currentRun = null;
    };
    void run.then(clear, clear);
    return run;
  }

  private async executeRun(): Promise<number> {
    await this.alerts.fanOut(this.options.fanOutBatchSize);
    return this.alerts.claimAndDeliver(
      this.owner,
      this.options.leaseMs ?? DEFAULT_LEASE_MS,
      this.options.deliveryBatchSize ?? DEFAULT_DELIVERY_BATCH,
    );
  }

  private schedule(delay: number): void {
    if (this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.runOnce()
        .catch((error) => this.options.onError?.(error))
        .finally(() => this.schedule(this.options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS));
    }, delay);
    this.timer.unref?.();
  }
}

function deliveryText(delivery: AlertDeliveryRecord): string {
  const headline = `[PolyTrade paper] ${delivery.action} — ${delivery.message}`;
  const market = delivery.context.marketQuestion
    ? `${delivery.context.marketQuestion}${delivery.context.outcome ? ` (${delivery.context.outcome})` : ""}`
    : null;
  const text = market ? `${headline}\n${market}` : headline;
  return text.slice(0, MAX_MESSAGE_CHARS);
}

function channelResponse(record: AlertChannelRecord): AlertChannel {
  return {
    channelId: record.channelId,
    kind: record.kind,
    label: record.label,
    eventKinds: record.eventKinds,
    enabled: record.enabled,
    targetHint: record.targetHint,
    createdAt: record.createdAt,
    updatedAt: record.updatedAt,
  };
}

function deliveryResponse(record: AlertDeliveryRecord): AlertDelivery {
  return {
    deliveryId: record.deliveryId,
    channelId: record.channelId,
    channelLabel: record.channelLabel,
    channelKind: record.channelKind,
    action: record.action,
    message: record.message,
    context: record.context,
    status: record.status,
    attempts: record.attempts,
    lastError: record.lastError,
    createdAt: record.createdAt,
    deliveredAt: record.deliveredAt,
  };
}

function targetHint(kind: AlertChannelKind, target: string): string {
  if (kind === "telegram") return `chat ${target}`;
  return `discord.com/api/webhooks/…${target.slice(-4)}`;
}

function associatedData(principalId: string, channelId: string): string {
  return `alert-channel:${principalId}:${channelId}`;
}