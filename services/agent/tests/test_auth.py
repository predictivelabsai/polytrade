import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

from polytrade_agent.auth import HeaderCachingJwkClient, TokenVerifier
from polytrade_agent.config import get_settings


@pytest.fixture
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def mint(
    private,
    *,
    issuer: str,
    audience: str = "polytrade",
    scope: str = "research",
    lifetime: int = 300,
    key_id: str = "test-key",
    issued_at: datetime | None = None,
    include_scope: bool = True,
):
    now = issued_at or datetime.now(UTC)
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": "user-123",
        "iat": now,
        "exp": now + timedelta(seconds=lifetime),
        "jti": "token-123",
    }
    if include_scope:
        claims["scope"] = scope
    return jwt.encode(
        claims,
        private,
        algorithm="RS256",
        headers={"kid": key_id},
    )


@pytest.mark.asyncio
async def test_assethero_token_becomes_namespaced_principal(keys) -> None:
    private, public = keys
    verifier = TokenVerifier(get_settings())
    issuer = "https://auth.assethero.test"
    verifier._clients[issuer].get_signing_key_from_jwt = lambda _token: SimpleNamespace(key=public)

    result = await verifier.verify(mint(private, issuer=issuer, scope="research trade"))

    assert result.identity == "assethero:user-123"
    assert result.scopes == ("research", "trade")
    assert result.bearer


@pytest.mark.asyncio
async def test_clerk_permissions_are_namespaced_and_hs256_is_rejected(keys) -> None:
    private, public = keys
    verifier = TokenVerifier(get_settings())
    issuer = "https://clerk.test"
    verifier._clients[issuer].get_signing_key_from_jwt = lambda _token: SimpleNamespace(key=public)
    now = datetime.now(UTC)
    claims = {
        "iss": issuer,
        "aud": "polytrade",
        "sub": "clerk-user",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": "clerk-token",
        "permissions": ["research", "trade"],
    }
    token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "clerk-key"})

    result = await verifier.verify(token, "trade")
    assert result.identity == "clerk:clerk-user"

    forbidden_algorithm = jwt.encode(
        claims,
        "not-an-rsa-key-but-at-least-32-bytes-long",
        algorithm="HS256",
        headers={"kid": "clerk-key"},
    )
    with pytest.raises(ValueError, match="RS256"):
        await verifier.verify(forbidden_algorithm)


@pytest.mark.asyncio
async def test_assethero_token_lifetime_is_capped(keys) -> None:
    private, public = keys
    verifier = TokenVerifier(get_settings())
    issuer = "https://auth.assethero.test"
    verifier._clients[issuer].get_signing_key_from_jwt = lambda _token: SimpleNamespace(key=public)

    with pytest.raises(ValueError, match="lifetime"):
        await verifier.verify(mint(private, issuer=issuer, lifetime=301))


@pytest.mark.asyncio
async def test_wrong_audience_and_missing_scope_fail_closed(keys) -> None:
    private, public = keys
    verifier = TokenVerifier(get_settings())
    issuer = "https://auth.assethero.test"
    verifier._clients[issuer].get_signing_key_from_jwt = lambda _token: SimpleNamespace(key=public)

    with pytest.raises(ValueError, match="Invalid bearer"):
        await verifier.verify(mint(private, issuer=issuer, audience="someone-else"))
    with pytest.raises(PermissionError, match="research"):
        await verifier.verify(mint(private, issuer=issuer, scope="trade"))
    with pytest.raises(ValueError, match="scope claim"):
        await verifier.verify(mint(private, issuer=issuer, include_scope=False))


@pytest.mark.asyncio
async def test_expired_future_and_unknown_key_tokens_fail_closed(keys) -> None:
    private, public = keys
    verifier = TokenVerifier(get_settings())
    issuer = "https://auth.assethero.test"
    verifier._clients[issuer].get_signing_key_from_jwt = lambda _token: SimpleNamespace(key=public)

    with pytest.raises(ValueError, match="Invalid bearer"):
        await verifier.verify(
            mint(
                private,
                issuer=issuer,
                issued_at=datetime.now(UTC) - timedelta(minutes=10),
                lifetime=60,
            )
        )
    with pytest.raises(ValueError, match="Invalid bearer|issued-at"):
        await verifier.verify(
            mint(
                private,
                issuer=issuer,
                issued_at=datetime.now(UTC) + timedelta(seconds=31),
            )
        )
    verifier._clients[issuer].get_signing_key_from_jwt = (
        lambda _token: (_ for _ in ()).throw(jwt.PyJWKClientError("unknown kid"))
    )
    with pytest.raises(ValueError, match="Invalid bearer"):
        await verifier.verify(mint(private, issuer=issuer, key_id="unknown"))


def test_jwks_cache_refreshes_once_for_rotated_kid() -> None:
    old_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    new_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    old_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(old_private.public_key()))
    new_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(new_private.public_key()))
    url = "https://auth.assethero.test/.well-known/jwks.json"
    client = HeaderCachingJwkClient(url)
    old_token = mint(old_private, issuer="https://auth.assethero.test", key_id="old")
    new_token = mint(new_private, issuer="https://auth.assethero.test", key_id="new")

    with respx.mock:
        route = respx.get(url).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"keys": [{**old_jwk, "kid": "old", "alg": "RS256", "use": "sig"}]},
                    headers={"Cache-Control": "public, max-age=600"},
                ),
                httpx.Response(
                    200,
                    json={
                        "keys": [
                            {**old_jwk, "kid": "old", "alg": "RS256", "use": "sig"},
                            {**new_jwk, "kid": "new", "alg": "RS256", "use": "sig"},
                        ]
                    },
                    headers={"Cache-Control": "public, max-age=600"},
                ),
            ]
        )
        assert client.get_signing_key_from_jwt(old_token).key_id == "old"
        assert client.get_signing_key_from_jwt(new_token).key_id == "new"
        assert route.call_count == 2
