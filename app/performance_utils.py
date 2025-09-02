# app/performance_utils.py
from __future__ import annotations
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Callable, Optional, Tuple
from functools import lru_cache

class SmartRateLimiter:
    """Adaptive rate limiter that only delays when necessary."""
    
    def __init__(self, calls_per_second: float = 2.0, burst_size: int = 5):
        self.calls_per_second = calls_per_second
        self.burst_size = burst_size
        self.tokens = burst_size
        self.last_refill = time.time()
        self.lock = threading.Lock()
    
    def wait_if_needed(self):
        """Wait only if we've exceeded the rate limit."""
        with self.lock:
            now = time.time()
            # Refill tokens based on elapsed time
            elapsed = now - self.last_refill
            self.tokens = min(self.burst_size, self.tokens + elapsed * self.calls_per_second)
            self.last_refill = now
            
            if self.tokens >= 1:
                self.tokens -= 1
                return  # No waiting needed
            
            # Need to wait for next token
            wait_time = (1 - self.tokens) / self.calls_per_second
            time.sleep(wait_time)
            self.tokens = 0

class ParallelProcessor:
    """Utility for processing items in parallel with proper error handling."""
    
    @staticmethod
    def process_batch(items: List[Any], 
                     processor_func: Callable,
                     max_workers: int = 8,
                     timeout: Optional[float] = None) -> Dict[Any, Any]:
        """Process a batch of items in parallel."""
        results = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_item = {
                executor.submit(processor_func, item): item 
                for item in items
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_item, timeout=timeout):
                item = future_to_item[future]
                try:
                    results[item] = future.result()
                except Exception as e:
                    print(f"[PARALLEL] Error processing {item}: {e}")
                    results[item] = None
                    
        return results
    
    @staticmethod
    def process_dict_batch(items_dict: Dict[str, Any],
                          processor_func: Callable,
                          max_workers: int = 8,
                          timeout: Optional[float] = None) -> Dict[str, Any]:
        """Process a dictionary of items in parallel."""
        results = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks with key-value pairs
            future_to_key = {
                executor.submit(processor_func, key, value): key 
                for key, value in items_dict.items()
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_key, timeout=timeout):
                key = future_to_key[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    print(f"[PARALLEL] Error processing {key}: {e}")
                    results[key] = None
                    
        return results

class ConcurrentAPIClient:
    """Utility for making concurrent API calls with rate limiting."""
    
    def __init__(self, rate_limiter: Optional[SmartRateLimiter] = None):
        self.rate_limiter = rate_limiter or SmartRateLimiter(calls_per_second=3.0)
    
    def batch_api_calls(self, 
                       api_calls: List[Callable],
                       max_workers: int = 6,
                       timeout: Optional[float] = 30) -> List[Any]:
        """Execute multiple API calls concurrently with rate limiting."""
        
        def rate_limited_call(call_func):
            self.rate_limiter.wait_if_needed()
            return call_func()
        
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(rate_limited_call, call) for call in api_calls]
            
            for future in as_completed(futures, timeout=timeout):
                try:
                    results.append(future.result())
                except Exception as e:
                    print(f"[API] Call failed: {e}")
                    results.append(None)
                    
        return [r for r in results if r is not None]

# Cache for expensive operations
@lru_cache(maxsize=500)
def cached_domain_validation(domain: str) -> bool:
    """Cache domain validation results to avoid redundant checks."""
    try:
        from app.dossier_baseline import validate_domain_to_url
        return validate_domain_to_url(domain) is not None
    except Exception:
        return False

@lru_cache(maxsize=1000)
def cached_text_hash_extract(text_hash: int, extraction_func_name: str) -> str:
    """Cache expensive text extraction operations."""
    # This is a placeholder for cached text processing
    # Actual implementation would need to reconstruct the function call
    return ""

def has_valid_domain(org: Dict[str, Any]) -> bool:
    """
    Check if organization has CSV metabase domain/website data that should be preserved.
    CSV domain_root and website fields are absolute priority over any existing Notion data.
    """
    # CSV metabase fields are priority - if these exist, don't search - handle None values safely
    csv_domain_root = str(org.get("domain_root") or "").strip()
    csv_website = str(org.get("website") or "").strip()
    
    # If we have either CSV field, always preserve (don't search)
    if csv_domain_root or csv_website:
        return True
        
    return False

def should_skip_processing(org: Dict[str, Any], operation_type: str) -> bool:
    """Determine if we can skip expensive operations for this org."""
    
    if operation_type == "domain_resolution":
        return has_valid_domain(org)
    
    elif operation_type == "funding_collection":
        # Skip if we have recent funding data (less than 30 days old)
        last_funding_check = org.get("last_funding_check")
        if last_funding_check:
            try:
                from datetime import datetime, timedelta
                last_check = datetime.fromisoformat(last_funding_check)
                if datetime.now() - last_check < timedelta(days=30):
                    return True
            except Exception:
                pass
    
    elif operation_type == "news_collection":
        # Skip if we have very recent news data (less than 6 hours old)
        last_news_check = org.get("last_news_check")
        if last_news_check:
            try:
                from datetime import datetime, timedelta
                last_check = datetime.fromisoformat(last_news_check)
                if datetime.now() - last_check < timedelta(hours=6):
                    return True
            except Exception:
                pass
    
    return False

class PerformanceMonitor:
    """Simple performance monitoring utility."""
    
    def __init__(self):
        self.timings = {}
        self.start_times = {}
    
    def start_timer(self, operation: str):
        self.start_times[operation] = time.time()
    
    def end_timer(self, operation: str):
        if operation in self.start_times:
            elapsed = time.time() - self.start_times[operation]
            if operation not in self.timings:
                self.timings[operation] = []
            self.timings[operation].append(elapsed)
            del self.start_times[operation]
            return elapsed
        return 0
    
    def get_stats(self) -> Dict[str, Dict[str, float]]:
        stats = {}
        for operation, times in self.timings.items():
            stats[operation] = {
                "count": len(times),
                "total": sum(times),
                "average": sum(times) / len(times),
                "min": min(times),
                "max": max(times)
            }
        return stats
    
    def print_stats(self):
        print("\n=== Performance Stats ===")
        for operation, stats in self.get_stats().items():
            print(f"{operation}:")
            print(f"  Count: {stats['count']}")
            print(f"  Total: {stats['total']:.2f}s")
            print(f"  Average: {stats['average']:.2f}s")
            print(f"  Min/Max: {stats['min']:.2f}s / {stats['max']:.2f}s")

# Global instances
DEFAULT_RATE_LIMITER = SmartRateLimiter(calls_per_second=2.5, burst_size=8)
DEFAULT_API_CLIENT = ConcurrentAPIClient(DEFAULT_RATE_LIMITER)
PERFORMANCE_MONITOR = PerformanceMonitor()