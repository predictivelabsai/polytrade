from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import encode
from jwt.algorithms import RSAAlgorithm
from polytrade_contracts import PaperQuoteRequest
from pydantic import ValidationError

from polytrade_gateway.auth import CachedJwks, IssuerConfig, JwtVerifier
from polytrade_gateway.cache import TtlCache
from polytrade_gateway.config import GatewayConfig
from polytrade_gateway.crypto import CredentialCipher
from polytrade_gateway.errors import AppError
from polytrade_gateway.paper_pricing import (
    PaperPricingError,
    best_bid_from_order_book,
    paper_liquidation_value,
    quote_paper_order,
)


def environment(**changes: str) -> dict[str, str]:
    value = {
        "NODE_ENV": "test",
        "DATABASE_URL": "postgresql://db.test/polytrade",
        "CREDENTIALS_KEK_BASE64": base64.b64encode(bytes(range(32))).decode(),
        "CLERK_ISSUER": "https://clerk.test",
        "CLERK_JWKS_URL": "https://clerk.test/.well-known/jwks.json",
        "CORS_ORIGINS": "https://polytrade.test,https://assethero.test",
    }
    value.update(changes)
    return value


def test_config_normalizes_origins_and_rejects_unsafe_values() -> None:
    config = GatewayConfig.model_validate(environment(TELEGRAM_BOT_TOKEN=""))
    assert config.TELEGRAM_BOT_TOKEN is None
    assert config.cors_origins == ["https://polytrade.test", "https://assethero.test"]
    assert len(config.credential_key) == 32
    assert config.WALLET_SESSION_IDLE_SECONDS == 14_400
    with pytest.raises(ValidationError, match="configured together"):
        GatewayConfig.model_validate(
            environment(ASSETHERO_API_ISSUER="https://auth.assethero.test")
        )
    with pytest.raises(ValidationError, match="exact origins"):
        GatewayConfig.model_validate(environment(CORS_ORIGINS="https://polytrade.test/path"))


def test_credential_cipher_matches_the_existing_envelope_shape() -> None:
    cipher = CredentialCipher(bytes(range(32)))
    envelope = cipher.encrypt({"key": "api", "secret": "hidden"}, "session-id")
    assert envelope.startswith("v1.")
    assert len(envelope.split(".")) == 4
    assert cipher.decrypt(envelope, "session-id") == {"key": "api", "secret": "hidden"}
    with pytest.raises(InvalidTag):
        cipher.decrypt(envelope, "different-session")


async def test_ttl_cache_coalesces_loads_and_tracks_negative_entries() -> None:
    cache = TtlCache(max_entries=2)
    calls = 0

    async def load() -> dict[str, int]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"value": calls}

    left, right = await asyncio.gather(
        cache.load("same", 1_000, load), cache.load("same", 1_000, load)
    )
    assert left == right == {"value": 1}
    assert calls == 1
    cache.mark_missing("notfound:item", 1_000)
    assert cache.is_known_missing("notfound:item")
    assert cache.stats().hits == 1


class StaticJwks(CachedJwks):
    def __init__(self, key) -> None:
        self._key = key

    async def key(self, kid: str):
        assert kid == "key-1"
        return self._key


async def test_jwt_verifier_enforces_issuer_scope_and_lifetime() -> None:
    private = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    public = RSAAlgorithm.from_jwk(RSAAlgorithm.to_jwk(private.public_key()))
    verifier = JwtVerifier(
        [
            IssuerConfig(
                name="assethero",
                issuer="https://auth.assethero.test",
                audience="polytrade",
                jwks=StaticJwks(public),
                max_lifetime_seconds=300,
                require_scope_claim=True,
            )
        ]
    )
    now = datetime.now(UTC)
    claims = {
        "iss": "https://auth.assethero.test",
        "aud": "polytrade",
        "sub": "user-1",
        "jti": "token-1",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "scope": "research trade",
    }
    token = encode(claims, private, algorithm="RS256", headers={"kid": "key-1"})
    principal = await verifier.verify_authorization(f"Bearer {token}", "trade")
    assert principal.id == "assethero:user-1"
    with pytest.raises(AppError, match="Missing admin scope"):
        await verifier.verify(token, "admin")


IDENTITY = {
    "conditionId": "0xcondition",
    "tokenId": "123",
    "marketQuestion": "Will pricing remain deterministic?",
    "outcome": "Yes",
}
OBSERVED_AT = "2026-08-03T00:00:00.000Z"


def request(side: str, shares: str) -> PaperQuoteRequest:
    return PaperQuoteRequest(conditionId="0xcondition", tokenId="123", side=side, shares=shares)


def test_paper_pricing_sweeps_each_book_level_deterministically() -> None:
    quote = quote_paper_order(
        request("BUY", "4"),
        IDENTITY,
        {
            "bids": [{"price": "0.39", "size": "20"}],
            "asks": [{"price": "0.50", "size": "3"}, {"price": "0.40", "size": "2"}],
            "observedAt": OBSERVED_AT,
        },
        "0.04",
    )
    assert quote.averagePrice == "0.450000"
    assert quote.limitPrice == "0.500000"
    assert quote.grossNotional == "1.800000"
    assert quote.fee == "0.03920"
    assert quote.cashEffect == "-1.839200"


def test_paper_pricing_sorts_sells_and_distinguishes_failures() -> None:
    book = {
        "bids": [
            {"price": "0.30", "size": "10"},
            {"price": "0.45", "size": "2"},
            {"price": "0.40", "size": "5"},
        ],
        "asks": [],
        "observedAt": OBSERVED_AT,
    }
    quote = quote_paper_order(request("SELL", "4"), IDENTITY, book, "0")
    assert quote.averagePrice == "0.425000"
    assert quote.limitPrice == "0.400000"
    assert best_bid_from_order_book(book) == {"price": "0.450000", "observedAt": OBSERVED_AT}
    assert paper_liquidation_value("10", "0.50", "0.04") == "4.900000"

    asks = {
        "bids": [],
        "asks": [{"price": "0.40", "size": "2"}, {"price": "0.50", "size": "3"}],
        "observedAt": OBSERVED_AT,
    }
    with pytest.raises(PaperPricingError) as moved:
        quote_paper_order(request("BUY", "4"), IDENTITY, asks, "0", "0.45")
    assert moved.value.reason == "PRICE_MOVED"
    with pytest.raises(PaperPricingError) as shallow:
        quote_paper_order(request("BUY", "6"), IDENTITY, asks, "0")
    assert shallow.value.reason == "INSUFFICIENT_LIQUIDITY"
