"""Async PostgreSQL connection pool — uses the 'polycode' schema inside finespresso_db."""
import asyncpg
import os
from typing import Optional

_pool: Optional[asyncpg.Pool] = None

SCHEMA = "polycode"


def _get_dsn() -> str:
    """DSN points to finespresso_db; the polycode schema is set via search_path."""
    return os.getenv(
        "POLYCODE_DB_URL",
        os.getenv(
            "DATABASE_URL",
            "postgresql://finespresso:mlfpass2026@72.62.114.124:5432/finespresso_db",
        ),
    )


async def _init_conn(conn: asyncpg.Connection) -> None:
    """Set search_path on every new connection so queries hit the polycode schema."""
    await conn.execute(f"SET search_path TO {SCHEMA}, public")


async def get_pool() -> asyncpg.Pool:
    """Return (or lazily create) the shared connection pool. Auto-recreates on stale pool."""
    global _pool
    if _pool is not None:
        # Quick health check — if pool is closed, recreate
        try:
            await _pool.fetchval("SELECT 1")
        except Exception:
            try:
                await _pool.close()
            except Exception:
                pass
            _pool = None
    if _pool is None:
        _pool = await asyncpg.create_pool(
            _get_dsn(),
            min_size=2,
            max_size=10,
            command_timeout=30,
            init=_init_conn,
        )
    return _pool


async def close_pool() -> None:
    """Gracefully shut down the pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def _record_to_dict(record) -> dict:
    """Convert an asyncpg Record to a JSON-serialisable dict."""
    if record is None:
        return {}
    result = {}
    for k, v in dict(record).items():
        if v is None:
            result[k] = None
        elif hasattr(v, "isoformat"):          # datetime / date
            result[k] = v.isoformat()
        elif hasattr(v, "__float__"):           # Decimal
            result[k] = float(v)
        elif hasattr(v, "hex"):                 # UUID
            result[k] = str(v)
        else:
            result[k] = v
    return result
