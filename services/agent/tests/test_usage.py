import math
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from polytrade_agent.activity import (
    ACTIVITY_METADATA_KEYS,
    MAX_STORED_CHARS,
    record_activity_complete,
    record_activity_start,
    redact_text,
    safe_metadata,
)
from polytrade_agent.config import AgentSettings, get_settings
from polytrade_agent.usage import (
    DailyBudgetExceeded,
    QueryAuthorization,
    QueryLimitExceeded,
    TokenUsage,
    allowance_warning,
    authorize_query,
    budget_warning,
    decimal_string,
    estimate_cost,
    estimate_usage,
    extract_usage,
    refund_query,
    usage_status,
)


def make_settings(**overrides: Any) -> AgentSettings:
    return AgentSettings.model_validate({**get_settings().model_dump(), **overrides})


class FakeUsageRepository:
    def __init__(
        self,
        *,
        exhausted: bool = False,
        daily_cost: Decimal = Decimal("0"),
        queries_used: int = 0,
        disabled: bool = False,
    ) -> None:
        self.exhausted = exhausted
        self.disabled = disabled
        self.daily_cost = daily_cost
        self.queries_used = queries_used
        self.refunds = 0

    async def authorize_daily_query(self, principal_id: str, limit: int) -> int | None:
        if self.disabled or self.exhausted or self.queries_used >= limit:
            return None
        self.queries_used += 1
        return self.queries_used

    async def refund_daily_query(self, principal_id: str) -> None:
        self.refunds += 1
        self.queries_used = max(self.queries_used - 1, 0)

    async def sum_daily_cost(self) -> Decimal:
        return self.daily_cost

    async def used_queries_today(self, principal_id: str) -> int:
        return self.queries_used


class FakeActivityRepository:
    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.completions: list[dict[str, Any]] = []

    async def insert_activity(self, **kwargs: Any) -> None:
        self.starts.append(kwargs)

    async def complete_activity(self, **kwargs: Any) -> None:
        self.completions.append(kwargs)


def test_redact_text_strips_xai_keys_secrets_and_bearer_tokens() -> None:
    text = (
        "Use xai-SuperSecret1234 for weather. api_key=hunter2 "
        'and "API KEY: abc123" plus Authorization: Bearer eyJ.jwt.sig'
    )
    redacted = redact_text(text)

    assert "xai-SuperSecret1234" not in redacted
    assert "[REDACTED_XAI_KEY]" in redacted
    assert "hunter2" not in redacted
    assert "api_key=[REDACTED]" in redacted
    assert "abc123" not in redacted
    assert "Bearer [REDACTED]" in redacted


def test_redact_text_clamps_storage_length_and_handles_empty() -> None:
    assert redact_text("") == ""
    assert redact_text(None) == ""
    assert len(redact_text("x" * (MAX_STORED_CHARS + 5_000))) == MAX_STORED_CHARS


def test_safe_metadata_only_survives_the_allowlist() -> None:
    metadata = {"runtime": "hermes", "requested_runtime": "hermes", "fallback": True, "code": None}
    assert safe_metadata(metadata) == metadata
    assert safe_metadata({**metadata, "bearer": "leak", "raw": "x"}) == metadata
    assert safe_metadata(None) == {}


