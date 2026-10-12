import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from langchain_core.messages import ToolMessage

from polytrade_agent.context import AgentRunContext
from polytrade_agent.schemas import ExperimentReference
from polytrade_agent.server import _public_thread_items, _tool_messages
from polytrade_agent.tools import (
    get_my_experiment,
    start_polymarket_experiment,
    walk_forward_my_experiment,
)

ID = "11111111-1111-4111-8111-111111111111"


def runtime():
    return SimpleNamespace(
        context=AgentRunContext(
            principal_id="clerk:test", scopes=("research",), gateway_bearer="delegated-test-token"
        ),
        tool_call_id="experiment-tool-call",
    )


def envelope(mode="grid"):
    return {
        "experiment": {
            "experimentId": ID,
            "request": {"mode": mode, "marketIds": ["market"]},
            "status": "queued",
            "totalSimulations": 18,
            "completedSimulations": 0,
            "createdAt": "2026-05-01T00:00:00Z",
        }
    }


@pytest.mark.asyncio
async def test_experiment_tool_queues_whole_grid_and_delegates_only_request_auth():
    grid = [
        {
            "baseConfig": {"strategy": "momentum_v1"},
            "parameters": {
                "momentumThreshold": ["0.03", "0.05", "0.07"],
                "takeProfit": ["0.01", "0.015"],
                "maxHoldMinutes": [1440, 2880, 4320],
            },
        }
    ]
    with respx.mock:
        route = respx.post("http://localhost:8100/v1/backtests/experiments").mock(
            return_value=httpx.Response(202, json=envelope())
        )
        output = await start_polymarket_experiment.coroutine(
            market_ids=["market"], strategies=grid, runtime=runtime()
        )
    sent = json.loads(route.calls.last.request.content)
    assert sent["strategies"] == grid
    assert route.call_count == 1
    assert route.calls.last.request.headers["Authorization"] == "Bearer delegated-test-token"
    assert route.calls.last.request.headers["Idempotency-Key"] == "agent:experiment-tool-call"
    assert "delegated-test-token" not in output
    assert ExperimentReference.model_validate_json(output).total_simulations == 18


@pytest.mark.asyncio
async def test_followup_reuses_original_experiment_and_surfaces_capacity_error():
    with respx.mock:
        route = respx.post(
            f"http://localhost:8100/v1/backtests/experiments/{ID}/walk-forward"
        ).mock(return_value=httpx.Response(202, json=envelope("walk_forward")))
        result = await walk_forward_my_experiment.coroutine(experiment_id=ID, runtime=runtime())
        assert json.loads(result)["mode"] == "walk_forward"
        assert json.loads(route.calls.last.request.content)["folds"] == 5
        respx.post("http://localhost:8100/v1/backtests/experiments").mock(
            return_value=httpx.Response(
                422, json={"detail": "Experiment needs 1800 simulations; the limit is 1000"}
            )
        )
        with pytest.raises(ValueError, match="1800.*1000"):
            await start_polymarket_experiment.coroutine(market_ids=["market"], runtime=runtime())


def test_experiments_are_typed_thread_items_only_for_successful_creation_tools():
    reference = ExperimentReference(
        experiment_id=ID,
        mode="grid",
        status="queued",
        market_ids=["market"],
        total_simulations=18,
        completed_simulations=0,
        created_at="2026-05-01T00:00:00Z",
    )
    success = ToolMessage(
        name="start_polymarket_experiment",
        tool_call_id="experiment-tool-call",
        content=reference.model_dump_json(by_alias=True),
    )
    untrusted = ToolMessage(
        name="search_polymarket_markets", tool_call_id="external", content=success.content
    )
    failed = success.model_copy(update={"status": "error"})
    items = _public_thread_items([success, untrusted, failed])
    assert len(items) == 1
    assert items[0].kind == "experiment"
    assert items[0].experiment == reference
    assert list(_tool_messages({"messages": [success, untrusted, failed]})) == [success]


@pytest.mark.asyncio
async def test_results_tool_keeps_fold_metrics_and_limits_large_curves():
    result = {
        **envelope(),
        "result": {
            "markets": [
                {
                    "ranking": list(range(18)),
                    "series": [1, 2, 3],
                    "folds": [{"fold": 1, "testMetrics": {"returnPct": "-1"}}],
                }
            ]
        },
    }
    with respx.mock:
        respx.get(f"http://localhost:8100/v1/backtests/experiments/{ID}").mock(
            return_value=httpx.Response(200, json=result)
        )
        output = json.loads(await get_my_experiment.coroutine(experiment_id=ID, runtime=runtime()))
    market = output["result"]["markets"][0]
    assert market["candidateCount"] == 18
    assert len(market["ranking"]) == 10
    assert "series" not in market
    assert market["folds"][0]["testMetrics"]["returnPct"] == "-1"


@pytest.mark.asyncio
async def test_results_pages_make_later_markets_candidates_and_folds_accessible():
    result = {
        **envelope(),
        "result": {
            "markets": [
                {
                    "marketId": f"market-{i}",
                    "ranking": list(range(18)),
                    "series": [],
                    "folds": [{"fold": index} for index in range(1, 11)],
                }
                for i in range(7)
            ]
        },
    }
    with respx.mock:
        respx.get(f"http://localhost:8100/v1/backtests/experiments/{ID}").mock(
            return_value=httpx.Response(200, json=result)
        )
        output = json.loads(
            await get_my_experiment.coroutine(
                experiment_id=ID,
                runtime=runtime(),
                market_offset=6,
                candidate_offset=10,
                fold_offset=5,
            )
        )
    assert output["result"]["marketCount"] == 7
    assert output["result"]["nextMarketOffset"] is None
    market = output["result"]["markets"][0]
    assert market["marketId"] == "market-6"
    assert market["ranking"] == list(range(10, 18))
    assert market["nextCandidateOffset"] is None
    assert [fold["fold"] for fold in market["folds"]] == [6, 7, 8, 9, 10]
    assert market["nextFoldOffset"] is None
