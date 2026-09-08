"""
User authentication and credential management for PolyTrade.

Provides password hashing (bcrypt), user CRUD (asyncpg), password reset tokens,
and JWT token handling.
"""

import os
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Iterable, Optional
from uuid import NAMESPACE_URL, UUID, uuid5

import bcrypt as _bcrypt
from cryptography.fernet import Fernet

from db.connection import get_pool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its bcrypt hash."""
    if len(password.encode("utf-8")) > 72:
        return False
    return _bcrypt.checkpw(password.encode(), password_hash.encode())


# ---------------------------------------------------------------------------
# User CRUD (async, asyncpg)
# ---------------------------------------------------------------------------

async def create_user(
    email: str,
    password: Optional[str] = None,
    display_name: Optional[str] = None,
) -> Optional[Dict]:
    """Create a new user. Returns user dict or None if email already exists."""
    pw_hash = hash_password(password) if password else None
    pool = await get_pool()
    row = await pool.fetchrow("""
        INSERT INTO polycode.users (email, password_hash, display_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (email) DO NOTHING
        RETURNING user_id, email, display_name, is_admin, is_active, created_at
    """, email.lower().strip(), pw_hash, display_name or email.split("@")[0])
    if not row:
        return None
    return _row_to_user(row)


async def get_user_by_email(email: str) -> Optional[Dict]:
    """Fetch a user by email address."""
    pool = await get_pool()
    row = await pool.fetchrow("""
        SELECT user_id, email, password_hash, display_name, is_admin, is_active, created_at
        FROM polycode.users
        WHERE email = $1 AND is_active = TRUE
    """, email.lower().strip())
    if not row:
        return None
    return _row_to_user(row)


async def get_user_by_id(user_id: str) -> Optional[Dict]:
    """Fetch a user by user_id (UUID)."""
    pool = await get_pool()
    uid = UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id
    row = await pool.fetchrow("""
        SELECT user_id, email, password_hash, display_name, is_admin, is_active, created_at
        FROM polycode.users
        WHERE user_id = $1 AND is_active = TRUE
    """, uid)
    if not row:
        return None
    return _row_to_user(row)


async def authenticate(email: str, password: str) -> Optional[Dict]:
    """Authenticate by email + password. Returns user dict on success, None on failure."""
    user = await get_user_by_email(email)
    if not user:
        return None
    pw_hash = user.get("password_hash")
    if not pw_hash:
        return None
    if not verify_password(password, pw_hash):
        return None
    user.pop("password_hash", None)
    return user


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

async def create_password_reset_token(email: str) -> Optional[str]:
    """Generate a password-reset token (1-hour expiry). Returns token or None."""
    user = await get_user_by_email(email)
    if not user:
        return None
    token = secrets.token_urlsafe(48)
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO polycode.password_reset_tokens (user_id, token, expires_at)
        VALUES ($1, $2, $3)
    """, UUID(user["user_id"]), token, datetime.now(timezone.utc) + timedelta(hours=1))
    return token


async def verify_and_consume_reset_token(token: str) -> Optional[Dict]:
    """Verify a reset token is valid and not expired. Marks as used. Returns user dict or None."""
    pool = await get_pool()
    row = await pool.fetchrow("""
        SELECT t.user_id, u.email, u.display_name
        FROM polycode.password_reset_tokens t
        JOIN polycode.users u ON u.user_id = t.user_id
        WHERE t.token = $1
          AND t.used_at IS NULL
          AND t.expires_at > $2
          AND u.is_active = TRUE
    """, token, datetime.now(timezone.utc))
    if not row:
        return None
    # Mark token as consumed
    await pool.execute("""
        UPDATE polycode.password_reset_tokens SET used_at = $1 WHERE token = $2
    """, datetime.now(timezone.utc), token)
    return {"user_id": str(row["user_id"]), "email": row["email"], "display_name": row["display_name"]}


async def update_password(user_id: str, new_password: str) -> bool:
    """Update a user's password hash."""
    pw_hash = hash_password(new_password)
    pool = await get_pool()
    uid = UUID(str(user_id))
    result = await pool.execute("""
        UPDATE polycode.users SET password_hash = $1, updated_at = NOW()
        WHERE user_id = $2 AND is_active = TRUE
    """, pw_hash, uid)
    return result.endswith("1")  # "UPDATE 1"


