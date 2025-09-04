"""
Company Deep Dive Report Generator

Creates comprehensive intelligence reports for individual companies
combining CSV metrics, Notion data, and news analysis.
"""

from datetime import datetime
from typing import Dict, Any, Optional
import structlog

from app.core.config import Settings
from app.intelligence.data_aggregator import IntelligenceAggregator
from app.intelligence.analyzers import CompanyAnalyzer, NewsAnalyzer
from app.intelligence.models import Report, ReportMetadata, CompanyDeepDive
from app.data.notion_client import EnhancedNotionClient
from app.data.gmail_client import EnhancedGmailClient
from app.utils.reliability import track_performance

logger = structlog.get_logger(__name__)


class CompanyDeepDiveReport:
    """
    Generates comprehensive deep dive reports for companies.
    
    Combines movement data, Notion intelligence, news analysis,
    and risk assessment into actionable business reports.
    """
    
    def __init__(
        self,
        aggregator: IntelligenceAggregator,
        notion_client: Optional[EnhancedNotionClient] = None,
        settings: Optional[Settings] = None
    ):
        self.aggregator = aggregator
        self.notion_client = notion_client
        self.settings = settings or Settings()
        self.company_analyzer = CompanyAnalyzer()
        self.news_analyzer = NewsAnalyzer()
        
        logger.info("Company deep dive report generator initialized")
    
    @track_performance("generate_company_report")
    def generate(self, callsign: str, include_email: bool = True) -> Report:
        """
        Generate comprehensive deep dive report for a company.
        
        Args:
            callsign: Company callsign to analyze
            include_email: Whether to send email with report
            
        Returns:
            Report object with complete analysis
        """
        start_time = datetime.utcnow()
        report_id = f"deepdive_{callsign}_{start_time.strftime('%Y%m%d_%H%M%S')}"
        
        logger.info("Generating company deep dive", 
                   callsign=callsign, report_id=report_id)
        
        try:
            # Step 1: Gather intelligence data
            intelligence = self.aggregator.get_company_360(callsign)
            
            # Step 2: Analyze components
            executive_summary = self.company_analyzer.generate_executive_summary(intelligence)
            metrics_analysis = self.company_analyzer.analyze_metrics_trend(intelligence)
            product_analysis = self.company_analyzer.analyze_product_usage(intelligence)
            
            # Step 3: News analysis
            news_timeline = intelligence.news_history
            
            # Step 4: Create deep dive structure
            deepdive = CompanyDeepDive(
                company=intelligence,
                executive_summary=executive_summary,
                metrics_analysis=metrics_analysis,
                news_timeline=news_timeline,
                product_analysis=product_analysis
            )
            
            # Step 5: Create report
            content = self._structure_report_content(deepdive)
            html = self._render_html_report(deepdive)
            markdown = self._render_markdown_report(deepdive)
            
            # Create metadata
            metadata = ReportMetadata(
                report_id=report_id,
                report_type="company_deepdive",
                generated_at=start_time,
                data_sources=["csv", "notion", "gmail"],
                parameters={"callsign": callsign},
                duration_seconds=(datetime.utcnow() - start_time).total_seconds()
            )
            
            # Create final report
            report = Report(
                metadata=metadata,
                title=f"Company Deep Dive: {intelligence.profile.company_name}",
                content=content,
                html=html,
                markdown=markdown
            )
            
            # Step 6: Deliver report
            if include_email:
                self._send_email_report(report, deepdive)
            
            if self.notion_client:
                report.notion_page_id = self._create_notion_report(report, deepdive)
            
            logger.info("Company deep dive completed",
                       callsign=callsign,
                       report_id=report_id,
                       duration=metadata.duration_seconds)
            
            return report
            
        except Exception as e:
            logger.error("Failed to generate company deep dive",
                        callsign=callsign, error=str(e))
            raise
    
    def _structure_report_content(self, deepdive: CompanyDeepDive) -> Dict[str, Any]:
        """Structure the report content data."""
        return {
            "company_info": {
                "callsign": deepdive.company.profile.callsign,
                "name": deepdive.company.profile.company_name,
                "website": deepdive.company.profile.website,
                "products": deepdive.company.profile.products,
                "notion_page": deepdive.company.profile.notion_page_id
            },
            "executive_summary": deepdive.executive_summary,
            "metrics": deepdive.metrics_analysis,
            "movement": {
                "current_balance": deepdive.company.movement.current_balance if deepdive.company.movement else None,
                "percentage_change": deepdive.company.movement.percentage_change if deepdive.company.movement else None,
                "movement_type": deepdive.company.movement.movement_type.value if deepdive.company.movement else None,
                "is_new_account": deepdive.company.movement.is_new_account if deepdive.company.movement else False
            },
            "news_summary": {
                "total_items": len(deepdive.news_timeline),
                "recent_items": len([n for n in deepdive.news_timeline 
                                   if self._is_recent(n.published_at, 30)]),
                "by_type": self._categorize_news_for_summary(deepdive.news_timeline)
            },
            "products": deepdive.product_analysis
        }
    
    def _render_html_report(self, deepdive: CompanyDeepDive) -> str:
        """Render HTML version of the report."""
        company = deepdive.company
        movement = company.movement
        
        html_parts = [
            f'<h1>Company Deep Dive: {company.profile.company_name}</h1>',
            f'<p><strong>Callsign:</strong> {company.profile.callsign}</p>',
            f'<p><strong>Generated:</strong> {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}</p>',
            '<hr>',
            
            f'<h2>Executive Summary</h2>',
            f'<p>{deepdive.executive_summary}</p>',
            
            '<h2>Current Metrics</h2>',
        ]
        
        if movement:
            html_parts.extend([
                f'<ul>',
                f'<li><strong>Current Balance:</strong> ${movement.current_balance:,.2f}</li>',
                f'<li><strong>Change:</strong> {movement.percentage_change:+.1f}% ({movement.movement_type.value.replace("_", " ").title()})</li>',
                f'<li><strong>Account Type:</strong> {"New Account" if movement.is_new_account else "Existing Account"}</li>',
                '</ul>'
            ])
        
        
        # News Timeline
        if deepdive.news_timeline:
            html_parts.extend([
                '<h2>Recent News (90 days)</h2>',
                '<ul>'
            ])
            for news_item in deepdive.news_timeline[:5]:  # Top 5 recent items
                html_parts.append(f'<li><strong>{news_item.title}</strong><br>')
                html_parts.append(f'Source: {news_item.source} | Date: {news_item.published_at}<br>')
                if news_item.url:
                    html_parts.append(f'<a href="{news_item.url}">Read more</a>')
                html_parts.append('</li>')
            html_parts.append('</ul>')
        
        
        # Footer
        html_parts.extend([
            '<p><small>Generated by SeeRM Intelligence Reports</small></p>'
        ])
        
        return '\n'.join(html_parts)
    
    def _render_markdown_report(self, deepdive: CompanyDeepDive) -> str:
        """Render Markdown version of the report."""
        company = deepdive.company
        movement = company.movement
        
        md_parts = [
            f'# Company Deep Dive: {company.profile.company_name}',
            f'**Callsign:** {company.profile.callsign}',
            f'**Generated:** {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}',
            '',
            '## Executive Summary',
            deepdive.executive_summary,
            '',
            '## Current Metrics'
        ]
        
        if movement:
            md_parts.extend([
                f'- **Current Balance:** ${movement.current_balance:,.2f}',
                f'- **Change:** {movement.percentage_change:+.1f}% ({movement.movement_type.value.replace("_", " ").title()})',
                f'- **Account Type:** {"New Account" if movement.is_new_account else "Existing Account"}',
                ''
            ])
        
        
        # News Timeline
        if deepdive.news_timeline:
            md_parts.extend([
                '## Recent News (90 days)',
                ''
            ])
            for news_item in deepdive.news_timeline[:5]:
                md_parts.append(f'### {news_item.title}')
                md_parts.append(f'**Source:** {news_item.source} | **Date:** {news_item.published_at}')
                if news_item.url:
                    md_parts.append(f'[Read more]({news_item.url})')
                md_parts.append('')
        
        
        md_parts.extend(['', '_Generated by SeeRM Intelligence Reports_'])
        
        return '\n'.join(md_parts)
    
    def _send_email_report(self, report: Report, deepdive: CompanyDeepDive):
        """Send report via email."""
        try:
            if not self.aggregator.gmail_client:
                logger.warning("Gmail client not available for email delivery")
                return
            
            subject = f"Company Deep Dive: {deepdive.company.profile.company_name}"
            
            # Send HTML email
            response = self.aggregator.gmail_client.send_html_email(
                to=self.settings.gmail.user,  # Send to self
                subject=subject,
                html=report.html or "Report generated successfully"
            )
            
            report.email_sent = True
            logger.info("Deep dive report emailed",
                       callsign=deepdive.company.profile.callsign,
                       message_id=response.get("id"))
            
        except Exception as e:
            logger.error("Failed to send report email", error=str(e))
    
    def _create_notion_report(self, report: Report, deepdive: CompanyDeepDive) -> Optional[str]:
        """Create report page in Notion."""
        try:
            if not self.notion_client or not self.settings.notion.reports_db_id:
                logger.debug("Notion not configured for report storage")
                return None
            
            # Prepare metadata for the report
            metadata = {
                "Callsign": deepdive.company.profile.callsign,
                "Company Name": deepdive.company.profile.company_name,
                "Duration": f"{report.metadata.duration_seconds:.1f}s"
            }
            
            # Add movement data if available
            if deepdive.company.movement:
                metadata["Current Balance"] = deepdive.company.movement.current_balance
                metadata["Percentage Change"] = deepdive.company.movement.percentage_change
                metadata["Movement Type"] = deepdive.company.movement.movement_type.value
            
            # Add news count
            metadata["News Items"] = len(deepdive.news_timeline)
            
            # Create the report page
            page_id = self.notion_client.create_report_page(
                database_id=self.settings.notion.reports_db_id,
                title=report.title,
                report_type="company_deepdive",
                content_markdown=report.markdown,
                metadata=metadata
            )
            
            if page_id:
                logger.info("Company deep dive report created in Notion",
                           callsign=deepdive.company.profile.callsign,
                           page_id=page_id,
                           report_id=report.metadata.report_id)
            
            return page_id
            
        except Exception as e:
            logger.error("Failed to create Notion report", 
                        callsign=deepdive.company.profile.callsign,
                        error=str(e))
            return None
    
    def _is_recent(self, date_string: str, days: int) -> bool:
        """Check if date is recent."""
        try:
            if date_string.endswith('Z'):
                date_string = date_string[:-1] + '+00:00'
            date = datetime.fromisoformat(date_string)
            return (datetime.now() - date).days <= days
        except:
            return False
    
    def _categorize_news_for_summary(self, news_items) -> Dict[str, int]:
        """Get news counts by category."""
        counts = {}
        categorized = self.news_analyzer.categorize_news(news_items)
        
        for news_type, items in categorized.items():
            counts[news_type.value] = len(items)
        
        return counts
    
