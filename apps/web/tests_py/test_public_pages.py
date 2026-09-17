from __future__ import annotations

import httpx
from starlette.testclient import TestClient

from polytrade_web.app import create_app
from polytrade_web.config import WebSettings

TOKEN = "a" * 32
NOW = "2026-09-01T00:00:00.000Z"


def record() -> dict:
    return {
        "profile": {"displayName": "Paper account", "startedAt": "2026-08-01T00:00:00.000Z"},
        "stats": {
            "initialCash": "10000.000000",
            "cash": "9500.000000",
            "equity": "9505.200000",
            "totalPnl": "-494.800000",
            "realizedPnl": "10.000000",
            "unrealizedPnl": "0.200000",
            "totalFees": "1.000000",
            "tradeCount": 2,
            "winRate": "100.00",
        },
        "equityCurve": [
            {"t": NOW, "equity": "9497.000000"},
            {"t": "2026-09-02T00:00:00.000Z", "equity": "9505.200000"},
        ],
        "positions": [
            {
                "marketQuestion": "Will the Fed hold rates?",
                "outcome": "Yes",
                "shares": "10.000000",
                "averageCost": "0.500000",
                "liquidationValue": "5.200000",
                "unrealizedPnl": "0.200000",
                "markStatus": "current",
            }
        ],
        "fills": [
            {
                "fillId": "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f",
                "kind": "BUY",
                "marketQuestion": "Will the Fed hold rates?",
                "outcome": "Yes",
                "shares": "10.000000",
                "averagePrice": "0.500000",
                "fee": "0.000000",
                "cashEffect": "-5.000000",
                "realizedPnl": "0.000000",
                "createdAt": NOW,
            }
        ],
        "observedAt": "2026-09-02T00:00:00.000Z",
    }


def app_for(handler) -> TestClient:
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(
        WebSettings(API_URL="https://api.polytrade.test", PUBLIC_ORIGIN="https://polytrade.test"),
        async_client,
    )
    return TestClient(app)


def test_templates_page_preserves_content_and_has_no_auth_or_scripts() -> None:
    client = app_for(lambda request: httpx.Response(500))
    response = client.get("/templates")
    assert response.status_code == 200
    assert "Start paper trading in two minutes" in response.text
    assert response.text.count('class="template-card"') == 5
    assert "Base-rate divergence" in response.text
    assert "/paper?template=ev-sniping" in response.text
    assert "<script" not in response.text.lower()


def test_track_record_is_server_rendered_and_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/public/track-records/{TOKEN}"
        return httpx.Response(200, json=record())

    response = app_for(handler).get(f"/u/{TOKEN}")
    assert response.status_code == 200
    assert "Paper account" in response.text
    assert "9,505.20 USDC" in response.text
    assert "Will the Fed hold rates?" in response.text
    assert "equity-curve-line" in response.text
    assert 'content="noindex"' in response.text
    assert "conditionId" not in response.text
    assert "tokenId" not in response.text
    assert "<script" not in response.text.lower()


def test_missing_and_malformed_track_records_have_the_same_safe_empty_state() -> None:
    missing = app_for(lambda request: httpx.Response(404, json={"error": {"code": "NOT_FOUND"}}))
    response = missing.get(f"/u/{TOKEN}")
    assert "This track record is not available" in response.text

    malformed = app_for(lambda request: httpx.Response(200, json={"profile": {}}))
    response = malformed.get(f"/u/{TOKEN}")
    assert "Track record could not be loaded" in response.text

    response = missing.get("/u/short")
    assert "This track record is not available" in response.text


def test_health_and_styles_are_served_without_node() -> None:
    client = app_for(lambda request: httpx.Response(500))
    assert client.get("/health").json() == {"status": "ok", "version": "4.0.0"}
    styles = client.get("/assets/styles.css")
    assert styles.status_code == 200
    assert ".template-landing-hero" in styles.text
    assert "fonts.googleapis.com" in styles.text
