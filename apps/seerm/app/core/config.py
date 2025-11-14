"""
Configuration management for SeeRM application.

Provides centralized, validated configuration from environment variables
with proper type checking and defaults.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GmailConfig(BaseSettings):
    """Gmail API configuration."""

    client_id: Optional[str] = Field(default=None, alias="GMAIL_CLIENT_ID")
    client_secret: Optional[str] = Field(default=None, alias="GMAIL_CLIENT_SECRET")
    refresh_token: Optional[str] = Field(default=None, alias="GMAIL_REFRESH_TOKEN")
    user: Optional[str] = Field(default=None, alias="GMAIL_USER")

    # Query settings
    query: str = Field(
        default=(
            "from:metabase@mercury.com "
            'subject:"Alert: SeeRM Master Query has results" '
            "has:attachment filename:csv newer_than:10d"
        ),
        alias="GMAIL_QUERY",
    )
    attachment_regex: str = Field(default=r".*\.csv$", alias="ATTACHMENT_REGEX")

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")


class DigestConfig(BaseSettings):
    """Email digest configuration."""

    to: Optional[str] = Field(default=None, alias="DIGEST_TO")
    cc: Optional[str] = Field(default=None, alias="DIGEST_CC")
    bcc: Optional[str] = Field(default=None, alias="DIGEST_BCC")
    subject: Optional[str] = Field(default=None, alias="DIGEST_SUBJECT")
    top_movers: int = Field(default=15, alias="TOP_MOVERS")

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")


class NotionConfig(BaseSettings):
    """Notion API configuration."""

    api_key: Optional[str] = Field(default=None, alias="NOTION_API_KEY")
    version: str = Field(default="2022-06-28", alias="NOTION_VERSION")
    companies_db_id: Optional[str] = Field(default=None, alias="NOTION_COMPANIES_DB_ID")
    intel_db_id: Optional[str] = Field(default=None, alias="NOTION_INTEL_DB_ID")
    reports_db_id: Optional[str] = Field(default=None, alias="NOTION_REPORTS_DB_ID")

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")


class IntelligenceConfig(BaseSettings):
    """News intelligence configuration."""

    # Source filtering
    filter_callsigns: Union[List[str], str, None] = Field(
        default_factory=list, alias="FILTER_CALLSIGNS"
    )
    lookback_days: int = Field(default=10, alias="INTEL_LOOKBACK_DAYS")
    max_per_org: int = Field(default=5, alias="INTEL_MAX_PER_ORG")
    preview_only: bool = Field(default=True, alias="PREVIEW_ONLY")

    # Profile data
    news_profile_subject: str = Field(
        default="Org Profile — Will Mitchell", alias="NEWS_PROFILE_SUBJECT"
    )

    # Google Custom Search
    google_api_key: Optional[str] = Field(default=None, alias="GOOGLE_API_KEY")
    google_cse_id: Optional[str] = Field(default=None, alias="GOOGLE_CSE_ID")
    cse_disable: bool = Field(default=False, alias="CSE_DISABLE")
    cse_max_queries_per_org: int = Field(default=5, alias="CSE_MAX_QUERIES_PER_ORG")

    # OpenAI for summaries
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5-mini", alias="OPENAI_CHAT_MODEL")
    openai_temperature: Optional[float] = Field(default=0.2, alias="OPENAI_TEMPERATURE")

    # News quality tuning
    trusted_domains: Union[List[str], str, None] = Field(
        default_factory=list, alias="NEWS_TRUSTED_DOMAINS"
    )
    blocked_domains: Union[List[str], str, None] = Field(
        default_factory=list, alias="NEWS_BLOCKED_DOMAINS"
    )
    demoted_domains: Union[List[str], str, None] = Field(
        default_factory=list, alias="NEWS_DEMOTED_DOMAINS"
    )
    positive_keywords: Union[List[str], str, None] = Field(
        default_factory=list, alias="NEWS_POSITIVE_KEYWORDS"
    )
    negative_keywords: Union[List[str], str, None] = Field(
        default_factory=list, alias="NEWS_NEGATIVE_KEYWORDS"
    )

    # Intelligence Reports configuration
    reports_enabled: bool = Field(default=True, alias="INTELLIGENCE_REPORTS_ENABLED")
    default_report_days: int = Field(default=7, alias="INTELLIGENCE_DEFAULT_REPORT_DAYS")
    max_news_items_per_company: int = Field(default=10, alias="INTELLIGENCE_MAX_NEWS_PER_COMPANY")
    risk_assessment_enabled: bool = Field(
        default=True, alias="INTELLIGENCE_RISK_ASSESSMENT_ENABLED"
    )

    @field_validator("filter_callsigns", mode="before")
    @classmethod
    def parse_callsigns(cls, v):  # noqa: D102
        if isinstance(v, str):
            return [c.strip().lower() for c in v.split(",") if c.strip()]
        return v or []

    @field_validator("preview_only", mode="before")
    @classmethod
    def parse_preview_only(cls, v):  # noqa: D102
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes", "y")
        return bool(v)

    @field_validator("cse_disable", mode="before")
    @classmethod
    def parse_cse_disable(cls, v):  # noqa: D102
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)

    @field_validator(
        "trusted_domains",
        "blocked_domains",
        "demoted_domains",
        "positive_keywords",
        "negative_keywords",
        mode="before",
    )
    @classmethod
    def parse_quality_lists(cls, v):  # noqa: D102
        if isinstance(v, str):
            return [item.strip().lower() for item in v.split(",") if item.strip()]
        return v or []

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")


class BaselineConfig(BaseSettings):
    """Baseline dossier configuration."""

    callsigns: List[str] = Field(default_factory=list, alias="BASELINE_CALLSIGNS")
    debug: bool = Field(default=False, alias="BASELINE_DEBUG")
    use_notion_flags: bool = Field(default=False, alias="BASELINE_USE_NOTION_FLAGS")
    use_llm_intel: bool = Field(default=False, alias="BASELINE_USE_LLM_INTEL")

    @field_validator("callsigns", mode="before")
    @classmethod
    def parse_callsigns(cls, v):  # noqa: D102
        if isinstance(v, str):
            return [c.strip().lower() for c in v.split(",") if c.strip()]
        return v or []

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, v):  # noqa: D102
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)

    @field_validator("use_notion_flags", mode="before")
    @classmethod
    def parse_use_notion_flags(cls, v):  # noqa: D102
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)

    @field_validator("use_llm_intel", mode="before")
    @classmethod
    def parse_use_llm_intel(cls, v):  # noqa: D102
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")


class Settings(BaseSettings):
    """Main application settings."""

    # Environment
    environment: str = Field(default="production", alias="ENVIRONMENT")
    debug: bool = Field(default=False, alias="DEBUG")
    dry_run: bool = Field(default=False, alias="DRY_RUN")

    # Data source paths
    csv_source_path: Optional[str] = Field(default=None, alias="CSV_SOURCE_PATH")
    relationship_manager_name: Optional[str] = Field(
        default="Will Mitchell", alias="RELATIONSHIP_MANAGER_NAME"
    )

    # Component configurations
    gmail: GmailConfig = Field(default_factory=GmailConfig)
    digest: DigestConfig = Field(default_factory=DigestConfig)
    notion: NotionConfig = Field(default_factory=NotionConfig)
    intelligence: IntelligenceConfig = Field(default_factory=IntelligenceConfig)
    baseline: BaselineConfig = Field(default_factory=BaselineConfig)

    # Performance settings
    max_workers: int = Field(default=6, alias="MAX_WORKERS")
    request_timeout: int = Field(default=30, alias="REQUEST_TIMEOUT")
    rate_limit_calls_per_second: float = Field(default=2.5, alias="RATE_LIMIT_CALLS_PER_SECOND")

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, v):  # noqa: D102
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)

    @field_validator("relationship_manager_name", mode="before")
    @classmethod
    def normalize_relationship_manager_name(cls, v):  # noqa: D102
        if v is None:
            return None
        value = str(v).strip()
        return value or None

    @field_validator("dry_run", mode="before")
    @classmethod
    def parse_dry_run(cls, v):  # noqa: D102
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)

    def model_post_init(self, __context) -> None:  # noqa: D102
        # Initialize sub-configurations
        self.gmail = GmailConfig()
        self.digest = DigestConfig()
        self.notion = NotionConfig()
        self.intelligence = IntelligenceConfig()
        self.baseline = BaselineConfig()

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


# Global settings instance
settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the global settings instance.

    Load order for env file (first match wins):
      1. SEERM_CONFIG env var path
      2. ~/.seerm/.env
      3. ./.env (default handled by Settings())
    """
    global settings
    if settings is None:
        # 1) Explicit config path from env var
        seerm_cfg = os.getenv("SEERM_CONFIG")
        if seerm_cfg and Path(seerm_cfg).expanduser().exists():
            settings = Settings(_env_file=str(Path(seerm_cfg).expanduser()))
            return settings

        # 2) Home default
        home_env = Path("~/.seerm/.env").expanduser()
        if home_env.exists():
            settings = Settings(_env_file=str(home_env))
            return settings

        # 3) Fallback to default .env in CWD or pure env vars
        settings = Settings()
    return settings


