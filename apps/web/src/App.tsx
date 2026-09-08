import {
  ArrowRight,
  BarChart3,
  Check,
  CircleAlert,
  CircleDot,
  Clock3,
  ExternalLink,
  LogOut,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  WalletCards,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  createOrderProposalSchema,
  tradingActionProposalSchema,
  type CancellationSelector,
  type CreateOrderProposal,
  type OrderIntentResponse,
  type TradingActionProposal,
  type WalletSessionResponse,
} from "@polytrade/contracts";
import type { Hex } from "viem";

import { GatewayClient, type AccountSnapshot } from "./api";
import { AgentApiError, getAgentThreadItems, runAgentTurn } from "./agent";
import { useAuthentication } from "./auth";
import { BacktestClient } from "./backtest";
import { BacktestsWorkspace } from "./Backtests";
import { checkBrowserEligibility, type Eligibility } from "./eligibility";
import { env } from "./env";
import { DEFAULT_CASH_EXPOSURE_LIMIT, maximumExposure, orderRiskSummary, parseCashExposureLimit } from "./order";
import { connectWallet, signTypedPayload, type ConnectedWallet } from "./wallet";

interface DeskMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
}

export interface ProposalDraft {
  proposal: TradingActionProposal;
  expiresAt: string;
}

interface PendingOrder {
  createKey: string;
  intent?: OrderIntentResponse;
  signature?: Hex;
}

type FlowStage = "research" | "review" | "sign" | "live";
type BusyAction = "agent" | "wallet" | "submit" | "account" | "cancel" | null;
type Workspace = "trade" | "backtests";

const STARTER_QUESTIONS = [
  "Backtest mean reversion on a resolved election market",
  "Find a liquid active market for a paper-trading setup",
  "Research the leading Fed market and summarize its order book",
];

