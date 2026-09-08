/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import type { GatewayClient } from "./api";
import { PaperWorkspace } from "./Paper";
import { ShareCard } from "./TrackRecord";
import type { MarketSearchMarket } from "@polytrade/contracts";

// CI has no .env.local; ShareCard's module pulls env at import time.
vi.mock("./env", () => ({
  env: {
    VITE_API_URL: "https://api.polytrade.test",
    VITE_CLERK_PUBLISHABLE_KEY: "pk_test",
    VITE_CLERK_JWT_TEMPLATE: "polytrade",
  },
}));

const portfolio = {
  initialCash: "10000.000000",
  cash: "10000.000000",
  positionsValue: "0.000000",
  equity: "10000.000000",
  realizedPnl: "0.000000",
  unrealizedPnl: "0.000000",
  totalPnl: "0.000000",
  totalFees: "0.000000",
  positions: [],
  warnings: [],
  observedAt: "2026-08-04T00:00:00.000Z",
};

const noShare = { token: null, enabled: false, createdAt: null, updatedAt: null };

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("PaperWorkspace live dashboard", () => {
  it("polls persistent strategy, portfolio, and first-page fills every three seconds", async () => {
    vi.useFakeTimers();
    const client = {
      refreshPaperPortfolio: vi.fn(async () => portfolio),
      paperPortfolio: vi.fn(async () => portfolio),
      paperFills: vi.fn(async () => ({ items: [], total: 0, offset: 0, limit: 20 })),
      paperStrategy: vi.fn(async () => ({ strategy: null, events: [] })),
      paperShareStatus: vi.fn(async () => noShare),
    } as unknown as GatewayClient;
    const view = render(<PaperWorkspace client={client} onError={vi.fn()} onNotice={vi.fn()} />);

    await act(async () => flushPromises());
    expect(screen.getByRole("heading", { name: "Paper trading" })).toBeInTheDocument();
    vi.mocked(client.paperPortfolio).mockClear();
    vi.mocked(client.paperStrategy).mockClear();
    vi.mocked(client.paperFills).mockClear();

    await act(async () => {
      vi.advanceTimersByTime(3_000);
      await flushPromises();
    });

    expect(client.paperPortfolio).toHaveBeenCalledOnce();
    expect(client.paperStrategy).toHaveBeenCalledOnce();
    expect(client.paperFills).toHaveBeenCalledWith(20, 0);

    view.unmount();
    vi.mocked(client.paperPortfolio).mockClear();
    vi.advanceTimersByTime(3_000);
    expect(client.paperPortfolio).not.toHaveBeenCalled();
  });

});

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("ShareCard", () => {
  it("creates a link from the private state and shows the share URL", async () => {
    const token = "a".repeat(32);
    const client = {
      paperShareStatus: vi.fn(async () => noShare),
      enablePaperShare: vi.fn(async () => ({
        token,
        enabled: true,
        createdAt: "2026-09-02T00:00:00.000Z",
        updatedAt: "2026-09-02T00:00:00.000Z",
      })),
    } as unknown as GatewayClient;
    const onNotice = vi.fn();
    render(<ShareCard client={client} onNotice={onNotice} onError={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: /Create share link/ }));
    expect(client.enablePaperShare).toHaveBeenCalledWith(false);
    expect(await screen.findByText("Public")).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`/u/${token}`))).toBeInTheDocument();
    expect(onNotice).toHaveBeenCalledWith("Share link created.");
  });

  it("flips to private when the link is disabled", async () => {
    const token = "b".repeat(32);
    const client = {
      paperShareStatus: vi.fn(async () => ({
        token,
        enabled: true,
        createdAt: "2026-09-02T00:00:00.000Z",
        updatedAt: "2026-09-02T00:00:00.000Z",
      })),
      enablePaperShare: vi.fn(),
      disablePaperShare: vi.fn(async () => ({ token, enabled: false, createdAt: null, updatedAt: null })),
    } as unknown as GatewayClient;
    render(<ShareCard client={client} onNotice={vi.fn()} onError={vi.fn()} />);

    expect(await screen.findByText("Public")).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`/u/${token}`))).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Disable sharing/ }));
    expect(await screen.findByText("Private")).toBeInTheDocument();
    expect(client.disablePaperShare).toHaveBeenCalledOnce();
  });
});

const templateMarket: MarketSearchMarket = {
  id: "template-market",
  conditionId: "0xtemplatecondition",
  slug: "template-market",
  question: "Will the template market resolve?",
  description: "Fixture market",
  outcomes: ["Yes", "No"],
  outcomePrices: ["0.90", "0.10"],
  clobTokenIds: ["100", "101"],
  active: true,
  closed: false,
  acceptingOrders: true,
  enableOrderBook: true,
  archived: false,
  restricted: false,
  minimumOrderSize: "5",
  minimumTickSize: "0.01",
  endDate: null,
  startDate: null,
  createdAt: null,
  closedTime: null,
  liquidity: "100000",
  volume: "500000",
};

