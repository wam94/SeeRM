"""
Enhanced email delivery with retry mechanisms and fallback options.

This module provides robust email delivery capabilities with:
- Exponential backoff retry logic for SSL/TLS failures
- Multiple delivery attempts with different configurations
- Automatic fallback to HTML file output
- Comprehensive error handling and logging
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .gmail_client import EnhancedGmailClient, GmailError

logger = structlog.get_logger(__name__)
# Standard logger for tenacity compatibility
retry_logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Email delivery error."""


class RobustEmailDelivery:
    """Robust email delivery with multiple retry strategies and fallback options."""

    def __init__(
        self,
        gmail_client: Optional[EnhancedGmailClient] = None,
        fallback_directory: Optional[str] = None,
    ):
        """Initialize robust email delivery system."""
        self.gmail_client = gmail_client
        self.fallback_directory = Path(fallback_directory or "./email_fallbacks")
        self.fallback_directory.mkdir(parents=True, exist_ok=True)

    @retry(
        retry=retry_if_exception_type((GmailError, ConnectionError, OSError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        before_sleep=before_sleep_log(retry_logger, logging.WARNING, exc_info=True),
    )
    def _attempt_email_delivery(
        self, to: str, subject: str, html: str, cc: Optional[str] = None, bcc: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Attempt email delivery with retry logic.

        Args:
            to: Recipient email address
            subject: Email subject
            html: HTML content
            cc: CC recipients (optional)
            bcc: BCC recipients (optional)

        Returns:
            Gmail API response

        Raises:
            GmailError: On persistent failures after retries
        """
        if not self.gmail_client:
            raise EmailDeliveryError("Gmail client not configured")

        logger.info(
            "Attempting email delivery",
            to=to,
            subject=subject,
            html_size=len(html),
            retry_attempt=True,
        )

        return self.gmail_client.send_html_email(to=to, subject=subject, html=html, cc=cc, bcc=bcc)

    def send_with_fallback(
        self,
        to: str,
        subject: str,
        html: str,
        cc: Optional[str] = None,
        bcc: Optional[str] = None,
        save_fallback: bool = True,
    ) -> Dict[str, Any]:
        """
        Send email with automatic fallback to file output on failure.

        Args:
            to: Recipient email address
            subject: Email subject
            html: HTML content
            cc: CC recipients (optional)
            bcc: BCC recipients (optional)
            save_fallback: Whether to save HTML file as fallback

        Returns:
            Delivery result with status and details
        """
        result = {
            "delivered": False,
            "method": None,
            "response": None,
            "fallback_file": None,
            "error": None,
            "attempts": 0,
        }

        # Attempt 1: Standard email delivery with retries
        try:
            logger.info("Starting robust email delivery", to=to, subject=subject)
            response = self._attempt_email_delivery(to, subject, html, cc, bcc)

            result.update(
                {
                    "delivered": True,
                    "method": "email",
                    "response": response,
                    "attempts": 1,  # tenacity handles internal retries
                }
            )

            logger.info("Email delivered successfully", message_id=response.get("id"), to=to)
            return result

        except Exception as e:
            # Preserve root cause if wrapped by retry logic
            root_error = e
            try:
                from tenacity import RetryError

                if isinstance(e, RetryError) and e.last_attempt and e.last_attempt.failed:
                    root_exc = e.last_attempt.exception()
                    if root_exc:
                        root_error = root_exc
            except Exception as fallback_exc:  # noqa: BLE001
                logger.debug(
                    "Failed to unwrap RetryError",
                    error=str(fallback_exc),
                )

            logger.warning(
                "Email delivery failed after retries",
                error=str(root_error),
                error_type=type(root_error).__name__,
                to=to,
            )
            result["error"] = str(root_error)
            result["attempts"] = 3  # Max retries attempted

            # Attempt 2: Fallback to HTML file
            if save_fallback:
                try:
                    fallback_file = self._save_html_fallback(to, subject, html, cc, bcc)
                    result.update(
                        {"delivered": True, "method": "file", "fallback_file": str(fallback_file)}
                    )

                    logger.info(
                        "Email saved as HTML file fallback",
                        file_path=str(fallback_file),
                        original_error=str(result["error"] or str(e)),
                    )
                    return result

                except Exception as fallback_error:
                    logger.error(
                        "Fallback file creation failed",
                        error=str(fallback_error),
                        original_error=str(e),
                    )
                    result["error"] = f"Email failed: {e}. Fallback failed: {fallback_error}"

            # Complete failure
            logger.error("All email delivery methods failed", error=result["error"])
            raise EmailDeliveryError(f"All delivery methods failed: {result['error']}")

    def _save_html_fallback(
        self, to: str, subject: str, html: str, cc: Optional[str] = None, bcc: Optional[str] = None
    ) -> Path:
        """
        Save email as HTML file for manual review.

        Args:
            to: Recipient email address
            subject: Email subject
            html: HTML content
            cc: CC recipients (optional)
            bcc: BCC recipients (optional)

        Returns:
            Path to saved HTML file
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_subject = "".join(c for c in subject if c.isalnum() or c in " -_").rstrip()
        safe_subject = safe_subject.replace(" ", "_")[:50]  # Limit filename length

        filename = f"{timestamp}_{safe_subject}.html"
        file_path = self.fallback_directory / filename

        # Create comprehensive HTML document
        full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{subject}</title>
    <style>
        .email-header {{
            background: #f5f5f5;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 5px;
            font-family: Arial, sans-serif;
        }}
        .email-content {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
        }}
        .metadata {{
            font-size: 0.9em;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="email-header">
        <h1>📧 Email Report - {subject}</h1>
        <div class="metadata">
            <p><strong>To:</strong> {to}</p>
            {f'<p><strong>CC:</strong> {cc}</p>' if cc else ''}
            {f'<p><strong>BCC:</strong> {bcc}</p>' if bcc else ''}
            <p><strong>Generated:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
            <p><strong>Status:</strong> ⚠️ Email delivery failed - Saved as fallback file</p>
        </div>
    </div>

    <div class="email-content">
        {html}
    </div>

    <hr>
    <p><small>
        This file was generated because email delivery failed.<br>
        File: {file_path}<br>
        Generated by SeeRM Intelligence Reports
    </small></p>
</body>
</html>"""

        file_path.write_text(full_html, encoding="utf-8")

        logger.info(
            "HTML fallback file created",
            file_path=str(file_path),
            file_size=len(full_html),
            subject=subject,
        )

        return file_path

    def get_fallback_files(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get list of recent fallback files.

        Args:
            limit: Maximum number of files to return

        Returns:
            List of file information dictionaries
        """
        try:
            files = []
            for file_path in sorted(
                self.fallback_directory.glob("*.html"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:limit]:
                stat = file_path.stat()
                files.append(
                    {
                        "filename": file_path.name,
                        "path": str(file_path),
                        "size": stat.st_size,
                        "created": datetime.fromtimestamp(stat.st_mtime),
                        "url": f"file://{file_path.absolute()}",
                    }
                )
            return files

        except Exception as e:
            logger.error("Failed to list fallback files", error=str(e))
            return []


# Factory function for easy integration
def create_robust_email_delivery(
    gmail_client: Optional[EnhancedGmailClient] = None, fallback_directory: Optional[str] = None
) -> RobustEmailDelivery:
    """Create configured robust email delivery instance."""
    return RobustEmailDelivery(gmail_client=gmail_client, fallback_directory=fallback_directory)
