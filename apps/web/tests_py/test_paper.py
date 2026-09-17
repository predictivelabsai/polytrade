from __future__ import annotations

import httpx
from test_workspace import app_for

from polytrade_web.paper import encode_market

MARKET = {
    "conditionId": "0xcondition",
    "question": "Will the Fed hold rates?",
    "outcomes": ["Yes", "No"],
    "outcomePrices": ["0.71", "0.29"],
    "clobTokenIds": ["123", "456"],
    "minimumOrderSize": "1",
    "active": True,
    "closed": False,
    "acceptingOrders": True,
    "enableOrderBook": True,
}


def _portfolio() -> dict:
    return {
        "cash": "10000",
        "initialCash": "10000",
        "positionsValue": "0",
        "equity": "10000",
        "realizedPnl": "0",
        "unrealizedPnl": "0",
        "totalPnl": "0",
        "totalFees": "0",
        "positions": [],
        "warnings": [],
        "observedAt": "2026-09-18T10:00:00Z",
    }


def test_paper_page_renders_ledger_templates_and_script_free_ticket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/paper/portfolio":
            return httpx.Response(200, json=_portfolio())
        if request.url.path == "/v1/paper/fills":
            return httpx.Response(200, json={"items": [], "total": 0, "offset": 0, "limit": 20})
        if request.url.path == "/v1/paper/strategy":
            return httpx.Response(200, json={"strategy": None, "events": []})
        raise AssertionError(request.url)

    response = app_for(handler).get("/paper")
    assert response.status_code == 200
    assert "Paper trading" in response.text
    assert "Base-rate divergence" in response.text
    assert "Paper only" in response.text
    assert "Search active Polymarket markets" in response.text
    assert "<script" not in response.text.lower()


def test_paper_search_and_quote_are_native_form_flows() -> None:
    quote = {
        "conditionId": "0xcondition",
        "tokenId": "123",
        "marketQuestion": MARKET["question"],
        "outcome": "Yes",
        "side": "BUY",
        "shares": "10",
        "averagePrice": "0.71",
        "limitPrice": "0.72",
        "grossNotional": "7.10",
        "feeRate": "0.02",
        "fee": "0.14",
        "cashEffect": "-7.24",
        "observedAt": "2026-09-18T10:00:00Z",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/paper/portfolio":
            return httpx.Response(200, json=_portfolio())
        if request.url.path == "/v1/paper/fills":
            return httpx.Response(200, json={"items": [], "total": 0, "offset": 0, "limit": 20})
        if request.url.path == "/v1/paper/strategy":
            return httpx.Response(200, json={"strategy": None, "events": []})
        if request.url.path == "/v1/research/markets":
            return httpx.Response(200, json={"events": [{"markets": [MARKET]}]})
        if request.url.path == "/v1/paper/quotes":
            return httpx.Response(200, json=quote)
        raise AssertionError(request.url)

    client = app_for(handler)
    response = client.get("/paper?query=fed")
    assert "Will the Fed hold rates?" in response.text
    encoded = encode_market(MARKET)
    response = client.post(
        "/paper/quote",
        data={"market": encoded, "token_id": "123", "side": "BUY", "shares": "10"},
    )
    assert response.status_code == 200
    assert "Preview ready" in response.text
    assert "Confirm paper fill" in response.text
    assert "<script" not in response.text.lower()
