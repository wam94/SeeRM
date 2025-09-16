"""
Parallel processing utilities for intelligence data fetching.

Provides concurrent execution for API calls and data processing
to significantly reduce overall runtime.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")


class ParallelProcessor:
    """
    Handles parallel processing of intelligence data operations.

    Features:
    - Concurrent API calls with rate limiting
    - Batch processing with configurable workers
    - Error isolation and partial result handling
    - Progress tracking and logging
    """

    def __init__(self, max_workers: int = 10):
        """
        Initialize parallel processor.

        Args:
            max_workers: Maximum number of concurrent threads
        """
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        logger.info("Parallel processor initialized", max_workers=max_workers)

    def fetch_company_profiles(
        self, callsigns: List[str], fetch_func: Callable[[str], Optional[Any]]
    ) -> Dict[str, Any]:
        """
        Fetch multiple company profiles in parallel.

        Args:
            callsigns: List of company callsigns
            fetch_func: Function to fetch single profile

        Returns:
            Dictionary mapping callsign to profile data
        """
        results = {}
        errors = []

        logger.info(f"Fetching {len(callsigns)} company profiles in parallel")

        # Submit all tasks
        futures = {self.executor.submit(fetch_func, callsign): callsign for callsign in callsigns}

        # Collect results as they complete
        for future in as_completed(futures):
            callsign = futures[future]
            try:
                result = future.result(timeout=30)
                if result:
                    results[callsign] = result
            except Exception as e:
                logger.warning(f"Failed to fetch profile for {callsign}", error=str(e))
                errors.append((callsign, str(e)))

        logger.info(
            "Profile fetch completed",
            success=len(results),
            failed=len(errors),
            total=len(callsigns),
        )

        return results

    def batch_classify_news(
        self,
        news_items_batches: List[List[Any]],
        classify_func: Callable[[List[Any]], List[Any]],
    ) -> List[Any]:
        """
        Classify news items in parallel batches.

        Args:
            news_items_batches: List of news item batches
            classify_func: Function to classify a batch

        Returns:
            Flat list of classified news items
        """
        all_results = []

        logger.info(f"Classifying {len(news_items_batches)} batches in parallel")

        # Submit all classification tasks
        futures = {
            self.executor.submit(classify_func, batch): i
            for i, batch in enumerate(news_items_batches)
        }

        # Collect results in order
        batch_results = [None] * len(news_items_batches)
        for future in as_completed(futures):
            batch_index = futures[future]
            try:
                result = future.result(timeout=60)
                batch_results[batch_index] = result
            except Exception as e:
                logger.warning(f"Batch {batch_index} classification failed", error=str(e))
                batch_results[batch_index] = news_items_batches[batch_index]  # Return unclassified

        # Flatten results
        for batch_result in batch_results:
            if batch_result:
                all_results.extend(batch_result)

        logger.info("News classification completed", total_items=len(all_results))
        return all_results

    def parallel_fetch_news(
        self,
        companies: List[str],
        fetch_news_func: Callable[[str, int], List[Any]],
        days: int = 90,
    ) -> Dict[str, List[Any]]:
        """
        Fetch news for multiple companies in parallel.

        Args:
            companies: List of company callsigns
            fetch_news_func: Function to fetch news for one company
            days: Number of days to fetch

        Returns:
            Dictionary mapping callsign to news items
        """
        news_by_company = {}

        logger.info(f"Fetching news for {len(companies)} companies in parallel")

        # Create partial function with days parameter
        fetch_with_days = partial(fetch_news_func, days=days)

        # Submit all tasks
        futures = {self.executor.submit(fetch_with_days, company): company for company in companies}

        # Collect results
        for future in as_completed(futures):
            company = futures[future]
            try:
                news_items = future.result(timeout=30)
                if news_items:
                    news_by_company[company] = news_items
            except Exception as e:
                logger.warning(f"Failed to fetch news for {company}", error=str(e))

        total_news = sum(len(items) for items in news_by_company.values())
        logger.info(
            "News fetch completed",
            companies_with_news=len(news_by_company),
            total_news_items=total_news,
        )

        return news_by_company

    def parallel_notion_queries(
        self, queries: List[Tuple[str, Dict[str, Any]]], query_func: Callable
    ) -> List[Any]:
        """
        Execute multiple Notion queries in parallel.

        Args:
            queries: List of (database_id, filter_dict) tuples
            query_func: Notion query function

        Returns:
            List of query results
        """
        results = []

        logger.info(f"Executing {len(queries)} Notion queries in parallel")

        # Submit all queries
        futures = {
            self.executor.submit(query_func, db_id, filters): i
            for i, (db_id, filters) in enumerate(queries)
        }

        # Collect results in order
        ordered_results = [None] * len(queries)
        for future in as_completed(futures):
            query_index = futures[future]
            try:
                result = future.result(timeout=45)
                ordered_results[query_index] = result
            except Exception as e:
                logger.warning(f"Query {query_index} failed", error=str(e))
                ordered_results[query_index] = []

        # Filter out None values
        results = [r for r in ordered_results if r is not None]

        logger.info("Notion queries completed", successful=len(results))
        return results

    async def async_batch_process(
        self, items: List[T], process_func: Callable[[T], Any], batch_size: int = 20
    ) -> List[Any]:
        """
        Process items in batches using async/await.

        Args:
            items: Items to process
            process_func: Async function to process each item
            batch_size: Number of items per batch

        Returns:
            List of processed results
        """
        results = []

        for i in range(0, len(items), batch_size):
            batch = items[i : i + batch_size]

            # Process batch concurrently
            tasks = [asyncio.create_task(process_func(item)) for item in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Handle results and errors
            for j, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    logger.warning(f"Item {i+j} processing failed", error=str(result))
                else:
                    results.append(result)

        return results

    def shutdown(self):
        """Shutdown the executor gracefully."""
        self.executor.shutdown(wait=True)
        logger.info("Parallel processor shutdown")


# Global processor instance
_processor: Optional[ParallelProcessor] = None


def get_parallel_processor(max_workers: int = 10) -> ParallelProcessor:
    """
    Get or create global parallel processor.

    Args:
        max_workers: Maximum number of workers

    Returns:
        ParallelProcessor instance
    """
    global _processor
    if _processor is None:
        _processor = ParallelProcessor(max_workers=max_workers)
    return _processor