export default function App() {
  const authentication = useAuthentication();
  const gateway = useMemo(
    () => new GatewayClient(env.VITE_API_URL, authentication.getToken),
    [authentication.getToken],
  );
  const backtests = useMemo(
    () => new BacktestClient(env.VITE_API_URL, authentication.getToken),
    [authentication.getToken],
  );
  const [messages, setMessages] = useState<DeskMessage[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "Ask me about a Polymarket market, its order book, price history, or your connected account. I can also replay momentum, mean reversion, or breakout on a resolved binary market. Real orders always remain a separate, reviewed action.",
    },
  ]);
  const [question, setQuestion] = useState("");
  const [threadId, setThreadId] = useState<string | undefined>(() =>
    sessionStorage.getItem("polytrade.thread") ?? undefined,
  );
  const [proposalDraft, setProposalDraft] = useState<ProposalDraft | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [pendingOrder, setPendingOrder] = useState<PendingOrder | null>(null);
  const [wallet, setWallet] = useState<ConnectedWallet | null>(null);
  const [session, setSession] = useState<WalletSessionResponse | null>(null);
  const [signatureType, setSignatureType] = useState<0 | 1 | 2 | 3>(0);
  const [funderAddress, setFunderAddress] = useState("");
  const [eligibility, setEligibility] = useState<Eligibility | null>(null);
  const [account, setAccount] = useState<AccountSnapshot | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [flowStage, setFlowStage] = useState<FlowStage>("research");
  const [workspace, setWorkspace] = useState<Workspace>("trade");
  const [focusedBacktestId, setFocusedBacktestId] = useState<string>();
  const endRef = useRef<HTMLDivElement>(null);
  const initialThreadId = useRef(threadId);
  const hasInteracted = useRef(false);

  const refreshEligibility = useCallback(async () => {
    setEligibility(await checkBrowserEligibility());
  }, []);

  useEffect(() => {
    void refreshEligibility();
  }, [refreshEligibility]);

  useEffect(() => {
    const savedThreadId = initialThreadId.current;
    if (!savedThreadId) return;
    let cancelled = false;
    void getAgentThreadItems(
      env.VITE_API_URL,
      authentication.getToken,
      savedThreadId,
    ).then((items) => {
      if (cancelled || hasInteracted.current) return;
      const restored = items
        .filter((item) => item.kind === "message")
        .map((item) => ({ id: item.id, role: item.role, text: item.text } satisfies DeskMessage));
      if (restored.length > 0) setMessages(restored);
      const latestAction = [...items]
        .reverse()
        .find((item) => (
          item.kind === "backtest"
          || (item.kind === "proposal" && Date.parse(item.expiresAt) > Date.now())
        ));
      if (latestAction?.kind === "proposal") {
        setProposalDraft({
          proposal: latestAction.proposal,
          expiresAt: latestAction.expiresAt,
        });
        setFlowStage("review");
      } else if (latestAction?.kind === "backtest") {
        setFocusedBacktestId(latestAction.backtest.runId);
        setWorkspace("backtests");
      }
    }).catch((caught: unknown) => {
      if (
        cancelled
        || hasInteracted.current
        || !(caught instanceof AgentApiError)
        || caught.status !== 404
      ) return;
      sessionStorage.removeItem("polytrade.thread");
      setThreadId(undefined);
    });
    return () => {
      cancelled = true;
    };
  }, [authentication.getToken]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages]);

  const submitQuestion = async (event?: FormEvent) => {
    event?.preventDefault();
    const text = question.trim();
    if (!text || busy === "agent") return;
    hasInteracted.current = true;
    setError(null);
    setNotice(null);
    setQuestion("");
    setMessages((current) => [
      ...current,
      { id: `user-${crypto.randomUUID()}`, role: "user", text },
    ]);
    setBusy("agent");
    setFlowStage("research");
    try {
      await runAgentTurn({
        apiUrl: env.VITE_API_URL,
        getToken: authentication.getToken,
        threadId,
        text,
        handlers: {
          onThreadId: (id) => {
            sessionStorage.setItem("polytrade.thread", id);
            setThreadId(id);
          },
          onMessageStart: (id) => {
            setMessages((current) =>
              current.some((message) => message.id === id)
                ? current
                : [...current, { id, role: "assistant", text: "" }],
            );
          },
          onMessageText: (id, value) => {
            setMessages((current) =>
              current.map((message) => (message.id === id ? { ...message, text: value } : message)),
            );
          },
          onProposal: (proposal, expiresAt) => {
            setProposalDraft({ proposal, expiresAt });
            setPendingOrder(null);
            setReviewed(false);
            setFlowStage("review");
          },
          onBacktest: (backtest) => {
            setFocusedBacktestId(backtest.runId);
            setWorkspace("backtests");
            setNotice("Backtest queued. The replay tape will update as the worker progresses.");
          },
        },
      });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  };

  const establishWalletSession = async () => {
    setBusy("wallet");
    setError(null);
    setNotice(null);
    try {
      const connected = await connectWallet();
      setWallet(connected);
      const request = {
        walletAddress: connected.address,
        signatureType,
        ...(signatureType === 0 ? {} : { funderAddress }),
      };
      const challenge = await gateway.createChallenge(request);
      const signature = await signTypedPayload(connected, challenge.typedData);
      const created = await gateway.createWalletSession(challenge.challengeId, signature);
      setSession(created);
      setNotice("Wallet authority verified. No private key or seed phrase left your wallet.");
      await loadAccount();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  };

  const disconnectWalletSession = async () => {
    if (!session) return;
    setBusy("wallet");
    setError(null);
    try {
      await gateway.revokeWalletSession(session.sessionId);
      setSession(null);
      setWallet(null);
      setAccount(null);
      setPendingOrder(null);
      setNotice("Wallet session disconnected.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  };

  const loadAccount = async () => {
    setBusy((current) => current ?? "account");
    setError(null);
    try {
      setAccount(await gateway.account());
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy((current) => (current === "account" ? null : current));
    }
  };

  const executeReviewedAction = async () => {
    if (!proposalDraft || !reviewed || !session || !wallet) return;
    setBusy("submit");
    setError(null);
    setNotice(null);
    setFlowStage("sign");
    try {
      if (!pendingOrder?.signature && new Date(proposalDraft.expiresAt).getTime() <= Date.now()) {
        throw new Error("This draft has expired. Ask the agent to refresh the market data.");
      }
      if (proposalDraft.proposal.action === "cancel") {
        await gateway.cancel(session.sessionId, proposalDraft.proposal.selector);
        setNotice("Cancellation request accepted. Refresh account data to confirm the final state.");
      } else {
        const proposal = createOrderProposalSchema.parse(proposalDraft.proposal);
        let pending = pendingOrder ?? { createKey: crypto.randomUUID() };
        setPendingOrder(pending);
        const intent = pending.intent ?? await gateway.createIntent(
          session.sessionId,
          proposal,
          pending.createKey,
        );
        pending = { ...pending, intent };
        setPendingOrder(pending);
        const signature = pending.signature ?? await signTypedPayload(wallet, intent.typedData);
        pending = { ...pending, signature };
        setPendingOrder(pending);
        const submissionKey = crypto.randomUUID();
        const result = await gateway.submitIntent(intent.intentId, signature, submissionKey);
        setNotice(orderResultMessage(result));
      }
      setPendingOrder(null);
      setProposalDraft(null);
      setReviewed(false);
      setFlowStage("live");
      await loadAccount();
    } catch (caught) {
      setFlowStage("review");
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  };

  const cancelOpenOrder = async (selector: CancellationSelector) => {
    if (!session || !window.confirm("Cancel this open Polymarket order?")) return;
    setBusy("cancel");
    setError(null);
    try {
      await gateway.cancel(session.sessionId, selector);
      setNotice("Cancellation request accepted.");
      await loadAccount();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  };

  const tradeAllowed = Boolean(eligibility?.verified && !eligibility.blocked);
  const visibleMessages = messages.filter((message) => message.text.trim());

  return (
    <div className="desk-shell">
      <header className="topbar">
        <a className="brand" href={workspace === "trade" ? "#research" : "#backtests"} aria-label="PolyTrade home">
          <span className="brand-mark" aria-hidden="true"><CircleDot /></span>
          <span>POLY<span>TRADE</span></span>
        </a>
        <div className="topbar-controls">
          <nav className="workspace-switch" aria-label="Workspace">
            <button
              className={workspace === "trade" ? "workspace-active" : ""}
              type="button"
              aria-current={workspace === "trade" ? "page" : undefined}
              onClick={() => setWorkspace("trade")}
            >
              <CircleDot aria-hidden="true" /> Trade
            </button>
            <button
              className={workspace === "backtests" ? "workspace-active" : ""}
              type="button"
              aria-current={workspace === "backtests" ? "page" : undefined}
              onClick={() => setWorkspace("backtests")}
            >
              <BarChart3 aria-hidden="true" /> Backtests
            </button>
          </nav>
          <div className="topbar-meta">
            <span className="auth-pill">
              <ShieldCheck aria-hidden="true" />
              Clerk identity
            </span>
            {authentication.accountControl}
          </div>
        </div>
      </header>

      {workspace === "trade" ? (
        <>
          <DecisionRail stage={flowStage} />

          <main className="desk-grid" id="research">
        <section className="research-panel" aria-labelledby="research-heading">
          <div className="section-heading">
            <div>
              <span className="eyebrow">Polymarket research</span>
              <h1 id="research-heading">Turn a market question into a reviewed decision.</h1>
            </div>
            <StatusStamp eligibility={eligibility} onRefresh={() => void refreshEligibility()} />
          </div>

          <div className="conversation" aria-live="polite">
            {visibleMessages.map((message) => (
              <article className={`message message-${message.role}`} key={message.id}>
                <span className="message-author">{message.role === "user" ? "You" : "Research agent"}</span>
                <p>{message.text}</p>
              </article>
            ))}
            {busy === "agent" && (
              <div className="thinking-line" role="status">
                <span /><span /><span /> Reading Polymarket data
              </div>
            )}
            <div ref={endRef} />
          </div>

          {visibleMessages.length === 1 && (
            <div className="starter-grid" aria-label="Example questions">
              {STARTER_QUESTIONS.map((starter) => (
                <button key={starter} type="button" onClick={() => setQuestion(starter)}>
                  <ArrowRight aria-hidden="true" /> {starter}
                </button>
              ))}
            </div>
          )}

          <form className="ask-box" onSubmit={(event) => void submitQuestion(event)}>
            <label htmlFor="market-question">Ask about Polymarket</label>
            <textarea
              id="market-question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void submitQuestion();
                }
              }}
              placeholder="Compare prices, inspect liquidity, or prepare an order draft…"
              rows={3}
              maxLength={2_000}
            />
            <div className="ask-footer">
              <span>Shift + Enter for a new line</span>
              <button className="send-button" type="submit" disabled={!question.trim() || busy === "agent"}>
                <Send aria-hidden="true" /> Ask
              </button>
            </div>
          </form>
        </section>

        <aside className="action-column" aria-label="Order review and account">
          <WalletPanel
            account={account}
            eligibility={eligibility}
            funderAddress={funderAddress}
            onCancelOrder={(selector) => void cancelOpenOrder(selector)}
            onConnect={() => void establishWalletSession()}
            onDisconnect={() => void disconnectWalletSession()}
            onFunderAddress={setFunderAddress}
            onRefresh={() => void loadAccount()}
            onSignatureType={setSignatureType}
            session={session}
            signatureType={signatureType}
            busy={busy}
          />

          <ProposalTicket
            draft={proposalDraft}
            onChange={(proposal) => {
              const parsed = tradingActionProposalSchema.safeParse(proposal);
              if (parsed.success && proposalDraft) {
                setProposalDraft({ ...proposalDraft, proposal: parsed.data });
              }
              setPendingOrder(null);
              setReviewed(false);
            }}
            onClear={() => {
              setProposalDraft(null);
              setPendingOrder(null);
              setReviewed(false);
              setFlowStage("research");
            }}
            onExecute={() => void executeReviewedAction()}
            reviewed={reviewed}
            onReviewed={setReviewed}
            sessionReady={Boolean(session && wallet)}
            tradeAllowed={tradeAllowed}
            submitting={busy === "submit"}
            pendingSignedOrder={Boolean(pendingOrder?.signature)}
          />
        </aside>
          </main>
        </>
      ) : (
        <BacktestsWorkspace
          client={backtests}
          focusedRunId={focusedBacktestId}
          onAskAgent={() => {
            setWorkspace("trade");
            setQuestion((current) => current || "Backtest a strategy on a resolved binary Polymarket market");
          }}
          onError={(message) => { setError(message); setNotice(null); }}
          onNotice={(message) => { setNotice(message); setError(null); }}
        />
      )}

      {(error || notice) && (
        <div className={`toast ${error ? "toast-error" : "toast-success"}`} role={error ? "alert" : "status"}>
          {error ? <CircleAlert aria-hidden="true" /> : <Check aria-hidden="true" />}
          <span>{error ?? notice}</span>
          <button type="button" aria-label="Dismiss message" onClick={() => { setError(null); setNotice(null); }}>
            <X aria-hidden="true" />
          </button>
        </div>
      )}

      <footer className="desk-footer">
        <span>{workspace === "trade"
          ? "Public Polymarket data only · Real orders require review and a wallet signature"
          : "Hypothetical historical results · No wallet or order controls"}</span>
        <a href="https://polymarket.com/terms-of-use" target="_blank" rel="noreferrer">
          Polymarket terms <ExternalLink aria-hidden="true" />
        </a>
      </footer>
    </div>
  );
}

function DecisionRail({ stage }: { stage: FlowStage }) {
  const steps: Array<{ key: FlowStage; label: string; detail: string }> = [
    { key: "research", label: "Research", detail: "Agent reads" },
    { key: "review", label: "Review", detail: "You decide" },
    { key: "sign", label: "Sign", detail: "Wallet proves" },
    { key: "live", label: "Live", detail: "Gateway confirms" },
  ];
  const active = steps.findIndex((step) => step.key === stage);
  return (
    <nav className="decision-rail" aria-label="Order decision stages">
      {steps.map((step, index) => (
        <div className={index <= active ? "rail-step rail-step-active" : "rail-step"} key={step.key}>
          <span className="rail-index">{String(index + 1).padStart(2, "0")}</span>
          <span><strong>{step.label}</strong><small>{step.detail}</small></span>
        </div>
      ))}
    </nav>
  );
}

function StatusStamp({ eligibility, onRefresh }: { eligibility: Eligibility | null; onRefresh: () => void }) {
  const label = eligibility
    ? eligibility.verified
      ? eligibility.blocked
        ? `Research only · ${eligibility.country || "blocked region"}`
        : `Trading eligible · ${eligibility.country || "verified"}`
      : "Research only · location unverified"
    : "Checking order eligibility";
  return (
    <button className={`status-stamp ${eligibility?.blocked ? "status-blocked" : ""}`} type="button" onClick={onRefresh}>
      <span className="status-light" /> {label} <RefreshCw aria-hidden="true" />
    </button>
  );
}

function WalletPanel(props: {
  account: AccountSnapshot | null;
  eligibility: Eligibility | null;
  funderAddress: string;
  onCancelOrder: (selector: CancellationSelector) => void;
  onConnect: () => void;
  onDisconnect: () => void;
  onFunderAddress: (value: string) => void;
  onRefresh: () => void;
  onSignatureType: (value: 0 | 1 | 2 | 3) => void;
  session: WalletSessionResponse | null;
  signatureType: 0 | 1 | 2 | 3;
  busy: BusyAction;
}) {
  const blocked = props.eligibility?.blocked || !props.eligibility?.verified;
  return (
    <section className="wallet-card" aria-labelledby="wallet-heading">
      <div className="card-title-row">
        <div>
          <span className="eyebrow">Wallet authority</span>
          <h2 id="wallet-heading">{props.session ? compactAddress(props.session.walletAddress) : "Not connected"}</h2>
        </div>
        <WalletCards aria-hidden="true" />
      </div>
      {props.session ? (
        <>
          <div className="session-state"><span /> Active until {formatTime(props.session.expiresAt)}</div>
          <div className="account-stats">
            <Metric label="Positions" value={props.account?.positions.length ?? "—"} />
            <Metric label="Open orders" value={props.account?.openOrders.length ?? "—"} />
            <Metric label="Fills" value={props.account?.trades.length ?? "—"} />
          </div>
          <div className="wallet-actions">
            <button className="button button-quiet" type="button" onClick={props.onRefresh} disabled={Boolean(props.busy)}>
              <RefreshCw aria-hidden="true" /> Refresh
            </button>
            <button className="button button-quiet" type="button" onClick={props.onDisconnect} disabled={Boolean(props.busy)}>
              <LogOut aria-hidden="true" /> Disconnect
            </button>
          </div>
          <OpenOrders orders={props.account?.openOrders ?? []} onCancel={props.onCancelOrder} disabled={props.busy === "cancel"} />
        </>
      ) : (
        <>
          <p className="card-copy">Identity and wallet authority stay separate. Connect an existing funded Polygon wallet only when you want account access or a real order.</p>
          <label className="field-label" htmlFor="signature-type">Wallet structure</label>
          <select
            id="signature-type"
            value={props.signatureType}
            onChange={(event) => props.onSignatureType(Number(event.target.value) as 0 | 1 | 2 | 3)}
          >
            <option value={0}>Direct wallet (EOA)</option>
            <option value={1}>Polymarket proxy</option>
            <option value={2}>Safe wallet</option>
            <option value={3}>EIP-1271 wallet</option>
          </select>
          {props.signatureType !== 0 && (
            <>
              <label className="field-label" htmlFor="funder-address">Funder / maker address</label>
              <input
                id="funder-address"
                value={props.funderAddress}
                onChange={(event) => props.onFunderAddress(event.target.value)}
                placeholder="0x…"
                spellCheck={false}
              />
            </>
          )}
          <button
            className="button button-dark button-wide"
            type="button"
            onClick={props.onConnect}
            disabled={Boolean(props.busy) || blocked || (props.signatureType !== 0 && !props.funderAddress)}
          >
            <WalletCards aria-hidden="true" />
            {props.busy === "wallet" ? "Waiting for wallet…" : "Connect and verify wallet"}
          </button>
          {blocked && <p className="restriction-note">Research is available, but this location cannot create a trading session.</p>}
        </>
      )}
    </section>
  );
}

export function ProposalTicket(props: {
  draft: ProposalDraft | null;
  onChange: (proposal: TradingActionProposal) => void;
  onClear: () => void;
  onExecute: () => void;
  reviewed: boolean;
  onReviewed: (value: boolean) => void;
  sessionReady: boolean;
  tradeAllowed: boolean;
  submitting: boolean;
  pendingSignedOrder: boolean;
}) {
  const { draft } = props;
  const [fieldsValid, setFieldsValid] = useState(true);
  const [cashExposureLimit, setCashExposureLimit] = useState(() => loadCashExposureLimit());
  const [riskConfirmed, setRiskConfirmed] = useState(false);
  // Any proposal edit changes the exact data the wallet will sign, so it must
  // invalidate the previous worst-case acknowledgement as well.
  const riskKey = draft?.proposal.action === "create" ? JSON.stringify(draft.proposal) : "";
  useEffect(() => setRiskConfirmed(false), [riskKey]);
  if (!draft) {
    return (
      <section className="ticket ticket-empty" aria-labelledby="ticket-heading">
        <span className="ticket-tear" aria-hidden="true" />
        <Sparkles aria-hidden="true" />
        <h2 id="ticket-heading">No action drafted</h2>
        <p>Ask the agent to prepare an order or cancellation. Nothing here simulates a fill, balance, or P&amp;L.</p>
      </section>
    );
  }

  const expired = new Date(draft.expiresAt).getTime() <= Date.now();
  const proposal = draft.proposal;
  const risk = proposal.action === "create" ? orderRiskSummary(proposal) : null;
  const parsedLimit = parseCashExposureLimit(cashExposureLimit);
  const limitExceeded = risk !== null && risk.cashExposure !== null && (parsedLimit === null || risk.cashExposure > parsedLimit);
  const geographyAllowsAction = proposal.action === "cancel" || props.tradeAllowed || props.pendingSignedOrder;
  const timeAllowsAction = !expired || props.pendingSignedOrder;
  const canExecute = props.reviewed && props.sessionReady && geographyAllowsAction && timeAllowsAction && !props.submitting && (proposal.action === "cancel" || (fieldsValid && !limitExceeded && riskConfirmed));
  const actionLabel = props.pendingSignedOrder
    ? "Reconcile signed order"
    : proposal.action === "cancel"
      ? "Confirm cancellation"
      : "Sign and place real order";

  return (
    <section className="ticket ticket-live" aria-labelledby="ticket-heading">
      <span className="ticket-tear" aria-hidden="true" />
      <div className="ticket-header">
        <div>
          <span className="eyebrow">Unsigned action · draft</span>
          <h2 id="ticket-heading">{proposal.action === "create" ? proposal.marketQuestion : "Cancel orders"}</h2>
        </div>
        <button className="icon-button" type="button" onClick={props.onClear} aria-label="Reject draft"><X /></button>
      </div>

      {proposal.action === "create" ? (
        <OrderEditor proposal={proposal} onChange={props.onChange} onValidityChange={setFieldsValid} />
      ) : (
        <CancellationSummary selector={proposal.selector} rationale={proposal.rationale} />
      )}

      {risk ? (
        <OrderGuardrails
          limit={cashExposureLimit}
          limitExceeded={limitExceeded}
          onLimitChange={setCashExposureLimit}
          risk={risk}
        />
      ) : null}

      <div className="ticket-expiry">
        <Clock3 aria-hidden="true" /> {expired && !props.pendingSignedOrder ? "Expired — refresh before signing" : props.pendingSignedOrder ? "Exact signed intent retained for reconciliation" : `Draft expires ${formatTime(draft.expiresAt)}`}
      </div>
      <label className="review-check">
        <input
          type="checkbox"
          checked={props.reviewed}
          onChange={(event) => props.onReviewed(event.target.checked)}
        />
        <span>I reviewed the exact market, side, quantity, price protection, and expiry.</span>
      </label>
      {risk ? (
        <label className="review-check risk-confirmation">
          <input type="checkbox" checked={riskConfirmed} onChange={(event) => setRiskConfirmed(event.target.checked)} />
          <span>I understand the worst case: {risk.worstCase}</span>
        </label>
      ) : null}
      <button className="button button-primary button-wide" type="button" onClick={props.onExecute} disabled={!canExecute}>
        {props.submitting ? "Waiting for wallet…" : actionLabel} <ArrowRight aria-hidden="true" />
      </button>
      {proposal.action === "create" && !fieldsValid && <p className="restriction-note">Fix the highlighted fields — this exact text would be signed.</p>}
      {limitExceeded && <p className="restriction-note" role="alert">This order exceeds the browser cash guard. Raise the guard deliberately or reduce the buy amount before signing.</p>}
      {!props.sessionReady && <p className="restriction-note">Connect and verify the wallet before signing.</p>}
      {!props.tradeAllowed && proposal.action === "create" && !props.pendingSignedOrder && <p className="restriction-note">Real orders are disabled until geographic eligibility is verified.</p>}
      {props.pendingSignedOrder && <p className="reconcile-note">The browser retained this exact signed intent after an uncertain response. Reconcile checks the same order hash; it does not create a replacement order.</p>}
      <p className="ticket-warning">This draft is neither a paper order nor a submitted order. A new order exists only if your wallet signs the exact intent and the gateway accepts it.</p>
    </section>
  );
}

function OrderGuardrails(props: {
  limit: string;
  limitExceeded: boolean;
  onLimitChange: (value: string) => void;
  risk: ReturnType<typeof orderRiskSummary>;
}) {
  return (
    <section className={`order-guardrails ${props.limitExceeded ? "order-guardrails-blocked" : ""}`} aria-label="Order risk checks" aria-live="polite">
      <div className="order-guardrails-heading"><ShieldCheck aria-hidden="true" /><strong>Order risk checks</strong><span>{props.risk.stale ? "Stale data" : "Current draft"}</span></div>
      <dl>
        <div><dt>Worst case</dt><dd>{props.risk.maximumExposure}</dd></div>
        <div><dt>Price protection</dt><dd>{props.risk.priceProtection}</dd></div>
      </dl>
      {props.risk.cashExposure !== null ? (
        <label className="cash-guard-field">
          <span>Browser cash guard (USDC)</span>
          <input aria-label="Browser cash guard (USDC)" inputMode="decimal" value={props.limit} onChange={(event) => {
            props.onLimitChange(event.target.value);
            saveCashExposureLimit(event.target.value);
          }} />
        </label>
      ) : <p className="order-guardrails-note">This sell order exposes inventory, not new cash. Confirm that the shares are available before signing.</p>}
      {props.risk.stale ? <p className="order-guardrails-warning"><Clock3 aria-hidden="true" /> Market observation is over two minutes old. Refresh the research before signing.</p> : null}
      <p className="order-guardrails-note">Browser guard only — the venue and your wallet remain the source of truth.</p>
    </section>
  );
}

function loadCashExposureLimit(): string {
  try {
    return window.localStorage.getItem("polytrade.cash-exposure-limit") ?? String(DEFAULT_CASH_EXPOSURE_LIMIT);
  } catch {
    return String(DEFAULT_CASH_EXPOSURE_LIMIT);
  }
}

function saveCashExposureLimit(value: string): void {
  try {
    window.localStorage.setItem("polytrade.cash-exposure-limit", value);
  } catch {
    // A private browsing policy should not prevent the local guard from working for this page.
  }
}

type EditableField = "price" | "size" | "amount" | "limitPrice" | "expiration";
const EDITABLE_KEYS = ["price", "size", "amount", "limitPrice", "expiration"] as const;

function sameEditableDraft(a: TradingActionProposal, b: TradingActionProposal): boolean {
  if (a.action !== b.action) return false;
  if (a.action === "cancel" || b.action === "cancel") return a === b;
  const stableKeys = ["execution", "tokenId", "marketId", "marketQuestion", "outcome", "side", "rationale", "observedAt", "postOnly"] as const;
  for (const key of stableKeys) {
    if (a[key] !== b[key]) return false;
  }
  for (const key of EDITABLE_KEYS) {
    if (String((a as Record<string, unknown>)[key] ?? "") !== String((b as Record<string, unknown>)[key] ?? "")) return false;
  }
  return true;
}

function OrderEditor({ proposal, onChange, onValidityChange }: {
  proposal: CreateOrderProposal;
  onChange: (proposal: TradingActionProposal) => void;
  onValidityChange?: (valid: boolean) => void;
}) {
  // Numeric fields keep their in-progress text locally; the shared proposal only
  // advances while the whole edited draft still parses, so intermediate states
  // ("0.", trailing zeros, a cleared field) never snap back or silently compose
  // a different number than the one on screen.
  const [rawDrafts, setRawDrafts] = useState<Partial<Record<EditableField, string>>>({});
  const lastCommitted = useRef<TradingActionProposal>(proposal);

  useEffect(() => {
    if (sameEditableDraft(proposal, lastCommitted.current)) {
      lastCommitted.current = proposal;
      return;
    }
    lastCommitted.current = proposal;
    setRawDrafts({});
  }, [proposal]);

  const update = (patch: Record<string, unknown>) => {
    const candidate = { ...proposal, ...patch } as TradingActionProposal;
    const candidateParsed = tradingActionProposalSchema.safeParse(candidate);
    if (candidateParsed.success) lastCommitted.current = candidateParsed.data;
    onChange(candidate);
  };

  const rawValue = (field: EditableField, fallback: string) => rawDrafts[field] ?? fallback;
  const expiryFallback = () => ((proposal as { expiration?: number }).expiration === undefined ? "" : String((proposal as { expiration?: number }).expiration));
  const rawProposal = { ...proposal } as Record<string, unknown>;
  if ("price" in proposal) {
    rawProposal.price = rawValue("price", proposal.price);
    rawProposal.size = rawValue("size", proposal.size);
    if (proposal.execution === "GTD") rawProposal.expiration = Number(rawValue("expiration", expiryFallback()));
  } else {
    rawProposal.amount = rawValue("amount", proposal.amount);
    rawProposal.limitPrice = rawValue("limitPrice", proposal.limitPrice);
  }

  const parsed = tradingActionProposalSchema.safeParse(rawProposal);
  const fieldsValid = parsed.success;
  const fieldErrors = new Map<string, string>();
  if (!parsed.success) {
    for (const issue of parsed.error.issues) {
      const key = String(issue.path[0] ?? "");
      if (!fieldErrors.has(key)) fieldErrors.set(key, issue.message);
    }
  }

  useEffect(() => {
    onValidityChange?.(fieldsValid);
  }, [fieldsValid, onValidityChange]);

  const edit = (field: EditableField, raw: string) => {
    setRawDrafts((current) => ({ ...current, [field]: raw }));
    const candidate = { ...rawProposal, [field]: field === "expiration" ? Number(raw) : raw } as Record<string, unknown>;
    const candidateParsed = tradingActionProposalSchema.safeParse(candidate);
    if (candidateParsed.success) {
      lastCommitted.current = candidateParsed.data;
      onChange(candidateParsed.data);
    }
  };

  const exposure = fieldsValid ? maximumExposure(parsed.success ? parsed.data as CreateOrderProposal : proposal) : "—";

  return (
    <div className="order-fields">
      <div className="ticket-market-line">
        <span className={`side-flag side-${proposal.side.toLowerCase()}`}>{proposal.side}</span>
        <strong>{proposal.outcome}</strong>
        <small>{proposal.execution}</small>
      </div>
      <div className="field-grid">
        <TicketField label="Side">
          <select value={proposal.side} onChange={(event) => update({ side: event.target.value })}>
            <option value="BUY">Buy</option><option value="SELL">Sell</option>
          </select>
        </TicketField>
        <TicketField label="Time in force"><output>{proposal.execution}</output></TicketField>
        {"price" in proposal ? (
          <>
            <TicketField label="Limit price" error={fieldErrors.get("price")}>
              <input inputMode="decimal" value={rawValue("price", proposal.price)} onChange={(event) => edit("price", event.target.value)} />
            </TicketField>
            <TicketField label="Size (shares)" error={fieldErrors.get("size")}>
              <input inputMode="decimal" value={rawValue("size", proposal.size)} onChange={(event) => edit("size", event.target.value)} />
            </TicketField>
            {proposal.execution === "GTD" && (
              <TicketField label="Expires (Unix seconds)" error={fieldErrors.get("expiration")}>
                <input inputMode="numeric" value={rawValue("expiration", proposal.expiration === undefined ? "" : String(proposal.expiration))} onChange={(event) => edit("expiration", event.target.value)} />
              </TicketField>
            )}
            <TicketField label="Maximum exposure"><output>{exposure}</output></TicketField>
            <label className="post-only-field">
              <input type="checkbox" checked={proposal.postOnly} onChange={(event) => update({ postOnly: event.target.checked })} />
              Post only
            </label>
          </>
        ) : (
          <>
            <TicketField label={proposal.side === "BUY" ? "Amount (USDC)" : "Amount (shares)"} error={fieldErrors.get("amount")}>
              <input inputMode="decimal" value={rawValue("amount", proposal.amount)} onChange={(event) => edit("amount", event.target.value)} />
            </TicketField>
            <TicketField label="Worst accepted price" error={fieldErrors.get("limitPrice")}>
              <input inputMode="decimal" value={rawValue("limitPrice", proposal.limitPrice)} onChange={(event) => edit("limitPrice", event.target.value)} />
            </TicketField>
            <TicketField label="Maximum exposure"><output>{exposure}</output></TicketField>
          </>
        )}
      </div>
      <div className="proposal-rationale"><span>Agent rationale</span><p>{proposal.rationale || "No rationale supplied."}</p></div>
      <div className="proposal-source"><span>Market ID</span><code>{proposal.marketId}</code><span>Observed</span><time>{formatDateTime(proposal.observedAt)}</time></div>
    </div>
  );
}

function TicketField({ label, children, error }: { label: string; children: ReactNode; error?: string }) {
  return (
    <label className="ticket-field">
      <span>{label}</span>
      {children}
      {error && <small className="ticket-field-error" role="alert">{error}</small>}
    </label>
  );
}

function CancellationSummary({ selector, rationale }: { selector: CancellationSelector; rationale: string }) {
  const target = selector.kind === "all"
    ? "Every open order"
    : selector.kind === "order"
      ? `Order ${selector.orderId}`
      : `Market ${selector.marketId}${selector.tokenId ? ` · token ${selector.tokenId}` : ""}`;
  return <div className="cancel-summary"><CircleAlert aria-hidden="true" /><div><strong>{target}</strong><p>{rationale || "No rationale supplied."}</p></div></div>;
}

function OpenOrders({ orders, onCancel, disabled }: { orders: unknown[]; onCancel: (selector: CancellationSelector) => void; disabled: boolean }) {
  if (!orders.length) return <p className="empty-account">No open orders.</p>;
  return (
    <div className="open-orders">
      <div className="subheading"><span>Open orders</span><small>{orders.length}</small></div>
      {orders.slice(0, 5).map((order, index) => {
        const record = asRecord(order);
        const id = stringField(record, "id", "orderID", "order_id");
        const side = stringField(record, "side") || "ORDER";
        const price = stringField(record, "price") || "—";
        const size = stringField(record, "original_size", "size") || "—";
        return (
          <div className="open-order" key={id || index}>
            <span><strong>{side}</strong><small>{size} @ {price}</small></span>
            {id && <button type="button" onClick={() => onCancel({ kind: "order", orderId: id })} disabled={disabled}>Cancel</button>}
          </div>
        );
      })}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><strong>{value}</strong><span>{label}</span></div>;
}

export function compactAddress(value: string): string {
  return `${value.slice(0, 6)}…${value.slice(-4)}`;
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function errorMessage(caught: unknown): string {
  return caught instanceof Error ? caught.message : "The action could not be completed";
}

export function orderResultMessage(value: unknown): string {
  const record = asRecord(value);
  const success = record.success === true;
  const orderId = stringField(record, "orderID", "orderId", "id");
  if (!success) return "Polymarket rejected the order. Review the response and current market state.";
  return orderId ? `Order accepted by Polymarket · ${orderId}` : "Order accepted by Polymarket.";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function stringField(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string") return value;
    if (typeof value === "number") return String(value);
  }
  return "";
}
