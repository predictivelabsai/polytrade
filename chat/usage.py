"""Daily AI allowance, BYOK funding attribution, and token/cost accounting."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from db.connection import get_pool, _record_to_dict
from .errors import ChatError

FREE_PLATFORM_QUERY_LIMIT = int(os.getenv("FREE_PLATFORM_QUERY_LIMIT", "5"))
PLATFORM_DAILY_BUDGET_USD = Decimal(os.getenv("PLATFORM_LLM_DAILY_BUDGET_USD", "5"))
DEFAULT_INPUT_USD_PER_M = Decimal(os.getenv("LLM_INPUT_USD_PER_MILLION", "1.25"))
DEFAULT_OUTPUT_USD_PER_M = Decimal(os.getenv("LLM_OUTPUT_USD_PER_MILLION", "2.50"))
HERMES_INPUT_USD_PER_M = Decimal(os.getenv("HERMES_INPUT_USD_PER_MILLION", "2"))
HERMES_OUTPUT_USD_PER_M = Decimal(os.getenv("HERMES_OUTPUT_USD_PER_MILLION", "6"))


class QueryLimitExceeded(ChatError):
    code = "query_limit_exceeded"
    status_code = 429
    retryable = False


class DailyBudgetExceeded(ChatError):
    code = "daily_budget_exceeded"
    status_code = 429
    retryable = True


@dataclass(frozen=True)
class QueryAuthorization:
    funding_source: str
    platform_slot: bool = False
    used: int = 0
    limit: int = FREE_PLATFORM_QUERY_LIMIT


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
        data = (getattr(data, "usage_metadata", None)
                or getattr(data, "response_metadata", None) or {})
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


def estimate_cost(usage: TokenUsage, *, agent: str, model: str) -> Decimal:
    hermes = agent.lower() == "hermes" or model.lower() == "hermes-agent"
    in_rate = HERMES_INPUT_USD_PER_M if hermes else DEFAULT_INPUT_USD_PER_M
    out_rate = HERMES_OUTPUT_USD_PER_M if hermes else DEFAULT_OUTPUT_USD_PER_M
    return (Decimal(usage.input_tokens) * in_rate
            + Decimal(usage.output_tokens) * out_rate) / Decimal(1_000_000)


async def daily_platform_cost(user_id: str | None = None) -> Decimal:
    pool = await get_pool()
    if user_id:
        value = await pool.fetchval("""
          SELECT COALESCE(SUM(estimated_cost_usd),0) FROM polycode.llm_usage_logging
          WHERE funding_source='platform' AND status='completed'
            AND created_at >= date_trunc('day',NOW()) AND user_id=$1
        """, UUID(user_id))
    else:
        value = await pool.fetchval("""
          SELECT COALESCE(SUM(estimated_cost_usd),0) FROM polycode.llm_usage_logging
          WHERE funding_source='platform' AND status='completed'
            AND created_at >= date_trunc('day',NOW())
        """)
    return Decimal(str(value or 0))


async def daily_user_cost(user_id: str) -> Decimal:
    pool = await get_pool()
    value = await pool.fetchval("""
      SELECT COALESCE(SUM(estimated_cost_usd),0) FROM polycode.llm_usage_logging
      WHERE status='completed' AND created_at >= date_trunc('day',NOW())
        AND user_id=$1
    """, UUID(user_id))
    return Decimal(str(value or 0))


async def authorize_query(user_id: str | None, *, has_byok: bool) -> QueryAuthorization:
    if has_byok:
        return QueryAuthorization("user_byok")
    if not user_id:
        raise QueryLimitExceeded("Sign in to use an AI agent.")
    spent = await daily_platform_cost()
    if spent >= PLATFORM_DAILY_BUDGET_USD:
        raise DailyBudgetExceeded(
            f"The platform's ${PLATFORM_DAILY_BUDGET_USD:.2f} daily AI budget is reached. "
            "Add your own xAI key in Settings or try again tomorrow."
        )
    pool = await get_pool()
    is_registered_user = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM polycode.users WHERE user_id=$1)",
        UUID(user_id),
    )
    if not is_registered_user:
        # Trusted API service principals share the platform dollar cap but do
        # not consume a human user's five daily starter slots.
        return QueryAuthorization("platform")
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("""
              INSERT INTO polycode.user_ai_daily_allowances(user_id,usage_date)
              VALUES($1,CURRENT_DATE) ON CONFLICT(user_id,usage_date) DO NOTHING
            """, UUID(user_id))
            used = await connection.fetchval("""
              UPDATE polycode.user_ai_daily_allowances
              SET platform_queries_used=platform_queries_used+1,updated_at=NOW()
              WHERE user_id=$1 AND usage_date=CURRENT_DATE
                AND platform_queries_used < $2
              RETURNING platform_queries_used
            """, UUID(user_id), max(FREE_PLATFORM_QUERY_LIMIT, 0))
    if used is None:
        raise QueryLimitExceeded(
            f"You have used today's {FREE_PLATFORM_QUERY_LIMIT} platform-funded AI queries. "
            "Add your own xAI key in Settings or try again tomorrow."
        )
    return QueryAuthorization("platform", True, int(used), FREE_PLATFORM_QUERY_LIMIT)


async def refund_query(user_id: str | None, authorization: QueryAuthorization | None) -> None:
    if not user_id or not authorization or not authorization.platform_slot:
        return
    pool = await get_pool()
    await pool.execute("""
      UPDATE polycode.user_ai_daily_allowances
      SET platform_queries_used=GREATEST(platform_queries_used-1,0),updated_at=NOW()
      WHERE user_id=$1 AND usage_date=CURRENT_DATE
    """, UUID(user_id))


async def record_usage(*, user_id: str, thread_id: str, request_id: str,
                       agent: str, provider: str, model: str, funding_source: str,
                       prompt: str, response: str, usage: TokenUsage | None = None) -> None:
    final = usage if usage and usage.total_tokens else estimate_usage(prompt, response)
    pool = await get_pool()
    await pool.execute("""
      INSERT INTO polycode.llm_usage_logging
        (user_id,thread_id,request_id,agent_framework,provider,model_name,
         funding_source,input_tokens,output_tokens,total_tokens,estimated_cost_usd,
         usage_quality,metadata)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'{"pricing":"configured_estimate"}'::jsonb)
    """, UUID(user_id), UUID(thread_id), request_id, agent, provider, model,
         funding_source, final.input_tokens, final.output_tokens, final.total_tokens,
         estimate_cost(final, agent=agent, model=model), final.quality)


async def get_usage_status(user_id: str, *, has_byok: bool) -> dict[str, Any]:
    pool = await get_pool()
    used = await pool.fetchval("""
      SELECT platform_queries_used FROM polycode.user_ai_daily_allowances
      WHERE user_id=$1 AND usage_date=CURRENT_DATE
    """, UUID(user_id)) or 0
    platform_cost = await daily_platform_cost()
    user_cost = await daily_user_cost(user_id)
    limit = max(FREE_PLATFORM_QUERY_LIMIT, 0)
    return {"funding_source": "user_byok" if has_byok else "platform",
            "used": int(used), "limit": limit, "remaining": max(limit-int(used), 0),
            "user_cost": user_cost, "platform_cost": platform_cost,
            "platform_budget": PLATFORM_DAILY_BUDGET_USD}


def render_usage_status(status: dict[str, Any]) -> str:
    return (
      "# AI usage today\n\n"
      f"- **Current funding:** {'Your xAI key' if status['funding_source']=='user_byok' else 'Platform'}\n"
      f"- **Platform queries used:** {status['used']} / {status['limit']}\n"
      f"- **Platform queries remaining:** {status['remaining']}\n"
      f"- **Your estimated AI cost today:** ${status['user_cost']:.4f}\n"
      f"- **Platform estimated spend today:** ${status['platform_cost']:.4f} / ${status['platform_budget']:.2f}\n\n"
      "Unprefixed AI questions, `/deepagent`, and `/hermes` consume one query when platform-funded. "
      "Raw deterministic commands without an agent prefix do not call a model."
    )


def allowance_warning(auth: QueryAuthorization) -> str | None:
    if auth.funding_source != "platform" or auth.limit <= 0:
        return None
    if auth.used >= auth.limit:
        return f"You have used all {auth.limit} platform-funded queries for today."
    if auth.used >= math.ceil(auth.limit * Decimal("0.8")):
        return f"You have used {auth.used} of {auth.limit} platform-funded queries today."
    return None


async def budget_warning(funding_source: str) -> str | None:
    if funding_source != "platform" or PLATFORM_DAILY_BUDGET_USD <= 0:
        return None
    spent = await daily_platform_cost()
    ratio = spent / PLATFORM_DAILY_BUDGET_USD
    if ratio < Decimal("0.8"):
        return None
    level = "90%" if ratio >= Decimal("0.9") else "80%"
    return (f"Platform AI spending reached the {level} warning level "
            f"(${spent:.2f} of ${PLATFORM_DAILY_BUDGET_USD:.2f} today).")


async def list_usage(requester_id: str, *, is_admin: bool, email: str = "", limit: int = 100):
    pool = await get_pool()
    args: list[Any] = [] if is_admin else [UUID(requester_id)]
    clauses = [] if is_admin else ["l.user_id=$1"]
    if is_admin and email.strip():
        args.append(f"%{email.strip()}%")
        clauses.append(f"u.email ILIKE ${len(args)}")
    args.append(min(max(limit, 1), 500))
    rows = await pool.fetch(f"""
      SELECT l.*,u.email FROM polycode.llm_usage_logging l
      LEFT JOIN polycode.users u ON u.user_id=l.user_id
      WHERE {' AND '.join(clauses) or 'TRUE'} ORDER BY l.created_at DESC LIMIT ${len(args)}
    """, *args)
    return [_record_to_dict(row) for row in rows]


async def usage_summary(requester_id: str, *, is_admin: bool, email: str = ""):
    pool = await get_pool()
    args: list[Any] = [] if is_admin else [UUID(requester_id)]
    clauses = ["l.created_at >= date_trunc('day',NOW())"]
    if not is_admin:
        clauses.append("l.user_id=$1")
    elif email.strip():
        args.append(f"%{email.strip()}%")
        clauses.append(f"u.email ILIKE ${len(args)}")
    rows = await pool.fetch(f"""
      SELECT COALESCE(u.email,'Unowned') email,l.agent_framework,l.funding_source,
        COUNT(*) calls,SUM(l.total_tokens) total_tokens,SUM(l.estimated_cost_usd) estimated_cost_usd
      FROM polycode.llm_usage_logging l LEFT JOIN polycode.users u ON u.user_id=l.user_id
      WHERE {' AND '.join(clauses)} GROUP BY u.email,l.agent_framework,l.funding_source
      ORDER BY estimated_cost_usd DESC
    """, *args)
    return [_record_to_dict(row) for row in rows]
