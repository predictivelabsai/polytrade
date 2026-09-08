import {
  Activity,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  paperQuoteRequestSchema,
  type MarketSearchMarket,
  type PaperFill,
  type PaperFillsResponse,
  type PaperPortfolio,
  type PaperQuote,
  type PaperQuoteRequest,
  type PaperStrategySnapshot,
  type StrategyTemplate,
} from "@polytrade/contracts";

import { GatewayClient, GatewayError } from "./api";
import { FirstValueGuide, type FirstValueStep } from "./FirstValueGuide";
import { MarketFocus } from "./MarketFocus";
import { PaperStrategyRunner } from "./PaperStrategy";
import { ShareCard } from "./TrackRecord";
import { StrategyTemplateGrid } from "./TemplateGrid";
import { resolveTemplateTokenId, strategyTemplateById, strategyTemplates } from "./strategy-templates";

const FILL_PAGE_SIZE = 20;

export function PaperWorkspace(props: {
  client: GatewayClient;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
  initialTemplateId?: string | null;
  onInitialTemplateConsumed?: () => void;
  initialMarket?: MarketSearchMarket | null;
  onMarketSelected?: (market: MarketSearchMarket) => void;
  onMarketCleared?: () => void;
}) {
  const [portfolio, setPortfolio] = useState<PaperPortfolio | null>(null);
  const [fills, setFills] = useState<PaperFillsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fillOffset, setFillOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MarketSearchMarket[]>([]);
  const [searching, setSearching] = useState(false);
  const [selectedMarket, setSelectedMarket] = useState<MarketSearchMarket | null>(null);
  const [selectedTokenId, setSelectedTokenId] = useState("");
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [shareQuantity, setShareQuantity] = useState("");
  const [quote, setQuote] = useState<PaperQuote | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [ordering, setOrdering] = useState(false);
  const [orderKey, setOrderKey] = useState<string | null>(null);
  const [strategySnapshot, setStrategySnapshot] = useState<PaperStrategySnapshot | null>(null);
  const [initialError, setInitialError] = useState<string | null>(null);
  const [activeTemplate, setActiveTemplate] = useState<StrategyTemplate | null>(null);
  const pollBusyRef = useRef(false);
  const pollFailedRef = useRef(false);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const initialMarketRef = useRef<string | null>(null);

  const loadFills = useCallback(async (offset: number) => {
    try {
      setFills(await props.client.paperFills(FILL_PAGE_SIZE, offset));
    } catch (error) {
      props.onError(message(error));
    }
  }, [props.client, props.onError]);

  const refreshPortfolio = useCallback(async (announce = false) => {
    setRefreshing(true);
    try {
      const next = await props.client.refreshPaperPortfolio();
      setPortfolio(next);
      if (announce) props.onNotice("Paper portfolio refreshed.");
      return next;
    } catch (error) {
      props.onError(message(error));
      return null;
    } finally {
      setRefreshing(false);
    }
  }, [props.client, props.onError, props.onNotice]);

  // The dashboard is shown only after every panel loaded, or with an explicit
  // failure panel — a null portfolio rendered as "$0.00 equity" would invent
  // ledger numbers that were never received.
  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setInitialError(null);
    try {
      const [nextPortfolio, nextFills, nextStrategy] = await Promise.all([
        props.client.refreshPaperPortfolio(),
        props.client.paperFills(FILL_PAGE_SIZE, 0),
        props.client.paperStrategy(),
      ]);
      setPortfolio(nextPortfolio);
      setFills(nextFills);
      setStrategySnapshot(nextStrategy);
    } catch (error) {
      setInitialError(message(error));
    } finally {
      setLoading(false);
    }
  }, [props.client]);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      if (pollBusyRef.current) return;
      pollBusyRef.current = true;
      try {
        const [nextPortfolio, nextStrategy, nextFills] = await Promise.all([
          props.client.paperPortfolio(),
          props.client.paperStrategy(),
          fillOffset === 0 ? props.client.paperFills(FILL_PAGE_SIZE, 0) : Promise.resolve(null),
        ]);
        if (cancelled) return;
        setPortfolio(nextPortfolio);
        setStrategySnapshot(nextStrategy);
        if (nextFills) setFills(nextFills);
        pollFailedRef.current = false;
      } catch {
        // Background refresh: retry on the next tick, but surface the failure
        // once per episode instead of silently letting the page go stale.
        if (!cancelled && !pollFailedRef.current) {
          pollFailedRef.current = true;
          props.onError("The paper dashboard lost its live updates; still showing the last received data. It will reconnect automatically.");
        }
      } finally {
        pollBusyRef.current = false;
      }
    };
    const timer = window.setInterval(() => void poll(), 3_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [fillOffset, props.client, props.onError]);

  const selectedOutcome = useMemo(() => {
    if (!selectedMarket) return null;
    const index = selectedMarket.clobTokenIds.indexOf(selectedTokenId);
    return index < 0 ? null : selectedMarket.outcomes[index] ?? null;
  }, [selectedMarket, selectedTokenId]);

  const firstValueStep = useMemo<FirstValueStep>(() => {
    if (strategySnapshot?.strategy?.status === "RUNNING") return "running";
    if (selectedMarket) return "review";
    if (activeTemplate) return "market";
    return "template";
  }, [activeTemplate, selectedMarket, strategySnapshot?.strategy?.status]);

  const request = useMemo<PaperQuoteRequest | null>(() => {
    if (!selectedMarket || !selectedTokenId) return null;
    const parsed = paperQuoteRequestSchema.safeParse({
      conditionId: selectedMarket.conditionId,
      tokenId: selectedTokenId,
      side,
      shares: shareQuantity,
    });
    return parsed.success ? parsed.data : null;
  }, [selectedMarket, selectedTokenId, shareQuantity, side]);

  const clearPreview = () => {
    setQuote(null);
    setOrderKey(null);
  };

  const runSearch = useCallback(async (term: string) => {
    const trimmed = term.trim();
    if (!trimmed) return;
    setSearching(true);
    clearPreview();
    try {
      const response = await props.client.searchMarkets(trimmed, "active", 20);
      const markets = response.events.flatMap((eventItem) => eventItem.markets)
        .filter((market, index, all) => (
          market.active
          && !market.closed
          && market.acceptingOrders
          && market.enableOrderBook
          && all.findIndex((candidate) => candidate.conditionId === market.conditionId) === index
        ));
      setResults(markets);
    } catch (error) {
      props.onError(message(error));
    } finally {
      setSearching(false);
    }
  }, [props.client, props.onError]);

  const searchMarkets = (event: FormEvent) => {
    event.preventDefault();
    void runSearch(query);
  };

  const selectMarket = (market: MarketSearchMarket) => {
    setSelectedMarket(market);
    // An armed template picks the outcome its band is written for; otherwise
    // the first outcome is selected, as before.
    const templateTokenId = activeTemplate ? resolveTemplateTokenId(activeTemplate, market) : null;
    setSelectedTokenId(templateTokenId ?? market.clobTokenIds[0] ?? "");
    setShareQuantity("");
    clearPreview();
    props.onMarketSelected?.(market);
  };

  const clearMarket = () => {
    setSelectedMarket(null);
    setSelectedTokenId("");
    setShareQuantity("");
    clearPreview();
    props.onMarketCleared?.();
  };

  useEffect(() => {
    const market = props.initialMarket;
    if (!market || initialMarketRef.current === market.conditionId) return;
    initialMarketRef.current = market.conditionId;
    if (!market.active || market.closed || !market.acceptingOrders || !market.enableOrderBook) return;
    setSelectedMarket(market);
    setSelectedTokenId(market.clobTokenIds[0] ?? "");
    setQuery(market.question);
  }, [props.initialMarket]);

  const deployTemplate = (template: StrategyTemplate) => {
    setActiveTemplate(template);
    setQuery(template.suggestedSearchQuery);
    void runSearch(template.suggestedSearchQuery);
    searchInputRef.current?.focus();
  };

  const clearTemplate = () => setActiveTemplate(null);

  // A template id handed in through the URL (public /templates landing) arms
  // the runner once on mount, pre-searches the picker, and hands the query
  // param back to the route wrapper so a refresh does not re-arm it.
  const initialTemplateRef = useRef(false);
  useEffect(() => {
    if (initialTemplateRef.current) return;
    initialTemplateRef.current = true;
    if (!props.initialTemplateId) return;
    const template = strategyTemplateById(props.initialTemplateId) ?? null;
    if (template) {
      setActiveTemplate(template);
      setQuery(template.suggestedSearchQuery);
      void runSearch(template.suggestedSearchQuery);
    }
    props.onInitialTemplateConsumed?.();
  }, [props.initialTemplateId, props.onInitialTemplateConsumed, runSearch]);

  const previewOrder = async () => {
    if (!request) return;
    setQuoting(true);
    try {
      const next = await props.client.paperQuote(request);
      setQuote(next);
      setOrderKey(crypto.randomUUID());
    } catch (error) {
      props.onError(message(error));
    } finally {
      setQuoting(false);
    }
  };

  const confirmOrder = async () => {
    if (!quote || !request || !orderKey) return;
    setOrdering(true);
    try {
      const result = await props.client.paperOrder({ ...request, limitPrice: quote.limitPrice }, orderKey);
      setPortfolio(result.portfolio);
      props.onNotice(`Paper ${result.fill.kind.toLowerCase()} filled: ${shortNumber(result.fill.shares)} ${result.fill.outcome} shares.`);
      clearPreview();
      setFillOffset(0);
      await Promise.all([refreshPortfolio(false), loadFills(0)]);
    } catch (error) {
      if (error instanceof GatewayError && error.code === "PAPER_PRICE_MOVED") clearPreview();
      props.onError(message(error));
    } finally {
      setOrdering(false);
    }
  };

  const changeFillPage = (offset: number) => {
    setFillOffset(offset);
    void loadFills(offset);
  };

  if (loading) {
    return <main className="paper-loading"><RefreshCw className="spin" /><span>Opening paper ledger…</span></main>;
  }

  if (initialError) {
    return (
      <main className="paper-loading paper-load-failed" role="alert">
        <CircleAlert aria-hidden="true" />
        <div>
          <strong>Paper ledger could not be loaded</strong>
          <p>{initialError}</p>
        </div>
        <button className="button button-quiet" type="button" onClick={() => void loadDashboard()}>Try again</button>
      </main>
    );
  }

  return (
    <main className="detail-page paper-page">
      <header className="page-title paper-title">
        <div>
          <span className="eyebrow paper-eyebrow">Simulation ledger</span>
          <h1>Paper trading</h1>
          <p>Practice against the live public order book with virtual USDC. Every fill stays inside this sandbox.</p>
        </div>
        <button className="button button-quiet" type="button" onClick={() => void refreshPortfolio(true)} disabled={refreshing}>
          <RefreshCw className={refreshing ? "spin" : ""} /> Refresh portfolio
        </button>
      </header>

      <section className="paper-boundary" aria-label="Paper trading boundary">
        <ShieldCheck aria-hidden="true" />
        <strong>Paper only</strong>
        <span>No wallet, signature, real order, or withdrawable balance.</span>
      </section>

      <PaperLedger portfolio={portfolio} />

      {portfolio?.warnings.length ? (
        <section className="paper-warnings" role="status">
          <CircleAlert aria-hidden="true" />
          <div><strong>Some prices could not be refreshed</strong>{portfolio.warnings.map((warning) => <p key={warning}>{warning}</p>)}</div>
        </section>
      ) : null}

      <FirstValueGuide step={firstValueStep} />

      <StrategyTemplateGrid
        templates={strategyTemplates}
        onDeploy={deployTemplate}
        runningBlocked={strategySnapshot?.strategy?.status === "RUNNING"}
      />

      {selectedMarket ? <MarketFocus market={selectedMarket} onClear={clearMarket} /> : null}

      <div className="paper-grid">
        <div className="paper-data-column">
          <section className="paper-market-panel">
            <header><div><span className="eyebrow paper-eyebrow">Find a contract</span><h2>Active markets</h2></div><Activity aria-hidden="true" /></header>
            <form className="paper-market-search" onSubmit={(event) => void searchMarkets(event)}>
              <Search aria-hidden="true" />
              <label className="sr-only" htmlFor="paper-market-query">Search active Polymarket markets</label>
              <input id="paper-market-query" ref={searchInputRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search elections, rates, crypto…" />
              <button type="submit" disabled={searching || !query.trim()}>{searching ? "Searching…" : "Search"}</button>
            </form>
            <div className="paper-market-results">
              {results.map((market) => (
                <button
                  className={selectedMarket?.conditionId === market.conditionId ? "paper-market-selected" : ""}
                  type="button"
                  key={market.conditionId}
                  onClick={() => selectMarket(market)}
                >
                  <span><strong>{market.question}</strong><small>{market.outcomes.map((outcome, index) => `${outcome} ${formatPrice(market.outcomePrices[index])}`).join(" · ")}</small></span>
                  {selectedMarket?.conditionId === market.conditionId ? <Check /> : <ArrowRight />}
                </button>
              ))}
              {!results.length && query && !searching && <p className="paper-empty">No active CLOB markets found for this search.</p>}
              {!results.length && !query && <p className="paper-empty">Search for a market, then choose an outcome in the paper ticket.</p>}
            </div>
          </section>

          <PaperPositions portfolio={portfolio} />
          <PaperFills fills={fills} offset={fillOffset} onPage={changeFillPage} />
          <ShareCard
            client={props.client}
            onNotice={props.onNotice}
            onError={props.onError}
          />
        </div>

        <aside className="paper-ticket-column">
          <PaperStrategyRunner
            client={props.client}
            market={selectedMarket}
            tokenId={selectedTokenId}
            portfolio={portfolio}
            snapshot={strategySnapshot}
            template={activeTemplate}
            onClearTemplate={clearTemplate}
            onSnapshot={setStrategySnapshot}
            onError={props.onError}
            onNotice={props.onNotice}
          />
          <section className="paper-ticket">
            <header>
              <div><span className="eyebrow paper-eyebrow">Fill-or-kill simulation</span><h2>Paper ticket</h2></div>
              <span className="paper-mode-chip">Virtual</span>
            </header>
            {selectedMarket ? (
              <>
                <div className="paper-selected-market"><strong>{selectedMarket.question}</strong><code>{compact(selectedMarket.conditionId)}</code></div>
                <div className="paper-side-toggle" aria-label="Paper order side">
                  {(["BUY", "SELL"] as const).map((value) => <button className={side === value ? `paper-side-${value.toLowerCase()}` : ""} type="button" key={value} aria-pressed={side === value} onClick={() => { setSide(value); clearPreview(); }}>{value}</button>)}
                </div>
                <div className="paper-ticket-fields">
                  <label><span>Outcome</span><select value={selectedTokenId} onChange={(event) => { setSelectedTokenId(event.target.value); clearPreview(); }}>{selectedMarket.clobTokenIds.map((tokenId, index) => <option key={tokenId} value={tokenId}>{selectedMarket.outcomes[index] ?? tokenId}</option>)}</select></label>
                  <label><span>Shares</span><input inputMode="decimal" value={shareQuantity} onChange={(event) => { setShareQuantity(event.target.value); clearPreview(); }} placeholder="0.000000" /></label>
                </div>
                <p className="paper-ticket-hint">{side === "BUY" ? "Buys sweep the lowest asks first." : `You own ${ownedShares(portfolio, selectedTokenId)} ${selectedOutcome ?? "outcome"} shares.`}</p>
                {quote ? <ExecutionTape quote={quote} /> : (
                  <button className="button button-primary button-wide paper-preview-button" type="button" disabled={!request || quoting} onClick={() => void previewOrder()}>{quoting ? "Reading order book…" : "Preview paper trade"} <ArrowRight /></button>
                )}
                {quote && (
                  <div className="paper-confirm-actions">
                    <button className="button button-quiet" type="button" disabled={ordering} onClick={clearPreview}>Edit</button>
                    <button className="button button-primary" type="button" disabled={ordering} onClick={() => void confirmOrder()}>{ordering ? "Filling…" : `Confirm paper ${quote.side.toLowerCase()}`}</button>
                  </div>
                )}
              </>
            ) : (
              <div className="paper-ticket-empty"><Search /><strong>Select a market</strong><p>Search active markets to prepare a simulated fill.</p></div>
            )}
            <footer>Price protection uses the preview’s worst consumed level. If the book moves beyond it, the complete order is rejected.</footer>
          </section>
        </aside>
      </div>
    </main>
  );
}

