import type { GatewayConfig } from "./config.js";

/**
 * Delivers alert text to Discord webhooks and Telegram chats. Plain text only —
 * strategy messages are never markdown-escaped, and URLs (which embed the
 * webhook or bot token) are never logged.
 */
export class AlertSender {
  constructor(
    private readonly config: Pick<GatewayConfig, "TELEGRAM_BOT_TOKEN" | "ALERT_SEND_TIMEOUT_MS">,
    private readonly request: typeof fetch = fetch,
  ) {}

  async send(kind: "discord" | "telegram", target: string, text: string): Promise<void> {
    if (kind === "telegram" && !this.config.TELEGRAM_BOT_TOKEN) {
      throw new Error("TELEGRAM_BOT_TOKEN is not configured on the gateway");
    }
    const url = kind === "discord"
      ? new URL(target)
      : new URL(`/bot${this.config.TELEGRAM_BOT_TOKEN}/sendMessage`, "https://api.telegram.org");
    const body = kind === "discord" ? { content: text } : { chat_id: target, text };
    const response = await this.request(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(this.config.ALERT_SEND_TIMEOUT_MS),
    });
    if (!response.ok) {
      throw new Error(`${kind} delivery failed with status ${response.status}`);
    }
  }
}