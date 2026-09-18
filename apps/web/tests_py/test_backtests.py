from __future__ import annotations

import httpx
from test_workspace import app_for

RUN_ID = "33333333-3333-4333-8333-333333333333"
MARKET = {
    "conditionId": "condition-fed",
    "question": "Will the Fed hold rates?",
    "outcomes": ["Yes", "No"],
    "outcomePrices": ["0.71", "0.29"],
    "clobTokenIds": ["123", "456"],
    "active": False,
    "closed": True,
    "acceptingOrders": False,
    "enableOrderBook": True,
}


def run() -> dict:
    return {
        "runId": RUN_ID,
        "marketId": "condition-fed",
        "marketQuestion": "Will the Fed hold rates?",
        "status": "completed",
        "phase": "completed",
        "progress": 100,
        "config": {"strategy": "momentum_v1", "initialCapital": "10000", "positionSizePct": "0.1"},
        "createdAt": "2026-09-18T10:00:00Z",
    }


def test_backtest_library_renders_static_replay_without_scripts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/backtests":
            return httpx.Response(200, json={"items": [run()]})
        if request.url.path == f"/v1/backtests/{RUN_ID}":
            return httpx.Response(
                200,
                json={
                    "run": run(),
                    "result": {
                        "metrics": {
                            "returnPct": "4.2",
                            "pnl": "420",
                            "winRatePct": "68",
                            "tradeCount": 2,
                            "maxDrawdownPct": "1.4",
                            "exposurePct": "25",
                        },
                        "assumptions": ["Next-observation fills"],
                    },
                },
            )
        if request.url.path.endswith("/series"):
            return httpx.Response(200, json={"points": [{"equity": "10000"}, {"equity": "10420"}]})
        if request.url.path.endswith("/trades"):
            return httpx.Response(200, json={"items": [], "total": 0, "offset": 0, "limit": 50})
        raise AssertionError(request.url)

    response = app_for(handler).get("/backtests")
    assert response.status_code == 200
    assert "Will the Fed hold rates?" in response.text
    assert "Equity replay" in response.text
    assert "polyline" in response.text
    assert "Next-observation fills" in response.text
    assert "<script" not in response.text.lower()


def test_new_backtest_search_and_launch_are_native_forms() -> None:
    created = {"run": {"runId": RUN_ID}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/research/markets":
            return httpx.Response(200, json={"events": [{"markets": [MARKET]}]})
        if request.url.path == "/v1/backtests":
            assert request.method == "POST"
            return httpx.Response(200, json=created)
        raise AssertionError(request.url)

    client = app_for(handler)
    response = client.get("/backtests/new?query=fed")
    assert response.status_code == 200
    assert "Will the Fed hold rates?" in response.text
    assert 'action="/backtests/new"' in response.text
    response = client.post(
        "/backtests/new",
        data={
            "market": "eyJjb25kaXRpb25JZCI6ImNvbmRpdGlvbi1mZWQifQ",
            "strategy": "momentum_v1",
            "momentumWindowMinutes": "60",
            "momentumThreshold": "0.05",
            "initialCapital": "10000",
            "positionSizePct": "0.1",
            "takeProfit": "0.1",
            "stopLoss": "0.05",
            "maxHoldMinutes": "1440",
            "cooldownMinutes": "60",
            "slippage": "0.01",
            "maxFillDelayMinutes": "5",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/backtests/{RUN_ID}"