function PaperLedger({ portfolio }: { portfolio: PaperPortfolio | null }) {
  const pnl = portfolio?.totalPnl ?? "0";
  return (
    <section className="paper-ledger" aria-label="Paper account summary">
      <div className="paper-equity">
        <span>Paper equity</span>
        <strong>{formatMoney(portfolio?.equity)}</strong>
        <small>Started with 10,000.00 virtual USDC</small>
      </div>
      <dl>
        <div><dt>Cash</dt><dd>{formatMoney(portfolio?.cash)}</dd></div>
        <div><dt>Liquidation value</dt><dd>{formatMoney(portfolio?.positionsValue)}</dd></div>
        <div><dt>Realized P&amp;L</dt><dd className={tone(portfolio?.realizedPnl)}>{formatSignedMoney(portfolio?.realizedPnl)}</dd></div>
        <div><dt>Fees paid</dt><dd>{formatMoney(portfolio?.totalFees)}</dd></div>
      </dl>
      <div className={`paper-pnl-stamp ${tone(pnl)}`}><span>Net P&amp;L</span><strong>{formatSignedMoney(pnl)}</strong><small>{portfolio?.positions.length ?? 0} open positions</small></div>
    </section>
  );
}

function ExecutionTape({ quote }: { quote: PaperQuote }) {
  const cash = quote.side === "BUY" ? formatMoney(Math.abs(Number(quote.cashEffect))) : formatMoney(quote.cashEffect);
  return (
    <section className="execution-tape" aria-label="Paper trade preview">
      <div><span>Shares</span><strong>{shortNumber(quote.shares)}</strong></div><ArrowRight />
      <div><span>VWAP</span><strong>{formatPrice(quote.averagePrice)}</strong></div><ArrowRight />
      <div><span>Fee</span><strong>{formatMoney(quote.fee)}</strong></div><ArrowRight />
      <div className="execution-tape-total"><span>{quote.side === "BUY" ? "Debit" : "Proceeds"}</span><strong>{cash}</strong></div>
      <footer>Protected at {formatPrice(quote.limitPrice)} · observed {formatTime(quote.observedAt)}</footer>
    </section>
  );
}

