"""CLI commands for testing email delivery and fallback systems."""

from datetime import datetime
from pathlib import Path

import click
import structlog

from app.core.config import Settings
from app.data.email_delivery import create_robust_email_delivery
from app.data.gmail_client import EnhancedGmailClient, GmailError

logger = structlog.get_logger(__name__)


@click.group()
def test_email():
    """Test email delivery and fallback systems."""
    pass


@test_email.command()
@click.option("--to", default=None, help="Recipient email (defaults to configured user)")
@click.option("--subject", default="SeeRM Email Delivery Test", help="Email subject")
@click.option("--force-failure", is_flag=True, help="Force email failure to test fallback")
@click.option(
    "--fallback-dir",
    default="./test_email_fallbacks",
    help="Directory for fallback files",
)
def send_test(to: str, subject: str, force_failure: bool, fallback_dir: str):
    """
    Send a test email to verify delivery and fallback systems.

    This command tests:
    - Gmail API authentication and connection
    - Email delivery with retry logic
    - HTML fallback file creation on failure
    - Error handling and logging
    """
    try:
        settings = Settings()

        # Use configured user if no recipient specified
        if not to:
            to = settings.gmail.user

        click.echo("🔍 Testing Email Delivery System")
        click.echo(f"Recipient: {to}")
        click.echo(f"Subject: {subject}")
        click.echo(f"Force failure: {force_failure}")
        click.echo(f"Fallback directory: {fallback_dir}")
        click.echo("-" * 50)

        # Create Gmail client
        gmail_client = None
        if all(
            [
                settings.gmail.client_id,
                settings.gmail.client_secret,
                settings.gmail.refresh_token,
                settings.gmail.user,
            ]
        ):
            try:
                gmail_client = EnhancedGmailClient(settings.gmail, dry_run=False)
                click.echo("✅ Gmail client created successfully")
            except Exception as e:
                click.echo(f"❌ Gmail client creation failed: {e}")
                if not force_failure:
                    return
        else:
            click.echo("⚠️  Gmail credentials not configured")
            if not force_failure:
                click.echo("Use --force-failure to test fallback without Gmail")
                return

        # Create robust email delivery system
        email_delivery = create_robust_email_delivery(
            gmail_client=gmail_client, fallback_directory=fallback_dir
        )

        # Generate test email content
        html_content = _generate_test_email_content(force_failure)

        # Force failure for testing if requested
        if force_failure and gmail_client:
            # Temporarily raise an error to exercise the fallback path
            def _forced_failure(*_args, **_kwargs):
                raise GmailError(
                    "Forced test failure: EOF occurred in violation of protocol (_ssl.c:2437)"
                )

            gmail_client.send_html_email = _forced_failure

        click.echo("\n📧 Attempting email delivery...")

        # Attempt delivery
        result = email_delivery.send_with_fallback(to=to, subject=subject, html=html_content)

        # Report results
        click.echo("\n📊 Delivery Results:")
        click.echo(f"Delivered: {'✅' if result['delivered'] else '❌'}")
        click.echo(f"Method: {result['method']}")
        click.echo(f"Attempts: {result['attempts']}")

        if result["method"] == "email":
            click.echo(f"Message ID: {result['response'].get('id', 'Unknown')}")
            click.echo("✅ Email sent successfully!")

        elif result["method"] == "file":
            click.echo(f"Fallback file: {result['fallback_file']}")
            click.echo(f"Original error: {result.get('error', 'Unknown')}")
            click.echo("⚠️  Email failed - HTML file created as fallback")

            # Open file for review
            if click.confirm("Would you like to open the HTML file?"):
                import webbrowser

                webbrowser.open(f"file://{Path(result['fallback_file']).absolute()}")

        else:
            click.echo("❌ All delivery methods failed")
            if result.get("error"):
                click.echo(f"Error: {result['error']}")

    except Exception as e:
        logger.error("Test email command failed", error=str(e))
        click.echo(f"❌ Test failed: {e}")


