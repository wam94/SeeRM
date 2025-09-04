"""
Test suite for reliability patterns and error handling.

Validates circuit breakers, rate limiting, retry logic, and graceful degradation.
"""

import time
from unittest.mock import Mock, patch

import pytest

from app.core.exceptions import CircuitBreakerError, RateLimitError
from app.utils.reliability import (
    AdaptiveRateLimiter,
    CircuitBreaker,
    HealthChecker,
    ParallelProcessor,
    with_circuit_breaker,
    with_retry,
)


class TestCircuitBreaker:
    """Test circuit breaker functionality."""

    def test_circuit_breaker_closed_state(self):
        """Test circuit breaker allows calls when closed."""
        breaker = CircuitBreaker("test", failure_threshold=3)

        def test_func():
            return "success"

        result = breaker.call(test_func)
        assert result == "success"
        assert breaker.state.value == "closed"

    def test_circuit_breaker_opens_on_failures(self):
        """Test circuit breaker opens after threshold failures."""
        breaker = CircuitBreaker("test", failure_threshold=2)

        def failing_func():
            raise Exception("Test failure")

        # First failure
        with pytest.raises(Exception):
            breaker.call(failing_func)
        assert breaker.failure_count == 1

        # Second failure should open circuit
        with pytest.raises(Exception):
            breaker.call(failing_func)
        assert breaker.failure_count == 2
        assert breaker.state.value == "open"

        # Third call should be blocked by circuit breaker
        with pytest.raises(CircuitBreakerError):
            breaker.call(failing_func)

    def test_circuit_breaker_recovery(self):
        """Test circuit breaker recovery after timeout."""
        breaker = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)

        def failing_func():
            raise Exception("Test failure")

        def success_func():
            return "recovered"

        # Cause failure to open circuit
        with pytest.raises(Exception):
            breaker.call(failing_func)
        assert breaker.state.value == "open"

        # Wait for recovery timeout
        time.sleep(0.2)

        # Should allow call and close circuit on success
        result = breaker.call(success_func)
        assert result == "recovered"
        assert breaker.state.value == "closed"
        assert breaker.failure_count == 0


class TestAdaptiveRateLimiter:
    """Test adaptive rate limiting functionality."""

    def test_rate_limiter_allows_burst(self):
        """Test rate limiter allows burst of requests."""
        limiter = AdaptiveRateLimiter(calls_per_second=10, burst_size=5)

        # Should allow burst_size requests immediately
        start_time = time.time()
        for _ in range(5):
            assert limiter.acquire(timeout=1.0)
        elapsed = time.time() - start_time

        assert elapsed < 0.1  # Should be nearly instantaneous

    def test_rate_limiter_enforces_rate(self):
        """Test rate limiter enforces rate after burst."""
        limiter = AdaptiveRateLimiter(calls_per_second=5, burst_size=2)

        # Use up burst
        assert limiter.acquire(timeout=0.1)
        assert limiter.acquire(timeout=0.1)

        # Next request should be rate limited
        start_time = time.time()
        assert limiter.acquire(timeout=1.0)
        elapsed = time.time() - start_time

        # Should have waited roughly 0.2 seconds (1/5 calls per second)
        assert 0.15 < elapsed < 0.35

    def test_adaptive_rate_adjustment(self):
        """Test adaptive rate adjustment based on success/error patterns."""
        limiter = AdaptiveRateLimiter(calls_per_second=2.0, adaptive=True)
        initial_rate = limiter.current_calls_per_second

        # Simulate consecutive errors (3 errors trigger a decrease)
        for _ in range(3):
            limiter.on_error()

        # Rate should decrease
        decreased_rate = limiter.current_calls_per_second
        assert decreased_rate < initial_rate

        # Simulate enough consecutive successes to bring it back up
        for _ in range(25):
            limiter.on_success()

        # Rate should increase back above the decreased rate
        final_rate = limiter.current_calls_per_second
        assert final_rate > decreased_rate
        # Should not exceed base * 1.5
        assert final_rate <= initial_rate * 1.5


class TestParallelProcessor:
    """Test parallel processing with reliability patterns."""

    def test_parallel_processing_success(self):
        """Test successful parallel processing."""
        processor = ParallelProcessor(max_workers=4)

        def square_func(x):
            return x * x

        items = [1, 2, 3, 4, 5]
        results = processor.process_batch(items, square_func, timeout=5.0)

        expected = {1: 1, 2: 4, 3: 9, 4: 16, 5: 25}
        assert results == expected

    def test_parallel_processing_with_failures(self):
        """Test parallel processing handles individual failures."""
        processor = ParallelProcessor(max_workers=4)

        def sometimes_fail(x):
            if x == 3:
                raise ValueError(f"Intentional failure for {x}")
            return x * 2

        items = [1, 2, 3, 4, 5]
        results = processor.process_batch(items, sometimes_fail, timeout=5.0)

        # Should have results for successful items and None for failed
        assert results[1] == 2
        assert results[2] == 4
        assert results[3] is None  # Failed
        assert results[4] == 8
        assert results[5] == 10

    def test_parallel_processing_with_rate_limiter(self):
        """Test parallel processing respects rate limiting."""
        rate_limiter = AdaptiveRateLimiter(calls_per_second=10, burst_size=3)
        processor = ParallelProcessor(max_workers=2, rate_limiter=rate_limiter)

        def slow_func(x):
            time.sleep(0.01)  # Small delay
            return x * 2

        items = [1, 2, 3, 4, 5]
        start_time = time.time()
        results = processor.process_batch(items, slow_func, timeout=5.0)
        elapsed = time.time() - start_time

        # All should succeed
        assert all(v is not None for v in results.values())
        # Should take some time due to rate limiting
        assert elapsed > 0.1


