import asyncio
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import time
from typing import Any

import httpx
import jwt

from .config import BacktestSettings


@dataclass(frozen=True)
class Issuer:
    name: str
    issuer: str
    audience: str
    jwks_url: str
    max_lifetime_seconds: int | None = None
    require_scope_claim: bool = False


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    identity: str
    issuer: str
    scopes: tuple[str, ...]


class HeaderCachingJwkClient:
    def __init__(self, url: str) -> None:
        if not url.startswith("https://"):
            raise ValueError("JWKS URL must use HTTPS")
        self.url = url
        self._keys: list[jwt.PyJWK] = []
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        key_id = jwt.get_unverified_header(token).get("kid")
        with self._lock:
            if not self._keys or time() >= self._expires_at:
                self._reload()
            key = self._find(key_id)
            if key is not None:
                return key
            self._reload()
            key = self._find(key_id)
            if key is None:
                raise jwt.PyJWKClientError("Unable to find a signing key that matches kid")
            return key

    def _find(self, key_id: Any) -> jwt.PyJWK | None:
        if not isinstance(key_id, str):
            return None
        return next((key for key in self._keys if key.key_id == key_id), None)

    def _reload(self) -> None:
        with httpx.Client(timeout=5, follow_redirects=False) as client:
            response = client.get(
                self.url,
                headers={"Accept": "application/jwk-set+json, application/json"},
            )
            response.raise_for_status()
            value = response.json()
        if not isinstance(value, dict) or not isinstance(value.get("keys"), list):
            raise jwt.PyJWKClientError("JWKS response has no keys")
        key_set = jwt.PyJWKSet.from_dict(value)
        if not key_set.keys:
            raise jwt.PyJWKClientError("JWKS response has no keys")
        self._keys = list(key_set.keys)
        self._expires_at = time() + _cache_lifetime_seconds(response.headers)


def _cache_lifetime_seconds(headers: httpx.Headers) -> float:
    cache_control = headers.get("cache-control", "")
    if re.search(r"(?:^|,)\s*(?:no-store|no-cache)(?:\s|,|$)", cache_control, re.I):
        return 0.0
    match = re.search(r"(?:^|,)\s*(?:s-maxage|max-age)=(\d+)", cache_control, re.I)
    if match:
        return float(match.group(1))
    expires = headers.get("expires")
    if expires:
        try:
            return max(0.0, (parsedate_to_datetime(expires) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError):
            pass
    return 300.0


class TokenVerifier:
    def __init__(self, settings: BacktestSettings):
        self.issuers = {
            str(settings.CLERK_ISSUER).rstrip("/"): Issuer(
                name="clerk",
                issuer=str(settings.CLERK_ISSUER).rstrip("/"),
                audience=settings.CLERK_AUDIENCE,
                jwks_url=str(settings.CLERK_JWKS_URL),
            ),
        }
        if settings.ASSETHERO_API_ISSUER and settings.ASSETHERO_API_JWKS_URL:
            assethero_issuer = str(settings.ASSETHERO_API_ISSUER).rstrip("/")
            self.issuers[assethero_issuer] = Issuer(
                name="assethero",
                issuer=assethero_issuer,
                audience=settings.ASSETHERO_API_AUDIENCE,
                jwks_url=str(settings.ASSETHERO_API_JWKS_URL),
                max_lifetime_seconds=300,
                require_scope_claim=True,
            )
        self._clients = {
            key: HeaderCachingJwkClient(value.jwks_url) for key, value in self.issuers.items()
        }

    async def verify(self, token: str, required_scope: str = "research") -> AuthenticatedPrincipal:
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise ValueError("Malformed bearer token") from exc
        issuer = self.issuers.get(unverified.get("iss"))
        if issuer is None:
            raise ValueError("Untrusted token issuer")
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise ValueError("JWT must use RS256 with a key ID")
        try:
            signing_key = await asyncio.to_thread(
                self._clients[issuer.issuer].get_signing_key_from_jwt, token
            )
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=issuer.issuer,
                audience=issuer.audience,
                leeway=30,
                options={"require": ["iss", "aud", "sub", "iat", "exp", "jti"]},
            )
        except (jwt.PyJWTError, httpx.HTTPError, ValueError) as exc:
            raise ValueError("Invalid bearer token") from exc
        subject = claims.get("sub")
        issued_at = claims.get("iat")
        expires_at = claims.get("exp")
        if not isinstance(subject, str) or not subject:
            raise ValueError("JWT subject is required")
        if issuer.max_lifetime_seconds is not None and (
            not isinstance(issued_at, int)
            or not isinstance(expires_at, int)
            or expires_at - issued_at > issuer.max_lifetime_seconds
        ):
            raise ValueError("AssetHero JWT lifetime exceeds five minutes")
        now = int(time())
        if not isinstance(issued_at, int) or issued_at > now + 30:
            raise ValueError("JWT issued-at time is invalid")
        if not isinstance(expires_at, int) or expires_at <= issued_at:
            raise ValueError("JWT expiration is invalid")
        if not isinstance(claims.get("jti"), str) or not claims["jti"]:
            raise ValueError("JWT ID is required")
        raw_scope = claims.get("scope", "")
        if issuer.require_scope_claim and (not isinstance(raw_scope, str) or not raw_scope.strip()):
            raise ValueError("AssetHero JWT scope claim is required")
        scopes = set(raw_scope.split()) if isinstance(raw_scope, str) else set()
        permissions = claims.get("permissions", [])
        if not issuer.require_scope_claim and isinstance(permissions, list):
            scopes.update(item for item in permissions if isinstance(item, str))
        if required_scope not in scopes:
            raise PermissionError(f"Missing {required_scope} scope")
        return AuthenticatedPrincipal(
            identity=f"{issuer.name}:{subject}",
            issuer=issuer.name,
            scopes=tuple(sorted(scopes)),
        )
