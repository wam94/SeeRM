"""
"Doctor" command: consolidated health, config, and diagnostics.

Runs a series of checks and prints a concise, friendly report:
 - Config summary and required keys
 - Gmail health (if configured)
 - Notion health + database IDs
 - CSV availability (file or via Gmail)
 - Email fallback files (recent)
"""

from __future__ import annotations

from pathlib import Path

import click

from app.core.config import (
    get_settings,
    print_configuration_summary,
    validate_intelligence_reports_config,
)
from app.data.email_delivery import create_robust_email_delivery
from app.data.gmail_client import EnhancedGmailClient
from app.data.notion_client import EnhancedNotionClient


@click.command()
@click.option(
    "--fallback-dir",
    default="./reports/email_fallbacks",
    help="Fallback directory to scan",
)
def doctor(fallback_dir: str):
    """Run SeeRM diagnostics and print a summary report."""
    click.echo("SeeRM Doctor")
    click.echo("=" * 40)

    # Config summary
    print_configuration_summary()

    cfg = get_settings()

    # Intelligence reports configuration
    status = validate_intelligence_reports_config()
    overall = status.get("overall", "unknown")
    click.echo("\nIntelligence Reports Status:")
    click.echo(f"  Overall: {overall}")
    for k in ("csv_source", "notion_reports_db", "openai_summaries", "google_search"):
        v = status.get(k)
        if v is not None:
            click.echo(f"  {k}: {v}")

    # CSV availability (file path only)
    if cfg.csv_source_path:
        p = Path(cfg.csv_source_path)
        if p.exists():
            click.echo(f"\n✓ CSV file available: {p} ({p.stat().st_size} bytes)")
        else:
            click.echo(f"\n✗ CSV path not found: {p}")
    else:
        click.echo("\n- CSV file path not set (using Gmail ingestion)")

    # Gmail health
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
            h = gmail.health_check()
            if h.get("status") == "healthy":
                click.echo(f"✓ Gmail healthy ({cfg.gmail.user})")
            else:
                click.echo(f"✗ Gmail unhealthy: {h.get('error','unknown')}")
        except Exception as e:
            click.echo(f"✗ Gmail init failed: {e}")
    else:
        click.echo("- Gmail not fully configured")

    # Notion health
    if cfg.notion.api_key:
        try:
            notion = EnhancedNotionClient(cfg.notion)
            nh = notion.health_check()
            if nh.get("status") == "healthy":
                click.echo("✓ Notion API healthy")
            else:
                click.echo(f"✗ Notion unhealthy: {nh.get('error','unknown')}")
        except Exception as e:
            click.echo(f"✗ Notion init failed: {e}")
        companies_status = "set" if cfg.notion.companies_db_id else "not set"
        reports_status = "set" if cfg.notion.reports_db_id else "not set"
        click.echo(f"  Companies DB: {companies_status} | Reports DB: {reports_status}")
    else:
        click.echo("- Notion not configured")

    # Email fallback files
    click.echo("\nEmail Fallback Files:")
    delivery = create_robust_email_delivery(None, fallback_directory=fallback_dir)
    files = delivery.get_fallback_files(limit=5)
    if not files:
        click.echo("  (none found)")
    else:
        for i, f in enumerate(files, 1):
            name = Path(f["path"]).name
            size = f["size"]
            created = f["created"].strftime("%Y-%m-%d %H:%M:%S")
            click.echo(f"  {i}. {name} | {size} bytes | {created}")

    click.echo("\nDone.")
