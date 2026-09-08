"""Proxy-backtest sweep that produces the strategy-template card stats.

Runs each template's ``backtestHint.strategy`` engine (momentum / mean-reversion
/ breakout defaults) over resolved CLOB-v2 markets found through the same Gamma
search the gateway uses, and aggregates the per-run metrics into the four
numbers shown on the /templates cards. The numbers land in the committed
contracts constant (``packages/contracts/src/index.ts``); refreshing them is a
deliberate re-run:

    # 1. Pin the market set (commit the manifest it prints to)
    uv run --project services/backtest python scripts/template_stats_sweep.py \
        --print-universe services/backtest/scripts/template_stats_universe.json

    # 2. Run the sweep against the pinned universe
    uv run --project services/backtest python scripts/template_stats_sweep.py \
        --universe services/backtest/scripts/template_stats_universe.json

The manifest makes the stats reproducible even though Gamma search results
drift. Datasets are cached under .template-sweep-cache/ (gitignored) so a
re-run only pays for markets it has not seen before.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import partial
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polytrade_backtest.config import BacktestSettings  # noqa: E402
from polytrade_backtest.engine import run_backtest  # noqa: E402
from polytrade_backtest.market import (  # noqa: E402
    CLOB_V2_START,
    MarketDataError,
    PolymarketHistoryClient,
    decode_dataset,
)
from polytrade_backtest.schemas import BacktestConfig, parse_backtest_config  # noqa: E402

RETRY_DELAYS_SECONDS = (5, 15, 45)
# Publishable floor: a template whose stats rest on fewer markets than this is
# reported but flagged, and its numbers must not be committed.
PUBLISHABLE_MIN_MARKETS = 5


@dataclass(frozen=True)
class TemplateSeed:
    template_id: str
    search_query: str
    strategy: str


# Mirrors strategyTemplates in packages/contracts/src/index.ts (this script
# cannot import TypeScript). Keep the two in sync: id, suggestedSearchQuery,
# backtestHint.strategy.
TEMPLATE_UNIVERSE: tuple[TemplateSeed, ...] = (
    TemplateSeed("base-rate-divergence", "fed decision", "mean_reversion_v1"),
    TemplateSeed("longshot-fade", "election winner", "momentum_v1"),
    TemplateSeed("ev-sniping", "crypto above", "mean_reversion_v1"),
    TemplateSeed("overreaction-fade", "court ruling", "mean_reversion_v1"),
    TemplateSeed("resolution-grinder", "confirmation vote", "breakout_v1"),
)


class SearchError(RuntimeError):
    pass


def sweep_settings() -> BacktestSettings:
    # Nothing in this script touches the database or auth, but BacktestSettings
    # requires these fields at construction. The dummies are never dialed.
    return BacktestSettings(
        DATABASE_URL="postgresql://sweep@localhost/sweep_unused",
        CORS_ORIGINS="http://localhost:5173",
        CLERK_ISSUER="https://clerk.invalid/sweep",
        CLERK_JWKS_URL="https://clerk.invalid/sweep/jwks",
    )


def _string_array(value: object) -> list[Any]:
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, list):
        return value
    raise ValueError("not an array")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def eligible_market(raw: Any) -> dict[str, Any] | None:
    """Loose pre-filter mirroring isBacktestEligibleMarket in contracts.

    Everything this checks is enforced again, authoritatively, by
    PolymarketHistoryClient.fetch_dataset — this only avoids wasted fetches.
    """
    if not isinstance(raw, dict):
        return None
    condition_id = raw.get("conditionId")
    if not isinstance(condition_id, str) or not condition_id:
        return None
    if raw.get("closed") is not True or raw.get("acceptingOrders") is not False:
        return None
    if raw.get("enableOrderBook") is not True:
        return None
    try:
        outcomes = [str(item).strip().upper() for item in _string_array(raw.get("outcomes"))]
        prices = [str(item) for item in _string_array(raw.get("outcomePrices"))]
        tokens = _string_array(raw.get("clobTokenIds"))
        volume = Decimal(str(raw.get("volume") or "0"))
        started_at = _parse_timestamp(raw.get("startDate") or raw.get("createdAt"))
    except (ValueError, TypeError, InvalidOperation, json.JSONDecodeError):
        return None
    if len(outcomes) != 2 or set(outcomes) != {"YES", "NO"} or len(tokens) != 2:
        return None
    if len(prices) != 2:
        return None
    try:
        resolution_prices = [Decimal(price) for price in prices]
    except InvalidOperation:
        return None
    winners = [price for price in resolution_prices if price == Decimal("1")]
    losers = [price for price in resolution_prices if price == Decimal("0")]
    if len(winners) != 1 or len(losers) != 1:
        return None
    if started_at < CLOB_V2_START:
        return None
    return {
        "conditionId": condition_id,
        "question": str(raw.get("question") or ""),
        "volume": volume,
        "startDate": started_at.isoformat(),
        "closedAt": str(raw.get("closedTime") or raw.get("endDate") or ""),
    }


def collect_markets(events: list[Any]) -> list[dict[str, Any]]:
    """Flatten Gamma search events into unique eligible markets."""
    markets: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("markets"), list):
            continue
        for raw in event["markets"]:
            market = eligible_market(raw)
            if market is not None and market["conditionId"] not in markets:
                markets[market["conditionId"]] = market
    return list(markets.values())


def rank_and_cap(markets: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Most-liquid first, conditionId as the deterministic tie-break.

    Volume is a Decimal from eligible_market but a JSON string once a saved
    universe manifest is loaded, so sort through float() to accept both.
    """
    ranked = sorted(
        markets, key=lambda market: (-float(market["volume"]), str(market["conditionId"]))
    )
    return ranked[:limit]


