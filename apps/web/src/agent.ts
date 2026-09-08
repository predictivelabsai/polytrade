import {
  tradingActionProposalSchema,
  type BacktestStrategy,
  type TradingActionProposal,
} from "@polytrade/contracts";
import { z } from "zod";

export interface AgentTurnHandlers {
  onThreadId: (threadId: string) => void;
  onMessageStart: (messageId: string) => void;
  onMessageText: (messageId: string, text: string) => void;
  onProposal: (proposal: TradingActionProposal, expiresAt: string) => void;
  onBacktest?: (backtest: AgentBacktestReference) => void;
}

export interface AgentThreadMessage {
  kind: "message";
  id: string;
  role: "user" | "assistant";
  text: string;
}

export interface AgentThreadProposal {
  kind: "proposal";
  id: string;
  proposal: TradingActionProposal;
  expiresAt: string;
}

export interface AgentBacktestReference {
  kind: "backtest_run";
  runId: string;
  marketId: string;
  marketQuestion?: string | null;
  strategy: BacktestStrategy;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  phase: "queued" | "fetching" | "simulating" | "saving" | "completed" | "failed" | "cancelled";
  progress: number;
  createdAt: string;
}

export interface AgentThreadBacktest {
  kind: "backtest";
  id: string;
  backtest: AgentBacktestReference;
}

export type AgentThreadItem = AgentThreadMessage | AgentThreadProposal | AgentThreadBacktest;

export interface AgentThreadSummary {
  threadId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
}

export class AgentApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AgentApiError";
  }
}

const threadSchema = z.object({
  threadId: z.string().uuid(),
  title: z.string().min(1).max(80),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
  expiresAt: z.string().datetime(),
});

const threadListSchema = z.object({ items: z.array(threadSchema) });

const proposalEnvelopeSchema = z.object({
  proposal: tradingActionProposalSchema,
  expiresAt: z.string().datetime(),
});

const backtestReferenceSchema = z.object({
  kind: z.literal("backtest_run"),
  runId: z.string().uuid(),
  marketId: z.string(),
  marketQuestion: z.string().nullable().optional(),
  strategy: z.enum(["momentum_v1", "mean_reversion_v1", "breakout_v1"]).default("momentum_v1"),
  status: z.enum(["queued", "running", "completed", "failed", "cancelled"]),
  phase: z.enum(["queued", "fetching", "simulating", "saving", "completed", "failed", "cancelled"]),
  progress: z.number().int().min(0).max(100),
  createdAt: z.string().datetime(),
});

const threadItemsSchema = z.object({
  threadId: z.string().uuid(),
  items: z.array(
    z.discriminatedUnion("kind", [
      z.object({
        kind: z.literal("message"),
        id: z.string(),
        role: z.enum(["user", "assistant"]),
        text: z.string(),
      }),
      z.object({
        kind: z.literal("proposal"),
        id: z.string(),
        envelope: proposalEnvelopeSchema,
      }),
      z.object({
        kind: z.literal("backtest"),
        id: z.string(),
        backtest: backtestReferenceSchema,
      }),
    ]),
  ),
});

const messageStartedSchema = z.object({ messageId: z.string().min(1) });
const messageDeltaSchema = z.object({
  messageId: z.string().min(1),
  textDelta: z.string(),
});
const proposalCreatedSchema = z.object({
  proposalId: z.string().min(1),
  envelope: proposalEnvelopeSchema,
});
const backtestCreatedSchema = z.object({
  backtestId: z.string().min(1),
  backtest: backtestReferenceSchema,
});
const runFailedSchema = z.object({
  code: z.string(),
  message: z.string(),
});

export async function runAgentTurn(options: {
  apiUrl: string;
  getToken: () => Promise<string>;
  threadId?: string;
  text: string;
  handlers: AgentTurnHandlers;
}): Promise<void> {
  let threadId = options.threadId;
  if (!threadId) {
    threadId = await createAgentThread(options.apiUrl, options.getToken);
    options.handlers.onThreadId(threadId);
  }

  try {
    await streamRun({ ...options, threadId });
  } catch (error) {
    if (!(error instanceof AgentApiError) || error.status !== 404 || !options.threadId) {
      throw error;
    }
    const replacement = await createAgentThread(options.apiUrl, options.getToken);
    options.handlers.onThreadId(replacement);
    await streamRun({ ...options, threadId: replacement });
  }
}

export async function createAgentThread(
  apiUrl: string,
  getToken: () => Promise<string>,
): Promise<string> {
  const response = await agentFetch(apiUrl, "/v1/agent/threads", getToken, { method: "POST" });
  return threadSchema.parse(await response.json()).threadId;
}

export async function listAgentThreads(
  apiUrl: string,
  getToken: () => Promise<string>,
  limit = 50,
  offset = 0,
): Promise<AgentThreadSummary[]> {
  const search = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  const response = await agentFetch(apiUrl, `/v1/agent/threads?${search}`, getToken);
  return threadListSchema.parse(await response.json()).items;
}

