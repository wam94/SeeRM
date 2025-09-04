"""
Reliability patterns for SeeRM application.

Provides circuit breakers, retry logic, rate limiting, and other resilience patterns.
"""

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, Optional, Type, Union

import structlog
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.exceptions import CircuitBreakerError, ExternalServiceError, RateLimitError
from app.core.exceptions import TimeoutError as SeeRMTimeoutError

logger = structlog.get_logger(__name__)


class CircuitBreakerState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Circuit breaker pattern implementation.

    Prevents cascading failures by opening the circuit when error thresholds are exceeded.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: Type[Exception] = Exception,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = CircuitBreakerState.CLOSED
        self._lock = threading.Lock()

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        with self._lock:
            if self.state == CircuitBreakerState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitBreakerState.HALF_OPEN
                    logger.info("Circuit breaker half-open", name=self.name)
                else:
                    raise CircuitBreakerError(
                        f"Circuit breaker '{self.name}' is open. "
                        f"Next attempt allowed at {self.last_failure_time + self.recovery_timeout}"
                    )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        return (
            self.last_failure_time is not None
            and time.time() >= self.last_failure_time + self.recovery_timeout
        )

    def _on_success(self):
        """Handle successful execution."""
        with self._lock:
            self.failure_count = 0
            if self.state == CircuitBreakerState.HALF_OPEN:
                self.state = CircuitBreakerState.CLOSED
                logger.info("Circuit breaker closed", name=self.name)

    def _on_failure(self):
        """Handle failed execution."""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.failure_count >= self.failure_threshold:
                self.state = CircuitBreakerState.OPEN
                logger.warning(
                    "Circuit breaker opened",
                    name=self.name,
                    failure_count=self.failure_count,
                    threshold=self.failure_threshold,
                )

    @property
    def status(self) -> Dict[str, Any]:
        """Get current circuit breaker status."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "last_failure_time": self.last_failure_time,
            "next_attempt_time": (
                self.last_failure_time + self.recovery_timeout if self.last_failure_time else None
            ),
        }


class AdaptiveRateLimiter:
    """
    Adaptive rate limiter using token bucket algorithm.

    Automatically adjusts rate based on error patterns.
    """

    def __init__(self, calls_per_second: float = 2.0, burst_size: int = 5, adaptive: bool = True):
        self.base_calls_per_second = calls_per_second
        self.current_calls_per_second = calls_per_second
        self.burst_size = burst_size
        self.adaptive = adaptive

        self.tokens = burst_size
        self.last_refill = time.time()
        self.consecutive_errors = 0
        self.consecutive_successes = 0
        self._lock = threading.Lock()

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """
        Acquire a token, waiting if necessary.

        Returns True if token acquired, False if timeout exceeded.
        """
        start_time = time.time()

        while True:
            with self._lock:
                self._refill_tokens()

                if self.tokens >= 1:
                    self.tokens -= 1
                    return True

                # Calculate wait time for next token
                wait_time = (1 - self.tokens) / self.current_calls_per_second

            if timeout and (time.time() - start_time + wait_time) > timeout:
                return False

            time.sleep(min(wait_time, 0.1))  # Sleep in small increments

    def _refill_tokens(self):
        """Refill token bucket based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill

        # Add tokens based on elapsed time
        self.tokens = min(self.burst_size, self.tokens + elapsed * self.current_calls_per_second)
        self.last_refill = now

    def on_success(self):
        """Called after successful operation to potentially increase rate."""
        if not self.adaptive:
            return

        self.consecutive_errors = 0
        self.consecutive_successes += 1

        # Gradually increase rate after consecutive successes
        if self.consecutive_successes >= 10:
            self.current_calls_per_second = min(
                self.base_calls_per_second * 1.5, self.current_calls_per_second * 1.1
            )
            self.consecutive_successes = 0

    def on_error(self):
        """Called after error to potentially decrease rate."""
        if not self.adaptive:
            return

        self.consecutive_successes = 0
        self.consecutive_errors += 1

        # Decrease rate after errors
        if self.consecutive_errors >= 3:
            self.current_calls_per_second = max(
                self.base_calls_per_second * 0.5, self.current_calls_per_second * 0.8
            )
            self.consecutive_errors = 0

    @property
    def status(self) -> Dict[str, Any]:
        """Get current rate limiter status."""
        return {
            "base_calls_per_second": self.base_calls_per_second,
            "current_calls_per_second": self.current_calls_per_second,
            "tokens": self.tokens,
            "consecutive_errors": self.consecutive_errors,
            "consecutive_successes": self.consecutive_successes,
        }


def with_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
    expected_exception: Type[Exception] = Exception,
):
    """Decorator to add circuit breaker protection to functions."""

    # Global circuit breaker registry
    if not hasattr(with_circuit_breaker, "_breakers"):
        with_circuit_breaker._breakers = {}

    if name not in with_circuit_breaker._breakers:
        with_circuit_breaker._breakers[name] = CircuitBreaker(
            name, failure_threshold, recovery_timeout, expected_exception
        )

    breaker = with_circuit_breaker._breakers[name]

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            return breaker.call(func, *args, **kwargs)

        wrapper.circuit_breaker = breaker
        return wrapper

    return decorator


