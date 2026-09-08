from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .config import BacktestSettings
from .engine import PriceObservation
from .schemas import BacktestConfig, Outcome, strategy_lookback_minutes

CLOB_V2_START = datetime(2026, 4, 28, tzinfo=UTC)
# Polymarket rejects one-minute price-history windows longer than roughly two weeks.
# Keep the window below that boundary so a full market lifespan can be paged safely.
HISTORY_CHUNK = timedelta(days=14)


class MarketDataError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable


@dataclass(frozen=True)
class MarketSnapshot:
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    resolved_outcome: Outcome
    fee_rate: Decimal
    start_at: datetime
    closed_at: datetime


@dataclass(frozen=True)
class HistoricalDataset:
    snapshot: MarketSnapshot
    histories: dict[Outcome, list[PriceObservation]]
    dataset_hash: str
    payload: bytes
    point_count: int
    start_at: datetime
    end_at: datetime
    request_start_at: datetime | None
    request_end_at: datetime | None

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "conditionId": self.snapshot.condition_id,
            "question": self.snapshot.question,
            "yesTokenId": self.snapshot.yes_token_id,
            "noTokenId": self.snapshot.no_token_id,
            "resolvedOutcome": self.snapshot.resolved_outcome,
            "feeRate": _decimal(self.snapshot.fee_rate),
            "marketStartAt": self.snapshot.start_at.isoformat(),
            "closedAt": self.snapshot.closed_at.isoformat(),
            "requestStartAt": self.request_start_at.isoformat() if self.request_start_at else None,
            "requestEndAt": self.request_end_at.isoformat() if self.request_end_at else None,
            "source": "polymarket-clob-prices-history",
            "fidelityMinutes": 1,
        }


