"""Configuration helpers for the messaging consumer."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MessagingSettings(BaseSettings):
    """Unified settings for Notion, OpenAI, and Gmail."""

    # Notion
    notion_api_key: str = Field(..., alias="NOTION_API_KEY")
    notion_companies_db_id: str = Field(..., alias="NOTION_COMPANIES_DB_ID")
    notion_intel_db_id: Optional[str] = Field(None, alias="NOTION_INTEL_DB_ID")
    notion_reports_db_id: Optional[str] = Field(None, alias="NOTION_REPORTS_DB_ID")

    # OpenAI fine-tuned voice model
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    reasoning_model_id: str = Field(default="gpt-4o-mini", alias="REASONING_MODEL_ID")
    reasoning_model_temperature: float = Field(0.2, alias="REASONING_MODEL_TEMPERATURE")
    voice_model_id: str = Field(
        default="ft:gpt-4o-2024-08-06:mercury-technologies-inc:wam:CYJP0xCA",
        alias="VOICE_MODEL_ID",
    )
    voice_model_temperature: float = Field(0.3, alias="VOICE_MODEL_TEMPERATURE")

    # Gmail API (same as SeeRM)
    gmail_client_id: str = Field(..., alias="GMAIL_CLIENT_ID")
    gmail_client_secret: str = Field(..., alias="GMAIL_CLIENT_SECRET")
    gmail_refresh_token: str = Field(..., alias="GMAIL_REFRESH_TOKEN")
    gmail_user: str = Field(..., alias="GMAIL_USER")
    gmail_template_label: str = Field(default="SEERM", alias="GMAIL_TEMPLATE_LABEL")
    gmail_template_subject_tag: str = Field(
        default="[SEERM TEMPLATE]", alias="GMAIL_TEMPLATE_SUBJECT_TAG"
    )

    # Template + drafting defaults
    greeting_template_path: Path = Field(
        default=Path("templates/greeting_template.html"),
        alias="GREETING_TEMPLATE_PATH",
    )
    default_subject: str = Field(
        default="Quick intro from your Mercury RM",
        alias="GREETING_DEFAULT_SUBJECT",
    )

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=(".env.local", ".env"),
        extra="ignore",
    )

    def load_template(self) -> str:
        path = self.greeting_template_path
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        if not path.exists():
            raise FileNotFoundError(f"Greeting template not found at {path}")
        return path.read_text(encoding="utf-8")