async def search_markets(
    client: httpx.AsyncClient,
    gamma_url: str,
    query: str,
    limit_per_type: int = 80,
) -> list[dict[str, Any]]:
    url = f"{gamma_url.rstrip('/')}/public-search"
    params = {
        "q": query,
        "limit_per_type": str(limit_per_type),
        "events_status": "closed",
        "search_profiles": "false",
    }
    response = await client.get(url, params=params)
    if response.status_code == 429 or response.status_code >= 500:
        raise SearchError(f"Gamma search returned {response.status_code}")
    if not response.is_success:
        raise SearchError(f"Gamma search rejected the query ({response.status_code})")
    payload = response.json()
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise SearchError("Gamma search returned malformed events")
    return collect_markets(events)


def cache_path(cache_dir: Path, condition_id: str) -> Path:
    return cache_dir / f"{condition_id}.json.gz"


async def fetch_dataset_cached(
    client: httpx.AsyncClient,
    settings: BacktestSettings,
    condition_id: str,
    config: BacktestConfig,
    cache_dir: Path | None,
) -> tuple[Any, bool]:
    """Returns (dataset, from_cache); the cache stores the dataset payload."""
    path = cache_path(cache_dir, condition_id) if cache_dir is not None else None
    if path is not None and path.exists():
        return decode_dataset(path.read_bytes()), True
    dataset = await PolymarketHistoryClient(settings, client).fetch_dataset(condition_id, config)
    if path is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(dataset.payload)
    return dataset, False


@dataclass(frozen=True)
class SweepOutcome:
    condition_id: str
    question: str
    metrics: Any | None
    trade_count: int | None
    error_code: str | None