export async function getAgentThreadItems(
  apiUrl: string,
  getToken: () => Promise<string>,
  threadId: string,
): Promise<AgentThreadItem[]> {
  const response = await agentFetch(
    apiUrl,
    `/v1/agent/threads/${encodeURIComponent(threadId)}/messages`,
    getToken,
  );
  const payload = threadItemsSchema.parse(await response.json());
  return payload.items.map((item) => {
    if (item.kind === "message" || item.kind === "backtest") return item;
    return {
          kind: "proposal" as const,
          id: item.id,
          proposal: item.envelope.proposal,
          expiresAt: item.envelope.expiresAt,
        };
  });
}

export async function deleteAgentThread(
  apiUrl: string,
  getToken: () => Promise<string>,
  threadId: string,
): Promise<void> {
  await agentFetch(
    apiUrl,
    `/v1/agent/threads/${encodeURIComponent(threadId)}`,
    getToken,
    { method: "DELETE" },
  );
}

async function streamRun(options: {
  apiUrl: string;
  getToken: () => Promise<string>;
  threadId: string;
  text: string;
  handlers: AgentTurnHandlers;
}): Promise<void> {
  const response = await agentFetch(
    options.apiUrl,
    `/v1/agent/threads/${encodeURIComponent(options.threadId)}/runs/stream`,
    options.getToken,
    {
      method: "POST",
      body: JSON.stringify({ message: options.text }),
    },
  );
  if (!response.body) throw new Error("Agent response did not include a stream");

  const rendered = new Map<string, string>();
  let completed = false;
  for await (const event of readServerEvents(response.body)) {
    switch (event.event) {
      case "run.started":
        break;
      case "message.started": {
        const payload = messageStartedSchema.parse(event.data);
        if (!rendered.has(payload.messageId)) {
          rendered.set(payload.messageId, "");
          options.handlers.onMessageStart(payload.messageId);
        }
        break;
      }
      case "message.delta": {
        const payload = messageDeltaSchema.parse(event.data);
        if (!rendered.has(payload.messageId)) {
          rendered.set(payload.messageId, "");
          options.handlers.onMessageStart(payload.messageId);
        }
        const text = `${rendered.get(payload.messageId) ?? ""}${payload.textDelta}`;
        rendered.set(payload.messageId, text);
        options.handlers.onMessageText(payload.messageId, text);
        break;
      }
      case "proposal.created": {
        const payload = proposalCreatedSchema.parse(event.data);
        options.handlers.onProposal(payload.envelope.proposal, payload.envelope.expiresAt);
        break;
      }
      case "backtest.created": {
        const payload = backtestCreatedSchema.parse(event.data);
        options.handlers.onBacktest?.(payload.backtest);
        break;
      }
      case "run.completed":
        completed = true;
        break;
      case "run.failed": {
        const payload = runFailedSchema.parse(event.data);
        throw new Error(payload.message);
      }
      default:
        break;
    }
  }
  if (!completed) throw new Error("Agent stream ended before the run completed");
}

async function agentFetch(
  apiUrl: string,
  path: string,
  getToken: () => Promise<string>,
  init: RequestInit = {},
): Promise<Response> {
  const token = await getToken();
  if (!token) throw new Error("Authentication token is unavailable");
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  headers.set("Accept", path.endsWith("/runs/stream") ? "text/event-stream" : "application/json");
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(`${apiUrl.replace(/\/$/, "")}${path}`, {
    ...init,
    headers,
    credentials: "omit",
  });
  if (!response.ok) throw new AgentApiError(await responseError(response), response.status);
  return response;
}

async function responseError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as {
      detail?: unknown;
      error?: { message?: unknown };
    };
    if (typeof payload.detail === "string") return payload.detail;
    if (typeof payload.error?.message === "string") return payload.error.message;
    return `Agent request failed (${response.status})`;
  } catch {
    return `Agent request failed (${response.status})`;
  }
}

interface ServerEvent {
  event: string;
  data: unknown;
}

async function* readServerEvents(stream: ReadableStream<Uint8Array>): AsyncGenerator<ServerEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary = eventBoundary(buffer);
      while (boundary) {
        const block = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary.length);
        const event = parseServerEvent(block);
        if (event) yield event;
        boundary = eventBoundary(buffer);
      }
      if (done) break;
    }
    if (buffer.trim()) {
      const event = parseServerEvent(buffer);
      if (event) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}

function eventBoundary(value: string): { index: number; length: number } | null {
  const match = /\r?\n\r?\n/.exec(value);
  return match ? { index: match.index, length: match[0].length } : null;
}

function parseServerEvent(block: string): ServerEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  const encoded = data.join("\n");
  try {
    return { event, data: JSON.parse(encoded) as unknown };
  } catch {
    throw new Error("Agent stream returned malformed JSON");
  }
}
