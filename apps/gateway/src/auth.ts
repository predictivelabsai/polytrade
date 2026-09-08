import {
  createLocalJWKSet,
  decodeJwt,
  decodeProtectedHeader,
  errors,
  jwtVerify,
  type JSONWebKeySet,
  type JWTVerifyGetKey,
} from "jose";

import type { GatewayConfig } from "./config.js";
import { forbidden, unauthorized } from "./errors.js";
import type { Principal } from "./types.js";

export interface IssuerConfig {
  name: Principal["issuer"];
  issuer: string;
  audience: string;
  key: JWTVerifyGetKey;
  maxLifetimeSeconds?: number;
  requireScopeClaim?: boolean;
}

export class JwtVerifier {
  private readonly issuers = new Map<string, IssuerConfig>();

  constructor(issuers: IssuerConfig[]) {
    for (const issuer of issuers) this.issuers.set(issuer.issuer, issuer);
  }

  async verifyAuthorization(header: string | undefined, requiredScope: string): Promise<Principal> {
    if (!header?.startsWith("Bearer ")) throw unauthorized("Bearer token required");
    return this.verify(header.slice(7), requiredScope);
  }

  async verify(token: string, requiredScope: string): Promise<Principal> {
    let unverifiedIssuer: unknown;
    let protectedHeader: ReturnType<typeof decodeProtectedHeader>;
    try {
      unverifiedIssuer = decodeJwt(token).iss;
      protectedHeader = decodeProtectedHeader(token);
    } catch {
      throw unauthorized("Malformed bearer token");
    }

    const issuer = typeof unverifiedIssuer === "string" ? this.issuers.get(unverifiedIssuer) : undefined;
    if (!issuer) throw unauthorized("Untrusted token issuer");
    if (protectedHeader.alg !== "RS256" || !protectedHeader.kid) {
      throw unauthorized("JWT must use RS256 with a key ID");
    }

    let payload;
    try {
      ({ payload } = await jwtVerify(token, issuer.key, {
        algorithms: ["RS256"],
        issuer: issuer.issuer,
        audience: issuer.audience,
        clockTolerance: 30,
        requiredClaims: ["iss", "aud", "sub", "iat", "exp", "jti"],
      }));
    } catch {
      throw unauthorized("Invalid bearer token");
    }

    if (typeof payload.sub !== "string" || !payload.sub) throw unauthorized("JWT subject is required");
    if (typeof payload.jti !== "string" || !payload.jti) throw unauthorized("JWT ID is required");
    const now = Math.floor(Date.now() / 1_000);
    if (typeof payload.iat !== "number" || payload.iat > now + 30) {
      throw unauthorized("JWT issued-at time is invalid");
    }
    if (typeof payload.exp !== "number" || payload.exp <= payload.iat) {
      throw unauthorized("JWT expiration is invalid");
    }
    if (
      issuer.maxLifetimeSeconds !== undefined &&
      (typeof payload.iat !== "number" ||
        typeof payload.exp !== "number" ||
        payload.exp - payload.iat > issuer.maxLifetimeSeconds)
    ) {
      throw unauthorized("AssetHero JWT lifetime exceeds five minutes");
    }

    const scopes = new Set<string>();
    if (issuer.requireScopeClaim && (typeof payload.scope !== "string" || !payload.scope.trim())) {
      throw unauthorized("AssetHero JWT scope claim is required");
    }
    if (typeof payload.scope === "string") {
      for (const scope of payload.scope.split(/\s+/).filter(Boolean)) scopes.add(scope);
    }
    if (!issuer.requireScopeClaim && Array.isArray(payload.permissions)) {
      for (const scope of payload.permissions) if (typeof scope === "string") scopes.add(scope);
    }
    if (!scopes.has(requiredScope)) throw forbidden(`Missing ${requiredScope} scope`);

    return {
      id: `${issuer.name}:${payload.sub}`,
      issuer: issuer.name,
      subject: payload.sub,
      scopes,
    };
  }
}

export function createCachedRemoteJWKSet(
  url: URL,
  request: typeof fetch = fetch,
): JWTVerifyGetKey {
  let local: JWTVerifyGetKey | undefined;
  let expiresAt = 0;
  let pending: Promise<void> | undefined;

  const reload = async () => {
    pending ??= (async () => {
      const response = await request(url, {
        method: "GET",
        redirect: "manual",
        headers: { Accept: "application/jwk-set+json, application/json" },
        signal: AbortSignal.timeout(5_000),
      });
      if (!response.ok) throw new Error(`JWKS request failed (${response.status})`);
      const value = await response.json() as Partial<JSONWebKeySet>;
      if (!Array.isArray(value.keys) || value.keys.length === 0) {
        throw new Error("JWKS response has no keys");
      }
      local = createLocalJWKSet({ keys: value.keys });
      expiresAt = Date.now() + cacheLifetimeMilliseconds(response.headers);
    })().finally(() => {
      pending = undefined;
    });
    await pending;
  };

  return async (protectedHeader, token) => {
    if (!local || Date.now() >= expiresAt) await reload();
    try {
      return await local!(protectedHeader, token);
    } catch (error) {
      if (!(error instanceof errors.JWKSNoMatchingKey)) throw error;
      // One forced refresh permits overlapping key rotation. A second miss
      // fails closed instead of repeatedly fetching attacker-selected kids.
      await reload();
      return local!(protectedHeader, token);
    }
  };
}

function cacheLifetimeMilliseconds(headers: Headers): number {
  const cacheControl = headers.get("cache-control") ?? "";
  if (/(?:^|,)\s*(?:no-store|no-cache)(?:\s|,|$)/i.test(cacheControl)) return 0;
  const match = cacheControl.match(/(?:^|,)\s*(?:s-maxage|max-age)=(\d+)/i);
  if (match?.[1]) return Number(match[1]) * 1_000;
  const expires = headers.get("expires");
  if (expires) {
    const milliseconds = Date.parse(expires) - Date.now();
    if (Number.isFinite(milliseconds) && milliseconds >= 0) return milliseconds;
  }
  return 300_000;
}

export function createJwtVerifier(config: GatewayConfig): JwtVerifier {
  const issuers: IssuerConfig[] = [
    {
      name: "clerk",
      issuer: config.CLERK_ISSUER.replace(/\/$/, ""),
      audience: config.CLERK_AUDIENCE,
      key: createCachedRemoteJWKSet(new URL(config.CLERK_JWKS_URL)),
    },
  ];
  if (config.ASSETHERO_API_ISSUER && config.ASSETHERO_API_JWKS_URL) {
    issuers.push({
      name: "assethero",
      issuer: config.ASSETHERO_API_ISSUER,
      audience: config.ASSETHERO_API_AUDIENCE,
      key: createCachedRemoteJWKSet(new URL(config.ASSETHERO_API_JWKS_URL)),
      maxLifetimeSeconds: 300,
      requireScopeClaim: true,
    });
  }
  return new JwtVerifier(issuers);
}