def aggregate_template(outcomes: list[SweepOutcome]) -> dict[str, Any] | None:
    """The four card stats from a template's sweep.

    Rate aggregates (returnPct, winRatePct, maxDrawdownPct) come from runs with
    at least one trade; tradeCount sums every successful run so a market the
    strategy never traded still contributes its (zero) volume of evidence.
    """
    successful = [outcome for outcome in outcomes if outcome.metrics is not None]
    qualifying = [outcome for outcome in successful if (outcome.trade_count or 0) > 0]
    if not qualifying:
        return None
    mean_return = (
        sum((Decimal(outcome.metrics.return_pct) for outcome in qualifying), Decimal(0))
        / len(qualifying)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total_trades = sum(outcome.trade_count for outcome in qualifying)
    weighted_win_rate = (
        sum(
            (
                Decimal(outcome.metrics.win_rate_pct) * outcome.trade_count
                for outcome in qualifying
            ),
            Decimal(0),
        )
        / total_trades
    ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    worst_drawdown = max(
        Decimal(outcome.metrics.max_drawdown_pct) for outcome in qualifying
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "returnPct": str(mean_return),
        "winRatePct": str(weighted_win_rate),
        "tradeCount": sum(outcome.trade_count for outcome in successful),
        "maxDrawdownPct": str(worst_drawdown),
        "marketsWithTrades": len(qualifying),
        "successfulRuns": len(successful),
    }


def build_basis(strategy: str, markets_with_trades: int) -> str:
    text = (
        f"Proxy backtest: {strategy} engine defaults on {markets_with_trades} resolved "
        "CLOB-v2 markets, full lifespan to close, $10k virtual USDC. "
        "Deployed price band differs."
    )
    assert len(text) <= 200, "basis must fit the contracts schema's 200-char cap"
    return text


async def run_with_retries(operation, delays: tuple[float, ...]) -> Any:
    for attempt, delay in enumerate((0, *delays)):
        if attempt:
            await asyncio.sleep(delay)
        try:
            return await operation()
        except MarketDataError as exc:
            if not exc.retryable or attempt == len(delays):
                raise
    raise AssertionError("unreachable")


async def sweep_template(
    client: httpx.AsyncClient,
    settings: BacktestSettings,
    seed: TemplateSeed,
    markets: list[dict[str, Any]],
    cache_dir: Path | None,
    sleep_seconds: float,
) -> list[SweepOutcome]:
    config = parse_backtest_config({"strategy": seed.strategy})
    outcomes: list[SweepOutcome] = []
    for index, market in enumerate(markets):
        condition_id = market["conditionId"]
        try:
            dataset, from_cache = await run_with_retries(
                partial(fetch_dataset_cached, client, settings, condition_id, config, cache_dir),
                RETRY_DELAYS_SECONDS,
            )
        except MarketDataError as exc:
            print(f"  SKIP {condition_id[:14]}… {exc.code}", flush=True)
            outcomes.append(
                SweepOutcome(condition_id, market["question"], None, None, exc.code)
            )
            continue
        try:
            output = run_backtest(
                dataset.histories,
                resolved_outcome=dataset.snapshot.resolved_outcome,
                fee_rate=dataset.snapshot.fee_rate,
                config=config,
                settlement_at=dataset.snapshot.closed_at,
            )
        except (ValueError, ArithmeticError) as exc:
            print(f"  SKIP {condition_id[:14]}… ENGINE_ERROR {exc}", flush=True)
            outcomes.append(
                SweepOutcome(condition_id, market["question"], None, None, "ENGINE_ERROR")
            )
            continue
        metrics = output.metrics
        print(
            f"  ok   {condition_id[:14]}… trades={metrics.trade_count:>4} "
            f"ret={metrics.return_pct:>9}% wr={metrics.win_rate_pct:>6}% "
            f"dd={metrics.max_drawdown_pct:>6}%"
            f"{' (cached)' if from_cache else ''}",
            flush=True,
        )
        outcomes.append(
            SweepOutcome(
                condition_id,
                market["question"],
                metrics,
                metrics.trade_count,
                None,
            )
        )
        if index < len(markets) - 1:
            await asyncio.sleep(sleep_seconds)
    return outcomes


def load_universe(path: Path) -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    universe: dict[str, list[dict[str, Any]]] = {}
    for entry in payload.get("templates", []):
        universe[entry["id"]] = entry["markets"]
    return universe


def universe_document(universe: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "source": "Gamma /public-search, events_status=closed",
        "templates": [
            {
                "id": seed.template_id,
                "searchQuery": seed.search_query,
                "strategy": seed.strategy,
                "markets": [
                    {
                        "conditionId": market["conditionId"],
                        "question": market["question"],
                        # Volume arrives as a Decimal from eligible_market;
                        # the manifest must stay plain JSON.
                        "volume": str(market["volume"]),
                        "startDate": market["startDate"],
                        "closedAt": market["closedAt"],
                    }
                    for market in universe.get(seed.template_id, [])
                ],
            }
            for seed in TEMPLATE_UNIVERSE
        ],
    }


async def main_async(args: argparse.Namespace) -> int:
    settings = sweep_settings()
    gamma_url = str(settings.POLYMARKET_GAMMA_URL)
    selected = (
        [seed for seed in TEMPLATE_UNIVERSE if seed.template_id in set(args.templates)]
        if args.templates
        else list(TEMPLATE_UNIVERSE)
    )
    universe: dict[str, list[dict[str, Any]]] = {}
    if args.universe:
        universe = load_universe(args.universe)
    results: dict[str, Any] = {}
    async with httpx.AsyncClient(
        timeout=settings.POLYMARKET_REQUEST_TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"Accept": "application/json"},
    ) as client:
        for seed in selected:
            print(f"\n== {seed.template_id} ({seed.strategy}) ==", flush=True)
            if seed.template_id in universe:
                markets = rank_and_cap(universe[seed.template_id], args.limit)
                print(f"  {len(markets)} markets from universe manifest", flush=True)
            else:
                try:
                    markets = rank_and_cap(
                        await run_with_retries(
                            partial(search_markets, client, gamma_url, seed.search_query),
                            RETRY_DELAYS_SECONDS,
                        ),
                        args.limit,
                    )
                    print(f"  {len(markets)} eligible markets via Gamma search", flush=True)
                except (SearchError, MarketDataError) as exc:
                    print(f"  search failed: {exc}", flush=True)
                    results[seed.template_id] = {"error": str(exc)}
                    continue
            if not markets:
                results[seed.template_id] = {"error": "no eligible markets found"}
                continue
            cache_dir = None if args.no_cache else args.cache_dir
            outcomes = await sweep_template(
                client, settings, seed, markets, cache_dir, args.sleep
            )
            skipped: dict[str, int] = {}
            for outcome in outcomes:
                if outcome.error_code:
                    skipped[outcome.error_code] = skipped.get(outcome.error_code, 0) + 1
            aggregate = aggregate_template(outcomes)
            if aggregate is None:
                results[seed.template_id] = {"error": "no successful runs", "skipped": skipped}
                continue
            aggregate["basis"] = build_basis(seed.strategy, aggregate["marketsWithTrades"])
            aggregate["skipped"] = skipped
            results[seed.template_id] = aggregate

    report = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "templates": results,
    }
    print("\n=== RESULTS ===")
    print(json.dumps(report, indent=2))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.json_out}")
    for seed in selected:
        aggregate = results.get(seed.template_id, {})
        with_trades = aggregate.get("marketsWithTrades")
        if with_trades is not None and with_trades < PUBLISHABLE_MIN_MARKETS:
            print(
                f"WARNING: {seed.template_id} has {with_trades} markets "
                f"with trades — below the {PUBLISHABLE_MIN_MARKETS}-market publishable floor"
            )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_cache = Path(__file__).resolve().parents[1] / ".template-sweep-cache"
    parser = argparse.ArgumentParser(
        description="Proxy-backtest sweep for the strategy-template card stats",
    )
    parser.add_argument("--limit", type=int, default=25, help="markets per template (default 25)")
    parser.add_argument(
        "--template",
        action="append",
        dest="templates",
        choices=[seed.template_id for seed in TEMPLATE_UNIVERSE],
        help="restrict to one template (repeatable)",
    )
    parser.add_argument(
        "--universe",
        type=Path,
        help="load markets from a universe manifest instead of Gamma search",
    )
    parser.add_argument(
        "--print-universe",
        type=Path,
        help="run Gamma search and write the universe manifest, then exit",
    )
    parser.add_argument("--cache-dir", type=Path, default=default_cache)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.4, help="pause between runs, seconds")
    parser.add_argument("--json-out", type=Path, help="also write the results JSON to this path")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    if args.print_universe:
        asyncio.run(_print_universe(args))
        return 0
    return asyncio.run(main_async(args))


async def _print_universe(args: argparse.Namespace) -> None:
    settings = sweep_settings()
    gamma_url = str(settings.POLYMARKET_GAMMA_URL)
    selected = (
        [seed for seed in TEMPLATE_UNIVERSE if seed.template_id in set(args.templates)]
        if args.templates
        else list(TEMPLATE_UNIVERSE)
    )
    universe: dict[str, list[dict[str, Any]]] = {}
    async with httpx.AsyncClient(
        timeout=settings.POLYMARKET_REQUEST_TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"Accept": "application/json"},
    ) as client:
        for seed in selected:
            markets = await run_with_retries(
                partial(search_markets, client, gamma_url, seed.search_query),
                RETRY_DELAYS_SECONDS,
            )
            universe[seed.template_id] = rank_and_cap(markets, args.limit)
            print(f"{seed.template_id}: {len(markets)} eligible markets", flush=True)
            await asyncio.sleep(args.sleep)
    args.print_universe.parent.mkdir(parents=True, exist_ok=True)
    args.print_universe.write_text(
        json.dumps(universe_document(universe), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.print_universe}")


if __name__ == "__main__":
    sys.exit(main())