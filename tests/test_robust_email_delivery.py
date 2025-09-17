"""Test robust email delivery with SSL error handling and fallbacks."""

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.data.email_delivery import EmailDeliveryError, RobustEmailDelivery
from app.data.gmail_client import GmailError


class TestRobustEmailDelivery:
    """Exercise the robust email delivery system."""

    @pytest.fixture
    def temp_fallback_dir(self):
        """Create temporary directory for fallback files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield temp_dir

    @pytest.fixture
    def mock_gmail_client(self):
        """Mock Gmail client."""
        client = Mock()
        client.send_html_email.return_value = {"id": "test_msg_123", "threadId": "thread_456"}
        return client

    @pytest.fixture
    def email_delivery(self, mock_gmail_client, temp_fallback_dir):
        """Create email delivery instance with mocked client."""
        return RobustEmailDelivery(
            gmail_client=mock_gmail_client, fallback_directory=temp_fallback_dir
        )

    def test_successful_email_delivery(self, email_delivery, mock_gmail_client):
        """Test successful email delivery on first attempt."""
        result = email_delivery.send_with_fallback(
            to="test@example.com", subject="Test Subject", html="<p>Test content</p>"
        )

        assert result["delivered"] is True
        assert result["method"] == "email"
        assert result["response"]["id"] == "test_msg_123"
        assert result["fallback_file"] is None
        assert result["error"] is None

        mock_gmail_client.send_html_email.assert_called_once()

    def test_ssl_error_with_successful_fallback(
        self, email_delivery, mock_gmail_client, temp_fallback_dir
    ):
        """Test SSL error handling with successful HTML file fallback."""
        # Simulate SSL error
        ssl_error = GmailError(
            "Unexpected error sending email: EOF occurred in violation of protocol" " (_ssl.c:2437)"
        )
        mock_gmail_client.send_html_email.side_effect = ssl_error

        result = email_delivery.send_with_fallback(
            to="test@example.com",
            subject="Test SSL Failure",
            html="<p>Test content for SSL failure</p>",
        )

        assert result["delivered"] is True
        assert result["method"] == "file"
        assert result["fallback_file"] is not None
        assert result["error"] == str(ssl_error)
        assert result["attempts"] == 3  # Max retries attempted

        # Verify fallback file was created
        fallback_path = Path(result["fallback_file"])
        assert fallback_path.exists()
        assert fallback_path.suffix == ".html"

        # Verify file content
        content = fallback_path.read_text(encoding="utf-8")
        assert "Test SSL Failure" in content
        assert "Test content for SSL failure" in content
        assert "Email delivery failed" in content

    def test_connection_error_with_retry(self, email_delivery, mock_gmail_client):
        """Test connection error with retry mechanism."""
        # First two attempts fail, third succeeds
        mock_gmail_client.send_html_email.side_effect = [
            ConnectionError("Connection timeout"),
            ConnectionError("Network unreachable"),
            {"id": "success_123", "threadId": "thread_789"},
        ]

        result = email_delivery.send_with_fallback(
            to="test@example.com", subject="Test Retry", html="<p>Test retry content</p>"
        )

        assert result["delivered"] is True
        assert result["method"] == "email"
        assert result["response"]["id"] == "success_123"
        assert mock_gmail_client.send_html_email.call_count == 3

    def test_complete_failure_raises_error(
        self, email_delivery, mock_gmail_client, temp_fallback_dir
    ):
        """Test complete failure when both email and fallback fail."""
        # Make email fail
        mock_gmail_client.send_html_email.side_effect = GmailError("Persistent error")

        # Make fallback directory unwritable to cause fallback failure
        os.chmod(temp_fallback_dir, 0o444)  # Read-only

        try:
            with pytest.raises(EmailDeliveryError) as exc_info:
                email_delivery.send_with_fallback(
                    to="test@example.com",
                    subject="Test Complete Failure",
                    html="<p>Test content</p>",
                )

            assert "All delivery methods failed" in str(exc_info.value)
        finally:
            # Restore permissions for cleanup
            os.chmod(temp_fallback_dir, 0o755)

    def test_fallback_file_content_structure(
        self, email_delivery, mock_gmail_client, temp_fallback_dir
    ):
        """Test HTML fallback file has correct structure and metadata."""
        mock_gmail_client.send_html_email.side_effect = GmailError("Test error")

        result = email_delivery.send_with_fallback(
            to="user@test.com",
            subject="Structured Test Subject",
            html="<h1>Test Report</h1><p>Report content here</p>",
            cc="cc@test.com",
            bcc="bcc@test.com",
        )

        fallback_path = Path(result["fallback_file"])
        content = fallback_path.read_text(encoding="utf-8")

        # Check HTML structure
        assert "<!DOCTYPE html>" in content
        assert "<html>" in content
        assert "<head>" in content
        assert "<title>Structured Test Subject</title>" in content

        # Check metadata
        assert "To:</strong> user@test.com" in content
        assert "CC:</strong> cc@test.com" in content
        assert "BCC:</strong> bcc@test.com" in content
        assert "Email delivery failed" in content

        # Check original content is preserved
        assert "<h1>Test Report</h1>" in content
        assert "<p>Report content here</p>" in content

        # Check CSS styling is included
        assert ".email-header" in content
        assert ".email-content" in content

    def test_no_gmail_client_raises_error(self, temp_fallback_dir):
        """Test error when no Gmail client is configured."""
        email_delivery = RobustEmailDelivery(
            gmail_client=None, fallback_directory=temp_fallback_dir
        )

        with pytest.raises(EmailDeliveryError) as exc_info:
            email_delivery.send_with_fallback(
                to="test@example.com",
                subject="Test No Client",
                html="<p>Test</p>",
                save_fallback=False,  # Disable fallback
            )

        assert "Gmail client not configured" in str(exc_info.value)

    def test_get_fallback_files(self, email_delivery, mock_gmail_client, temp_fallback_dir):
        """Test listing of fallback files."""
        # Create some fallback files
        mock_gmail_client.send_html_email.side_effect = GmailError("Test error")

        # Generate multiple files
        for i in range(3):
            email_delivery.send_with_fallback(
                to="test@example.com", subject=f"Test Report {i}", html=f"<p>Content {i}</p>"
            )

        files = email_delivery.get_fallback_files(limit=5)

        assert len(files) == 3
        assert all("filename" in f for f in files)
        assert all("path" in f for f in files)
        assert all("size" in f for f in files)
        assert all("created" in f for f in files)
        assert all("url" in f for f in files)
        assert all(f["url"].startswith("file://") for f in files)

    def test_filename_sanitization(self, email_delivery, mock_gmail_client, temp_fallback_dir):
        """Test that filenames are properly sanitized."""
        mock_gmail_client.send_html_email.side_effect = GmailError("Test error")

        result = email_delivery.send_with_fallback(
            to="test@example.com",
            subject=(
                'Test/with\\special:chars<>|*?" and very long subject '
                "that should be truncated at some point"
            ),
            html="<p>Test content</p>",
        )

        fallback_path = Path(result["fallback_file"])

        # Filename should not contain special characters
        assert "/" not in fallback_path.name
        assert "\\" not in fallback_path.name
        assert "<" not in fallback_path.name
        assert ">" not in fallback_path.name

        # Should be truncated
        assert len(fallback_path.stem) < 100  # Reasonable length


class TestEmailDeliveryIntegration:
    """Integration tests for email delivery with weekly reports."""

    @pytest.fixture
    def mock_settings(self):
        """Mock application settings."""
        settings = Mock()
        settings.gmail.user = "test@example.com"
        return settings

    @pytest.fixture
    def mock_aggregator(self):
        """Mock intelligence aggregator."""
        aggregator = Mock()
        return aggregator

    @pytest.fixture
    def mock_digest(self):
        """Mock weekly news digest."""
        digest = Mock()
        digest.total_items = 140
        digest.by_type = {}
        digest.by_company = {}
        return digest

    @pytest.fixture
    def mock_report(self):
        """Mock report object."""
        report = Mock()
        report.email_sent = False
        report.metadata = Mock()
        report.metadata.additional_info = {}
        return report

    @patch("app.reports.weekly_news.create_robust_email_delivery")
    def test_weekly_report_email_success(
        self, mock_create_delivery, mock_settings, mock_aggregator, mock_digest, mock_report
    ):
        """Test successful weekly report email delivery."""
        from app.reports.weekly_news import WeeklyNewsReport

        # Setup mocks
        mock_delivery = Mock()
        mock_delivery.send_with_fallback.return_value = {
            "delivered": True,
            "method": "email",
            "response": {"id": "msg_123"},
            "attempts": 1,
            "fallback_file": None,
            "error": None,
        }
        mock_create_delivery.return_value = mock_delivery

        # Create report instance
        report_generator = WeeklyNewsReport(
            aggregator=mock_aggregator, notion_client=None, settings=mock_settings
        )

        # Mock the _create_email_bulletin method
        with patch.object(
            report_generator, "_create_email_bulletin", return_value="<p>Test bulletin</p>"
        ):
            report_generator._send_email_report(mock_report, mock_digest)

        # Verify delivery was attempted
        mock_delivery.send_with_fallback.assert_called_once()
        assert mock_report.email_sent is True

    @patch("app.reports.weekly_news.create_robust_email_delivery")
    def test_weekly_report_email_fallback(
        self, mock_create_delivery, mock_settings, mock_aggregator, mock_digest, mock_report
    ):
        """Test weekly report with email failure and HTML fallback."""
        from app.reports.weekly_news import WeeklyNewsReport

        # Setup mocks for fallback scenario
        mock_delivery = Mock()
        mock_delivery.send_with_fallback.return_value = {
            "delivered": True,
            "method": "file",
            "response": None,
            "attempts": 3,
            "fallback_file": "/path/to/fallback.html",
            "error": "SSL error",
        }
        mock_create_delivery.return_value = mock_delivery

        # Create report instance
        report_generator = WeeklyNewsReport(
            aggregator=mock_aggregator, notion_client=None, settings=mock_settings
        )

        # Mock the _create_email_bulletin method
        with patch.object(
            report_generator, "_create_email_bulletin", return_value="<p>Test bulletin</p>"
        ):
            report_generator._send_email_report(mock_report, mock_digest)

        # Verify fallback was handled
        assert mock_report.email_sent is True  # Still marked as "sent" via fallback
        assert mock_report.metadata.additional_info["fallback_file"] == "/path/to/fallback.html"
        assert mock_report.metadata.additional_info["delivery_method"] == "file_fallback"


if __name__ == "__main__":
    pytest.main([__file__])
