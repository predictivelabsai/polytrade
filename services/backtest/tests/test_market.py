from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx

from polytrade_backtest.config import get_settings
from polytrade_backtest.engine import PriceObservation
from polytrade_backtest.market import (
    MarketDataError,
    PolymarketHistoryClient,
    decode_dataset,
    validate_dataset_history,
)
from polytrade_backtest.schemas import BreakoutBacktestConfig, MomentumBacktestConfig


def market_payload(*, closed: bool = True, start_date: str = "2026-05-01T00:00:00Z", **overrides):
    return {
        "conditionId": "condition-1",
        "question": "Will the test resolve YES?",
        "closed": closed,
        "acceptingOrders": not closed,
        "enableOrderBook": True,
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["1","0"]',
        "clobTokenIds": '["101","202"]',
        "startDate": start_date,
        "closedTime": "2026-05-01T02:00:00Z",
        **overrides,
    }


def history(price: str):
    start = datetime(2026, 5, 1, tzinfo=UTC)
    return {
        "history": [
            {"t": int((start + timedelta(minutes=minute)).timestamp()), "p": price}
            for minute in range(121)
        ]
    }


def keyset_payload(*markets: dict) -> dict:
    return {"markets": list(markets)}


def test_history_validation_uses_the_selected_strategy_lookback() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    points = [
        PriceObservation(start + timedelta(minutes=minute), Decimal("0.5"))
        for minute in range(121)
    ]
    histories = {"YES": points, "NO": points}

    validate_dataset_history(histories, MomentumBacktestConfig())
    with pytest.raises(MarketDataError) as error:
        validate_dataset_history(histories, BreakoutBacktestConfig())
    assert error.value.code == "INSUFFICIENT_HISTORY"


@pytest.mark.asyncio
@respx.mock
async def test_fetches_validated_binary_dataset_and_round_trips() -> None:
    def market_response(request: httpx.Request) -> httpx.Response:
        assert request.url.params["condition_ids"] == "condition-1"
        assert request.url.params["closed"] == "true"
        assert request.url.params["limit"] == "2"
        return httpx.Response(200, json=keyset_payload(market_payload()))

    respx.get("https://gamma-api.polymarket.com/markets/keyset").mock(side_effect=market_response)
    respx.get("https://clob.polymarket.com/fee-rate").mock(
        return_value=httpx.Response(200, json={"base_fee": 400})
    )

    def history_response(request: httpx.Request) -> httpx.Response:
        asset_id = request.url.params["market"]
        assert request.url.params["fidelity"] == "1"
        assert int(request.url.params["startTs"]) < int(request.url.params["endTs"])
        return httpx.Response(200, json=history("0.4" if asset_id == "101" else "0.6"))

    respx.get("https://clob.polymarket.com/prices-history").mock(side_effect=history_response)
    async with httpx.AsyncClient() as client:
        dataset = await PolymarketHistoryClient(get_settings(), client).fetch_dataset(
            "condition-1", MomentumBacktestConfig()
        )
    restored = decode_dataset(dataset.payload)

    assert dataset.point_count == 242
    assert dataset.snapshot.fee_rate.as_tuple() == restored.snapshot.fee_rate.as_tuple()
    assert restored.dataset_hash == dataset.dataset_hash
    assert restored.snapshot.resolved_outcome == "YES"