function templateClient() {
  return {
    refreshPaperPortfolio: vi.fn(async () => portfolio),
    paperPortfolio: vi.fn(async () => portfolio),
    paperFills: vi.fn(async () => ({ items: [], total: 0, offset: 0, limit: 20 })),
    paperStrategy: vi.fn(async () => ({ strategy: null, events: [] })),
    paperShareStatus: vi.fn(async () => noShare),
    searchMarkets: vi.fn(async () => ({ query: "", state: "active", observedAt: "2026-09-02T00:00:00.000Z", events: [{ id: "e1", title: "t", markets: [templateMarket] }] })),
    startPaperStrategy: vi.fn(async () => ({ strategy: null, events: [] })),
  } as unknown as GatewayClient;
}

async function selectTemplateMarket(client: GatewayClient) {
  await act(async () => flushPromises());
  // The mounted template's suggested search has already run; pick its result.
  const result = await screen.findByRole("button", { name: /Will the template market resolve/ });
  await userEvent.click(result);
  await act(async () => flushPromises());
}

describe("PaperWorkspace strategy templates", () => {
  it("renders the template cards", async () => {
    render(<PaperWorkspace client={templateClient()} onError={vi.fn()} onNotice={vi.fn()} />);
    await act(async () => flushPromises());
    expect(screen.getByText("Base-rate divergence")).toBeInTheDocument();
    expect(screen.getByText("Longshot fade")).toBeInTheDocument();
    expect(screen.getByText("EV sniping")).toBeInTheDocument();
    expect(screen.getByText("Overreaction fade")).toBeInTheDocument();
    expect(screen.getByText("Resolution grinder")).toBeInTheDocument();
    expect(screen.getAllByText("Illustrative").length).toBeGreaterThanOrEqual(5);
    expect(screen.getByRole("region", { name: "First paper strategy guide" })).toHaveTextContent("Choose a ready-made plan");
  });

  it("arms the template and pre-fills the search on deploy", async () => {
    const client = templateClient();
    render(<PaperWorkspace client={client} onError={vi.fn()} onNotice={vi.fn()} />);
    await act(async () => flushPromises());
    await userEvent.click(screen.getAllByRole("button", { name: /Deploy to paper/ })[1]!);
    await act(async () => flushPromises());
    expect(client.searchMarkets).toHaveBeenCalledWith("election winner", "active", 20);
    expect(screen.getByText(/Template · Longshot fade/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "First paper strategy guide" })).toHaveTextContent("Choose a live market");
  });

  it("lets a trader dismiss the first-value guide permanently", async () => {
    render(<PaperWorkspace client={templateClient()} onError={vi.fn()} onNotice={vi.fn()} />);
    await act(async () => flushPromises());

    await userEvent.click(screen.getByRole("button", { name: "Dismiss first paper strategy guide" }));
    expect(screen.queryByRole("region", { name: "First paper strategy guide" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem("polytrade.first-value-guide-dismissed")).toBe("true");
  });

  it("opens an active market handed off from another workspace", async () => {
    render(
      <MemoryRouter>
        <PaperWorkspace client={templateClient()} onError={vi.fn()} onNotice={vi.fn()} initialMarket={templateMarket} />
      </MemoryRouter>,
    );
    await act(async () => flushPromises());

    expect(screen.getByText("Market in focus")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Ask agent/ })).toHaveAttribute("href", "/chat/new?focus=0xtemplatecondition");
  });

  it("starts a template strategy with template-derived absolute prices", async () => {
    const client = templateClient();
    render(
      <PaperWorkspace
        client={client}
        onError={vi.fn()}
        onNotice={vi.fn()}
        initialTemplateId="longshot-fade"
      />,
    );
    await selectTemplateMarket(client);
    await userEvent.click(await screen.findByRole("button", { name: /Start in background/ }));
    expect(client.startPaperStrategy).toHaveBeenCalledWith(
      expect.objectContaining({
        conditionId: "0xtemplatecondition",
        tokenId: "100",
        entryPrice: "0.88",
        exitPrice: "0.93",
        sharesPerOrder: "20",
        maxPosition: "80",
        intervalSeconds: 30,
      }),
      expect.any(String),
    );
  });

  it("clears the template when the user edits a field", async () => {
    const client = templateClient();
    render(
      <PaperWorkspace
        client={client}
        onError={vi.fn()}
        onNotice={vi.fn()}
        initialTemplateId="longshot-fade"
      />,
    );
    await selectTemplateMarket(client);
    const buyInput = await screen.findByLabelText("Strategy buy price");
    await userEvent.clear(buyInput);
    await userEvent.type(buyInput, "0.5");
    await userEvent.click(await screen.findByRole("button", { name: /Start in background/ }));
    expect(client.startPaperStrategy).toHaveBeenCalledWith(
      expect.objectContaining({ entryPrice: "0.5" }),
      expect.any(String),
    );
  });

  it("keeps the default auto-fit when no template is armed", async () => {
    const client = templateClient();
    render(<PaperWorkspace client={client} onError={vi.fn()} onNotice={vi.fn()} />);
    await act(async () => flushPromises());
    await userEvent.type(screen.getByLabelText(/Search active Polymarket markets/), "fed");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await act(async () => flushPromises());
    await userEvent.click(await screen.findByRole("button", { name: /Will the template market resolve/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Start in background/ }));
    expect(client.startPaperStrategy).toHaveBeenCalledWith(
      expect.objectContaining({ entryPrice: "0.88", exitPrice: "0.95", sharesPerOrder: "10", maxPosition: "50" }),
      expect.any(String),
    );
  });
});
