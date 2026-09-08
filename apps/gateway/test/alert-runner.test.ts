import { describe, expect, it } from "vitest";

import type { AlertCreateChannelRequest } from "@polytrade/contracts";

import { AlertDeliveryRunner, AlertService } from "../src/alert-service.js";
import type { AlertSender } from "../src/alert-sender.js";
import { CredentialCipher } from "../src/crypto.js";
import { MemoryAlertStore, type MemoryAlertEvent } from "./fakes.js";

const PRINCIPAL = "principal-1";
const STRATEGY = "11111111-1111-4111-8111-111111111111";

const cipher = new CredentialCipher(Buffer.alloc(32, 7));

interface Harness {
  store: MemoryAlertStore;
  alerts: AlertService;
  sends: Array<{ target: string; text: string }>;
}

function fakeSender(behavior: (target: string, text: string) => Promise<void>): AlertSender {
  return {
    send: (kind: string, target: string, text: string) => {
      void kind;
      return behavior(target, text);
    },
  } as unknown as AlertSender;
}

function harness(): Harness {
  const store = new MemoryAlertStore();
  const sends: Array<{ target: string; text: string }> = [];
  const sender = fakeSender(async (target, text) => {
    sends.push({ target, text });
  });
  const alerts = new AlertService(store, cipher, sender, () => new Date("2026-09-01T12:00:00Z"));
  return { store, alerts, sends };
}

function event(overrides: Partial<MemoryAlertEvent> = {}): MemoryAlertEvent {
  return {
    eventSeq: 1,
    eventId: "event-1",
    strategyId: STRATEGY,
    action: "BUY",
    message: "Bought 10 shares at 0.42",
    side: "BUY",
    price: "0.42",
    principalId: PRINCIPAL,
    marketQuestion: "Will the runner tests pass?",
    outcome: "Yes",
    ...overrides,
  };
}

const discordRequest: AlertCreateChannelRequest = {
  kind: "discord",
  label: "Trading Discord",
  target: "https://discord.com/api/webhooks/1234/abcdefghij",
  eventKinds: ["BUY", "SELL", "ERROR"],
};

describe("alert fan-out", () => {
  it("creates one delivery per subscribed channel and skips WAIT events", async () => {
    const { store, alerts } = harness();
    await alerts.createChannel(PRINCIPAL, discordRequest);
    store.events.push(
      event({ eventSeq: 1, action: "WAIT", message: "waiting" }),
      event({ eventSeq: 2, action: "BUY", message: "first buy" }),
      event({ eventSeq: 3, action: "SELL", message: "sold" }),
    );

    await alerts.fanOut();
    expect(store.deliveries.size).toBe(2);
    expect([...store.deliveries.values()].map((delivery) => delivery.action).sort())
      .toEqual(["BUY", "SELL"]);
  });

  it("delivers one row per channel when two channels subscribe", async () => {
    const { store, alerts } = harness();
    await alerts.createChannel(PRINCIPAL, discordRequest);
    await alerts.createChannel(PRINCIPAL, { ...discordRequest, label: "Second channel" });
    store.events.push(event({ eventSeq: 1 }));

    await alerts.fanOut();
    expect(store.deliveries.size).toBe(2);
  });

  it("does not fan out events to channels of other principals", async () => {
    const { store, alerts } = harness();
    await alerts.createChannel("principal-2", discordRequest);
    store.events.push(event({ eventSeq: 1, principalId: "principal-2" }));
    store.events.push(event({ eventSeq: 2, principalId: "principal-3" }));

    await alerts.fanOut();
    expect(store.deliveries.size).toBe(1);
  });

  it("ignores disabled channels and unsubscribed event kinds", async () => {
    const { store, alerts } = harness();
    await alerts.createChannel(PRINCIPAL, { ...discordRequest, eventKinds: ["BUY"] });
    const disabled = await alerts.createChannel(PRINCIPAL, { ...discordRequest, label: "Disabled" });
    const record = store.channels.get(disabled.channelId)!;
    store.channels.set(disabled.channelId, { ...record, enabled: false });
    store.events.push(
      event({ eventSeq: 1, action: "SELL" }),
      event({ eventSeq: 2, action: "BUY" }),
    );

    await alerts.fanOut();
    expect(store.deliveries.size).toBe(1);
    expect([...store.deliveries.values()][0]!.action).toBe("BUY");
  });

  it("never re-fans-out events the cursor already advanced past", async () => {
    const { store, alerts } = harness();
    store.events.push(event({ eventSeq: 1 }));

    await alerts.fanOut();
    expect(store.deliveries.size).toBe(0);

    // The channel arrives late — it only sees future events.
    await alerts.createChannel(PRINCIPAL, discordRequest);
    await alerts.fanOut();
    expect(store.deliveries.size).toBe(0);

    store.events.push(event({ eventSeq: 2 }));
    await alerts.fanOut();
    expect(store.deliveries.size).toBe(1);
  });
});

