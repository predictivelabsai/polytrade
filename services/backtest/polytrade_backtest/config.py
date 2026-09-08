from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BacktestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        env_ignore_empty=True,
        extra="ignore",
    )

    DATABASE_URL: SecretStr
    REDIS_BROKER_URL: SecretStr = SecretStr("redis://localhost:6379/0")
    REDIS_RESULT_URL: SecretStr = SecretStr("redis://localhost:6379/1")
    CORS_ORIGINS: str

    ASSETHERO_API_ISSUER: AnyHttpUrl | None = None
    ASSETHERO_API_JWKS_URL: AnyHttpUrl | None = None
    ASSETHERO_API_AUDIENCE: Literal["polytrade"] = "polytrade"
    CLERK_ISSUER: AnyHttpUrl
    CLERK_JWKS_URL: AnyHttpUrl
    CLERK_AUDIENCE: Literal["polytrade"] = "polytrade"

    POLYMARKET_GAMMA_URL: AnyHttpUrl = Field(default="https://gamma-api.polymarket.com")
    POLYMARKET_CLOB_URL: AnyHttpUrl = Field(default="https://clob.polymarket.com")
    POLYMARKET_REQUEST_TIMEOUT_SECONDS: float = Field(default=15, ge=1, le=60)
    BACKTEST_MAX_POINTS: int = Field(default=2_000_000, ge=10_000, le=5_000_000)
    BACKTEST_WORKER_CONCURRENCY: int = Field(default=2, ge=1, le=16)
    BACKTEST_MAX_ACTIVE_RUNS_PER_OWNER: int = Field(default=10, ge=3, le=50)
    BACKTEST_MAX_RETRIES: int = Field(default=5, ge=0, le=10)
    BACKTEST_SOFT_TIME_LIMIT_SECONDS: int = Field(default=840, ge=30, le=3_600)
    BACKTEST_HARD_TIME_LIMIT_SECONDS: int = Field(default=900, ge=60, le=3_900)
    BACKTEST_VISIBILITY_TIMEOUT_SECONDS: int = Field(default=1_200, ge=120, le=7_200)
    BACKTEST_STALE_SECONDS: int = Field(default=960, ge=120, le=7_200)
    BACKTEST_OUTBOX_INTERVAL_SECONDS: float = Field(default=2, ge=0.25, le=30)

    @field_validator(
        "ASSETHERO_API_ISSUER",
        "ASSETHERO_API_JWKS_URL",
        "CLERK_ISSUER",
        "CLERK_JWKS_URL",
        "POLYMARKET_GAMMA_URL",
        "POLYMARKET_CLOB_URL",
    )
    @classmethod
    def public_urls_use_https(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("Public service URLs must use HTTPS")
        return value

    @field_validator("REDIS_BROKER_URL", "REDIS_RESULT_URL")
    @classmethod
    def redis_url_required(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("Redis URL must use redis:// or rediss://")
        return value

    @model_validator(mode="after")
    def validate_settings(self) -> "BacktestSettings":
        if (self.ASSETHERO_API_ISSUER is None) != (self.ASSETHERO_API_JWKS_URL is None):
            raise ValueError(
                "ASSETHERO_API_ISSUER and ASSETHERO_API_JWKS_URL must be configured together"
            )
        if self.ASSETHERO_API_ISSUER is not None and (
            str(self.ASSETHERO_API_ISSUER).rstrip("/")
            == str(self.CLERK_ISSUER).rstrip("/")
        ):
            raise ValueError("AssetHero API and Clerk issuers must differ")
        if self.BACKTEST_SOFT_TIME_LIMIT_SECONDS >= self.BACKTEST_HARD_TIME_LIMIT_SECONDS:
            raise ValueError("Soft time limit must be lower than hard time limit")
        if self.BACKTEST_VISIBILITY_TIMEOUT_SECONDS <= self.BACKTEST_HARD_TIME_LIMIT_SECONDS:
            raise ValueError("Redis visibility timeout must exceed the hard time limit")
        if self.BACKTEST_STALE_SECONDS <= self.BACKTEST_HARD_TIME_LIMIT_SECONDS:
            raise ValueError("Stale-run timeout must exceed the hard time limit")
        if not self.cors_origins:
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        return self

    @property
    def cors_origins(self) -> tuple[str, ...]:
        origins: list[str] = []
        for raw in self.CORS_ORIGINS.split(","):
            origin = raw.strip().rstrip("/")
            if not origin:
                continue
            parsed = urlparse(origin)
            if (
                "*" in origin
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS_ORIGINS must contain exact HTTP(S) origins")
            origins.append(origin)
        return tuple(dict.fromkeys(origins))


@lru_cache(maxsize=1)
def get_settings() -> BacktestSettings:
    return BacktestSettings()  # type: ignore[call-arg]