function PaperPositions({ portfolio }: { portfolio: PaperPortfolio | null }) {
  return (
    <section className="data-section paper-table-section paper-holdings-panel">
      <header><h2>Holdings</h2><span className="count-pill">{portfolio?.positions.length ?? 0}</span></header>
      <div className="table-scroll"><table><thead><tr><th>Market / outcome</th><th className="num">Shares</th><th className="num">Average cost</th><th className="num">Best bid</th><th className="num">Liquidation</th><th className="num">Unrealized P&amp;L</th><th>Mark</th></tr></thead><tbody>
        {portfolio?.positions.map((position) => <tr key={position.tokenId}><th>{position.marketQuestion}<small>{position.outcome}</small></th><td className="num">{shortNumber(position.shares)}</td><td className="num">{formatPrice(position.averageCost)}</td><td className="num">{formatPrice(position.bestBid)}</td><td className="num">{formatMoney(position.liquidationValue)}</td><td className={`num ${tone(position.unrealizedPnl)}`}>{formatSignedMoney(position.unrealizedPnl)}</td><td><span className={`paper-mark paper-mark-${position.markStatus}`}>{position.markStatus}</span><small>{position.markedAt ? formatTime(position.markedAt) : "Not priced"}</small></td></tr>)}
      </tbody></table></div>
      {!portfolio?.positions.length && <p className="table-empty">No paper holdings yet. Preview a buy to start the ledger.</p>}
    </section>
  );
}

