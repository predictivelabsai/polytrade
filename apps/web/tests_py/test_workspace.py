from __future__ import annotations

import json
from uuid import UUID

import httpx
from starlette.testclient import TestClient

from polytrade_web.app import create_app
from polytrade_web.config import WebSettings

THREAD_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = "22222222-2222-4222-8222-222222222222"
NOW = "2026-09-18T10:00:00.000Z"


def app_for(handler) -> TestClient:
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(WebSettings(API_URL="https://api.polytrade.test"), async_client)
    client = TestClient(app)
    client.cookies.set("polytrade_access_token", "test-token")
    return client


def assert_authorized(request: httpx.Request) -> None:
    assert request.headers["authorization"] == "Bearer test-token"


def test_chat_workspace_is_server_rendered_without_scripts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert_authorized(request)
        if request.url.path == "/v1/agent/threads":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "threadId": THREAD_ID,
                            "title": "Fed market research",
                            "updatedAt": NOW,
                        }
                    ]
                },
            )
        if request.url.path == "/v1/agent/usage":
            return httpx.Response(200, json={"used": 3, "limit": 20})
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "threadId": THREAD_ID,
                    "items": [
                        {"kind": "message", "id": "one", "role": "user", "text": "Hold?"},
                        {
                            "kind": "message",
                            "id": "two",
                            "role": "assistant",
                            "text": "The market implies a 71% chance.",
                        },
                    ],
                },
            )
        raise AssertionError(request.url)

    response = app_for(handler).get(f"/chat/{THREAD_ID}")
    assert response.status_code == 200
    assert "Fed market research" in response.text
    assert "The market implies a 71% chance." in response.text
    assert 'action="/chat/11111111-1111-4111-8111-111111111111"' in response.text
    assert "3 / 20 queries used today" in response.text
    assert "<script" not in response.text.lower()


def test_chat_post_creates_thread_consumes_stream_and_redirects() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert_authorized(request)
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/agent/threads":
            assert request.method == "POST"
            return httpx.Response(200, json={"threadId": THREAD_ID})
        if request.url.path.endswith("/runs/stream"):
            assert request.headers["accept"] == "text/event-stream"
            assert json.loads(request.content) == {
                "message": "Research the Fed",
                "runtime": "hermes",
            }
            return httpx.Response(200, text="event: run.completed\ndata: {}\n\n")
        raise AssertionError(request.url)

    response = app_for(handler).post(
        "/chat/new",
        data={"message": "Research the Fed", "runtime": "hermes"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/chat/{THREAD_ID}"
    assert calls == [
        ("POST", "/v1/agent/threads"),
        ("POST", f"/v1/agent/threads/{THREAD_ID}/runs/stream"),
    ]


def test_trades_renders_account_tables_and_cancel_form() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert_authorized(request)
        if request.url.path == "/v1/wallet-sessions/current":
            return httpx.Response(
                200,
                json={"sessionId": SESSION_ID, "walletAddress": "0x" + "1" * 40},
            )
        if request.url.path == "/v1/account/overview":
            return httpx.Response(
                200,
                json={
                    "positions": [
                        {
                            "positionId": "position-1",
                            "marketTitle": "Will the Fed hold?",
                            "outcome": "Yes",
                            "size": "10.0",
                            "averagePrice": "0.65",
                            "currentPrice": "0.71",
                            "currentValue": "7.10",
                            "cashPnl": "0.60",
                            "percentPnl": "9.23%",
                            "redeemable": False,
                        }
                    ],
                    "openOrders": [
                        {
                            "orderId": "order-1",
                            "marketId": "market-1",
                            "outcome": "Yes",
                            "side": "BUY",
                            "remainingSize": "3",
                            "matchedSize": "2",
                            "price": "0.68",
                            "orderType": "GTC",
                            "createdAt": NOW,
                        }
                    ],
                    "fills": [],
                    "observedAt": NOW,
                },
            )
        raise AssertionError(request.url)

    response = app_for(handler).get("/trades")
    assert response.status_code == 200
    assert "Will the Fed hold?" in response.text
    assert "0.600000" not in response.text
    assert 'action="/trades/cancel"' in response.text
    assert "<script" not in response.text.lower()


def test_settings_and_unauthenticated_state_are_safe_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert_authorized(request)
        if request.url.path == "/v1/wallet-sessions/current":
            return httpx.Response(404, json={"error": {"message": "No session"}})
        if request.url.path == "/v1/alerts/channels":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "channelId": str(UUID(int=3)),
                            "kind": "discord",
                            "label": "Trading desk",
                            "targetHint": "…/desk",
                            "enabled": True,
                        }
                    ]
                },
            )
        raise AssertionError(request.url)

    client = app_for(handler)
    response = client.get("/settings")
    assert response.status_code == 200
    assert "Server-rendered / script-free" in response.text
    assert "Trading desk" in response.text
    assert "<script" not in response.text.lower()

    client.cookies.clear()
    response = client.get("/chat/new")
    assert response.status_code == 200
    assert "Sign in required" in response.text
    assert "<script" not in response.text.lower()