describe("alert delivery runner", () => {
  it("delivers claimed rows to the decrypted target with the alert text", async () => {
    const { store, alerts, sends } = harness();
    const channel = await alerts.createChannel(PRINCIPAL, discordRequest);
    store.events.push(event({ eventSeq: 1 }));

    const runner = new AlertDeliveryRunner(alerts, { pollIntervalMs: 3_600_000 });
    await runner.runOnce();
    expect(sends).toEqual([{
      target: discordRequest.target,
      text: "[PolyTrade paper] BUY — Bought 10 shares at 0.42\nWill the runner tests pass? (Yes)",
    }]);
    const delivery = [...store.deliveries.values()][0]!;
    expect(delivery.status).toBe("delivered");
    expect(delivery.channelId).toBe(channel.channelId);
    await runner.close();
  });

  it("does not re-deliver a delivered row on the next run", async () => {
    const { store, alerts, sends } = harness();
    await alerts.createChannel(PRINCIPAL, discordRequest);
    store.events.push(event({ eventSeq: 1 }));

    const runner = new AlertDeliveryRunner(alerts, { pollIntervalMs: 3_600_000 });
    await runner.runOnce();
    await runner.runOnce();
    expect(sends).toEqual([{
      target: discordRequest.target,
      text: "[PolyTrade paper] BUY — Bought 10 shares at 0.42\nWill the runner tests pass? (Yes)",
    }]);
    expect([...store.deliveries.values()][0]!.attempts).toBe(1);
    await runner.close();
  });

  it("retries with backoff after a failed send and exhausts at max attempts", async () => {
    const { store } = harness();
    await exhaustAfterFiveAttempts(store);
  });

  it("marks a claim whose channel vanished as failed without sending", async () => {
    const { store, alerts, sends } = harness();
    await alerts.createChannel(PRINCIPAL, discordRequest);
    store.events.push(event({ eventSeq: 1 }));
    await alerts.fanOut();

    // Strip the claimed row's target to simulate a channel gone between claim and delivery.
    const stored = [...store.deliveries.values()][0]!;
    (stored as { target: string | null }).target = null;

    const runner = new AlertDeliveryRunner(alerts, { pollIntervalMs: 3_600_000 });
    await runner.runOnce();
    expect(sends).toEqual([]);
    expect(stored.status).toBe("failed");
    expect(stored.lastError).toBe("Alert channel is gone or disabled");
    await runner.close();
  });
});

async function exhaustAfterFiveAttempts(store: MemoryAlertStore): Promise<void> {
  const alerts = new AlertService(
    store,
    cipher,
    fakeSender(async () => {
      throw new Error("delivery failed with status 500");
    }),
    () => new Date("2026-09-01T12:00:00Z"),
  );
  await alerts.createChannel(PRINCIPAL, discordRequest);
  store.events.push(event({ eventSeq: 1 }));

  for (let round = 0; round < 5; round += 1) {
    const runner = new AlertDeliveryRunner(alerts, { pollIntervalMs: 3_600_000 });
    for (const delivery of store.deliveries.values()) {
      delivery.nextAttemptAtMs = 0;
    }
    await runner.runOnce();
    await runner.close();
  }

  const delivery = [...store.deliveries.values()][0]!;
  expect(delivery.status).toBe("failed");
  expect(delivery.lastError).toBe("delivery failed with status 500");
  expect(delivery.attempts).toBe(5);
  expect(store.deliveries.size).toBe(1);
}