def test_extract_usage_reads_measured_dict_and_object_metadata() -> None:
    measured = extract_usage({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    assert (measured.input_tokens, measured.output_tokens, measured.total_tokens) == (10, 5, 15)
    assert measured.quality == "measured"

    legacy = extract_usage({"prompt_tokens": 8, "completion_tokens": 2})
    assert (legacy.input_tokens, legacy.output_tokens, legacy.total_tokens) == (8, 2, 10)

    class Chunk:
        usage_metadata = {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}

    wrapped = extract_usage(Chunk())
    assert wrapped.quality == "measured" and wrapped.total_tokens == 7

    unavailable = extract_usage(None)
    assert unavailable.quality == "unavailable" and unavailable.total_tokens == 0


def test_estimate_usage_never_returns_zero() -> None:
    estimated = estimate_usage("", "")
    assert estimated.quality == "estimated"
    assert estimated.input_tokens == 1 and estimated.output_tokens == 1

    sized = estimate_usage("x" * 41, "y" * 7)
    assert sized.input_tokens == math.ceil(41 / 4) == 11
    assert sized.output_tokens == 2


def test_estimate_cost_uses_runtime_specific_rates() -> None:
    settings = make_settings(
        HERMES_INPUT_USD_PER_MILLION=2,
        HERMES_OUTPUT_USD_PER_MILLION=6,
        DEEPSEEK_INPUT_USD_PER_MILLION=1.25,
        DEEPSEEK_OUTPUT_USD_PER_MILLION=2.50,
    )
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000)

    assert estimate_cost(usage, runtime="hermes", settings=settings) == Decimal("8.00")
    assert estimate_cost(usage, runtime="deepseek", settings=settings) == Decimal("3.75")


def test_decimal_string_trims_zeros_without_corrupting_integers() -> None:
    assert decimal_string(Decimal("5.000000")) == "5"
    assert decimal_string(Decimal("0.500000")) == "0.5"
    assert decimal_string(Decimal("100")) == "100"
    assert decimal_string(Decimal("0")) == "0"
    assert decimal_string(Decimal("0.0000004")) == "0"


@pytest.mark.asyncio
async def test_authorize_query_rejects_disabled_limit_before_touching_slots() -> None:
    repository = FakeUsageRepository(disabled=True)
    with pytest.raises(QueryLimitExceeded, match="disabled"):
        await authorize_query(repository, make_settings(FREE_PLATFORM_QUERY_LIMIT=0), "clerk:u")
    assert repository.queries_used == 0


@pytest.mark.asyncio
async def test_authorize_query_rejects_when_platform_budget_is_spent() -> None:
    repository = FakeUsageRepository(daily_cost=Decimal("5.00"))
    settings = make_settings(PLATFORM_LLM_DAILY_BUDGET_USD=5.0)
    with pytest.raises(DailyBudgetExceeded, match="daily AI budget"):
        await authorize_query(repository, settings, "clerk:u")
    assert repository.queries_used == 0


@pytest.mark.asyncio
async def test_authorize_query_claims_a_slot_then_rejects_at_the_cap() -> None:
    repository = FakeUsageRepository()
    settings = make_settings(FREE_PLATFORM_QUERY_LIMIT=2)

    first = await authorize_query(repository, settings, "clerk:u")
    second = await authorize_query(repository, settings, "clerk:u")
    assert (first.used, first.limit) == (1, 2)
    assert second.used == 2
    with pytest.raises(QueryLimitExceeded, match="today's 2 platform-funded"):
        await authorize_query(repository, settings, "clerk:u")
    assert repository.queries_used == 2


@pytest.mark.asyncio
async def test_refund_query_releases_the_claimed_slot() -> None:
    repository = FakeUsageRepository()
    settings = make_settings(FREE_PLATFORM_QUERY_LIMIT=1)
    await authorize_query(repository, settings, "clerk:u")
    await refund_query(repository, "clerk:u")
    assert repository.refunds == 1 and repository.queries_used == 0


def test_allowance_warning_fires_at_eighty_percent_and_at_the_cap() -> None:
    assert allowance_warning(QueryAuthorization("platform", 3, 10)) is None
    assert "8 of 10" in (allowance_warning(QueryAuthorization("platform", 8, 10)) or "")
    assert "all 5" in (allowance_warning(QueryAuthorization("platform", 5, 5)) or "")
    assert allowance_warning(QueryAuthorization("platform", 2, 0)) is None


@pytest.mark.asyncio
async def test_budget_warning_escalates_at_eighty_and_ninety_percent() -> None:
    settings = make_settings(PLATFORM_LLM_DAILY_BUDGET_USD=10.0)

    assert await budget_warning(FakeUsageRepository(daily_cost=Decimal("7.99")), settings) is None
    eighty = await budget_warning(FakeUsageRepository(daily_cost=Decimal("8.50")), settings)
    assert eighty is not None and "80%" in eighty and "$8.50 of $10.00" in eighty
    ninety = await budget_warning(FakeUsageRepository(daily_cost=Decimal("9.50")), settings)
    assert ninety is not None and "90%" in ninety
    disabled = make_settings(PLATFORM_LLM_DAILY_BUDGET_USD=0)
    assert await budget_warning(FakeUsageRepository(), disabled) is None


@pytest.mark.asyncio
async def test_usage_status_reports_remaining_and_costs() -> None:
    repository = FakeUsageRepository(queries_used=2, daily_cost=Decimal("1.250000"))
    response = await usage_status(repository, make_settings(FREE_PLATFORM_QUERY_LIMIT=5), "clerk:u")

    assert response.funding_source == "platform"
    assert (response.used, response.limit, response.remaining) == (2, 5, 3)
    assert response.cost_usd == "1.25" == response.platform_cost_usd
    assert response.platform_budget_usd == "5"


@pytest.mark.asyncio
async def test_activity_recording_redacts_request_and_filters_metadata() -> None:
    repository = FakeActivityRepository()
    run_id, thread_id = uuid4(), uuid4()

    await record_activity_start(
        repository,
        run_id=run_id,
        thread_id=thread_id,
        principal_id="clerk:u",
        requested_runtime="hermes",
        runtime="hermes",
        request_text="query with api_key=leak",
    )
    assert repository.starts[0]["request_text"] == "query with api_key=[REDACTED]"
    assert repository.starts[0]["run_id"] == run_id

    await record_activity_complete(
        repository,
        run_id=run_id,
        status="completed",
        error_code=None,
        response_text="Bearer leaked-token",
        metadata={
            "runtime": "deepseek",
            "requested_runtime": "hermes",
            "fallback": True,
            "code": None,
        },
    )
    completion = repository.completions[0]
    assert completion["response_text"] == "Bearer [REDACTED]"
    assert set(completion["metadata"]) == set(ACTIVITY_METADATA_KEYS)