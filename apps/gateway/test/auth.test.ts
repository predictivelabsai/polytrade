import { exportJWK, generateKeyPair, SignJWT, type JSONWebKeySet } from "jose";
import { createLocalJWKSet } from "jose";
import { describe, expect, it } from "vitest";

import { createCachedRemoteJWKSet, JwtVerifier } from "../src/auth.js";

async function setup() {
  const { privateKey, publicKey } = await generateKeyPair("RS256");
  const jwk = await exportJWK(publicKey);
  const keySet: JSONWebKeySet = { keys: [{ ...jwk, kid: "test-key", alg: "RS256", use: "sig" }] };
  const issuer = "https://auth.assethero.test";
  const verifier = new JwtVerifier([
    {
      name: "assethero",
      issuer,
      audience: "polytrade",
      key: createLocalJWKSet(keySet),
      maxLifetimeSeconds: 300,
      requireScopeClaim: true,
    },
  ]);
  return { privateKey, issuer, verifier };
}

async function mint(
  privateKey: CryptoKey,
  issuer: string,
  options: {
    audience?: string;
    scope?: string | null;
    lifetime?: number;
    kid?: string;
    subject?: string;
    issuer?: string;
    issuedAt?: number;
  } = {},
) {
  const now = Math.floor(Date.now() / 1_000);
  return new SignJWT(options.scope === null ? {} : { scope: options.scope ?? "research trade" })
    .setProtectedHeader({ alg: "RS256", kid: options.kid ?? "test-key" })
    .setIssuer(options.issuer ?? issuer)
    .setAudience(options.audience ?? "polytrade")
    .setSubject(options.subject ?? "user-123")
    .setJti("jti-123")
    .setIssuedAt(options.issuedAt ?? now)
    .setExpirationTime((options.issuedAt ?? now) + (options.lifetime ?? 300))
    .sign(privateKey);
}

describe("JwtVerifier", () => {
  it("namespaces AssetHero identities", async () => {
    const { privateKey, issuer, verifier } = await setup();
    const principal = await verifier.verify(await mint(privateKey, issuer), "trade");
    expect(principal.id).toBe("assethero:user-123");
    expect([...principal.scopes]).toEqual(["research", "trade"]);
  });

  it("accepts Clerk permissions and rejects a forbidden signing algorithm", async () => {
    const { privateKey, publicKey } = await generateKeyPair("RS256");
    const jwk = await exportJWK(publicKey);
    const issuer = "https://clerk.test";
    const verifier = new JwtVerifier([{
      name: "clerk",
      issuer,
      audience: "polytrade",
      key: createLocalJWKSet({ keys: [{ ...jwk, kid: "clerk-key", alg: "RS256", use: "sig" }] }),
    }]);
    const now = Math.floor(Date.now() / 1_000);
    const claims = { permissions: ["research", "trade"] };
    const valid = await new SignJWT(claims)
      .setProtectedHeader({ alg: "RS256", kid: "clerk-key" })
      .setIssuer(issuer)
      .setAudience("polytrade")
      .setSubject("clerk-user")
      .setJti("clerk-jti")
      .setIssuedAt(now)
      .setExpirationTime(now + 300)
      .sign(privateKey);
    await expect(verifier.verify(valid, "trade")).resolves.toMatchObject({
      id: "clerk:clerk-user",
      issuer: "clerk",
    });

    const forbiddenAlgorithm = await new SignJWT(claims)
      .setProtectedHeader({ alg: "HS256", kid: "clerk-key" })
      .setIssuer(issuer)
      .setAudience("polytrade")
      .setSubject("clerk-user")
      .setJti("clerk-jti-hs")
      .setIssuedAt(now)
      .setExpirationTime(now + 300)
      .sign(new TextEncoder().encode("not-an-rsa-key"));
    await expect(verifier.verify(forbiddenAlgorithm, "research")).rejects.toMatchObject({
      statusCode: 401,
    });
  });

  it("rejects wrong audience, scope, and excessive AssetHero lifetime", async () => {
    const { privateKey, issuer, verifier } = await setup();
    await expect(verifier.verify(await mint(privateKey, issuer, { audience: "wrong" }), "research")).rejects.toMatchObject({ statusCode: 401 });
    await expect(verifier.verify(await mint(privateKey, issuer, { scope: "research" }), "trade")).rejects.toMatchObject({ statusCode: 403 });
    await expect(verifier.verify(await mint(privateKey, issuer, { lifetime: 301 }), "research")).rejects.toMatchObject({ statusCode: 401 });
  });

  it("rejects expired, wrong issuer, unknown-key, future, and missing-scope tokens", async () => {
    const { privateKey, issuer, verifier } = await setup();
    const now = Math.floor(Date.now() / 1_000);
    await expect(verifier.verify(await mint(privateKey, issuer, { issuedAt: now - 400, lifetime: 100 }), "research")).rejects.toMatchObject({ statusCode: 401 });
    await expect(verifier.verify(await mint(privateKey, issuer, { issuer: "https://evil.test" }), "research")).rejects.toMatchObject({ statusCode: 401 });
    await expect(verifier.verify(await mint(privateKey, issuer, { kid: "unknown" }), "research")).rejects.toMatchObject({ statusCode: 401 });
    await expect(verifier.verify(await mint(privateKey, issuer, { issuedAt: now + 120 }), "research")).rejects.toMatchObject({ statusCode: 401 });
    await expect(verifier.verify(await mint(privateKey, issuer, { scope: null }), "research")).rejects.toMatchObject({ statusCode: 401 });
  });

  it("accepts an overlapping rotated key after one unknown-kid refresh", async () => {
    const oldPair = await generateKeyPair("RS256");
    const newPair = await generateKeyPair("RS256");
    const oldJwk = await exportJWK(oldPair.publicKey);
    const newJwk = await exportJWK(newPair.publicKey);
    let requests = 0;
    const remote = createCachedRemoteJWKSet(
      new URL("https://auth.assethero.test/jwks"),
      (async () => {
        requests += 1;
        const keys = requests === 1
          ? [{ ...oldJwk, kid: "old", alg: "RS256", use: "sig" }]
          : [
              { ...oldJwk, kid: "old", alg: "RS256", use: "sig" },
              { ...newJwk, kid: "new", alg: "RS256", use: "sig" },
            ];
        return new Response(JSON.stringify({ keys }), {
          status: 200,
          headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=600" },
        });
      }) as typeof fetch,
    );
    const issuer = "https://auth.assethero.test";
    const verifier = new JwtVerifier([{ name: "assethero", issuer, audience: "polytrade", key: remote, maxLifetimeSeconds: 300, requireScopeClaim: true }]);
    await verifier.verify(await mint(oldPair.privateKey, issuer, { kid: "old" }), "research");
    await verifier.verify(await mint(newPair.privateKey, issuer, { kid: "new" }), "research");
    expect(requests).toBe(2);
  });
});
