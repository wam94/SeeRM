"""
CLI commands for intelligence report generation.

Provides command-line interface for generating company deep dives,
new client summaries, and weekly news digests.
"""

from typing import Optional

import click
import structlog

from app.core.config import Settings, validate_intelligence_reports_config
from app.data.csv_parser import CSVProcessor
from app.data.gmail_client import EnhancedGmailClient
from app.data.notion_client import EnhancedNotionClient
from app.intelligence.data_aggregator import IntelligenceAggregator
from app.reports.company_deepdive import CompanyDeepDiveReport
from app.reports.new_clients import NewClientReport
from app.reports.weekly_news import WeeklyNewsReport

logger = structlog.get_logger(__name__)


def _create_services(settings: Settings):
    """Create shared services for report generation."""
    gmail_client = None
    notion_client = None

    # Initialize optional services
    if settings.gmail.user and settings.gmail.credentials_path:
        try:
            gmail_client = EnhancedGmailClient(settings)
        except Exception as e:
            logger.warning("Gmail client initialization failed", error=str(e))

    if settings.notion.api_key:
        try:
            notion_client = EnhancedNotionClient(settings)
        except Exception as e:
            logger.warning("Notion client initialization failed", error=str(e))

    # Create aggregator
    aggregator = IntelligenceAggregator(
        gmail_client=gmail_client, notion_client=notion_client, settings=settings
    )

    return aggregator, notion_client


@click.group()
def reports():
    """Intelligence reports generation commands."""
    pass


@reports.command()
@click.argument("callsign", required=True)
@click.option("--no-email", is_flag=True, help="Skip email delivery")
@click.option("--config-file", type=click.Path(exists=True), help="Configuration file path")
def company_deepdive(callsign: str, no_email: bool, config_file: Optional[str]):
    """
    Generate comprehensive company deep dive report.

    CALLSIGN: Company callsign to analyze
    """
    try:
        settings = Settings(_env_file=config_file if config_file else None)
        aggregator, notion_client = _create_services(settings)

        # Generate report
        report_generator = CompanyDeepDiveReport(
            aggregator=aggregator, notion_client=notion_client, settings=settings
        )

        click.echo(f"Generating deep dive report for {callsign}...")

        report = report_generator.generate(callsign=callsign.upper(), include_email=not no_email)

        click.echo(f"✓ Report generated: {report.title}")
        click.echo(f"  Report ID: {report.metadata.report_id}")
        click.echo(f"  Duration: {report.metadata.duration_seconds:.1f}s")

        if report.email_sent:
            click.echo("  ✓ Email sent")

        if report.notion_page_id:
            click.echo(f"  ✓ Notion page: {report.notion_page_id}")

    except Exception as e:
        logger.error("Company deep dive failed", callsign=callsign, error=str(e))
        click.echo(f"Error: {e}", err=True)
        raise click.Abort()


@reports.command()
@click.option(
    "--callsigns", help="Comma-separated list of new callsigns (auto-detects if not provided)"
)
@click.option("--no-email", is_flag=True, help="Skip email delivery")
@click.option("--config-file", type=click.Path(exists=True), help="Configuration file path")
def new_clients(callsigns: Optional[str], no_email: bool, config_file: Optional[str]):
    """
    Generate weekly new client summary report.

    Auto-detects new clients from recent movements unless --callsigns specified.
    """
    try:
        settings = Settings(_env_file=config_file if config_file else None)
        aggregator, notion_client = _create_services(settings)

        # Parse callsigns if provided
        new_callsigns = None
        if callsigns:
            new_callsigns = [cs.strip().upper() for cs in callsigns.split(",")]
            click.echo(f"Generating report for specified clients: {', '.join(new_callsigns)}")
        else:
            click.echo("Generating report for auto-detected new clients...")

        # Generate report
        report_generator = NewClientReport(
            aggregator=aggregator, notion_client=notion_client, settings=settings
        )

        report = report_generator.generate(new_callsigns=new_callsigns, include_email=not no_email)

        if report:
            click.echo(f"✓ Report generated: {report.title}")
            click.echo(f"  Report ID: {report.metadata.report_id}")
            click.echo(f"  Duration: {report.metadata.duration_seconds:.1f}s")
            click.echo(f"  Clients: {report.metadata.parameters['client_count']}")

            if report.email_sent:
                click.echo("  ✓ Email sent")

            if report.notion_page_id:
                click.echo(f"  ✓ Notion page: {report.notion_page_id}")
        else:
            click.echo("No new clients found for this period")

    except Exception as e:
        logger.error("New client report failed", error=str(e))
        click.echo(f"Error: {e}", err=True)
        raise click.Abort()


