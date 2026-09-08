/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";

import RoutedApp from "./RoutedApp";

const THREAD_A = "11111111-1111-4111-8111-111111111111";
const THREAD_B = "22222222-2222-4222-8222-222222222222";
const RUN_ID = "33333333-3333-4333-8333-333333333333";
const MEAN_REVERSION_RUN_ID = "88888888-8888-4888-8888-888888888888";
const BREAKOUT_RUN_ID = "99999999-9999-4999-8999-999999999999";
const WALLET = "0x0000000000000000000000000000000000000001";

const mocks = vi.hoisted(() => {
  class GatewayError extends Error {
    constructor(message: string, readonly code: string, readonly status: number) {
      super(message);
    }
  }
  return {
    GatewayError,
    accountOverview: vi.fn(),
    attachWallet: vi.fn(),
    backtestCreate: vi.fn(),
    backtestGet: vi.fn(),
    backtestList: vi.fn(),
    browserEligibility: vi.fn(),
    createAgentThread: vi.fn(),
    createChallenge: vi.fn(),
    createWalletSession: vi.fn(),
    currentWalletSession: vi.fn(),
    deleteAgentThread: vi.fn(),
    getAgentThreadItems: vi.fn(),
    listAgentThreads: vi.fn(),
    listAlertChannels: vi.fn(),
    listAlertDeliveries: vi.fn(),
    createAlertChannel: vi.fn(),
    deleteAlertChannel: vi.fn(),
    testAlertChannel: vi.fn(),
    paperFills: vi.fn(),
    paperOrder: vi.fn(),
    paperPortfolio: vi.fn(),
    paperQuote: vi.fn(),
    paperShareStatus: vi.fn(async () => ({ token: null, enabled: false, createdAt: null, updatedAt: null })),
    enablePaperShare: vi.fn(),
    disablePaperShare: vi.fn(),
    paperStrategy: vi.fn(),
    refreshPaperPortfolio: vi.fn(),
    runAgentTurn: vi.fn(),
    searchMarkets: vi.fn(),
    signTypedPayload: vi.fn(),
    startPaperStrategy: vi.fn(),
    stopPaperStrategy: vi.fn(),
  };
});

vi.mock("./env", () => ({
  env: {
    VITE_API_URL: "https://api.polytrade.test",
    VITE_CLERK_PUBLISHABLE_KEY: "pk_test",
    VITE_CLERK_JWT_TEMPLATE: "polytrade",
  },
}));

vi.mock("./auth", () => ({
  useAuthentication: () => ({
    getToken: async () => "token",
    accountControl: <span data-testid="profile">Profile</span>,
  }),
}));

vi.mock("./agent", async (importOriginal) => {
  const original = await importOriginal<typeof import("./agent")>();
  return {
    ...original,
    createAgentThread: mocks.createAgentThread,
    deleteAgentThread: mocks.deleteAgentThread,
    getAgentThreadItems: mocks.getAgentThreadItems,
    listAgentThreads: mocks.listAgentThreads,
    runAgentTurn: mocks.runAgentTurn,
  };
});

vi.mock("./api", () => ({
  GatewayError: mocks.GatewayError,
  GatewayClient: class GatewayClient {
    currentWalletSession = mocks.currentWalletSession;
    accountOverview = mocks.accountOverview;
    searchMarkets = mocks.searchMarkets;
    paperPortfolio = mocks.paperPortfolio;
    paperQuote = mocks.paperQuote;
    paperShareStatus = mocks.paperShareStatus;
    enablePaperShare = mocks.enablePaperShare;
    disablePaperShare = mocks.disablePaperShare;
    paperStrategy = mocks.paperStrategy;
    paperOrder = mocks.paperOrder;
    refreshPaperPortfolio = mocks.refreshPaperPortfolio;
    paperFills = mocks.paperFills;
    startPaperStrategy = mocks.startPaperStrategy;
    stopPaperStrategy = mocks.stopPaperStrategy;
    listAlertChannels = mocks.listAlertChannels;
    createAlertChannel = mocks.createAlertChannel;
    deleteAlertChannel = mocks.deleteAlertChannel;
    testAlertChannel = mocks.testAlertChannel;
    listAlertDeliveries = mocks.listAlertDeliveries;
    createChallenge = mocks.createChallenge;
    createWalletSession = mocks.createWalletSession;
    revokeWalletSession = vi.fn();
    cancel = vi.fn();
    createIntent = vi.fn();
    submitIntent = vi.fn();
  },
}));

vi.mock("./backtest", () => ({
  BacktestClient: class BacktestClient {
    list = mocks.backtestList;
    get = mocks.backtestGet;
    create = mocks.backtestCreate;
    trades = vi.fn(async () => ({ items: [], total: 0, offset: 0, limit: 50, runId: RUN_ID }));
    series = vi.fn(async () => ({ points: [], runId: RUN_ID }));
    cancel = vi.fn();
    delete = vi.fn();
  },
}));

vi.mock("./eligibility", () => ({
  checkBrowserEligibility: mocks.browserEligibility,
}));

