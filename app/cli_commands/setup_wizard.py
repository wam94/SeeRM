"""
Provide an interactive setup wizard for SeeRM configuration.

Guide a non-technical user through configuring Gmail, CSV source,
Notion, and optional services. Write a .env file to a chosen location,
defaulting to ~/.seerm/.env, and optionally validate via health checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click
import structlog

from app.core.config import Settings
from app.data.csv_parser import filter_dataframe_by_relationship_manager

logger = structlog.get_logger(__name__)


DEFAULT_HOME_ENV = Path("~/.seerm/.env").expanduser()
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


@click.group()
def setup():
    """Provide setup and onboarding utilities."""
    pass


@setup.command()
@click.option(
    "--config-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=str(DEFAULT_HOME_ENV),
    help="Where to save your .env (default: ~/.seerm/.env)",
)
def run(config_path: Path):
    """Run the interactive setup wizard."""
    click.echo("SeeRM Setup Wizard")
    click.echo("=" * 40)

    # Ensure directory exists
    config_path = config_path.expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Detect existing config
    existing = {}
    if config_path.exists():
        click.echo(f"Found existing config at: {config_path}")
        if click.confirm("Load existing values as defaults?", default=True):
            try:
                from dotenv import dotenv_values

                existing = dict(dotenv_values(str(config_path)))
            except Exception:
                existing = {}

    def ask(prompt: str, key: str, default: Optional[str] = None, hide: bool = False) -> str:
        dv = existing.get(key) if existing else None
        if dv is None:
            dv = default
        return click.prompt(prompt, default=dv, hide_input=hide, show_default=True)

    # Ingestion mode
    click.echo("\nChoose data ingestion method:")
    use_gmail = click.confirm("Use Gmail to fetch CSV from Metabase emails?", default=True)

    cfg = {}

    if use_gmail:
        click.echo("\nGmail Configuration")
        # Optionally run OAuth flow to get refresh token
        have_refresh = click.confirm("Do you already have a Gmail refresh token?", default=False)

        if not have_refresh:
            # Ask for client id/secret and run the flow
            client_id = ask("Gmail OAuth Client ID", "GMAIL_CLIENT_ID")
            client_secret = ask("Gmail OAuth Client Secret", "GMAIL_CLIENT_SECRET", hide=True)
            gmail_user = ask("Your Gmail address", "GMAIL_USER")

            refresh_token = _run_gmail_oauth_flow(client_id, client_secret)
            cfg.update(
                {
                    "GMAIL_CLIENT_ID": client_id,
                    "GMAIL_CLIENT_SECRET": client_secret,
                    "GMAIL_REFRESH_TOKEN": refresh_token or "",
                    "GMAIL_USER": gmail_user,
                }
            )
        else:
            cfg.update(
                {
                    "GMAIL_CLIENT_ID": ask("Gmail OAuth Client ID", "GMAIL_CLIENT_ID"),
                    "GMAIL_CLIENT_SECRET": ask(
                        "Gmail OAuth Client Secret", "GMAIL_CLIENT_SECRET", hide=True
                    ),
                    "GMAIL_REFRESH_TOKEN": ask(
                        "Gmail Refresh Token", "GMAIL_REFRESH_TOKEN", hide=True
                    ),
                    "GMAIL_USER": ask("Your Gmail address", "GMAIL_USER"),
                }
            )

        # Gmail query override (optional)
        default_query = (
            existing.get("GMAIL_QUERY")
            if existing
            else Settings().gmail.query  # use code default as a hint
        )
        cfg["GMAIL_QUERY"] = click.prompt(
            "Gmail search query to find Metabase CSV",
            default=default_query,
            show_default=True,
        )

        # Optional: Additional query presets for other jobs
        cfg["WEEKLY_GMAIL_QUERY"] = ask(
            "Weekly Gmail query (optional)", "WEEKLY_GMAIL_QUERY", default=""
        )
        cfg["NEWS_GMAIL_QUERY"] = ask("News Gmail query (optional)", "NEWS_GMAIL_QUERY", default="")
    else:
        # Local CSV path
        csv_path = ask("Path to weekly CSV file", "CSV_SOURCE_PATH")
        cfg["CSV_SOURCE_PATH"] = csv_path

    # Digest recipients
    click.echo("\nEmail Recipients (optional)")
    cfg["DIGEST_TO"] = ask(
        "To", "DIGEST_TO", default=existing.get("GMAIL_USER") if existing else None
    )
    cfg["DIGEST_CC"] = ask("CC", "DIGEST_CC", default="")
    cfg["DIGEST_BCC"] = ask("BCC", "DIGEST_BCC", default="")

    # Notion (optional)
    click.echo("\nNotion Integration (optional)")
    if click.confirm("Configure Notion API integration?", default=False):
        cfg["NOTION_API_KEY"] = ask("Notion API key", "NOTION_API_KEY")
        cfg["NOTION_COMPANIES_DB_ID"] = ask("Companies DB ID", "NOTION_COMPANIES_DB_ID", default="")
        cfg["NOTION_REPORTS_DB_ID"] = ask("Reports DB ID", "NOTION_REPORTS_DB_ID", default="")
        cfg["NOTION_INTEL_DB_ID"] = ask("Intel DB ID (optional)", "NOTION_INTEL_DB_ID", default="")
        cfg["NOTION_WORKSPACE_NAME"] = ask(
            "Workspace short name for URLs (optional)",
            "NOTION_WORKSPACE_NAME",
            default="",
        )
        cfg["NOTION_COMPANIES_VIEW_ID"] = ask(
            "Companies DB view ID (optional)", "NOTION_COMPANIES_VIEW_ID", default=""
        )

    # News & profile subjects
    click.echo("\nNews/Profile Subject Phrases (optional)")
    cfg["NEWS_PROFILE_SUBJECT"] = ask(
        "Gmail subject used for profile CSV (NEWS_PROFILE_SUBJECT)",
        "NEWS_PROFILE_SUBJECT",
        default=existing.get("NEWS_PROFILE_SUBJECT")
        or Settings().intelligence.news_profile_subject,
    )

    # Intelligence defaults
    cfg.setdefault("INTELLIGENCE_REPORTS_ENABLED", "true")
    cfg.setdefault("INTELLIGENCE_DEFAULT_REPORT_DAYS", "7")
    cfg.setdefault("INTELLIGENCE_MAX_NEWS_PER_COMPANY", "10")
    cfg.setdefault("INTELLIGENCE_RISK_ASSESSMENT_ENABLED", "true")

    # Optional AI/enrichment
    click.echo("\nOptional AI/Enrichment (OpenAI, Google CSE)")
    if click.confirm("Configure OpenAI API for summaries?", default=False):
        cfg["OPENAI_API_KEY"] = ask("OpenAI API key", "OPENAI_API_KEY", default="")
    if click.confirm("Configure Google Custom Search for enrichment?", default=False):
        cfg["GOOGLE_API_KEY"] = ask("Google API key", "GOOGLE_API_KEY", default="")
        cfg["GOOGLE_CSE_ID"] = ask("Google CSE ID", "GOOGLE_CSE_ID", default="")

    # Save .env
    click.echo(f"\nSaving configuration to: {config_path}")
    _write_env_file(config_path, cfg)
    click.echo("✅ Configuration saved")

    # Offer smoke tests
    if click.confirm("Run a quick health check now?", default=True):
        try:
            _run_quick_health_check()
        except Exception as e:
            logger.warning("Health check failed", error=str(e))

    # Optional email test
    if click.confirm("Send a test email (or create HTML fallback)?", default=False):
        try:
            from app.cli_commands.test_email import send_test as test_send

            test_send(
                to=cfg.get("DIGEST_TO") or cfg.get("GMAIL_USER"),
                subject="SeeRM Setup Test Email",
                force_failure=False,
                fallback_dir="./reports/email_fallbacks",
            )
        except Exception as e:
            logger.warning("Test email failed", error=str(e))


def _run_gmail_oauth_flow(client_id: str, client_secret: str) -> Optional[str]:
    """Guide the user through Google OAuth to obtain a refresh token."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [
                    "http://localhost:8765/",
                    "http://localhost:8080/",
                    "urn:ietf:wg:oauth:2.0:oob",
                ],
            }
        }

        flow = InstalledAppFlow.from_client_config(client_config, GMAIL_SCOPES)

        # Try to open a local server first; fall back to console flow if needed
        try:
            creds = flow.run_local_server(port=8765, prompt="consent", access_type="offline")
        except Exception:
            creds = flow.run_console(prompt="consent")

        refresh_token = getattr(creds, "refresh_token", None)
        if not refresh_token:
            click.echo("⚠️  OAuth completed but no refresh token was returned.")
        else:
            click.echo("✅ Obtained Gmail refresh token")
        return refresh_token
    except Exception as e:
        click.echo(f"⚠️  OAuth flow failed: {e}")
        return None


