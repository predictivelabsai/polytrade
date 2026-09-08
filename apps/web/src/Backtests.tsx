import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Clock3,
  Copy,
  GitCompare,
  LineChart,
  RefreshCw,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  BacktestResult,
  BacktestRun,
  BacktestRunEnvelope,
  BacktestSeriesPoint,
  BacktestTrade,
} from "@polytrade/contracts";

import { BacktestApiError, BacktestClient } from "./backtest";

const ACTIVE_STATUSES = new Set<BacktestRun["status"]>(["queued", "running"]);
const TRADE_PAGE_SIZE = 50;

/** The chart and ledger of one run, tagged so they can never paint onto another. */
interface RunDetails {
  runId: string;
  series: BacktestSeriesPoint[];
  trades: BacktestTrade[];
  chartTrades: BacktestTrade[];
  tradeTotal: number;
}

export function BacktestsWorkspace(props: {
  client: BacktestClient;
  focusedRunId?: string;
  onAskAgent: () => void;
  onError: (message: string) => void;
  onNewBacktest?: () => void;
  onNotice: (message: string) => void;
  onSelectRun?: (runId: string) => void;
}) {
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selectedId, setSelectedId] = useState<string | undefined>(props.focusedRunId);
  const [envelope, setEnvelope] = useState<BacktestRunEnvelope | null>(null);
  const [details, setDetails] = useState<RunDetails | null>(null);
  const [tradePage, setTradePage] = useState(0);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [comparisons, setComparisons] = useState<BacktestRunEnvelope[]>([]);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [listLoadFailed, setListLoadFailed] = useState(false);
  const [selectedMissing, setSelectedMissing] = useState(false);
  // One toast per distinct failure episode, instead of re-arming every 3s tick.
  const pollErrorRef = useRef<string | null>(null);
  // Remembers which terminal run/page has already been fully fetched so the
  // poll stops re-downloading a completed run's series and ledger forever.
  const settledRef = useRef<{ runId: string; phase: string; tradePage: number } | null>(null);
  // and stops re-requesting a run that answered 404 until the selection moves.
  const missingRunRef = useRef<string | null>(null);

  useEffect(() => {
    if (props.focusedRunId) setSelectedId(props.focusedRunId);
    setSelectedMissing(false);
    missingRunRef.current = null;
  }, [props.focusedRunId]);

  // The library row already carries the title, status, and configuration, so the
  // header renders on click while the run's own responses are still in flight.
  // Each slower piece is matched to its run id before it is shown.
  const activeEnvelope = envelope && envelope.run.runId === selectedId ? envelope : null;
  const activeRun = activeEnvelope?.run ?? runs.find((run) => run.runId === selectedId);
  const activeResult = activeEnvelope?.result ?? null;
  const activeDetails = details && details.runId === selectedId ? details : null;

  const refresh = useCallback(async (isActive: () => boolean = () => true) => {
    const nextRuns = await props.client.list();
    if (!isActive()) return nextRuns;
    setRuns(nextRuns);
    setSelectedId((current) => current ?? nextRuns[0]?.runId);
    return nextRuns;
  }, [props.client]);

  const loadDetails = useCallback(async (runId: string, page: number): Promise<RunDetails> => {
    const [nextSeries, nextTrades, nextChartTrades] = await Promise.all([
      props.client.series(runId),
      props.client.trades(runId, page * TRADE_PAGE_SIZE, TRADE_PAGE_SIZE),
      props.client.trades(runId, 0, 200),
    ]);
    return {
      runId,
      series: nextSeries.points,
      trades: nextTrades.items,
      chartTrades: nextChartTrades.items,
      tradeTotal: nextTrades.total,
    };
  }, [props.client]);

  const refreshSelected = useCallback(async (
    runId: string,
    isActive: () => boolean = () => true,
    listedAsCompleted = false,
    page = tradePage,
  ) => {
    // A run the library already shows as completed can load its chart and trades
    // beside the summary rather than a round trip behind it.
    const eager = listedAsCompleted ? loadDetails(runId, page).catch(() => null) : null;
    const nextEnvelope = await props.client.get(runId);
    if (!isActive()) return nextEnvelope;
    setEnvelope(nextEnvelope);
    if (nextEnvelope.run.status !== "completed") {
      setDetails(null);
      return nextEnvelope;
    }
    const nextDetails = (await eager) ?? await loadDetails(runId, page);
    if (!isActive()) return nextEnvelope;
    setDetails(nextDetails);
    return nextEnvelope;
  }, [loadDetails, props.client, tradePage]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const isActive = () => !cancelled;
    const notify = (caught: unknown) => {
      const text = messageFor(caught);
      if (pollErrorRef.current !== text) {
        pollErrorRef.current = text;
        props.onError(text);
      }
    };
    const poll = async () => {
      try {
        const nextRuns = await refresh(isActive);
        if (cancelled) return;
        setListLoadFailed(false);
        pollErrorRef.current = null;
        const id = selectedId ?? props.focusedRunId ?? nextRuns[0]?.runId;
        if (id) {
          const listed = nextRuns.find((run) => run.runId === id);
          if (listed && missingRunRef.current === id) missingRunRef.current = null;
          const settled =
            listed && !ACTIVE_STATUSES.has(listed.status)
            && settledRef.current?.runId === id
            && settledRef.current.phase === listed.phase
            && settledRef.current.tradePage === tradePage;
          if (!settled && missingRunRef.current !== id) {
            try {
              await refreshSelected(id, isActive, listed?.status === "completed");
              if (!cancelled && listed) {
                setSelectedMissing(false);
                missingRunRef.current = null;
                if (!ACTIVE_STATUSES.has(listed.status)) {
                  settledRef.current = { runId: id, phase: listed.phase, tradePage };
                }
              }
            } catch (caught) {
              if (cancelled) return;
              if (caught instanceof BacktestApiError && caught.status === 404) {
                setSelectedMissing(true);
                missingRunRef.current = id;
              } else {
                notify(caught);
              }
            }
          }
        }
      } catch (caught) {
        if (cancelled) return;
        setListLoadFailed(true);
        notify(caught);
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
  }, [props.focusedRunId, props.onError, refresh, refreshSelected, selectedId, tradePage]);

  useEffect(() => {
    let cancelled = false;
    if (compareIds.length < 2) {
      setComparisons([]);
      return;
    }
    void Promise.all(compareIds.map((runId) => props.client.get(runId)))
      .then((values) => {
        if (!cancelled) setComparisons(values);
      })
      .catch((caught: unknown) => {
        if (!cancelled) props.onError(messageFor(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [compareIds, props.client, props.onError]);

  const selectRun = (runId: string) => {
    if (runId === selectedId) return;
    setSelectedId(runId);
    setTradePage(0);
    setLoading(true);
    setSelectedMissing(false);
    missingRunRef.current = null;
    props.onSelectRun?.(runId);
  };

  const reloadList = async () => {
    setLoading(true);
    setListLoadFailed(false);
    try {
      const nextRuns = await props.client.list();
      setRuns(nextRuns);
      setSelectedId((current) => current ?? nextRuns[0]?.runId);
      pollErrorRef.current = null;
    } catch (caught) {
      setListLoadFailed(true);
      const text = messageFor(caught);
      if (pollErrorRef.current !== text) {
        pollErrorRef.current = text;
        props.onError(text);
      }
    } finally {
      setLoading(false);
    }
  };

  const toggleCompare = (run: BacktestRun) => {
    if (run.status !== "completed") return;
    setCompareIds((current) => {
      if (current.includes(run.runId)) return current.filter((id) => id !== run.runId);
      if (current.length >= 4) {
        props.onError("Compare up to four completed runs at a time.");
        return current;
      }
      return [...current, run.runId];
    });
  };

  const cancel = async () => {
    if (!activeRun || !ACTIVE_STATUSES.has(activeRun.status)) return;
    setActing(true);
    try {
      const updated = await props.client.cancel(activeRun.runId);
      setEnvelope(updated);
      await refresh();
      props.onNotice(updated.run.status === "cancelled" ? "Backtest cancelled." : "Cancellation requested.");
    } catch (caught) {
      props.onError(messageFor(caught));
    } finally {
      setActing(false);
    }
  };

  const duplicate = async () => {
    if (!activeRun) return;
    setActing(true);
    try {
      const created = await props.client.create(activeRun.marketId, activeRun.config);
      setSelectedId(created.run.runId);
      setEnvelope(created);
      await refresh();
      props.onNotice("Backtest queued with the same configuration.");
    } catch (caught) {
      props.onError(messageFor(caught));
    } finally {
      setActing(false);
    }
  };

  const remove = async () => {
    if (!activeRun || ACTIVE_STATUSES.has(activeRun.status)) return;
    if (!window.confirm("Delete this terminal backtest and its saved data?")) return;
    setActing(true);
    try {
      await props.client.delete(activeRun.runId);
      const remaining = await refresh();
      const next = remaining.find((run) => run.runId !== activeRun.runId);
      setSelectedId(next?.runId);
      setEnvelope(null);
      setDetails(null);
      setCompareIds((current) => current.filter((id) => id !== activeRun.runId));
      // Keep the URL in step with the library, or a refresh would reopen the deleted run.
      props.onSelectRun?.(next?.runId ?? "");
      props.onNotice("Backtest deleted.");
    } catch (caught) {
      props.onError(messageFor(caught));
    } finally {
      setActing(false);
    }
  };

  return (
    <main className="backtest-workspace" id="backtests">
      <header className="backtest-hero">
        <div>
          <span className="eyebrow">Historical market analysis</span>
          <h1>Backtest</h1>
          <p>Evaluate strategy performance against one-minute Polymarket history with transparent execution assumptions and no wallet access.</p>
        </div>
        <div className="backtest-hero-actions">
          <button className="button button-quiet" type="button" onClick={() => void reloadList()}>
            <RefreshCw aria-hidden="true" /> Refresh runs
          </button>
          <button className="button button-dark" type="button" onClick={props.onAskAgent}>
            Ask the agent <ArrowRight aria-hidden="true" />
          </button>
          {props.onNewBacktest && (
            <button className="button button-primary" type="button" onClick={props.onNewBacktest}>
              New backtest <ArrowRight aria-hidden="true" />
            </button>
          )}
        </div>
      </header>

      <div className="backtest-layout">
        <aside className="run-library" aria-label="Recent backtests">
          <div className="run-library-heading">
            <div><span className="eyebrow">Run library</span><h2>Recent tapes</h2></div>
            <span>{runs.length}</span>
          </div>
          {runs.length === 0 && !loading ? (
            listLoadFailed ? (
              <div className="run-empty run-empty-error" role="alert">
                <AlertTriangle aria-hidden="true" />
                <strong>Backtests are unreachable</strong>
                <p>The run library could not be loaded. Your backtests were not lost — the storage service is just not responding.</p>
                <button type="button" onClick={() => void reloadList()}>Try again</button>
              </div>
            ) : (
              <div className="run-empty">
                <LineChart aria-hidden="true" />
                <strong>No backtests yet</strong>
                <p>Choose a resolved market and test momentum, mean reversion, or breakout.</p>
                <button type="button" onClick={props.onAskAgent}>Open research chat</button>
              </div>
            )
          ) : (
            <div className="run-list">
              {runs.map((run) => (
                <div className={`run-row ${run.runId === selectedId ? "run-row-selected" : ""}`} key={run.runId}>
                  <button type="button" onClick={() => selectRun(run.runId)}>
                    <span className={`run-state run-state-${run.status}`} />
                    <span>
                      <strong>{run.marketQuestion || compactId(run.marketId)}</strong>
                      <small>{strategyLabel(run.config.strategy)} · {phaseLabel(run.phase)} · {formatShortDate(run.createdAt)}</small>
                    </span>
                  </button>
                  <label title={run.status === "completed" ? "Add to comparison" : "Only completed runs can be compared"}>
                    <input
                      type="checkbox"
                      checked={compareIds.includes(run.runId)}
                      disabled={run.status !== "completed"}
                      onChange={() => toggleCompare(run)}
                    />
                    <span className="sr-only">Compare {run.marketQuestion || run.marketId}</span>
                  </label>
                </div>
              ))}
            </div>
          )}
          <div className="compare-counter">
            <GitCompare aria-hidden="true" />
            {compareIds.length < 2
              ? "Select 2–4 completed tapes to compare"
              : `${compareIds.length} tapes selected`}
          </div>
        </aside>

        <section className="backtest-stage" aria-live="polite">
          {activeRun ? (
            <>
              <RunHeader
                run={activeRun}
                acting={acting}
                onCancel={() => void cancel()}
                onDelete={() => void remove()}
                onDuplicate={() => void duplicate()}
              />
              {ACTIVE_STATUSES.has(activeRun.status) && <RunProgress run={activeRun} />}
              {activeRun.status === "failed" && <RunFailure run={activeRun} />}
              {activeRun.status === "cancelled" && (
                <div className="terminal-note"><X aria-hidden="true" /><span><strong>Replay cancelled</strong>No results were saved for this run.</span></div>
              )}
              {activeRun.status === "completed" && (
                <CompletedRun
                  result={activeResult}
                  details={activeDetails}
                  tradePage={tradePage}
                  onTradePage={setTradePage}
                />
              )}
              <ConfigurationStrip run={activeRun} />
            </>
          ) : selectedMissing ? (
            <div className="stage-empty stage-missing" role="alert">
              <AlertTriangle aria-hidden="true" />
              <h2>Backtest not found</h2>
              <p>This run no longer exists or belongs to another account.</p>
              <button type="button" className="button button-quiet" onClick={() => props.onSelectRun?.("")}>
                <RotateCcw aria-hidden="true" /> Back to all backtests
              </button>
            </div>
          ) : loading ? <LoadingTape /> : (
            <div className="stage-empty"><Activity aria-hidden="true" /><h2>Select a replay tape</h2><p>Choose a recent run to inspect its assumptions, fills, and result.</p></div>
          )}
        </section>
      </div>

      {comparisons.length >= 2 && <ComparisonPanel envelopes={comparisons} onClose={() => setCompareIds([])} />}
    </main>
  );
}

function RunHeader(props: {
  run: BacktestRun;
  acting: boolean;
  onCancel: () => void;
  onDelete: () => void;
  onDuplicate: () => void;
}) {
  const { run } = props;
  return (
    <header className="run-header">
      <div>
        <span className="eyebrow">Condition · {compactId(run.marketId)}</span>
        <h2>{run.marketQuestion || "Resolving market metadata…"}</h2>
        <div className="run-meta">
          <StatusBadge run={run} />
          <span>{strategyLabel(run.config.strategy)}</span>
          <span>Created {formatDateTime(run.createdAt)}</span>
          {run.resolvedOutcome && <span>Resolved {run.resolvedOutcome}</span>}
        </div>
      </div>
      <div className="run-actions">
        {ACTIVE_STATUSES.has(run.status) ? (
          <button type="button" className="button button-quiet" onClick={props.onCancel} disabled={props.acting}>
            <X aria-hidden="true" /> Cancel
          </button>
        ) : (
          <>
            <button type="button" className="button button-quiet" onClick={props.onDuplicate} disabled={props.acting}>
              <Copy aria-hidden="true" /> Rerun
            </button>
            <button type="button" className="icon-button" onClick={props.onDelete} disabled={props.acting} aria-label="Delete backtest">
              <Trash2 aria-hidden="true" />
            </button>
          </>
        )}
      </div>
    </header>
  );
}

function StatusBadge({ run }: { run: BacktestRun }) {
  return <span className={`backtest-badge backtest-badge-${run.status}`}><span />{phaseLabel(run.phase)}</span>;
}

function RunProgress({ run }: { run: BacktestRun }) {
  return (
    <section className="run-progress" aria-label={`Backtest ${run.progress}% complete`}>
      <div><Clock3 aria-hidden="true" /><span><strong>{phaseLabel(run.phase)}</strong>{progressCopy(run.phase)}</span><b>{run.progress}%</b></div>
      <div className="progress-track"><span style={{ width: `${run.progress}%` }} /></div>
      {run.cancelRequested && <p>Cancellation requested. The worker will stop at the next safe checkpoint.</p>}
    </section>
  );
}

function RunFailure({ run }: { run: BacktestRun }) {
  return (
    <div className="terminal-note terminal-note-failed">
      <AlertTriangle aria-hidden="true" />
      <span><strong>{run.failure?.code || "Backtest failed"}</strong>{run.failure?.message || "The replay could not be completed."}</span>
    </div>
  );
}

function CompletedRun({ result, details, tradePage, onTradePage }: {
  result: BacktestResult | null;
  details: RunDetails | null;
  tradePage: number;
  onTradePage: (page: number) => void;
}) {
  if (!result) {
    return (
      <>
        <MetricRibbonPlaceholder />
        <LoadingPanel title="Loading the replay chart" detail="Prices, executions, and equity on one clock." />
        <div className="result-lower-grid">
          <LoadingPanel title="Loading the trade ledger" detail="Every modeled fill for this run." />
          <LoadingPanel title="Loading benchmarks" detail="Buy-and-hold context for this market." />
        </div>
      </>
    );
  }
  const metrics = result.metrics;
  return (
    <>
      <section className="metric-ribbon" aria-label="Backtest summary">
        <ResultMetric label="Final equity" value={money(metrics.finalEquity)} emphasis />
        <ResultMetric label="Return" value={percent(metrics.returnPct)} tone={Number(metrics.returnPct) >= 0 ? "positive" : "negative"} />
        <ResultMetric label="Max drawdown" value={percent(metrics.maxDrawdownPct)} />
        <ResultMetric label="Trades" value={String(metrics.tradeCount)} />
        <ResultMetric label="Win rate" value={percent(metrics.winRatePct)} />
        <ResultMetric label="Fees" value={money(metrics.fees)} />
      </section>
      {details
        ? <ReplayTape points={details.series} trades={details.chartTrades} tradeTotal={details.tradeTotal} />
        : <LoadingPanel title="Loading the replay chart" detail="Prices, executions, and equity on one clock." />}
      <div className="result-lower-grid">
        {details
          ? <TradeLedger trades={details.trades} page={tradePage} total={details.tradeTotal} onPage={onTradePage} />
          : <LoadingPanel title="Loading the trade ledger" detail="Every modeled fill for this run." />}
        <aside className="benchmark-card">
          <span className="eyebrow">Context, not a target</span>
          <h3>Buy-and-hold benchmarks</h3>
          <div><span>Strategy P&amp;L</span><strong className={Number(metrics.pnl) >= 0 ? "value-positive" : "value-negative"}>{money(metrics.pnl)}</strong></div>
          <div><span>YES held to resolution</span><strong>{percent(metrics.yesBuyHoldReturnPct)}</strong></div>
          <div><span>NO held to resolution</span><strong>{percent(metrics.noBuyHoldReturnPct)}</strong></div>
          <div><span>Profit factor</span><strong>{metrics.profitFactor ? `${Number(metrics.profitFactor).toFixed(2)}×` : "—"}</strong></div>
          <div><span>Average holding time</span><strong>{duration(metrics.averageHoldingSeconds)}</strong></div>
          <div><span>Market exposure</span><strong>{percent(metrics.exposurePct)}</strong></div>
          <div><span>Skipped signals</span><strong>{metrics.skippedSignals}</strong></div>
          <ul>{result.assumptions.map((assumption) => <li key={assumption}>{assumption}</li>)}</ul>
        </aside>
      </div>
    </>
  );
}

function ResultMetric({ label, value, emphasis = false, tone }: {
  label: string;
  value: string;
  emphasis?: boolean;
  tone?: "positive" | "negative";
}) {
  return <div className={`result-metric ${emphasis ? "result-metric-emphasis" : ""} ${tone ? `metric-${tone}` : ""}`}><span>{label}</span><strong>{value}</strong></div>;
}

function ReplayTape({ points, trades, tradeTotal }: {
  points: BacktestSeriesPoint[];
  trades: BacktestTrade[];
  tradeTotal: number;
}) {
  const geometry = useMemo(() => chartGeometry(points), [points]);
  if (points.length < 2 || !geometry) {
    return <section className="replay-tape replay-tape-empty"><LineChart aria-hidden="true" /><p>Chart data is still being prepared.</p></section>;
  }
  const { width, height, yesPath, noPath, equityPath, x, priceY, equityY, first, last } = geometry;
  return (
    <section className="replay-tape" aria-labelledby="replay-heading">
      <div className="tape-heading">
        <div><span className="eyebrow">Replay tape</span><h3 id="replay-heading">Prices, executions, and equity on one clock</h3></div>
        <div className="tape-legend"><span className="legend-yes">YES</span><span className="legend-no">NO</span><span className="legend-equity">Equity</span></div>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="YES and NO price history with trade markers and portfolio equity">
        <title>Backtest replay from {formatDateTime(first)} to {formatDateTime(last)}</title>
        <g className="tape-grid">
          {[0, 0.25, 0.5, 0.75, 1].map((value) => (
            <g key={value}><line x1="58" x2={width - 24} y1={priceY(value)} y2={priceY(value)} /><text x="8" y={priceY(value) + 4}>{value.toFixed(2)}</text></g>
          ))}
          <line className="tape-seam" x1="58" x2={width - 24} y1="226" y2="226" />
        </g>
        <path className="tape-path tape-path-yes" d={yesPath} />
        <path className="tape-path tape-path-no" d={noPath} />
        <path className="tape-path tape-path-equity" d={equityPath} />
        <g className="trade-markers">
          {trades.map((trade) => {
            const entryX = x(trade.entryAt);
            const exitX = x(trade.exitAt);
            const entryY = priceY(Number(trade.entryPrice));
            const exitY = priceY(Number(trade.exitPrice));
            return (
              <g key={trade.tradeIndex}>
                <circle className={`trade-entry marker-${trade.outcome.toLowerCase()}`} cx={entryX} cy={entryY} r="5"><title>{trade.outcome} entry {trade.entryPrice}</title></circle>
                <path className="trade-exit" d={`M ${exitX - 4} ${exitY - 4} L ${exitX + 4} ${exitY + 4} M ${exitX + 4} ${exitY - 4} L ${exitX - 4} ${exitY + 4}`}><title>{trade.exitReason} {trade.exitPrice}</title></path>
              </g>
            );
          })}
        </g>
        <text className="axis-label" x="8" y="243">EQUITY</text>
        <text className="axis-value" x="58" y={equityY(geometry.equityMin) - 6}>{money(String(geometry.equityMin))}</text>
        <text className="axis-value" textAnchor="end" x={width - 24} y={equityY(geometry.equityMax) - 6}>{money(String(geometry.equityMax))}</text>
        <text className="axis-date" x="58" y={height - 8}>{formatShortDate(first)}</text>
        <text className="axis-date" textAnchor="end" x={width - 24} y={height - 8}>{formatShortDate(last)}</text>
      </svg>
      <p className="chart-fallback">The tape contains {points.length.toLocaleString()} plotted observations and {tradeTotal} completed trades. Circles mark entries; crosses mark exits{trades.length < tradeTotal ? " (the first 200 are shown)" : ""}.</p>
    </section>
  );
}

function TradeLedger({ trades, page, total, onPage }: {
  trades: BacktestTrade[];
  page: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(total / TRADE_PAGE_SIZE));
  return (
    <section className="trade-ledger">
      <div className="ledger-heading"><div><span className="eyebrow">Execution ledger</span><h3>Every modeled fill</h3></div><span>{total} trades</span></div>
      {trades.length === 0 ? <p className="ledger-empty">No qualifying strategy signal was filled.</p> : (
        <div className="table-scroll"><table><thead><tr><th className="num">#</th><th>Outcome</th><th>Entry</th><th>Exit</th><th className="num">Shares</th><th>Reason</th><th className="num">P&amp;L</th></tr></thead><tbody>
          {trades.map((trade) => <tr key={trade.tradeIndex}><td className="num">{trade.tradeIndex + 1}</td><td><span className={`outcome-chip outcome-${trade.outcome.toLowerCase()}`}>{trade.outcome}</span></td><td>{trade.entryPrice}<small>{formatShortDate(trade.entryAt)}</small></td><td>{trade.exitPrice}<small>{formatShortDate(trade.exitAt)}</small></td><td className="num">{Number(trade.shares).toFixed(2)}</td><td>{reasonLabel(trade.exitReason)}</td><td className={`num ${Number(trade.pnl) >= 0 ? "value-positive" : "value-negative"}`}>{money(trade.pnl)}</td></tr>)}
        </tbody></table></div>
      )}
      {total > TRADE_PAGE_SIZE && (
        <nav className="ledger-pagination" aria-label="Trade ledger pages">
          <button type="button" disabled={page === 0} onClick={() => onPage(page - 1)}>Previous</button>
          <span>Page {page + 1} of {pageCount}</span>
          <button type="button" disabled={page + 1 >= pageCount} onClick={() => onPage(page + 1)}>Next</button>
        </nav>
      )}
    </section>
  );
}

function ConfigurationStrip({ run }: { run: BacktestRun }) {
  const config = run.config;
  const strategyItems: Array<[string, string]> = config.strategy === "momentum_v1"
    ? [["Momentum signal", `${config.momentumThreshold} / ${config.momentumWindowMinutes}m`]]
    : config.strategy === "mean_reversion_v1"
      ? [["Mean discount", `${config.reversionThreshold} / ${config.reversionWindowMinutes}m`]]
      : [["Breakout buffer", `${config.breakoutThreshold} / ${config.breakoutWindowMinutes}m`]];
  const items: Array<[string, string]> = [
    ["Strategy", strategyLabel(config.strategy)],
    ["Starting capital", money(config.initialCapital)],
    ["Position size", percent(String(Number(config.positionSizePct) * 100))],
    ...strategyItems,
    ["Take profit", config.takeProfit],
    ["Stop loss", config.stopLoss],
    ["Max hold", `${config.maxHoldMinutes}m`],
    ["Cooldown", `${config.cooldownMinutes}m`],
    ["Slippage / side", config.slippage],
  ];
  return <section className="config-strip"><span className="eyebrow">Exact configuration</span><div>{items.map(([label, value]) => <span key={label}><small>{label}</small><strong>{value}</strong></span>)}</div>{run.datasetHash && <code>dataset sha256 · {run.datasetHash}</code>}</section>;
}

function ComparisonPanel({ envelopes, onClose }: { envelopes: BacktestRunEnvelope[]; onClose: () => void }) {
  return (
    <section className="comparison-panel" aria-labelledby="comparison-heading">
      <header><div><span className="eyebrow">Side-by-side audit</span><h2 id="comparison-heading">Run comparison</h2></div><button type="button" className="icon-button" onClick={onClose} aria-label="Close comparison"><X /></button></header>
      <div className="table-scroll"><table><thead><tr><th>Market</th><th>Strategy</th><th className="num">Return</th><th className="num">Drawdown</th><th className="num">Trades</th><th className="num">Win rate</th><th className="num">Fees</th><th className="num">Capital</th></tr></thead><tbody>
        {envelopes.map(({ run, result }) => <tr key={run.runId}><th>{run.marketQuestion || compactId(run.marketId)}<small>{compactId(run.runId)}</small></th><td>{strategyLabel(run.config.strategy)}</td><td className={`num ${Number(result?.metrics.returnPct || 0) >= 0 ? "value-positive" : "value-negative"}`}>{result ? percent(result.metrics.returnPct) : "—"}</td><td className="num">{result ? percent(result.metrics.maxDrawdownPct) : "—"}</td><td className="num">{result?.metrics.tradeCount ?? "—"}</td><td className="num">{result ? percent(result.metrics.winRatePct) : "—"}</td><td className="num">{result ? money(result.metrics.fees) : "—"}</td><td className="num">{money(run.config.initialCapital)}</td></tr>)}
      </tbody></table></div>
    </section>
  );
}

function LoadingPanel({ title, detail }: { title: string; detail: string }) {
  return (
    <section className="loading-panel">
      <RotateCcw aria-hidden="true" />
      <span><strong>{title}</strong>{detail}</span>
    </section>
  );
}

function MetricRibbonPlaceholder() {
  return (
    <section className="metric-ribbon metric-ribbon-placeholder" aria-label="Loading backtest summary">
      {["Final equity", "Return", "Max drawdown", "Trades", "Win rate", "Fees"].map((label) => (
        <div className="result-metric" key={label}>
          <span>{label}</span>
          <strong className="value-placeholder" aria-hidden="true" />
        </div>
      ))}
    </section>
  );
}

function LoadingTape() {
  return <div className="loading-tape"><RotateCcw aria-hidden="true" /><span><strong>Loading your backtests</strong>Fetching the most recent runs.</span></div>;
}

function chartGeometry(points: BacktestSeriesPoint[]) {
  if (points.length < 2) return null;
  const width = 960;
  const height = 356;
  const firstTime = Date.parse(points[0]!.timestamp);
  const lastTime = Date.parse(points.at(-1)!.timestamp);
  const timeSpan = Math.max(1, lastTime - firstTime);
  const x = (value: string) => 58 + ((Date.parse(value) - firstTime) / timeSpan) * (width - 82);
  const priceY = (value: number) => 28 + (1 - Math.max(0, Math.min(1, value))) * 178;
  const equities = points.map((point) => Number(point.equity));
  const equityMin = Math.min(...equities);
  const equityMax = Math.max(...equities);
  const equitySpan = Math.max(1, equityMax - equityMin);
  const equityY = (value: number) => 315 - ((value - equityMin) / equitySpan) * 64;
  const path = (key: "yesPrice" | "noPrice") => {
    let started = false;
    return points.flatMap((point) => {
      const value = point[key];
      if (value == null) return [];
      const command = started ? "L" : "M";
      started = true;
      return [`${command} ${x(point.timestamp).toFixed(2)} ${priceY(Number(value)).toFixed(2)}`];
    }).join(" ");
  };
  const equityPath = points.map((point, index) => `${index === 0 ? "M" : "L"} ${x(point.timestamp).toFixed(2)} ${equityY(Number(point.equity)).toFixed(2)}`).join(" ");
  return {
    width,
    height,
    yesPath: path("yesPrice"),
    noPath: path("noPrice"),
    equityPath,
    x,
    priceY,
    equityY,
    equityMin,
    equityMax,
    first: points[0]!.timestamp,
    last: points.at(-1)!.timestamp,
  };
}

function phaseLabel(value: BacktestRun["phase"]): string {
  return ({ queued: "Queued", fetching: "Fetching history", simulating: "Simulating", saving: "Saving", completed: "Completed", failed: "Failed", cancelled: "Cancelled" })[value];
}

function strategyLabel(value: BacktestRun["config"]["strategy"]): string {
  return ({
    momentum_v1: "Momentum",
    mean_reversion_v1: "Mean reversion",
    breakout_v1: "Breakout",
  })[value];
}

function progressCopy(value: BacktestRun["phase"]): string {
  return ({ queued: "Waiting for an available worker", fetching: "Normalizing one-minute YES and NO observations", simulating: "Applying next-observation fills and risk rules", saving: "Persisting metrics, trades, and chart series", completed: "Replay complete", failed: "Replay stopped", cancelled: "Replay cancelled" })[value];
}

function reasonLabel(value: BacktestTrade["exitReason"]): string {
  return ({ take_profit: "Take profit", stop_loss: "Stop loss", max_hold: "Maximum hold", settlement: "Resolution" })[value];
}

function money(value: string): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value));
}

function percent(value: string): string {
  return `${Number(value).toFixed(2)}%`;
}

function duration(value: string): string {
  const totalMinutes = Math.round(Number(value) / 60);
  if (totalMinutes < 60) return `${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
}

function compactId(value: string): string {
  return value.length <= 18 ? value : `${value.slice(0, 9)}…${value.slice(-6)}`;
}

function formatShortDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function messageFor(caught: unknown): string {
  return caught instanceof Error ? caught.message : "The backtest action could not be completed";
}
