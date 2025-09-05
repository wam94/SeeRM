"""
Enhanced Notion client with reliability patterns and dry-run support.

Provides robust Notion API integration with circuit breakers, retry logic, and validation.
"""

import datetime
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
import structlog
from httpx import HTTPStatusError, TimeoutException

from app.core.config import NotionConfig
from app.core.exceptions import NotionError, ValidationError
from app.core.models import Company, NotionPage, NotionUpdateOperation
from app.utils.reliability import (
    AdaptiveRateLimiter,
    default_rate_limiter,
    with_circuit_breaker,
    with_retry,
)

logger = structlog.get_logger(__name__)

NOTION_API = "https://api.notion.com/v1"


class EnhancedNotionClient:
    """
    Enhanced Notion client with reliability patterns and structured error handling.
    """

    def __init__(
        self,
        config: NotionConfig,
        rate_limiter: Optional[AdaptiveRateLimiter] = None,
        dry_run: bool = False,
    ):
        self.config = config
        self.rate_limiter = rate_limiter or default_rate_limiter
        self.dry_run = dry_run

        # HTTP client with proper timeout and retries
        self.client = httpx.Client(
            timeout=httpx.Timeout(30.0), headers=self._get_headers(), follow_redirects=True
        )

        # Schema cache to avoid repeated API calls
        self._schema_cache: Dict[str, Dict[str, Any]] = {}

        logger.info(
            "Notion client initialized",
            dry_run=dry_run,
            companies_db=bool(config.companies_db_id),
            intel_db=bool(config.intel_db_id),
        )

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for Notion API requests."""
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Notion-Version": self.config.version,
            "Content-Type": "application/json",
        }

    def __del__(self):
        """Close HTTP client on cleanup."""
        if hasattr(self, "client"):
            self.client.close()

    @with_circuit_breaker(
        name="notion_api",
        failure_threshold=5,
        recovery_timeout=60.0,
        expected_exception=NotionError,
    )
    @with_retry(max_attempts=3, retry_exceptions=(HTTPStatusError, TimeoutException, NotionError))
    def _make_request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Make authenticated request to Notion API.

        Args:
            method: HTTP method
            path: API path (without base URL)
            json_data: JSON payload
            params: Query parameters

        Returns:
            Response JSON data

        Raises:
            NotionError: On API errors
        """
        # Rate limiting
        self.rate_limiter.acquire(timeout=10.0)

        url = f"{NOTION_API}{path}"

        try:
            logger.debug(
                "Making Notion API request", method=method, path=path, has_data=bool(json_data)
            )

            response = self.client.request(method=method, url=url, json=json_data, params=params)

            response.raise_for_status()

            self.rate_limiter.on_success()
            return response.json()

        except HTTPStatusError as e:
            error_msg = f"Notion API HTTP error: {e.response.status_code} - {e.response.text}"
            logger.error(
                "Notion API HTTP error",
                status_code=e.response.status_code,
                response_text=e.response.text[:500],
                path=path,
            )
            self.rate_limiter.on_error()
            raise NotionError(
                error_msg, details={"status_code": e.response.status_code, "path": path}
            )

        except TimeoutException as e:
            error_msg = f"Notion API timeout: {e}"
            logger.error("Notion API timeout", path=path, error=str(e))
            self.rate_limiter.on_error()
            raise NotionError(error_msg, details={"path": path})

        except Exception as e:
            error_msg = f"Unexpected Notion API error: {e}"
            logger.error("Unexpected Notion API error", path=path, error=str(e))
            self.rate_limiter.on_error()
            raise NotionError(error_msg, details={"path": path})

    def get_database_schema(self, database_id: str) -> Dict[str, Any]:
        """
        Get database schema with caching.

        Args:
            database_id: Notion database ID

        Returns:
            Database schema
        """
        if database_id in self._schema_cache:
            return self._schema_cache[database_id]

        schema = self._make_request("GET", f"/databases/{database_id}")
        self._schema_cache[database_id] = schema

        logger.debug("Database schema cached", database_id=database_id)
        return schema

    def get_title_property_name(self, schema: Dict[str, Any]) -> str:
        """Get the title property name from database schema."""
        for name, meta in (schema.get("properties", {}) or {}).items():
            if meta.get("type") == "title":
                return name
        return "Name"

    def property_exists(self, schema: Dict[str, Any], name: str, prop_type: str) -> bool:
        """Check if property exists with given type in schema."""
        meta = (schema.get("properties") or {}).get(name)
        return bool(meta and meta.get("type") == prop_type)

    def _create_rich_text_segments(self, text: str, chunk_size: int = 1800) -> Dict[str, Any]:
        """Create rich text segments for long text."""
        if not text:
            return {"rich_text": []}

        # Break into chunks to respect Notion's limits
        chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

        return {"rich_text": [{"type": "text", "text": {"content": chunk}} for chunk in chunks]}

    def _create_title_segments(self, text: str) -> Dict[str, Any]:
        """Create title segments for long titles."""
        if not text:
            return {"title": []}

        # Titles are more restrictive
        chunks = [text[i : i + 1800] for i in range(0, len(text), 1800)]

        return {"title": [{"type": "text", "text": {"content": chunk}} for chunk in chunks]}

    def _create_date_property(self, date_iso: Optional[str]) -> Dict[str, Any]:
        """Create date property."""
        return {"date": {"start": date_iso or datetime.date.today().isoformat()}}

    def query_database(
        self,
        database_id: str,
        filter_obj: Optional[Dict[str, Any]] = None,
        sorts: Optional[List[Dict[str, Any]]] = None,
        page_size: int = 100,
        start_cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Query database with filters and sorting.

        Args:
            database_id: Database ID
            filter_obj: Filter object
            sorts: Sort configurations
            page_size: Page size (max 100)
            start_cursor: Pagination cursor

        Returns:
            Query results
        """
        query_data = {"page_size": min(page_size, 100)}

        if filter_obj:
            query_data["filter"] = filter_obj
        if sorts:
            query_data["sorts"] = sorts
        if start_cursor:
            query_data["start_cursor"] = start_cursor

        return self._make_request("POST", f"/databases/{database_id}/query", json_data=query_data)

    def find_company_page(self, database_id: str, callsign: str) -> Optional[str]:
        """
        Find company page by callsign.

        Args:
            database_id: Companies database ID
            callsign: Company callsign to search for

        Returns:
            Page ID if found, None otherwise
        """
        try:
            schema = self.get_database_schema(database_id)
            title_prop = self.get_title_property_name(schema)

            response = self.query_database(
                database_id,
                filter_obj={"property": title_prop, "title": {"equals": callsign}},
                page_size=1,
            )

            results = response.get("results", [])
            return results[0]["id"] if results else None

        except Exception as e:
            logger.error(
                "Failed to find company page",
                callsign=callsign,
                database_id=database_id,
                error=str(e),
            )
            return None

    def create_company_page(self, database_id: str, company: Company) -> NotionPage:
        """
        Create new company page.

        Args:
            database_id: Companies database ID
            company: Company data

        Returns:
            Created Notion page
        """
        if self.dry_run:
            logger.info(
                "DRY RUN: Would create company page",
                callsign=company.callsign,
                database_id=database_id,
            )
            return NotionPage(
                page_id="dry_run_page_id",
                database_id=database_id,
                title=company.callsign,
                created=True,
            )

        schema = self.get_database_schema(database_id)
        title_prop = self.get_title_property_name(schema)

        # Build properties based on schema
        properties = {title_prop: self._create_title_segments(company.callsign)}

        # Add optional properties if they exist in schema
        if company.dba and self.property_exists(schema, "Company", "rich_text"):
            properties["Company"] = self._create_rich_text_segments(company.dba)

        if company.website and self.property_exists(schema, "Website", "url"):
            properties["Website"] = {"url": company.website}

        if company.domain_root:
            if self.property_exists(schema, "Domain", "url"):
                domain_url = company.domain_root
                if not domain_url.startswith("http"):
                    domain_url = f"https://{domain_url}"
                properties["Domain"] = {"url": domain_url}
            elif self.property_exists(schema, "Domain", "rich_text"):
                properties["Domain"] = self._create_rich_text_segments(company.domain_root)

        if company.beneficial_owners and self.property_exists(schema, "Owners", "rich_text"):
            owners_text = ", ".join(company.beneficial_owners)
            properties["Owners"] = self._create_rich_text_segments(owners_text)

        if self.property_exists(schema, "Needs Dossier", "checkbox"):
            properties["Needs Dossier"] = {"checkbox": company.needs_dossier}

        # Create page
        page_data = {"parent": {"database_id": database_id}, "properties": properties}

        response = self._make_request("POST", "/pages", json_data=page_data)

        logger.info("Company page created", callsign=company.callsign, page_id=response["id"])

        return NotionPage(
            page_id=response["id"],
            database_id=database_id,
            title=company.callsign,
            properties=properties,
            created=True,
        )

    def update_company_page(
        self, page_id: str, company: Company, database_id: Optional[str] = None
    ) -> NotionPage:
        """
        Update existing company page.

        Args:
            page_id: Notion page ID
            company: Company data
            database_id: Database ID for schema lookup (optional)

        Returns:
            Updated Notion page
        """
        if self.dry_run:
            logger.info(
                "DRY RUN: Would update company page", page_id=page_id, callsign=company.callsign
            )
            return NotionPage(
                page_id=page_id,
                database_id=database_id or "unknown",
                title=company.callsign,
                updated=True,
            )

        # Get schema for validation if database_id provided
        properties = {}

        if database_id:
            schema = self.get_database_schema(database_id)

            # Only update properties that exist in schema
            if company.dba and self.property_exists(schema, "Company", "rich_text"):
                properties["Company"] = self._create_rich_text_segments(company.dba)

            if company.website and self.property_exists(schema, "Website", "url"):
                properties["Website"] = {"url": company.website}

            if company.domain_root:
                if self.property_exists(schema, "Domain", "url"):
                    domain_url = company.domain_root
                    if not domain_url.startswith("http"):
                        domain_url = f"https://{domain_url}"
                    properties["Domain"] = {"url": domain_url}
                elif self.property_exists(schema, "Domain", "rich_text"):
                    properties["Domain"] = self._create_rich_text_segments(company.domain_root)

        if properties:
            update_data = {"properties": properties}
            self._make_request("PATCH", f"/pages/{page_id}", json_data=update_data)

            logger.info(
                "Company page updated",
                page_id=page_id,
                callsign=company.callsign,
                updated_properties=list(properties.keys()),
            )

        return NotionPage(
            page_id=page_id,
            database_id=database_id or "unknown",
            title=company.callsign,
            properties=properties,
            updated=bool(properties),
        )

    def upsert_company_page(self, database_id: str, company: Company) -> NotionPage:
        """
        Create or update company page.

        Args:
            database_id: Companies database ID
            company: Company data

        Returns:
            Upserted Notion page
        """
        # Try to find existing page
        existing_page_id = self.find_company_page(database_id, company.callsign)

        if existing_page_id:
            return self.update_company_page(existing_page_id, company, database_id)
        else:
            return self.create_company_page(database_id, company)

    def set_needs_dossier(self, page_id: str, needs: bool = True) -> None:
        """
        Set the 'Needs Dossier' flag on a company page.

        Args:
            page_id: Notion page ID
            needs: Whether dossier is needed
        """
        if self.dry_run:
            logger.info("DRY RUN: Would set needs dossier", page_id=page_id, needs=needs)
            return

        update_data = {"properties": {"Needs Dossier": {"checkbox": needs}}}

        self._make_request("PATCH", f"/pages/{page_id}", json_data=update_data)

        logger.info("Needs dossier flag updated", page_id=page_id, needs=needs)

    def set_latest_intel(
        self,
        page_id: str,
        summary_text: str,
        date_iso: Optional[str] = None,
        database_id: Optional[str] = None,
    ) -> None:
        """
        Set latest intelligence summary on company page.

        Args:
            page_id: Company page ID
            summary_text: Intelligence summary
            date_iso: Date string in ISO format
            database_id: Database ID for schema validation
        """
        if self.dry_run:
            logger.info(
                "DRY RUN: Would set latest intel",
                page_id=page_id,
                summary_length=len(summary_text),
                date_iso=date_iso,
            )
            return

        properties = {}

        # Validate schema if database_id provided
        if database_id:
            schema = self.get_database_schema(database_id)

            if self.property_exists(schema, "Latest Intel", "rich_text"):
                properties["Latest Intel"] = self._create_rich_text_segments(summary_text)

            if self.property_exists(schema, "Last Intel At", "date"):
                properties["Last Intel At"] = self._create_date_property(date_iso)
        else:
            # Assume standard schema
            properties = {
                "Latest Intel": self._create_rich_text_segments(summary_text),
                "Last Intel At": self._create_date_property(date_iso),
            }

        if properties:
            update_data = {"properties": properties}
            self._make_request("PATCH", f"/pages/{page_id}", json_data=update_data)

            logger.info("Latest intel updated", page_id=page_id, summary_length=len(summary_text))

    def get_company_domain_data(self, database_id: str, callsign: str) -> Dict[str, Optional[str]]:
        """
        Get domain and website data for a company.

        Args:
            database_id: Companies database ID
            callsign: Company callsign

        Returns:
            Dict with domain and website data
        """
        try:
            page_id = self.find_company_page(database_id, callsign)
            if not page_id:
                return {"domain": None, "website": None}

            # Get page properties
            page_data = self._make_request("GET", f"/pages/{page_id}")
            properties = page_data.get("properties", {})

            # Extract domain
            domain = None
            domain_prop = properties.get("Domain", {})
            if domain_prop.get("type") == "url":
                url = domain_prop.get("url")
                if url:
                    # Clean domain from URL
                    domain = url.replace("https://", "").replace("http://", "").split("/")[0]
            elif domain_prop.get("type") == "rich_text":
                rich_text = domain_prop.get("rich_text", [])
                if rich_text and rich_text[0].get("text"):
                    domain = rich_text[0]["text"].get("content", "").strip()

            # Extract website
            website = None
            website_prop = properties.get("Website", {})
            if website_prop.get("type") == "url":
                website = website_prop.get("url")

            return {"domain": domain, "website": website}

        except Exception as e:
            logger.error("Failed to get company domain data", callsign=callsign, error=str(e))
            return {"domain": None, "website": None}

    def get_all_companies_domain_data(
        self, database_id: str, callsigns: List[str]
    ) -> Dict[str, Dict[str, Optional[str]]]:
        """
        Get domain data for multiple companies efficiently.

        Args:
            database_id: Companies database ID
            callsigns: List of callsigns to lookup

        Returns:
            Dict mapping callsign to domain data
        """
        try:
            # Get all pages from database (paginated)
            all_results = []
            has_more = True
            start_cursor = None

            while has_more:
                response = self.query_database(
                    database_id, page_size=100, start_cursor=start_cursor
                )

                results = response.get("results", [])
                all_results.extend(results)

                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")

            # Extract domain data for requested callsigns
            domain_data = {}
            callsigns_set = {cs.lower() for cs in callsigns}

            schema = self.get_database_schema(database_id)
            title_prop = self.get_title_property_name(schema)

            for page in all_results:
                properties = page.get("properties", {})

                # Get callsign from title
                title_data = properties.get(title_prop, {})
                title_array = title_data.get("title", [])
                if not title_array:
                    continue

                callsign = title_array[0].get("text", {}).get("content", "").strip().lower()
                if callsign not in callsigns_set:
                    continue

                # Extract domain and website
                domain = None
                website = None

                domain_prop = properties.get("Domain", {})
                if domain_prop.get("type") == "url":
                    url = domain_prop.get("url")
                    if url:
                        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
                elif domain_prop.get("type") == "rich_text":
                    rich_text = domain_prop.get("rich_text", [])
                    if rich_text and rich_text[0].get("text"):
                        domain = rich_text[0]["text"].get("content", "").strip()

                website_prop = properties.get("Website", {})
                if website_prop.get("type") == "url":
                    website = website_prop.get("url")

                domain_data[callsign] = {"domain": domain, "website": website}

            # Fill in missing callsigns
            for cs in callsigns:
                if cs.lower() not in domain_data:
                    domain_data[cs.lower()] = {"domain": None, "website": None}

            logger.info(
                "Company domain data retrieved",
                requested=len(callsigns),
                found=len([d for d in domain_data.values() if d.get("domain") or d.get("website")]),
            )

            return domain_data

        except Exception as e:
            logger.error(
                "Failed to get companies domain data", database_id=database_id, error=str(e)
            )
            # Return empty data for all callsigns
            return {cs.lower(): {"domain": None, "website": None} for cs in callsigns}

    def health_check(self) -> Dict[str, Any]:
        """
        Perform health check on Notion API.

        Returns:
            Health status information
        """
        try:
            # Try to get user info as a simple health check
            response = self._make_request("GET", "/users/me")

            return {
                "status": "healthy",
                "user_id": response.get("id"),
                "user_type": response.get("type"),
                "name": response.get("name"),
            }

        except Exception as e:
            return {"status": "unhealthy", "error": str(e), "error_type": type(e).__name__}

    def create_report_page(
        self,
        database_id: str,
        title: str,
        report_type: str,
        content_markdown: Optional[str] = None,
        content_html: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Create a new report page in Notion.

        Args:
            database_id: Reports database ID
            title: Report title
            report_type: Type of report (e.g., "company_deepdive", "weekly_news", "new_clients")
            content_markdown: Markdown content
            content_html: HTML content
            metadata: Additional metadata dictionary

        Returns:
            Page ID if successful, None otherwise
        """
        if self.dry_run:
            logger.info(
                "DRY RUN: Would create report page",
                title=title,
                report_type=report_type,
                database_id=database_id,
            )
            return f"dry_run_report_{report_type}_{int(datetime.now().timestamp())}"

        try:
            schema = self.get_database_schema(database_id)
            title_prop = self.get_title_property_name(schema)

            # Build basic properties
            properties = {title_prop: self._create_title_segments(title)}

            # Add report type
            if self.property_exists(schema, "Report Type", "select"):
                properties["Report Type"] = {"select": {"name": report_type}}
            elif self.property_exists(schema, "Type", "select"):
                properties["Type"] = {"select": {"name": report_type}}
            elif self.property_exists(schema, "Report Type", "rich_text"):
                properties["Report Type"] = self._create_rich_text_segments(report_type)
            elif self.property_exists(schema, "Type", "rich_text"):
                properties["Type"] = self._create_rich_text_segments(report_type)

            # Add generated date
            if self.property_exists(schema, "Generated", "date"):
                properties["Generated"] = self._create_date_property(datetime.now().isoformat())
            elif self.property_exists(schema, "Created", "date"):
                properties["Created"] = self._create_date_property(datetime.now().isoformat())

            # Add status
            if self.property_exists(schema, "Status", "select"):
                properties["Status"] = {"select": {"name": "Generated"}}

            # Add metadata as properties if they exist in schema
            if metadata:
                for key, value in metadata.items():
                    if isinstance(value, str):
                        if self.property_exists(schema, key, "rich_text"):
                            properties[key] = self._create_rich_text_segments(value)
                        elif self.property_exists(schema, key, "title"):
                            properties[key] = self._create_title_segments(value)
                    elif isinstance(value, (int, float)):
                        if self.property_exists(schema, key, "number"):
                            properties[key] = {"number": value}
                    elif isinstance(value, bool):
                        if self.property_exists(schema, key, "checkbox"):
                            properties[key] = {"checkbox": value}
                    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                        if self.property_exists(schema, key, "multi_select"):
                            properties[key] = {"multi_select": [{"name": item} for item in value]}

            # Create page
            page_data = {"parent": {"database_id": database_id}, "properties": properties}

            # Add content as children if provided
            children = []
            if content_markdown:
                # Convert markdown to Notion blocks (simplified)
                lines = content_markdown.split("\n")
                for line in lines:
                    if line.strip():
                        if line.startswith("# "):
                            children.append(
                                {
                                    "object": "block",
                                    "type": "heading_1",
                                    "heading_1": {
                                        "rich_text": [
                                            {"type": "text", "text": {"content": line[2:].strip()}}
                                        ]
                                    },
                                }
                            )
                        elif line.startswith("## "):
                            children.append(
                                {
                                    "object": "block",
                                    "type": "heading_2",
                                    "heading_2": {
                                        "rich_text": [
                                            {"type": "text", "text": {"content": line[3:].strip()}}
                                        ]
                                    },
                                }
                            )
                        elif line.startswith("### "):
                            children.append(
                                {
                                    "object": "block",
                                    "type": "heading_3",
                                    "heading_3": {
                                        "rich_text": [
                                            {"type": "text", "text": {"content": line[4:].strip()}}
                                        ]
                                    },
                                }
                            )
                        elif line.startswith("- ") or line.startswith("* "):
                            children.append(
                                {
                                    "object": "block",
                                    "type": "bulleted_list_item",
                                    "bulleted_list_item": {
                                        "rich_text": [
                                            {"type": "text", "text": {"content": line[2:].strip()}}
                                        ]
                                    },
                                }
                            )
                        else:
                            # Regular paragraph
                            children.append(
                                {
                                    "object": "block",
                                    "type": "paragraph",
                                    "paragraph": {
                                        "rich_text": [{"type": "text", "text": {"content": line}}]
                                    },
                                }
                            )

            if children:
                page_data["children"] = children[:100]  # Notion has a limit

            response = self._make_request("POST", "/pages", json_data=page_data)

            page_id = response.get("id")

            logger.info(
                "Report page created in Notion",
                page_id=page_id,
                title=title,
                report_type=report_type,
                database_id=database_id,
            )

            return page_id

        except Exception as e:
            logger.error(
                "Failed to create report page",
                title=title,
                report_type=report_type,
                database_id=database_id,
                error=str(e),
            )
            return None

    @with_circuit_breaker(
        name="notion_intel_query",
        failure_threshold=3,
        recovery_timeout=30.0,
        expected_exception=NotionError,
    )
    @with_retry(max_attempts=3, retry_exceptions=(HTTPStatusError, TimeoutException))
    def get_intel_archive_for_company(
        self,
        database_id: str,
        callsign: str,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
    ) -> List[Dict[str, Any]]:
        """
        Get intelligence archive data for a specific company from Notion.

        Args:
            database_id: Intelligence database ID
            callsign: Company callsign to filter by
            start_date: Start date for filtering
            end_date: End date for filtering

        Returns:
            List of intelligence items as dictionaries
        """
        if self.dry_run:
            logger.info(
                "DRY RUN: Would query intel archive", database_id=database_id, callsign=callsign
            )
            return []

        try:
            logger.debug("Querying intel archive", database_id=database_id, callsign=callsign)

            # Build the filter for callsign
            filter_conditions = {
                "and": [
                    {"property": "Callsign", "rich_text": {"equals": callsign}},
                    {"property": "Last Updated", "date": {"on_or_after": start_date.isoformat()}},
                    {"property": "Last Updated", "date": {"on_or_before": end_date.isoformat()}},
                ]
            }

            response = self.query_database(database_id, filter_conditions)
            pages = response.get("results", [])

            intel_items = []
            for page in pages:
                # Get basic page info
                page_id = page.get("id")

                # Get the page content to extract news items
                try:
                    # Get page content blocks
                    blocks_response = self._make_request("GET", f"/blocks/{page_id}/children")
                    blocks = blocks_response.get("results", [])

                    # Parse blocks to extract news items
                    # This is a simplified version - adjust based on actual content structure
                    for block in blocks:
                        if block.get("type") == "toggle":
                            toggle_data = block.get("toggle", {})
                            rich_text = toggle_data.get("rich_text", [])
                            if rich_text:
                                title = "".join([t.get("plain_text", "") for t in rich_text])

                                # Try to extract structured data from toggle content
                                intel_items.append(
                                    {
                                        "title": title,
                                        "url": "",  # You may need to extract this from the content
                                        "source": "Notion Intel Archive",
                                        "published_at": start_date.isoformat(),  # Use actual date
                                        "summary": title,  # Use title as summary for now
                                        "relevance_score": 0.8,
                                        "sentiment": "neutral",
                                    }
                                )

                except Exception as block_error:
                    logger.warning(
                        "Failed to get page content", page_id=page_id, error=str(block_error)
                    )
                    continue

            logger.info(
                "Intel archive query completed",
                database_id=database_id,
                callsign=callsign,
                items_found=len(intel_items),
            )

            return intel_items

        except Exception as e:
            logger.error(
                "Failed to query intel archive",
                database_id=database_id,
                callsign=callsign,
                error=str(e),
            )
            raise NotionError(f"Failed to query intelligence archive: {e}")


def create_notion_client(
    config: NotionConfig, rate_limiter: Optional[AdaptiveRateLimiter] = None, dry_run: bool = False
) -> EnhancedNotionClient:
    """
    Factory function to create Notion client.

    Args:
        config: Notion configuration
        rate_limiter: Optional rate limiter instance
        dry_run: Whether to run in dry-run mode

    Returns:
        Configured Notion client
    """
    return EnhancedNotionClient(config, rate_limiter, dry_run)
