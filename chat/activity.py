"""Credential-safe, tenant-scoped user and agent activity history."""
from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from db.connection import get_pool, _record_to_dict

_PATTERNS = (
    re.compile(r"\bxai-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\b(api[_ -]?key|secret|token|password)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bBearer\s+\S+"),
)


def redact_text(value: Any) -> str:
    result = str(value or "")[:50_000]
    result = _PATTERNS[0].sub("[REDACTED_XAI_KEY]", result)
    result = _PATTERNS[1].sub(r"\1=[REDACTED]", result)
    return _PATTERNS[2].sub("Bearer [REDACTED]", result)


async def start_user_log(run_id: str, user_id: str | None, thread_id: str, request: str) -> None:
    if not user_id:
        return
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO polycode.user_logging(run_id,user_id,thread_id,request_text)
        VALUES($1,$2,$3,$4) ON CONFLICT(run_id) DO NOTHING
    """, UUID(run_id), UUID(user_id), UUID(thread_id), redact_text(request))


async def complete_user_log(run_id: str, *, response: str, framework: str,
                            status: str = "completed", error: str | None = None,
                            metadata: dict | None = None) -> None:
    pool = await get_pool()
    safe_meta = {k: v for k, v in (metadata or {}).items()
                 if k in {"runtime", "agent", "requested_runtime", "fallback", "code"}}
    await pool.execute("""
        UPDATE polycode.user_logging SET response_text=$2,agent_framework=$3,status=$4,
          error=$5,metadata=$6::jsonb,completed_at=NOW() WHERE run_id=$1
    """, UUID(run_id), redact_text(response), framework[:64], status,
         redact_text(error) if error else None, json.dumps(safe_meta, default=str))


async def list_user_logs(requester_id: str, *, is_admin: bool, email: str = "", limit: int = 100):
    pool = await get_pool()
    args: list[Any] = [] if is_admin else [UUID(requester_id)]
    clauses = [] if is_admin else ["l.user_id=$1"]
    if is_admin and email.strip():
        args.append(f"%{email.strip()}%")
        clauses.append(f"u.email ILIKE ${len(args)}")
    args.append(min(max(limit, 1), 500))
    where = " AND ".join(clauses) or "TRUE"
    rows = await pool.fetch(f"""
      SELECT l.*,u.email FROM polycode.user_logging l
      LEFT JOIN polycode.users u ON u.user_id=l.user_id WHERE {where}
      ORDER BY l.created_at DESC LIMIT ${len(args)}
    """, *args)
    return [_record_to_dict(row) for row in rows]


async def list_agent_logs(requester_id: str, *, is_admin: bool, email: str = "", limit: int = 100):
    pool = await get_pool()
    args: list[Any] = [] if is_admin else [UUID(requester_id)]
    clauses = [] if is_admin else ["l.user_id=$1"]
    if is_admin and email.strip():
        args.append(f"%{email.strip()}%")
        clauses.append(f"u.email ILIKE ${len(args)}")
    args.append(min(max(limit, 1), 500))
    where = " AND ".join(clauses) or "TRUE"
    rows = await pool.fetch(f"""
      SELECT l.*,u.email FROM polycode.agent_logging l
      LEFT JOIN polycode.users u ON u.user_id=l.user_id WHERE {where}
      ORDER BY l.updated_at DESC LIMIT ${len(args)}
    """, *args)
    return [_record_to_dict(row) for row in rows]
