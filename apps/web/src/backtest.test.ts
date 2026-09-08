import { defaultMomentumBacktestConfig } from "@polytrade/contracts";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BacktestApiError, BacktestClient } from "./backtest";

const envelope = {
  run: {
    runId: "11111111-1111-4111-8111-111111111111",
    marketId: "condition-1",
    marketQuestion: null,
    status: "queued",
    phase: "queued",
    progress: 0,
    config: defaultMomentumBacktestConfig,
    resolvedOutcome: null,
    datasetHash: null,
    cancelRequested: false,
    failure: null,
    warnings: [],
    createdAt: "2026-08-04T00:00:00.000Z",
    startedAt: null,
    completedAt: null,
  },
  result: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BacktestClient", () => {
  it("parses list capacity metadata while returning the runs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        Response.json({ items: [envelope.run], activeCount: 1, activeLimit: 10 }),
      ),
    );
    const client = new BacktestClient(
      "https://api.polytrade.test",
      async () => "research-jwt",
    );

    await expect(client.list()).resolves.toEqual([expect.objectContaining({
      runId: envelope.run.runId,
    })]);
  });

  it("creates a run with the research JWT and an idempotency key", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(Response.json(envelope));
    vi.stubGlobal("fetch", fetchMock);
    const client = new BacktestClient(
      "https://api.polytrade.test",
      async () => "research-jwt",
    );

    const created = await client.create("condition-1", defaultMomentumBacktestConfig);

    expect(created.run.runId).toBe(envelope.run.runId);
    const call = fetchMock.mock.calls[0];
    expect(call).toBeDefined();
    expect(String(call![0])).toBe("https://api.polytrade.test/v1/backtests");
    expect(call![1]?.method).toBe("POST");
    const headers = new Headers(call![1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer research-jwt");
    expect(headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(String(call![1]?.body))).toEqual({
      marketId: "condition-1",
      config: defaultMomentumBacktestConfig,
    });
  });

  it("surfaces only the API's public error message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        Response.json(
          { detail: "Choose a narrower date range", internalTrace: "must-not-leak" },
          { status: 422 },
        ),
      ),
    );
    const client = new BacktestClient(
      "https://api.polytrade.test",
      async () => "research-jwt",
    );

    await expect(client.list()).rejects.toEqual(
      new BacktestApiError("Choose a narrower date range", 422),
    );
  });
});