function PaperFills(props: { fills: PaperFillsResponse | null; offset: number; onPage: (offset: number) => void }) {
  const hasPrevious = props.offset > 0;
  const hasNext = Boolean(props.fills && props.offset + props.fills.items.length < props.fills.total);
  return (
    <section className="data-section paper-table-section paper-fills-panel">
      <header><h2>Paper fills</h2><div className="paper-pagination"><span className="count-pill">{props.fills?.total ?? 0}</span><button type="button" aria-label="Previous paper fills" disabled={!hasPrevious} onClick={() => props.onPage(Math.max(0, props.offset - FILL_PAGE_SIZE))}><ChevronLeft /></button><button type="button" aria-label="Next paper fills" disabled={!hasNext} onClick={() => props.onPage(props.offset + FILL_PAGE_SIZE)}><ChevronRight /></button></div></header>
      <div className="table-scroll"><table><thead><tr><th>Time</th><th>Market / outcome</th><th>Type</th><th className="num">Shares</th><th className="num">VWAP</th><th className="num">Fee</th><th className="num">Cash effect</th><th className="num">Realized P&amp;L</th></tr></thead><tbody>
        {props.fills?.items.map((fill) => <PaperFillRow key={fill.fillId} fill={fill} />)}
      </tbody></table></div>
      {!props.fills?.items.length && <p className="table-empty">No simulated fills or settlements recorded.</p>}
    </section>
  );
}

