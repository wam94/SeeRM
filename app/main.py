"""
Main application entry point for SeeRM.

Provides CLI interface for all workflows and operations.
"""

import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from app.cli_commands.reports import reports
from app.cli_commands.test_email import test_email
from app.core.config import get_settings, print_configuration_summary, validate_required_settings
from app.core.exceptions import ConfigurationError, SeeRMError
from app.core.logging import set_correlation_id, setup_logging
from app.utils.reliability import get_circuit_breaker_status, reset_circuit_breaker
from app.workflows.weekly_digest import dry_run_weekly_digest_workflow, run_weekly_digest_workflow

console = Console()


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.option("--dry-run", is_flag=True, help="Run in dry-run mode (no actual changes)")
@click.option("--correlation-id", help="Set correlation ID for request tracing")
@click.pass_context
def main(ctx, debug: bool, dry_run: bool, correlation_id: Optional[str]):
    """Automated client intelligence and digest system.

    Generates weekly client digests, intelligence reports, and baseline dossiers
    from Metabase data and external sources.
    """
    # Ensure context object exists
    ctx.ensure_object(dict)

    # Set up logging
    setup_logging(debug=debug, rich_output=True)

    # Set correlation ID
    if correlation_id:
        set_correlation_id(correlation_id)

    # Store global options
    ctx.obj["debug"] = debug
    ctx.obj["dry_run"] = dry_run
    ctx.obj["correlation_id"] = correlation_id


# Add subcommand groups
main.add_command(reports)
main.add_command(test_email)


@main.command()
@click.option("--callsigns", help="Comma-separated callsigns to limit analysis (optional)")
@click.option(
    "--lookback-days",
    type=int,
    default=10,
    help="Number of days to look back for news (default: 10)",
)
@click.option("--no-email", is_flag=True, help="Generate report without sending email")
@click.pass_context
def news(ctx, callsigns: Optional[str], lookback_days: int, no_email: bool):
    """Generate weekly news intelligence report.

    This is equivalent to running 'reports weekly-news' but matches
    the expected interface for the news intelligence workflow.
    """
    try:
        # Use the existing weekly-news report functionality
        ctx.invoke(reports.commands["weekly-news"], no_email=no_email)
    except Exception as e:
        console.print(f"[red]News Intelligence Error:[/red] {e}")
        if ctx.obj and ctx.obj.get("debug"):
            import traceback

            console.print(traceback.format_exc())
        sys.exit(1)


@main.command()
@click.option("--gmail-query", help="Gmail search query override")
@click.option("--max-messages", default=5, help="Maximum messages to search")
@click.option("--skip-validation", is_flag=True, help="Skip configuration validation")
@click.pass_context
def digest(ctx, gmail_query: Optional[str], max_messages: int, skip_validation: bool):
    """Generate and send weekly client digest."""
    try:
        if not skip_validation:
            # Validate configuration
            missing = validate_required_settings()
            if missing:
                console.print("[red]Configuration Error:[/red]")
                for item in missing:
                    console.print(f"  • Missing: {item}")
                sys.exit(1)

        # Get settings with dry-run override
        settings = get_settings()
        if ctx.obj["dry_run"]:
            settings.dry_run = True

        console.print("[blue]Starting weekly digest workflow[/blue]")
        if settings.dry_run:
            console.print("[yellow]🔸 DRY RUN MODE - No actual changes will be made[/yellow]")

        # Run workflow
        result = run_weekly_digest_workflow(
            gmail_query=gmail_query,
            max_messages=max_messages,
            dry_run=ctx.obj["dry_run"],
            correlation_id=ctx.obj["correlation_id"],
        )

        # Display results
        _display_workflow_result("Weekly Digest", result)

        # Exit with appropriate code
        sys.exit(0 if _get_status_value(result.status) == "completed" else 1)

    except ConfigurationError as e:
        console.print(f"[red]Configuration Error:[/red] {e}")
        sys.exit(1)
    except SeeRMError as e:
        console.print(f"[red]Workflow Error:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected Error:[/red] {e}")
        if ctx.obj["debug"]:
            import traceback

            console.print(traceback.format_exc())
        sys.exit(1)


