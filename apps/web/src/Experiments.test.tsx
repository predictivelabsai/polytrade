/** @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createExperimentRequestSchema,
  type ExperimentEnvelope,
} from "@polytrade/contracts";
import { ExperimentsWorkspace } from "./Experiments";
import type { BacktestClient } from "./backtest";

const ID = "11111111-1111-4111-8111-111111111111";
const NEXT = "22222222-2222-4222-8222-222222222222";
const request = createExperimentRequestSchema.parse({
  marketIds: ["market"],
  strategies: [{ baseConfig: { strategy: "momentum_v1" } }],
});
const metrics = {
  initialCapital: "10000",
  finalEquity: "10100",
  pnl: "100",
  returnPct: "1",
  maxDrawdownPct: "0.2",
  tradeCount: 4,
  winRatePct: "50",
  profitFactor: "2",
  averageHoldingSeconds: "60",
  exposurePct: "10",
  fees: "2",
  skippedSignals: 0,
  yesBuyHoldReturnPct: "3",
  noBuyHoldReturnPct: "-3",
};
function envelope(id = ID): ExperimentEnvelope {
  return {
    experiment: {
      experimentId: id,
      request,
      status: "completed",
      totalSimulations: 1,
      completedSimulations: 1,
      message: "Completed",
      createdAt: "2026-05-01T00:00:00Z",
    },
    result: {
      markets: [
        {
          marketId: "market",
          marketQuestion: "Election outcome?",
          datasetHash: "a".repeat(64),
          period: "Available history",
          startAt: "2026-05-01T00:00:00Z",
          endAt: "2026-05-02T00:00:00Z",
          ranking: [
            {
              candidateId: "000-000000",
              config: request.strategies[0]!.baseConfig,
              metrics,
            },
          ],
          folds: [],
          series: [],
        },
      ],
      assumptions: ["Historical simulations only."],
    },
  };
}
const props = () => ({
  onSelect: vi.fn(),
  onShowRuns: vi.fn(),
  onAskAgent: vi.fn(),
  onError: vi.fn(),
});
afterEach(cleanup);

describe("managed experiment workspace", () => {
  it("labels in-sample results and opens a walk-forward follow-up", async () => {
    const source = envelope();
    const next = envelope(NEXT);
    next.experiment.request = { ...request, mode: "walk_forward" };
    next.experiment.status = "queued";
    next.result = null;
    const client = {
      listExperiments: vi.fn(async () => [source.experiment]),
      getExperiment: vi.fn(async (id) => (id === ID ? source : next)),
      walkForwardExperiment: vi.fn(async () => next),
    } as unknown as BacktestClient;
    const callbacks = props();
    render(<ExperimentsWorkspace client={client} {...callbacks} />);
    expect(
      await screen.findByRole("heading", { name: "In-sample rankings" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Election outcome?")).toBeInTheDocument();
    expect(screen.getByText("1.00%")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Run walk-forward validation" }),
    );
    await waitFor(() => expect(callbacks.onSelect).toHaveBeenCalledWith(NEXT));
    expect(client.walkForwardExperiment).toHaveBeenCalledWith(
      ID,
      { folds: 5 },
      expect.any(String),
    );
    expect(callbacks.onError).not.toHaveBeenCalled();
  });

  it("shows only test performance in the out-of-sample curve and summary", async () => {
    const value = envelope();
    value.experiment.request = { ...request, mode: "walk_forward" };
    value.result!.markets[0]!.outOfSample = metrics;
    value.result!.markets[0]!.ranking = [];
    value.result!.markets[0]!.series = [
      { timestamp: "2026-05-01T12:00:00Z", equity: "10000" },
      { timestamp: "2026-05-02T00:00:00Z", equity: "10100" },
    ];
    const client = {
      listExperiments: vi.fn(async () => [value.experiment]),
      getExperiment: vi.fn(async () => value),
    } as unknown as BacktestClient;
    render(<ExperimentsWorkspace client={client} {...props()} />);
    expect(
      await screen.findByRole("heading", { name: "Out-of-sample performance" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", {
        name: /Out-of-sample equity from 10,000 to 10,100/,
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText("In-sample rankings")).not.toBeInTheDocument();
  });

  it("does not display a delayed result for a different selected experiment", async () => {
    let resolve: (value: ExperimentEnvelope) => void = () => {};
    const pending = new Promise<ExperimentEnvelope>((done) => {
      resolve = done;
    });
    const source = envelope(),
      next = envelope(NEXT);
    next.result!.markets[0]!.marketQuestion = "New market?";
    const client = {
      listExperiments: vi.fn(async () => [source.experiment, next.experiment]),
      getExperiment: vi.fn((id) =>
        id === ID ? pending : Promise.resolve(next),
      ),
    } as unknown as BacktestClient;
    const callbacks = props();
    const rendered = render(
      <ExperimentsWorkspace
        client={client}
        focusedExperimentId={ID}
        {...callbacks}
      />,
    );
    await waitFor(() => expect(client.getExperiment).toHaveBeenCalledWith(ID));
    rendered.rerender(
      <ExperimentsWorkspace
        client={client}
        focusedExperimentId={NEXT}
        {...callbacks}
      />,
    );
    expect(await screen.findByText("New market?")).toBeInTheDocument();
    resolve(source);
    await waitFor(() =>
      expect(screen.queryByText("Election outcome?")).not.toBeInTheDocument(),
    );
  });

  it("shows a data failure and cancels active work", async () => {
    const value = envelope();
    value.experiment.status = "running";
    value.result = null;
    const cancelled = {
      ...value,
      experiment: {
        ...value.experiment,
        status: "cancelled" as const,
        message: "Cancelled",
      },
    };
    const client = {
      listExperiments: vi.fn(async () => [value.experiment]),
      getExperiment: vi.fn(async () => value),
      cancelExperiment: vi.fn(async () => cancelled),
    } as unknown as BacktestClient;
    render(<ExperimentsWorkspace client={client} {...props()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Cancel experiment" }),
    );
    expect(
      await screen.findByText(
        "Experiment cancelled. Completed simulations remain saved.",
      ),
    ).toBeInTheDocument();
    expect(client.cancelExperiment).toHaveBeenCalledWith(ID);
  });
});
