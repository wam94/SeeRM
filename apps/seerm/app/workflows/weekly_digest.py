"""
Weekly digest workflow orchestration.

Coordinates the complete weekly digest generation process including CSV extraction,
digest rendering, and email delivery.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from app.core.config import Settings, get_settings
from app.core.exceptions import ConfigurationError
from app.core.logging import get_logger, set_correlation_id
from app.core.models import ProcessingResult, ProcessingStatus, WorkflowType
from app.data.gmail_client import create_gmail_client
from app.services.digest_service import create_digest_service

logger = get_logger(__name__)


class WeeklyDigestWorkflow:
    """Orchestrate the weekly digest generation workflow."""

    def __init__(self, settings: Optional[Settings] = None, correlation_id: Optional[str] = None):
        """Initialise the workflow with settings and optional correlation ID."""
        self.settings = settings or get_settings()
        self.correlation_id = set_correlation_id(correlation_id)

        # Initialize clients
        self._gmail_client = None
        self._digest_service = None

        logger.info(
            "Weekly digest workflow initialized",
            correlation_id=self.correlation_id,
            dry_run=self.settings.dry_run,
        )

    @property
    def gmail_client(self):
        """Lazy initialization of Gmail client."""
        if self._gmail_client is None:
            self._gmail_client = create_gmail_client(
                self.settings.gmail, dry_run=self.settings.dry_run
            )
        return self._gmail_client

    @property
    def digest_service(self):
        """Lazy initialization of digest service."""
        if self._digest_service is None:
            self._digest_service = create_digest_service(self.gmail_client, self.settings)
        return self._digest_service

    def validate_configuration(self) -> None:
        """
        Validate that all required configuration is present.

        Raises:
            ConfigurationError: If configuration is invalid
        """
        try:
            # In dry-run mode, skip strict Gmail credential validation
            if not self.settings.dry_run:
                # Check required Gmail settings
                if not self.settings.gmail.client_id:
                    raise ConfigurationError("GMAIL_CLIENT_ID is required")
                if not self.settings.gmail.client_secret:
                    raise ConfigurationError("GMAIL_CLIENT_SECRET is required")
                if not self.settings.gmail.refresh_token:
                    raise ConfigurationError("GMAIL_REFRESH_TOKEN is required")
                if not self.settings.gmail.user:
                    raise ConfigurationError("GMAIL_USER is required")
            else:
                logger.info("DRY RUN: Skipping Gmail credential validation")

            # Check Notion settings if configured
            if hasattr(self.settings, "notion") and self.settings.notion.api_key:
                if self.settings.notion.companies_db_id:
                    logger.info("Notion integration configured with companies database")
                else:
                    logger.warning(
                        "Notion API key provided but companies database not set; skipping sync"
                    )

            logger.info("Configuration validation passed")

        except Exception as e:
            error_msg = f"Configuration validation failed: {e}"
            logger.error("Configuration validation failed", error=str(e))
            raise ConfigurationError(error_msg)

    def perform_health_checks(self) -> Dict[str, Any]:
        """
        Perform health checks on all dependencies.

        Returns:
            Dict with health check results
        """
        logger.info("Performing health checks")

        try:
            # Check Gmail connectivity
            gmail_health = self.gmail_client.health_check()

            # Check digest service
            digest_health = self.digest_service.health_check()

            health_results = {
                "gmail": gmail_health,
                "digest_service": digest_health,
                "overall_status": (
                    "healthy"
                    if all(h.get("status") == "healthy" for h in [gmail_health, digest_health])
                    else "unhealthy"
                ),
            }

            logger.info(
                "Health checks completed",
                overall_status=health_results["overall_status"],
            )

            return health_results

        except Exception as e:
            logger.error("Health checks failed", error=str(e))
            return {
                "overall_status": "unhealthy",
                "error": str(e),
                "error_type": type(e).__name__,
            }

    def run(
        self,
        gmail_query: Optional[str] = None,
        max_messages: int = 5,
        skip_health_checks: bool = False,
    ) -> ProcessingResult:
        """
        Execute the complete weekly digest workflow.

        Args:
            gmail_query: Optional Gmail search query override
            max_messages: Maximum messages to search through
            skip_health_checks: Whether to skip initial health checks

        Returns:
            ProcessingResult with execution details
        """
        result = ProcessingResult(workflow_type=WorkflowType.DIGEST, started_at=datetime.now())

        try:
            logger.info(
                "Starting weekly digest workflow",
                correlation_id=self.correlation_id,
                dry_run=self.settings.dry_run,
            )

            # Step 1: Validate configuration
            self.validate_configuration()

            # Step 2: Health checks (unless skipped)
            if not skip_health_checks:
                health_results = self.perform_health_checks()
                if health_results.get("overall_status") != "healthy":
                    logger.warning("Health check warnings detected", health_results=health_results)

            # Step 3: Run digest service workflow
            digest_result = self.digest_service.run_digest_workflow(
                gmail_query=gmail_query, max_messages=max_messages
            )

            # Copy results from digest service
            result.status = digest_result.status
            result.items_processed = digest_result.items_processed
            result.items_successful = digest_result.items_successful
            result.items_failed = digest_result.items_failed
            result.data = digest_result.data
            result.error_message = digest_result.error_message
            result.error_details = digest_result.error_details

            result.completed_at = datetime.now()
            if result.started_at:
                result.duration_seconds = (result.completed_at - result.started_at).total_seconds()

            # Log final results
            if result.status == ProcessingStatus.COMPLETED:
                logger.info(
                    "Weekly digest workflow completed successfully",
                    correlation_id=self.correlation_id,
                    companies_processed=result.items_processed,
                    duration_seconds=result.duration_seconds,
                    new_accounts=(result.data.get("new_callsigns", []) if result.data else []),
                )
            else:
                logger.error(
                    "Weekly digest workflow failed",
                    correlation_id=self.correlation_id,
                    error=result.error_message,
                    duration_seconds=result.duration_seconds,
                )

            return result

        except Exception as e:
            # Handle unexpected errors
            result.status = ProcessingStatus.FAILED
            result.completed_at = datetime.now()
            result.error_message = str(e)
            result.error_details = {
                "error_type": type(e).__name__,
                "correlation_id": self.correlation_id,
            }

            if result.started_at:
                result.duration_seconds = (result.completed_at - result.started_at).total_seconds()

            logger.error(
                "Weekly digest workflow failed with unexpected error",
                correlation_id=self.correlation_id,
                error=str(e),
                error_type=type(e).__name__,
                duration_seconds=result.duration_seconds,
            )

            return result

    def dry_run(self, gmail_query: Optional[str] = None, max_messages: int = 5) -> Dict[str, Any]:
        """
        Perform a dry run of the workflow without making changes.

        Args:
            gmail_query: Optional Gmail search query override
            max_messages: Maximum messages to search through

        Returns:
            Dict with dry run results
        """
        logger.info("Starting dry run", correlation_id=self.correlation_id)

        # Temporarily enable dry run mode
        original_dry_run = self.settings.dry_run
        self.settings.dry_run = True

        try:
            result = self.run(
                gmail_query=gmail_query,
                max_messages=max_messages,
                skip_health_checks=True,
            )

            dry_run_results = {
                "status": (
                    result.status.value
                    if hasattr(result.status, "value")
                    else str(result.status) if result.status else "unknown"
                ),
                "would_process_companies": result.items_processed,
                "would_send_email_to": self.settings.digest.to or self.settings.gmail.user,
                "duration_seconds": result.duration_seconds,
                "operations_summary": [
                    "Fetch CSV data from Gmail",
                    "Parse company data",
                    "Generate digest statistics",
                    "Render HTML digest",
                    f"Send email to {self.settings.digest.to or self.settings.gmail.user}",
                    "Extract new callsigns for baseline workflow",
                ],
                "data": result.data,
            }

            logger.info("Dry run completed", results=dry_run_results)
            return dry_run_results

        finally:
            # Restore original dry run setting
            self.settings.dry_run = original_dry_run

    def get_status(self) -> Dict[str, Any]:
        """
        Get current workflow status and configuration.

        Returns:
            Status information dictionary
        """
        return {
            "workflow_type": "weekly_digest",
            "correlation_id": self.correlation_id,
            "configuration": {
                "dry_run": self.settings.dry_run,
                "gmail_user": self.settings.gmail.user,
                "digest_to": self.settings.digest.to,
                "top_movers": self.settings.digest.top_movers,
            },
            "health": self.perform_health_checks(),
        }


def run_weekly_digest_workflow(
    gmail_query: Optional[str] = None,
    max_messages: int = 5,
    dry_run: bool = False,
    correlation_id: Optional[str] = None,
) -> ProcessingResult:
    """
    Run the weekly digest workflow with optional parameters.

    Args:
        gmail_query: Optional Gmail search query override
        max_messages: Maximum messages to search through
        dry_run: Whether to run in dry-run mode
        correlation_id: Optional correlation ID for tracing

    Returns:
        ProcessingResult with execution details
    """
    # Get settings and optionally override dry_run
    settings = get_settings()
    if dry_run:
        settings.dry_run = True

    # Create and run workflow
    workflow = WeeklyDigestWorkflow(settings, correlation_id)
    return workflow.run(gmail_query, max_messages)


def dry_run_weekly_digest_workflow(
    gmail_query: Optional[str] = None,
    max_messages: int = 5,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Perform a dry run of the weekly digest workflow.

    Args:
        gmail_query: Optional Gmail search query override
        max_messages: Maximum messages to search through
        correlation_id: Optional correlation ID for tracing

    Returns:
        Dict with dry run results
    """
    workflow = WeeklyDigestWorkflow(correlation_id=correlation_id)
    return workflow.dry_run(gmail_query, max_messages)
