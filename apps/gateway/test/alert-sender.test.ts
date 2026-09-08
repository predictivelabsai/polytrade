import { describe, expect, it } from "vitest";

import { AlertSender } from "../src/alert-sender.js";

const baseConfig = {
  TELEGRAM_BOT_TOKEN: "1234567890" as string | undefined,
  ALERT_SEND_TIMEOUT_MS: 5_000,
};

describe("AlertSender", () => {
  it("posts {content} to the Discord webhook URL", async () => {
    const requests: Array<{ url: string; body: string }> = [];
    const sender = new AlertSender(baseConfig, async (url, init) => {
      requests.push({ url: String(url), body: String(init?.body ?? "") });
      return new Response(null, { status: 204 });
    });
    const webhook = "https://discord.com/api/webhooks/1234567890/abcdef0123456789";
    await sender.send("discord", webhook, "hello discord");
    expect(requests).toEqual([
      { url: webhook, body: JSON.stringify({ content: "hello discord" }) },
    ]);
  });

  it("posts {chat_id, text} to the Telegram bot API", async () => {
    const requests: Array<{ url: string; body: string }> = [];
    const sender = new AlertSender(baseConfig, async (url, init) => {
      requests.push({ url: String(url), body: String(init?.body ?? "") });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });
    await sender.send("telegram", "123456789", "hello telegram");
    expect(requests).toEqual([{
      url: "https://api.telegram.org/bot1234567890/sendMessage",
      body: JSON.stringify({ chat_id: "123456789", text: "hello telegram" }),
    }]);
  });

  it("throws with the provider status for non-2xx replies", async () => {
    const sender = new AlertSender(baseConfig, async () => new Response(null, { status: 404 }));
    await expect(sender.send("discord", "https://discord.com/api/webhooks/123/x", "hi"))
      .rejects.toThrow("discord delivery failed with status 404");
  });

  it("fails with a clear config error when Telegram has no bot token", async () => {
    const sender = new AlertSender(
      { ...baseConfig, TELEGRAM_BOT_TOKEN: undefined },
      async () => {
        throw new Error("fetch must not be called without a token");
      },
    );
    await expect(sender.send("telegram", "123456789", "hello")).rejects.toThrow(
      "TELEGRAM_BOT_TOKEN is not configured on the gateway",
    );
  });

  it("propagates timeout aborts from fetch", async () => {
    let observedSignal: AbortSignal | null = null;
    const sender = new AlertSender(
      { ...baseConfig, ALERT_SEND_TIMEOUT_MS: 1 },
      (_url, init) => {
        observedSignal = init?.signal ?? null;
        return new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("The operation was aborted due to timeout", "TimeoutError"));
          });
        });
      },
    );
    await expect(sender.send("discord", "https://discord.com/api/webhooks/123/x", "t"))
      .rejects.toThrow();
    const signal = observedSignal as AbortSignal | null;
    expect(signal?.aborted).toBe(true);
  });
});