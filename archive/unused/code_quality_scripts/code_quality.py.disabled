"""
CLI commands for the code quality automation system.

Provides command-line interface for running quality checks, fixes,
and monitoring services.
"""

import asyncio
from typing import Optional

import click
import structlog

from app.code_quality import BatchCleanupAgent, CodeQualityMonitor, PreCommitEnhancementAgent
from app.code_quality.github_integration import GitHubActionsIntegration

logger = structlog.get_logger(__name__)


@click.group()
def code_quality():
    """Code quality automation commands."""
    pass


@code_quality.command()
@click.option("--files", "-", multiple=True, help="Specific files to fix")
@click.option("--dry-run", is_flag=True, help="Show what would be fixed without making changes")
def auto_fix(files, dry_run):
    """Run automatic fixes for common code quality issues."""
    agent = PreCommitEnhancementAgent()

    if dry_run:
        click.echo("🔍 Running in dry-run mode - no changes will be made")

    file_list = list(files) if files else None
    results = agent.run_auto_fixes(file_list)

    click.echo("🤖 Auto-fix Results:")
    click.echo("=" * 50)

    total_fixes = 0
    for fix_type, fixed_files in results.items():
        if fixed_files:
            count = len(fixed_files)
            total_fixes += count
            click.echo(f"  {fix_type}: {count} files")

            if dry_run:
                for file_path in fixed_files[:3]:  # Show first 3
                    click.echo(f"    - {file_path}")
                if len(fixed_files) > 3:
                    click.echo(f"    ... and {len(fixed_files) - 3} more")

    if total_fixes == 0:
        click.echo("  ✅ No issues found")
    elif dry_run:
        click.echo(f"\nTo apply these {total_fixes} fixes, run without --dry-run")
    else:
        click.echo(f"\n✅ Applied {total_fixes} fixes successfully")

        # Run formatters after fixes
        click.echo("\n🎨 Running formatters...")
        formatter_results = agent.run_formatters()
        for tool, success in formatter_results.items():
            status = "✅" if success else "❌"
            click.echo(f"  {tool}: {status}")


@code_quality.command()
@click.option("--dry-run", is_flag=True, help="Analyze without making changes")
@click.option("--skip-backup", is_flag=True, help="Skip safety backup (faster but less safe)")
def fix_all(dry_run, skip_backup):
    """Run comprehensive code quality cleanup on entire codebase."""
    agent = BatchCleanupAgent()

    click.echo("🚀 Starting comprehensive code quality cleanup...")

    if dry_run:
        click.echo("🔍 DRY RUN MODE - No changes will be made")
    elif not skip_backup:
        click.echo("💾 Creating safety backup...")

    results = agent.run_full_cleanup(dry_run=dry_run)

    # Display results
    report = agent.create_cleanup_report(results)
    click.echo(report)

    if results.get("error"):
        click.echo(f"❌ Cleanup failed: {results['error']}", err=True)
        return False

    if not dry_run:
        validation = results.get("validation_results", {})
        if not validation.get("syntax_valid"):
            click.echo("⚠️  Warning: Syntax validation failed", err=True)
        if validation.get("tests_pass") is False:
            click.echo("⚠️  Warning: Tests may be failing", err=True)

    return True


@code_quality.command()
@click.option("--auto-fix/--no-auto-fix", default=True, help="Enable automatic fixes")
@click.option("--daemon", is_flag=True, help="Run as daemon process")
def monitor(auto_fix, daemon):
    """Start real-time code quality monitoring."""
    config_status = "enabled" if auto_fix else "disabled"
    click.echo(f"🔍 Starting code quality monitor (auto-fix {config_status})")

    async def run_monitor():
        monitor = CodeQualityMonitor(auto_fix=auto_fix)

        try:
            await monitor.start_monitoring()

            if daemon:
                click.echo("📡 Monitor running as daemon - press Ctrl+C to stop")
                # Keep running until interrupted
                while True:
                    await asyncio.sleep(1)
                    stats = monitor.get_statistics()
                    if stats["files_processed"] % 10 == 0 and stats["files_processed"] > 0:
                        click.echo(
                            f"📊 Processed {stats['files_processed']} files, "
                            f"applied {stats['fixes_applied']} fixes"
                        )
            else:
                click.echo("📡 Monitor started - make some changes to Python files to test")
                click.echo("Press Ctrl+C to stop")
                await asyncio.sleep(30)  # Run for 30 seconds in demo mode

        except KeyboardInterrupt:
            click.echo("\n🛑 Stopping monitor...")
        finally:
            monitor.stop_monitoring()
            stats = monitor.get_statistics()
            click.echo(
                f"📊 Final stats: {stats['files_processed']} files processed, "
                f"{stats['fixes_applied']} fixes applied"
            )

    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        click.echo("\n👋 Monitor stopped")


