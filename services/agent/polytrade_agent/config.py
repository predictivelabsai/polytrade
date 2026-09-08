import os
from collections.abc import MutableMapping
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LANGSMITH_CREDENTIAL_ENV = (
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_ENDPOINT",
    "LANGCHAIN_ENDPOINT",
    "LANGSMITH_PROJECT",
    "LANGCHAIN_PROJECT",
    "LANGSMITH_WORKSPACE_ID",
)
LANGSMITH_TRACING_ENV = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_OTEL_ENABLED",
)


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        env_ignore_empty=True,
        extra="ignore",
    )

    DEEPSEEK_API_KEY: SecretStr
    DEEPSEEK_BASE_URL: AnyHttpUrl = Field(default="https://api.deepseek.com")
    GATEWAY_BASE_URL: AnyHttpUrl = Field(default="http://localhost:4000")
    BACKTEST_BASE_URL: AnyHttpUrl = Field(default="http://localhost:8100")
    AGENT_HTTP_TIMEOUT_SECONDS: float = Field(default=15, ge=1, le=60)
    AGENT_RUN_TIMEOUT_SECONDS: int = Field(default=300, ge=30, le=900)
    AGENT_MAX_CONCURRENT_RUNS: int = Field(default=8, ge=1, le=100)
    AGENT_MAX_MESSAGE_CHARS: int = Field(default=8_000, ge=1, le=32_000)
    AGENT_THREAD_TTL_DAYS: int = Field(default=30, ge=1, le=365)
    BACKTEST_MAX_ACTIVE_RUNS_PER_OWNER: int = Field(default=10, ge=3, le=50)

    DATABASE_URL: SecretStr
    LANGGRAPH_AES_KEY: SecretStr
    CORS_ORIGINS: str

    ASSETHERO_API_ISSUER: AnyHttpUrl | None = None
    ASSETHERO_API_JWKS_URL: AnyHttpUrl | None = None
    ASSETHERO_API_AUDIENCE: Literal["polytrade"] = "polytrade"
    CLERK_ISSUER: AnyHttpUrl
    CLERK_JWKS_URL: AnyHttpUrl
    CLERK_AUDIENCE: Literal["polytrade"] = "polytrade"

    @field_validator(
        "ASSETHERO_API_ISSUER",
        "ASSETHERO_API_JWKS_URL",
        "CLERK_ISSUER",
        "CLERK_JWKS_URL",
    )
    @classmethod
    def jwks_must_use_https(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("JWKS URL must use HTTPS")
        return value

    @model_validator(mode="after")
    def issuers_must_differ(self) -> "AgentSettings":
        if (self.ASSETHERO_API_ISSUER is None) != (self.ASSETHERO_API_JWKS_URL is None):
            raise ValueError(
                "ASSETHERO_API_ISSUER and ASSETHERO_API_JWKS_URL must be configured together"
            )
        if self.ASSETHERO_API_ISSUER is not None and (
            str(self.ASSETHERO_API_ISSUER).rstrip("/")
            == str(self.CLERK_ISSUER).rstrip("/")
        ):
            raise ValueError("AssetHero API and Clerk issuers must differ")
        if len(self.LANGGRAPH_AES_KEY.get_secret_value().encode("utf-8")) != 32:
            raise ValueError("LANGGRAPH_AES_KEY must contain exactly 32 UTF-8 bytes")
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
            ):
                raise ValueError("CORS_ORIGINS must contain exact HTTP(S) origins")
            if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
                raise ValueError("CORS_ORIGINS entries cannot contain paths, queries, or fragments")
            origins.append(origin)
        return tuple(dict.fromkeys(origins))


@lru_cache(maxsize=1)
def get_settings() -> AgentSettings:
    enforce_no_langsmith()
    return AgentSettings()  # type: ignore[call-arg]


def enforce_no_langsmith(
    environment: MutableMapping[str, str] | None = None,
) -> None:
    """Fail closed on LangSmith credentials/tracing and disable optional telemetry."""
    target = os.environ if environment is None else environment
    for name in LANGSMITH_CREDENTIAL_ENV:
        if target.get(name, "").strip():
            raise RuntimeError(f"{name} must not be configured for PolyTrade")
    for name in LANGSMITH_TRACING_ENV:
        configured = target.get(name, "").strip().lower()
        if configured not in {"", "0", "false", "off", "no"}:
            raise RuntimeError(f"{name} must be disabled for PolyTrade")
        target[name] = "false"
    target["LANGGRAPH_CLI_NO_ANALYTICS"] = "1"