def validate_required_settings(for_workflow: str = "digest") -> List[str]:
    """
    Validate that required settings are present for specific workflows.

    Args:
        for_workflow: Workflow name ("digest", "intelligence", or "minimal")

    Returns:
        List of missing required settings
    """
    missing = []
    try:
        config = get_settings()

        if for_workflow == "digest":
            # Digest workflow requires Gmail and Notion
            if not config.gmail.client_id:
                missing.append("GMAIL_CLIENT_ID")
            if not config.gmail.client_secret:
                missing.append("GMAIL_CLIENT_SECRET")
            if not config.gmail.refresh_token:
                missing.append("GMAIL_REFRESH_TOKEN")
            if not config.gmail.user:
                missing.append("GMAIL_USER")
            if not config.notion.api_key:
                missing.append("NOTION_API_KEY")

        elif for_workflow == "intelligence":
            # Intelligence reports only need CSV source - Gmail and Notion are optional
            csv_path = getattr(config, "csv_source_path", None)
            if not csv_path:
                missing.append("CSV_SOURCE_PATH")

        elif for_workflow == "minimal":
            # Minimal validation - just check basic config loads
            pass

    except Exception as e:
        missing.append(f"Configuration error: {e}")

    return missing


def validate_intelligence_reports_config() -> Dict[str, str]:
    """
    Validate intelligence reports configuration.

    Returns a dictionary with service status and messages.
    """
    status = {}

    try:
        config = get_settings()

        if not config.intelligence.reports_enabled:
            status["intelligence_reports"] = "disabled"
            return status

        # Check CSV access
        try:
            csv_path = getattr(config, "csv_source_path", None)
            if csv_path:
                status["csv_source"] = "configured"
            else:
                status["csv_source"] = "missing_csv_path"
        except Exception:
            status["csv_source"] = "error"

        # Check Notion Reports DB
        if config.notion.reports_db_id:
            status["notion_reports_db"] = "configured"
        else:
            status["notion_reports_db"] = "missing_reports_db_id"

        # Check optional enhancements
        if config.intelligence.openai_api_key:
            status["openai_summaries"] = "available"
        else:
            status["openai_summaries"] = "unavailable"

        if config.intelligence.google_api_key and config.intelligence.google_cse_id:
            status["google_search"] = "available"
        else:
            status["google_search"] = "unavailable"

        # Overall assessment
        required_services = ["csv_source"]
        missing_required = [
            k
            for k, v in status.items()
            if k in required_services and v not in ["configured", "available"]
        ]

        if not missing_required:
            status["overall"] = "ready"
        else:
            status["overall"] = "missing_requirements"

        return status

    except Exception as e:
        return {"overall": "configuration_error", "error": str(e)}


