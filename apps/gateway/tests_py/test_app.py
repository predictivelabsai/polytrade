from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from polytrade_gateway.app import create_app
from polytrade_gateway.config import GatewayConfig
from polytrade_gateway.types import Principal


class AllowVerifier:
    async def verify_authorization(self, _header, scope):
        return Principal("clerk:test-user", "clerk", "test-user", frozenset({scope}))


def config() -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "NODE_ENV": "test",
            "DATABASE_URL": "postgresql://localhost/polytrade",
            "CREDENTIALS_KEK_BASE64": base64.b64encode(bytes(32)).decode(),
            "CLERK_ISSUER": "https://clerk.test",
            "CLERK_JWKS_URL": "https://clerk.test/.well-known/jwks.json",
            "CORS_ORIGINS": "http://testserver",
        }
    )


def test_health_and_strategy_templates_are_python_routes() -> None:
    with TestClient(create_app(config(), verifier=AllowVerifier())) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["version"] == "4.0.0"
        templates = client.get("/v1/public/strategy-templates")
        assert templates.status_code == 200
        assert len(templates.json()["items"]) == 5


def test_paper_portfolio_is_scoped_to_authenticated_principal() -> None:
    with TestClient(create_app(config(), verifier=AllowVerifier())) as client:
        response = client.get("/v1/paper/portfolio", headers={"Authorization": "Bearer test"})
        assert response.status_code == 200
        assert response.json()["cash"] == "10000.000000"