def with_retry(
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    backoff_max: float = 60.0,
    retry_exceptions: tuple = (Exception,),
):
    """Decorator to add retry logic with exponential backoff."""

    def decorator(func: Callable) -> Callable:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=0.5, max=backoff_max),
            retry=retry_if_exception_type(retry_exceptions),
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except retry_exceptions as e:
                logger.warning(
                    "Retrying operation",
                    function=func.__name__,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                raise

        return wrapper

    return decorator


def with_timeout(timeout_seconds: float):
    """Decorator to add timeout protection to functions."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            import signal

            def timeout_handler(signum, frame):
                raise SeeRMTimeoutError(
                    f"Function {func.__name__} timed out after {timeout_seconds} seconds"
                )

            # Set timeout alarm
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(int(timeout_seconds))

            try:
                result = func(*args, **kwargs)
                return result
            finally:
                # Cancel alarm and restore old handler
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

        return wrapper

    return decorator


class ParallelProcessor:
    """Enhanced parallel processing with reliability patterns."""

    def __init__(
        self,
        max_workers: int = 6,
        rate_limiter: Optional[AdaptiveRateLimiter] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.max_workers = max_workers
        self.rate_limiter = rate_limiter
        self.circuit_breaker = circuit_breaker

    def process_batch(
        self, items: list, processor_func: Callable, timeout: Optional[float] = None
    ) -> Dict[Any, Any]:
        """Process items in parallel with reliability patterns."""

        def safe_process_item(item):
            # Rate limiting
            if self.rate_limiter:
                if not self.rate_limiter.acquire(timeout=5.0):
                    raise RateLimitError("Rate limit timeout exceeded")

            # Circuit breaker protection
            if self.circuit_breaker:
                return self.circuit_breaker.call(processor_func, item)
            else:
                return processor_func(item)

        results = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_item = {executor.submit(safe_process_item, item): item for item in items}

            # Collect results
            for future in as_completed(future_to_item, timeout=timeout):
                item = future_to_item[future]
                try:
                    result = future.result()
                    results[item] = result

                    # Update rate limiter on success
                    if self.rate_limiter:
                        self.rate_limiter.on_success()

                except Exception as e:
                    logger.error(
                        "Parallel processing error",
                        item=str(item)[:100],
                        error=str(e),
                        error_type=type(e).__name__,
                    )
                    results[item] = None

                    # Update rate limiter on error
                    if self.rate_limiter:
                        self.rate_limiter.on_error()

        return results


class HealthChecker:
    """Health checking for external services."""

    def __init__(self):
        self.checks: Dict[str, Callable] = {}
        self.last_results: Dict[str, Dict[str, Any]] = {}

    def register_check(self, name: str, check_func: Callable):
        """Register a health check function."""
        self.checks[name] = check_func

    def check_all(self) -> Dict[str, Dict[str, Any]]:
        """Run all registered health checks."""
        results = {}

        for name, check_func in self.checks.items():
            start_time = time.time()
            try:
                check_result = check_func()
                results[name] = {
                    "status": "healthy",
                    "response_time_ms": (time.time() - start_time) * 1000,
                    "details": check_result if isinstance(check_result, dict) else {},
                }
            except Exception as e:
                results[name] = {
                    "status": "unhealthy",
                    "response_time_ms": (time.time() - start_time) * 1000,
                    "error": str(e),
                    "error_type": type(e).__name__,
                }

        self.last_results = results
        return results

    def is_healthy(self, service_name: Optional[str] = None) -> bool:
        """Check if service(s) are healthy."""
        if not self.last_results:
            self.check_all()

        if service_name:
            return self.last_results.get(service_name, {}).get("status") == "healthy"

        return all(result.get("status") == "healthy" for result in self.last_results.values())


# Global instances
default_rate_limiter = AdaptiveRateLimiter(calls_per_second=2.5, burst_size=8)
health_checker = HealthChecker()


def get_circuit_breaker_status() -> Dict[str, Any]:
    """Get status of all circuit breakers."""
    if hasattr(with_circuit_breaker, "_breakers"):
        return {name: breaker.status for name, breaker in with_circuit_breaker._breakers.items()}
    return {}


def reset_circuit_breaker(name: str) -> bool:
    """Reset a circuit breaker by name."""
    if hasattr(with_circuit_breaker, "_breakers"):
        if name in with_circuit_breaker._breakers:
            breaker = with_circuit_breaker._breakers[name]
            with breaker._lock:
                breaker.failure_count = 0
                breaker.state = CircuitBreakerState.CLOSED
                breaker.last_failure_time = None
            logger.info("Circuit breaker reset", name=name)
            return True
    return False


def track_performance(operation_name: str):
    """
    Decorator to track performance metrics for operations.

    Args:
        operation_name: Name of the operation for logging
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            operation_id = f"{operation_name}_{int(start_time)}"

            logger.info(
                "Performance tracking started", operation=operation_name, operation_id=operation_id
            )

            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time

                logger.info(
                    "Performance tracking completed",
                    operation=operation_name,
                    operation_id=operation_id,
                    duration_seconds=duration,
                    status="success",
                )

                return result

            except Exception as e:
                duration = time.time() - start_time

                logger.error(
                    "Performance tracking failed",
                    operation=operation_name,
                    operation_id=operation_id,
                    duration_seconds=duration,
                    status="failed",
                    error=str(e),
                )

                raise

        return wrapper

    return decorator