def print_configuration_summary():
    """Print a summary of the current configuration for debugging."""
    try:
        config = get_settings()
        print("=== SeeRM Configuration Summary ===")
        print(f"Environment: {config.environment}")
        print(f"Debug Mode: {config.debug}")
        print(f"Dry Run: {config.dry_run}")
        print(f"Max Workers: {config.max_workers}")
        print(f"Rate Limit: {config.rate_limit_calls_per_second} calls/sec")
        print()
        print(f"Gmail User: {config.gmail.user}")
        print(f"Digest Recipients: {config.digest.to or 'Same as Gmail user'}")
        print(f"Notion Companies DB: {'✓' if config.notion.companies_db_id else '✗'}")
        print(f"Notion Intel DB: {'✓' if config.notion.intel_db_id else '✗'}")
        print(f"Notion Reports DB: {'✓' if config.notion.reports_db_id else '✗'}")
        print(
            "Google CSE: "
            + (
                "✓"
                if (config.intelligence.google_api_key and config.intelligence.google_cse_id)
                else "✗"
            )
        )
        print(f"OpenAI: {'✓' if config.intelligence.openai_api_key else '✗'}")
        print()
        print("Intelligence Reports:")
        print(f"  Enabled: {'✓' if config.intelligence.reports_enabled else '✗'}")
        print(f"  Default Days: {config.intelligence.default_report_days}")
        print(f"  Risk Assessment: {'✓' if config.intelligence.risk_assessment_enabled else '✗'}")
        print(f"  Max News per Company: {config.intelligence.max_news_items_per_company}")

        # Show intelligence reports status
        reports_status = validate_intelligence_reports_config()
        overall_status = reports_status.get("overall", "unknown")
        if overall_status == "ready":
            print("  Status: ✓ Ready for reports")
        elif overall_status == "disabled":
            print("  Status: ✗ Disabled")
        elif overall_status == "missing_requirements":
            print("  Status: ⚠ Missing requirements")
        else:
            print("  Status: ✗ Configuration error")

        print("=" * 35)
    except Exception as e:
        print(f"Error loading configuration: {e}")
