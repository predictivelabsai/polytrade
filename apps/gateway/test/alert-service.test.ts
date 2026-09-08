import { describe, expect, it } from "vitest";

import type { AlertCreateChannelRequest } from "@polytrade/contracts";

import { AlertSender } from "../src/alert-sender.js";
import { AlertService } from "../src/alert-service.js";
import { CredentialCipher } from "../src/crypto.js";
import { MemoryAlertStore } from "./fakes.js";

const PRINCIPAL = "principal-1";

const cipher = new CredentialCipher(Buffer.alloc(32, 9));

function service(
  store: MemoryAlertStore,
  sends: Array<{ kind: string; target: string; text: string }> = [],
  failWith?: Error,
): AlertService {
  const sender = new AlertSender(
    { TELEGRAM_BOT_TOKEN: "1234567890", ALERT_SEND_TIMEOUT_MS: 5_000 },
    async (url, init) => {
      const parsed = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
      sends.push({
        kind: String(url).includes("api.telegram.org") ? "telegram" : "discord",
        target: String(url),
        text: String(parsed.content ?? parsed.text ?? ""),
      });
      if (failWith) throw failWith;
      return new Response(null, { status: 204 });
    },
  );
  return new AlertService(store, cipher, sender, () => new Date("2026-09-01T12:00:00Z"));
}

const discordRequest: AlertCreateChannelRequest = {
  kind: "discord",
  label: "Trading Discord",
  target: "https://discord.com/api/webhooks/1234/abcdefghij",
  eventKinds: ["BUY", "SELL"],
};

describe("AlertService channels", () => {
  it("creates a channel and returns a hint, never the target", async () => {
    const store = new MemoryAlertStore();
    const alerts = service(store);
    const channel = await alerts.createChannel(PRINCIPAL, discordRequest);
    expect(channel).toMatchObject({
      kind: "discord",
      label: "Trading Discord",
      targetHint: "discord.com/api/webhooks/…ghij",
      enabled: true,
      eventKinds: ["BUY", "SELL"],
    });
    expect(JSON.stringify(channel)).not.toContain("webhooks/1234");

    const stored = store.channels.get(channel.channelId);
    expect(stored?.encryptedTarget).toMatch(/^v1\./);
    // The encrypted envelope decrypts back to the webhook under the channel's association data.
    expect(cipher.decrypt<string>(
      stored!.encryptedTarget,
      `alert-channel:${PRINCIPAL}:${channel.channelId}`,
    )).toBe(discordRequest.target);
  });

  it("rejects a duplicate label with a conflict", async () => {
    const alerts = service(new MemoryAlertStore());
    await alerts.createChannel(PRINCIPAL, discordRequest);
    await expect(alerts.createChannel(PRINCIPAL, {
      ...discordRequest,
      target: "https://discord.com/api/webhooks/999/zzzz",
    })).rejects.toThrow(/already have a channel with that label/);
  });

  it("allows the same label for a different principal", async () => {
    const alerts = service(new MemoryAlertStore());
    await alerts.createChannel(PRINCIPAL, discordRequest);
    await expect(alerts.createChannel("principal-2", discordRequest)).resolves.toMatchObject({
      label: "Trading Discord",
    });
  });

  it("enforces the per-principal channel limit", async () => {
    const alerts = service(new MemoryAlertStore());
    for (let index = 0; index < 10; index += 1) {
      await alerts.createChannel(PRINCIPAL, { ...discordRequest, label: `Channel ${index}` });
    }
    await expect(alerts.createChannel(PRINCIPAL, { ...discordRequest, label: "One too many" }))
      .rejects.toThrow(/channel limit reached/);
  });

  it("deletes only the owner's channel and 404s unknown ids", async () => {
    const alerts = service(new MemoryAlertStore());
    const channel = await alerts.createChannel(PRINCIPAL, discordRequest);
    await expect(alerts.deleteChannel("principal-2", channel.channelId)).rejects.toThrow(/not found/);
    await expect(alerts.deleteChannel(PRINCIPAL, channel.channelId)).resolves.toBeUndefined();
    await expect(alerts.deleteChannel(PRINCIPAL, channel.channelId)).rejects.toThrow(/not found/);
  });
});

describe("AlertService testSend", () => {
  it("decrypts the target and sends the test text", async () => {
    const store = new MemoryAlertStore();
    const sends: Array<{ kind: string; target: string; text: string }> = [];
    const alerts = service(store, sends);
    const channel = await alerts.createChannel(PRINCIPAL, discordRequest);
    await expect(alerts.testSend(PRINCIPAL, channel.channelId)).resolves.toEqual({
      status: "sent",
      error: null,
    });
    expect(sends).toEqual([{
      kind: "discord",
      target: discordRequest.target,
      text: "PolyTrade test alert — strategy alert delivery is wired up.",
    }]);
  });

  it("returns a failed response instead of throwing when the send fails", async () => {
    const alerts = service(new MemoryAlertStore(), [], new Error("delivery failed with status 404"));
    const channel = await alerts.createChannel(PRINCIPAL, discordRequest);
    await expect(alerts.testSend(PRINCIPAL, channel.channelId)).resolves.toEqual({
      status: "failed",
      error: "delivery failed with status 404",
    });
  });

  it("404s test sends for unknown channels", async () => {
    const alerts = service(new MemoryAlertStore());
    await expect(alerts.testSend(PRINCIPAL, "00000000-0000-4000-8000-000000000000"))
      .rejects.toThrow(/not found/);
  });
});