@reports.command()
@click.option("--days", default=7, help="Number of days to look back for news")
@click.option("--no-email", is_flag=True, help="Skip email delivery")
@click.option("--config-file", type=click.Path(exists=True), help="Configuration file path")
def weekly_news(days: int, no_email: bool, config_file: Optional[str]):
    """
    Generate weekly news digest report.

    Creates bulletized news summary organized by category.
    """
    try:
        settings = Settings(_env_file=config_file if config_file else None)
        aggregator, notion_client = _create_services(settings)

        click.echo(f"Generating weekly news digest ({days} days)...")

        # Generate report
        report_generator = WeeklyNewsReport(
            aggregator=aggregator, notion_client=notion_client, settings=settings
        )

        report = report_generator.generate(days=days, include_email=not no_email)

        if report:
            click.echo(f"✓ Report generated: {report.title}")
            click.echo(f"  Report ID: {report.metadata.report_id}")
            click.echo(f"  Duration: {report.metadata.duration_seconds:.1f}s")

            if report.email_sent:
                click.echo("  ✓ Email sent")

            if report.notion_page_id:
                click.echo(f"  ✓ Notion page: {report.notion_page_id}")
        else:
            click.echo("No news items found for this period")

    except Exception as e:
        logger.error("Weekly news report failed", error=str(e))
        click.echo(f"Error: {e}", err=True)
        raise click.Abort()


@reports.command()
@click.option("--config-file", type=click.Path(exists=True), help="Configuration file path")
def health_check(config_file: Optional[str]):
    """
    Check health of intelligence reporting services.
    """
    try:
        # Use minimal validation to allow missing Gmail/Notion for intelligence reports
        import os

        if config_file:
            os.environ["PYDANTIC_SETTINGS_FILE"] = config_file
        settings = Settings(_env_file=config_file if config_file else None)

        click.echo("Intelligence Reports Health Check")
        click.echo("=" * 40)

        # Use the intelligence reports validation
        reports_status = validate_intelligence_reports_config()

        if reports_status.get("intelligence_reports") == "disabled":
            click.echo("✗ Intelligence Reports: Disabled")
            click.echo("\nTo enable: Set INTELLIGENCE_REPORTS_ENABLED=true")
            return

        # Check each service status
        csv_status = reports_status.get("csv_source", "unknown")
        if csv_status == "configured":
            try:
                csv_processor = CSVProcessor()
                # Test CSV parsing by reading the file
                import pandas as pd

                df = pd.read_csv(settings.csv_source_path)
                companies = csv_processor.parse_companies_csv(df)
                click.echo(f"✓ CSV Access: {len(companies)} company records available")
            except Exception as e:
                click.echo(f"✗ CSV Access: {e}")
        else:
            click.echo(f"✗ CSV Access: {csv_status}")

        # Check Gmail
        try:
            if settings.gmail.user and settings.gmail.credentials_path:
                # Test Gmail connection
                _ = EnhancedGmailClient(settings)
                click.echo(f"✓ Gmail: Connected as {settings.gmail.user}")
            else:
                click.echo("- Gmail: Not configured (optional for reports)")
        except Exception as e:
            click.echo(f"✗ Gmail: {e}")

        # Check Notion
        try:
            if settings.notion.api_key:
                # Test Notion connection
                _ = EnhancedNotionClient(settings)
                click.echo("✓ Notion API: Connected")

                # Check specific databases
                if settings.notion.companies_db_id:
                    click.echo(f"  ✓ Companies DB: {settings.notion.companies_db_id}")
                else:
                    click.echo("  - Companies DB: Not configured")

                reports_db_status = reports_status.get("notion_reports_db", "unknown")
                if reports_db_status == "configured":
                    click.echo(f"  ✓ Reports DB: {settings.notion.reports_db_id}")
                else:
                    click.echo("  - Reports DB: Not configured (optional)")
            else:
                click.echo("- Notion: Not configured (optional for reports)")
        except Exception as e:
            click.echo(f"✗ Notion: {e}")

        # Check optional enhancements
        openai_status = reports_status.get("openai_summaries", "unavailable")
        if openai_status == "available":
            click.echo("✓ OpenAI: Available for enhanced summaries")
        else:
            click.echo("- OpenAI: Not configured (optional)")

        google_status = reports_status.get("google_search", "unavailable")
        if google_status == "available":
            click.echo("✓ Google Search: Available for data enrichment")
        else:
            click.echo("- Google CSE: Not configured (optional)")

        # Overall status
        overall_status = reports_status.get("overall", "unknown")
        click.echo("\nOverall Status:")
        if overall_status == "ready":
            click.echo("✓ Intelligence Reports: Ready for full functionality")
        elif overall_status == "missing_requirements":
            click.echo("⚠ Intelligence Reports: Missing required configuration")
            click.echo("  Required: CSV source path")
        elif overall_status == "configuration_error":
            error = reports_status.get("error", "Unknown error")
            click.echo(f"✗ Intelligence Reports: Configuration error - {error}")
        else:
            click.echo(f"? Intelligence Reports: Status unknown - {overall_status}")

    except Exception as e:
        logger.error("Health check failed", error=str(e))
        click.echo(f"Error: {e}", err=True)
        raise click.Abort()


if __name__ == "__main__":
    reports()