@pytest.mark.asyncio
@respx.mock
async def test_rejects_unresolved_market_before_fetching_history() -> None:
    respx.get("https://gamma-api.polymarket.com/markets/keyset").mock(
        return_value=httpx.Response(200, json=keyset_payload(market_payload(closed=False)))
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(MarketDataError, match="resolved") as error:
            await PolymarketHistoryClient(get_settings(), client).fetch_dataset(
                "condition-1", MomentumBacktestConfig()
            )
    assert error.value.code == "MARKET_NOT_RESOLVED"


@pytest.mark.asyncio
@respx.mock
async def test_reports_not_found_for_empty_keyset_result() -> None:
    respx.get("https://gamma-api.polymarket.com/markets/keyset").mock(
        return_value=httpx.Response(200, json=keyset_payload())
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(MarketDataError) as error:
            await PolymarketHistoryClient(get_settings(), client).fetch_dataset(
                "condition-1", MomentumBacktestConfig()
            )
    assert error.value.code == "MARKET_NOT_FOUND"


@pytest.mark.asyncio
@respx.mock
async def test_chunks_one_minute_history_below_polymarket_interval_limit() -> None:
    observed_ranges: list[tuple[int, int]] = []

    def history_response(request: httpx.Request) -> httpx.Response:
        start_ts = int(request.url.params["startTs"])
        end_ts = int(request.url.params["endTs"])
        observed_ranges.append((start_ts, end_ts))
        assert end_ts - start_ts <= int(timedelta(days=14).total_seconds())
        return httpx.Response(200, json={"history": []})

    respx.get("https://clob.polymarket.com/prices-history").mock(side_effect=history_response)
    start = datetime(2026, 6, 1, tzinfo=UTC)
    end = start + timedelta(days=35)
    async with httpx.AsyncClient() as client:
        points = await PolymarketHistoryClient(get_settings(), client)._fetch_history(
            client, "101", start, end, 100_000
        )

    assert points == []
    assert len(observed_ranges) == 3
    assert observed_ranges[0][0] == int(start.timestamp())
    assert observed_ranges[-1][1] == int(end.timestamp())


@pytest.mark.asyncio
@respx.mock
async def test_rejects_pre_clob_v2_market() -> None:
    respx.get("https://gamma-api.polymarket.com/markets/keyset").mock(
        return_value=httpx.Response(
            200, json=keyset_payload(market_payload(start_date="2026-04-27T23:59:59Z"))
        )
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(MarketDataError) as error:
            await PolymarketHistoryClient(get_settings(), client).fetch_dataset(
                "condition-1", MomentumBacktestConfig()
            )
    assert error.value.code == "PRE_CLOB_V2_MARKET"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"enableOrderBook": False}, "NON_CLOB_MARKET"),
        (
            {
                "outcomes": '["Yes","No","Maybe"]',
                "outcomePrices": '["1","0","0"]',
                "clobTokenIds": '["101","202","303"]',
            },
            "NON_BINARY_MARKET",
        ),
        ({"outcomePrices": '["0.5","0.5"]'}, "AMBIGUOUS_RESOLUTION"),
    ],
)
@respx.mock
async def test_rejects_ineligible_market_shapes(override, code) -> None:
    respx.get("https://gamma-api.polymarket.com/markets/keyset").mock(
        return_value=httpx.Response(200, json=keyset_payload(market_payload(**override)))
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(MarketDataError) as error:
            await PolymarketHistoryClient(get_settings(), client).fetch_dataset(
                "condition-1", MomentumBacktestConfig()
            )
    assert error.value.code == code


@pytest.mark.asyncio
@respx.mock
async def test_rejects_missing_recorded_fee_rate() -> None:
    respx.get("https://gamma-api.polymarket.com/markets/keyset").mock(
        return_value=httpx.Response(200, json=keyset_payload(market_payload()))
    )
    respx.get("https://clob.polymarket.com/fee-rate").mock(
        return_value=httpx.Response(200, json={})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(MarketDataError) as error:
            await PolymarketHistoryClient(get_settings(), client).fetch_dataset(
                "condition-1", MomentumBacktestConfig()
            )
    assert error.value.code == "MISSING_FEE_RATE"


@pytest.mark.asyncio
@respx.mock
async def test_enforces_the_combined_normalized_point_limit() -> None:
    respx.get("https://gamma-api.polymarket.com/markets/keyset").mock(
        return_value=httpx.Response(200, json=keyset_payload(market_payload()))
    )
    respx.get("https://clob.polymarket.com/fee-rate").mock(
        return_value=httpx.Response(200, json={"base_fee": 400})
    )
    start = datetime(2026, 5, 1, tzinfo=UTC)
    oversized = {
        "history": [
            {"t": int((start + timedelta(seconds=index)).timestamp()), "p": "0.5"}
            for index in range(6_000)
        ]
    }
    respx.get("https://clob.polymarket.com/prices-history").mock(
        return_value=httpx.Response(200, json=oversized)
    )
    settings = get_settings().model_copy(update={"BACKTEST_MAX_POINTS": 10_000})
    async with httpx.AsyncClient() as client:
        with pytest.raises(MarketDataError) as error:
            await PolymarketHistoryClient(settings, client).fetch_dataset(
                "condition-1", MomentumBacktestConfig()
            )
    assert error.value.code == "DATASET_TOO_LARGE"