def _write_env_file(path: Path, values: dict) -> None:
    lines = []
    for k, v in values.items():
        if v is None:
            continue
        # Preserve simple formatting; escape newlines if any
        val = str(v).replace("\n", "\\n")
        lines.append(f"{k}={val}")
    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8")


def _run_quick_health_check() -> None:
    """Minimal, inline health check (CSV, Gmail, Notion)."""
    import pandas as pd

    from app.core.config import get_settings, print_configuration_summary
    from app.data.gmail_client import EnhancedGmailClient
    from app.data.notion_client import EnhancedNotionClient

    click.echo("\nRunning quick health checks...\n")
    print_configuration_summary()

    cfg = get_settings()

    # CSV
    if cfg.csv_source_path:
        try:
            df = pd.read_csv(cfg.csv_source_path)
            df = filter_dataframe_by_relationship_manager(df, cfg.relationship_manager_name)
            click.echo(f"✓ CSV readable: {cfg.csv_source_path} ({len(df)} rows)")
        except Exception as e:
            click.echo(f"✗ CSV read failed: {e}")
    else:
        click.echo("- CSV: Using Gmail ingestion (no CSV_SOURCE_PATH set)")

    # Gmail
    if all(
        [
            cfg.gmail.client_id,
            cfg.gmail.client_secret,
            cfg.gmail.refresh_token,
            cfg.gmail.user,
        ]
    ):
        try:
            gmail = EnhancedGmailClient(cfg.gmail)
            health = gmail.health_check()
            if health.get("status") == "healthy":
                click.echo(f"✓ Gmail: Connected as {cfg.gmail.user}")
            else:
                click.echo(f"✗ Gmail: {health.get('error', 'unhealthy')}")
        except Exception as e:
            click.echo(f"✗ Gmail init failed: {e}")
    else:
        click.echo("- Gmail: Not fully configured")

    # Notion
    if cfg.notion.api_key:
        try:
            notion = EnhancedNotionClient(cfg.notion)
            health = notion.health_check()
            if health.get("status") == "healthy":
                click.echo("✓ Notion API: Connected")
            else:
                click.echo(f"✗ Notion: {health.get('error', 'unhealthy')}")
        except Exception as e:
            click.echo(f"✗ Notion init failed: {e}")
    else:
        click.echo("- Notion: Not configured")
