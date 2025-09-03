"""
Configuration management for SeeRM application.

Provides centralized, validated configuration from environment variables
with proper type checking and defaults.
"""

from __future__ import annotations

import os
from typing import Optional, List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GmailConfig(BaseSettings):
    """Gmail API configuration."""
    
    client_id: str = Field(..., env="GMAIL_CLIENT_ID")
    client_secret: str = Field(..., env="GMAIL_CLIENT_SECRET")
    refresh_token: str = Field(..., env="GMAIL_REFRESH_TOKEN")
    user: str = Field(..., env="GMAIL_USER")
    
    # Query settings
    query: str = Field(
        default='from:metabase@mercury.com subject:"Alert: SeeRM Master Query has results" has:attachment filename:csv newer_than:10d',
        env="GMAIL_QUERY"
    )
    attachment_regex: str = Field(default=r".*\.csv$", env="ATTACHMENT_REGEX")
    
    model_config = SettingsConfigDict(env_prefix="")


class DigestConfig(BaseSettings):
    """Email digest configuration."""
    
    to: Optional[str] = Field(default=None, env="DIGEST_TO")
    cc: Optional[str] = Field(default=None, env="DIGEST_CC") 
    bcc: Optional[str] = Field(default=None, env="DIGEST_BCC")
    subject: Optional[str] = Field(default=None, env="DIGEST_SUBJECT")
    top_movers: int = Field(default=15, env="TOP_MOVERS")
    
    model_config = SettingsConfigDict(env_prefix="")


class NotionConfig(BaseSettings):
    """Notion API configuration."""
    
    api_key: str = Field(..., env="NOTION_API_KEY")
    version: str = Field(default="2022-06-28", env="NOTION_VERSION")
    companies_db_id: Optional[str] = Field(default=None, env="NOTION_COMPANIES_DB_ID")
    intel_db_id: Optional[str] = Field(default=None, env="NOTION_INTEL_DB_ID")
    
    model_config = SettingsConfigDict(env_prefix="")


class IntelligenceConfig(BaseSettings):
    """News intelligence configuration."""
    
    # Source filtering
    filter_callsigns: List[str] = Field(default_factory=list, env="FILTER_CALLSIGNS")
    lookback_days: int = Field(default=10, env="INTEL_LOOKBACK_DAYS")
    max_per_org: int = Field(default=5, env="INTEL_MAX_PER_ORG")
    preview_only: bool = Field(default=True, env="PREVIEW_ONLY")
    
    # Profile data
    news_profile_subject: str = Field(
        default="Org Profile — Will Mitchell",
        env="NEWS_PROFILE_SUBJECT"
    )
    
    # Google Custom Search
    google_api_key: Optional[str] = Field(default=None, env="GOOGLE_API_KEY")
    google_cse_id: Optional[str] = Field(default=None, env="GOOGLE_CSE_ID")
    cse_disable: bool = Field(default=False, env="CSE_DISABLE")
    cse_max_queries_per_org: int = Field(default=5, env="CSE_MAX_QUERIES_PER_ORG")
    
    # OpenAI for summaries
    openai_api_key: Optional[str] = Field(default=None, env="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", env="OPENAI_CHAT_MODEL")
    openai_temperature: Optional[float] = Field(default=0.2, env="OPENAI_TEMPERATURE")
    
    @field_validator("filter_callsigns", mode="before")
    @classmethod
    def parse_callsigns(cls, v):
        if isinstance(v, str):
            return [c.strip().lower() for c in v.split(",") if c.strip()]
        return v or []
    
    @field_validator("preview_only", mode="before")
    @classmethod
    def parse_preview_only(cls, v):
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes", "y")
        return bool(v)
    
    @field_validator("cse_disable", mode="before")
    @classmethod
    def parse_cse_disable(cls, v):
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)
    
    model_config = SettingsConfigDict(env_prefix="")


class BaselineConfig(BaseSettings):
    """Baseline dossier configuration."""
    
    callsigns: List[str] = Field(default_factory=list, env="BASELINE_CALLSIGNS")
    debug: bool = Field(default=False, env="BASELINE_DEBUG")
    
    @field_validator("callsigns", mode="before")
    @classmethod
    def parse_callsigns(cls, v):
        if isinstance(v, str):
            return [c.strip().lower() for c in v.split(",") if c.strip()]
        return v or []
    
    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, v):
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)
    
    model_config = SettingsConfigDict(env_prefix="")


class Settings(BaseSettings):
    """Main application settings."""
    
    # Environment
    environment: str = Field(default="production", env="ENVIRONMENT")
    debug: bool = Field(default=False, env="DEBUG")
    dry_run: bool = Field(default=False, env="DRY_RUN")
    
    # Component configurations
    gmail: GmailConfig = Field(default_factory=GmailConfig)
    digest: DigestConfig = Field(default_factory=DigestConfig)
    notion: NotionConfig = Field(default_factory=NotionConfig)
    intelligence: IntelligenceConfig = Field(default_factory=IntelligenceConfig)
    baseline: BaselineConfig = Field(default_factory=BaselineConfig)
    
    # Performance settings
    max_workers: int = Field(default=6, env="MAX_WORKERS")
    request_timeout: int = Field(default=30, env="REQUEST_TIMEOUT")
    rate_limit_calls_per_second: float = Field(default=2.5, env="RATE_LIMIT_CALLS_PER_SECOND")
    
    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, v):
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)
    
    @field_validator("dry_run", mode="before")
    @classmethod
    def parse_dry_run(cls, v):
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)
    
    def model_post_init(self, __context) -> None:
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
        case_sensitive=False
    )


# Global settings instance
settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global settings
    if settings is None:
        settings = Settings()
    return settings


def validate_required_settings() -> List[str]:
    """
    Validate that all required settings are present.
    Returns a list of missing required settings.
    """
    missing = []
    try:
        config = get_settings()
        
        # Check Gmail required fields
        if not config.gmail.client_id:
            missing.append("GMAIL_CLIENT_ID")
        if not config.gmail.client_secret:
            missing.append("GMAIL_CLIENT_SECRET")
        if not config.gmail.refresh_token:
            missing.append("GMAIL_REFRESH_TOKEN")
        if not config.gmail.user:
            missing.append("GMAIL_USER")
        
        # Check Notion required fields
        if not config.notion.api_key:
            missing.append("NOTION_API_KEY")
        
    except Exception as e:
        missing.append(f"Configuration error: {e}")
    
    return missing


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
        print(f"Google CSE: {'✓' if config.intelligence.google_api_key and config.intelligence.google_cse_id else '✗'}")
        print(f"OpenAI: {'✓' if config.intelligence.openai_api_key else '✗'}")
        print("=" * 35)
    except Exception as e:
        print(f"Error loading configuration: {e}")