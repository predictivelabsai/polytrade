"""Idempotent PostgreSQL schema bootstrap for the gateway container."""

from __future__ import annotations

from pathlib import Path

import psycopg

SCHEMA = Path(__file__).resolve().parents[1] / "bootstrap" / "schema.sql"


async def bootstrap_schema(database_url: str) -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(
            "SELECT pg_advisory_lock(hashtextextended('polytrade-schema-bootstrap', 0))"
        )
        try:
            await connection.execute(sql)
        finally:
            await connection.execute(
                "SELECT pg_advisory_unlock(hashtextextended('polytrade-schema-bootstrap', 0))"
            )
