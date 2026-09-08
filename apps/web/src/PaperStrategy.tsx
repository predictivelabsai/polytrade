import {
  Gauge,
  Play,
  Radio,
  Sparkles,
  Square,
} from "lucide-react";
import {
  paperStrategyStartRequestSchema,
  type MarketSearchMarket,
  type PaperPortfolio,
  type PaperStrategy,
  type PaperStrategySnapshot,
  type PaperStrategyStartRequest,
  type StrategyTemplate,
} from "@polytrade/contracts";
import { useEffect, useMemo, useRef, useState } from "react";

import { GatewayClient } from "./api";
import { templateDraft } from "./strategy-templates";

const INTERVAL_OPTIONS = [5, 15, 30, 60, 300] as const;

interface StrategyDraft {
  entryPrice: string;
  exitPrice: string;
  sharesPerOrder: string;
  maxPosition: string;
  intervalSeconds: string;
}

export function PaperStrategyRunner(props: {
  client: GatewayClient;
  market: MarketSearchMarket | null;
  tokenId: string;
  portfolio: PaperPortfolio | null;
  snapshot: PaperStrategySnapshot | null;
  template?: StrategyTemplate | null;
  onClearTemplate?: () => void;
  onSnapshot: (snapshot: PaperStrategySnapshot) => void;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const [draft, setDraft] = useState<StrategyDraft>({
    entryPrice: "0.40",
    exitPrice: "0.60",
    sharesPerOrder: "10",
    maxPosition: "50",
    intervalSeconds: "15",
  });
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const startKeyRef = useRef<string | null>(null);
  const templateIdRef = useRef<string | null>(null);
  const sourceRef = useRef<string | null>(null);
  const editedRef = useRef(false);

  const selectedOutcome = useMemo(() => {
    if (!props.market) return null;
    const index = props.market.clobTokenIds.indexOf(props.tokenId);
    return index < 0 ? null : props.market.outcomes[index] ?? null;
  }, [props.market, props.tokenId]);
  const strategy = props.snapshot?.strategy ?? null;
  const running = strategy?.status === "RUNNING";
  const displayTokenId = running ? strategy.tokenId : props.tokenId;
  const currentShares = props.portfolio?.positions
    .find((position) => position.tokenId === displayTokenId)?.shares ?? "0";
  const request = useMemo<PaperStrategyStartRequest | null>(() => {
    if (!props.market || !selectedOutcome) return null;
    const parsed = paperStrategyStartRequestSchema.safeParse({
      conditionId: props.market.conditionId,
      tokenId: props.tokenId,
      entryPrice: draft.entryPrice,
      exitPrice: draft.exitPrice,
      sharesPerOrder: draft.sharesPerOrder,
      maxPosition: draft.maxPosition,
      intervalSeconds: Number(draft.intervalSeconds),
    });
    if (!parsed.success) return null;
    if (Number(parsed.data.sharesPerOrder) < Number(props.market.minimumOrderSize)) return null;
    return parsed.data;
  }, [draft, props.market, props.tokenId, selectedOutcome]);
  const validationError = strategyValidationError(draft, props.market?.minimumOrderSize, Boolean(selectedOutcome));

  useEffect(() => {
    if (running || !props.market || !selectedOutcome) return;
    // An armed template is a richer auto-fit: the band offsets are translated
    // against the selected outcome's reference price. A manual edit always
    // wins — the auto-fit re-applies only when the market, outcome, or
    // template identity changes, so clearing a template by editing never
    // clobbers the edit itself.
    const templateId = props.template?.id ?? null;
    const templateChanged = templateIdRef.current !== templateId;
    templateIdRef.current = templateId;
    if (templateChanged) {
      // Arming a template always re-applies; clearing one never re-fits, so
      // the values the user is looking at stay on screen.
      if (templateId !== null) editedRef.current = false;
      else return;
    }
    const source = `${props.market.conditionId}:${props.tokenId}`;
    if (sourceRef.current !== source) {
      sourceRef.current = source;
      editedRef.current = false;
    }
    if (editedRef.current) return;
    const fromTemplate = props.template
      ? templateDraft(props.template, props.market, props.tokenId)
      : null;
    if (fromTemplate) {
      setDraft(fromTemplate);
      startKeyRef.current = null;
      return;
    }
    const index = props.market.clobTokenIds.indexOf(props.tokenId);
    const reference = finiteNumber(props.market.outcomePrices[index] ?? "0.5");
    const minimum = Math.max(finiteNumber(props.market.minimumOrderSize), 1);
    const orderSize = Math.max(minimum, 10);
    setDraft((current) => ({
      ...current,
      entryPrice: formatInputPrice(Math.max(0.01, reference - 0.02)),
      exitPrice: formatInputPrice(Math.min(1, reference + 0.05)),
      sharesPerOrder: formatInputShares(orderSize),
      maxPosition: formatInputShares(orderSize * 5),
    }));
    startKeyRef.current = null;
  }, [props.market, props.tokenId, running, selectedOutcome, props.template]);

  const updateDraft = (key: keyof StrategyDraft, value: string) => {
    startKeyRef.current = null;
    editedRef.current = true;
    if (props.template) props.onClearTemplate?.();
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const start = async () => {
    if (!request || running) return;
    setStarting(true);
    startKeyRef.current ??= crypto.randomUUID();
    try {
      const next = await props.client.startPaperStrategy(request, startKeyRef.current);
      props.onSnapshot(next);
      props.onNotice("Background paper strategy started.");
      startKeyRef.current = null;
    } catch (error) {
      props.onError(message(error));
    } finally {
      setStarting(false);
    }
  };

  const stop = async () => {
    if (!running) return;
    setStopping(true);
    try {
      const next = await props.client.stopPaperStrategy();
      props.onSnapshot(next);
      props.onNotice("Background paper strategy stopped.");
      startKeyRef.current = null;
    } catch (error) {
      props.onError(message(error));
    } finally {
      setStopping(false);
    }
  };

  return (
    <section className={`paper-strategy ${running ? "paper-strategy-running" : ""}`} aria-label="Continuous paper strategy">
      <header>
        <div><span className="eyebrow paper-eyebrow">Server automation</span><h2>Price-band strategy</h2></div>
        <span className={`paper-strategy-chip paper-strategy-chip-${strategy?.status.toLowerCase() ?? "ready"}`}><Radio aria-hidden="true" /> {strategy?.status ?? "READY"}</span>
      </header>

      {props.template && !running ? (
        <div className="template-chip" role="status">
          <Sparkles aria-hidden="true" />
          <span>Template · {props.template.name}</span>
          <button className="button button-quiet template-chip-clear" type="button" onClick={props.onClearTemplate}>Use custom</button>
        </div>
      ) : null}

      {running && strategy ? (
        <div className="paper-strategy-target">
          <span>Running in the background</span>
          <strong>{strategy.marketQuestion}</strong>
          <small>{strategy.outcome} · {formatShares(currentShares)} shares held now</small>
        </div>
      ) : props.market && selectedOutcome ? (
        <div className="paper-strategy-target">
          <span>Next strategy target</span>
          <strong>{props.market.question}</strong>
          <small>{selectedOutcome} · {formatShares(currentShares)} shares held now</small>
        </div>
      ) : null}

      {running && strategy ? (
        <dl className="paper-strategy-band" aria-label="Running strategy settings">
          <div><dt>Buy</dt><dd>≤ {formatPrice(strategy.entryPrice)}</dd></div>
          <div><dt>Sell</dt><dd>≥ {formatPrice(strategy.exitPrice)}</dd></div>
          <div><dt>Order</dt><dd>{formatShares(strategy.sharesPerOrder)}</dd></div>
          <div><dt>Position cap</dt><dd>{formatShares(strategy.maxPosition)}</dd></div>
          <div><dt>Cadence</dt><dd>{intervalLabel(strategy.intervalSeconds)}</dd></div>
        </dl>
      ) : null}

      <div className="paper-strategy-heartbeat" role="status" aria-live="polite">
        <div className="paper-strategy-pulse" aria-hidden="true"><i /></div>
        <div>
          <span>{strategyStatusLabel(strategy?.status)}</span>
          <strong>{strategy?.lastMessage ?? "Select an outcome and set a price band to begin."}</strong>
        </div>
        <dl>
          <div><dt>Orders</dt><dd>{strategy?.ordersPlaced ?? 0}</dd></div>
          <div><dt>Scans</dt><dd>{strategy?.scansCompleted ?? 0}</dd></div>
          <div><dt>Next scan</dt><dd>{strategy?.nextScanAt ? relativeTime(strategy.nextScanAt) : "—"}</dd></div>
          <div><dt>Last book</dt><dd>{strategy?.lastQuoteSide && strategy.lastQuotePrice ? `${strategy.lastQuoteSide} ${formatPrice(strategy.lastQuotePrice)}` : "—"}</dd></div>
        </dl>
      </div>

      {!running ? (
        props.market && selectedOutcome ? (
          <>
            <div className="paper-strategy-fields">
              <label><span>Buy at or below</span><input aria-label="Strategy buy price" inputMode="decimal" value={draft.entryPrice} onChange={(event) => updateDraft("entryPrice", event.target.value)} /></label>
              <label><span>Sell at or above</span><input aria-label="Strategy sell price" inputMode="decimal" value={draft.exitPrice} onChange={(event) => updateDraft("exitPrice", event.target.value)} /></label>
              <label><span>Shares per order</span><input aria-label="Strategy shares per order" inputMode="decimal" value={draft.sharesPerOrder} onChange={(event) => updateDraft("sharesPerOrder", event.target.value)} /></label>
              <label><span>Maximum position</span><input aria-label="Strategy maximum position" inputMode="decimal" value={draft.maxPosition} onChange={(event) => updateDraft("maxPosition", event.target.value)} /></label>
              <label className="paper-strategy-field-wide"><span>Scan interval</span><select aria-label="Strategy scan interval" value={draft.intervalSeconds} onChange={(event) => updateDraft("intervalSeconds", event.target.value)}>{INTERVAL_OPTIONS.map((seconds) => <option key={seconds} value={seconds}>{intervalLabel(seconds)}</option>)}</select></label>
            </div>
            {validationError ? <p className="paper-strategy-error">{validationError}</p> : null}
          </>
        ) : (
          <div className="paper-strategy-empty"><Radio aria-hidden="true" /><strong>Select an outcome first</strong><p>The strategy uses the market and outcome chosen in the paper ticket.</p></div>
        )
      ) : null}

      <div className="paper-strategy-actions">
        {running ? (
          <button className="button paper-strategy-stop" type="button" disabled={stopping} onClick={() => void stop()}><Square aria-hidden="true" /> {stopping ? "Stopping…" : "Stop strategy"}</button>
        ) : (
          <button className="button paper-strategy-start" type="button" disabled={!request || starting} onClick={() => void start()}><Play aria-hidden="true" /> {starting ? "Starting…" : "Start in background"}</button>
        )}
        <span><Gauge aria-hidden="true" /> Fill-or-kill paper orders only</span>
      </div>

      <p className="paper-strategy-boundary">Runs on the gateway after this page closes. Stop prevents future scans; a fill already being committed may still finish.</p>

      <div className="paper-strategy-log">
        <div><span>Strategy tape</span><small>Dashboard polls continuously</small></div>
        {props.snapshot?.events.length ? (
          <ol aria-label="Strategy activity">{props.snapshot.events.map((event) => <li key={event.eventId} className={`paper-strategy-event-${event.action.toLowerCase()}`}><time>{formatTime(event.createdAt)}</time><span>{event.message}</span></li>)}</ol>
        ) : (
          <p>No background scans yet.</p>
        )}
      </div>
    </section>
  );
}

export function strategyValidationError(
  draft: StrategyDraft,
  minimumOrderSize: string | undefined,
  hasSelection = true,
): string | null {
  if (!hasSelection) return "Select a market and outcome before starting.";
  const parsed = paperStrategyStartRequestSchema.safeParse({
    conditionId: "condition",
    tokenId: "1",
    entryPrice: draft.entryPrice,
    exitPrice: draft.exitPrice,
    sharesPerOrder: draft.sharesPerOrder,
    maxPosition: draft.maxPosition,
    intervalSeconds: Number(draft.intervalSeconds),
  });
  if (!parsed.success) {
    if (Number(draft.entryPrice) >= Number(draft.exitPrice)) return "The sell price must be higher than the buy price.";
    if (!/^\d+$/.test(draft.intervalSeconds)) return "Choose a valid scan interval.";
    return "Use positive prices and quantities with at most six decimal places.";
  }
  if (Number(draft.sharesPerOrder) < Number(minimumOrderSize ?? 0)) {
    return `Each order must be at least ${formatShares(minimumOrderSize ?? "0")} shares.`;
  }
  return null;
}

function finiteNumber(value: string): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function formatInputPrice(value: number): string {
  return value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

function formatInputShares(value: number): string {
  return value.toFixed(6).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
}

function formatPrice(value: string): string {
  return finiteNumber(value).toFixed(4);
}

function formatShares(value: string): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(finiteNumber(value));
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "medium" }).format(new Date(value));
}

function relativeTime(value: string): string {
  const seconds = Math.max(0, Math.ceil((new Date(value).getTime() - Date.now()) / 1_000));
  return seconds <= 1 ? "due" : `in ${seconds}s`;
}

function intervalLabel(seconds: number): string {
  return seconds < 60 ? `Every ${seconds} seconds` : `Every ${seconds / 60} minute${seconds === 60 ? "" : "s"}`;
}

function strategyStatusLabel(status: PaperStrategy["status"] | undefined): string {
  if (status === "RUNNING") return "Background runner active";
  if (status === "FAILED") return "Strategy needs attention";
  if (status === "STOPPED") return "Last run stopped";
  return "Ready";
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : "The strategy action could not be completed";
}
