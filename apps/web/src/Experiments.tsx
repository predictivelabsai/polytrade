import { useCallback, useEffect, useRef, useState } from "react";
import type {
  BacktestConfig,
  BacktestMetrics,
  BacktestSeriesPoint,
  ExperimentEnvelope,
  ExperimentRun,
  MarketExperimentResult,
} from "@polytrade/contracts";
import { BacktestClient } from "./backtest";

const active = (status: string) => status === "queued" || status === "running";
const label = (mode: string) =>
  mode === "walk_forward" ? "Walk-forward" : "Grid comparison";
const percent = (value: string) => `${Number(value).toFixed(2)}%`;
const money = (value: string) =>
  Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
const date = (value: string) => new Date(value).toLocaleString();
const strategy = (value: string) =>
  value.replace(/_v1$/, "").replaceAll("_", " ");

export function ExperimentsWorkspace(props: {
  client: BacktestClient;
  focusedExperimentId?: string;
  onSelect: (id: string) => void;
  onShowRuns: () => void;
  onAskAgent: () => void;
  onError: (message: string) => void;
}) {
  const [runs, setRuns] = useState<ExperimentRun[]>([]);
  const [selected, setSelected] = useState(props.focusedExperimentId);
  const [envelope, setEnvelope] = useState<ExperimentEnvelope | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const settled = useRef<ExperimentEnvelope | null>(null);
  const requestKeys = useRef(new Map<string, string>());

  useEffect(() => {
    setSelected(props.focusedExperimentId);
  }, [props.focusedExperimentId]);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const list = await props.client.listExperiments();
        if (cancelled) return;
        setRuns(list);
        const id = selected ?? list[0]?.experimentId;
        if (id) {
          const saved = settled.current;
          const next =
            saved?.experiment.experimentId === id &&
            !active(saved.experiment.status)
              ? saved
              : await props.client.getExperiment(id);
          if (cancelled) return;
          setEnvelope(next);
          if (!active(next.experiment.status)) settled.current = next;
        } else setEnvelope(null);
        setFailure(null);
      } catch (error) {
        if (!cancelled)
          setFailure(
            error instanceof Error
              ? error.message
              : "Could not load experiments",
          );
      } finally {
        if (!cancelled) {
          setLoading(false);
          timer = window.setTimeout(() => void poll(), 3_000);
        }
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [props.client, selected, refresh]);

  const choose = useCallback(
    (id: string) => {
      setSelected(id);
      setEnvelope(null);
      setFailure(null);
      setLoading(true);
      props.onSelect(id);
    },
    [props.onSelect],
  );
  const current =
    envelope && (!selected || envelope.experiment.experimentId === selected)
      ? envelope
      : null;
  const act = async (kind: "cancel" | "walk-forward") => {
    if (!current || acting) return;
    setActing(true);
    const id = current.experiment.experimentId;
    try {
      if (kind === "cancel") {
        const next = await props.client.cancelExperiment(id);
        setEnvelope(next);
        settled.current = next;
      } else {
        // A retry after an uncertain network response reuses the same creation key.
        let key = requestKeys.current.get(id);
        if (!key) {
          key = crypto.randomUUID();
          requestKeys.current.set(id, key);
        }
        const next = await props.client.walkForwardExperiment(
          id,
          { folds: 5 },
          key,
        );
        choose(next.experiment.experimentId);
        setEnvelope(next);
      }
      setRefresh((value) => value + 1);
    } catch (error) {
      props.onError(
        error instanceof Error ? error.message : "Experiment action failed",
      );
    } finally {
      setActing(false);
    }
  };

  return (
    <main className="backtest-workspace experiment-workspace">
      <header className="backtest-hero">
        <div>
          <span className="eyebrow">Historical market analysis</span>
          <h1>Experiments</h1>
          <p>
            Compare settings, then evaluate the process on unseen historical
            windows. Each market has its own capital.
          </p>
        </div>
        <div className="backtest-hero-actions">
          <button className="button button-quiet" onClick={props.onShowRuns}>
            Single runs
          </button>
          <button
            className="button button-quiet"
            onClick={() => setRefresh((value) => value + 1)}
          >
            Refresh experiments
          </button>
          <button className="button button-primary" onClick={props.onAskAgent}>
            Describe an experiment
          </button>
        </div>
      </header>
      {failure && (
        <div className="terminal-note terminal-note-failed" role="alert">
          {failure}
        </div>
      )}
      <div className="backtest-layout">
        <aside className="run-library" aria-label="Experiments">
          <div className="pane-heading">
            <h2>Experiment library</h2>
            <span>{runs.length}</span>
          </div>
          {loading && !runs.length ? (
            <p role="status">Loading experiments…</p>
          ) : !runs.length && !failure ? (
            <div className="run-empty">
              <strong>No experiments yet</strong>
              <p>
                Ask the agent to compare strategy settings on selected resolved
                markets.
              </p>
            </div>
          ) : null}
          <div className="run-list">
            {runs.map((run) => (
              <div
                className={`run-row ${current?.experiment.experimentId === run.experimentId ? "run-row-selected" : ""}`}
                key={run.experimentId}
              >
                <button onClick={() => choose(run.experimentId)}>
                  <span className={`run-state run-state-${run.status}`} />
                  <span>
                    <strong>{label(run.request.mode)}</strong>
                    <small>
                      {run.request.marketIds.length} markets ·{" "}
                      {run.completedSimulations}/{run.totalSimulations}{" "}
                      simulations · {run.status}
                    </small>
                    <small>{date(run.createdAt)}</small>
                  </span>
                </button>
              </div>
            ))}
          </div>
        </aside>
        <section className="backtest-stage" aria-live="polite">
          {current ? (
            <>
              <div className="experiment-heading">
                <div>
                  <span className="eyebrow">{current.experiment.status}</span>
                  <h2>{label(current.experiment.request.mode)}</h2>
                  <small>{current.experiment.experimentId}</small>
                </div>
                {active(current.experiment.status) ? (
                  <button
                    className="button button-quiet"
                    disabled={acting}
                    onClick={() => void act("cancel")}
                  >
                    Cancel experiment
                  </button>
                ) : current.experiment.status === "completed" &&
                  current.experiment.request.mode === "grid" ? (
                  <button
                    className="button button-primary"
                    disabled={acting}
                    onClick={() => void act("walk-forward")}
                  >
                    Run walk-forward validation
                  </button>
                ) : null}
              </div>
              <p>
                {current.experiment.message} ·{" "}
                {current.experiment.completedSimulations} /{" "}
                {current.experiment.totalSimulations} simulations
              </p>
              <progress
                aria-label="Experiment progress"
                value={current.experiment.completedSimulations}
                max={current.experiment.totalSimulations}
              />
              {current.experiment.failure && (
                <div
                  className="terminal-note terminal-note-failed"
                  role="alert"
                >
                  {current.experiment.failure.message}
                </div>
              )}
              {current.experiment.status === "cancelled" && (
                <p>Experiment cancelled. Completed simulations remain saved.</p>
              )}
              {current.result?.markets.map((market) => (
                <MarketResults
                  key={`${market.marketId}:${market.period}`}
                  market={market}
                />
              ))}
              {current.result && (
                <aside className="benchmark-card">
                  <h3>Assumptions and limitations</h3>
                  <ul>
                    {current.result.assumptions.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </aside>
              )}
              <details className="experiment-definition">
                <summary>Exact experiment definition</summary>
                <pre>{JSON.stringify(current.experiment.request, null, 2)}</pre>
              </details>
            </>
          ) : (
            <div className="stage-empty">
              <h2>
                {loading ? "Loading experiment…" : "Select an experiment"}
              </h2>
              <p>Rankings, window dates, and saved results appear here.</p>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function MarketResults({ market }: { market: MarketExperimentResult }) {
  return (
    <section className="experiment-result">
      <header>
        <span className="eyebrow">{market.period}</span>
        <h3>{market.marketQuestion}</h3>
        <p>
          {date(market.startAt)} — {date(market.endAt)}
        </p>
      </header>
      {market.outOfSample ? (
        <>
          <h4>Out-of-sample performance</h4>
          <p>
            Only the unseen test windows contribute to this result. Settings
            were selected using earlier training windows.
          </p>
          <Metrics metrics={market.outOfSample} />
          <EquityCurve points={market.series} />
          <div className="experiment-table-scroll">
            <table className="experiment-table">
              <caption>Walk-forward folds</caption>
              <thead>
                <tr>
                  <th>Fold</th>
                  <th>Training window</th>
                  <th>Unseen test window</th>
                  <th>Selected settings</th>
                  <th>Training return</th>
                  <th>Test return</th>
                  <th>Test drawdown</th>
                  <th>Trades</th>
                </tr>
              </thead>
              <tbody>
                {market.folds.map((fold) => (
                  <tr key={fold.fold}>
                    <td>{fold.fold}</td>
                    <td>
                      {date(fold.trainStartAt)}
                      <br />
                      {date(fold.trainEndAt)}
                    </td>
                    <td>
                      {date(fold.testStartAt)}
                      <br />
                      {date(fold.testEndAt)}
                    </td>
                    <td>
                      <Settings config={fold.selected.config} />
                    </td>
                    <td>{percent(fold.selected.metrics.returnPct)}</td>
                    <td>{percent(fold.testMetrics.returnPct)}</td>
                    <td>{percent(fold.testMetrics.maxDrawdownPct)}</td>
                    <td>{fold.testMetrics.tradeCount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
          <h4>In-sample rankings</h4>
          <p>
            Ranked by net return, then lower drawdown. These settings were
            compared on the same history used to report their results.
          </p>
          <div className="experiment-table-scroll">
            <table className="experiment-table">
              <caption>Strategy and parameter comparison</caption>
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Strategy and settings</th>
                  <th>Net return</th>
                  <th>PnL</th>
                  <th>Drawdown</th>
                  <th>Win rate</th>
                  <th>Trades</th>
                  <th>Fees</th>
                  <th>YES benchmark</th>
                  <th>NO benchmark</th>
                </tr>
              </thead>
              <tbody>
                {market.ranking.map((item, index) => (
                  <tr key={item.candidateId}>
                    <td>{index + 1}</td>
                    <td>
                      <Settings config={item.config} />
                    </td>
                    <td>{percent(item.metrics.returnPct)}</td>
                    <td>{money(item.metrics.pnl)}</td>
                    <td>{percent(item.metrics.maxDrawdownPct)}</td>
                    <td>{percent(item.metrics.winRatePct)}</td>
                    <td>{item.metrics.tradeCount}</td>
                    <td>{money(item.metrics.fees)}</td>
                    <td>{percent(item.metrics.yesBuyHoldReturnPct)}</td>
                    <td>{percent(item.metrics.noBuyHoldReturnPct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <details>
        <summary>Reproducibility</summary>
        <p>Market: {market.marketId}</p>
        <p className="experiment-hash">Dataset: {market.datasetHash}</p>
      </details>
    </section>
  );
}

function Settings({ config }: { config: BacktestConfig }) {
  return (
    <details>
      <summary>{strategy(config.strategy)}</summary>
      <dl className="experiment-settings">
        {Object.entries(config)
          .filter(([key, value]) => key !== "strategy" && value != null)
          .map(([key, value]) => (
            <div key={key}>
              <dt>{key.replace(/([A-Z])/g, " $1")}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
      </dl>
    </details>
  );
}

function Metrics({ metrics }: { metrics: BacktestMetrics }) {
  const values = [
    ["Net return", percent(metrics.returnPct)],
    ["PnL (USDC)", money(metrics.pnl)],
    ["Max drawdown", percent(metrics.maxDrawdownPct)],
    ["Win rate", percent(metrics.winRatePct)],
    ["Trades", String(metrics.tradeCount)],
    ["Fees (USDC)", money(metrics.fees)],
    ["YES benchmark", percent(metrics.yesBuyHoldReturnPct)],
    ["NO benchmark", percent(metrics.noBuyHoldReturnPct)],
  ];
  return (
    <dl className="experiment-metrics">
      {values.map(([name, value]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function EquityCurve({ points }: { points: BacktestSeriesPoint[] }) {
  if (points.length < 2) return null;
  const values = points.map((point) => Number(point.equity));
  const min = Math.min(...values),
    max = Math.max(...values);
  const start = Date.parse(points[0]!.timestamp),
    end = Date.parse(points[points.length - 1]!.timestamp);
  const line = points
    .map(
      (point, i) =>
        `${20 + ((Date.parse(point.timestamp) - start) / Math.max(1, end - start)) * 680},${160 - ((values[i]! - min) / Math.max(1, max - min)) * 130}`,
    )
    .join(" ");
  return (
    <figure className="experiment-curve">
      <figcaption>Combined out-of-sample equity · USDC</figcaption>
      <svg
        viewBox="0 0 720 185"
        role="img"
        aria-label={`Out-of-sample equity from ${money(points[0]!.equity)} to ${money(points[points.length - 1]!.equity)} USDC`}
      >
        <line
          x1="20"
          y1="160"
          x2="700"
          y2="160"
          stroke="currentColor"
          opacity="0.2"
        />
        <polyline
          points={line}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        />
        <text x="20" y="18">
          {money(String(max))}
        </text>
        <text x="20" y="180">
          {money(String(min))}
        </text>
      </svg>
      <div>
        <small>{date(points[0]!.timestamp)}</small>
        <small>{date(points[points.length - 1]!.timestamp)}</small>
      </div>
    </figure>
  );
}