vi.mock("./wallet", () => ({
  connectWallet: mocks.attachWallet,
  signTypedPayload: mocks.signTypedPayload,
}));

const summaryA = {
  threadId: THREAD_A,
  title: "Election liquidity",
  createdAt: "2026-08-03T00:00:00.000Z",
  updatedAt: "2026-08-03T00:05:00.000Z",
  expiresAt: "2026-09-02T00:00:00.000Z",
};
const summaryB = {
  threadId: THREAD_B,
  title: "Fed order book",
  createdAt: "2026-08-02T00:00:00.000Z",
  updatedAt: "2026-08-02T00:05:00.000Z",
  expiresAt: "2026-09-01T00:00:00.000Z",
};
const session = {
  sessionId: "44444444-4444-4444-8444-444444444444",
  walletAddress: WALLET,
  signatureType: 0 as const,
  idleExpiresAt: "2026-08-04T01:00:00.000Z",
  expiresAt: "2026-08-04T08:00:00.000Z",
};
const overview = {
  walletAddress: WALLET,
  positions: [],
  openOrders: [],
  fills: [],
  observedAt: "2026-08-04T00:00:00.000Z",
};
const paperPortfolio = {
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
const paperMarket = {
  id: "paper-market",
  conditionId: "paper-condition",
  slug: "paper-market",
  question: "Will the paper workspace pass?",
  description: "",
  outcomes: ["Yes", "No"],
  outcomePrices: ["0.40", "0.60"],
  clobTokenIds: ["123", "456"],
  active: true,
  closed: false,
  acceptingOrders: true,
  enableOrderBook: true,
  archived: false,
  restricted: false,
  minimumOrderSize: "1",
  minimumTickSize: "0.01",
  endDate: null,
  startDate: null,
  createdAt: null,
  closedTime: null,
  liquidity: "1000",
  volume: "5000",
};
const paperQuote = {
  conditionId: "paper-condition",
  tokenId: "123",
  marketQuestion: "Will the paper workspace pass?",
  outcome: "Yes",
  side: "BUY" as const,
  shares: "10.000000",
  averagePrice: "0.410000",
  limitPrice: "0.420000",
  grossNotional: "4.100000",
  feeRate: "0.040000",
  fee: "0.09840",
  cashEffect: "-4.198400",
  observedAt: "2026-08-04T00:00:00.000Z",
};
const paperFill = {
  fillId: "55555555-5555-4555-8555-555555555555",
  kind: "BUY" as const,
  conditionId: "paper-condition",
  tokenId: "123",
  marketQuestion: "Will the paper workspace pass?",
  outcome: "Yes",
  shares: "10.000000",
  averagePrice: "0.410000",
  grossNotional: "4.100000",
  feeRate: "0.040000",
  fee: "0.09840",
  cashEffect: "-4.198400",
  realizedPnl: "0.000000",
  observedAt: "2026-08-04T00:00:00.000Z",
  createdAt: "2026-08-04T00:00:00.000Z",
};
const runningPaperStrategy = {
  strategy: {
    strategyId: "66666666-6666-4666-8666-666666666666",
    conditionId: "paper-condition",
    tokenId: "123",
    marketQuestion: "Will the paper workspace pass?",
    outcome: "Yes",
    entryPrice: "0.380000",
    exitPrice: "0.450000",
    sharesPerOrder: "10.000000",
    maxPosition: "50.000000",
    intervalSeconds: 15,
    status: "RUNNING" as const,
    ordersPlaced: 0,
    scansCompleted: 0,
    lastAction: "STARTED" as const,
    lastMessage: "Strategy started. Waiting for the background runner.",
    lastQuoteSide: null,
    lastQuotePrice: null,
    lastScannedAt: null,
    nextScanAt: "2026-08-04T00:00:15.000Z",
    startedAt: "2026-08-04T00:00:00.000Z",
    stoppedAt: null,
    updatedAt: "2026-08-04T00:00:00.000Z",
  },
  events: [{
    eventId: "77777777-7777-4777-8777-777777777777",
    action: "STARTED" as const,
    message: "Watching Yes in the background.",
    side: null,
    price: null,
    fillId: null,
    createdAt: "2026-08-04T00:00:00.000Z",
  }],
};
const run = {
  runId: RUN_ID,
  marketId: "condition-1",
  marketQuestion: "Will the activity order be clear?",
  status: "running" as const,
  phase: "simulating" as const,
  progress: 65,
  config: {
    strategy: "momentum_v1" as const,
    initialCapital: "10000",
    positionSizePct: "0.10",
    momentumWindowMinutes: 60,
    momentumThreshold: "0.05",
    takeProfit: "0.10",
    stopLoss: "0.05",
    maxHoldMinutes: 1440,
    cooldownMinutes: 60,
    slippage: "0.01",
    maxFillDelayMinutes: 5,
  },
  cancelRequested: false,
  warnings: [],
  createdAt: "2026-08-04T00:00:00.000Z",
};

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function renderRoute(route: string) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <RoutedApp />
      <LocationProbe />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn((query: string) => ({
      matches: query.includes("min-width: 1200px"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  mocks.listAgentThreads.mockResolvedValue([summaryA, summaryB]);
  mocks.getAgentThreadItems.mockImplementation(async (_url: string, _token: unknown, threadId: string) => ([{
    kind: "message",
    id: `${threadId}-message`,
    role: "assistant",
    text: threadId === THREAD_A ? "Election answer" : "Fed answer",
  }]));
  mocks.createAgentThread.mockResolvedValue(THREAD_A);
  mocks.deleteAgentThread.mockResolvedValue(undefined);
  mocks.browserEligibility.mockResolvedValue({ blocked: false, verified: true, country: "US", region: "NY", checkedAt: new Date().toISOString() });
  mocks.currentWalletSession.mockRejectedValue(new mocks.GatewayError("No active wallet session", "NOT_FOUND", 404));
  mocks.accountOverview.mockResolvedValue(overview);
  mocks.paperPortfolio.mockResolvedValue(paperPortfolio);
  mocks.refreshPaperPortfolio.mockResolvedValue(paperPortfolio);
  mocks.paperFills.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  mocks.paperQuote.mockResolvedValue(paperQuote);
  mocks.paperOrder.mockResolvedValue({ fill: paperFill, portfolio: paperPortfolio });
  mocks.paperStrategy.mockResolvedValue({ strategy: null, events: [] });
  mocks.listAlertChannels.mockResolvedValue({ items: [] });
  mocks.listAlertDeliveries.mockResolvedValue({ items: [], limit: 20 });
  mocks.startPaperStrategy.mockImplementation(async () => {
    mocks.paperStrategy.mockResolvedValue(runningPaperStrategy);
    return runningPaperStrategy;
  });
  mocks.stopPaperStrategy.mockImplementation(async () => {
    const stopped = {
      strategy: { ...runningPaperStrategy.strategy, status: "STOPPED" as const, lastAction: "STOPPED" as const, lastMessage: "Strategy stopped by the user.", nextScanAt: null, stoppedAt: "2026-08-04T00:01:00.000Z" },
      events: runningPaperStrategy.events,
    };
    mocks.paperStrategy.mockResolvedValue(stopped);
    return stopped;
  });
  mocks.backtestList.mockResolvedValue([]);
  mocks.backtestGet.mockResolvedValue({ run, result: null });
  mocks.attachWallet.mockResolvedValue({ address: WALLET, provider: {} });
  mocks.runAgentTurn.mockImplementation(async (options: Parameters<typeof import("./agent").runAgentTurn>[0]) => {
    options.handlers.onMessageStart("assistant-stream");
    options.handlers.onMessageText("assistant-stream", "Streamed answer");
  });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("routed workspace", () => {
  it("loads deep-linked threads and switches without mixing messages", async () => {
    const user = userEvent.setup();
    renderRoute(`/chat/${THREAD_A}`);

    expect(await screen.findByText("Election answer")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: /Fed order book/i }));

    expect(await screen.findByText("Fed answer")).toBeInTheDocument();
    expect(screen.queryByText("Election answer")).not.toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(`/chat/${THREAD_B}`);
  });

  it("keeps a new chat unsaved until its first message and then replaces the route", async () => {
    const user = userEvent.setup();
    mocks.listAgentThreads.mockResolvedValueOnce([]).mockResolvedValue([summaryA]);
    renderRoute("/chat/new");

    expect(screen.getByTestId("location")).toHaveTextContent("/chat/new");
    await user.type(screen.getByRole("textbox", { name: "Ask PolyTrade" }), "Find election liquidity");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(`/chat/${THREAD_A}`));
    expect(mocks.createAgentThread).toHaveBeenCalledTimes(1);
    expect(mocks.runAgentTurn).toHaveBeenCalledWith(expect.objectContaining({ threadId: THREAD_A, text: "Find election liquidity" }));
    expect(await screen.findByText("Streamed answer")).toBeInTheDocument();
  });

  it("keeps an in-flight stream attached to its originating thread across navigation", async () => {
    const user = userEvent.setup();
    let streamOptions: Parameters<typeof import("./agent").runAgentTurn>[0] | undefined;
    let finishStream: (() => void) | undefined;
    mocks.runAgentTurn.mockImplementation((options: Parameters<typeof import("./agent").runAgentTurn>[0]) => {
      streamOptions = options;
      return new Promise<void>((resolve) => { finishStream = resolve; });
    });
    renderRoute(`/chat/${THREAD_A}`);
    expect(await screen.findByText("Election answer")).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: "Ask PolyTrade" }), "Keep this response on A");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(streamOptions).toBeDefined());
    await user.click(screen.getByRole("link", { name: /Fed order book/i }));
    expect(await screen.findByText("Fed answer")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Ask PolyTrade" })).toBeDisabled();

    await act(async () => {
      streamOptions!.handlers.onMessageStart("late-a");
      streamOptions!.handlers.onMessageText("late-a", "Late answer for A");
      finishStream!();
    });
    expect(screen.queryByText("Late answer for A")).not.toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: /Election liquidity/i }));
    expect(await screen.findByText("Late answer for A")).toBeInTheDocument();
  });

  it("shows unlabeled dots only until the first visible response text", async () => {
    const user = userEvent.setup();
    let streamOptions: Parameters<typeof import("./agent").runAgentTurn>[0] | undefined;
    let finishStream: (() => void) | undefined;
    mocks.runAgentTurn.mockImplementation((options: Parameters<typeof import("./agent").runAgentTurn>[0]) => {
      streamOptions = options;
      return new Promise<void>((resolve) => { finishStream = resolve; });
    });
    renderRoute(`/chat/${THREAD_A}`);
    expect(await screen.findByText("Election answer")).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: "Ask PolyTrade" }), "Trace the stream state");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(streamOptions).toBeDefined());
    expect(screen.getByText("Agent is working")).toBeInTheDocument();
    expect(screen.queryByText("Reading market data")).not.toBeInTheDocument();

    act(() => {
      streamOptions!.handlers.onMessageStart("visible-response");
      streamOptions!.handlers.onMessageText("visible-response", "The visible response has started.");
    });
    expect(screen.getByText("The visible response has started.")).toBeInTheDocument();
    expect(screen.queryByText("Agent is working")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Ask PolyTrade" })).toBeDisabled();

    act(() => finishStream!());
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Ask PolyTrade" })).toBeEnabled());
  });

  it("shows every strategy run and keeps the right-pane tape order", async () => {
    const proposal = {
      action: "create" as const,
      execution: "GTC" as const,
      tokenId: "123",
      marketId: "condition-1",
      marketQuestion: "Will the proposal stay first?",
      outcome: "Yes",
      side: "BUY" as const,
      rationale: "Priority test",
      observedAt: "2026-08-04T00:00:00.000Z",
      price: "0.4",
      size: "10",
      postOnly: true,
    };
    mocks.getAgentThreadItems.mockResolvedValue([
      { kind: "message", id: "message", role: "assistant", text: "Draft ready" },
      { kind: "proposal", id: "proposal", proposal, expiresAt: "2099-08-04T00:02:00.000Z" },
      { kind: "backtest", id: "momentum-backtest", backtest: { kind: "backtest_run", ...run, strategy: "momentum_v1" } },
      { kind: "backtest", id: "mean-reversion-backtest", backtest: { kind: "backtest_run", ...run, runId: MEAN_REVERSION_RUN_ID, strategy: "mean_reversion_v1" } },
      { kind: "backtest", id: "breakout-backtest", backtest: { kind: "backtest_run", ...run, runId: BREAKOUT_RUN_ID, strategy: "breakout_v1" } },
    ]);
    renderRoute(`/chat/${THREAD_A}`);

    const proposalHeading = await screen.findByRole("heading", { name: "Will the proposal stay first?" });
    const backtestHeadings = await screen.findAllByRole("heading", { name: "Will the activity order be clear?" });
    expect(screen.getByText(/Momentum ·/)).toBeInTheDocument();
    expect(screen.getByText(/Mean reversion ·/)).toBeInTheDocument();
    expect(screen.getByText(/Breakout ·/)).toBeInTheDocument();
    const accountHeading = screen.getByText("Account");
    const statusHeading = screen.getByText("Eligibility");
    expect(backtestHeadings).toHaveLength(3);
    expect(proposalHeading.compareDocumentPosition(backtestHeadings[0]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(backtestHeadings[2]!.compareDocumentPosition(accountHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(accountHeading.compareDocumentPosition(statusHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps typed order edits on screen instead of snapping back mid-decimal", async () => {
    const user = userEvent.setup();
    mocks.getAgentThreadItems.mockResolvedValueOnce([
      { kind: "message", id: "message", role: "assistant", text: "Draft ready" },
      {
        kind: "proposal",
        id: "proposal",
        proposal: {
          action: "create" as const,
          execution: "GTC" as const,
          tokenId: "123",
          marketId: "condition-1",
          marketQuestion: "Will the typed edit stay in place?",
          outcome: "Yes",
          side: "BUY" as const,
          rationale: "Editing regression test",
          observedAt: "2026-08-04T00:00:00.000Z",
          price: "0.4",
          size: "10",
          postOnly: false,
        },
        expiresAt: "2099-08-04T00:02:00.000Z",
      },
    ]);
    renderRoute(`/chat/${THREAD_A}`);

    const price = await screen.findByLabelText("Limit price");
    await user.clear(price);
    expect(price).toHaveValue("");
    await user.type(price, "0.55");
    expect(price).toHaveValue("0.55");

    const size = screen.getByLabelText("Size (shares)");
    await user.clear(size);
    await user.type(size, "2.5");
    expect(size).toHaveValue("2.5");
    expect(price).toHaveValue("0.55");

    await user.type(size, "x");
    expect(screen.getByText("Fix the highlighted fields — this exact text would be signed.")).toBeInTheDocument();
    await user.type(size, "{backspace}");
    expect(screen.queryByText("Fix the highlighted fields — this exact text would be signed.")).not.toBeInTheDocument();
  });

  it("routes missing wallet sessions to Settings and requires local reattachment after restore", async () => {
    const user = userEvent.setup();
    const first = renderRoute("/trades");
    const settingsLink = await screen.findByRole("link", { name: /Open Settings/i });
    expect(settingsLink).toHaveAttribute("href", "/settings");
    first.unmount();

    mocks.currentWalletSession.mockResolvedValue(session);
    renderRoute("/settings");
    expect(await screen.findByText("Not locally attached")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Attach matching wallet/i }));
    expect(await screen.findByText("Matching wallet attached")).toBeInTheDocument();
  });

  describe("wallet session lifecycle", () => {
    const liveSession = {
      ...session,
      idleExpiresAt: new Date(Date.now() + 4 * 3_600_000).toISOString(),
      expiresAt: new Date(Date.now() + 23 * 3_600_000).toISOString(),
    };

    afterEach(() => {
      window.localStorage.removeItem("polytrade.wallet-session-prefs");
    });

    it("reconnects an expired session from Trades with one signature", async () => {
      const user = userEvent.setup();
      mocks.attachWallet.mockResolvedValue({ address: WALLET, provider: {} });
      mocks.createChallenge.mockResolvedValue({
        challengeId: "challenge-1",
        typedData: { domain: { name: "PolyTrade" }, primaryType: "WalletAuth", types: {}, message: {} },
        expiresAt: new Date(Date.now() + 5 * 60_000).toISOString(),
      });
      mocks.signTypedPayload.mockResolvedValue(`0x${"ab".repeat(65)}`);
      mocks.createWalletSession.mockResolvedValue(liveSession);
      mocks.accountOverview.mockResolvedValue(overview);

      renderRoute("/trades");
      await user.click(await screen.findByRole("button", { name: /Reconnect wallet/i }));

      expect(await screen.findByText("Wallet verified and locally attached for signing.")).toBeInTheDocument();
      expect(mocks.createChallenge).toHaveBeenCalledWith(expect.objectContaining({ walletAddress: WALLET, signatureType: 0 }));
      expect(mocks.createWalletSession).toHaveBeenCalledWith("challenge-1", `0x${"ab".repeat(65)}`);
      // The trades page leaves the empty state and shows the live account strip.
      expect(screen.getAllByText("Fill history").length).toBeGreaterThan(0);
      expect(JSON.parse(window.localStorage.getItem("polytrade.wallet-session-prefs") ?? "{}")).toEqual({ signatureType: 0, funderAddress: "" });
    });

    it("prefills the reconnect form with the remembered wallet type and funder", async () => {
      window.localStorage.setItem("polytrade.wallet-session-prefs", JSON.stringify({ signatureType: 2, funderAddress: "0xfunder" }));

      renderRoute("/settings");

      expect(await screen.findByLabelText("Wallet type")).toHaveValue("2");
      expect(screen.getByLabelText("Funder / maker address")).toHaveValue("0xfunder");
    });

    it("announces an expired wallet session instead of dropping it silently", async () => {
      mocks.currentWalletSession.mockResolvedValue(liveSession);
      mocks.accountOverview.mockRejectedValue(new mocks.GatewayError("No active wallet session", "NOT_FOUND", 404));

      renderRoute("/trades");

      expect(await screen.findByText(/Wallet session expired or was revoked/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Reconnect wallet/i })).toBeEnabled();
    });

    it("renews the session while the tab is visible and announces expiry once", async () => {
      vi.useFakeTimers();
      try {
        mocks.currentWalletSession.mockResolvedValue(liveSession);
        mocks.accountOverview.mockResolvedValue(overview);
        renderRoute("/chat");
        // Flush the initial restore; avoid waitFor-based queries here because
        // fake timers would freeze its polling interval.
        await act(async () => {});
        expect(screen.getByText(/Session expires/i)).toBeInTheDocument();

        const before = mocks.currentWalletSession.mock.calls.length;
        await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
        expect(mocks.currentWalletSession.mock.calls.length).toBe(before + 1);

        const expired = new mocks.GatewayError("No active wallet session", "NOT_FOUND", 404);
        mocks.currentWalletSession.mockRejectedValue(expired);
        mocks.accountOverview.mockRejectedValue(expired);
        await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
        expect(screen.getByText(/Wallet session expired or was revoked/i)).toBeInTheDocument();
        expect(screen.getAllByText(/Wallet session expired or was revoked/i)).toHaveLength(1);
        await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
        expect(screen.getAllByText(/Wallet session expired or was revoked/i)).toHaveLength(1);
      } finally {
        vi.useRealTimers();
      }
    });
  });

  it("authorizes wallet verification from the browser IP check alone", async () => {
    mocks.browserEligibility.mockResolvedValue({
      blocked: false,
      verified: true,
      country: "NZ",
      region: "AUK",
      checkedAt: new Date().toISOString(),
    });

    renderRoute("/settings");

    const browserStatus = (await screen.findByText("Browser IP check")).parentElement;
    await waitFor(() => expect(browserStatus).toHaveTextContent("Trading eligible"));
    expect(screen.getByText("Country / region").parentElement).toHaveTextContent("NZ / AUK");
    expect(screen.queryByText("Gateway enforcement")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Connect and verify wallet/i })).toBeEnabled();
  });

  it("blocks wallet verification when the browser IP check is blocked", async () => {
    mocks.browserEligibility.mockResolvedValue({
      blocked: true,
      verified: true,
      country: "AU",
      region: "VIC",
      checkedAt: new Date().toISOString(),
    });

    renderRoute("/settings");

    await waitFor(() => expect(screen.getByText("Browser IP check").parentElement).toHaveTextContent("Research only"));
    expect(screen.getByRole("button", { name: /Connect and verify wallet/i })).toBeDisabled();
    expect(screen.getByText(/New orders are unavailable in AU/i)).toBeInTheDocument();
  });

  it("renders the strategy alerts card with channels and recent deliveries", async () => {
    mocks.listAlertChannels.mockResolvedValue({
      items: [{
        channelId: "77777777-7777-4777-8777-777777777777",
        kind: "discord",
        label: "Trading Discord",
        eventKinds: ["BUY", "SELL", "ERROR"],
        enabled: true,
        targetHint: "discord.com/api/webhooks/…ghij",
        createdAt: "2026-08-04T00:00:00.000Z",
        updatedAt: "2026-08-04T00:00:00.000Z",
      }],
    });
    mocks.listAlertDeliveries.mockResolvedValue({
      items: [{
        deliveryId: "88888888-8888-4888-8888-888888888888",
        channelId: "77777777-7777-4777-8777-777777777777",
        channelLabel: "Trading Discord",
        channelKind: "discord",
        action: "BUY",
        message: "Bought 10 shares",
        context: { marketQuestion: null, outcome: null, side: "BUY", price: "0.42" },
        status: "delivered",
        attempts: 1,
        lastError: null,
        createdAt: "2026-08-04T00:01:00.000Z",
        deliveredAt: "2026-08-04T00:01:01.000Z",
      }],
      limit: 20,
    });

    renderRoute("/settings");

    expect(await screen.findByText("Strategy alerts")).toBeInTheDocument();
    expect((await screen.findAllByText("Trading Discord")).length).toBeGreaterThan(0);
    expect(screen.getByText("discord.com/api/webhooks/…ghij")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Send test/i })).toBeEnabled();
  });

  it("opens the isolated paper ledger and completes a preview-confirm fill", async () => {
    const user = userEvent.setup();
    mocks.searchMarkets.mockResolvedValue({
      query: "paper",
      state: "active",
      observedAt: "2026-08-04T00:00:00.000Z",
      events: [{
        id: "paper-event",
        slug: "paper-event",
        title: "Paper event",
        description: "",
        endDate: null,
        liquidity: "1000",
        volume: "5000",
        markets: [paperMarket],
      }],
    });
    renderRoute("/paper");

    expect(await screen.findByRole("heading", { name: "Paper trading" })).toBeInTheDocument();
    expect(screen.getByText("No wallet, signature, real order, or withdrawable balance.")).toBeInTheDocument();
    expect(mocks.refreshPaperPortfolio).toHaveBeenCalled();

    await user.type(screen.getByRole("textbox", { name: "Search active Polymarket markets" }), "paper");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: /Will the paper workspace pass/i }));
    await user.type(screen.getByLabelText("Shares"), "10");
    await user.click(screen.getByRole("button", { name: /Preview paper trade/i }));

    await waitFor(() => expect(mocks.paperQuote).toHaveBeenCalledWith({
      conditionId: "paper-condition",
      tokenId: "123",
      side: "BUY",
      shares: "10",
    }));
    expect(await screen.findByRole("region", { name: "Paper trade preview" })).toHaveTextContent("VWAP");
    await user.click(screen.getByRole("button", { name: "Confirm paper buy" }));

    await waitFor(() => expect(mocks.paperOrder).toHaveBeenCalledWith({
      conditionId: "paper-condition",
      tokenId: "123",
      side: "BUY",
      shares: "10",
      limitPrice: "0.420000",
    }, expect.any(String)));
    expect(mocks.attachWallet).not.toHaveBeenCalled();
  });

  it("hands an active paper market into a prefilled research chat", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("polytrade.market-focus", JSON.stringify(paperMarket));
    renderRoute("/paper?focus=paper-condition");

    expect(await screen.findByLabelText("Market in focus")).toHaveTextContent("Will the paper workspace pass?");
    const handoff = screen.getByRole("link", { name: /Ask agent/i });
    expect(handoff).toHaveAttribute("href", "/chat/new?focus=paper-condition");

    await user.click(handoff);

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/chat/new"));
    expect((screen.getByRole("textbox", { name: "Ask PolyTrade" }) as HTMLTextAreaElement).value).toContain("Will the paper workspace pass?");
  });

  it("starts and stops a persistent background paper strategy", async () => {
    const user = userEvent.setup();
    mocks.searchMarkets.mockResolvedValue({
      query: "paper",
      state: "active",
      observedAt: "2026-08-04T00:00:00.000Z",
      events: [{
        id: "paper-event",
        slug: "paper-event",
        title: "Paper event",
        description: "",
        endDate: null,
        liquidity: "1000",
        volume: "5000",
        markets: [paperMarket],
      }],
    });
    renderRoute("/paper");

    await screen.findByRole("heading", { name: "Paper trading" });
    await user.type(screen.getByRole("textbox", { name: "Search active Polymarket markets" }), "paper");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: /Will the paper workspace pass/i }));

    expect(await screen.findByLabelText("Strategy buy price")).toHaveValue("0.38");
    await user.click(screen.getByRole("button", { name: /Start in background/i }));

    await waitFor(() => expect(mocks.startPaperStrategy).toHaveBeenCalledWith({
      conditionId: "paper-condition",
      tokenId: "123",
      entryPrice: "0.38",
      exitPrice: "0.45",
      sharesPerOrder: "10",
      maxPosition: "50",
      intervalSeconds: 15,
    }, expect.any(String)));
    expect(await screen.findByText("Background runner active")).toBeInTheDocument();
    expect(screen.getByText("Runs on the gateway after this page closes. Stop prevents future scans; a fill already being committed may still finish.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Stop strategy/i }));
    await waitFor(() => expect(mocks.stopPaperStrategy).toHaveBeenCalledOnce());
    expect(mocks.attachWallet).not.toHaveBeenCalled();
  });

  it("retains strategy drafts and launches the selected breakout configuration", async () => {
    const user = userEvent.setup();
    mocks.searchMarkets.mockResolvedValue({
      query: "election",
      state: "resolved",
      observedAt: "2026-08-04T00:00:00.000Z",
      events: [{
        id: "event",
        slug: "event",
        title: "Election",
        description: "",
        endDate: null,
        liquidity: "100",
        volume: "1000",
        markets: [{
          id: "market",
          conditionId: "condition-1",
          slug: "market",
          question: "Was the result yes?",
          description: "",
          outcomes: ["Yes", "No"],
          outcomePrices: ["1", "0"],
          clobTokenIds: ["yes", "no"],
          active: false,
          closed: true,
          acceptingOrders: false,
          enableOrderBook: true,
          archived: false,
          restricted: false,
          minimumOrderSize: "5",
          minimumTickSize: "0.01",
          endDate: null,
          startDate: "2026-05-01T00:00:00.000Z",
          createdAt: null,
          closedTime: "2026-08-01T00:00:00.000Z",
          liquidity: "100",
          volume: "1000",
        }, {
          id: "legacy-market",
          conditionId: "condition-legacy",
          slug: "legacy-market",
          question: "Was the 2024 result yes?",
          description: "",
          outcomes: ["Yes", "No"],
          outcomePrices: ["1", "0"],
          clobTokenIds: ["legacy-yes", "legacy-no"],
          active: false,
          closed: true,
          acceptingOrders: false,
          enableOrderBook: true,
          archived: false,
          restricted: false,
          minimumOrderSize: "5",
          minimumTickSize: "0.01",
          endDate: null,
          startDate: "2024-01-04T00:00:00.000Z",
          createdAt: null,
          closedTime: "2024-11-06T00:00:00.000Z",
          liquidity: "100",
          volume: "1000",
        }],
      }],
    });
    mocks.backtestCreate.mockResolvedValue({ run: { ...run, status: "queued", phase: "queued", progress: 0 }, result: null });
    renderRoute("/backtests/new");

    expect(screen.getAllByRole("radio")).toHaveLength(3);
    await user.click(screen.getByRole("radio", { name: /Breakout/i }));
    await user.clear(screen.getByRole("textbox", { name: "Prior-high window (min)" }));
    await user.type(screen.getByRole("textbox", { name: "Prior-high window (min)" }), "180");
    await user.clear(screen.getByRole("textbox", { name: "Breakout buffer" }));
    await user.type(screen.getByRole("textbox", { name: "Breakout buffer" }), "0.03");
    await user.clear(screen.getByRole("textbox", { name: "Starting capital" }));
    await user.type(screen.getByRole("textbox", { name: "Starting capital" }), "12000");
    await user.click(screen.getByRole("radio", { name: /Mean reversion/i }));
    expect(screen.getByRole("textbox", { name: "Starting capital" })).toHaveValue("12000");
    await user.click(screen.getByRole("radio", { name: /Breakout/i }));
    expect(screen.getByRole("textbox", { name: "Prior-high window (min)" })).toHaveValue("180");
    expect(screen.getByRole("textbox", { name: "Breakout buffer" })).toHaveValue("0.03");

    await user.type(screen.getByRole("textbox", { name: "Search resolved markets" }), "election");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: /Was the result yes/i }));
    expect(screen.queryByRole("button", { name: /Was the 2024 result yes/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Launch backtest/i }));

    await waitFor(() => expect(mocks.searchMarkets).toHaveBeenCalledWith("election", "resolved", 20));
    expect(mocks.backtestCreate).toHaveBeenCalledWith("condition-1", {
      strategy: "breakout_v1",
      initialCapital: "12000",
      positionSizePct: "0.10",
      breakoutWindowMinutes: 180,
      breakoutThreshold: "0.03",
      takeProfit: "0.10",
      stopLoss: "0.05",
      maxHoldMinutes: 1440,
      cooldownMinutes: 60,
      slippage: "0.01",
      maxFillDelayMinutes: 5,
    });
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(`/backtests/${RUN_ID}`));
  });

  it("pre-fills the backtest form from a ?template= link and strips the param", async () => {
    mocks.searchMarkets.mockResolvedValue({
      query: "crypto above",
      state: "resolved",
      observedAt: "2026-08-04T00:00:00.000Z",
      events: [],
    });
    renderRoute("/backtests/new?template=ev-sniping");

    expect(await screen.findByText("Pre-filled from template · EV sniping")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Mean reversion/i })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "Search resolved markets" })).toHaveValue("crypto above");
    await waitFor(() => expect(mocks.searchMarkets).toHaveBeenCalledWith("crypto above", "resolved", 20));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/backtests/new"));
    expect(screen.getByTestId("location")).not.toHaveTextContent("template=");
  });

  it("ignores an unknown ?template= id and strips it", () => {
    renderRoute("/backtests/new?template=does-not-exist");
    expect(screen.queryByText(/Pre-filled from template/)).not.toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/backtests/new");
    expect(screen.getByTestId("location")).not.toHaveTextContent("template=");
  });

  it("confirms deletion and removes a non-streaming thread", async () => {
    const user = userEvent.setup();
    renderRoute(`/chat/${THREAD_A}`);
    const history = await screen.findByRole("complementary", { name: "Chat history" });
    // The thread list arrives in a separate fetch from the pane itself, so the
    // delete button needs an async query or this flakes on slow runners.
    await user.click(await within(history).findByRole("button", { name: "Delete Election liquidity" }));
    await waitFor(() => expect(mocks.deleteAgentThread).toHaveBeenCalledWith("https://api.polytrade.test", expect.any(Function), THREAD_A));
    expect(window.confirm).toHaveBeenCalled();
  });

  it("restores composer text when the first message cannot be saved", async () => {
    const user = userEvent.setup();
    mocks.listAgentThreads.mockResolvedValue([]);
    mocks.createAgentThread.mockRejectedValueOnce(new mocks.GatewayError("Could not reach the agent service", "UPSTREAM", 503));
    renderRoute("/chat/new");

    await user.type(screen.getByRole("textbox", { name: "Ask PolyTrade" }), "Find election liquidity");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Could not reach the agent service")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Ask PolyTrade" })).toHaveValue("Find election liquidity");
    expect(screen.getByTestId("location")).toHaveTextContent("/chat/new");

    mocks.createAgentThread.mockResolvedValue(THREAD_A);
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(`/chat/${THREAD_A}`));
    expect(mocks.createAgentThread).toHaveBeenCalledTimes(2);
  });

  it("shows a retryable error panel when a conversation fails to load", async () => {
    const user = userEvent.setup();
    // Fail while the flag is up (not once): the mocked auth token is a new
    // function each render, which recreates the clients and can re-trigger loads.
    let failThreadLoad = true;
    mocks.getAgentThreadItems.mockImplementation(async (...args: Parameters<typeof mocks.getAgentThreadItems>) => {
      if (failThreadLoad) throw new mocks.GatewayError("Thread service unavailable", "UPSTREAM", 503);
      return [
        { kind: "message", id: `${args[2]}-message`, role: "assistant", text: "Election answer" },
      ];
    });
    renderRoute(`/chat/${THREAD_A}`);

    // The toast and the inline panel both echo the error message, so query
    // the panel by its heading and assert the message appears somewhere.
    expect(await screen.findByText("This conversation could not be loaded")).toBeInTheDocument();
    expect(screen.getAllByText("Thread service unavailable").length).toBeGreaterThan(0);
    expect(screen.queryByText("Election answer")).not.toBeInTheDocument();

    failThreadLoad = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Election answer")).toBeInTheDocument();
    expect(screen.queryByText("This conversation could not be loaded")).not.toBeInTheDocument();
  });

  it("shows a failure panel instead of an empty ledger when the paper dashboard cannot load", async () => {
    const user = userEvent.setup();
    let failLedger = true;
    const failOr = <T,>(fallback: () => Promise<T>) => async () => {
      if (failLedger) throw new mocks.GatewayError("Ledger unavailable", "UPSTREAM", 503);
      return fallback();
    };
    mocks.refreshPaperPortfolio.mockImplementation(failOr(async () => paperPortfolio));
    mocks.paperFills.mockImplementation(failOr(async () => ({ items: [], total: 0, limit: 20, offset: 0 })));
    mocks.paperStrategy.mockImplementation(failOr(async () => ({ strategy: null, events: [] })));
    mocks.paperPortfolio.mockImplementation(failOr(async () => paperPortfolio));
    renderRoute("/paper");

    expect(await screen.findByText("Paper ledger could not be loaded")).toBeInTheDocument();
    expect(screen.getByText("Ledger unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Practice against the live public order book")).not.toBeInTheDocument();
    expect(mocks.attachWallet).not.toHaveBeenCalled();

    failLedger = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No wallet, signature, real order, or withdrawable balance.")).toBeInTheDocument();
  });
});
