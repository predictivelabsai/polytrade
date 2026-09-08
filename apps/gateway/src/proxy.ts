import httpProxy from "@fastify/http-proxy";
import type { FastifyInstance } from "fastify";

import type { GatewayConfig } from "./config.js";

type ProxyError = Error & { code?: string; statusCode?: number };

function withoutBrowserCorsRequestHeaders<T extends Record<string, unknown>>(headers: T): T {
  const rewritten = { ...headers };
  delete rewritten.origin;
  delete rewritten["access-control-request-headers"];
  delete rewritten["access-control-request-method"];
  return rewritten;
}

function withoutUpstreamCorsResponseHeaders<T extends Record<string, unknown>>(headers: T): T {
  const rewritten = { ...headers };
  for (const name of Object.keys(rewritten)) {
    if (name.toLowerCase().startsWith("access-control-")) delete rewritten[name];
  }
  return rewritten;
}

async function registerProxy(
  app: FastifyInstance,
  options: {
    prefix: "/v1/agent" | "/v1/backtests";
    service: "Agent" | "Backtest";
    timeoutMs: number;
    upstream: string;
  },
) {
  await app.register(httpProxy, {
    upstream: options.upstream,
    prefix: options.prefix,
    rewritePrefix: options.prefix,
    proxyPayloads: true,
    replyOptions: {
      timeout: options.timeoutMs,
      rewriteRequestHeaders: (_request, headers) =>
        withoutBrowserCorsRequestHeaders(headers),
      rewriteHeaders: (headers) => withoutUpstreamCorsResponseHeaders(headers),
      onError: (reply, { error }) => {
        const upstreamError = error as ProxyError;
        const timeout = upstreamError.statusCode === 504
          || upstreamError.code?.includes("TIMEOUT") === true;
        const status = timeout ? 504 : 502;
        reply.request.log.warn(
          {
            upstreamService: options.service.toLowerCase(),
            errorCode: upstreamError.code,
            status,
          },
          "upstream request failed",
        );
        reply.status(status).send({
          error: {
            code: timeout ? "UPSTREAM_TIMEOUT" : "UPSTREAM_UNAVAILABLE",
            message: timeout
              ? `${options.service} service timed out`
              : `${options.service} service is unavailable`,
          },
        });
      },
    },
  });
}

export async function registerServiceProxies(app: FastifyInstance, config: GatewayConfig) {
  await registerProxy(app, {
    prefix: "/v1/agent",
    service: "Agent",
    timeoutMs: config.UPSTREAM_PROXY_TIMEOUT_MS,
    upstream: config.AGENT_UPSTREAM_URL,
  });
  await registerProxy(app, {
    prefix: "/v1/backtests",
    service: "Backtest",
    timeoutMs: config.UPSTREAM_PROXY_TIMEOUT_MS,
    upstream: config.BACKTEST_UPSTREAM_URL,
  });
}
