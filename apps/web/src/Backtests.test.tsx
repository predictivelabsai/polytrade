/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import {
  defaultMeanReversionBacktestConfig,
  defaultMomentumBacktestConfig,
  type BacktestConfig,
  type BacktestRun,
  type BacktestRunEnvelope,
} from "@polytrade/contracts";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BacktestsWorkspace } from "./Backtests";
import type { BacktestClient } from "./backtest";

const RUN_A = "00000000-0000-4000-8000-00000000000a";
const RUN_B = "00000000-0000-4000-8000-00000000000b";

function run(
  runId: string,
  question: string,
  config: BacktestConfig = defaultMomentumBacktestConfig,
): BacktestRun {
  return {
    runId,
    marketId: `condition-${question}`,
    marketQuestion: question,
    status: "completed",
    phase: "completed",
    progress: 100,
    config,
    cancelRequested: false,
    warnings: [],
    createdAt: "2026-08-04T00:00:00.000Z",
  };
}

function envelope(
  runId: string,
  question: string,
  finalEquity: string,
  config: BacktestConfig = defaultMomentumBacktestConfig,
): BacktestRunEnvelope {
  return {
    run: run(runId, question, config),
    result: {
      metrics: {
        initialCapital: "10000.000000",
        finalEquity,
        pnl: "0.000000",
        returnPct: "0.00",
        maxDrawdownPct: "0.00",
        tradeCount: 0,
        winRatePct: "0.00",
        profitFactor: null,
        averageHoldingSeconds: "0.000000",
        exposurePct: "0.00",
        fees: "0.000000",
        yesBuyHoldReturnPct: "0.00",
        noBuyHoldReturnPct: "0.00",
        skippedSignals: 0,
      },
      assumptions: [],
    },
  };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("BacktestsWorkspace run selection", () => {
  it("ignores a superseded run's charts when they resolve after a newer selection", async () => {
    vi.useFakeTimers();
    let releaseRunA = () => {};
    const runAPending = new Promise<void>((resolve) => {
      releaseRunA = resolve;
    });

    const client = {
      list: vi.fn(async () => [run(RUN_A, "Run A"), run(RUN_B, "Run B")]),
      get: vi.fn(async (runId: string) => (runId === RUN_A
        ? envelope(RUN_A, "Run A", "11000.000000")
        : envelope(RUN_B, "Run B", "12000.000000"))),
      series: vi.fn(async (runId: string) => {
        if (runId === RUN_A) await runAPending;
        return { runId, points: [] };
      }),
      trades: vi.fn(async (runId: string) => {
        if (runId === RUN_A) await runAPending;
        return { runId, items: [], total: runId === RUN_A ? 7 : 2, offset: 0, limit: 50 };
      }),
    } as unknown as BacktestClient;

    render(
      <BacktestsWorkspace
        client={client}
        focusedRunId={RUN_A}
        onAskAgent={vi.fn()}
        onError={vi.fn()}
        onNotice={vi.fn()}
      />,
    );

    // Run A's series and trades are still in flight when the user moves on.
    await act(async () => flushPromises());
    fireEvent.click(screen.getByRole("button", { name: /Run B/ }));
    await act(async () => flushPromises());

    expect(screen.getByRole("heading", { name: "Run B" })).toBeInTheDocument();
    expect(screen.getByText("2 trades")).toBeInTheDocument();

    await act(async () => {
      releaseRunA();
      await flushPromises();
    });

    expect(screen.getByRole("heading", { name: "Run B" })).toBeInTheDocument();
    expect(screen.getByText("2 trades")).toBeInTheDocument();
    expect(screen.queryByText("7 trades")).not.toBeInTheDocument();
  });

  it("shows the title first, then the summary, then the chart and ledger", async () => {
    vi.useFakeTimers();
    let releaseSummary = () => {};
    let releaseDetails = () => {};
    const summaryPending = new Promise<void>((resolve) => { releaseSummary = resolve; });
    const detailsPending = new Promise<void>((resolve) => { releaseDetails = resolve; });

    const client = {
      list: vi.fn(async () => [run(RUN_A, "Will Perez win the 2026 election?")]),
      get: vi.fn(async (runId: string) => {
        await summaryPending;
        return envelope(runId, "Will Perez win the 2026 election?", "11000.000000");
      }),
      series: vi.fn(async (runId: string) => {
        await detailsPending;
        return {
          runId,
          points: [
            { timestamp: "2026-08-04T00:00:00.000Z", yesPrice: "0.40", noPrice: "0.60", equity: "10000.000000" },
            { timestamp: "2026-08-04T01:00:00.000Z", yesPrice: "0.55", noPrice: "0.45", equity: "11000.000000" },
          ],
        };
      }),
      trades: vi.fn(async (runId: string) => {
        await detailsPending;
        return { runId, items: [], total: 2, offset: 0, limit: 50 };
      }),
    } as unknown as BacktestClient;

    render(
      <BacktestsWorkspace
        client={client}
        focusedRunId={RUN_A}
        onAskAgent={vi.fn()}
        onError={vi.fn()}
        onNotice={vi.fn()}
      />,
    );

    // The library row carries the title, so it lands before the run's requests do.
    await act(async () => flushPromises());
    expect(screen.getByRole("heading", { name: "Will Perez win the 2026 election?" })).toBeInTheDocument();
    expect(screen.getByLabelText("Loading backtest summary")).toBeInTheDocument();
    expect(screen.getByText("Loading the replay chart")).toBeInTheDocument();
    // The chart request does not queue behind the summary request.
    expect(client.series).toHaveBeenCalledWith(RUN_A);

    await act(async () => {
      releaseSummary();
      await flushPromises();
    });

    expect(screen.getByLabelText("Backtest summary")).toBeInTheDocument();
    expect(screen.getByText("Buy-and-hold benchmarks")).toBeInTheDocument();
    expect(screen.getByText("Loading the replay chart")).toBeInTheDocument();

    await act(async () => {
      releaseDetails();
      await flushPromises();
    });

    expect(screen.queryByText("Loading the replay chart")).not.toBeInTheDocument();
    expect(screen.getByText("2 trades")).toBeInTheDocument();
  });

  it("renders and reruns the exact strategy-specific configuration", async () => {
    vi.useFakeTimers();
    const meanRun = run(
      RUN_A,
      "Mean reversion run",
      defaultMeanReversionBacktestConfig,
    );
    const client = {
      list: vi.fn(async () => [meanRun]),
      get: vi.fn(async () => envelope(
        RUN_A,
        "Mean reversion run",
        "10100.000000",
        defaultMeanReversionBacktestConfig,
      )),
      series: vi.fn(async () => ({ runId: RUN_A, points: [] })),
      trades: vi.fn(async () => ({ runId: RUN_A, items: [], total: 0, offset: 0, limit: 50 })),
      create: vi.fn(async () => ({ run: meanRun, result: null })),
    } as unknown as BacktestClient;

    render(
      <BacktestsWorkspace
        client={client}
        focusedRunId={RUN_A}
        onAskAgent={vi.fn()}
        onError={vi.fn()}
        onNotice={vi.fn()}
      />,
    );
    await act(async () => flushPromises());

    expect(screen.getAllByText("Mean reversion").length).toBeGreaterThan(0);
    expect(screen.getByText("0.05 / 60m")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Rerun" }));
    await act(async () => flushPromises());
    expect(client.create).toHaveBeenCalledWith(
      meanRun.marketId,
      defaultMeanReversionBacktestConfig,
    );
  });
});

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}
