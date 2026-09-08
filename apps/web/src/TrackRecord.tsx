// Public, signed-out track-record page at /u/:token. Mounted outside the
// Clerk wrapper in main.tsx — the viewer may never have authenticated, so
// everything here is plain gateway reads keyed by the secret share token.
import {
  CircleAlert,
  Copy,
  Link2,
  RefreshCw,
  RotateCcw,
  ShieldOff,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import type {
  PaperShareStatus,
  PublicTrackRecord,
  PublicTrackRecordFill,
  PublicTrackRecordPoint,
  PublicTrackRecordPosition,
} from "@polytrade/contracts";

import { GatewayClient, GatewayError } from "./api";
import { env } from "./env";
import { fetchPublicTrackRecord } from "./public-api";

export default function TrackRecordPage() {
  const { token = "" } = useParams();
  const [record, setRecord] = useState<PublicTrackRecord | null>(null);
  const [failed, setFailed] = useState<"not-found" | "error" | null>(null);
  const [loading, setLoading] = useState(true);

  // Best-effort in an SPA: keeps a leaked link out of search results that
  // respect robots, and names the tab for the shared visitor.
  useEffect(() => {
    document.title = "Paper track record · PolyTrade";
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setFailed(null);
    try {
      setRecord(await fetchPublicTrackRecord(env.VITE_API_URL, token));
    } catch (error) {
      setFailed(error instanceof GatewayError && error.status === 404 ? "not-found" : "error");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <main className="paper-loading"><RefreshCw className="spin" /><span>Opening track record…</span></main>
    );
  }

  if (failed === "not-found") {
    return <TrackRecordUnavailable title="This track record is not available" body="The link may have been rotated or turned off by its owner. Ask for a fresh link to view these paper results." />;
  }
  if (failed === "error") {
    return <TrackRecordUnavailable title="Track record could not be loaded" body="The gateway did not answer this request. It may be a temporary outage — try again in a moment." retry onRetry={() => void load()} />;
  }
  if (!record) return null;

  return (
    <main className="detail-page track-record-page">
      <header className="page-title">
        <div>
          <span className="eyebrow">Public paper track record</span>
          <h1>{record.profile.displayName}</h1>
          <p>Shared paper-trading performance. Virtual USDC only — no wallet, no real funds.</p>
        </div>
        <PublicShareButton />
      </header>

      <TrackRecordSummary record={record} />
      <TrackRecordEvidence record={record} />

      <section className="data-section track-record-curve" aria-label="Equity curve">
        <header><h2>Equity curve</h2><span className="count-pill">{record.equityCurve.length}</span></header>
        <EquityCurve points={record.equityCurve} />
        <p className="track-record-footnote">Curve tracks settled cash; the final point is live equity.</p>
      </section>

      <TrackRecordPositions positions={record.positions} />
      <TrackRecordFills fills={record.fills} />
      <p className="track-record-observed">Observed {formatDate(record.observedAt)}.</p>
    </main>
  );
}

function TrackRecordUnavailable(props: { title: string; body: string; retry?: boolean; onRetry?: () => void }) {
  return (
    <main className="detail-page track-record-page">
      <section className="empty-page-card">
        <CircleAlert aria-hidden="true" />
        <h1>{props.title}</h1>
        <p>{props.body}</p>
        {props.retry && <button className="button button-primary" type="button" onClick={props.onRetry}><RefreshCw /> Try again</button>}
      </section>
    </main>
  );
}

function TrackRecordSummary({ record }: { record: PublicTrackRecord }) {
  const stats = record.stats;
  return (
    <section className="data-section track-record-summary" aria-label="Track record summary">
      <div className="summary-metrics">
        <SummaryStat label="Equity" value={formatMoney(stats.equity)} />
        <SummaryStat label="Initial cash" value={formatMoney(stats.initialCash)} />
        <SummaryStat label="Total P&L" value={formatSignedMoney(stats.totalPnl)} tone={tone(stats.totalPnl)} />
        <SummaryStat label="Realized" value={formatSignedMoney(stats.realizedPnl)} tone={tone(stats.realizedPnl)} />
        <SummaryStat label="Unrealized" value={formatSignedMoney(stats.unrealizedPnl)} tone={tone(stats.unrealizedPnl)} />
        <SummaryStat label="Fees paid" value={formatMoney(stats.totalFees)} />
        <SummaryStat label="Trades" value={String(stats.tradeCount)} />
        <SummaryStat label="Win rate" value={stats.winRate === null ? "—" : `${stats.winRate}%`} />
      </div>
    </section>
  );
}

function SummaryStat(props: { label: string; value: string; tone?: string }) {
  return <span><strong className={props.tone}>{props.value}</strong><small>{props.label}</small></span>;
}

function TrackRecordEvidence({ record }: { record: PublicTrackRecord }) {
  const firstCurvePoint = record.equityCurve[0]?.t ?? record.profile.startedAt;
  return <section className="track-record-evidence" aria-labelledby="track-record-evidence-heading">
    <div><span className="eyebrow">Evidence context</span><h2 id="track-record-evidence-heading">How to read this record</h2></div>
    <dl>
      <div><dt>Record type</dt><dd>Paper ledger · virtual USDC</dd></div>
      <div><dt>Observed sample</dt><dd>{record.stats.tradeCount} fills · {record.positions.length} open positions</dd></div>
      <div><dt>Window begins</dt><dd>{formatDate(firstCurvePoint)}</dd></div>
      <div><dt>Equity treatment</dt><dd>Cash plus marked open positions</dd></div>
    </dl>
    <p>These results are a shareable simulation record, not a broker statement or a forecast. Fees are included; unsettled positions can change as the market moves.</p>
  </section>;
}

function PublicShareButton() {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
    } catch {
      // The address bar remains a manual sharing fallback.
    }
  };
  return <button className="button button-quiet public-share-button" type="button" onClick={() => void copy()}><Copy /> {copied ? "Link copied" : "Copy record link"}</button>;
}

