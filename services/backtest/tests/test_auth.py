from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from polytrade_backtest.auth import TokenVerifier
from polytrade_backtest.config import BacktestSettings, get_settings


def mint(private_key, *, issuer: str, scope: str, lifetime: int = 300) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": issuer,
            "aud": "polytrade",
            "sub": "researcher-1",
            "iat": now,
            "exp": now + timedelta(seconds=lifetime),
            "jti": "backtest-token-1",
            "scope": scope,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "backtest-key"},
    )


@pytest.mark.asyncio
async def test_assethero_research_token_is_namespaced() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = TokenVerifier(get_settings())
    issuer = "https://auth.assethero.test"
    verifier._clients[issuer].get_signing_key_from_jwt = lambda _token: SimpleNamespace(
        key=private_key.public_key()
    )

    principal = await verifier.verify(
        mint(private_key, issuer=issuer, scope="research trade"), "research"
    )

    assert principal.identity == "assethero:researcher-1"
    assert principal.scopes == ("research", "trade")


@pytest.mark.asyncio
async def test_missing_scope_and_excessive_assethero_lifetime_fail_closed() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = TokenVerifier(get_settings())
    issuer = "https://auth.assethero.test"
    verifier._clients[issuer].get_signing_key_from_jwt = lambda _token: SimpleNamespace(
        key=private_key.public_key()
    )

    with pytest.raises(PermissionError, match="research"):
        await verifier.verify(mint(private_key, issuer=issuer, scope="trade"), "research")
    with pytest.raises(ValueError, match="lifetime"):
        await verifier.verify(
            mint(private_key, issuer=issuer, scope="research", lifetime=301), "research"
        )


def test_assethero_api_trust_is_optional_but_requires_a_complete_pair() -> None:
    settings = get_settings()
    assert settings.BACKTEST_MAX_ACTIVE_RUNS_PER_OWNER == 10
    values = settings.model_dump()
    standalone = BacktestSettings.model_validate(
        {
            **values,
            "ASSETHERO_API_ISSUER": None,
            "ASSETHERO_API_JWKS_URL": None,
        }
    )
    verifier = TokenVerifier(standalone)
    assert set(verifier.issuers) == {"https://clerk.test"}

    with pytest.raises(ValueError, match="configured together"):
        BacktestSettings.model_validate(
            {
                **values,
                "ASSETHERO_API_JWKS_URL": None,
            }
        )
