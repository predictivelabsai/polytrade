"""Daily platform-funded query allowance and token/cost accounting.

Ported from the legacy chat stack's ``chat/usage.py`` minus the BYOK branch:
every principal shares the platform's daily dollar budget and consumes one of
their own daily query slots per run. Cost rates are selected by runtime.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .config import AgentSettings
from .schemas import AdminUsageResponse, AgentUsageResponse, DecimalString
from .storage import AgentRepository

RUNTIMES = ("deepseek", "hermes")


class QueryLimitExceeded(Exception):
    """Raised when a principal has consumed all daily platform-funded slots."""


class DailyBudgetExceeded(Exception):
    """Raised when the platform's daily LLM dollar budget is reached."""


@dataclass(frozen=True)
class QueryAuthorization:
    funding_source: str = "platform"
    used: int = 0
    limit: int = 0


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    quality: str = "unavailable"


def _integer(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def extract_usage(value: Any) -> TokenUsage:
    data = value or {}
    if not isinstance(data, dict):
        data = (
            getattr(data, "usage_metadata", None)
            or getattr(data, "response_metadata", None)
            or {}
        )
    nested = data.get("usage") or data.get("token_usage") or data.get("usage_metadata") or data
    if not isinstance(nested, dict):
        return TokenUsage()
    inp = _integer(nested.get("input_tokens", nested.get("prompt_tokens")))
    out = _integer(nested.get("output_tokens", nested.get("completion_tokens")))
    total = _integer(nested.get("total_tokens")) or inp + out
    return TokenUsage(inp, out, total, "measured" if total else "unavailable")


def estimate_usage(prompt: str, response: str) -> TokenUsage:
    inp = max(math.ceil(len(prompt or "") / 4), 1)
    out = max(math.ceil(len(response or "") / 4), 1)
    return TokenUsage(inp, out, inp + out, "estimated")


def estimate_cost(usage: TokenUsage, *, runtime: str, settings: AgentSettings) -> Decimal:
    if runtime == "hermes":
        in_rate = Decimal(str(settings.HERMES_INPUT_USD_PER_MILLION))
        out_rate = Decimal(str(settings.HERMES_OUTPUT_USD_PER_MILLION))
    else:
        in_rate = Decimal(str(settings.DEEPSEEK_INPUT_USD_PER_MILLION))
        out_rate = Decimal(str(settings.DEEPSEEK_OUTPUT_USD_PER_MILLION))
    return (
        Decimal(usage.input_tokens) * in_rate + Decimal(usage.output_tokens) * out_rate
    ) / Decimal(1_000_000)


def decimal_string(value: Decimal | int | float | str) -> DecimalString:
    text = str(Decimal(value).quantize(Decimal("0.000001")))
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


async def authorize_query(
    repository: AgentRepository,
    settings: AgentSettings,
    principal_id: str,
) -> QueryAuthorization:
    """Claim one daily query slot for a principal.

    The dollar-budget check runs before the slot claim, so a principal at the
    cap never burns a slot on a doomed request.
    """
    limit = max(settings.FREE_PLATFORM_QUERY_LIMIT, 0)
    if limit == 0:
        raise QueryLimitExceeded("Platform-funded AI queries are disabled.")
    spent = await repository.sum_daily_cost()
    budget = Decimal(str(settings.PLATFORM_LLM_DAILY_BUDGET_USD))
    if spent >= budget:
        raise DailyBudgetExceeded(
            f"The platform's ${budget:.2f} daily AI budget is reached. Try again tomorrow."
        )
    used = await repository.authorize_daily_query(principal_id, limit)
    if used is None:
        raise QueryLimitExceeded(
            f"You have used today's {limit} platform-funded AI queries. Try again tomorrow."
        )
    return QueryAuthorization("platform", int(used), limit)


async def refund_query(repository: AgentRepository, principal_id: str) -> None:
    await repository.refund_daily_query(principal_id)


def allowance_warning(authorization: QueryAuthorization) -> str | None:
    if authorization.limit <= 0:
        return None
    if authorization.used >= authorization.limit:
        return f"You have used all {authorization.limit} platform-funded queries for today."
    if authorization.used >= math.ceil(authorization.limit * Decimal("0.8")):
        return (
            f"You have used {authorization.used} of {authorization.limit} "
            "platform-funded queries today."
        )
    return None


async def budget_warning(
    repository: AgentRepository,
    settings: AgentSettings,
) -> str | None:
    budget = Decimal(str(settings.PLATFORM_LLM_DAILY_BUDGET_USD))
    if budget <= 0:
        return None
    spent = await repository.sum_daily_cost()
    ratio = spent / budget
    if ratio < Decimal("0.8"):
        return None
    level = "90%" if ratio >= Decimal("0.9") else "80%"
    return (
        f"Platform AI spending reached the {level} warning level "
        f"(${spent:.2f} of ${budget:.2f} today)."
    )


async def record_llm_usage(
    repository: AgentRepository,
    *,
    principal_id: str,
    run_id: Any,
    thread_id: Any,
    runtime: str,
    provider: str,
    model: str,
    usage: TokenUsage,
    status: str = "completed",
    settings: AgentSettings,
) -> None:
    await repository.insert_llm_usage(
        principal_id=principal_id,
        run_id=run_id,
        thread_id=thread_id,
        runtime=runtime,
        provider=provider,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        estimated_cost_usd=estimate_cost(usage, runtime=runtime, settings=settings),
        usage_quality=usage.quality,
        status=status,
    )


async def usage_status(
    repository: AgentRepository,
    settings: AgentSettings,
    principal_id: str,
) -> AgentUsageResponse:
    used = await repository.used_queries_today(principal_id)
    platform_cost = await repository.sum_daily_cost()
    limit = max(settings.FREE_PLATFORM_QUERY_LIMIT, 0)
    return AgentUsageResponse(
        used=used,
        limit=limit,
        remaining=max(limit - used, 0),
        cost_usd=decimal_string(platform_cost),
        platform_cost_usd=decimal_string(platform_cost),
        platform_budget_usd=decimal_string(settings.PLATFORM_LLM_DAILY_BUDGET_USD),
    )


async def admin_usage_summary(repository: AgentRepository) -> AdminUsageResponse:
    summary = await repository.admin_usage_summary()
    return AdminUsageResponse(
        usage_date=summary["usage_date"],
        by_runtime=[
            {
                "runtime": row["runtime"],
                "calls": int(row["calls"]),
                "total_tokens": int(row["total_tokens"]),
                "estimated_cost_usd": decimal_string(row["estimated_cost_usd"]),
            }
            for row in summary["by_runtime"]
        ],
        by_principal=[
            {
                "principal_id": row["principal_id"],
                "queries_used": int(row["queries_used"]),
                "estimated_cost_usd": decimal_string(row["estimated_cost_usd"]),
            }
            for row in summary["by_principal"]
        ],
    )