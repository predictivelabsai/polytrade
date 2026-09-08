from decimal import Decimal
from pathlib import Path

import pytest

from chat.activity import redact_text
from chat.usage import (
    QueryAuthorization, TokenUsage, allowance_warning, estimate_cost,
    estimate_usage, extract_usage,
)


def test_activity_redaction_removes_credentials():
    value = redact_text("api_key=xai-secretvalue Bearer abc123 password=hunter2")
    assert "secretvalue" not in value
    assert "abc123" not in value
    assert "hunter2" not in value
    assert "REDACTED" in value


def test_usage_normalization_estimation_and_cost():
    measured = extract_usage({"usage": {
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
    }})
    assert measured == TokenUsage(100, 20, 120, "measured")
    estimated = estimate_usage("abcd" * 10, "abcd" * 5)
    assert estimated.quality == "estimated"
    assert estimated.total_tokens == 15
    assert estimate_cost(measured, agent="deepagents", model="grok") > Decimal("0")


def test_daily_allowance_warning_is_visible_at_four_of_five():
    assert allowance_warning(QueryAuthorization("platform", True, 3, 5)) is None
    assert "4 of 5" in allowance_warning(QueryAuthorization("platform", True, 4, 5))
    assert "all 5" in allowance_warning(QueryAuthorization("platform", True, 5, 5))
    assert allowance_warning(QueryAuthorization("user_byok", False, 5, 5)) is None


def test_activity_migration_has_daily_gate_logs_and_triggers():
    sql = Path("db/migrations/004_activity_usage.sql").read_text(encoding="utf-8")
    for name in (
        "user_provider_credentials", "user_ai_daily_allowances", "user_logging",
        "llm_usage_logging", "agent_logging", "trg_chat_agent_logging",
        "trg_run_agent_logging", "trg_trade_agent_logging",
    ):
        assert name in sql
    assert "api_key_enc" in sql
    assert "request_text" not in sql.split("CREATE TABLE IF NOT EXISTS polycode.llm_usage_logging", 1)[1].split("CREATE TABLE", 1)[0]


def test_sidebar_exposes_settings_usage_and_logging():
    source = Path("agui_app.py").read_text(encoding="utf-8")
    assert 'href="/settings"' in source
    assert 'href="/admin/logging"' in source
    assert '("/usage"' in source


@pytest.mark.asyncio
async def test_daily_query_gate_reserves_one_atomic_slot(monkeypatch):
    import chat.usage as usage

    class Context:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        def transaction(self):
            return self
        async def execute(self, *args):
            return "INSERT 0 1"
        async def fetchval(self, *args):
            return 1

    class Pool:
        async def fetchval(self, *args):
            return True
        def acquire(self):
            return Context()

    async def zero_cost(user_id=None):
        return Decimal("0")

    async def fake_pool():
        return Pool()

    monkeypatch.setattr(usage, "daily_platform_cost", zero_cost)
    monkeypatch.setattr(usage, "get_pool", fake_pool)
    authorization = await usage.authorize_query(
        "00000000-0000-0000-0000-000000000001", has_byok=False
    )
    assert authorization.funding_source == "platform"
    assert authorization.platform_slot is True
    assert authorization.used == 1


@pytest.mark.asyncio
async def test_byok_bypasses_platform_gate_without_database(monkeypatch):
    import chat.usage as usage

    async def forbidden_pool():
        raise AssertionError("database must not be touched for BYOK authorization")

    monkeypatch.setattr(usage, "get_pool", forbidden_pool)
    authorization = await usage.authorize_query(None, has_byok=True)
    assert authorization.funding_source == "user_byok"
    assert authorization.platform_slot is False


@pytest.mark.asyncio
async def test_provider_key_is_encrypted_before_database_write(monkeypatch):
    from cryptography.fernet import Fernet
    import utils.auth as auth

    class Pool:
        saved = None
        async def execute(self, query, *args):
            self.saved = args

    pool = Pool()

    async def fake_pool():
        return pool

    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(auth, "get_pool", fake_pool)
    plaintext = "xai-this-is-a-test-key"
    await auth.store_provider_api_key(
        "00000000-0000-0000-0000-000000000001", "xai", plaintext
    )
    ciphertext = bytes(pool.saved[2])
    assert plaintext.encode() not in ciphertext
    assert pool.saved[3] == "xai-...-key"


@pytest.mark.asyncio
async def test_usage_command_is_deterministic_without_model_call():
    from chat.repository import MemoryChatRepository
    from chat.service import ChatService

    repository = MemoryChatRepository()
    service = ChatService(
        repository=repository,
        agent_factory=lambda: (_ for _ in ()).throw(AssertionError("model called")),
    )
    user_id = "00000000-0000-0000-0000-000000000001"
    thread = await service.create_thread(user_id)
    events = [event async for event in service.stream_message(
        user_id=user_id, thread_id=thread["thread_id"], content="/usage",
        idempotency_key="usage-test",
    )]
    completed = next(e for e in events if e.event == "message.completed")
    assert "Usage accounting requires PostgreSQL" in completed.data["message"]["content"]


@pytest.mark.asyncio
async def test_admin_usage_queries_do_not_pass_unused_requester_argument(monkeypatch):
    import chat.usage as usage

    class Pool:
        calls = []

        async def fetch(self, query, *args):
            self.calls.append((query, args))
            return []

    pool = Pool()

    async def fake_pool():
        return pool

    monkeypatch.setattr(usage, "get_pool", fake_pool)
    user_id = "00000000-0000-0000-0000-000000000001"
    await usage.list_usage(user_id, is_admin=True)
    await usage.usage_summary(user_id, is_admin=True)
    assert pool.calls[0][1] == (100,)
    assert pool.calls[1][1] == ()
