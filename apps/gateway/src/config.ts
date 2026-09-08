import { z } from "zod";

const httpsUrl = z.string().url().refine((value) => new URL(value).protocol === "https:", {
  message: "URL must use HTTPS",
});

const optionalHttpsUrl = z.preprocess(
  (value) => typeof value === "string" && value.trim() === "" ? undefined : value,
  httpsUrl.optional(),
);

const internalHttpOrigin = z.string().url().superRefine((value, context) => {
  const parsed = new URL(value);
  if (!new Set(["http:", "https:"]).has(parsed.protocol)) {
    context.addIssue({ code: "custom", message: "URL must use HTTP or HTTPS" });
  }
  if (
    parsed.username
    || parsed.password
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
  ) {
    context.addIssue({
      code: "custom",
      message: "URL must be an origin without credentials, path, query, or fragment",
    });
  }
}).transform((value) => value.replace(/\/$/, ""));

const envSchema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  PORT: z.coerce.number().int().min(1).max(65_535).default(4000),
  DATABASE_URL: z.string().min(1),
  CREDENTIALS_KEK_BASE64: z.string().min(1),
  CORS_ORIGINS: z.string().default("http://localhost:5173"),
  TRUSTED_PROXIES: z.string().default("127.0.0.1/32,::1/128"),
  AGENT_UPSTREAM_URL: internalHttpOrigin.default("http://localhost:8000"),
  BACKTEST_UPSTREAM_URL: internalHttpOrigin.default("http://localhost:8100"),
  UPSTREAM_PROXY_TIMEOUT_MS: z.coerce.number().int().min(100).max(300_000).default(60_000),
  ASSETHERO_API_ISSUER: optionalHttpsUrl,
  ASSETHERO_API_JWKS_URL: optionalHttpsUrl,
  ASSETHERO_API_AUDIENCE: z.literal("polytrade").default("polytrade"),
  CLERK_ISSUER: httpsUrl,
  CLERK_JWKS_URL: httpsUrl,
  CLERK_AUDIENCE: z.literal("polytrade").default("polytrade"),
  POLYMARKET_GAMMA_URL: httpsUrl.default("https://gamma-api.polymarket.com"),
  POLYMARKET_DATA_URL: httpsUrl.default("https://data-api.polymarket.com"),
  POLYMARKET_CLOB_URL: httpsUrl.default("https://clob.polymarket.com"),
  POLYMARKET_CHAIN_ID: z.coerce.number().int().default(137),
  POLYMARKET_REQUEST_TIMEOUT_MS: z.coerce.number().int().min(1_000).max(30_000).default(10_000),
  WALLET_CHALLENGE_TTL_SECONDS: z.coerce.number().int().min(60).max(600).default(300),
  // The idle window slides forward on every wallet-scoped call the web client
  // makes while its tab is visible (account refresh), so it only binds when the
  // user is away. The absolute cap still bounds credential custody to one day.
  WALLET_SESSION_IDLE_SECONDS: z.coerce.number().int().min(300).max(28_800).default(14_400),
  WALLET_SESSION_MAX_SECONDS: z.coerce.number().int().min(1_800).max(86_400).default(86_400),
  ORDER_INTENT_TTL_SECONDS: z.coerce.number().int().min(30).max(300).default(120),
  TELEGRAM_BOT_TOKEN: z.string().min(10).optional(),
  ALERT_SEND_TIMEOUT_MS: z.coerce.number().int().min(1_000).max(30_000).default(5_000),
});

export type GatewayConfig = ReturnType<typeof parseConfig>;

export function parseConfig(environment: NodeJS.ProcessEnv) {
  const env = envSchema.parse(environment);
  if (env.NODE_ENV !== "test" && env.UPSTREAM_PROXY_TIMEOUT_MS < 20_000) {
    throw new Error("UPSTREAM_PROXY_TIMEOUT_MS must be at least 20000 outside tests");
  }
  const key = Buffer.from(env.CREDENTIALS_KEK_BASE64, "base64");
  if (key.length !== 32) {
    throw new Error("CREDENTIALS_KEK_BASE64 must decode to exactly 32 bytes");
  }
  const assetHeroIssuer = env.ASSETHERO_API_ISSUER?.replace(/\/$/, "");
  const assetHeroJwksUrl = env.ASSETHERO_API_JWKS_URL;
  const clerkIssuer = env.CLERK_ISSUER.replace(/\/$/, "");
  if (Boolean(assetHeroIssuer) !== Boolean(assetHeroJwksUrl)) {
    throw new Error("ASSETHERO_API_ISSUER and ASSETHERO_API_JWKS_URL must be configured together");
  }
  if (assetHeroIssuer === clerkIssuer) throw new Error("AssetHero API and Clerk issuers must differ");
  const corsOrigins = env.CORS_ORIGINS.split(",").map((value) => value.trim()).filter(Boolean);
  if (corsOrigins.length === 0) throw new Error("CORS_ORIGINS must contain at least one origin");
  for (const origin of corsOrigins) {
    const parsed = new URL(origin);
    if (parsed.origin !== origin || (env.NODE_ENV === "production" && parsed.protocol !== "https:")) {
      throw new Error("CORS_ORIGINS must contain exact origins (HTTPS in production)");
    }
  }
  return {
    ...env,
    ASSETHERO_API_ISSUER: assetHeroIssuer,
    CLERK_ISSUER: clerkIssuer,
    credentialKey: key,
    corsOrigins,
    trustedProxies: env.TRUSTED_PROXIES.split(",").map((value) => value.trim()).filter(Boolean),
  } as const;
}
