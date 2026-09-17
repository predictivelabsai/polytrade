"""Cached read model for unauthenticated market pages."""

from __future__ import annotations

import asyncio
from typing import Any

from polytrade_contracts import PublicMarketDetail, PublicOrderBook

from .cache import TtlCache
from .errors import AppError, not_found
from .polymarket import PolymarketAdapter, now_iso

PUBLIC_CACHE_TTL_MS = {"index": 30_000, "market": 30_000, "missing": 60_000, "book": 5_000}
HISTORY_TTL_MS = {"1h": 60_000, "6h": 120_000, "1d": 300_000, "1w": 600_000, "max": 3_600_000}


def public_price_history_ttl_ms(interval: str) -> int:
    return HISTORY_TTL_MS.get(interval, HISTORY_TTL_MS["1d"])


class PublicMarketService:
    def __init__(self, polymarket: PolymarketAdapter, cache: TtlCache) -> None:
        self.polymarket = polymarket
        self.cache = cache

    async def list(self, *, limit: int, offset: int, order: str):
        return await self.cache.load(
            f"index:{limit}:{offset}:{order}",
            PUBLIC_CACHE_TTL_MS["index"],
            lambda: self.polymarket.list_active_markets(limit=limit, offset=offset, order=order),
        )

    async def detail(self, slug: str) -> PublicMarketDetail:
        missing_key = f"notfound:market:{slug}"
        if self.cache.is_known_missing(missing_key):
            raise not_found("Public market not found")

        async def load() -> PublicMarketDetail:
            try:
                snapshot = await self.polymarket.get_public_market(slug)
                market = snapshot["market"]
                books = await asyncio.gather(
                    *(self._try_book(token_id) for token_id in market.clobTokenIds)
                )
                quotes: list[dict[str, Any]] = []
                for index, outcome in enumerate(market.outcomes):
                    token_id = (
                        market.clobTokenIds[index] if index < len(market.clobTokenIds) else ""
                    )
                    fallback = (
                        market.outcomePrices[index] if index < len(market.outcomePrices) else None
                    )
                    book = books[index] if index < len(books) else None
                    quotes.append(
                        {
                            "outcome": outcome,
                            "tokenId": token_id,
                            "price": (
                                book.lastTradePrice
                                or (book.bids[0].price if book.bids else fallback)
                            )
                            if book
                            else fallback,
                            "bestBid": book.bids[0].price if book and book.bids else None,
                            "bestAsk": book.asks[0].price if book and book.asks else None,
                            "source": "order-book" if book else "gamma",
                        }
                    )
                return PublicMarketDetail(market=market, quotes=quotes, observedAt=now_iso())
            except AppError as error:
                if error.status_code == 404:
                    self.cache.mark_missing(missing_key, PUBLIC_CACHE_TTL_MS["missing"])
                raise

        return await self.cache.load(f"market:{slug}", PUBLIC_CACHE_TTL_MS["market"], load)

    async def book(self, token_id: str) -> PublicOrderBook:
        return await self.cache.load(
            f"book:{token_id}",
            PUBLIC_CACHE_TTL_MS["book"],
            lambda: self.polymarket.get_order_book(token_id),
        )

    async def history(self, token_id: str, interval: str):
        return await self.cache.load(
            f"hist:{token_id}:{interval}",
            public_price_history_ttl_ms(interval),
            lambda: self.polymarket.get_price_history(token_id, interval),
        )

    async def _try_book(self, token_id: str) -> PublicOrderBook | None:
        try:
            return await self.book(token_id)
        except Exception:
            return None