class PolymarketHistoryClient:
    def __init__(
        self,
        settings: BacktestSettings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client

    async def fetch_dataset(
        self,
        condition_id: str,
        config: BacktestConfig,
    ) -> HistoricalDataset:
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self.settings.POLYMARKET_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
        try:
            snapshot = await self._fetch_market(client, condition_id)
            start_at = max(snapshot.start_at, config.start_at or snapshot.start_at)
            end_at = min(snapshot.closed_at, config.end_at or snapshot.closed_at)
            if start_at >= end_at:
                raise MarketDataError(
                    "INVALID_DATE_RANGE", "The requested range does not overlap market history"
                )
            yes_history = await self._fetch_history(
                client,
                snapshot.yes_token_id,
                start_at,
                end_at,
                self.settings.BACKTEST_MAX_POINTS,
            )
            remaining_points = self.settings.BACKTEST_MAX_POINTS - len(yes_history)
            if remaining_points <= 0:
                raise MarketDataError(
                    "DATASET_TOO_LARGE",
                    "This market exceeds the backtest point limit; choose a narrower date range",
                )
            histories = {
                "YES": yes_history,
                "NO": await self._fetch_history(
                    client,
                    snapshot.no_token_id,
                    start_at,
                    end_at,
                    remaining_points,
                ),
            }
            point_count = len(histories["YES"]) + len(histories["NO"])
            if point_count > self.settings.BACKTEST_MAX_POINTS:
                raise MarketDataError(
                    "DATASET_TOO_LARGE",
                    "This market exceeds the backtest point limit; choose a narrower date range",
                )
            validate_dataset_history(histories, config)
            return build_dataset(
                snapshot,
                histories,
                request_start_at=config.start_at,
                request_end_at=config.end_at,
            )
        finally:
            if own_client:
                await client.aclose()

    async def _fetch_market(self, client: httpx.AsyncClient, condition_id: str) -> MarketSnapshot:
        response = await self._request(
            client,
            "GET",
            f"{str(self.settings.POLYMARKET_GAMMA_URL).rstrip('/')}/markets/keyset",
            params={"condition_ids": condition_id, "closed": "true", "limit": "2"},
        )
        payload = response.json()
        markets = payload.get("markets") if isinstance(payload, dict) else None
        if not isinstance(markets, list):
            raise MarketDataError("MALFORMED_MARKET", "Polymarket returned malformed market data")
        matches = [
            item
            for item in markets
            if isinstance(item, dict) and item.get("conditionId") == condition_id
        ]
        if not matches:
            raise MarketDataError("MARKET_NOT_FOUND", "Resolved market not found")
        if len(matches) != 1:
            raise MarketDataError("AMBIGUOUS_MARKET", "Polymarket returned ambiguous market data")
        market = matches[0]
        if market.get("closed") is not True or market.get("acceptingOrders") is True:
            raise MarketDataError("MARKET_NOT_RESOLVED", "Backtests require a resolved market")
        if market.get("enableOrderBook") is not True:
            raise MarketDataError("NON_CLOB_MARKET", "Backtests require a CLOB market")

        outcomes = _string_array(market.get("outcomes"), "outcomes")
        prices = _string_array(market.get("outcomePrices"), "outcome prices")
        token_ids = _string_array(market.get("clobTokenIds"), "token IDs")
        if len(outcomes) != 2 or len(prices) != 2 or len(token_ids) != 2:
            raise MarketDataError("NON_BINARY_MARKET", "Backtests require exactly two outcomes")
        labels = [item.upper() for item in outcomes]
        if set(labels) != {"YES", "NO"}:
            raise MarketDataError("NON_BINARY_MARKET", "Backtests require YES and NO outcomes")
        try:
            resolution_prices = [Decimal(value) for value in prices]
        except InvalidOperation as exc:
            raise MarketDataError(
                "AMBIGUOUS_RESOLUTION", "Market resolution prices are malformed"
            ) from exc
        winners = [index for index, price in enumerate(resolution_prices) if price == Decimal("1")]
        losers = [index for index, price in enumerate(resolution_prices) if price == Decimal("0")]
        if len(winners) != 1 or len(losers) != 1:
            raise MarketDataError(
                "AMBIGUOUS_RESOLUTION", "Resolution must contain exactly one winner and one loser"
            )

        started_at = _timestamp(market.get("startDate") or market.get("createdAt"), "start date")
        if started_at < CLOB_V2_START:
            raise MarketDataError(
                "PRE_CLOB_V2_MARKET", "Backtests support CLOB V2 markets from April 28, 2026"
            )
        closed_at = _timestamp(market.get("closedTime") or market.get("endDate"), "resolution date")
        yes_index = labels.index("YES")
        no_index = labels.index("NO")
        yes_fee, no_fee = await _gather_fees(
            self,
            client,
            token_ids[yes_index],
            token_ids[no_index],
        )
        if yes_fee != no_fee:
            raise MarketDataError(
                "INCONSISTENT_FEE_RATE", "Outcome tokens returned different fee rates"
            )
        return MarketSnapshot(
            condition_id=condition_id,
            question=str(market.get("question") or ""),
            yes_token_id=token_ids[yes_index],
            no_token_id=token_ids[no_index],
            resolved_outcome=labels[winners[0]],  # type: ignore[arg-type]
            fee_rate=yes_fee,
            start_at=started_at,
            closed_at=closed_at,
        )

    async def _fetch_fee(self, client: httpx.AsyncClient, token_id: str) -> Decimal:
        response = await self._request(
            client,
            "GET",
            f"{str(self.settings.POLYMARKET_CLOB_URL).rstrip('/')}/fee-rate",
            params={"token_id": token_id},
        )
        payload = response.json()
        value = payload.get("base_fee") if isinstance(payload, dict) else None
        if not isinstance(value, int) or value < 0:
            raise MarketDataError("MISSING_FEE_RATE", "Market fee rate is unavailable")
        return Decimal(value) / Decimal("10000")

    async def _fetch_history(
        self,
        client: httpx.AsyncClient,
        token_id: str,
        start_at: datetime,
        end_at: datetime,
        maximum_points: int,
    ) -> list[PriceObservation]:
        deduplicated: dict[int, PriceObservation] = {}
        cursor = start_at
        while cursor < end_at:
            chunk_end = min(end_at, cursor + HISTORY_CHUNK)
            response = await self._request(
                client,
                "GET",
                f"{str(self.settings.POLYMARKET_CLOB_URL).rstrip('/')}/prices-history",
                params={
                    "market": token_id,
                    "startTs": str(int(cursor.timestamp())),
                    "endTs": str(int(chunk_end.timestamp())),
                    "fidelity": "1",
                },
            )
            payload = response.json()
            history = payload.get("history") if isinstance(payload, dict) else None
            if not isinstance(history, list):
                raise MarketDataError(
                    "MALFORMED_HISTORY", "Polymarket returned malformed price history"
                )
            for raw in history:
                if not isinstance(raw, dict):
                    continue
                try:
                    timestamp = int(raw["t"])
                    price = Decimal(str(raw["p"]))
                    point = PriceObservation(
                        timestamp=datetime.fromtimestamp(timestamp, tz=UTC), price=price
                    )
                except (KeyError, TypeError, ValueError, InvalidOperation):
                    continue
                if start_at <= point.timestamp <= end_at:
                    deduplicated[timestamp] = point
                    if len(deduplicated) > maximum_points:
                        raise MarketDataError(
                            "DATASET_TOO_LARGE",
                            "This market exceeds the backtest point limit; "
                            "choose a narrower date range",
                        )
            cursor = chunk_end
        return [deduplicated[key] for key in sorted(deduplicated)]

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise MarketDataError(
                "POLYMARKET_UNAVAILABLE",
                "Polymarket history is temporarily unavailable",
                retryable=True,
            ) from exc
        if response.status_code == 404:
            raise MarketDataError("MARKET_NOT_FOUND", "Requested Polymarket data was not found")
        if response.status_code == 429 or response.status_code >= 500:
            raise MarketDataError(
                "POLYMARKET_UNAVAILABLE",
                "Polymarket history is temporarily unavailable",
                retryable=True,
            )
        if not response.is_success:
            raise MarketDataError(
                "POLYMARKET_REJECTED",
                f"Polymarket rejected the data request ({response.status_code})",
            )
        return response


def validate_dataset_history(
    histories: dict[Outcome, list[PriceObservation]], config: BacktestConfig
) -> None:
    minimum_span = timedelta(minutes=strategy_lookback_minutes(config) + 1)
    for outcome in ("YES", "NO"):
        points = histories.get(outcome, [])
        if len(points) < 2 or points[-1].timestamp - points[0].timestamp < minimum_span:
            raise MarketDataError(
                "INSUFFICIENT_HISTORY",
                f"{outcome} does not have enough one-minute history for this strategy",
            )


async def _gather_fees(
    adapter: PolymarketHistoryClient,
    client: httpx.AsyncClient,
    yes_token_id: str,
    no_token_id: str,
) -> tuple[Decimal, Decimal]:
    # Keep the calls explicit and sequential to remain comfortably below the
    # public CLOB endpoint's burst limits.
    return (
        await adapter._fetch_fee(client, yes_token_id),
        await adapter._fetch_fee(client, no_token_id),
    )


def build_dataset(
    snapshot: MarketSnapshot,
    histories: dict[Outcome, list[PriceObservation]],
    *,
    request_start_at: datetime | None,
    request_end_at: datetime | None,
) -> HistoricalDataset:
    canonical = {
        "version": 1,
        "market": {
            "conditionId": snapshot.condition_id,
            "question": snapshot.question,
            "yesTokenId": snapshot.yes_token_id,
            "noTokenId": snapshot.no_token_id,
            "resolvedOutcome": snapshot.resolved_outcome,
            "feeRate": _decimal(snapshot.fee_rate),
            "startAt": snapshot.start_at.isoformat(),
            "closedAt": snapshot.closed_at.isoformat(),
        },
        "request": {
            "startAt": request_start_at.isoformat() if request_start_at else None,
            "endAt": request_end_at.isoformat() if request_end_at else None,
        },
        "histories": {
            outcome: [
                [int(point.timestamp.timestamp()), _decimal(point.price)]
                for point in histories[outcome]
            ]
            for outcome in ("YES", "NO")
        },
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    dataset_hash = hashlib.sha256(encoded).hexdigest()
    all_points = [*histories["YES"], *histories["NO"]]
    return HistoricalDataset(
        snapshot=snapshot,
        histories=histories,
        dataset_hash=dataset_hash,
        payload=gzip.compress(encoded, mtime=0),
        point_count=len(all_points),
        start_at=min(point.timestamp for point in all_points),
        end_at=max(point.timestamp for point in all_points),
        request_start_at=request_start_at,
        request_end_at=request_end_at,
    )


def decode_dataset(payload: bytes) -> HistoricalDataset:
    value = json.loads(gzip.decompress(payload))
    market = value["market"]
    request = value["request"]
    snapshot = MarketSnapshot(
        condition_id=market["conditionId"],
        question=market["question"],
        yes_token_id=market["yesTokenId"],
        no_token_id=market["noTokenId"],
        resolved_outcome=market["resolvedOutcome"],
        fee_rate=Decimal(market["feeRate"]),
        start_at=datetime.fromisoformat(market["startAt"]),
        closed_at=datetime.fromisoformat(market["closedAt"]),
    )
    histories = {
        outcome: [
            PriceObservation(datetime.fromtimestamp(item[0], tz=UTC), Decimal(item[1]))
            for item in value["histories"][outcome]
        ]
        for outcome in ("YES", "NO")
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    all_points = [*histories["YES"], *histories["NO"]]
    return HistoricalDataset(
        snapshot=snapshot,
        histories=histories,
        dataset_hash=hashlib.sha256(encoded).hexdigest(),
        payload=payload,
        point_count=len(all_points),
        start_at=min(point.timestamp for point in all_points),
        end_at=max(point.timestamp for point in all_points),
        request_start_at=datetime.fromisoformat(request["startAt"]) if request["startAt"] else None,
        request_end_at=datetime.fromisoformat(request["endAt"]) if request["endAt"] else None,
    )


def _string_array(value: Any, label: str) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MarketDataError("MALFORMED_MARKET", f"Market {label} are malformed") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MarketDataError("MALFORMED_MARKET", f"Market {label} are malformed")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise MarketDataError("MALFORMED_MARKET", f"Market {label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("MALFORMED_MARKET", f"Market {label} is malformed") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decimal(value: Decimal) -> str:
    encoded = format(value, "f").rstrip("0").rstrip(".")
    return encoded if encoded else "0"
