"""Configuration for the SayRM local service."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


from . import _ROOT


class SayRMSettings(BaseSettings):
    """Centralised settings for the SayRM workflow service."""

    # External context (Notion)
    notion_api_key: str = Field(..., alias="NOTION_API_KEY")
    notion_companies_db_id: str = Field(..., alias="NOTION_COMPANIES_DB_ID")
    notion_intel_db_id: Optional[str] = Field(None, alias="NOTION_INTEL_DB_ID")

    # LLM
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    llm_model: str = Field("gpt-5-mini", alias="SAYRM_LLM_MODEL")
    llm_temperature: float = Field(0.25, alias="SAYRM_LLM_TEMPERATURE")

    # Service runtime
    service_host: str = Field("127.0.0.1", alias="SAYRM_SERVICE_HOST")
    service_port: int = Field(8070, alias="SAYRM_SERVICE_PORT")
    database_path: Path = Field(Path("apps/SayRM/.sayrm.db"), alias="SAYRM_DB_PATH")
    template_path: Path = Field(
        Path("apps/SayRM/templates/templates.json"),
        alias="SAYRM_TEMPLATE_PATH",
    )

    # Optional internal usage API endpoint
    internal_usage_base_url: Optional[str] = Field(None, alias="SAYRM_INTERNAL_API")
    internal_usage_api_key: Optional[str] = Field(None, alias="SAYRM_INTERNAL_API_KEY")

    model_config = SettingsConfigDict(
        env_file=(".env.local", "apps/SayRM/.env.local", ".env"),
        env_prefix="",
        extra="ignore",
    )

    def resolved_template_path(self) -> Path:
        """Return the absolute template path."""
        path = self.template_path
        if not path.is_absolute():
            path = _ROOT / path
        return path

    def resolved_database_path(self) -> Path:
        """Return the absolute sqlite path and ensure parent exists."""
        path = self.database_path
        if not path.is_absolute():
            path = _ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
