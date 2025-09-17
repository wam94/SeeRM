"""Digest service for generating and sending weekly client digests."""

from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog

from app.core.config import DigestConfig, Settings
from app.core.exceptions import GmailError, WorkflowError
from app.core.models import Company, DigestData, ProcessingResult, ProcessingStatus, WorkflowType
from app.data.csv_parser import CSVProcessor
from app.data.gmail_client import EnhancedGmailClient
from app.data.notion_client import EnhancedNotionClient
from app.services.render_service import DigestRenderer

logger = structlog.get_logger(__name__)


class DigestService:
    """Generate and send weekly client digests."""

    def __init__(
        self,
        gmail_client: EnhancedGmailClient,
        renderer: DigestRenderer,
        config: DigestConfig,
        notion_client: Optional[EnhancedNotionClient] = None,
        companies_db_id: Optional[str] = None,
    ):
        """Initialise the digest service with clients and configuration."""
        self.gmail_client = gmail_client
        self.renderer = renderer
        self.config = config
        self.notion_client = notion_client
        self._companies_db_id = companies_db_id
        self.csv_processor = CSVProcessor(strict_validation=False)

    def fetch_latest_csv_data(
        self, query: Optional[str] = None, max_messages: int = 5
    ) -> List[Company]:
        """
        Fetch and parse the latest CSV data from Gmail.

        Args:
            query: Gmail search query (uses config default if None)
            max_messages: Maximum messages to search

        Returns:
            List of Company objects

        Raises:
            WorkflowError: On processing errors
        """
        try:
            logger.info("Fetching latest CSV data from Gmail")

            # Get latest CSV from Gmail
            df = self.gmail_client.get_latest_csv_from_query(query, max_messages)

            if df is None:
                raise WorkflowError("No CSV attachments found in Gmail messages")

            # Parse into Company objects
            companies = self.csv_processor.parse_companies_csv(df)

            logger.info(
                "CSV data processed successfully",
                companies_count=len(companies),
                columns=list(df.columns),
            )

            return companies

        except Exception as e:
            if isinstance(e, WorkflowError):
                raise

            error_msg = f"Failed to fetch CSV data: {e}"
            logger.error("CSV data fetch failed", error=str(e))
            raise WorkflowError(error_msg)

    def generate_digest_data(
        self,
        companies: List[Company],
        top_n: Optional[int] = None,
        new_callsigns: Optional[List[str]] = None,
    ) -> DigestData:
        """
        Generate digest data from company list.

        Args:
            companies: List of Company objects
            top_n: Number of top movers (uses config default if None)

        Returns:
            DigestData object

        Raises:
            WorkflowError: On processing errors
        """
        try:
            top_movers = top_n or self.config.top_movers

            logger.info(
                "Generating digest data",
                companies_count=len(companies),
                top_movers=top_movers,
            )

            # Calculate digest statistics and movements
            digest_dict = self.csv_processor.calculate_digest_data(companies, top_movers)

            if new_callsigns is not None:
                digest_dict.setdefault("stats", {})
                digest_dict["stats"]["new_accounts"] = len(new_callsigns)

            # Create DigestData object
            digest_data = DigestData(
                subject=self.config.subject or f"Client Weekly Digest — {datetime.now().date()}",
                **digest_dict,
            )

            logger.info(
                "Digest data generated",
                total_accounts=digest_data.stats.total_accounts,
                changed_accounts=digest_data.stats.changed_accounts,
                top_gainers=len(digest_data.top_pct_gainers),
                top_losers=len(digest_data.top_pct_losers),
            )

            return digest_data

        except Exception as e:
            error_msg = f"Failed to generate digest data: {e}"
            logger.error("Digest generation failed", error=str(e))
            raise WorkflowError(error_msg)

    def _fetch_notion_company_data(
        self, companies: List[Company]
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """Fetch Notion company metadata for the supplied companies."""
        if not self.notion_client or not self._companies_db_id:
            logger.debug("Notion client/DB not configured; skipping Notion lookup for new accounts")
            return None

        callsigns = [c.callsign for c in companies if getattr(c, "callsign", None)]
        if not callsigns:
            return {}

        try:
            notion_data = self.notion_client.get_all_companies_domain_data(
                self._companies_db_id, callsigns
            )
            logger.debug(
                "Fetched Notion company metadata",
                requested=len(callsigns),
                received=len(notion_data or {}),
            )
            return notion_data
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to fetch Notion company data for new-account detection",
                error=str(exc),
                companies=len(callsigns),
            )
            return None

    def extract_new_account_callsigns(
        self,
        companies: List[Company],
        notion_company_data: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[str]:
        """
        Extract callsigns of new accounts for downstream processing.

        Args:
            companies: List of Company objects
            notion_company_data: Optional cached Notion metadata

        Returns:
            List of new account callsigns
        """
        if notion_company_data is None:
            notion_company_data = self._fetch_notion_company_data(companies)

        if notion_company_data is None:
            logger.debug("Notion metadata unavailable; skipping Notion-based new account detection")
            return []

        new_callsigns: List[str] = []
        for company in companies:
            callsign = getattr(company, "callsign", "") or ""
            if not callsign:
                continue

            entry = notion_company_data.get(callsign.lower()) if notion_company_data else None
            page_id = entry.get("page_id") if entry else None

            if not page_id:
                new_callsigns.append(callsign)

        if new_callsigns:
            logger.info(
                "New accounts detected via Notion",
                count=len(new_callsigns),
                callsigns=new_callsigns[:5],
            )
        else:
            logger.debug("No new accounts detected via Notion lookup", company_count=len(companies))

        return new_callsigns

    def sync_new_companies_to_notion(
        self,
        companies: List[Company],
        companies_db_id: str,
        new_callsigns: Optional[List[str]] = None,
    ) -> None:
        """
        Sync new companies to Notion database.

        Args:
            companies: List of all companies
            companies_db_id: Notion database ID for companies
        """
        if not self.notion_client or not companies_db_id:
            logger.debug("Notion sync skipped - no client or DB ID configured")
            return

        targets = {cs.lower() for cs in (new_callsigns or []) if cs}
        if not targets:
            logger.debug("No new callsigns supplied for Notion sync; skipping")
            return

        try:
            new_companies = [c for c in companies if c.callsign and c.callsign.lower() in targets]

            if not new_companies:
                logger.debug(
                    "No new companies matched the provided callsigns; skipping Notion sync"
                )
                return

            logger.info(
                "Syncing new companies to Notion",
                count=len(new_companies),
                companies=[c.callsign for c in new_companies[:5]],
            )

            synced_count = 0
            for company in new_companies:
                try:
                    company.needs_dossier = True

                    notion_page = self.notion_client.upsert_company_page(companies_db_id, company)
                    page_id = getattr(notion_page, "page_id", None)

                    if page_id:
                        try:
                            self.notion_client.set_needs_dossier(page_id, True)
                        except Exception as flag_error:  # noqa: BLE001
                            logger.warning(
                                "Failed to set Needs Dossier flag",
                                callsign=company.callsign,
                                page_id=page_id,
                                error=str(flag_error),
                            )

                    logger.debug(
                        "Company synced to Notion",
                        callsign=company.callsign,
                        page_id=page_id,
                    )
                    synced_count += 1

                except Exception as e:
                    logger.warning(
                        "Failed to sync company to Notion",
                        callsign=company.callsign,
                        error=str(e),
                    )
                    # Continue with other companies
                    continue

            logger.info(
                "Notion sync completed",
                synced_count=synced_count,
                total_new=len(new_companies),
            )

        except Exception as e:
            logger.error("Notion sync failed", error=str(e), error_type=type(e).__name__)
            # Don't raise - this is not critical for the main workflow

    def write_new_callsigns_trigger(
        self, callsigns: List[str], trigger_file: Optional[str] = None
    ) -> None:
        """
        Write new callsigns to trigger file for downstream workflows.

        Args:
            callsigns: List of callsigns to write
            trigger_file: Path to trigger file
        """
        if not callsigns:
            logger.debug("No new callsigns to write")
            return

        from pathlib import Path

        default_path = Path("/tmp/new_callsigns.txt")  # nosec B108
        target = Path(trigger_file) if trigger_file else default_path

        try:
            with open(str(target), "w") as handle:
                handle.write(",".join(callsigns))

            logger.info("New callsigns written to trigger file", path=str(target))
        except Exception as e:
            logger.error("Trigger file write failed", error=str(e), path=str(target))
            # Don't raise exception - this is not critical

    def send_digest_email(self, digest_data: DigestData, html_content: str) -> Dict[str, Any]:
        """
        Send digest email via Gmail.

        Args:
            digest_data: Digest data for email metadata
            html_content: Rendered HTML content

        Returns:
            Gmail API response

        Raises:
            WorkflowError: On sending errors
        """
        try:
            # Determine recipients
            to = self.config.to or self.gmail_client.config.user
            cc = self.config.cc
            bcc = self.config.bcc

            logger.info(
                "Sending digest email",
                to=to,
                cc=cc,
                bcc=bcc,
                subject=digest_data.subject,
            )

            # Send email
            response = self.gmail_client.send_html_email(
                to=to, subject=digest_data.subject, html=html_content, cc=cc, bcc=bcc
            )

            logger.info("Digest email sent successfully", message_id=response.get("id"), to=to)

            return response

        except GmailError as e:
            # Re-raise Gmail errors as workflow errors
            raise WorkflowError(f"Failed to send digest email: {e}")
        except Exception as e:
            error_msg = f"Unexpected error sending digest email: {e}"
            logger.error("Digest email send failed", error=str(e))
            raise WorkflowError(error_msg)

    def run_digest_workflow(
        self, gmail_query: Optional[str] = None, max_messages: int = 5
    ) -> ProcessingResult:
        """
        Run the complete digest workflow.

        Args:
            gmail_query: Gmail search query (optional)
            max_messages: Max messages to search

        Returns:
            ProcessingResult with workflow outcome
        """
        result = ProcessingResult(workflow_type=WorkflowType.DIGEST, started_at=datetime.now())

        try:
            logger.info("Starting digest workflow")

            # Step 1: Fetch CSV data from Gmail
            companies = self.fetch_latest_csv_data(gmail_query, max_messages)
            result.items_processed = len(companies)

            notion_company_data = self._fetch_notion_company_data(companies)

            # Step 2: Extract new callsigns for downstream processing using Notion data
            new_callsigns = self.extract_new_account_callsigns(
                companies, notion_company_data=notion_company_data
            )

            # Step 3: Generate digest data (override new account count when available)
            digest_data = self.generate_digest_data(
                companies,
                new_callsigns=new_callsigns if new_callsigns else None,
            )

            # Step 4: Render HTML
            html_content = self.renderer.render_digest(digest_data)

            if new_callsigns:
                self.write_new_callsigns_trigger(new_callsigns)

            # Step 4a: Sync new companies to Notion (if configured)
            if self.notion_client:
                # Access companies_db_id through the settings passed to the factory
                companies_db_id = getattr(self, "_companies_db_id", None)
                if companies_db_id:
                    self.sync_new_companies_to_notion(
                        companies, companies_db_id, new_callsigns=new_callsigns
                    )

            # Step 5: Send digest email
            email_response = self.send_digest_email(digest_data, html_content)

            # Update result
            result.status = ProcessingStatus.COMPLETED
            result.completed_at = datetime.now()
            result.items_successful = len(companies)
            result.data = {
                "digest_stats": digest_data.stats.model_dump(),
                "new_callsigns": new_callsigns,
                "email_message_id": email_response.get("id"),
                "html_length": len(html_content),
            }

            if result.started_at:
                duration = (result.completed_at - result.started_at).total_seconds()
                result.duration_seconds = duration

            logger.info(
                "Digest workflow completed successfully",
                companies_processed=result.items_processed,
                new_accounts=len(new_callsigns),
                duration_seconds=result.duration_seconds,
            )

            return result

        except Exception as e:
            # Update result with error
            result.status = ProcessingStatus.FAILED
            result.completed_at = datetime.now()
            result.error_message = str(e)
            result.error_details = {"error_type": type(e).__name__}

            if result.started_at:
                duration = (result.completed_at - result.started_at).total_seconds()
                result.duration_seconds = duration

            logger.error(
                "Digest workflow failed",
                error=str(e),
                error_type=type(e).__name__,
                duration_seconds=result.duration_seconds,
            )

            return result

    def health_check(self) -> Dict[str, Any]:
        """
        Perform health check for digest service.

        Returns:
            Health status information
        """
        try:
            # Check Gmail connectivity
            gmail_health = self.gmail_client.health_check()

            # Check Notion client if available
            notion_health = {"status": "not_configured"}
            if self.notion_client:
                notion_health = self.notion_client.health_check()

            services_healthy = gmail_health.get("status") == "healthy" and (
                notion_health.get("status") in ["healthy", "not_configured"]
            )

            return {
                "status": "healthy" if services_healthy else "unhealthy",
                "gmail": gmail_health,
                "notion": notion_health,
                "config": {
                    "gmail_user": self.gmail_client.config.user,
                    "digest_to": self.config.to,
                    "top_movers": self.config.top_movers,
                    "notion_companies_db_id": self._companies_db_id or "not_configured",
                },
            }

        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "error_type": type(e).__name__,
            }


def create_digest_service(gmail_client: EnhancedGmailClient, settings: Settings) -> DigestService:
    """Create a digest service configured from application settings."""
    from app.data.notion_client import create_notion_client
    from app.services.render_service import create_digest_renderer

    renderer = create_digest_renderer()

    # Create Notion client if configured
    notion_client = None
    companies_db_id = None
    if hasattr(settings, "notion") and settings.notion.api_key:
        try:
            notion_client = create_notion_client(settings.notion, dry_run=settings.dry_run)
            companies_db_id = settings.notion.companies_db_id
            logger.info("Notion client initialized for digest service")
        except Exception as e:
            logger.warning("Failed to initialize Notion client", error=str(e))

    return DigestService(gmail_client, renderer, settings.digest, notion_client, companies_db_id)
