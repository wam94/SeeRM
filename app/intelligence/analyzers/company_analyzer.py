"""Company analysis functionality for deep dive reports."""

from datetime import datetime, timedelta
from typing import Any, Dict

import structlog

from ..models import CompanyIntelligence

logger = structlog.get_logger(__name__)


class CompanyAnalyzer:
    """Analyzes company data for deep dive reports."""

    def analyze_metrics_trend(self, intelligence: CompanyIntelligence) -> Dict[str, Any]:
        """
        Analyze 30-day metrics for a company.

        Args:
            intelligence: Company intelligence data

        Returns:
            Dict with metrics data
        """
        movement = intelligence.movement
        if not movement:
            return {"analysis": "No movement data available"}

        analysis = {
            "current_balance": movement.current_balance,
            "percentage_change": movement.percentage_change,
            "movement_type": movement.movement_type.value,
            "rank_position": movement.rank,
            "is_new_account": movement.is_new_account,
        }

        return analysis

    def generate_executive_summary(self, intelligence: CompanyIntelligence) -> str:
        """
        Generate factual summary for company.

        Args:
            intelligence: Company intelligence data

        Returns:
            Factual summary text
        """
        profile = intelligence.profile
        movement = intelligence.movement
        news_count = len(intelligence.news_history)

        # Build summary components
        company_desc = f"{profile.company_name} ({profile.callsign})"

        # Movement summary
        movement_desc = "No recent activity"
        if movement:
            if movement.is_new_account:
                movement_desc = f"New account with ${movement.current_balance:,.2f} balance"
            elif movement.percentage_change is not None:
                direction = "increased" if movement.percentage_change > 0 else "decreased"
                delta = abs(movement.percentage_change)
                movement_desc = f"Account balance {direction} by {delta:.1f}%"

        # News summary
        news_desc = (
            f"{news_count} news items in past 90 days" if news_count > 0 else "No recent news"
        )

        summary_parts = [company_desc, movement_desc, news_desc]

        summary = " • ".join(summary_parts)

        logger.debug(
            "Executive summary generated",
            callsign=profile.callsign,
            length=len(summary),
        )
        return summary

    def analyze_product_usage(self, intelligence: CompanyIntelligence) -> Dict[str, Any]:
        """
        Analyze product usage data.

        Args:
            intelligence: Company intelligence data

        Returns:
            Product usage data
        """
        movement = intelligence.movement
        profile = intelligence.profile

        # Get products from movement data (more recent) or profile
        products = []
        if movement and movement.products:
            products = movement.products
        elif profile.products:
            products = profile.products

        analysis = {"active_products": products, "product_count": len(products)}

        if not products:
            analysis["status"] = "No product usage data available"
        else:
            analysis["status"] = f"Using {len(products)} product(s): {', '.join(products)}"

        return analysis

    def _is_recent(self, date_string: str, days: int = 30) -> bool:
        """Check if date is within recent timeframe."""
        try:
            # Handle various date formats
            if date_string.endswith("Z"):
                date_string = date_string[:-1] + "+00:00"

            date = datetime.fromisoformat(date_string)
            cutoff = datetime.now() - timedelta(days=days)
            return date > cutoff
        except Exception:
            return False
