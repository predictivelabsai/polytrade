/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { PublicTrackRecord } from "@polytrade/contracts";

import { GatewayError } from "./api";
import TrackRecordPage from "./TrackRecord";
import { fetchPublicTrackRecord } from "./public-api";

vi.mock("./public-api", () => ({ fetchPublicTrackRecord: vi.fn() }));

// CI has no .env.local; TrackRecord.tsx reads env at module scope.
vi.mock("./env", () => ({
  env: {
    VITE_API_URL: "https://api.polytrade.test",
    VITE_CLERK_PUBLISHABLE_KEY: "pk_test",
    VITE_CLERK_JWT_TEMPLATE: "polytrade",
  },
}));

const record: PublicTrackRecord = {
  profile: { displayName: "Paper account", startedAt: "2026-08-01T00:00:00.000Z" },
  stats: {
    initialCash: "10000.000000",
    cash: "9500.000000",
    equity: "9505.200000",
    totalPnl: "-494.800000",
    realizedPnl: "10.000000",
    unrealizedPnl: "0.200000",
    totalFees: "1.000000",
    tradeCount: 2,
    winRate: "100.00",
  },
  equityCurve: [
    { t: "2026-09-01T00:00:00.000Z", equity: "9497.000000" },
    { t: "2026-09-01T12:00:00.000Z", equity: "9500.000000" },
    { t: "2026-09-02T00:00:00.000Z", equity: "9505.200000" },
  ],
  positions: [{
    marketQuestion: "Will the Fed hold rates in September?",
    outcome: "Yes",
    shares: "10.000000",
    averageCost: "0.500000",
    liquidationValue: "5.200000",
    unrealizedPnl: "0.200000",
    markStatus: "current",
  }],
  fills: [{
    fillId: "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f",
    kind: "BUY",
    marketQuestion: "Will the Fed hold rates in September?",
    outcome: "Yes",
    shares: "10.000000",
    averagePrice: "0.500000",
    fee: "0.000000",
    cashEffect: "-5.000000",
    realizedPnl: "0.000000",
    createdAt: "2026-09-01T00:00:00.000Z",
  }],
  observedAt: "2026-09-02T00:00:00.000Z",
};

const TOKEN = "a".repeat(32);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/u/${TOKEN}`]}>
      <Routes>
        <Route path="/u/:token" element={<TrackRecordPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(fetchPublicTrackRecord).mockReset();
});

afterEach(() => {
  cleanup();
});

describe("TrackRecordPage", () => {
  it("renders the shared stats, curve, positions, and fills", async () => {
    vi.mocked(fetchPublicTrackRecord).mockResolvedValue(record);
    renderPage();

    expect(await screen.findByRole("heading", { name: "Paper account" })).toBeInTheDocument();
    expect(screen.getByText("Equity")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Paper equity from/ })).toBeInTheDocument();
    // The same market appears as an open position and as a recent fill.
    expect(screen.getAllByText("Will the Fed hold rates in September?")).toHaveLength(2);
    expect(screen.getByText("Recent fills")).toBeInTheDocument();
    expect(screen.getByText(/Curve tracks settled cash/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "How to read this record" })).toBeInTheDocument();
    expect(screen.getByText("2 fills · 1 open positions")).toBeInTheDocument();
    expect(screen.getByText(/not a broker statement or a forecast/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy record link" })).toBeInTheDocument();
    // The public fetch never carries an identity: the page only receives the token.
    expect(fetchPublicTrackRecord).toHaveBeenCalledWith(expect.any(String), TOKEN);
  });

  it("marks the page noindex while it is mounted", async () => {
    vi.mocked(fetchPublicTrackRecord).mockResolvedValue(record);
    renderPage();
    await screen.findByRole("heading", { name: "Paper account" });
    expect(document.querySelector('meta[name="robots"]')?.getAttribute("content")).toBe("noindex");

    cleanup();
    expect(document.querySelector('meta[name="robots"]')).toBeNull();
  });

  it("renders the unavailable card for a rotated or disabled link", async () => {
    vi.mocked(fetchPublicTrackRecord).mockRejectedValue(new GatewayError("Track record not found", "NOT_FOUND", 404));
    renderPage();

    expect(await screen.findByText("This track record is not available")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Try again/ })).toBeNull();
  });

  it("offers a retry after a transient gateway failure", async () => {
    vi.mocked(fetchPublicTrackRecord)
      .mockRejectedValueOnce(new GatewayError("upstream unavailable", "UPSTREAM", 503))
      .mockResolvedValueOnce(record);
    renderPage();

    expect(await screen.findByText("Track record could not be loaded")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Try again/ }));
    expect(await screen.findByRole("heading", { name: "Paper account" })).toBeInTheDocument();
    expect(fetchPublicTrackRecord).toHaveBeenCalledTimes(2);
  });
});
