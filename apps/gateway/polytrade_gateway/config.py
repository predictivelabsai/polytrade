"""Validated gateway configuration."""

from __future__ import annotations

import base64
import ipaddress
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _origin(value: str, *, https_only: bool = False) -> str:
    parsed = urlparse(value)
    protocols = {"https"} if https_only else {"http", "https"}
    if parsed.scheme not in protocols or not parsed.netloc:
        raise ValueError("URL must use HTTPS" if https_only else "URL must use HTTP or HTTPS")
    if (
        parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("URL must be an origin without credentials, path, query, or fragment")
    return value.rstrip("/")


def _https_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("URL must use HTTPS")
    return value


class GatewayConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    NODE_ENV: Literal["development", "test", "production"] = "development"
    PORT: int = Field(default=4000, ge=1, le=65_535)
    DATABASE_URL: str = Field(min_length=1)
    CREDENTIALS_KEK_BASE64: str = Field(min_length=1)
    CORS_ORIGINS: str = "http://localhost:5173"
    TRUSTED_PROXIES: str = "127.0.0.1/32,::1/128"
    AGENT_UPSTREAM_URL: str = "http://localhost:8000"
    BACKTEST_UPSTREAM_URL: str = "http://localhost:8100"
    UPSTREAM_PROXY_TIMEOUT_MS: int = Field(default=60_000, ge=100, le=300_000)
    ASSETHERO_API_ISSUER: str | None = None
    ASSETHERO_API_JWKS_URL: str | None = None
    ASSETHERO_API_AUDIENCE: Literal["polytrade"] = "polytrade"
    CLERK_ISSUER: str
    CLERK_JWKS_URL: str
    CLERK_AUDIENCE: Literal["polytrade"] = "polytrade"
    POLYMARKET_GAMMA_URL: str = "https://gamma-api.polymarket.com"
    POLYMARKET_DATA_URL: str = "https://data-api.polymarket.com"
    POLYMARKET_CLOB_URL: str = "https://clob.polymarket.com"
    POLYMARKET_CHAIN_ID: int = 137
    POLYMARKET_REQUEST_TIMEOUT_MS: int = Field(default=10_000, ge=1_000, le=30_000)
    WALLET_CHALLENGE_TTL_SECONDS: int = Field(default=300, ge=60, le=600)
    WALLET_SESSION_IDLE_SECONDS: int = Field(default=14_400, ge=300, le=28_800)
    WALLET_SESSION_MAX_SECONDS: int = Field(default=86_400, ge=1_800, le=86_400)
    ORDER_INTENT_TTL_SECONDS: int = Field(default=120, ge=30, le=300)
    TELEGRAM_BOT_TOKEN: str | None = None
    ALERT_SEND_TIMEOUT_MS: int = Field(default=5_000, ge=1_000, le=30_000)

    @field_validator(
        "ASSETHERO_API_ISSUER", "ASSETHERO_API_JWKS_URL", "TELEGRAM_BOT_TOKEN", mode="before"
    )
    @classmethod
    def blanks_are_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("CLERK_ISSUER", "ASSETHERO_API_ISSUER")
    @classmethod
    def issuer_origins(cls, value: str | None) -> str | None:
        return _origin(value, https_only=True) if value else value

    @field_validator("CLERK_JWKS_URL", "ASSETHERO_API_JWKS_URL")
    @classmethod
    def jwks_urls(cls, value: str | None) -> str | None:
        return _https_url(value) if value else value

    @field_validator("POLYMARKET_GAMMA_URL", "POLYMARKET_DATA_URL", "POLYMARKET_CLOB_URL")
    @classmethod
    def polymarket_https_urls(cls, value: str) -> str:
        return _origin(value, https_only=True)

    @field_validator("AGENT_UPSTREAM_URL", "BACKTEST_UPSTREAM_URL")
    @classmethod
    def internal_origins(cls, value: str) -> str:
        return _origin(value)

    @model_validator(mode="after")
    def validate_combinations(self):
        if self.NODE_ENV != "test" and self.UPSTREAM_PROXY_TIMEOUT_MS < 20_000:
            raise ValueError("UPSTREAM_PROXY_TIMEOUT_MS must be at least 20000 outside tests")
        if bool(self.ASSETHERO_API_ISSUER) != bool(self.ASSETHERO_API_JWKS_URL):
            raise ValueError(
                "ASSETHERO_API_ISSUER and ASSETHERO_API_JWKS_URL must be configured together"
            )
        if self.ASSETHERO_API_ISSUER == self.CLERK_ISSUER:
            raise ValueError("AssetHero API and Clerk issuers must differ")
        if self.TELEGRAM_BOT_TOKEN is not None and len(self.TELEGRAM_BOT_TOKEN) < 10:
            raise ValueError("TELEGRAM_BOT_TOKEN must contain at least 10 characters")
        _ = self.credential_key
        _ = self.cors_origins
        _ = self.trusted_proxies
        return self

    @property
    def credential_key(self) -> bytes:
        try:
            value = base64.b64decode(self.CREDENTIALS_KEK_BASE64, validate=True)
        except ValueError as exc:
            raise ValueError("CREDENTIALS_KEK_BASE64 must be valid base64") from exc
        if len(value) != 32:
            raise ValueError("CREDENTIALS_KEK_BASE64 must decode to exactly 32 bytes")
        return value

    @property
    def cors_origins(self) -> list[str]:
        origins = [value.strip() for value in self.CORS_ORIGINS.split(",") if value.strip()]
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        for value in origins:
            parsed = urlparse(value)
            exact = f"{parsed.scheme}://{parsed.netloc}"
            if exact != value or (self.NODE_ENV == "production" and parsed.scheme != "https"):
                raise ValueError("CORS_ORIGINS must contain exact origins (HTTPS in production)")
        return origins

    @property
    def trusted_proxies(self) -> list[str]:
        values = [value.strip() for value in self.TRUSTED_PROXIES.split(",") if value.strip()]
        for value in values:
            ipaddress.ip_network(value, strict=False)
        return values


def parse_config(environment: dict[str, str]) -> GatewayConfig:
    try:
        return GatewayConfig.model_validate(environment)
    except ValidationError:
        raise