async def update_display_name(user_id: str, display_name: str) -> bool:
    """Update a user's display name."""
    pool = await get_pool()
    uid = UUID(str(user_id))
    result = await pool.execute("""
        UPDATE polycode.users SET display_name = $1, updated_at = NOW()
        WHERE user_id = $2 AND is_active = TRUE
    """, display_name.strip(), uid)
    return result.endswith("1")


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_jwt_token(user_id: str, email: str) -> str:
    """Create the legacy unscoped web-session JWT."""
    import jwt
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET not set")
    payload = {
        "user_id": str(user_id),
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_jwt_token(token: str) -> Optional[Dict]:
    """Decode the legacy web-session JWT. Returns payload dict or None."""
    import jwt
    secret = os.getenv("JWT_SECRET")
    if not secret:
        return None
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def _create_api_access_token(
    *,
    subject: str,
    user_id: str,
    email: str,
    scopes: Iterable[str],
    expires_seconds: int,
    principal_type: str,
    client_id: Optional[str] = None,
) -> str:
    """Create a signed, scoped access token for a user or service principal."""
    import jwt

    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET not set")
    now = datetime.now(timezone.utc)
    issuer = os.getenv("JWT_ISSUER", "polytrade")
    audience = os.getenv("JWT_AUDIENCE", "polytrade-api")
    payload = {
        "sub": subject,
        "user_id": str(user_id),
        "email": email,
        "principal_type": principal_type,
        "purpose": "api_access",
        "scope": " ".join(sorted(set(scopes))),
        "iss": issuer,
        "aud": audience,
        "jti": secrets.token_urlsafe(24),
        "iat": now,
        "exp": now + timedelta(seconds=max(60, min(expires_seconds, 86400))),
    }
    if client_id:
        payload["client_id"] = client_id
    return jwt.encode(payload, secret, algorithm="HS256")


def create_api_token(user_id: str, email: str, expires_minutes: int = 60) -> str:
    """Create a scoped access token for a PolyTrade end user."""
    return _create_api_access_token(
        subject=str(user_id),
        user_id=str(user_id),
        email=email,
        scopes=("chat:read", "chat:write"),
        expires_seconds=max(1, min(expires_minutes, 1440)) * 60,
        principal_type="user",
    )


def derive_service_user_id(client_id: str) -> str:
    """Return the stable UUID used to own one service client's chat data."""
    return str(
        uuid5(
            NAMESPACE_URL,
            f"https://polytrade.chat/service-principals/{client_id}",
        )
    )


def create_service_api_token(
    client_id: str,
    user_id: str,
    expires_seconds: int = 900,
) -> str:
    """Create a short-lived, chat-only token for a trusted backend service."""
    return _create_api_access_token(
        subject=f"service:{client_id}",
        user_id=str(user_id),
        email="",
        scopes=("chat:read", "chat:write"),
        expires_seconds=max(60, min(expires_seconds, 3600)),
        principal_type="service",
        client_id=client_id,
    )


def decode_api_token(token: str) -> Optional[Dict]:
    """Verify an API access token, including issuer, audience, and purpose."""
    import jwt

    secret = os.getenv("JWT_SECRET")
    if not secret:
        return None
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=os.getenv("JWT_ISSUER", "polytrade"),
            audience=os.getenv("JWT_AUDIENCE", "polytrade-api"),
        )
        if payload.get("purpose") != "api_access":
            return None
        if not payload.get("user_id"):
            return None
        principal_type = payload.get("principal_type", "user")
        if principal_type not in {"user", "service"}:
            return None
        if principal_type == "service" and not payload.get("client_id"):
            return None
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------

def session_login(session, user: Dict):
    """Set session state after successful login."""
    session["user"] = {
        "user_id": str(user["user_id"]),
        "email": user["email"],
        "display_name": user.get("display_name", ""),
        "is_admin": bool(user.get("is_admin")),
    }


def _credential_cipher() -> Fernet:
    key = os.getenv("ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is required to store provider keys")
    return Fernet(key.encode())


async def store_provider_api_key(user_id: str, provider: str, api_key: str) -> None:
    """Encrypt one provider key before persistence. Never log the plaintext."""
    provider = (provider or "").strip().lower()
    api_key = (api_key or "").strip()
    if provider != "xai" or not api_key:
        raise ValueError("A non-empty xAI key is required")
    hint = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) >= 10 else "configured"
    encrypted = _credential_cipher().encrypt(api_key.encode())
    pool = await get_pool()
    await pool.execute("""
      INSERT INTO polycode.user_provider_credentials
        (user_id,provider,api_key_enc,api_key_hint,is_active)
      VALUES($1,$2,$3,$4,TRUE)
      ON CONFLICT(user_id,provider) DO UPDATE SET api_key_enc=EXCLUDED.api_key_enc,
        api_key_hint=EXCLUDED.api_key_hint,is_active=TRUE,updated_at=NOW()
    """, UUID(user_id), provider, encrypted, hint)


async def get_provider_api_key(user_id: str | None, provider: str = "xai") -> Optional[str]:
    if not user_id:
        return None
    pool = await get_pool()
    value = await pool.fetchval("""
      SELECT api_key_enc FROM polycode.user_provider_credentials
      WHERE user_id=$1 AND provider=$2 AND is_active=TRUE
    """, UUID(user_id), provider.lower())
    if not value:
        return None
    return _credential_cipher().decrypt(bytes(value)).decode()


async def get_provider_key_status(user_id: str, provider: str = "xai") -> Dict:
    pool = await get_pool()
    hint = await pool.fetchval("""
      SELECT api_key_hint FROM polycode.user_provider_credentials
      WHERE user_id=$1 AND provider=$2 AND is_active=TRUE
    """, UUID(user_id), provider.lower())
    return {"configured": bool(hint), "hint": hint or ""}


async def clear_provider_api_key(user_id: str, provider: str = "xai") -> None:
    pool = await get_pool()
    await pool.execute("""
      DELETE FROM polycode.user_provider_credentials WHERE user_id=$1 AND provider=$2
    """, UUID(user_id), provider.lower())


def create_cross_app_token(user_id: str, email: str) -> str:
    """Create a short-lived JWT (60s) for cross-app SSO (agui→web_app)."""
    import jwt
    secret = os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError("JWT_SECRET not set")
    payload = {
        "user_id": str(user_id),
        "email": email,
        "purpose": "cross_app_sso",
        "exp": datetime.now(timezone.utc) + timedelta(seconds=60),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_cross_app_token(token: str) -> Optional[Dict]:
    """Verify a cross-app SSO token. Returns {user_id, email} or None."""
    import jwt
    secret = os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY")
    if not secret:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        if payload.get("purpose") != "cross_app_sso":
            return None
        return {"user_id": payload["user_id"], "email": payload["email"]}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_user(row) -> Dict:
    """Convert an asyncpg Record to a user dict."""
    d = dict(row)
    if d.get("user_id"):
        d["user_id"] = str(d["user_id"])
    return d