@main.command()
@click.option("--gmail-query", help="Gmail search query override")
@click.option("--max-messages", default=5, help="Maximum messages to search")
@click.pass_context
def digest_dry_run(ctx, gmail_query: Optional[str], max_messages: int):
    """Perform dry run of weekly digest workflow."""
    try:
        console.print("[blue]Starting weekly digest dry run[/blue]")

        # Run dry run
        results = dry_run_weekly_digest_workflow(
            gmail_query=gmail_query,
            max_messages=max_messages,
            correlation_id=ctx.obj["correlation_id"],
        )

        # Display results
        console.print("[green]✅ Dry Run Completed[/green]")

        # Create summary table
        table = Table(title="Dry Run Summary")
        table.add_column("Operation", style="cyan")
        table.add_column("Details", style="white")

        table.add_row("Status", results.get("status", "unknown"))
        table.add_row("Companies to Process", str(results.get("would_process_companies", 0)))
        table.add_row("Email Recipient", results.get("would_send_email_to", "unknown"))
        table.add_row("Duration", f"{results.get('duration_seconds', 0):.2f}s")

        console.print(table)

        # Show operations that would be performed
        if "operations_summary" in results:
            console.print("\n[bold]Operations that would be performed:[/bold]")
            for i, op in enumerate(results["operations_summary"], 1):
                console.print(f"  {i}. {op}")

        # Show additional data
        if "data" in results and results["data"]:
            data = results["data"]
            if data.get("new_callsigns"):
                console.print(
                    f"\n[yellow]New accounts detected:[/yellow] {', '.join(data['new_callsigns'])}"
                )

        sys.exit(0)

    except Exception as e:
        console.print(f"[red]Dry Run Error:[/red] {e}")
        if ctx.obj["debug"]:
            import traceback

            console.print(traceback.format_exc())
        sys.exit(1)


@main.command()
@click.pass_context
def config(ctx):
    """Display current configuration."""
    try:
        console.print("[blue]SeeRM Configuration[/blue]")

        # Validate configuration
        missing = validate_required_settings()
        if missing:
            console.print("[red]⚠️  Configuration Issues:[/red]")
            for item in missing:
                console.print(f"  • Missing: {item}")
            console.print()
        else:
            console.print("[green]✅ Configuration Valid[/green]")
            console.print()

        # Print configuration summary
        print_configuration_summary()

        sys.exit(0 if not missing else 1)

    except Exception as e:
        console.print(f"[red]Configuration Error:[/red] {e}")
        sys.exit(1)


@main.command()
@click.pass_context
def health(ctx):
    """Check system health and connectivity."""
    try:
        from app.workflows.weekly_digest import WeeklyDigestWorkflow

        console.print("[blue]Checking system health...[/blue]")

        # Create workflow and run health checks
        workflow = WeeklyDigestWorkflow(correlation_id=ctx.obj["correlation_id"])
        health_results = workflow.perform_health_checks()

        # Display results
        overall_status = health_results.get("overall_status", "unknown")

        if overall_status == "healthy":
            console.print("[green]✅ System is healthy[/green]")
        else:
            console.print("[red]❌ System has issues[/red]")

        # Create health table
        table = Table(title="Health Check Results")
        table.add_column("Service", style="cyan")
        table.add_column("Status", style="white")
        table.add_column("Details", style="dim")

        for service, status in health_results.items():
            if service == "overall_status":
                continue

            if isinstance(status, dict):
                service_status = status.get("status", "unknown")
                details = status.get("user", status.get("name", ""))
                if status.get("error"):
                    details = status.get("error", "")[:50] + "..."
            else:
                service_status = str(status)
                details = ""

            # Style status
            if service_status == "healthy":
                status_text = "[green]✅ Healthy[/green]"
            else:
                status_text = "[red]❌ Unhealthy[/red]"

            table.add_row(service.title(), status_text, details)

        console.print(table)

        # Show circuit breaker status
        cb_status = get_circuit_breaker_status()
        if cb_status:
            console.print("\n[bold]Circuit Breaker Status:[/bold]")
            cb_table = Table()
            cb_table.add_column("Name", style="cyan")
            cb_table.add_column("State", style="white")
            cb_table.add_column("Failures", style="yellow")

            for name, status in cb_status.items():
                state = status.get("state", "unknown")
                failures = str(status.get("failure_count", 0))

                if state == "closed":
                    state_text = "[green]Closed[/green]"
                elif state == "open":
                    state_text = "[red]Open[/red]"
                else:
                    state_text = "[yellow]Half-Open[/yellow]"

                cb_table.add_row(name, state_text, failures)

            console.print(cb_table)

        sys.exit(0 if overall_status == "healthy" else 1)

    except Exception as e:
        console.print(f"[red]Health Check Error:[/red] {e}")
        if ctx.obj["debug"]:
            import traceback

            console.print(traceback.format_exc())
        sys.exit(1)


