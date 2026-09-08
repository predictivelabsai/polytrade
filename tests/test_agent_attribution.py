from unittest.mock import AsyncMock

import pytest

import db.repository as repository
from db.attribution import bind_agent, reset_agent


class RecordingPool:
    def __init__(self):
        self.execute = AsyncMock()
        self.fetchrow = AsyncMock(return_value={"trade_id": "trade-1"})


@pytest.mark.asyncio
async def test_agent_context_attributes_runs_without_changing_callers(monkeypatch):
    pool = RecordingPool()
    monkeypatch.setattr(repository, "get_pool", AsyncMock(return_value=pool))
    tokens = bind_agent("hermes")
    try:
        await repository.create_run("question", "model", "provider")
    finally:
        reset_agent(tokens)

    sql, *args = pool.execute.await_args.args
    assert "agent_framework" in sql
    assert "agent_name" in sql
    assert "hermes" in args
    assert "Hermes" in args


@pytest.mark.asyncio
async def test_agent_context_attributes_new_trades_and_preserves_origin(monkeypatch):
    pool = RecordingPool()
    monkeypatch.setattr(repository, "get_pool", AsyncMock(return_value=pool))
    tokens = bind_agent("deepagents")
    try:
        await repository.upsert_trade({
            "trade_id": "trade-1", "market_id": "market-1", "amount": 10,
            "entry_price": 0.5, "trade_type": "paper",
        })
    finally:
        reset_agent(tokens)

    sql, *args = pool.fetchrow.await_args.args
    assert "agent_framework" in sql
    assert "polycode.trades.agent_framework = 'system'" in sql
    assert "deepagents" in args
    assert "DeepAgents" in args