const CURVE_WIDTH = 640;
const CURVE_HEIGHT = 200;
const CURVE_PAD_X = 10;
const CURVE_PAD_Y = 14;

function EquityCurve({ points }: { points: PublicTrackRecordPoint[] }) {
  // Guard against a single-point degenerate response.
  const series = points.length === 1 ? [points[0]!, points[0]!] : points;
  const equities = series.map((point) => Number(point.equity));
  const min = Math.min(...equities);
  const max = Math.max(...equities);
  const span = max - min || Math.abs(max) || 1;
  const x = (index: number) => series.length < 2
    ? CURVE_WIDTH / 2
    : CURVE_PAD_X + (index * (CURVE_WIDTH - 2 * CURVE_PAD_X)) / (series.length - 1);
  const y = (equity: number) => CURVE_HEIGHT - CURVE_PAD_Y - ((equity - min) / span) * (CURVE_HEIGHT - 2 * CURVE_PAD_Y);
  const coords = equities.map((equity, index) => [x(index), y(equity)] as const);
  const baseline = CURVE_HEIGHT - CURVE_PAD_Y;
  const line = coords.map(([cx, cy]) => `${cx.toFixed(1)},${cy.toFixed(1)}`).join(" ");
  const area = `M ${coords[0]![0]!.toFixed(1)},${baseline} L ${coords.map(([cx, cy]) => `${cx!.toFixed(1)},${cy!.toFixed(1)}`).join(" L ")} L ${coords.at(-1)![0]!.toFixed(1)},${baseline} Z`;
  const last = coords.at(-1)!;

  return (
    <svg className="equity-curve" viewBox={`0 0 ${CURVE_WIDTH} ${CURVE_HEIGHT}`} role="img" preserveAspectRatio="none">
      <title>Paper equity from {formatDate(series[0]!.t)} to {formatDate(series.at(-1)!.t)}</title>
      <path className="equity-curve-area" d={area} />
      <polyline className="equity-curve-line" points={line} />
      <circle className="equity-curve-dot" cx={last[0]!.toFixed(1)} cy={last[1]!.toFixed(1)} r="4" />
      <text className="equity-curve-label" x={CURVE_PAD_X} y={CURVE_PAD_Y + 4}>{formatSignedMoney(series[0]!.equity)}</text>
      <text className="equity-curve-label equity-curve-label-end" x={CURVE_WIDTH - CURVE_PAD_X} y={last[1]! - 10}>{formatSignedMoney(series.at(-1)!.equity)}</text>
    </svg>
  );
}

function TrackRecordPositions({ positions }: { positions: PublicTrackRecordPosition[] }) {
  return (
    <section className="data-section paper-table-section">
      <header><h2>Open positions</h2><span className="count-pill">{positions.length}</span></header>
      <div className="table-scroll"><table><thead><tr><th>Market / outcome</th><th className="num">Shares</th><th className="num">Average cost</th><th className="num">Liquidation</th><th className="num">Unrealized P&amp;L</th><th>Mark</th></tr></thead><tbody>
        {positions.map((position) => <tr key={`${position.marketQuestion}-${position.outcome}`}><th>{position.marketQuestion}<small>{position.outcome}</small></th><td className="num">{shortNumber(position.shares)}</td><td className="num">{formatPrice(position.averageCost)}</td><td className="num">{formatMoney(position.liquidationValue)}</td><td className={`num ${tone(position.unrealizedPnl)}`}>{formatSignedMoney(position.unrealizedPnl)}</td><td><span className={`paper-mark paper-mark-${position.markStatus}`}>{position.markStatus}</span></td></tr>)}
      </tbody></table></div>
      {!positions.length && <p className="table-empty">No open paper positions.</p>}
    </section>
  );
}

