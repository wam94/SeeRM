"""
Structured logging configuration for SeeRM application.

Provides consistent, structured logging with correlation IDs and rich formatting.
"""

import sys
import uuid
import structlog
from typing import Optional
from rich.console import Console
from rich.logging import RichHandler

# Global correlation ID for request tracing
_correlation_id: Optional[str] = None


def set_correlation_id(correlation_id: Optional[str] = None) -> str:
    """Set a correlation ID for the current execution context."""
    global _correlation_id
    _correlation_id = correlation_id or str(uuid.uuid4())[:8]
    return _correlation_id


def get_correlation_id() -> Optional[str]:
    """Get the current correlation ID."""
    return _correlation_id


def add_correlation_id(logger, method_name, event_dict):
    """Add correlation ID to log entries."""
    if _correlation_id:
        event_dict["correlation_id"] = _correlation_id
    return event_dict


def setup_logging(debug: bool = False, rich_output: bool = True) -> None:
    """
    Configure structured logging for the application.
    
    Args:
        debug: Enable debug level logging
        rich_output: Use rich formatting for console output
    """
    
    # Configure structlog
    processors = [
        structlog.contextvars.merge_contextvars,
        add_correlation_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.dev.set_exc_info,
    ]
    
    if rich_output:
        # Rich console output for development
        console = Console(stderr=True, force_terminal=True)
        processors.append(
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=structlog.dev.rich_traceback
            )
        )
        
        # Configure standard library logging with rich
        import logging
        logging.basicConfig(
            level=logging.DEBUG if debug else logging.INFO,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(console=console, show_path=False)]
        )
    else:
        # JSON output for production
        processors.append(structlog.processors.JSONRenderer())
        
        import logging
        logging.basicConfig(
            level=logging.DEBUG if debug else logging.INFO,
            format="%(message)s",
            stream=sys.stdout
        )
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if debug else logging.INFO
        ),
        logger_factory=structlog.WriteLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a logger instance with the given name."""
    return structlog.get_logger(name)


# Convenience loggers for common components
gmail_logger = structlog.get_logger("gmail")
notion_logger = structlog.get_logger("notion") 
digest_logger = structlog.get_logger("digest")
news_logger = structlog.get_logger("news")
baseline_logger = structlog.get_logger("baseline")
performance_logger = structlog.get_logger("performance")