"""Strict multi-issuer JWT verification with bounded JWKS caching."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from .config import GatewayConfig
from .errors import AppError, forbidden, unauthorized
from .types import Principal


class CachedJwks:
    def __init__(self, url: str, client: httpx.AsyncClient | None = None) -> None:
        self.url = url
        self.client = client or httpx.AsyncClient(timeout=5, follow_redirects=False)
        self._keys: dict[str, Any] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def key(self, kid: str) -> Any:
        if time.monotonic() >= self._expires_at or kid not in self._keys:
            await self._reload()
        key = self._keys.get(kid)
        if key is None:
            await self._reload(force=True)
            key = self._keys.get(kid)
        if key is None:
            raise unauthorized("Invalid bearer token")
        return key

    async def _reload(self, force: bool = False) -> None:
        async with self._lock:
            if not force and time.monotonic() < self._expires_at and self._keys:
                return
            response = await self.client.get(
                self.url, headers={"Accept": "application/jwk-set+json, application/json"}
            )
            response.raise_for_status()
            payload = response.json()
            keys = payload.get("keys") if isinstance(payload, dict) else None
            if not isinstance(keys, list) or not keys:
                raise ValueError("JWKS response has no keys")
            self._keys = {
                item["kid"]: RSAAlgorithm.from_jwk(json.dumps(item))
                for item in keys
                if isinstance(item, dict) and isinstance(item.get("kid"), str)
            }
            self._expires_at = time.monotonic() + _cache_lifetime(response.headers)


def _cache_lifetime(headers: httpx.Headers) -> float:
    cache_control = headers.get("cache-control", "")
    if "no-store" in cache_control.lower() or "no-cache" in cache_control.lower():
        return 0
    for item in cache_control.split(","):
        name, _, value = item.strip().partition("=")
        if name.lower() in {"s-maxage", "max-age"} and value.isdigit():
            return float(value)
    expires = headers.get("expires")
    if expires:
        try:
            return max(0.0, (parsedate_to_datetime(expires) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError):
            pass
    return 300.0


@dataclass(frozen=True)
class IssuerConfig:
    name: Literal["assethero", "clerk"]
    issuer: str
    audience: str
    jwks: CachedJwks
    max_lifetime_seconds: int | None = None
    require_scope_claim: bool = False


class JwtVerifier:
    def __init__(self, issuers: list[IssuerConfig]) -> None:
        self.issuers = {issuer.issuer: issuer for issuer in issuers}

    async def verify_authorization(self, header: str | None, required_scope: str) -> Principal:
        if not header or not header.startswith("Bearer "):
            raise unauthorized("Bearer token required")
        return await self.verify(header[7:], required_scope)

    async def verify(self, token: str, required_scope: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
            payload = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise unauthorized("Malformed bearer token") from exc
        issuer = self.issuers.get(payload.get("iss"))
        if issuer is None:
            raise unauthorized("Untrusted token issuer")
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise unauthorized("JWT must use RS256 with a key ID")
        try:
            key = await issuer.jwks.key(header["kid"])
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=issuer.issuer,
                audience=issuer.audience,
                leeway=30,
                options={"require": ["iss", "aud", "sub", "iat", "exp", "jti"]},
            )
        except Exception as exc:
            if isinstance(exc, AppError):
                raise
            raise unauthorized("Invalid bearer token") from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise unauthorized("JWT subject is required")
        if not isinstance(claims.get("jti"), str) or not claims["jti"]:
            raise unauthorized("JWT ID is required")
        issued_at, expires_at = claims.get("iat"), claims.get("exp")
        now = int(time.time())
        if not isinstance(issued_at, int) or issued_at > now + 30:
            raise unauthorized("JWT issued-at time is invalid")
        if not isinstance(expires_at, int) or expires_at <= issued_at:
            raise unauthorized("JWT expiration is invalid")
        if (
            issuer.max_lifetime_seconds is not None
            and expires_at - issued_at > issuer.max_lifetime_seconds
        ):
            raise unauthorized("AssetHero JWT lifetime exceeds five minutes")
        scope_claim = claims.get("scope")
        if issuer.require_scope_claim and (
            not isinstance(scope_claim, str) or not scope_claim.strip()
        ):
            raise unauthorized("AssetHero JWT scope claim is required")
        scopes = set(scope_claim.split()) if isinstance(scope_claim, str) else set()
        permissions = claims.get("permissions")
        if not issuer.require_scope_claim and isinstance(permissions, list):
            scopes.update(value for value in permissions if isinstance(value, str))
        if required_scope not in scopes:
            raise forbidden(f"Missing {required_scope} scope")
        return Principal(
            id=f"{issuer.name}:{subject}",
            issuer=issuer.name,
            subject=subject,
            scopes=frozenset(scopes),
        )


def create_jwt_verifier(
    config: GatewayConfig, client: httpx.AsyncClient | None = None
) -> JwtVerifier:
    issuers = [
        IssuerConfig(
            name="clerk",
            issuer=config.CLERK_ISSUER,
            audience=config.CLERK_AUDIENCE,
            jwks=CachedJwks(config.CLERK_JWKS_URL, client),
        )
    ]
    if config.ASSETHERO_API_ISSUER and config.ASSETHERO_API_JWKS_URL:
        issuers.append(
            IssuerConfig(
                name="assethero",
                issuer=config.ASSETHERO_API_ISSUER,
                audience=config.ASSETHERO_API_AUDIENCE,
                jwks=CachedJwks(config.ASSETHERO_API_JWKS_URL, client),
                max_lifetime_seconds=300,
                require_scope_claim=True,
            )
        )
    return JwtVerifier(issuers)
