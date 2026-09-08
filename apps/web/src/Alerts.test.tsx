/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AlertsSettings } from "./Alerts";
import { GatewayError, type GatewayClient } from "./api";

const CHANNEL = {
  channelId: "77777777-7777-4777-8777-777777777777",
  kind: "discord" as const,
  label: "Trading Discord",
  eventKinds: ["BUY", "SELL", "ERROR"],
  enabled: true,
  targetHint: "discord.com/api/webhooks/…ghij",
  createdAt: "2026-09-01T00:00:00.000Z",
  updatedAt: "2026-09-01T00:00:00.000Z",
};

const DELIVERY = {
  deliveryId: "88888888-8888-4888-8888-888888888888",
  channelId: CHANNEL.channelId,
  channelLabel: "Trading Discord",
  channelKind: "discord" as const,
  action: "BUY" as const,
  message: "Bought 10 shares at 0.42",
  context: { marketQuestion: "Will alerts ship?", outcome: "Yes", side: "BUY" as const, price: "0.42" },
  status: "delivered" as const,
  attempts: 1,
  lastError: null,
  createdAt: "2026-09-01T00:01:00.000Z",
  deliveredAt: "2026-09-01T00:01:01.000Z",
};

function client(overrides: Partial<GatewayClient> = {}): GatewayClient {
  return {
    listAlertChannels: vi.fn(async () => ({ items: [CHANNEL] })),
    listAlertDeliveries: vi.fn(async () => ({ items: [DELIVERY], limit: 20 })),
    createAlertChannel: vi.fn(async () => ({ ...CHANNEL, channelId: "99999999-9999-4999-8999-999999999999", label: "Phone" })),
    deleteAlertChannel: vi.fn(async () => undefined),
    testAlertChannel: vi.fn(async () => ({ status: "sent" as const, error: null })),
    ...overrides,
  } as unknown as GatewayClient;
}

function renderAlerts(client: GatewayClient) {
  const onError = vi.fn();
  const onNotice = vi.fn();
  render(<AlertsSettings client={client} onError={onError} onNotice={onNotice} />);
  return { onError, onNotice };
}

beforeEach(() => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(crypto, "randomUUID").mockReturnValue("00000000-0000-4000-8000-00000000abc1" as `${string}-${string}-${string}-${string}-${string}`);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AlertsSettings", () => {
  it("loads and lists channels with a masked target hint and recent deliveries", async () => {
    render(<AlertsSettings client={client()} onError={vi.fn()} onNotice={vi.fn()} />);

    expect((await screen.findAllByText("Trading Discord")).length).toBeGreaterThan(0);
    expect(screen.getByText("discord.com/api/webhooks/…ghij")).toBeInTheDocument();
    expect(screen.getByText("Bought 10 shares at 0.42")).toBeInTheDocument();
    expect(screen.queryByText(/webhooks\/1234/)).not.toBeInTheDocument();
  });

  it("reports load failures through onError", async () => {
    const failing = client({
      listAlertChannels: vi.fn(async () => {
        throw new GatewayError("Gateway unavailable", "UNAVAILABLE", 503);
      }),
    });
    const onError = vi.fn();
    render(<AlertsSettings client={failing} onError={onError} onNotice={vi.fn()} />);
    await waitFor(() => expect(onError).toHaveBeenCalledWith("Gateway unavailable"));
  });

  it("adds a discord channel from the form and clears the draft", async () => {
    const mocked = client();
    const user = userEvent.setup();
    const onNotice = vi.fn();
    render(<AlertsSettings client={mocked} onError={vi.fn()} onNotice={onNotice} />);

    await screen.findAllByText("Trading Discord");
    await user.type(screen.getByLabelText("Alert channel label"), "Trading Discord");
    await user.type(screen.getByLabelText("Alert channel target"), "https://discord.com/api/webhooks/1234/abcdefghij");
    await user.click(screen.getByRole("button", { name: /Add channel/i }));

    await waitFor(() => expect(mocked.createAlertChannel).toHaveBeenCalled());
    const [request, key] = (mocked.createAlertChannel as ReturnType<typeof vi.fn>).mock
      .calls[0] as [unknown, string];
    expect(request).toEqual({
      kind: "discord",
      label: "Trading Discord",
      target: "https://discord.com/api/webhooks/1234/abcdefghij",
      eventKinds: ["BUY", "SELL", "ERROR"],
    });
    expect(key).toEqual(expect.any(String));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith(expect.stringContaining("saved.")));
  });

  it("toggles notification event tiles through their native checkboxes", async () => {
    const user = userEvent.setup();
    renderAlerts(client());

    const started = screen.getByRole("checkbox", { name: "STARTED" });
    expect(started).not.toBeChecked();
    await user.click(started);
    expect(started).toBeChecked();
  });

  it("shows a form error and skips the request when the target is invalid", async () => {
    const mocked = client();
    const user = userEvent.setup();
    render(<AlertsSettings client={mocked} onError={vi.fn()} onNotice={vi.fn()} />);

    await screen.findAllByText("Trading Discord");
    await user.type(screen.getByLabelText("Alert channel label"), "Bad channel");
    await user.type(screen.getByLabelText("Alert channel target"), "https://evil.example/api/webhooks/1/x");
    await user.click(screen.getByRole("button", { name: /Add channel/i }));

    expect(await screen.findByText(/Paste an HTTPS Discord webhook URL/)).toBeInTheDocument();
    expect(mocked.createAlertChannel).not.toHaveBeenCalled();
  });

  it("surfaces a failed test send as an error", async () => {
    const mocked = client({
      testAlertChannel: vi.fn(async () => ({ status: "failed" as const, error: "delivery failed with status 404" })),
    });
    const user = userEvent.setup();
    const onError = vi.fn();
    render(<AlertsSettings client={mocked} onError={onError} onNotice={vi.fn()} />);

    await screen.findAllByText("Trading Discord");
    await user.click(screen.getByRole("button", { name: /Send test/i }));

    await waitFor(() => expect(mocked.testAlertChannel).toHaveBeenCalledWith(CHANNEL.channelId, expect.any(String)));
    await waitFor(() => expect(onError).toHaveBeenCalledWith(
      expect.stringContaining("delivery failed with status 404"),
    ));
  });

  it("deletes a channel after confirmation and removes it from the list", async () => {
    const mocked = client();
    const user = userEvent.setup();
    render(<AlertsSettings client={mocked} onError={vi.fn()} onNotice={vi.fn()} />);

    await screen.findAllByText("Trading Discord");
    await user.click(screen.getByRole("button", { name: "Delete Trading Discord" }));

    await waitFor(() => expect(mocked.deleteAlertChannel).toHaveBeenCalledWith(CHANNEL.channelId, expect.any(String)));
    await waitFor(() => expect(screen.getByText("No alert channels yet — add one below.")).toBeInTheDocument());
  });
});
