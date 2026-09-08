import {
  backtestRunEnvelopeSchema,
  backtestRunListSchema,
  backtestSeriesResponseSchema,
  backtestTradesResponseSchema,
  createBacktestRequestSchema,
  type BacktestConfig,
  type BacktestRun,
  type BacktestRunEnvelope,
  type BacktestSeriesResponse,
  type BacktestTradesResponse,
} from "@polytrade/contracts";

export class BacktestApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "BacktestApiError";
  }
}

export class BacktestClient {
  constructor(
    private readonly baseUrl: string,
    private readonly getToken: () => Promise<string>,
  ) {}

  async list(limit = 50): Promise<BacktestRun[]> {
    const response = await this.request(`/v1/backtests?limit=${limit}`);
    return backtestRunListSchema.parse(response).items;
  }

  async get(runId: string): Promise<BacktestRunEnvelope> {
    return backtestRunEnvelopeSchema.parse(
      await this.request(`/v1/backtests/${encodeURIComponent(runId)}`),
    );
  }

  async trades(runId: string, offset = 0, limit = 100): Promise<BacktestTradesResponse> {
    const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    return backtestTradesResponseSchema.parse(
      await this.request(`/v1/backtests/${encodeURIComponent(runId)}/trades?${query}`),
    );
  }

  async series(runId: string): Promise<BacktestSeriesResponse> {
    return backtestSeriesResponseSchema.parse(
      await this.request(`/v1/backtests/${encodeURIComponent(runId)}/series`),
    );
  }

  async create(marketId: string, config: BacktestConfig): Promise<BacktestRunEnvelope> {
    const body = createBacktestRequestSchema.parse({ marketId, config });
    return backtestRunEnvelopeSchema.parse(
      await this.request("/v1/backtests", {
        method: "POST",
        body: JSON.stringify(body),
      }, crypto.randomUUID()),
    );
  }

  async cancel(runId: string): Promise<BacktestRunEnvelope> {
    return backtestRunEnvelopeSchema.parse(
      await this.request(
        `/v1/backtests/${encodeURIComponent(runId)}/cancel`,
        { method: "POST" },
        crypto.randomUUID(),
      ),
    );
  }

  async delete(runId: string): Promise<void> {
    await this.request(`/v1/backtests/${encodeURIComponent(runId)}`, { method: "DELETE" });
  }

  private async request(
    path: string,
    init: RequestInit = {},
    idempotencyKey?: string,
  ): Promise<unknown> {
    const token = await this.getToken();
    if (!token) throw new Error("Authentication token is unavailable");
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    headers.set("Accept", "application/json");
    if (init.body !== undefined) headers.set("Content-Type", "application/json");
    if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
    const response = await fetch(new URL(path, this.baseUrl), {
      ...init,
      headers,
      credentials: "omit",
    });
    if (response.status === 204) return undefined;
    const payload = await response.json().catch(() => null) as
      | { detail?: unknown; error?: { message?: unknown } }
      | null;
    if (!response.ok) {
      const message = typeof payload?.detail === "string"
        ? payload.detail
        : typeof payload?.error?.message === "string"
          ? payload.error.message
          : `Backtest request failed (${response.status})`;
      throw new BacktestApiError(message, response.status);
    }
    return payload;
  }
}
