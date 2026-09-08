"""Credential-safe, tenant-scoped agent activity logging.

Ported from the legacy chat stack's ``chat/activity.py``. Every stored text
field passes through :func:`redact_text` so leaked keys in prompts or model
output never reach the database.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from .storage import AgentRepository

_PATTERNS = (
    re.compile(r"\bxai-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\b(api[_ -]?key|secret|token|password)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bBearer\s+\S+"),
)

MAX_STORED_CHARS = 50_000

# Only these metadata keys survive into the stored jsonb payload.
ACTIVITY_METADATA_KEYS = frozenset({"runtime", "requested_runtime", "fallback", "code"})


def redact_text(value: Any) -> str:
    result = str(value or "")[:MAX_STORED_CHARS]
    result = _PATTERNS[0].sub("[REDACTED_XAI_KEY]", result)
    result = _PATTERNS[1].sub(r"\1=[REDACTED]", result)
    return _PATTERNS[2].sub("Bearer [REDACTED]", result)


def safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in (metadata or {}).items() if key in ACTIVITY_METADATA_KEYS}


async def record_activity_start(
    repository: AgentRepository,
    *,
    run_id: UUID,
    thread_id: UUID,
    principal_id: str,
    requested_runtime: str,
    runtime: str,
    request_text: str,
) -> None:
    await repository.insert_activity(
        run_id=run_id,
        thread_id=thread_id,
        principal_id=principal_id,
        requested_runtime=requested_runtime,
        runtime=runtime,
        request_text=redact_text(request_text),
    )


async def record_activity_complete(
    repository: AgentRepository,
    *,
    run_id: UUID,
    status: str,
    error_code: str | None,
    response_text: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await repository.complete_activity(
        run_id=run_id,
        status=status,
        error_code=redact_text(error_code) if error_code else None,
        response_text=redact_text(response_text) if response_text else None,
        metadata=safe_metadata(metadata),
    )