@code_quality.command()
@click.option("--path", "-p", help="Specific path to scan (default: app/)")
def scan(path):
    """Perform manual scan for code quality issues."""
    monitor = CodeQualityMonitor()

    click.echo("🔍 Scanning for code quality issues...")

    results = monitor.manual_scan(path)

    click.echo("📊 Scan Results:")
    click.echo("=" * 50)
    click.echo(f"Files scanned: {results['files_scanned']}")
    click.echo(f"Issues found: {len(results['issues_found'])}")
    click.echo(f"Files needing fixes: {len(results['fixes_available'])}")

    if results["issues_found"]:
        click.echo("\n🔍 Issues detected:")
        for issue in results["issues_found"][:10]:  # Show first 10
            click.echo(f"  - {issue}")

        if len(results["issues_found"]) > 10:
            click.echo(f"  ... and {len(results['issues_found']) - 10} more")

        click.echo("\n💡 To fix these issues, run:")
        click.echo("   python -m app.main code-quality auto-fix")
    else:
        click.echo("\n✅ No issues found!")


@code_quality.command()
@click.option("--create-workflow", is_flag=True, help="Create new quality workflow")
@click.option("--enhance-existing", is_flag=True, help="Enhance existing workflows")
def setup_github(create_workflow, enhance_existing):
    """Set up GitHub Actions integration for code quality."""
    integration = GitHubActionsIntegration()

    if create_workflow:
        click.echo("📝 Creating code quality workflow...")
        success = integration.create_quality_workflow()
        if success:
            click.echo("✅ Created .github/workflows/code-quality.yml")
        else:
            click.echo("❌ Failed to create workflow", err=True)

    if enhance_existing:
        click.echo("🔧 Enhancing existing workflows...")
        results = integration.enhance_existing_workflows()

        for workflow, enhanced in results.items():
            status = "✅" if enhanced else "⚠️ "
            action = "Enhanced" if enhanced else "Skipped"
            click.echo(f"  {status} {workflow}: {action}")

    if not create_workflow and not enhance_existing:
        click.echo("🔧 Setting up GitHub Actions integration...")

        # Create workflow
        workflow_success = integration.create_quality_workflow()

        # Enhance existing
        enhance_results = integration.enhance_existing_workflows()

        # Set up SKIP configuration
        skip_config = integration.setup_skip_configuration()

        # Validate
        validation = integration.validate_workflow_enhancement()

        click.echo("\n📊 Setup Results:")
        click.echo("=" * 40)
        click.echo(f"Quality workflow: {'✅' if workflow_success else '❌'}")

        enhanced_count = sum(1 for v in enhance_results.values() if v)
        click.echo(f"Enhanced workflows: {enhanced_count}/{len(enhance_results)}")

        valid_count = sum(1 for v in validation.values() if v)
        click.echo(f"Validation checks: {valid_count}/{len(validation)}")

        click.echo("\n💡 SKIP configuration created: .code-quality-skip.sh")
        click.echo("   Use: source .code-quality-skip.sh")


@code_quality.command()
def status():
    """Show code quality system status."""
    click.echo("📊 Code Quality System Status")
    click.echo("=" * 40)

    # Check if agents are available
    try:
        agent = PreCommitEnhancementAgent()
        click.echo("✅ Pre-commit Enhancement Agent: Available")
    except Exception as e:
        click.echo(f"❌ Pre-commit Enhancement Agent: {e}")

    try:
        batch_agent = BatchCleanupAgent()
        click.echo("✅ Batch Cleanup Agent: Available")
    except Exception as e:
        click.echo(f"❌ Batch Cleanup Agent: {e}")

    try:
        monitor = CodeQualityMonitor()
        click.echo("✅ Quality Monitor: Available")
    except Exception as e:
        click.echo(f"❌ Quality Monitor: {e}")

    # Quick scan
    try:
        monitor = CodeQualityMonitor()
        scan_results = monitor.manual_scan()
        click.echo(f"📁 Python files in app/: {scan_results['files_scanned']}")
        click.echo(f"🔍 Issues detected: {len(scan_results['issues_found'])}")

        if scan_results["issues_found"]:
            click.echo("💡 Run 'python -m app.main code-quality auto-fix' to resolve issues")
        else:
            click.echo("✨ Code quality looks good!")

    except Exception as e:
        click.echo(f"⚠️  Quick scan failed: {e}")


if __name__ == "__main__":
    code_quality()
