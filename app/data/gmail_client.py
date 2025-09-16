"""
Enhanced Gmail client with reliability patterns and structured error handling.

Provides robust Gmail API integration with circuit breakers, retry logic, and comprehensive logging.
"""

import base64
import io
import re
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import structlog
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import GmailConfig
from app.core.exceptions import GmailError, ValidationError
from app.utils.reliability import (
    AdaptiveRateLimiter,
    default_rate_limiter,
    with_circuit_breaker,
    with_retry,
)

logger = structlog.get_logger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"  # nosec B105
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


class EnhancedGmailClient:
    """Enhanced Gmail client with reliability patterns and structured error handling."""

    def __init__(
        self,
        config: GmailConfig,
        rate_limiter: Optional[AdaptiveRateLimiter] = None,
        dry_run: bool = False,
    ):
        """Initialise the client with configuration and optional rate limiter."""
        self.config = config
        self.rate_limiter = rate_limiter or default_rate_limiter
        self.dry_run = dry_run
        self._service = None

        logger.info("Gmail client initialized", user=config.user, dry_run=dry_run)

    @property
    def service(self):
        """Lazy initialization of Gmail service."""
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def _build_service(self):
        """Build Gmail API service with credentials."""
        try:
            creds = Credentials(
                None,
                refresh_token=self.config.refresh_token,
                token_uri=TOKEN_URI,
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
                scopes=SCOPES,
            )

            service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            logger.info("Gmail service initialized successfully")
            return service

        except Exception as e:
            logger.error("Failed to initialize Gmail service", error=str(e))
            raise GmailError(f"Failed to initialize Gmail service: {e}")

    @with_circuit_breaker(
        name="gmail_search",
        failure_threshold=3,
        recovery_timeout=30.0,
        expected_exception=GmailError,
    )
    @with_retry(max_attempts=3, retry_exceptions=(HttpError, GmailError))
    def search_messages(
        self, query: Optional[str] = None, max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search for messages matching the query.

        Args:
            query: Gmail search query (uses config default if None)
            max_results: Maximum number of messages to return

        Returns:
            List of message metadata dictionaries

        Raises:
            GmailError: On API errors
            ValidationError: On invalid parameters
        """
        if max_results <= 0:
            raise ValidationError("max_results must be positive")

        search_query = query or self.config.query

        if self.dry_run:
            logger.info(
                "DRY RUN: Would search Gmail messages",
                query=search_query,
                max_results=max_results,
                user=self.config.user,
            )
            # Return mock message data for dry run
            return [
                {"id": "dry_run_message_1", "threadId": "dry_run_thread_1"},
                {"id": "dry_run_message_2", "threadId": "dry_run_thread_2"},
            ]

        # Rate limiting
        self.rate_limiter.acquire(timeout=10.0)

        try:
            logger.info(
                "Searching Gmail messages",
                query=search_query,
                max_results=max_results,
                user=self.config.user,
            )

            response = (
                self.service.users()
                .messages()
                .list(userId=self.config.user, q=search_query, maxResults=max_results)
                .execute()
            )

            messages = response.get("messages", [])

            logger.info(
                "Gmail search completed",
                messages_found=len(messages),
                query=search_query,
            )

            self.rate_limiter.on_success()
            return messages

        except HttpError as e:
            error_msg = f"Gmail API error during search: {e}"
            logger.error(
                "Gmail search failed",
                error=str(e),
                status_code=e.resp.status,
                query=search_query,
            )
            self.rate_limiter.on_error()
            raise GmailError(error_msg, details={"status_code": e.resp.status})

        except Exception as e:
            error_msg = f"Unexpected error during Gmail search: {e}"
            logger.error("Gmail search failed", error=str(e))
            self.rate_limiter.on_error()
            raise GmailError(error_msg)

    @with_circuit_breaker(
        name="gmail_get_message",
        failure_threshold=3,
        recovery_timeout=30.0,
        expected_exception=GmailError,
    )
    @with_retry(max_attempts=3, retry_exceptions=(HttpError, GmailError))
    def get_message(self, message_id: str) -> Dict[str, Any]:
        """
        Get full message details by ID.

        Args:
            message_id: Gmail message ID

        Returns:
            Full message data dictionary

        Raises:
            GmailError: On API errors
            ValidationError: On invalid message ID
        """
        if not message_id:
            raise ValidationError("message_id cannot be empty")

        if self.dry_run:
            logger.debug("DRY RUN: Would fetch Gmail message", message_id=message_id)
            # Return mock message data with CSV attachment
            return {
                "id": message_id,
                "threadId": f"thread_{message_id}",
                "payload": {
                    "parts": [
                        {
                            "filename": "mock_data.csv",
                            "mimeType": "text/csv",
                            "body": {
                                "attachmentId": f"attachment_{message_id}",
                                "size": 1000,
                            },
                        }
                    ]
                },
            }

        # Rate limiting
        self.rate_limiter.acquire(timeout=10.0)

        try:
            logger.debug("Fetching Gmail message", message_id=message_id)

            message = (
                self.service.users()
                .messages()
                .get(userId=self.config.user, id=message_id, format="full")
                .execute()
            )

            logger.debug("Gmail message fetched successfully", message_id=message_id)

            self.rate_limiter.on_success()
            return message

        except HttpError as e:
            error_msg = f"Gmail API error fetching message {message_id}: {e}"
            logger.error(
                "Failed to fetch Gmail message",
                message_id=message_id,
                error=str(e),
                status_code=e.resp.status,
            )
            self.rate_limiter.on_error()
            raise GmailError(
                error_msg,
                details={"message_id": message_id, "status_code": e.resp.status},
            )

        except Exception as e:
            error_msg = f"Unexpected error fetching Gmail message {message_id}: {e}"
            logger.error("Failed to fetch Gmail message", message_id=message_id, error=str(e))
            self.rate_limiter.on_error()
            raise GmailError(error_msg)

    def extract_csv_attachments(
        self, message: Dict[str, Any], attachment_regex: Optional[str] = None
    ) -> List[Tuple[str, bytes]]:
        """
        Extract CSV attachments from a Gmail message.

        Args:
            message: Gmail message data
            attachment_regex: Regex pattern for attachment filenames

        Returns:
            List of (filename, data) tuples

        Raises:
            GmailError: On API errors
            ValidationError: On invalid message data
        """
        if not message:
            raise ValidationError("message cannot be empty")

        pattern_str = attachment_regex or self.config.attachment_regex
        pattern = re.compile(pattern_str)

        attachments = []
        payload = message.get("payload", {})
        parts = payload.get("parts", [])

        logger.debug(
            "Extracting CSV attachments",
            message_id=message.get("id"),
            parts_count=len(parts),
            pattern=pattern_str,
        )

        if self.dry_run:
            # Return mock CSV data for dry run
            import io

            import pandas as pd

            logger.debug("DRY RUN: Would extract CSV attachments")

            # Create mock CSV data to match test expectations (3 companies, including a new one)
            mock_data = pd.DataFrame(
                {
                    "CALLSIGN": ["test1", "test2", "newco"],
                    "DBA": ["Test Company 1", "Test Company 2", "New Company"],
                    "DOMAIN_ROOT": ["test1.com", "test2.com", "newco.com"],
                    "BENEFICIAL_OWNERS": [
                        '["John Doe"]',
                        '["Jane Smith"]',
                        '["Bob Wilson"]',
                    ],
                    "CURR_BALANCE": [100000, 80000, 50000],
                    "PREV_BALANCE": [90000, 100000, 0],
                    "BALANCE_PCT_DELTA_PCT": [11.11, -20.0, 0.0],
                    "IS_NEW_ACCOUNT": [False, False, True],
                    "ANY_CHANGE": [True, True, True],
                }
            )

            csv_buffer = io.StringIO()
            mock_data.to_csv(csv_buffer, index=False)
            csv_bytes = csv_buffer.getvalue().encode("utf-8")

            return [("mock_data.csv", csv_bytes)]

        for part in parts:
            filename = part.get("filename", "")

            if not filename or not pattern.match(filename):
                continue

            body = part.get("body", {})
            attach_id = body.get("attachmentId")

            try:
                if not attach_id:
                    # Inline data
                    data = body.get("data")
                    if data:
                        decoded_data = base64.urlsafe_b64decode(data)
                        attachments.append((filename, decoded_data))
                else:
                    # External attachment - rate limit this API call too
                    self.rate_limiter.acquire(timeout=10.0)

                    attachment = (
                        self.service.users()
                        .messages()
                        .attachments()
                        .get(
                            userId=self.config.user,
                            messageId=message["id"],
                            id=attach_id,
                        )
                        .execute()
                    )

                    decoded_data = base64.urlsafe_b64decode(attachment["data"])
                    attachments.append((filename, decoded_data))

                    self.rate_limiter.on_success()

            except HttpError as e:
                logger.error(
                    "Failed to download attachment",
                    filename=filename,
                    attach_id=attach_id,
                    error=str(e),
                )
                self.rate_limiter.on_error()
                # Continue processing other attachments
                continue
            except Exception as e:
                logger.error(
                    "Unexpected error downloading attachment",
                    filename=filename,
                    error=str(e),
                )
                continue

        logger.info(
            "CSV attachment extraction completed",
            attachments_found=len(attachments),
            filenames=[att[0] for att in attachments],
        )

        return attachments

    def parse_csv_attachment(self, attachment_data: bytes) -> pd.DataFrame:
        """
        Parse CSV attachment data into DataFrame.

        Args:
            attachment_data: Raw CSV data bytes

        Returns:
            Parsed DataFrame

        Raises:
            ValidationError: On CSV parsing errors
        """
        try:
            df = pd.read_csv(io.BytesIO(attachment_data))

            logger.info("CSV parsed successfully", rows=len(df), columns=list(df.columns))

            return df

        except Exception as e:
            error_msg = f"Failed to parse CSV data: {e}"
            logger.error("CSV parsing failed", error=str(e))
            raise ValidationError(error_msg)

    @with_circuit_breaker(
        name="gmail_send",
        failure_threshold=3,
        recovery_timeout=60.0,
        expected_exception=GmailError,
    )
    @with_retry(
        max_attempts=2,
        retry_exceptions=(HttpError,),  # Be more conservative with sending emails
    )
    def send_html_email(
        self,
        to: str,
        subject: str,
        html: str,
        cc: Optional[str] = None,
        bcc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send HTML email via Gmail API.

        Args:
            to: Recipient email address
            subject: Email subject
            html: HTML content
            cc: CC recipients (optional)
            bcc: BCC recipients (optional)

        Returns:
            Gmail API response

        Raises:
            GmailError: On API errors
            ValidationError: On invalid parameters
        """
        if not to or not subject or not html:
            raise ValidationError("to, subject, and html are required")

        if self.dry_run:
            logger.info(
                "DRY RUN: Would send email",
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                html_length=len(html),
            )
            return {"id": "dry_run_message", "threadId": "dry_run_thread"}

        # Rate limiting
        self.rate_limiter.acquire(timeout=10.0)

        try:
            # Create email message
            msg = EmailMessage()
            msg["To"] = to
            if cc:
                msg["Cc"] = cc
            if bcc:
                msg["Bcc"] = bcc
            msg["From"] = self.config.user
            msg["Subject"] = subject

            # Set content
            msg.set_content("HTML email. View in an HTML-capable client.")
            msg.add_alternative(html, subtype="html")

            # Encode for Gmail API
            raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode()

            logger.info(
                "Sending HTML email",
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                from_user=self.config.user,
            )

            # Send via Gmail API
            response = (
                self.service.users()
                .messages()
                .send(userId=self.config.user, body={"raw": raw_message})
                .execute()
            )

            logger.info(
                "Email sent successfully",
                message_id=response.get("id"),
                thread_id=response.get("threadId"),
                to=to,
            )

            self.rate_limiter.on_success()
            return response

        except HttpError as e:
            error_msg = f"Gmail API error sending email: {e}"
            logger.error(
                "Failed to send email",
                to=to,
                subject=subject,
                error=str(e),
                status_code=e.resp.status,
            )
            self.rate_limiter.on_error()
            raise GmailError(error_msg, details={"status_code": e.resp.status})

        except Exception as e:
            error_msg = f"Unexpected error sending email: {e}"
            logger.error("Failed to send email", to=to, subject=subject, error=str(e))
            self.rate_limiter.on_error()
            raise GmailError(error_msg)

    def health_check(self) -> Dict[str, Any]:
        """
        Perform health check on Gmail API.

        Returns:
            Health status information
        """
        try:
            # Try to get user profile as a simple health check
            profile = self.service.users().getProfile(userId=self.config.user).execute()

            return {
                "status": "healthy",
                "user": self.config.user,
                "messages_total": profile.get("messagesTotal", 0),
                "threads_total": profile.get("threadsTotal", 0),
            }

        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "error_type": type(e).__name__,
            }

    def get_latest_csv_from_query(
        self, query: Optional[str] = None, max_messages: int = 5
    ) -> Optional[pd.DataFrame]:
        """
        Get the latest CSV attachment from Gmail query.

        Args:
            query: Gmail search query (uses config default if None)
            max_messages: Maximum messages to search through

        Returns:
            DataFrame from latest CSV, or None if not found

        Raises:
            GmailError: On API errors
            ValidationError: On parsing errors
        """
        try:
            messages = self.search_messages(query, max_messages)

            if not messages:
                logger.warning("No messages found for CSV extraction")
                return None

            # Process messages in order until we find a CSV
            for msg_info in messages:
                try:
                    message = self.get_message(msg_info["id"])
                    attachments = self.extract_csv_attachments(message)

                    if attachments:
                        filename, data = attachments[0]  # Use first CSV found
                        df = self.parse_csv_attachment(data)

                        logger.info(
                            "Successfully extracted CSV",
                            filename=filename,
                            rows=len(df),
                            columns=len(df.columns),
                        )

                        return df

                except Exception as e:
                    logger.warning(
                        "Failed to process message for CSV",
                        message_id=msg_info["id"],
                        error=str(e),
                    )
                    continue

            logger.warning("No CSV attachments found in messages")
            return None

        except Exception as e:
            logger.error("Failed to get latest CSV from Gmail", error=str(e))
            raise


def create_gmail_client(
    config: GmailConfig,
    rate_limiter: Optional[AdaptiveRateLimiter] = None,
    dry_run: bool = False,
) -> EnhancedGmailClient:
    """Create an `EnhancedGmailClient` configured from environment settings."""
    return EnhancedGmailClient(config, rate_limiter, dry_run)
