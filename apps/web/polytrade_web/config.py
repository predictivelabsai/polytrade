"""FastHTML runtime settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    API_URL: str = Field(default="http://localhost:4000", min_length=1)
    PUBLIC_ORIGIN: str = Field(default="http://localhost:5173", min_length=1)
