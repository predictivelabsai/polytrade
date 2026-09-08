"""Tests for the template-stats proxy-backtest sweep script."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from polytrade_backtest.engine import PriceObservation
from polytrade_backtest.market import MarketSnapshot, build_dataset
from polytrade_backtest.schemas import BacktestMetrics, parse_backtest_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import template_stats_sweep as sweep  # noqa: E402

CONDITION_ID_A = "0x" + "a" * 40
CONDITION_ID_B = "0x" + "b" * 40
CONDITION_ID_C = "0x" + "c" * 40


def _gamma_market(condition_id: str = CONDITION_ID_A, **overrides: object) -> dict[str, object]:
    market: dict[str, object] = {
        "conditionId": condition_id,
        "question": "Will the Fed hold rates in September?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["1", "0"]',
        "clobTokenIds": "[111, 222]",
        "closed": True,
        "acceptingOrders": False,
        "enableOrderBook": True,
        "volume": "1000.50",
        "startDate": "2026-05-01T00:00:00Z",
        "closedTime": "2026-06-01T00:00:00Z",
    }
    market.update(overrides)
    return market


def _metrics(
    return_pct: str, win_rate_pct: str, trade_count: int, max_drawdown_pct: str
) -> BacktestMetrics:
    return BacktestMetrics(
        initial_capital="10000",
        final_equity="10000",
        pnl="0",
        return_pct=return_pct,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=trade_count,
        win_rate_pct=win_rate_pct,
        average_holding_seconds="0",
        exposure_pct="0",
        fees="0",
        skipped_signals=0,
        yes_buy_hold_return_pct="0",
        no_buy_hold_return_pct="0",
    )


def _outcome(
    condition_id: str,
    metrics: BacktestMetrics | None,
    error_code: str | None = None,
) -> sweep.SweepOutcome:
    return sweep.SweepOutcome(
        condition_id,
        "question",
        metrics,
        metrics.trade_count if metrics is not None else None,
        error_code,
    )


def test_collect_markets_filters_and_dedupes() -> None:
    shared = _gamma_market(CONDITION_ID_A)
    events = [
        {"markets": [shared, _gamma_market(CONDITION_ID_B, volume="5")]},
        # The same market again in another event must not be counted twice.
        {"markets": [shared]},
        # A non-event object and an ineligible market are dropped.
        {"markets": [_gamma_market(CONDITION_ID_C, closed=False)]},
        "not-an-event",
    ]
    markets = sweep.collect_markets(events)
    assert [market["conditionId"] for market in markets] == [CONDITION_ID_A, CONDITION_ID_B]
    assert markets[0]["volume"] == Decimal("1000.50")


def test_collect_markets_rejects_ambiguous_resolution() -> None:
    # A still-trading 50-50 market must not enter the universe.
    markets = sweep.collect_markets(
        [{"markets": [_gamma_market(outcomePrices='["0.5", "0.5"]')]}]
    )
    assert markets == []


def test_rank_and_cap_orders_by_volume_then_condition_id() -> None:
    # rank_and_cap runs on eligible_market's normalized shape, so run the raw
    # Gamma fixtures through it exactly as collect_markets would.
    markets = [
        market
        for market in (
            sweep.eligible_market(_gamma_market(condition_id, volume=volume))
            for condition_id, volume in [
                (CONDITION_ID_C, "10"),
                (CONDITION_ID_B, "20"),
                (CONDITION_ID_A, "20"),
                ("0x" + "d" * 40, "99"),
            ]
        )
        if market is not None
    ]
    ranked = sweep.rank_and_cap(markets, 3)
    assert [market["conditionId"] for market in ranked] == [
        "0x" + "d" * 40,
        CONDITION_ID_A,
        CONDITION_ID_B,
    ]


def test_aggregate_template_weights_win_rate_by_trades_and_ignores_zero_trade_runs() -> None:
    outcomes = [
        _outcome(CONDITION_ID_A, _metrics("10.00", "50.00", 3, "2.00")),
        _outcome(CONDITION_ID_B, _metrics("-2.50", "100.00", 1, "1.00")),
        # A market the strategy never traded: excluded from the rates, but its
        # zero trades still count toward the total volume of evidence.
        _outcome(CONDITION_ID_C, _metrics("999.00", "0.00", 0, "0.00")),
    ]
    aggregate = sweep.aggregate_template(outcomes)
    assert aggregate == {
        "returnPct": "3.75",
        "winRatePct": "62.5",
        "tradeCount": 4,
        "maxDrawdownPct": "2.00",
        "marketsWithTrades": 2,
        "successfulRuns": 3,
    }


def test_aggregate_template_returns_none_without_qualifying_runs() -> None:
    outcomes = [
        _outcome(CONDITION_ID_A, _metrics("10.00", "50.00", 0, "0.00")),
        _outcome(CONDITION_ID_B, None, error_code="INSUFFICIENT_HISTORY"),
    ]
    assert sweep.aggregate_template(outcomes) is None


def test_aggregate_template_rounds_negative_mean_half_up() -> None:
    # -2.355 and 0 average to -1.1775, which rounds away from zero to -1.18.
    outcomes = [
        _outcome(CONDITION_ID_A, _metrics("-2.355", "0.00", 1, "1.00")),
        _outcome(CONDITION_ID_B, _metrics("0.00", "0.00", 1, "1.00")),
    ]
    assert sweep.aggregate_template(outcomes)["returnPct"] == "-1.18"


def test_build_basis_fits_the_contracts_schema_cap() -> None:
    basis = sweep.build_basis("mean_reversion_v1", 25)
    assert basis.startswith("Proxy backtest: mean_reversion_v1 engine defaults on 25 resolved")
    assert len(basis) <= 200


class _RecordingClient:
    def __init__(self, dataset: object) -> None:
        self.dataset = dataset
        self.calls = 0

    async def fetch_dataset(self, condition_id: str, config: object) -> object:
        del condition_id, config
        self.calls += 1
        return self.dataset


@pytest.mark.asyncio
async def test_fetch_dataset_cached_round_trips_through_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    closed = datetime(2026, 6, 1, tzinfo=UTC)
    dataset = build_dataset(
        MarketSnapshot(
            condition_id=CONDITION_ID_A,
            question="Will the Fed hold rates in September?",
            yes_token_id="111",  # noqa: S106 - test fixture token id, not a secret
            no_token_id="222",  # noqa: S106 - test fixture token id, not a secret
            resolved_outcome="YES",
            fee_rate=Decimal("0"),
            start_at=start,
            closed_at=closed,
        ),
        {
            "YES": [
                PriceObservation(start, Decimal("0.40")),
                PriceObservation(closed, Decimal("1.00")),
            ],
            "NO": [
                PriceObservation(start, Decimal("0.60")),
                PriceObservation(closed, Decimal("0.00")),
            ],
        },
        request_start_at=None,
        request_end_at=None,
    )
    client = _RecordingClient(dataset)
    monkeypatch.setattr(sweep, "PolymarketHistoryClient", lambda settings, http_client: client)

    config = parse_backtest_config({"strategy": "momentum_v1"})
    first, from_cache = await sweep.fetch_dataset_cached(
        None, sweep.sweep_settings(), CONDITION_ID_A, config, tmp_path
    )
    assert from_cache is False
    assert client.calls == 1
    assert (tmp_path / f"{CONDITION_ID_A}.json.gz").exists()

    second, from_cache = await sweep.fetch_dataset_cached(
        None, sweep.sweep_settings(), CONDITION_ID_A, config, tmp_path
    )
    assert from_cache is True
    assert client.calls == 1
    assert second.snapshot == dataset.snapshot
    assert second.histories == dataset.histories


def test_universe_document_round_trip(tmp_path: Path) -> None:
    normalized = sweep.eligible_market(_gamma_market(CONDITION_ID_A))
    assert normalized is not None
    universe = {"base-rate-divergence": [normalized]}
    document = sweep.universe_document(universe)
    path = tmp_path / "universe.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    loaded = sweep.load_universe(path)
    # The manifest covers every template seed; templates without markets load
    # as empty lists so a sweep re-run can target any of them.
    assert loaded == {template["id"]: template["markets"] for template in document["templates"]}
    assert document["templates"][0]["searchQuery"] == "fed decision"


def test_template_universe_matches_the_contracts_constant() -> None:
    # Guards against the Python-side mirror drifting from
    # strategyTemplates in packages/contracts/src/index.ts.
    observed = [
        (seed.template_id, seed.search_query, seed.strategy) for seed in sweep.TEMPLATE_UNIVERSE
    ]
    assert observed == [
        ("base-rate-divergence", "fed decision", "mean_reversion_v1"),
        ("longshot-fade", "election winner", "momentum_v1"),
        ("ev-sniping", "crypto above", "mean_reversion_v1"),
        ("overreaction-fade", "court ruling", "mean_reversion_v1"),
        ("resolution-grinder", "confirmation vote", "breakout_v1"),
    ]