function TrackRecordFills({ fills }: { fills: PublicTrackRecordFill[] }) {
  return (
    <section className="data-section paper-table-section">
      <header><h2>Recent fills</h2><span className="count-pill">{fills.length}</span></header>
      <div className="table-scroll"><table><thead><tr><th>Time</th><th>Market / outcome</th><th>Type</th><th className="num">Shares</th><th className="num">VWAP</th><th className="num">Fee</th><th className="num">Cash effect</th><th className="num">Realized P&amp;L</th></tr></thead><tbody>
        {fills.map((fill) => <tr key={fill.fillId}><td>{formatDate(fill.createdAt)}</td><th>{fill.marketQuestion}<small>{fill.outcome}</small></th><td><span className={`paper-fill-kind paper-fill-${fill.kind.toLowerCase()}`}>{fill.kind}</span></td><td className="num">{shortNumber(fill.shares)}</td><td className="num">{formatPrice(fill.averagePrice)}</td><td className="num">{formatMoney(fill.fee)}</td><td className={`num ${tone(fill.cashEffect)}`}>{formatSignedMoney(fill.cashEffect)}</td><td className={`num ${tone(fill.realizedPnl)}`}>{fill.kind === "BUY" ? "—" : formatSignedMoney(fill.realizedPnl)}</td></tr>)}
      </tbody></table></div>
      {!fills.length && <p className="table-empty">No fills recorded yet.</p>}
    </section>
  );
}

export function ShareCard(props: { client: GatewayClient; onNotice: (message: string) => void; onError: (message: string) => void }) {
  const [status, setStatus] = useState<PaperShareStatus | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState<"create" | "rotate" | "disable" | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    props.client.paperShareStatus()
      .then((value) => { if (!cancelled) setStatus(value); })
      .catch(() => { if (!cancelled) setLoadFailed(true); });
    return () => { cancelled = true; };
  }, [props.client]);

  const shareUrl = status?.token ? `${window.location.origin}/u/${status.token}` : null;
  const enabled = Boolean(status?.enabled);

  const act = async (action: "create" | "rotate" | "disable") => {
    setBusy(action);
    try {
      const next = action === "disable"
        ? await props.client.disablePaperShare()
        : await props.client.enablePaperShare(action === "rotate");
      setStatus(next);
      props.onNotice(action === "disable" ? "Share link disabled — the public page now returns 404." : action === "rotate" ? "Share link rotated. The old link no longer works." : "Share link created.");
    } catch (error) {
      props.onError(error instanceof Error ? error.message : "The share action could not be completed");
    } finally {
      setBusy(null);
    }
  };

  const copy = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
    } catch {
      props.onError("Could not copy the link automatically — select it and copy by hand.");
    }
  };

  return (
    <section className="data-section share-card" aria-label="Share track record">
      <header><h2>Share track record</h2><span className={enabled ? "share-pill share-pill-on" : "share-pill share-pill-off"}>{enabled ? "Public" : "Private"}</span></header>
      {loadFailed ? (
        <p className="table-empty">Share status could not be loaded. Reload the page to try again.</p>
      ) : !status ? (
        <p className="table-empty">Checking share status…</p>
      ) : enabled && shareUrl ? (
        <>
          <p className="share-card-copy">Anyone with this secret link can view your paper stats, curve, and fills. Keep it private; rotate it any time.</p>
          <div className="share-token-row">
            <code>{shareUrl}</code>
            <button className="button button-quiet" type="button" onClick={() => void copy()}><Copy /> {copied ? "Copied" : "Copy"}</button>
          </div>
          <div className="share-card-actions">
            <button className="button button-quiet" type="button" disabled={busy !== null} onClick={() => void act("rotate")}><RotateCcw /> {busy === "rotate" ? "Rotating…" : "Rotate link"}</button>
            <button className="button button-danger" type="button" disabled={busy !== null} onClick={() => void act("disable")}><ShieldOff /> {busy === "disable" ? "Disabling…" : "Disable sharing"}</button>
          </div>
        </>
      ) : (
        <>
          <p className="share-card-copy">Create a secret link that shows your paper equity curve, stats, and fills — no sign-in needed for viewers.</p>
          <div className="share-card-actions">
            <button className="button button-primary" type="button" disabled={busy !== null} onClick={() => void act("create")}><Link2 /> {busy === "create" ? "Creating…" : "Create share link"}</button>
          </div>
        </>
      )}
    </section>
  );
}

function tone(value: string | null | undefined): string {
  const number = Number(value ?? 0);
  return number > 0 ? "value-positive" : number < 0 ? "value-negative" : "";
}

function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value))} USDC`;
}

function formatSignedMoney(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  const sign = number > 0 ? "+" : "";
  return `${sign}${new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(number)} USDC`;
}

function formatPrice(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(4) : "—";
}

function shortNumber(value: string): string {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(number) : value;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