function PaperFillRow({ fill }: { fill: PaperFill }) {
  return <tr><td>{formatDate(fill.createdAt)}</td><th>{fill.marketQuestion}<small>{fill.outcome}</small></th><td><span className={`paper-fill-kind paper-fill-${fill.kind.toLowerCase()}`}>{fill.kind}</span></td><td className="num">{shortNumber(fill.shares)}</td><td className="num">{formatPrice(fill.averagePrice)}</td><td className="num">{formatMoney(fill.fee)}</td><td className={`num ${tone(fill.cashEffect)}`}>{formatSignedMoney(fill.cashEffect)}</td><td className={`num ${tone(fill.realizedPnl)}`}>{fill.kind === "BUY" ? "—" : formatSignedMoney(fill.realizedPnl)}</td></tr>;
}

function ownedShares(portfolio: PaperPortfolio | null, tokenId: string): string {
  return shortNumber(portfolio?.positions.find((position) => position.tokenId === tokenId)?.shares ?? "0");
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : "The paper action could not be completed";
}

function compact(value: string): string {
  return value.length <= 18 ? value : `${value.slice(0, 9)}…${value.slice(-6)}`;
}

function tone(value: string | null | undefined): string {
  const number = Number(value ?? 0);
  return number > 0 ? "value-positive" : number < 0 ? "value-negative" : "";
}

function formatMoney(value: string | number | null | undefined): string {
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

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
