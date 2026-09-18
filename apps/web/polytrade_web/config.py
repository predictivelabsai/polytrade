"""FastHTML runtime settings."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

WEB_ROOT = Path(__file__).resolve().parents[1]


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=True,
        populate_by_name=True,
        env_file=(WEB_ROOT / ".env", WEB_ROOT / ".env.local"),
    )

    API_URL: str = Field(default="http://localhost:4000", min_length=1)
    PUBLIC_ORIGIN: str = Field(default="http://localhost:5173", min_length=1)
    AUTH_BYPASS: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "AUTH_BYPASS",
            "POLYTRADE_AUTH_BYPASS",
            "VITE_E2E_AUTH_BYPASS",
        ),
    )
