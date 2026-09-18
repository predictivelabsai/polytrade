"""Internal gateway records and authentication context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True)
class Principal:
    id: str
    issuer: Literal["assethero", "clerk"]
    subject: str
    scopes: frozenset[str] = field(default_factory=frozenset)


@dataclass
class ChallengeRecord:
    id: str
    principal_id: str
    wallet_address: str
    signature_type: int
    funder_address: str | None
    timestamp_seconds: int
    nonce: int
    typed_data: dict[str, Any]
    expires_at: datetime
    used_at: datetime | None = None


@dataclass
class WalletSessionRecord:
    id: str
    principal_id: str
    wallet_address: str
    signature_type: int
    funder_address: str | None
    encrypted_credentials: str
    idle_expires_at: datetime
    absolute_expires_at: datetime
    last_used_at: datetime
    revoked_at: datetime | None = None


@dataclass
class OrderIntentRecord:
    id: str
    principal_id: str
    session_id: str
    idempotency_key: str
    proposal: dict[str, Any]
    order_type: Literal["GTC", "GTD", "FOK", "FAK"]
    post_only: bool
    typed_data: dict[str, Any]
    unsigned_order: dict[str, Any]
    status: Literal["PENDING", "SUBMITTING", "SUBMITTED", "REJECTED", "AMBIGUOUS", "EXPIRED"]
    expires_at: datetime
    signature_suffix: str | None = None
    signed_order_hash: str | None = None
    upstream_response: Any = None
    submitted_at: datetime | None = None