@test_email.command()
@click.option("--fallback-dir", default="./reports/email_fallbacks", help="Directory to check")
@click.option("--limit", default=10, help="Maximum files to show")
def list_fallbacks(fallback_dir: str, limit: int):
    """List recent HTML fallback files."""
    try:
        fallback_path = Path(fallback_dir)

        if not fallback_path.exists():
            click.echo(f"❌ Fallback directory does not exist: {fallback_dir}")
            return

        # Find HTML files
        html_files = list(fallback_path.glob("*.html"))
        html_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        if not html_files:
            click.echo(f"📁 No HTML fallback files found in {fallback_dir}")
            return

        click.echo(
            f"📋 Recent HTML Fallback Files ({min(len(html_files), limit)} of {len(html_files)}):"
        )
        click.echo("-" * 70)

        for i, file_path in enumerate(html_files[:limit], 1):
            stat = file_path.stat()
            size = stat.st_size / 1024  # KB
            modified = datetime.fromtimestamp(stat.st_mtime)

            click.echo(f"{i:2d}. {file_path.name}")
            click.echo(
                f"     Size: {size:.1f} KB | Modified: {modified.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            click.echo(f"     Path: {file_path}")
            click.echo()

    except Exception as e:
        click.echo(f"❌ Failed to list fallback files: {e}")


@test_email.command()
@click.argument("file_path", type=click.Path(exists=True))
def open_fallback(file_path: str):
    """Open an HTML fallback file in the browser."""
    try:
        import webbrowser

        file_path = Path(file_path).absolute()
        webbrowser.open(f"file://{file_path}")
        click.echo(f"🌐 Opened {file_path.name} in browser")
    except Exception as e:
        click.echo(f"❌ Failed to open file: {e}")


def _generate_test_email_content(is_forced_failure: bool = False) -> str:
    """Generate test email HTML content."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    failure_note = ""

    if is_forced_failure:
        failure_note = """
        <div
            style="background:#fff3cd;border:1px solid #ffeaa7;padding:15px;"
        >
            <h3 style="color:#856404;margin-top:0;">⚠️ Forced Failure Test</h3>
            <p style="color:#856404;">
                This email was intentionally set to fail to test the fallback system.
            </p>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8" />
        <title>SeeRM Email Delivery Test</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
            }}
            .header {{
                background: #f8f9fa;
                padding: 20px;
                border-radius: 5px;
                margin-bottom: 20px;
            }}
            .footer {{
                background: #e9ecef;
                padding: 15px;
                border-radius: 5px;
                margin-top: 20px;
                font-size: 0.9em;
            }}
            .success {{ color: #28a745; }}
            .info {{ color: #17a2b8; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1 class="success">✅ SeeRM Email Delivery Test</h1>
            <p class="info">Generated: {timestamp} UTC</p>
        </div>

        {failure_note}

        <div class="content">
            <h2>📧 Email System Test</h2>
            <p>
                This is a test email to verify the SeeRM email delivery system is working
                correctly.
            </p>

            <h3>🔧 System Components Tested:</h3>
            <ul>
                <li>✅ Gmail API Authentication</li>
                <li>✅ SSL/TLS Connection</li>
                <li>✅ HTML Email Formatting</li>
                <li>✅ Error Handling & Logging</li>
                <li>✅ Retry Logic with Exponential Backoff</li>
                <li>✅ HTML File Fallback System</li>
            </ul>

            <h3>📊 Sample Intelligence Data:</h3>
            <p>
                This email contains sample formatted content similar to the weekly
                intelligence reports:
            </p>

            <h4>💰 FUNDING & INVESTMENT (Sample)</h4>
            <p>• Company A, Company B, Company C</p>

            <h4>🚀 PRODUCT LAUNCHES (Sample)</h4>
            <p>• Company D, Company E</p>

            <h4>🤝 PARTNERSHIPS & ALLIANCES (Sample)</h4>
            <p>• Company F, Company G, Company H</p>
        </div>

        <div class="footer">
            <p><strong>✅ Delivery system check succeeded.</strong></p>
            <p>Generated by SeeRM Intelligence Reports • Test Command</p>
        </div>
    </body>
    </html>
    """


# Add to main CLI
def add_test_email_commands(main_cli):
    """Add test email commands to main CLI."""
    main_cli.add_command(test_email)


if __name__ == "__main__":
    test_email()
