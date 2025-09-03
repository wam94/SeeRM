"""
Custom exceptions for SeeRM application.

Provides a hierarchy of exceptions for better error handling and debugging.
"""

from typing import Optional, Dict, Any


class SeeRMError(Exception):
    """Base exception for all SeeRM errors."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(SeeRMError):
    """Raised when there are configuration issues."""
    pass


class DataAccessError(SeeRMError):
    """Base class for data access errors."""
    pass


class GmailError(DataAccessError):
    """Gmail API related errors."""
    pass


class NotionError(DataAccessError):
    """Notion API related errors."""
    pass


class GoogleSearchError(DataAccessError):
    """Google Custom Search API related errors."""
    pass


class OpenAIError(DataAccessError):
    """OpenAI API related errors."""
    pass


class CSVParsingError(SeeRMError):
    """CSV parsing and validation errors."""
    pass


class ValidationError(SeeRMError):
    """Data validation errors."""
    pass


class WorkflowError(SeeRMError):
    """Workflow execution errors."""
    pass


class RateLimitError(DataAccessError):
    """Rate limiting errors."""
    
    def __init__(self, message: str, retry_after: Optional[float] = None, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class CircuitBreakerError(DataAccessError):
    """Circuit breaker is open, preventing calls."""
    pass


class TimeoutError(DataAccessError):
    """Operation timeout errors."""
    pass


class ExternalServiceError(DataAccessError):
    """External service is unavailable or returning errors."""
    
    def __init__(self, service: str, message: str, status_code: Optional[int] = None, **kwargs):
        super().__init__(f"{service}: {message}", **kwargs)
        self.service = service
        self.status_code = status_code