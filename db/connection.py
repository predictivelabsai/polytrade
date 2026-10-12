"""Async PostgreSQL connection pool — uses the 'polycode' schema inside finespresso_db."""
import asyncio
import asyncpg
import os
from typing import Optional

_pool: Optional[asyncpg.Pool] = None
_pool_lock: Optional[asyncio.Lock] = None

SCHEMA = "polycode"


def _get_dsn() -> str:
    """DSN points to finespresso_db; the polycode schema is set via search_path."""
    dsn = os.getenv("POLYCODE_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "POLYCODE_DB_URL or DATABASE_URL must be set for PostgreSQL persistence."
        )
    return dsn


async def _init_conn(conn: asyncpg.Connection) -> None:
    """Set search_path on every new connection so queries hit the polycode schema."""
    await conn.execute(f"SET search_path TO {SCHEMA}, public")


async def get_pool() -> asyncpg.Pool:
    """Return or lazily create the process-wide connection pool."""
    global _pool, _pool_lock
    if _pool is not None and not _pool.is_closing():
        return _pool
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    async with _pool_lock:
        if _pool is None or _pool.is_closing():
            _pool = await asyncpg.create_pool(
                _get_dsn(),
                min_size=1,
                max_size=3,
                max_inactive_connection_lifetime=300,
                command_timeout=30,
                server_settings={"application_name": os.getenv("DB_APPLICATION_NAME", "polytrade")},
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
