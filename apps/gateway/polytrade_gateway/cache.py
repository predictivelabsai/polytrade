"""Small in-memory TTL cache with request coalescing and negative entries."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass
class CacheStats:
    hits: int
    misses: int
    entries: int
    missing: int


class TtlCache:
    def __init__(self, max_entries: int = 500) -> None:
        self.max_entries = max_entries
        self._entries: OrderedDict[str, tuple[object, float]] = OrderedDict()
        self._missing: OrderedDict[str, float] = OrderedDict()
        self._pending: dict[str, asyncio.Task[object]] = {}
        self._hits = 0
        self._misses = 0

    async def load(self, key: str, ttl_ms: int, loader: Callable[[], Awaitable[T]]) -> T:
        self._prune()
        entry = self._entries.get(key)
        if entry and entry[1] > time.monotonic():
            self._hits += 1
            return entry[0]  # type: ignore[return-value]
        pending = self._pending.get(key)
        if pending:
            self._hits += 1
            return await pending  # type: ignore[return-value]
        self._misses += 1

        async def execute() -> object:
            value = await loader()
            self._put(self._entries, key, (value, time.monotonic() + ttl_ms / 1_000))
            return value

        task = asyncio.create_task(execute())
        self._pending[key] = task
        try:
            return await task  # type: ignore[return-value]
        finally:
            self._pending.pop(key, None)

    def is_known_missing(self, key: str) -> bool:
        self._prune()
        return key in self._missing

    def mark_missing(self, key: str, ttl_ms: int) -> None:
        self._put(self._missing, key, time.monotonic() + ttl_ms / 1_000)

    def stats(self) -> CacheStats:
        self._prune()
        return CacheStats(self._hits, self._misses, len(self._entries), len(self._missing))

    def _put(self, target: OrderedDict, key: str, value: object) -> None:
        if key not in target and len(target) >= self.max_entries:
            target.popitem(last=False)
        target[key] = value

    def _prune(self) -> None:
        now = time.monotonic()
        for key, (_, expires) in list(self._entries.items()):
            if expires <= now:
                self._entries.pop(key, None)
        for key, expires in list(self._missing.items()):
            if expires <= now:
                self._missing.pop(key, None)
