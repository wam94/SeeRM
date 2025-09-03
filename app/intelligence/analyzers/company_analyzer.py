"""
Company analysis functionality for deep dive reports.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import structlog

from ..models import CompanyIntelligence, Movement, NewsItem, RiskLevel

logger = structlog.get_logger(__name__)


class CompanyAnalyzer:
    """Analyzes company data for deep dive reports."""
    
    def analyze_metrics_trend(self, intelligence: CompanyIntelligence) -> Dict[str, Any]:
        """
        Analyze 30-day metrics trend for a company.
        
        Args:
            intelligence: Company intelligence data
            
        Returns:
            Dict with trend analysis
        """
        movement = intelligence.movement
        if not movement:
            return {"trend": "insufficient_data", "analysis": "No movement data available"}
        
        analysis = {
            "current_balance": movement.current_balance,
            "percentage_change": movement.percentage_change,
            "movement_type": movement.movement_type.value,
            "rank_position": movement.rank,
            "trend": "stable"
        }
        
        # Determine overall trend
        if movement.percentage_change is not None:
            if movement.percentage_change > 20:
                analysis["trend"] = "strong_growth"
            elif movement.percentage_change > 5:
                analysis["trend"] = "moderate_growth" 
            elif movement.percentage_change < -20:
                analysis["trend"] = "declining"
            elif movement.percentage_change < -5:
                analysis["trend"] = "moderate_decline"
        
        # Add context
        if movement.is_new_account:
            analysis["context"] = "New account - establishing baseline metrics"
        elif movement.percentage_change and abs(movement.percentage_change) > 30:
            analysis["context"] = "Significant movement requiring attention"
        else:
            analysis["context"] = "Normal account activity"
        
        return analysis
    
    def generate_executive_summary(self, intelligence: CompanyIntelligence) -> str:
        """
        Generate executive summary for company.
        
        Args:
            intelligence: Company intelligence data
            
        Returns:
            Executive summary text
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
            elif movement.percentage_change:
                direction = "increased" if movement.percentage_change > 0 else "decreased"
                movement_desc = f"Account balance {direction} by {abs(movement.percentage_change):.1f}%"
        
        # News summary
        news_desc = f"{news_count} news items in past 90 days" if news_count > 0 else "No recent news"
        
        # Risk assessment
        risk_desc = f"Risk Level: {intelligence.risk_level.value.title()}"
        
        summary_parts = [
            company_desc,
            movement_desc,
            news_desc,
            risk_desc
        ]
        
        if intelligence.opportunities:
            summary_parts.append(f"Opportunities: {len(intelligence.opportunities)} identified")
        
        summary = " • ".join(summary_parts)
        
        logger.debug("Executive summary generated", callsign=profile.callsign, length=len(summary))
        return summary
    
    def analyze_product_usage(self, intelligence: CompanyIntelligence) -> Dict[str, Any]:
        """
        Analyze product usage patterns.
        
        Args:
            intelligence: Company intelligence data
            
        Returns:
            Product usage analysis
        """
        movement = intelligence.movement
        profile = intelligence.profile
        
        # Get products from movement data (more recent) or profile
        products = []
        if movement and movement.products:
            products = movement.products
        elif profile.products:
            products = profile.products
        
        analysis = {
            "active_products": products,
            "product_count": len(products),
            "analysis": []
        }
        
        if not products:
            analysis["analysis"].append("No product usage data available")
            analysis["recommendation"] = "Gather product adoption data for better insights"
        else:
            analysis["analysis"].append(f"Using {len(products)} product(s): {', '.join(products)}")
            
            if len(products) == 1:
                analysis["recommendation"] = "Single product usage - explore cross-sell opportunities"
            elif len(products) > 3:
                analysis["recommendation"] = "High product adoption - strong engagement indicator"
            else:
                analysis["recommendation"] = "Moderate product usage - room for expansion"
        
        return analysis
    
    def calculate_risk_score(self, intelligence: CompanyIntelligence) -> Dict[str, Any]:
        """
        Calculate detailed risk assessment.
        
        Args:
            intelligence: Company intelligence data
            
        Returns:
            Risk assessment details
        """
        risk_factors = intelligence.risk_factors.copy()
        protective_factors = []
        
        # Analyze movement-based risks
        if intelligence.movement:
            movement = intelligence.movement
            if movement.percentage_change and movement.percentage_change < -30:
                risk_factors.append("Major account decline (>30%)")
            elif movement.percentage_change and movement.percentage_change < -10:
                risk_factors.append("Account decline trend")
            
            # Protective factors
            if movement.percentage_change and movement.percentage_change > 20:
                protective_factors.append("Strong account growth")
            if movement.products and len(movement.products) > 2:
                protective_factors.append("Multi-product engagement")
        
        # Analyze news-based risks
        recent_news = [n for n in intelligence.news_history 
                      if self._is_recent(n.published_at, days=30)]
        
        if len(recent_news) == 0:
            risk_factors.append("No recent news or activity")
        else:
            negative_news = [n for n in recent_news if n.sentiment == 'negative']
            if len(negative_news) > len(recent_news) * 0.5:
                risk_factors.append("Predominantly negative news coverage")
        
        # Calculate numeric score
        risk_score = len(risk_factors) * 0.2
        protection_score = len(protective_factors) * 0.1
        final_score = max(0, min(1, risk_score - protection_score))
        
        return {
            "risk_level": intelligence.risk_level.value,
            "risk_score": final_score,
            "risk_factors": risk_factors,
            "protective_factors": protective_factors,
            "assessment": self._get_risk_assessment_text(intelligence.risk_level)
        }
    
    def generate_recommendations(self, intelligence: CompanyIntelligence) -> List[str]:
        """
        Generate actionable recommendations.
        
        Args:
            intelligence: Company intelligence data
            
        Returns:
            List of recommendation strings
        """
        recommendations = []
        movement = intelligence.movement
        risk_level = intelligence.risk_level
        
        # Movement-based recommendations
        if movement:
            if movement.is_new_account:
                recommendations.extend([
                    "Schedule welcome call within 5 business days",
                    "Send onboarding materials and feature guides",
                    "Set up regular check-ins for first 90 days"
                ])
            elif movement.percentage_change and movement.percentage_change > 50:
                recommendations.extend([
                    "Reach out to congratulate on growth",
                    "Explore upsell opportunities for additional services",
                    "Document growth drivers for case study potential"
                ])
            elif movement.percentage_change and movement.percentage_change < -30:
                recommendations.extend([
                    "Schedule immediate check-in call",
                    "Review recent account activity and concerns",
                    "Prepare retention strategy if needed"
                ])
        
        # Risk-based recommendations
        if risk_level == RiskLevel.HIGH or risk_level == RiskLevel.CRITICAL:
            recommendations.extend([
                "Escalate to senior account manager",
                "Conduct comprehensive account health review",
                "Develop intervention plan within 48 hours"
            ])
        elif risk_level == RiskLevel.MEDIUM:
            recommendations.append("Monitor closely and increase touchpoints")
        
        # Opportunity-based recommendations
        for opportunity in intelligence.opportunities:
            if "funding" in opportunity.lower():
                recommendations.append("Explore expansion services following funding")
            elif "partnership" in opportunity.lower():
                recommendations.append("Investigate integration or collaboration needs")
            elif "upsell" in opportunity.lower():
                recommendations.append("Schedule product expansion discussion")
        
        # Default recommendations if none specific
        if not recommendations:
            recommendations.extend([
                "Maintain regular quarterly check-ins",
                "Monitor account metrics for changes",
                "Update product usage and satisfaction data"
            ])
        
        return recommendations[:6]  # Limit to top 6 recommendations
    
    def _is_recent(self, date_string: str, days: int = 30) -> bool:
        """Check if date is within recent timeframe."""
        try:
            # Handle various date formats
            if date_string.endswith('Z'):
                date_string = date_string[:-1] + '+00:00'
            
            date = datetime.fromisoformat(date_string)
            cutoff = datetime.now() - timedelta(days=days)
            return date > cutoff
        except:
            return False
    
    def _get_risk_assessment_text(self, risk_level: RiskLevel) -> str:
        """Get descriptive text for risk level."""
        assessments = {
            RiskLevel.LOW: "Account appears stable with minimal risk indicators",
            RiskLevel.MEDIUM: "Some risk factors present, monitor closely",
            RiskLevel.HIGH: "Multiple risk factors identified, proactive intervention recommended",
            RiskLevel.CRITICAL: "Immediate attention required, high churn risk"
        }
        return assessments.get(risk_level, "Risk assessment unavailable")