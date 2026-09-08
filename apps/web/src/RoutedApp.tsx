import {
  Activity,
  ArrowRight,
  BarChart3,
  Bell,
  BriefcaseBusiness,
  Check,
  ChevronRight,
  CircleAlert,
  CircleDot,
  History,
  LogOut,
  Menu,
  MessageSquare,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Trash2,
  WalletCards,
  X,
  Zap,
} from "lucide-react";
import {
  createContext,
  type FormEvent,
  type MutableRefObject,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Navigate,
  NavLink,
  Link,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  createOrderProposalSchema,
  backtestConfigSchema,
  defaultBreakoutBacktestConfig,
  defaultMeanReversionBacktestConfig,
  defaultMomentumBacktestConfig,
  isBacktestEligibleMarket,
  tradingActionProposalSchema,
  type AccountOverview,
  type BacktestConfig,
  type BacktestStrategy,
  type CancellationSelector,
  type MarketSearchMarket,
  type OrderIntentResponse,
  strategyTemplateById,
  type StrategyTemplate,
  type TradingActionProposal,
  type WalletSessionStatus,
} from "@polytrade/contracts";
import type { Hex } from "viem";

import {
  compactAddress,
  errorMessage,
  orderResultMessage,
  ProposalTicket,
  type ProposalDraft,
} from "./App";
import { GatewayClient, GatewayError } from "./api";
import {
  AgentApiError,
  createAgentThread,
  deleteAgentThread,
  getAgentThreadItems,
  listAgentThreads,
  runAgentTurn,
  type AgentBacktestReference,
  type AgentThreadSummary,
} from "./agent";
import { useAuthentication } from "./auth";
import { BacktestClient } from "./backtest";
import { BacktestsWorkspace } from "./Backtests";
import { AlertsSettings } from "./Alerts";
import { checkBrowserEligibility, type Eligibility } from "./eligibility";
import { env } from "./env";
import { MarkdownMessage } from "./MarkdownMessage";
import { MarketFocus } from "./MarketFocus";
import { clearMarketFocus, loadMarketFocus, researchPrompt, saveMarketFocus } from "./market-focus";
import { PaperWorkspace } from "./Paper";
import { connectWallet, signTypedPayload, type ConnectedWallet } from "./wallet";

interface DeskMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
}

interface ChatState {
  messages: DeskMessage[];
  loaded: boolean;
  loading: boolean;
  loadError?: string | null;
  proposalDraft: ProposalDraft | null;
  backtests: AgentBacktestReference[];
}

interface PendingOrder {
  createKey: string;
  intent?: OrderIntentResponse;
  signature?: Hex;
}

type BusyAction = "wallet" | "submit" | "account" | "cancel" | null;

