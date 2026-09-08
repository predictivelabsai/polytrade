import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentApiError, listAgentThreads, runAgentTurn } from "./agent";

const THREAD_ID = "11111111-1111-4111-8111-111111111111";
const REPLACEMENT_THREAD_ID = "22222222-2222-4222-8222-222222222222";
const BACKTEST_ID = "33333333-3333-4333-8333-333333333333";
const MEAN_REVERSION_BACKTEST_ID = "44444444-4444-4444-8444-444444444444";
const BREAKOUT_BACKTEST_ID = "55555555-5555-4555-8555-555555555555";

const proposal = {
  action: "create" as const,
  execution: "GTC" as const,
  tokenId: "123",
  marketId: "condition",
  marketQuestion: "Will the custom stream work?",
  outcome: "Yes",
  side: "BUY" as const,
  rationale: "Test",
  observedAt: "2026-08-03T00:00:00.000Z",
  price: "0.45",
  size: "10",
  postOnly: false,
};

function streamResponse(): Response {
  const body = [
    `event: run.started\ndata: {"runId":"run","threadId":"${THREAD_ID}"}\n\n`,
    "event: message.started\ndata: {\"messageId\":\"assistant-1\"}\n\n",
    "event: message.delta\ndata: {\"messageId\":\"assistant-1\",\"textDelta\":\"Current \"}\n\n",
    "event: message.delta\ndata: {\"messageId\":\"assistant-1\",\"textDelta\":\"answer\"}\n\n",
    `event: proposal.created\ndata: ${JSON.stringify({ proposalId: "proposal-1", envelope: { proposal, expiresAt: "2026-08-03T00:02:00.000Z" } })}\n\n`,
    `event: backtest.created\ndata: ${JSON.stringify({ backtestId: "backtest-1", backtest: { kind: "backtest_run", runId: BACKTEST_ID, marketId: "0xcondition", marketQuestion: "Will the typed replay work?", status: "queued", phase: "queued", progress: 0, createdAt: "2026-08-03T00:00:00.000Z" } })}\n\n`,
    `event: backtest.created\ndata: ${JSON.stringify({ backtestId: "backtest-2", backtest: { kind: "backtest_run", runId: MEAN_REVERSION_BACKTEST_ID, marketId: "0xcondition", marketQuestion: "Will the typed replay work?", strategy: "mean_reversion_v1", status: "queued", phase: "queued", progress: 0, createdAt: "2026-08-03T00:00:00.000Z" } })}\n\n`,
    `event: backtest.created\ndata: ${JSON.stringify({ backtestId: "backtest-3", backtest: { kind: "backtest_run", runId: BREAKOUT_BACKTEST_ID, marketId: "0xcondition", marketQuestion: "Will the typed replay work?", strategy: "breakout_v1", status: "queued", phase: "queued", progress: 0, createdAt: "2026-08-03T00:00:00.000Z" } })}\n\n`,
    "event: run.completed\ndata: {\"runId\":\"run\"}\n\n",
  ].join("");
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("runAgentTurn", () => {
  it("lists typed thread summaries with pagination", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json({ items: [{
      threadId: THREAD_ID,
      title: "Election liquidity",
      createdAt: "2026-08-03T00:00:00.000Z",
      updatedAt: "2026-08-03T00:05:00.000Z",
      expiresAt: "2026-09-02T00:00:00.000Z",
    }] }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listAgentThreads("https://api.polytrade.test/", async () => "browser-jwt", 20, 10))
      .resolves.toEqual([expect.objectContaining({ title: "Election liquidity" })]);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("https://api.polytrade.test/v1/agent/threads?limit=20&offset=10");
  });

  it("creates a thread and consumes typed public SSE events", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        Response.json(
          {
            threadId: THREAD_ID,
            title: "New chat",
            createdAt: "2026-08-03T00:00:00.000Z",
            updatedAt: "2026-08-03T00:00:00.000Z",
            expiresAt: "2026-09-02T00:00:00.000Z",
          },
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(streamResponse());
    vi.stubGlobal("fetch", fetchMock);
    const starts: string[] = [];
    const texts: string[] = [];
    const proposals: unknown[] = [];
    const backtests: unknown[] = [];

    await runAgentTurn({
      apiUrl: "https://api.polytrade.test/",
      getToken: async () => "browser-jwt",
      text: "Find a market",
      handlers: {
        onThreadId: (value) => expect(value).toBe(THREAD_ID),
        onMessageStart: (value) => starts.push(value),
        onMessageText: (_id, value) => texts.push(value),
        onProposal: (value, expiresAt) => proposals.push({ value, expiresAt }),
        onBacktest: (value) => backtests.push(value),
      },
    });

    expect(starts).toEqual(["assistant-1"]);
    expect(texts).toEqual(["Current ", "Current answer"]);
    expect(proposals).toEqual([
      { value: proposal, expiresAt: "2026-08-03T00:02:00.000Z" },
    ]);
    expect(backtests).toEqual([
      expect.objectContaining({ runId: BACKTEST_ID, status: "queued", strategy: "momentum_v1" }),
      expect.objectContaining({ runId: MEAN_REVERSION_BACKTEST_ID, status: "queued", strategy: "mean_reversion_v1" }),
      expect.objectContaining({ runId: BREAKOUT_BACKTEST_ID, status: "queued", strategy: "breakout_v1" }),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondCall = fetchMock.mock.calls[1];
    expect(secondCall).toBeDefined();
    const [url, init] = secondCall!;
    expect(url).toBe(
      `https://api.polytrade.test/v1/agent/threads/${THREAD_ID}/runs/stream`,
    );
    expect(init?.credentials).toBe("omit");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer browser-jwt");
  });

  it("replaces a stale owned-thread reference once", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ detail: "Thread not found" }, { status: 404 }))
      .mockResolvedValueOnce(
        Response.json(
          {
            threadId: REPLACEMENT_THREAD_ID,
            title: "New chat",
            createdAt: "2026-08-03T00:00:00.000Z",
            updatedAt: "2026-08-03T00:00:00.000Z",
            expiresAt: "2026-09-02T00:00:00.000Z",
          },
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(streamResponse());
    vi.stubGlobal("fetch", fetchMock);
    const threadIds: string[] = [];

    await runAgentTurn({
      apiUrl: "https://api.polytrade.test",
      getToken: async () => "browser-jwt",
      threadId: THREAD_ID,
      text: "Resume",
      handlers: {
        onThreadId: (value) => threadIds.push(value),
        onMessageStart: () => undefined,
        onMessageText: () => undefined,
        onProposal: () => undefined,
      },
    });

    expect(threadIds).toEqual([REPLACEMENT_THREAD_ID]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const retryCall = fetchMock.mock.calls[2];
    expect(retryCall).toBeDefined();
    expect(retryCall![0]).toContain(REPLACEMENT_THREAD_ID);
  });

  it("surfaces a sanitized gateway proxy failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        Response.json(
          { error: { code: "UPSTREAM_UNAVAILABLE", message: "Agent service is unavailable" } },
          { status: 502 },
        ),
      ),
    );

    await expect(
      listAgentThreads("https://api.polytrade.test", async () => "browser-jwt"),
    ).rejects.toEqual(new AgentApiError("Agent service is unavailable", 502));
  });
});