@main.command()
@click.argument("breaker_name")
@click.pass_context
def reset_breaker(ctx, breaker_name: str):
    """Reset a circuit breaker by name."""
    try:
        success = reset_circuit_breaker(breaker_name)

        if success:
            console.print(f"[green]✅ Circuit breaker '{breaker_name}' reset successfully[/green]")
        else:
            console.print(f"[red]❌ Circuit breaker '{breaker_name}' not found[/red]")

        sys.exit(0 if success else 1)

    except Exception as e:
        console.print(f"[red]Reset Error:[/red] {e}")
        sys.exit(1)


@main.command()
@click.argument("csv_path")
@click.option("--top-movers", default=15, help="Number of top movers to show")
@click.option("--output", help="Output file path (optional)")
@click.pass_context
def test_csv(ctx, csv_path: str, top_movers: int, output: Optional[str]):
    """Test CSV parsing with provided file."""
    try:
        from app.core.models import DigestData
        from app.data.csv_parser import parse_csv_file
        from app.services.render_service import create_digest_renderer

        console.print(f"[blue]Testing CSV parsing: {csv_path}[/blue]")

        # Parse CSV
        companies, digest_dict = parse_csv_file(csv_path, strict_validation=False)

        console.print("[green]✅ CSV parsed successfully[/green]")
        console.print(f"Companies found: {len(companies)}")
        console.print(
            f"Changed accounts: {digest_dict.get('stats', {}).get('changed_accounts', 0)}"
        )
        console.print(f"New accounts: {digest_dict.get('stats', {}).get('new_accounts', 0)}")

        # Create digest data
        digest_data = DigestData(**digest_dict)

        # Render HTML
        renderer = create_digest_renderer()
        html = renderer.render_digest(digest_data)

        console.print(f"HTML rendered: {len(html)} characters")

        # Save to file if requested
        if output:
            with open(output, "w") as f:
                f.write(html)
            console.print(f"[green]HTML saved to: {output}[/green]")

        # Show sample of top movers
        if digest_data.top_pct_gainers:
            console.print(f"\n[bold]Top {len(digest_data.top_pct_gainers)} Gainers:[/bold]")
            for gainer in digest_data.top_pct_gainers[:5]:
                console.print(f"  • {gainer.callsign}: +{gainer.percentage_change:.2f}%")

        if digest_data.top_pct_losers:
            console.print(f"\n[bold]Top {len(digest_data.top_pct_losers)} Losers:[/bold]")
            for loser in digest_data.top_pct_losers[:5]:
                console.print(f"  • {loser.callsign}: {loser.percentage_change:.2f}%")

        sys.exit(0)

    except Exception as e:
        console.print(f"[red]CSV Test Error:[/red] {e}")
        if ctx.obj["debug"]:
            import traceback

            console.print(traceback.format_exc())
        sys.exit(1)


def _get_status_value(status) -> str:
    """Safely extract status value from enum or string."""
    if hasattr(status, "value"):
        return status.value
    return str(status) if status else "unknown"


def _display_workflow_result(workflow_name: str, result) -> None:
    """Display workflow execution results."""
    status_value = _get_status_value(result.status)

    if status_value == "completed":
        console.print(f"[green]✅ {workflow_name} completed successfully[/green]")
    else:
        console.print(f"[red]❌ {workflow_name} failed[/red]")

    # Create results table
    table = Table(title=f"{workflow_name} Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Status", status_value)
    table.add_row(
        "Duration", f"{result.duration_seconds:.2f}s" if result.duration_seconds else "N/A"
    )
    table.add_row("Items Processed", str(result.items_processed))
    table.add_row("Successful", str(result.items_successful))
    table.add_row("Failed", str(result.items_failed))

    if result.error_message:
        table.add_row(
            "Error",
            (
                result.error_message[:100] + "..."
                if len(result.error_message) > 100
                else result.error_message
            ),
        )

    console.print(table)

    # Show additional data
    if result.data:
        data = result.data
        if data.get("new_callsigns"):
            console.print(
                f"\n[yellow]New accounts detected:[/yellow] {', '.join(data['new_callsigns'])}"
            )
        if data.get("email_message_id"):
            console.print(f"[blue]Email sent:[/blue] {data['email_message_id']}")


if __name__ == "__main__":
    main()