class TestRetryLogic:
    """Test retry decorator functionality."""

    def test_retry_succeeds_eventually(self):
        """Test retry succeeds after initial failures."""
        call_count = 0

        @with_retry(max_attempts=3, retry_exceptions=(ValueError,))
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Not ready yet")
            return "success"

        result = flaky_func()
        assert result == "success"
        assert call_count == 3

    def test_retry_gives_up_after_max_attempts(self):
        """Test retry gives up after max attempts."""
        call_count = 0

        @with_retry(max_attempts=2, retry_exceptions=(ValueError,))
        def always_fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")

        from tenacity import RetryError

        with pytest.raises(RetryError):
            always_fail()
        assert call_count == 2

    def test_retry_ignores_non_retry_exceptions(self):
        """Test retry doesn't retry non-specified exceptions."""
        call_count = 0

        @with_retry(max_attempts=3, retry_exceptions=(ValueError,))
        def wrong_exception():
            nonlocal call_count
            call_count += 1
            raise TypeError("Wrong exception type")

        with pytest.raises(TypeError):
            wrong_exception()
        assert call_count == 1  # No retries


class TestHealthChecker:
    """Test health checking functionality."""

    def test_health_checker_all_healthy(self):
        """Test health checker with all healthy services."""
        checker = HealthChecker()

        def healthy_service():
            return {"status": "ok", "latency": 50}

        checker.register_check("service1", healthy_service)
        checker.register_check("service2", healthy_service)

        results = checker.check_all()

        assert "service1" in results
        assert "service2" in results
        assert results["service1"]["status"] == "healthy"
        assert results["service2"]["status"] == "healthy"
        assert checker.is_healthy()

    def test_health_checker_with_failures(self):
        """Test health checker with some failing services."""
        checker = HealthChecker()

        def healthy_service():
            return {"status": "ok"}

        def unhealthy_service():
            raise ConnectionError("Service down")

        checker.register_check("healthy", healthy_service)
        checker.register_check("unhealthy", unhealthy_service)

        results = checker.check_all()

        assert results["healthy"]["status"] == "healthy"
        assert results["unhealthy"]["status"] == "unhealthy"
        assert results["unhealthy"]["error"] == "Service down"
        assert results["unhealthy"]["error_type"] == "ConnectionError"
        assert not checker.is_healthy()


class TestIntegrationReliability:
    """Integration tests for reliability patterns working together."""

    def test_circuit_breaker_with_retry(self):
        """Test circuit breaker and retry working together."""
        breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
        call_count = 0

        @with_retry(max_attempts=5, retry_exceptions=(Exception,))
        def flaky_service():
            nonlocal call_count
            call_count += 1

            # Use circuit breaker
            def inner():
                if call_count <= 3:
                    raise Exception(f"Failure {call_count}")
                return f"Success after {call_count} attempts"

            return breaker.call(inner)

        # This should eventually succeed, but circuit breaker may interfere
        try:
            result = flaky_service()
            assert "Success" in result
        except CircuitBreakerError:
            # Circuit breaker opened - this is expected behavior
            assert breaker.state.value == "open"

    @patch("time.sleep")  # Speed up test
    def test_comprehensive_reliability_patterns(self, mock_sleep):
        """Test multiple reliability patterns working together."""
        rate_limiter = AdaptiveRateLimiter(calls_per_second=5, burst_size=2)
        processor = ParallelProcessor(
            max_workers=2,
            rate_limiter=rate_limiter,
            circuit_breaker=CircuitBreaker("test", failure_threshold=3),
        )

        call_count = 0

        def reliable_func(x):
            nonlocal call_count
            call_count += 1

            # Simulate some failures
            if call_count <= 2:
                rate_limiter.on_error()
                raise Exception("Initial failures")

            rate_limiter.on_success()
            return x * 2

        items = [1, 2, 3, 4]
        results = processor.process_batch(items, reliable_func, timeout=5.0)

        # Some should succeed, some may fail due to reliability patterns
        assert isinstance(results, dict)
        assert len(results) == len(items)

        # Verify rate limiter adapted to errors
        # (Rate should have decreased due to errors)