interface WorkspaceContextValue {
  account: AccountOverview | null;
  activeStreamHasText: boolean;
  activeStreamThreadId: string | null;
  authenticationControl: ReactNode;
  backtests: BacktestClient;
  busy: BusyAction;
  chatStates: Record<string, ChatState>;
  clearProposal: (threadId: string) => void;
  connectAndVerify: () => Promise<void>;
  attachLocalSigner: () => Promise<void>;
  deleteThread: (threadId: string) => Promise<void>;
  disconnectWallet: () => Promise<void>;
  eligibility: Eligibility | null;
  error: string | null;
  executeProposal: (threadId: string) => Promise<void>;
  funderAddress: string;
  gateway: GatewayClient;
  lastProposalThreadId: string | null;
  loadThread: (threadId: string) => Promise<void>;
  localWallet: ConnectedWallet | null;
  notice: string | null;
  pendingOrders: Record<string, PendingOrder | undefined>;
  refreshAccount: () => Promise<void>;
  refreshEligibility: () => Promise<void>;
  refreshThreads: () => Promise<AgentThreadSummary[]>;
  reviewed: Record<string, boolean | undefined>;
  session: WalletSessionStatus | null;
  setFunderAddress: (value: string) => void;
  setMessage: (value: string | null, kind?: "error" | "notice") => void;
  setReviewed: (threadId: string, value: boolean) => void;
  setSignatureType: (value: 0 | 1 | 2 | 3) => void;
  signatureType: 0 | 1 | 2 | 3;
  submitMessage: (threadId: string | undefined, text: string) => Promise<boolean>;
  threads: AgentThreadSummary[];
  threadsLoaded: boolean;
  tradeAllowed: boolean;
  updateProposal: (threadId: string, proposal: TradingActionProposal) => void;
  cancelOpenOrder: (selector: CancellationSelector) => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

const STARTER_QUESTIONS = [
  "Backtest mean reversion on a resolved election market",
  "Find a liquid active market for a paper-trading setup",
  "Research the leading Fed market and summarize its order book",
];

const emptyChat = (): ChatState => ({
  messages: [],
  loaded: false,
  loading: false,
  proposalDraft: null,
  backtests: [],
});

/**
 * Wallet type and funder are the only reconnect inputs, so remembering them
 * locally turns an expired session into a single signature prompt instead of
 * the full connect walkthrough. The wallet address itself is never stored.
 */
const WALLET_PREFS_KEY = "polytrade.wallet-session-prefs";

interface WalletPrefs {
  signatureType: 0 | 1 | 2 | 3;
  funderAddress: string;
}

function loadWalletPrefs(): WalletPrefs {
  try {
    const raw = window.localStorage.getItem(WALLET_PREFS_KEY);
    if (!raw) return { signatureType: 0, funderAddress: "" };
    const parsed = JSON.parse(raw) as Partial<WalletPrefs>;
    return {
      signatureType: parsed.signatureType === 1 || parsed.signatureType === 2 || parsed.signatureType === 3 ? parsed.signatureType : 0,
      funderAddress: typeof parsed.funderAddress === "string" ? parsed.funderAddress : "",
    };
  } catch {
    return { signatureType: 0, funderAddress: "" };
  }
}

function saveWalletPrefs(prefs: WalletPrefs): void {
  try {
    window.localStorage.setItem(WALLET_PREFS_KEY, JSON.stringify(prefs));
  } catch {
    // Storage can be unavailable (private mode); reconnect just loses its prefill.
  }
}

export default function RoutedApp() {
  return (
    <WorkspaceProvider>
      <ApplicationShell />
    </WorkspaceProvider>
  );
}

function WorkspaceProvider({ children }: { children: ReactNode }) {
  const authentication = useAuthentication();
  const navigate = useNavigate();
  const location = useLocation();
  const gateway = useMemo(
    () => new GatewayClient(env.VITE_API_URL, authentication.getToken),
    [authentication.getToken],
  );
  const backtests = useMemo(
    () => new BacktestClient(env.VITE_API_URL, authentication.getToken),
    [authentication.getToken],
  );
  const [threads, setThreads] = useState<AgentThreadSummary[]>([]);
  const [threadsLoaded, setThreadsLoaded] = useState(false);
  const [chatStates, setChatStates] = useState<Record<string, ChatState>>({});
  const [activeStreamHasText, setActiveStreamHasText] = useState(false);
  const [activeStreamThreadId, setActiveStreamThreadId] = useState<string | null>(null);
  const [lastProposalThreadId, setLastProposalThreadId] = useState<string | null>(null);
  const [reviewed, setReviewedState] = useState<Record<string, boolean | undefined>>({});
  const [pendingOrders, setPendingOrders] = useState<Record<string, PendingOrder | undefined>>({});
  const [session, setSession] = useState<WalletSessionStatus | null>(null);
  const [localWallet, setLocalWallet] = useState<ConnectedWallet | null>(null);
  const [walletPrefs] = useState(loadWalletPrefs);
  const [signatureType, setSignatureType] = useState<0 | 1 | 2 | 3>(walletPrefs.signatureType);
  const [funderAddress, setFunderAddress] = useState(walletPrefs.funderAddress);
  const [eligibility, setEligibility] = useState<Eligibility | null>(null);
  const [account, setAccount] = useState<AccountOverview | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const locationRef = useRef(location.pathname);
  const loadedThreadsRef = useRef(new Set<string>());
  const loadingThreadsRef = useRef(new Set<string>());

  useEffect(() => {
    locationRef.current = location.pathname;
  }, [location.pathname]);

  const setMessage = useCallback((value: string | null, kind: "error" | "notice" = "error") => {
    if (value === null) {
      setError(null);
      setNotice(null);
      return;
    }
    if (kind === "error") {
      setError(value);
      if (value) setNotice(null);
    } else {
      setNotice(value);
      if (value) setError(null);
    }
  }, []);

  const refreshThreads = useCallback(async () => {
    try {
      const next = await listAgentThreads(env.VITE_API_URL, authentication.getToken);
      setThreads(next);
      return next;
    } catch (caught) {
      setMessage(errorMessage(caught));
      return [];
    } finally {
      setThreadsLoaded(true);
    }
  }, [authentication.getToken, setMessage]);

  const refreshEligibility = useCallback(async () => {
    setEligibility(await checkBrowserEligibility());
  }, []);

  // The gateway expires an idle wallet session server-side; detect that once
  // and say so, instead of silently dropping the session on a 404.
  const sessionExpiredRef = useRef(false);

  const handleMissingSession = useCallback(() => {
    setSession(null);
    setLocalWallet(null);
    setAccount(null);
    if (!sessionExpiredRef.current) {
      sessionExpiredRef.current = true;
      setMessage("Wallet session expired or was revoked. Reconnecting takes one signature — your wallet type is remembered.", "notice");
    }
  }, [setMessage]);

  const refreshAccount = useCallback(async () => {
    if (!session) return;
    setBusy((current) => current ?? "account");
    try {
      setAccount(await gateway.accountOverview());
    } catch (caught) {
      if (caught instanceof GatewayError && caught.status === 404) {
        handleMissingSession();
      } else {
        setMessage(errorMessage(caught));
      }
    } finally {
      setBusy((current) => current === "account" ? null : current);
    }
  }, [gateway, session, setMessage, handleMissingSession]);

  const refreshSession = useCallback(async () => {
    try {
      const fresh = await gateway.currentWalletSession();
      sessionExpiredRef.current = false;
      setSession((current) => (
        current && current.idleExpiresAt === fresh.idleExpiresAt && current.expiresAt === fresh.expiresAt ? current : fresh
      ));
    } catch (caught) {
      if (caught instanceof GatewayError && caught.status === 404) {
        handleMissingSession();
      } else {
        setMessage(errorMessage(caught));
      }
    }
  }, [gateway, handleMissingSession, setMessage]);

  useEffect(() => {
    void refreshThreads();
    void refreshEligibility();
    let cancelled = false;
    void gateway.currentWalletSession()
      .then(async (restored) => {
        if (cancelled) return;
        sessionExpiredRef.current = false;
        setSession(restored);
        try {
          const nextAccount = await gateway.accountOverview();
          if (!cancelled) setAccount(nextAccount);
        } catch (caught) {
          if (cancelled) return;
          if (caught instanceof GatewayError && caught.status === 404) {
            handleMissingSession();
          } else {
            setMessage(errorMessage(caught));
          }
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled && !(caught instanceof GatewayError && caught.status === 404)) {
          setMessage(errorMessage(caught));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [gateway, refreshEligibility, refreshThreads, setMessage, handleMissingSession]);

  useEffect(() => {
    if (!session) return;
    const poll = () => {
      if (document.visibilityState !== "visible") return;
      void (async () => {
        // The account call slides the idle window; the session read then syncs
        // the fresh expiry so countdowns and the Settings page stay truthful.
        await refreshAccount();
        await refreshSession();
      })();
    };
    const interval = window.setInterval(poll, 30_000);
    document.addEventListener("visibilitychange", poll);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [refreshAccount, refreshSession, session]);

  const loadThread = useCallback(async (threadId: string) => {
    if (loadedThreadsRef.current.has(threadId) || loadingThreadsRef.current.has(threadId)) return;
    loadingThreadsRef.current.add(threadId);
    setChatStates((current) => {
      const existing = current[threadId] ?? emptyChat();
      return { ...current, [threadId]: { ...existing, loading: true, loadError: null } };
    });
    try {
      const items = await getAgentThreadItems(env.VITE_API_URL, authentication.getToken, threadId);
      const messages = items
        .filter((item) => item.kind === "message")
        .map((item) => ({ id: item.id, role: item.role, text: item.text } satisfies DeskMessage));
      const proposal = [...items].reverse().find(
        (item) => item.kind === "proposal" && Date.parse(item.expiresAt) > Date.now(),
      );
      const backtests = items
        .filter((item) => item.kind === "backtest")
        .map((item) => item.backtest);
      setChatStates((current) => ({
        ...current,
        [threadId]: {
          messages,
          loaded: true,
          loading: false,
          proposalDraft: proposal?.kind === "proposal"
            ? { proposal: proposal.proposal, expiresAt: proposal.expiresAt }
            : null,
          backtests,
        },
      }));
      loadedThreadsRef.current.add(threadId);
      if (proposal?.kind === "proposal") setLastProposalThreadId(threadId);
    } catch (caught) {
      if (caught instanceof AgentApiError && caught.status === 404) {
        setChatStates((current) => {
          const existing = current[threadId] ?? emptyChat();
          return { ...current, [threadId]: { ...existing, loading: false, loaded: true } };
        });
        const next = await refreshThreads();
        if (locationRef.current === `/chat/${threadId}`) {
          navigate(next[0] ? `/chat/${next[0].threadId}` : "/chat/new", { replace: true });
        }
      } else {
        // Keep showing the conversation as unreachable instead of an empty,
        // "wiped" chat, and leave it retryable.
        setChatStates((current) => {
          const existing = current[threadId] ?? emptyChat();
          return {
            ...current,
            [threadId]: { ...existing, loading: false, loaded: false, loadError: errorMessage(caught) },
          };
        });
        setMessage(errorMessage(caught));
      }
    } finally {
      loadingThreadsRef.current.delete(threadId);
    }
  }, [authentication.getToken, navigate, refreshThreads, setMessage]);

  const submitMessage = useCallback(async (requestedThreadId: string | undefined, text: string): Promise<boolean> => {
    const message = text.trim();
    if (!message || activeStreamThreadId) return false;
    setError(null);
    setNotice(null);
    setActiveStreamHasText(false);
    setActiveStreamThreadId(requestedThreadId ?? "new");
    let threadId = requestedThreadId;
    // Mirrors the live thread id for the catch below: `targetThreadId` is
    // scoped inside the try so the stream handlers capture a plain `string`,
    // and `onThreadId` is the only place it can change mid-stream.
    let streamThreadId: string | undefined;
    try {
      if (!threadId) {
        try {
          threadId = await createAgentThread(env.VITE_API_URL, authentication.getToken);
        } catch (caught) {
          // The message never reached any thread; the caller restores the
          // composer text so the user is not forced to retype it.
          setMessage(errorMessage(caught));
          await refreshThreads();
          return false;
        }
        navigate(`/chat/${threadId}`, { replace: true });
      }
      // Declared after the null check so the stream handlers below capture a
      // `string`, not the pre-narrowing `string | undefined`.
      let targetThreadId: string = threadId;
      streamThreadId = targetThreadId;
      loadedThreadsRef.current.add(targetThreadId);
      setActiveStreamThreadId(targetThreadId);
      setChatStates((current) => {
        const state = current[targetThreadId] ?? emptyChat();
        return {
          ...current,
          [targetThreadId]: {
            ...state,
            loaded: true,
            loading: false,
            messages: [...state.messages, { id: `user-${crypto.randomUUID()}`, role: "user", text: message }],
          },
        };
      });
      await runAgentTurn({
        apiUrl: env.VITE_API_URL,
        getToken: authentication.getToken,
        threadId: targetThreadId,
        text: message,
        handlers: {
          onThreadId: (replacement) => {
            if (replacement === targetThreadId) return;
            const previous = targetThreadId;
            targetThreadId = replacement;
            streamThreadId = replacement;
            loadedThreadsRef.current.delete(previous);
            loadedThreadsRef.current.add(replacement);
            setActiveStreamThreadId(replacement);
            setChatStates((current) => {
              const moved = current[previous] ?? emptyChat();
              const { [previous]: _removed, ...rest } = current;
              return { ...rest, [replacement]: moved };
            });
            navigate(`/chat/${replacement}`, { replace: true });
          },
          onMessageStart: (messageId) => {
            setChatStates((current) => {
              const state = current[targetThreadId] ?? emptyChat();
              if (state.messages.some((item) => item.id === messageId)) return current;
              return {
                ...current,
                [targetThreadId]: {
                  ...state,
                  messages: [...state.messages, { id: messageId, role: "assistant", text: "" }],
                },
              };
            });
          },
          onMessageText: (messageId, value) => {
            if (value.trim()) setActiveStreamHasText(true);
            setChatStates((current) => {
              const state = current[targetThreadId] ?? emptyChat();
              return {
                ...current,
                [targetThreadId]: {
                  ...state,
                  messages: state.messages.map((item) => item.id === messageId ? { ...item, text: value } : item),
                },
              };
            });
          },
          onProposal: (proposal, expiresAt) => {
            setChatStates((current) => {
              const state = current[targetThreadId] ?? emptyChat();
              return { ...current, [targetThreadId]: { ...state, proposalDraft: { proposal, expiresAt } } };
            });
            setPendingOrders((current) => ({ ...current, [targetThreadId]: undefined }));
            setReviewedState((current) => ({ ...current, [targetThreadId]: false }));
            setLastProposalThreadId(targetThreadId);
          },
          onBacktest: (backtestReference) => {
            setChatStates((current) => {
              const state = current[targetThreadId] ?? emptyChat();
              const backtests = state.backtests.some(
                (item) => item.runId === backtestReference.runId,
              )
                ? state.backtests
                : [...state.backtests, backtestReference];
              return { ...current, [targetThreadId]: { ...state, backtests } };
            });
            setMessage("Backtest queued. Progress is available in activity and Backtests.", "notice");
          },
        },
      });
      return true;
    } catch (caught) {
      setMessage(errorMessage(caught));
      // The turn died mid-stream; let the next visit reload the thread from
      // the server instead of trusting the interrupted local copy forever.
      if (streamThreadId !== undefined) loadedThreadsRef.current.delete(streamThreadId);
      return true;
    } finally {
      setActiveStreamThreadId(null);
      setActiveStreamHasText(false);
      await refreshThreads();
    }
  }, [activeStreamThreadId, authentication.getToken, navigate, refreshThreads, setMessage]);

  const deleteThread = useCallback(async (threadId: string) => {
    if (activeStreamThreadId === threadId) {
      setMessage("Wait for this thread's active response before deleting it.");
      return;
    }
    try {
      await deleteAgentThread(env.VITE_API_URL, authentication.getToken, threadId);
      setChatStates((current) => {
        const { [threadId]: _removed, ...rest } = current;
        return rest;
      });
      loadedThreadsRef.current.delete(threadId);
      loadingThreadsRef.current.delete(threadId);
      const next = await refreshThreads();
      if (locationRef.current === `/chat/${threadId}`) {
        navigate(next[0] ? `/chat/${next[0].threadId}` : "/chat/new", { replace: true });
      }
      setMessage("Chat deleted.", "notice");
    } catch (caught) {
      setMessage(errorMessage(caught));
    }
  }, [activeStreamThreadId, authentication.getToken, navigate, refreshThreads, setMessage]);

  const connectAndVerify = useCallback(async () => {
    setBusy("wallet");
    setError(null);
    setNotice(null);
    try {
      const connected = await connectWallet();
      const challenge = await gateway.createChallenge({
        walletAddress: connected.address,
        signatureType,
        ...(signatureType === 0 ? {} : { funderAddress }),
      });
      const signature = await signTypedPayload(connected, challenge.typedData);
      const created = await gateway.createWalletSession(challenge.challengeId, signature);
      sessionExpiredRef.current = false;
      saveWalletPrefs({ signatureType, funderAddress: signatureType === 0 ? "" : funderAddress.trim() });
      setLocalWallet(connected);
      setSession(created);
      setAccount(await gateway.accountOverview());
      setMessage("Wallet verified and locally attached for signing.", "notice");
    } catch (caught) {
      setMessage(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }, [funderAddress, gateway, setMessage, signatureType]);

  const attachLocalSigner = useCallback(async () => {
    if (!session) return;
    setBusy("wallet");
    try {
      const connected = await connectWallet();
      if (connected.address.toLowerCase() !== session.walletAddress.toLowerCase()) {
        throw new Error(`Attach ${compactAddress(session.walletAddress)} to enable signing for this session.`);
      }
      setLocalWallet(connected);
      setMessage("Matching browser wallet attached. Signing is enabled for this tab.", "notice");
    } catch (caught) {
      setMessage(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }, [session, setMessage]);

  const disconnectWallet = useCallback(async () => {
    if (!session) return;
    setBusy("wallet");
    try {
      await gateway.revokeWalletSession(session.sessionId);
      setSession(null);
      setLocalWallet(null);
      setAccount(null);
      setPendingOrders({});
      setMessage("Wallet session disconnected.", "notice");
    } catch (caught) {
      setMessage(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }, [gateway, session, setMessage]);

  const updateProposal = useCallback((threadId: string, proposal: TradingActionProposal) => {
    const parsed = tradingActionProposalSchema.safeParse(proposal);
    if (!parsed.success) return;
    setChatStates((current) => {
      const state = current[threadId] ?? emptyChat();
      if (!state.proposalDraft) return current;
      return {
        ...current,
        [threadId]: { ...state, proposalDraft: { ...state.proposalDraft, proposal: parsed.data } },
      };
    });
    setPendingOrders((current) => ({ ...current, [threadId]: undefined }));
    setReviewedState((current) => ({ ...current, [threadId]: false }));
  }, []);

  const clearProposal = useCallback((threadId: string) => {
    setChatStates((current) => {
      const state = current[threadId] ?? emptyChat();
      return { ...current, [threadId]: { ...state, proposalDraft: null } };
    });
    setPendingOrders((current) => ({ ...current, [threadId]: undefined }));
    setReviewedState((current) => ({ ...current, [threadId]: false }));
    setLastProposalThreadId((current) => current === threadId ? null : current);
  }, []);

  const executeProposal = useCallback(async (threadId: string) => {
    const draft = chatStates[threadId]?.proposalDraft;
    const isReviewed = reviewed[threadId] === true;
    const signerMatches = Boolean(
      session && localWallet && localWallet.address.toLowerCase() === session.walletAddress.toLowerCase(),
    );
    if (!draft || !isReviewed || !session || !localWallet || !signerMatches) return;
    setBusy("submit");
    setError(null);
    setNotice(null);
    try {
      const prior = pendingOrders[threadId];
      if (!prior?.signature && Date.parse(draft.expiresAt) <= Date.now()) {
        throw new Error("This draft expired. Ask the agent to refresh the market data.");
      }
      if (draft.proposal.action === "cancel") {
        await gateway.cancel(session.sessionId, draft.proposal.selector);
        setMessage("Cancellation accepted. Account data will refresh with the final state.", "notice");
      } else {
        const proposal = createOrderProposalSchema.parse(draft.proposal);
        let pending = prior ?? { createKey: crypto.randomUUID() };
        setPendingOrders((current) => ({ ...current, [threadId]: pending }));
        const intent = pending.intent ?? await gateway.createIntent(session.sessionId, proposal, pending.createKey);
        pending = { ...pending, intent };
        setPendingOrders((current) => ({ ...current, [threadId]: pending }));
        const signature = pending.signature ?? await signTypedPayload(localWallet, intent.typedData);
        pending = { ...pending, signature };
        setPendingOrders((current) => ({ ...current, [threadId]: pending }));
        const result = await gateway.submitIntent(intent.intentId, signature, crypto.randomUUID());
        setMessage(orderResultMessage(result), "notice");
      }
      clearProposal(threadId);
      setAccount(await gateway.accountOverview());
    } catch (caught) {
      setMessage(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }, [chatStates, clearProposal, gateway, localWallet, pendingOrders, reviewed, session, setMessage]);

  const cancelOpenOrder = useCallback(async (selector: CancellationSelector) => {
    if (!session) return;
    setBusy("cancel");
    try {
      await gateway.cancel(session.sessionId, selector);
      setMessage("Cancellation accepted.", "notice");
      setAccount(await gateway.accountOverview());
    } catch (caught) {
      setMessage(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }, [gateway, session, setMessage]);

  const value = useMemo<WorkspaceContextValue>(() => ({
    account,
    activeStreamHasText,
    activeStreamThreadId,
    authenticationControl: authentication.accountControl,
    backtests,
    busy,
    chatStates,
    clearProposal,
    connectAndVerify,
    attachLocalSigner,
    deleteThread,
    disconnectWallet,
    eligibility,
    error,
    executeProposal,
    funderAddress,
    gateway,
    lastProposalThreadId,
    loadThread,
    localWallet,
    notice,
    pendingOrders,
    refreshAccount,
    refreshEligibility,
    refreshThreads,
    reviewed,
    session,
    setFunderAddress,
    setMessage,
    setReviewed: (threadId, value) => setReviewedState((current) => ({ ...current, [threadId]: value })),
    setSignatureType,
    signatureType,
    submitMessage,
    threads,
    threadsLoaded,
    tradeAllowed: eligibilityAllowsTrading(eligibility),
    updateProposal,
    cancelOpenOrder,
  }), [
    account, activeStreamHasText, activeStreamThreadId, authentication.accountControl, attachLocalSigner, backtests, busy,
    cancelOpenOrder, chatStates, clearProposal, connectAndVerify, deleteThread, disconnectWallet,
    eligibility, error, executeProposal, funderAddress, gateway, lastProposalThreadId, loadThread,
    localWallet, notice, pendingOrders, refreshAccount, refreshEligibility, refreshThreads, reviewed,
    session, setMessage, signatureType, submitMessage, threads, threadsLoaded, updateProposal,
  ]);

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("Workspace provider is unavailable");
  return value;
}

function ApplicationShell() {
  const workspace = useWorkspace();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => setMenuOpen(false), [location.pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [menuOpen]);

  return (
    <div className="app-shell">
      <header className="app-header">
        <Link className="app-brand" to="/chat" aria-label="PolyTrade chat">
          <span className="brand-glyph" aria-hidden="true"><CircleDot /></span>
          <span>PolyTrade</span>
        </Link>
        <nav className={`app-nav ${menuOpen ? "app-nav-open" : ""}`} aria-label="Primary navigation">
          <NavLink to="/chat" className={({ isActive }) => isActive ? "nav-active" : ""}><MessageSquare /> Chat</NavLink>
          <NavLink to="/trades" className={({ isActive }) => isActive ? "nav-active" : ""}><BriefcaseBusiness /> Trades</NavLink>
          <NavLink to="/paper" className={({ isActive }) => isActive ? "nav-active" : ""}><Activity /> Paper</NavLink>
          <NavLink to="/backtests" className={({ isActive }) => isActive ? "nav-active" : ""}><BarChart3 /> Backtests</NavLink>
        </nav>
        <div className="header-actions">
          <NavLink className={({ isActive }) => `header-settings ${isActive ? "nav-active" : ""}`} to="/settings">
            <Settings aria-hidden="true" /><span>Settings</span>
          </NavLink>
          <div className="profile-control" aria-label="Profile">{workspace.authenticationControl}</div>
          <button
            className="mobile-menu-button"
            type="button"
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((current) => !current)}
          >{menuOpen ? <X /> : <Menu />}</button>
        </div>
      </header>

      <Routes>
        <Route path="/" element={<Navigate replace to="/chat" />} />
        <Route path="/chat" element={<ChatIndex />} />
        <Route path="/chat/new" element={<ChatPage />} />
        <Route path="/chat/:threadId" element={<ChatThreadPage />} />
        <Route path="/trades" element={<TradesPage />} />
        <Route path="/paper" element={<PaperPage />} />
        <Route path="/backtests/new" element={<NewBacktestPage />} />
        <Route path="/backtests/:runId" element={<BacktestsPage />} />
        <Route path="/backtests" element={<BacktestsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>

      {(workspace.error || workspace.notice) && (
        <div className={`toast ${workspace.error ? "toast-error" : "toast-success"}`} role={workspace.error ? "alert" : "status"}>
          {workspace.error ? <CircleAlert aria-hidden="true" /> : <Check aria-hidden="true" />}
          <span>{workspace.error ?? workspace.notice}</span>
          <button type="button" aria-label="Dismiss message" onClick={() => workspace.setMessage(null)}><X /></button>
        </div>
      )}
    </div>
  );
}

function ChatIndex() {
  const { threads, threadsLoaded } = useWorkspace();
  if (!threadsLoaded) return <PageLoading label="Loading chats" />;
  return <Navigate replace to={threads[0] ? `/chat/${threads[0].threadId}` : "/chat/new"} />;
}

function ChatThreadPage() {
  const { threadId } = useParams();
  return threadId ? <ChatPage threadId={threadId} /> : <Navigate replace to="/chat/new" />;
}

function ChatPage({ threadId }: { threadId?: string }) {
  const workspace = useWorkspace();
  const location = useLocation();
  const [question, setQuestion] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [wideActivity, setWideActivity] = useState(() => window.matchMedia("(min-width: 1200px)").matches);
  const historyButtonRef = useRef<HTMLButtonElement>(null);
  const activityButtonRef = useRef<HTMLButtonElement>(null);
  const historyDrawerRef = useRef<HTMLElement>(null);
  const activityDrawerRef = useRef<HTMLElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const focusedMarket = loadMarketFocus(new URLSearchParams(location.search).get("focus"));
  const focusPromptRef = useRef<string | null>(null);
  const closeHistory = useCallback(() => {
    setHistoryOpen(false);
    historyButtonRef.current?.focus();
  }, []);
  const closeActivity = useCallback(() => {
    setActivityOpen(false);
    activityButtonRef.current?.focus();
  }, []);
  const state = threadId ? workspace.chatStates[threadId] ?? emptyChat() : { ...emptyChat(), loaded: true };

  useEffect(() => {
    if (threadId) void workspace.loadThread(threadId);
  }, [threadId, workspace.loadThread]);

  useEffect(() => {
    if (threadId || !focusedMarket || focusPromptRef.current === focusedMarket.conditionId) return;
    focusPromptRef.current = focusedMarket.conditionId;
    setQuestion((current) => current || researchPrompt(focusedMarket));
  }, [focusedMarket, threadId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [state.messages]);

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1200px)");
    const update = () => setWideActivity(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useDrawerKeyboard(historyOpen, closeHistory, historyDrawerRef, historyButtonRef);
  useDrawerKeyboard(activityOpen, closeActivity, activityDrawerRef, activityButtonRef);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const text = question.trim();
    if (!text || workspace.activeStreamThreadId) return;
    setQuestion("");
    void workspace.submitMessage(threadId, text).then((accepted) => {
      // A message that never reached any thread (thread creation failed) gives
      // the user their text back instead of silently discarding it.
      if (!accepted) setQuestion((current) => current || text);
    });
  };
  const currentStreaming = Boolean(threadId && workspace.activeStreamThreadId === threadId);
  const awaitingVisibleResponse = currentStreaming && !workspace.activeStreamHasText;

  return (
    <main className="chat-workspace">
      <button className="drawer-scrim" type="button" aria-label="Close drawer" hidden={!historyOpen && !activityOpen} onClick={() => { if (historyOpen) closeHistory(); if (activityOpen) closeActivity(); }} />
      <HistoryPane ref={historyDrawerRef} open={historyOpen} activeThreadId={threadId} onClose={closeHistory} />

      <section className="conversation-pane" aria-labelledby="conversation-heading">
        <div className="conversation-toolbar">
          <button ref={historyButtonRef} className="pane-toggle history-toggle" type="button" onClick={() => setHistoryOpen(true)} aria-expanded={historyOpen}>
            <History /> History
          </button>
          <div>
            <span className="eyebrow">Research workspace</span>
            <h1 id="conversation-heading">{threadId ? workspace.threads.find((item) => item.threadId === threadId)?.title ?? "Chat" : "New chat"}</h1>
          </div>
          <button ref={activityButtonRef} className="pane-toggle activity-toggle" type="button" onClick={() => setActivityOpen(true)} aria-expanded={activityOpen}>
            <Activity /> Activity
          </button>
        </div>

        <div className="message-scroll" aria-live="polite">
          {state.loading ? <PageLoading label="Loading conversation" compact /> : state.loadError ? (
            <div className="chat-load-error" role="alert">
              <CircleAlert aria-hidden="true" />
              <div>
                <strong>This conversation could not be loaded</strong>
                <p>{state.loadError}</p>
              </div>
              <button
                className="button button-quiet"
                type="button"
                onClick={() => { if (threadId) void workspace.loadThread(threadId); }}
              >
                Try again
              </button>
            </div>
          ) : state.messages.length === 0 ? (
            <div className="chat-empty">
              <span className="empty-orbit" aria-hidden="true"><Zap /></span>
              <h2>Research a market or prepare an action.</h2>
              <p>Ask about live Polymarket data, your account, or historical strategy backtests. Orders remain drafts until reviewed and signed.</p>
              <div className="starter-list" aria-label="Starter prompts">
                {STARTER_QUESTIONS.map((starter) => (
                  <button key={starter} type="button" onClick={() => setQuestion(starter)}>
                    <ArrowRight aria-hidden="true" /> <span>{starter}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="message-list">
              {state.messages.filter((message) => message.text.trim()).map((message) => (
                <article className={`chat-message chat-message-${message.role}`} key={message.id}>
                  <span className="message-label">{message.role === "user" ? "You" : "PolyTrade"}</span>
                  {message.role === "assistant"
                    ? <MarkdownMessage source={message.text} />
                    : <p>{message.text}</p>}
                </article>
              ))}
              {awaitingVisibleResponse && (
                <div className="stream-status" role="status">
                  <span className="stream-dot" aria-hidden="true" />
                  <span className="stream-dot" aria-hidden="true" />
                  <span className="stream-dot" aria-hidden="true" />
                  <span className="sr-only">Agent is working</span>
                </div>
              )}
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={submit}>
          <label className="sr-only" htmlFor="chat-question">Ask PolyTrade</label>
          <textarea
            id="chat-question"
            rows={2}
            maxLength={2_000}
            placeholder={workspace.activeStreamThreadId ? "One response is already running…" : "Ask about a market, account, order, or strategy backtest…"}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            disabled={Boolean(workspace.activeStreamThreadId)}
          />
          <div className="composer-footer">
            <span>{workspace.activeStreamThreadId && workspace.activeStreamThreadId !== threadId ? "Another chat is responding" : "Shift + Enter for a new line"}</span>
            <button className="send-button" type="submit" disabled={!question.trim() || Boolean(workspace.activeStreamThreadId)}>
              <Send aria-hidden="true" /><span>Send</span>
            </button>
          </div>
        </form>
      </section>

      <ActivityPane
        ref={activityDrawerRef}
        open={activityOpen}
        onClose={closeActivity}
        threadId={threadId}
        visible={wideActivity || activityOpen}
      />
    </main>
  );
}

const HistoryPane = function HistoryPane(props: {
  activeThreadId?: string;
  onClose: () => void;
  open: boolean;
  ref: MutableRefObject<HTMLElement | null>;
}) {
  const workspace = useWorkspace();
  const navigate = useNavigate();
  return (
    <aside ref={props.ref} className={`history-pane ${props.open ? "drawer-open" : ""}`} aria-label="Chat history" role={props.open ? "dialog" : undefined} aria-modal={props.open || undefined}>
      <div className="pane-heading">
        <div><span className="eyebrow">Workspace</span><h2>Chat history</h2></div>
        <button className="drawer-close" type="button" onClick={props.onClose} aria-label="Close chat history"><X /></button>
      </div>
      <Link className="new-chat-button" to="/chat/new" onClick={props.onClose}><Plus /> New chat</Link>
      <div className="thread-list">
        {workspace.threads.length === 0 && workspace.threadsLoaded ? (
          <p className="pane-empty">No saved conversations yet.</p>
        ) : workspace.threads.map((thread) => (
          <div className={`thread-row ${thread.threadId === props.activeThreadId ? "thread-active" : ""}`} key={thread.threadId}>
            <Link to={`/chat/${thread.threadId}`} onClick={props.onClose}>
              <span>{thread.title}</span>
              <small>{workspace.activeStreamThreadId === thread.threadId ? "Responding now" : relativeTime(thread.updatedAt)}</small>
            </Link>
            <button
              type="button"
              aria-label={`Delete ${thread.title}`}
              disabled={workspace.activeStreamThreadId === thread.threadId}
              onClick={() => {
                if (window.confirm(`Delete “${thread.title}”?`)) void workspace.deleteThread(thread.threadId);
              }}
            ><Trash2 /></button>
          </div>
        ))}
      </div>
      <button className="history-refresh" type="button" onClick={() => void workspace.refreshThreads()}><RefreshCw /> Refresh history</button>
    </aside>
  );
};

const ActivityPane = function ActivityPane(props: {
  onClose: () => void;
  open: boolean;
  ref: MutableRefObject<HTMLElement | null>;
  threadId?: string;
  visible: boolean;
}) {
  const workspace = useWorkspace();
  const fallbackState = useMemo(emptyChat, []);
  const state = props.threadId ? workspace.chatStates[props.threadId] ?? fallbackState : fallbackState;
  const [runs, setRuns] = useState<AgentBacktestReference[]>(state.backtests);
  const [pageVisible, setPageVisible] = useState(document.visibilityState === "visible");

  useEffect(() => setRuns(state.backtests), [state.backtests]);
  useEffect(() => {
    const update = () => setPageVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  useEffect(() => {
    const activeReferences = state.backtests.filter((reference) =>
      ["queued", "running"].includes(reference.status),
    );
    if (!activeReferences.length || !props.visible || !pageVisible) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const envelopes = await Promise.all(
          activeReferences.map((reference) => workspace.backtests.get(reference.runId)),
        );
        if (!cancelled) {
          const updates = new Map(envelopes.map((envelope) => [envelope.run.runId, envelope]));
          setRuns((current) => current.map((run) => {
            const envelope = updates.get(run.runId);
            return envelope ? {
              kind: "backtest_run",
              runId: envelope.run.runId,
              marketId: envelope.run.marketId,
              marketQuestion: envelope.run.marketQuestion,
              strategy: envelope.run.config.strategy,
              status: envelope.run.status,
              phase: envelope.run.phase,
              progress: envelope.run.progress,
              createdAt: envelope.run.createdAt,
            } : run;
          }));
        }
        if (
          !cancelled
          && envelopes.some((envelope) => ["queued", "running"].includes(envelope.run.status))
        ) {
          timer = window.setTimeout(() => void poll(), 3_000);
        }
      } catch (caught) {
        if (!cancelled) workspace.setMessage(errorMessage(caught));
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [pageVisible, props.visible, state.backtests, workspace.backtests, workspace.setMessage]);

  const proposal = state.proposalDraft;
  const signerReady = Boolean(
    workspace.session
    && workspace.localWallet
    && workspace.session.walletAddress.toLowerCase() === workspace.localWallet.address.toLowerCase(),
  );
  return (
    <aside ref={props.ref} className={`activity-pane ${props.open ? "drawer-open" : ""}`} aria-label="Context and activity" role={props.open ? "dialog" : undefined} aria-modal={props.open || undefined}>
      <div className="pane-heading">
        <div><span className="eyebrow">Live context</span><h2>Activity</h2></div>
        <button className="drawer-close" type="button" onClick={props.onClose} aria-label="Close activity"><X /></button>
      </div>
      <div className="activity-tape">
        {proposal && props.threadId && (
          <div className="activity-item activity-item-priority">
            <span className="tape-node" aria-hidden="true" />
            <ProposalTicket
              draft={proposal}
              onChange={(value) => workspace.updateProposal(props.threadId!, value)}
              onClear={() => workspace.clearProposal(props.threadId!)}
              onExecute={() => void workspace.executeProposal(props.threadId!)}
              reviewed={workspace.reviewed[props.threadId] === true}
              onReviewed={(value) => workspace.setReviewed(props.threadId!, value)}
              sessionReady={signerReady}
              tradeAllowed={workspace.tradeAllowed}
              submitting={workspace.busy === "submit"}
              pendingSignedOrder={Boolean(workspace.pendingOrders[props.threadId]?.signature)}
            />
          </div>
        )}
        {runs.map((run) => (
          <div className="activity-item" key={run.runId}>
            <span className="tape-node" aria-hidden="true" />
            <section className="activity-card">
              <div className="activity-card-heading"><span className={`status-dot status-${run.status}`} /><span>{strategyName(run.strategy)} · {phaseLabel(run.phase)}</span><strong>{run.progress}%</strong></div>
              <h3>{run.marketQuestion || compactIdentifier(run.marketId)}</h3>
              <div className="progress-track"><span style={{ width: `${run.progress}%` }} /></div>
              <Link to={`/backtests/${run.runId}`}>Open run <ChevronRight /></Link>
            </section>
          </div>
        ))}
        <div className="activity-item">
          <span className="tape-node" aria-hidden="true" />
          <AccountSummary account={workspace.account} session={workspace.session} onRefresh={() => void workspace.refreshAccount()} />
        </div>
        <div className="activity-item">
          <span className="tape-node" aria-hidden="true" />
          <section className="activity-card status-card">
            <div><ShieldCheck /><span>Eligibility</span><strong>{eligibilityLabel(workspace.eligibility)}</strong></div>
            <div><WalletCards /><span>Wallet</span><strong>{workspace.session ? "Session active" : "Disconnected"}</strong></div>
            {workspace.session && <p className="wallet-session-expiry">Session expires {relativeTime(workspace.session.expiresAt)}</p>}
            {workspace.session && !signerReady && <Link to="/settings">Attach browser wallet to sign <ChevronRight /></Link>}
            {!workspace.session && (
              <button className="button button-quiet reconnect-button" type="button" disabled={!workspace.tradeAllowed || workspace.busy === "wallet"} onClick={() => void workspace.connectAndVerify()}>
                <WalletCards /> {workspace.busy === "wallet" ? "Reconnecting…" : "Reconnect wallet"}
              </button>
            )}
          </section>
        </div>
      </div>
    </aside>
  );
};

function AccountSummary(props: { account: AccountOverview | null; session: WalletSessionStatus | null; onRefresh: () => void }) {
  return (
    <section className="activity-card account-summary-card">
      <div className="activity-card-heading"><span>Account</span><button type="button" onClick={props.onRefresh} disabled={!props.session} aria-label="Refresh account"><RefreshCw /></button></div>
      {props.session ? (
        <>
          <code>{compactAddress(props.session.walletAddress)}</code>
          <div className="summary-metrics">
            <span><strong>{props.account?.positions.length ?? "—"}</strong><small>Positions</small></span>
            <span><strong>{props.account?.openOrders.length ?? "—"}</strong><small>Open</small></span>
            <span><strong>{props.account?.fills.length ?? "—"}</strong><small>Fills</small></span>
          </div>
        </>
      ) : <p>Connect a wallet in Settings to load account data.</p>}
    </section>
  );
}

function TradesPage() {
  const workspace = useWorkspace();
  const proposalThreadId = workspace.lastProposalThreadId;
  const proposal = proposalThreadId ? workspace.chatStates[proposalThreadId]?.proposalDraft ?? null : null;
  const signerReady = Boolean(
    workspace.session && workspace.localWallet
    && workspace.session.walletAddress.toLowerCase() === workspace.localWallet.address.toLowerCase(),
  );
  return (
    <main className="detail-page trades-page">
      <PageTitle eyebrow="Account execution" title="Trades" description="Review positions, live orders, fills, and the exact pending action without mixing wallet verification into this page.">
        <button className="button button-quiet" type="button" onClick={() => void workspace.refreshAccount()} disabled={!workspace.session || Boolean(workspace.busy)}><RefreshCw /> Refresh account</button>
      </PageTitle>
      {!workspace.session ? (
        <section className="empty-page-card"><WalletCards /><h2>No wallet session</h2><p>Wallet connection and verification live in Settings. Reconnecting takes one free signature — your wallet type is remembered from the last session.</p><div className="empty-page-actions"><button className="button button-primary" type="button" disabled={!workspace.tradeAllowed || Boolean(workspace.busy)} onClick={() => void workspace.connectAndVerify()}><WalletCards /> {workspace.busy === "wallet" ? "Waiting for wallet…" : "Reconnect wallet"}</button><Link className="button button-quiet" to="/settings">Open Settings <ArrowRight /></Link></div>{!workspace.tradeAllowed && <p className="restriction-note">{eligibilityRestrictionMessage(workspace.eligibility)}</p>}</section>
      ) : (
        <>
          <section className="summary-strip" aria-label="Account summary">
            <SummaryStat label="Wallet" value={compactAddress(workspace.session.walletAddress)} />
            <SummaryStat label="Positions" value={workspace.account?.positions.length ?? "—"} />
            <SummaryStat label="Open orders" value={workspace.account?.openOrders.length ?? "—"} />
            <SummaryStat label="Fill history" value={workspace.account?.fills.length ?? "—"} />
            <SummaryStat label="Observed" value={workspace.account ? relativeTime(workspace.account.observedAt) : "—"} />
          </section>
          <div className="trades-grid">
            <div className="trade-data-column">
              <PositionsTable account={workspace.account} />
              <OrdersTable account={workspace.account} disabled={workspace.busy === "cancel"} onCancel={(selector) => {
                if (window.confirm("Cancel this open Polymarket order?")) void workspace.cancelOpenOrder(selector);
              }} />
              <FillsTable account={workspace.account} />
            </div>
            <aside className="pending-action-column">
              <ProposalTicket
                draft={proposal}
                onChange={(value) => proposalThreadId && workspace.updateProposal(proposalThreadId, value)}
                onClear={() => proposalThreadId && workspace.clearProposal(proposalThreadId)}
                onExecute={() => proposalThreadId && void workspace.executeProposal(proposalThreadId)}
                reviewed={Boolean(proposalThreadId && workspace.reviewed[proposalThreadId])}
                onReviewed={(value) => proposalThreadId && workspace.setReviewed(proposalThreadId, value)}
                sessionReady={signerReady}
                tradeAllowed={workspace.tradeAllowed}
                submitting={workspace.busy === "submit"}
                pendingSignedOrder={Boolean(proposalThreadId && workspace.pendingOrders[proposalThreadId]?.signature)}
              />
              {!signerReady && <Link className="inline-callout" to="/settings"><ShieldCheck /> Attach the matching browser wallet in Settings before signing.</Link>}
            </aside>
          </div>
        </>
      )}
    </main>
  );
}

function PaperPage() {
  const workspace = useWorkspace();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTemplateId = searchParams.get("template");
  const initialMarket = loadMarketFocus(searchParams.get("focus"));
  // Consume ?template= once it has armed the workspace so a refresh does not
  // re-arm it. Keeps PaperWorkspace itself router-free for its tests.
  const consumeInitialTemplate = useCallback(() => {
    setSearchParams((current) => {
      if (!current.has("template")) return current;
      const next = new URLSearchParams(current);
      next.delete("template");
      return next;
    }, { replace: true });
  }, [setSearchParams]);
  // Stable identity: PaperWorkspace's poll effect depends on these callbacks.
  const onNotice = useCallback((message: string) => workspace.setMessage(message, "notice"), [workspace.setMessage]);
  return (
    <PaperWorkspace
      client={workspace.gateway}
      onError={workspace.setMessage}
      onNotice={onNotice}
      initialTemplateId={initialTemplateId}
      onInitialTemplateConsumed={consumeInitialTemplate}
      initialMarket={initialMarket}
      onMarketSelected={saveMarketFocus}
      onMarketCleared={clearMarketFocus}
    />
  );
}

function PositionsTable({ account }: { account: AccountOverview | null }) {
  return (
    <DataSection title="Positions" count={account?.positions.length ?? 0}>
      <table><thead><tr><th>Market / outcome</th><th className="num">Size</th><th className="num">Average</th><th className="num">Current</th><th className="num">Value</th><th className="num">P&amp;L</th><th>Status</th></tr></thead><tbody>
        {account?.positions.map((position) => <tr key={position.positionId}><th>{position.marketTitle || compactIdentifier(position.conditionId || position.positionId)}<small>{position.outcome || "—"}</small></th><td className="num">{valueOrDash(position.size)}</td><td className="num">{price(position.averagePrice)}</td><td className="num">{price(position.currentPrice)}</td><td className="num">{money(position.currentValue)}</td><td className={`num ${Number(position.cashPnl) >= 0 ? "value-positive" : "value-negative"}`}>{money(position.cashPnl)}<small>{percent(position.percentPnl)}</small></td><td><StatusPill value={position.redeemable ? "Redeemable" : "Open"} /></td></tr>)}
      </tbody></table>
      {!account?.positions.length && <TableEmpty label="No positions." />}
    </DataSection>
  );
}

function OrdersTable(props: { account: AccountOverview | null; disabled: boolean; onCancel: (selector: CancellationSelector) => void }) {
  return (
    <DataSection title="Open orders" count={props.account?.openOrders.length ?? 0}>
      <table><thead><tr><th>Market / outcome</th><th>Side</th><th className="num">Remaining</th><th className="num">Price</th><th>Type</th><th>Created</th><th><span className="sr-only">Action</span></th></tr></thead><tbody>
        {props.account?.openOrders.map((order) => <tr key={order.orderId}><th>{compactIdentifier(order.marketId || order.orderId)}<small>{order.outcome || "—"}</small></th><td><StatusPill value={order.side || "—"} /></td><td className="num">{valueOrDash(order.remainingSize)}<small>{valueOrDash(order.matchedSize)} matched</small></td><td className="num">{price(order.price)}</td><td>{order.orderType || "—"}</td><td>{order.createdAt ? formatDate(order.createdAt) : "—"}</td><td><button className="table-action" type="button" disabled={props.disabled} onClick={() => props.onCancel({ kind: "order", orderId: order.orderId })}>Cancel</button></td></tr>)}
      </tbody></table>
      {!props.account?.openOrders.length && <TableEmpty label="No open orders." />}
    </DataSection>
  );
}

function FillsTable({ account }: { account: AccountOverview | null }) {
  return (
    <DataSection title="Fill history" count={account?.fills.length ?? 0}>
      <table><thead><tr><th>Trade</th><th>Market / outcome</th><th>Side</th><th className="num">Size</th><th className="num">Price</th><th>Matched</th><th>Role</th><th>Transaction</th></tr></thead><tbody>
        {account?.fills.map((fill) => <tr key={fill.tradeId}><th>{compactIdentifier(fill.tradeId)}</th><td>{compactIdentifier(fill.marketId || "—")}<small>{fill.outcome || "—"}</small></td><td><StatusPill value={fill.side || "—"} /></td><td className="num">{valueOrDash(fill.size)}</td><td className="num">{price(fill.price)}</td><td>{fill.matchedAt ? formatDate(fill.matchedAt) : "—"}</td><td>{fill.traderSide || "—"}</td><td><code>{fill.transactionHash ? compactIdentifier(fill.transactionHash) : "—"}</code></td></tr>)}
      </tbody></table>
      {!account?.fills.length && <TableEmpty label="No fills recorded." />}
    </DataSection>
  );
}

function SettingsPage() {
  const workspace = useWorkspace();
  const signerMatches = Boolean(
    workspace.session && workspace.localWallet
    && workspace.session.walletAddress.toLowerCase() === workspace.localWallet.address.toLowerCase(),
  );
  const restricted = !workspace.tradeAllowed;
  return (
    <main className="detail-page settings-page">
      <PageTitle eyebrow="Security and access" title="Settings" />
      <div className="settings-grid">
        <div className="settings-col">
          <section className="settings-card">
            <div className="settings-card-heading"><ShieldCheck /><div><span className="eyebrow">Connection status</span><h2>Eligibility</h2></div></div>
            <StatusLine label="Browser IP check" value={eligibilityLabel(workspace.eligibility)} tone={workspace.tradeAllowed ? "good" : "warn"} />
            <StatusLine label="Country / region" value={workspace.eligibility ? `${workspace.eligibility.country || "—"} / ${workspace.eligibility.region || "—"}` : "Checking"} />
            <StatusLine label="Identity" value="Signed in with Clerk" tone="good" />
            <button className="button button-quiet" type="button" onClick={() => void workspace.refreshEligibility()}><RefreshCw /> Recheck eligibility</button>
          </section>

          <section className="settings-card">
          <div className="settings-card-heading"><WalletCards /><div><span className="eyebrow">Wallet authority</span><h2>{workspace.session ? "Verified session" : "Connect a wallet"}</h2></div></div>
          {workspace.session ? (
            <>
              <div className="session-details">
                <StatusLine label="Wallet" value={workspace.session.walletAddress} mono />
                {workspace.session.funderAddress && <StatusLine label="Funder / maker" value={workspace.session.funderAddress} mono />}
                <StatusLine label="Structure" value={walletTypeLabel(workspace.session.signatureType)} />
                <StatusLine label="Idle expiry" value={formatDate(workspace.session.idleExpiresAt)} mono />
                <StatusLine label="Absolute expiry" value={formatDate(workspace.session.expiresAt)} mono />
                <StatusLine label="Browser signer" value={signerMatches ? "Matching wallet attached" : "Not locally attached"} tone={signerMatches ? "good" : "warn"} />
              </div>
              {!signerMatches && <div className="settings-warning"><CircleAlert /><p>Account data can be restored from the server session, but order signing stays disabled until the matching wallet is attached in this browser.</p></div>}
              <div className="settings-actions">
                {!signerMatches && <button className="button button-primary" type="button" onClick={() => void workspace.attachLocalSigner()} disabled={workspace.busy === "wallet"}><WalletCards /> Attach matching wallet</button>}
                <button className="button button-danger" type="button" onClick={() => void workspace.disconnectWallet()} disabled={workspace.busy === "wallet"}><LogOut /> Disconnect session</button>
              </div>
            </>
          ) : (
            <>
              <ol className="settings-steps" aria-label="How connecting works">
                <li><strong>Pick your wallet type below</strong> — the default covers most people.</li>
                <li><strong>Connect</strong> — your wallet opens and, if it is not on Polygon, asks you to switch. Accept that.</li>
                <li><strong>Sign once</strong> — a free signature proving you own the wallet. Never a transaction, no gas.</li>
              </ol>
              <p className="settings-copy">PolyTrade stores only encrypted API credentials server-side — your private key never leaves your wallet.</p>
              <div className="settings-form">
                <label>
                  <span>Wallet type</span>
                  <select value={workspace.signatureType} onChange={(event) => workspace.setSignatureType(Number(event.target.value) as 0 | 1 | 2 | 3)} aria-label="Wallet type">
                    <option value={0}>Personal wallet (MetaMask, Rabby, …)</option>
                    <option value={1}>Polymarket account — email login</option>
                    <option value={2}>Polymarket account — browser wallet login</option>
                    <option value={3}>Smart-contract wallet (EIP-1271)</option>
                  </select>
                </label>
                {workspace.signatureType !== 0 && <label><span>Funder / maker address</span><input value={workspace.funderAddress} onChange={(event) => workspace.setFunderAddress(event.target.value)} placeholder="0x…" spellCheck={false} /></label>}
              </div>
              <p className="settings-hint">{walletStructureHint(workspace.signatureType)}</p>
              {workspace.signatureType !== 0 && (
                <details className="settings-help">
                  <summary>Where do I find this address?</summary>
                  <ol>
                    <li>Log in at <a href="https://polymarket.com" target="_blank" rel="noreferrer">polymarket.com</a>.</li>
                    <li>Open <strong>Portfolio</strong> and copy the wallet address shown there — it starts with 0x and is <strong>not</strong> the address inside your wallet app.</li>
                    <li>Paste it above as the funder address. Your wallet app only signs; that Polymarket wallet holds the funds.</li>
                  </ol>
                </details>
              )}
              <button className="button button-primary" type="button" onClick={() => void workspace.connectAndVerify()} disabled={workspace.busy === "wallet" || restricted || (workspace.signatureType !== 0 && !workspace.funderAddress)}><WalletCards /> {workspace.busy === "wallet" ? "Waiting for wallet…" : "Connect and verify wallet"}</button>
              <p className="settings-hint">Needs a wallet extension in this browser (MetaMask, Rabby, …) — phone browsers usually do not have one.</p>
              {restricted && <p className="restriction-note">{eligibilityRestrictionMessage(workspace.eligibility)}</p>}
            </>
          )}
          </section>
        </div>

        <section className="settings-card alerts-settings-card">
          <div className="settings-card-heading"><Bell /><div><span className="eyebrow">Notifications</span><h2>Strategy alerts</h2></div></div>
          <AlertsSettings client={workspace.gateway} onError={workspace.setMessage} onNotice={(notice) => workspace.setMessage(notice, "notice")} />
        </section>
      </div>
    </main>
  );
}

function BacktestsPage() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const workspace = useWorkspace();
  // Stable props: BacktestsWorkspace's poll effect depends on these callbacks.
  const onSelectRun = useCallback((id: string) => navigate(id ? `/backtests/${id}` : "/backtests"), [navigate]);
  const onNewBacktest = useCallback(() => navigate("/backtests/new"), [navigate]);
  const onAskAgent = useCallback(() => navigate("/chat/new"), [navigate]);
  const onNotice = useCallback((message: string) => workspace.setMessage(message, "notice"), [workspace.setMessage]);
  return (
    <BacktestsWorkspace
      client={workspace.backtests}
      focusedRunId={runId}
      onSelectRun={onSelectRun}
      onNewBacktest={onNewBacktest}
      onAskAgent={onAskAgent}
      onError={workspace.setMessage}
      onNotice={onNotice}
    />
  );
}

interface CommonBacktestFormState {
  initialCapital: string;
  positionSizePct: string;
  takeProfit: string;
  stopLoss: string;
  maxHoldMinutes: string;
  cooldownMinutes: string;
  slippage: string;
  maxFillDelayMinutes: string;
}

interface StrategyFormDrafts {
  momentum_v1: { momentumWindowMinutes: string; momentumThreshold: string };
  mean_reversion_v1: { reversionWindowMinutes: string; reversionThreshold: string };
  breakout_v1: { breakoutWindowMinutes: string; breakoutThreshold: string };
}

const BACKTEST_STRATEGIES: Array<{
  id: BacktestStrategy;
  name: string;
  description: string;
}> = [
  {
    id: "momentum_v1",
    name: "Momentum",
    description: "Buy the outcome with the strongest configured price rise.",
  },
  {
    id: "mean_reversion_v1",
    name: "Mean reversion",
    description: "Buy an outcome trading below its trailing average.",
  },
  {
    id: "breakout_v1",
    name: "Breakout",
    description: "Buy an outcome clearing its prior rolling high.",
  },
];

function NewBacktestPage() {
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const focusedMarket = loadMarketFocus(searchParams.get("focus"));
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MarketSearchMarket[]>([]);
  const [selected, setSelected] = useState<MarketSearchMarket | null>(null);
  const [searching, setSearching] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [prefilledTemplate, setPrefilledTemplate] = useState<StrategyTemplate | null>(null);
  const [strategy, setStrategy] = useState<BacktestStrategy>("momentum_v1");
  const [common, setCommon] = useState<CommonBacktestFormState>({
    initialCapital: defaultMomentumBacktestConfig.initialCapital,
    positionSizePct: defaultMomentumBacktestConfig.positionSizePct,
    takeProfit: defaultMomentumBacktestConfig.takeProfit,
    stopLoss: defaultMomentumBacktestConfig.stopLoss,
    maxHoldMinutes: String(defaultMomentumBacktestConfig.maxHoldMinutes),
    cooldownMinutes: String(defaultMomentumBacktestConfig.cooldownMinutes),
    slippage: defaultMomentumBacktestConfig.slippage,
    maxFillDelayMinutes: String(defaultMomentumBacktestConfig.maxFillDelayMinutes),
  });
  const [drafts, setDrafts] = useState<StrategyFormDrafts>({
    momentum_v1: {
      momentumWindowMinutes: String(defaultMomentumBacktestConfig.momentumWindowMinutes),
      momentumThreshold: defaultMomentumBacktestConfig.momentumThreshold,
    },
    mean_reversion_v1: {
      reversionWindowMinutes: String(defaultMeanReversionBacktestConfig.reversionWindowMinutes),
      reversionThreshold: defaultMeanReversionBacktestConfig.reversionThreshold,
    },
    breakout_v1: {
      breakoutWindowMinutes: String(defaultBreakoutBacktestConfig.breakoutWindowMinutes),
      breakoutThreshold: defaultBreakoutBacktestConfig.breakoutThreshold,
    },
  });
  const parsed = backtestConfigSchema.safeParse(configFromForm(strategy, common, drafts));
  const selectedStrategy = BACKTEST_STRATEGIES.find((item) => item.id === strategy)!;
  const initialMarketRef = useRef<string | null>(null);

  const runSearch = useCallback(async (term: string) => {
    if (!term.trim()) return;
    setSearching(true);
    try {
      const response = await workspace.gateway.searchMarkets(term.trim(), "resolved", 20);
      setResults(response.events.flatMap((eventItem) => eventItem.markets).filter(isBacktestEligibleMarket));
      setSelected(null);
    } catch (caught) {
      workspace.setMessage(errorMessage(caught));
    } finally {
      setSearching(false);
    }
  }, [workspace]);

  // A ?template= link pre-fills the strategy picker and the market search
  // once, then the param is stripped so a refresh does not re-run the search.
  // The template's price-band offsets are deliberately NOT mapped onto the
  // backtest config — different engine vocabulary — only the strategy family
  // from backtestHint carries over.
  const templateId = searchParams.get("template");
  const initialTemplateRef = useRef<string | null>(null);
  useEffect(() => {
    if (initialTemplateRef.current !== null) return;
    initialTemplateRef.current = templateId ?? "";
    const template = templateId ? strategyTemplateById(templateId) : undefined;
    if (!template) {
      if (templateId) setSearchParams({}, { replace: true });
      return;
    }
    setPrefilledTemplate(template);
    setStrategy(template.backtestHint?.strategy ?? "mean_reversion_v1");
    setQuery(template.suggestedSearchQuery);
    void runSearch(template.suggestedSearchQuery);
    setSearchParams((current) => {
      if (!current.has("template")) return current;
      const next = new URLSearchParams(current);
      next.delete("template");
      return next;
    }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!focusedMarket || initialMarketRef.current === focusedMarket.conditionId) return;
    initialMarketRef.current = focusedMarket.conditionId;
    if (!isBacktestEligibleMarket(focusedMarket)) return;
    setSelected(focusedMarket);
    setQuery(focusedMarket.question);
  }, [focusedMarket]);

  const search = async (event: FormEvent) => {
    event.preventDefault();
    await runSearch(query);
  };

  const launch = async () => {
    if (!selected || !parsed.success) return;
    setLaunching(true);
    try {
      const created = await workspace.backtests.create(selected.conditionId || selected.id, parsed.data);
      workspace.setMessage("Backtest queued.", "notice");
      navigate(`/backtests/${created.run.runId}`);
    } catch (caught) {
      workspace.setMessage(errorMessage(caught));
    } finally {
      setLaunching(false);
    }
  };

  return (
    <main className="detail-page new-backtest-page">
      <PageTitle eyebrow="Strategies / New backtest" title="Launch a backtest" description={`${selectedStrategy.description} Every strategy uses next-observation fills and shared risk controls. Results are hypothetical.`}>
        <Link className="button button-quiet" to="/backtests">Back to runs</Link>
      </PageTitle>
      <div className="new-backtest-grid">
        <section className="form-card">
          <div className="form-section-heading"><span>01</span><div><h2>Resolved market</h2><p>Search resolved binary markets with normalized gateway data.</p></div></div>
          <form className="market-search" onSubmit={(event) => void search(event)}>
            <label className="sr-only" htmlFor="market-search">Search resolved markets</label>
            <Search aria-hidden="true" />
            <input id="market-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search election, Fed, sports…" />
            <button type="submit" disabled={searching || !query.trim()}>{searching ? "Searching…" : "Search"}</button>
          </form>
          <div className="market-results">
            {results.map((market) => (
              <button className={selected?.id === market.id ? "market-result-selected" : ""} key={market.id} type="button" onClick={() => { setSelected(market); saveMarketFocus(market); }}>
                <span><strong>{market.question}</strong><small>{market.outcomes.join(" / ")} · resolved {market.closedTime ? formatDate(market.closedTime) : "market"}</small></span>
                <span>{selected?.id === market.id ? <Check /> : <ChevronRight />}</span>
              </button>
            ))}
            {!results.length && query && !searching && <p className="pane-empty">No eligible markets found. Backtests require resolved binary CLOB V2 markets created on or after April 28, 2026.</p>}
          </div>
          {selected ? <MarketFocus market={selected} showBacktestAction={false} onClear={() => { setSelected(null); clearMarketFocus(); }} /> : null}
        </section>

        <section className="form-card">
          <div className="form-section-heading"><span>02</span><div><h2>Strategy and configuration</h2><p>Choose the entry signal, then tune its replay parameters.</p></div></div>
          {prefilledTemplate ? (
            <p className="template-chip template-chip-inline" role="status">
              <span>Pre-filled from template · {prefilledTemplate.name}</span>
              <button className="button button-quiet template-chip-clear" type="button" onClick={() => setPrefilledTemplate(null)}>Dismiss</button>
            </p>
          ) : null}
          <fieldset className="strategy-selector">
            <legend className="sr-only">Backtest strategy</legend>
            {BACKTEST_STRATEGIES.map((option) => (
              <label className={strategy === option.id ? "strategy-option-selected" : ""} key={option.id}>
                <input type="radio" name="backtest-strategy" value={option.id} checked={strategy === option.id} onChange={() => setStrategy(option.id)} />
                <span className="strategy-option-copy">
                  <span><strong>{option.name}</strong><code>{option.id}</code></span>
                  <small>{option.description}</small>
                </span>
              </label>
            ))}
          </fieldset>
          <div className="config-form-grid">
            {strategy === "momentum_v1" && <>
              <ConfigField label="Momentum window (min)" value={drafts.momentum_v1.momentumWindowMinutes} onChange={(value) => setDrafts((current) => ({ ...current, momentum_v1: { ...current.momentum_v1, momentumWindowMinutes: value } }))} integer />
              <ConfigField label="Momentum threshold" value={drafts.momentum_v1.momentumThreshold} onChange={(value) => setDrafts((current) => ({ ...current, momentum_v1: { ...current.momentum_v1, momentumThreshold: value } }))} />
            </>}
            {strategy === "mean_reversion_v1" && <>
              <ConfigField label="Trailing mean window (min)" value={drafts.mean_reversion_v1.reversionWindowMinutes} onChange={(value) => setDrafts((current) => ({ ...current, mean_reversion_v1: { ...current.mean_reversion_v1, reversionWindowMinutes: value } }))} integer />
              <ConfigField label="Discount to mean" value={drafts.mean_reversion_v1.reversionThreshold} onChange={(value) => setDrafts((current) => ({ ...current, mean_reversion_v1: { ...current.mean_reversion_v1, reversionThreshold: value } }))} />
            </>}
            {strategy === "breakout_v1" && <>
              <ConfigField label="Prior-high window (min)" value={drafts.breakout_v1.breakoutWindowMinutes} onChange={(value) => setDrafts((current) => ({ ...current, breakout_v1: { ...current.breakout_v1, breakoutWindowMinutes: value } }))} integer />
              <ConfigField label="Breakout buffer" value={drafts.breakout_v1.breakoutThreshold} onChange={(value) => setDrafts((current) => ({ ...current, breakout_v1: { ...current.breakout_v1, breakoutThreshold: value } }))} />
            </>}
            <ConfigField label="Starting capital" value={common.initialCapital} onChange={(value) => setCommon((current) => ({ ...current, initialCapital: value }))} />
            <ConfigField label="Position size (0–1)" value={common.positionSizePct} onChange={(value) => setCommon((current) => ({ ...current, positionSizePct: value }))} />
            <ConfigField label="Take-profit move" value={common.takeProfit} onChange={(value) => setCommon((current) => ({ ...current, takeProfit: value }))} />
            <ConfigField label="Stop-loss move" value={common.stopLoss} onChange={(value) => setCommon((current) => ({ ...current, stopLoss: value }))} />
            <ConfigField label="Maximum hold (min)" value={common.maxHoldMinutes} onChange={(value) => setCommon((current) => ({ ...current, maxHoldMinutes: value }))} integer />
            <ConfigField label="Cooldown (min)" value={common.cooldownMinutes} onChange={(value) => setCommon((current) => ({ ...current, cooldownMinutes: value }))} integer />
            <ConfigField label="Slippage / side" value={common.slippage} onChange={(value) => setCommon((current) => ({ ...current, slippage: value }))} />
            <ConfigField label="Maximum fill delay (min)" value={common.maxFillDelayMinutes} onChange={(value) => setCommon((current) => ({ ...current, maxFillDelayMinutes: value }))} integer />
          </div>
          {!parsed.success && <div className="validation-summary" role="alert"><CircleAlert />{parsed.error.issues[0]?.message ?? "Review the configuration."}</div>}
          <button className="button button-primary button-wide" type="button" disabled={!selected || !parsed.success || launching} onClick={() => void launch()}>{launching ? "Launching…" : "Launch backtest"} <ArrowRight /></button>
        </section>
      </div>
    </main>
  );
}

function ConfigField(props: {
  integer?: boolean;
  label: string;
  onChange: (value: string) => void;
  value: string;
}) {
  return <label><span>{props.label}</span><input inputMode={props.integer ? "numeric" : "decimal"} value={props.value} onChange={(event) => props.onChange(event.target.value)} /></label>;
}

function configFromForm(
  strategy: BacktestStrategy,
  common: CommonBacktestFormState,
  drafts: StrategyFormDrafts,
): BacktestConfig {
  const shared = {
    initialCapital: common.initialCapital,
    positionSizePct: common.positionSizePct,
    takeProfit: common.takeProfit,
    stopLoss: common.stopLoss,
    maxHoldMinutes: Number(common.maxHoldMinutes),
    cooldownMinutes: Number(common.cooldownMinutes),
    slippage: common.slippage,
    maxFillDelayMinutes: Number(common.maxFillDelayMinutes),
  };
  if (strategy === "momentum_v1") {
    return {
      strategy,
      ...shared,
      momentumWindowMinutes: Number(drafts.momentum_v1.momentumWindowMinutes),
      momentumThreshold: drafts.momentum_v1.momentumThreshold,
    };
  }
  if (strategy === "mean_reversion_v1") {
    return {
      strategy,
      ...shared,
      reversionWindowMinutes: Number(drafts.mean_reversion_v1.reversionWindowMinutes),
      reversionThreshold: drafts.mean_reversion_v1.reversionThreshold,
    };
  }
  return {
    strategy,
    ...shared,
    breakoutWindowMinutes: Number(drafts.breakout_v1.breakoutWindowMinutes),
    breakoutThreshold: drafts.breakout_v1.breakoutThreshold,
  };
}

function PageTitle(props: { children?: ReactNode; description?: string; eyebrow: string; title: string }) {
  return <header className="page-title"><div><span className="eyebrow">{props.eyebrow}</span><h1>{props.title}</h1>{props.description && <p>{props.description}</p>}</div>{props.children && <div className="page-title-actions">{props.children}</div>}</header>;
}

function DataSection(props: { children: ReactNode; count: number; title: string }) {
  return <section className="data-section"><header><h2>{props.title}</h2><span className="count-pill">{props.count}</span></header><div className="table-scroll">{props.children}</div></section>;
}

function TableEmpty({ label }: { label: string }) {
  return <p className="table-empty">{label}</p>;
}

function SummaryStat({ label, value }: { label: string; value: number | string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function StatusPill({ value }: { value: string }) {
  return <span className={`status-pill status-pill-${value.toLowerCase().replace(/[^a-z]+/g, "-")}`}>{value}</span>;
}

function StatusLine(props: { label: string; mono?: boolean; tone?: "good" | "warn"; value: string }) {
  return <div className="status-line"><span>{props.label}</span><strong className={`${props.mono ? "mono" : ""} ${props.tone ? `status-${props.tone}` : ""}`}>{props.value}</strong></div>;
}

function PageLoading({ compact = false, label }: { compact?: boolean; label: string }) {
  return <main className={`page-loading ${compact ? "page-loading-compact" : ""}`} role="status"><RefreshCw /><span>{label}</span></main>;
}

function NotFoundPage() {
  return <main className="detail-page"><section className="empty-page-card"><CircleAlert /><h1>Page not found</h1><p>This workspace route does not exist.</p><Link className="button button-primary" to="/chat">Return to chat</Link></section></main>;
}

function useDrawerKeyboard(
  open: boolean,
  close: () => void,
  drawerRef: MutableRefObject<HTMLElement | null>,
  returnRef: MutableRefObject<HTMLButtonElement | null>,
) {
  useEffect(() => {
    if (!open) return;
    const drawer = drawerRef.current;
    const focusable = () => [...(drawer?.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), input, textarea, select, [tabindex]:not([tabindex='-1'])") ?? [])];
    focusable()[0]?.focus();
    const handle = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close();
        returnRef.current?.focus();
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0]!;
      const last = items.at(-1)!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handle);
    return () => document.removeEventListener("keydown", handle);
  }, [close, drawerRef, open, returnRef]);
}

function relativeTime(value: string): string {
  const seconds = Math.round((Date.parse(value) - Date.now()) / 1_000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (Math.abs(seconds) < 60) return formatter.format(seconds, "second");
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
  return formatter.format(Math.round(hours / 24), "day");
}

function eligibilityLabel(value: Eligibility | null): string {
  if (!value) return "Checking";
  if (!value.verified) return "Unverified";
  return value.blocked ? "Research only" : "Trading eligible";
}

function eligibilityAllowsTrading(value: Eligibility | null): boolean {
  return Boolean(value?.verified && !value.blocked);
}

function eligibilityRestrictionMessage(browser: Eligibility | null): string {
  if (!browser?.verified) return "Polymarket's availability check did not respond from this browser — check your connection or VPN, then press Recheck eligibility above.";
  if (browser.blocked) return `New orders are unavailable in ${browser.country || "this browser location"}. Researching and paper trading still work.`;
  return "Wallet verification for new orders is unavailable — press Recheck eligibility above, then reload this page if it stays blocked.";
}

function walletTypeLabel(value: 0 | 1 | 2 | 3): string {
  return ["Personal wallet", "Polymarket account (email login)", "Polymarket account (browser wallet login)", "Smart-contract wallet (EIP-1271)"][value] ?? "Unknown";
}

function walletStructureHint(value: 0 | 1 | 2 | 3): string {
  return [
    "You hold USDC on Polygon directly in this wallet — no funder address needed.",
    "For Polymarket accounts created with an email login. Your funds sit in a Polymarket proxy wallet — paste its address in the funder field.",
    "For Polymarket accounts created by connecting a wallet like MetaMask to polymarket.com. Your funds sit in a Polymarket Safe wallet — paste its address in the funder field.",
    "Advanced: wallets that verify signatures through a smart contract. Paste the contract wallet address into the funder field.",
  ][value] ?? "";
}

function compactIdentifier(value: string): string {
  return value.length <= 22 ? value : `${value.slice(0, 11)}…${value.slice(-7)}`;
}

function phaseLabel(value: AgentBacktestReference["phase"]): string {
  return ({ queued: "Queued", fetching: "Fetching", simulating: "Simulating", saving: "Saving", completed: "Completed", failed: "Failed", cancelled: "Cancelled" })[value];
}

function strategyName(value: BacktestStrategy): string {
  return ({
    momentum_v1: "Momentum",
    mean_reversion_v1: "Mean reversion",
    breakout_v1: "Breakout",
  })[value];
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function valueOrDash(value: string | null): string {
  return value ?? "—";
}

function price(value: string | null): string {
  return value === null ? "—" : Number(value).toFixed(4);
}

function money(value: string | null): string {
  if (value === null || !Number.isFinite(Number(value))) return "—";
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(Number(value));
}

function percent(value: string | null): string {
  return value === null || !Number.isFinite(Number(value)) ? "—" : `${Number(value).toFixed(2)}%`;
}
