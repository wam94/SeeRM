"""
New Client Weekly Summary Report Generator

Creates weekly summaries of new client accounts with intelligence
and onboarding recommendations.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import structlog

from app.core.config import Settings
from app.intelligence.data_aggregator import IntelligenceAggregator
from app.intelligence.models import Report, ReportMetadata, NewClientSummary, MovementType
from app.data.notion_client import EnhancedNotionClient
from app.utils.reliability import track_performance

logger = structlog.get_logger(__name__)


class NewClientReport:
    """
    Generates weekly summaries for new client accounts.
    
    Provides onboarding intelligence, similar client analysis,
    and proactive engagement recommendations.
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
        
        logger.info("New client report generator initialized")
    
    @track_performance("generate_new_client_report")
    def generate(self, new_callsigns: Optional[List[str]] = None, include_email: bool = True) -> Optional[Report]:
        """
        Generate weekly new client summary report.
        
        Args:
            new_callsigns: Optional list of new callsigns. If None, auto-detects from movements
            include_email: Whether to send email with report
            
        Returns:
            Report object or None if no new clients
        """
        start_time = datetime.utcnow()
        week_of = start_time.strftime('%Y-%m-%d')
        report_id = f"new_clients_{week_of}_{start_time.strftime('%H%M%S')}"
        
        logger.info("Generating new client report", 
                   week_of=week_of, report_id=report_id)
        
        try:
            # Step 1: Identify new clients
            if new_callsigns is None:
                new_callsigns = self._identify_new_clients()
            
            if not new_callsigns:
                logger.info("No new clients found for this period")
                return None
            
            logger.info("Processing new clients", count=len(new_callsigns), callsigns=new_callsigns)
            
            # Step 2: Generate summaries for each new client
            client_summaries = []
            for callsign in new_callsigns:
                summary = self._generate_client_summary(callsign)
                if summary:
                    client_summaries.append(summary)
            
            if not client_summaries:
                logger.warning("No valid client summaries generated")
                return None
            
            # Step 3: Create consolidated report content
            content = self._create_consolidated_content(client_summaries, week_of)
            html = self._render_html_report(client_summaries, week_of)
            markdown = self._render_markdown_report(client_summaries, week_of)
            
            # Step 4: Create report metadata
            metadata = ReportMetadata(
                report_id=report_id,
                report_type="new_clients_weekly",
                generated_at=start_time,
                data_sources=["csv", "notion"],
                parameters={"week_of": week_of, "client_count": len(client_summaries)},
                duration_seconds=(datetime.utcnow() - start_time).total_seconds()
            )
            
            # Step 5: Create final report
            report = Report(
                metadata=metadata,
                title=f"New Clients Summary - Week of {week_of}",
                content=content,
                html=html,
                markdown=markdown
            )
            
            # Step 6: Deliver report
            if include_email:
                self._send_email_report(report, client_summaries)
            
            if self.notion_client:
                report.notion_page_id = self._create_notion_report(report, client_summaries)
            
            logger.info("New client report completed",
                       client_count=len(client_summaries),
                       report_id=report_id,
                       duration=metadata.duration_seconds)
            
            return report
            
        except Exception as e:
            logger.error("Failed to generate new client report", error=str(e))
            raise
    
    def _identify_new_clients(self) -> List[str]:
        """Identify new clients from recent movements."""
        movements = self.aggregator.get_latest_movements(days=7)
        new_clients = [
            m.callsign for m in movements 
            if m.movement_type == MovementType.NEW_ACCOUNT or m.is_new_account
        ]
        
        logger.debug("New clients identified", count=len(new_clients))
        return new_clients
    
    def _generate_client_summary(self, callsign: str) -> Optional[NewClientSummary]:
        """Generate summary for a single new client."""
        try:
            # Get full intelligence data
            intelligence = self.aggregator.get_company_360(callsign)
            
            if not intelligence.movement:
                logger.warning("No movement data for new client", callsign=callsign)
                return None
            
            # Get recent news (30 days for new clients)
            recent_news = intelligence.news_history[:5]  # Top 5 recent items
            
            # Find similar clients
            similar_clients = self._find_similar_clients(intelligence)
            
            summary = NewClientSummary(
                callsign=callsign,
                company_name=intelligence.profile.company_name,
                initial_balance=intelligence.movement.current_balance,
                products=intelligence.profile.products or intelligence.movement.products,
                recent_news=recent_news,
                similar_clients=similar_clients
            )
            
            logger.debug("Client summary generated", callsign=callsign)
            return summary
            
        except Exception as e:
            logger.error("Failed to generate client summary", callsign=callsign, error=str(e))
            return None
    
    def _find_similar_clients(self, intelligence) -> List[str]:
        """Find similar existing clients for reference."""
        similar = []
        
        # Simple similarity based on products and size
        try:
            all_movements = self.aggregator.get_latest_movements()
            client_balance = intelligence.movement.current_balance if intelligence.movement else 0
            client_products = set(intelligence.profile.products or [])
            
            candidates = []
            for movement in all_movements:
                if movement.callsign == intelligence.profile.callsign:
                    continue
                
                # Skip if too different in size (more than 10x difference)
                if client_balance > 0 and movement.current_balance > 0:
                    ratio = max(client_balance, movement.current_balance) / min(client_balance, movement.current_balance)
                    if ratio > 10:
                        continue
                
                # Get profile for product comparison
                other_profile = self.aggregator.get_company_profile(movement.callsign)
                if other_profile:
                    other_products = set(other_profile.products or [])
                    
                    # Calculate similarity score
                    product_overlap = len(client_products & other_products)
                    if client_products and other_products:
                        similarity = product_overlap / len(client_products | other_products)
                    else:
                        similarity = 0.0
                    
                    if similarity > 0.3:  # 30% similarity threshold
                        candidates.append((movement.callsign, similarity))
            
            # Sort by similarity and take top 3
            candidates.sort(key=lambda x: x[1], reverse=True)
            similar = [c[0] for c in candidates[:3]]
            
        except Exception as e:
            logger.warning("Failed to find similar clients", error=str(e))
        
        return similar
    
    
    def _create_consolidated_content(self, summaries: List[NewClientSummary], week_of: str) -> Dict[str, Any]:
        """Create consolidated report content."""
        total_value = sum(s.initial_balance for s in summaries)
        
        
        # Product analysis
        all_products = []
        for summary in summaries:
            all_products.extend(summary.products)
        
        from collections import Counter
        product_counts = Counter(all_products)
        
        return {
            "week_of": week_of,
            "summary_stats": {
                "total_new_clients": len(summaries),
                "total_initial_value": total_value,
                "average_initial_value": total_value / len(summaries) if summaries else 0,
            },
            "product_adoption": dict(product_counts.most_common(5)),
            "client_details": [
                {
                    "callsign": s.callsign,
                    "company_name": s.company_name,
                    "initial_balance": s.initial_balance,
                    "products": s.products,
                    "news_count": len(s.recent_news),
                    "similar_clients": s.similar_clients
                }
                for s in summaries
            ]
        }
    
    def _render_html_report(self, summaries: List[NewClientSummary], week_of: str) -> str:
        """Render HTML version of the report."""
        total_value = sum(s.initial_balance for s in summaries)
        
        html_parts = [
            f'<h1>New Clients Summary - Week of {week_of}</h1>',
            f'<p><strong>Generated:</strong> {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}</p>',
            '<hr>',
            
            '<h2>Executive Summary</h2>',
            f'<ul>',
            f'<li><strong>New Clients:</strong> {len(summaries)}</li>',
            f'<li><strong>Total Initial Value:</strong> ${total_value:,.2f}</li>',
            f'<li><strong>Average Value:</strong> ${total_value/len(summaries):,.2f}</li>',
            '</ul>',
            
            '<h2>Client Details</h2>'
        ]
        
        for summary in summaries:
            html_parts.extend([
                f'<h3>{summary.company_name} ({summary.callsign})</h3>',
                f'<ul>',
                f'<li><strong>Initial Balance:</strong> ${summary.initial_balance:,.2f}</li>',
                f'<li><strong>Products:</strong> {", ".join(summary.products) if summary.products else "None specified"}</li>',
                f'<li><strong>Recent News:</strong> {len(summary.recent_news)} items</li>',
                '</ul>'
            ])
            
            if summary.similar_clients:
                html_parts.extend([
                    f'<p><strong>Similar Clients:</strong> {", ".join(summary.similar_clients)}</p>'
                ])
            
            html_parts.append('<hr>')
        
        html_parts.extend([
            '<p><small>Generated by SeeRM Intelligence Reports</small></p>'
        ])
        
        return '\n'.join(html_parts)
    
    def _render_markdown_report(self, summaries: List[NewClientSummary], week_of: str) -> str:
        """Render Markdown version of the report."""
        total_value = sum(s.initial_balance for s in summaries)
        
        md_parts = [
            f'# New Clients Summary - Week of {week_of}',
            f'**Generated:** {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}',
            '',
            '## Executive Summary',
            f'- **New Clients:** {len(summaries)}',
            f'- **Total Initial Value:** ${total_value:,.2f}',
            f'- **Average Value:** ${total_value/len(summaries):,.2f}',
            '',
            '## Client Details'
        ]
        
        for summary in summaries:
            md_parts.extend([
                f'### {summary.company_name} ({summary.callsign})',
                f'- **Initial Balance:** ${summary.initial_balance:,.2f}',
                f'- **Products:** {", ".join(summary.products) if summary.products else "None specified"}',
                f'- **Recent News:** {len(summary.recent_news)} items',
                ''
            ])
            
            if summary.similar_clients:
                md_parts.extend([
                    '',
                    f'**Similar Clients:** {", ".join(summary.similar_clients)}'
                ])
            
            md_parts.extend(['', '---', ''])
        
        md_parts.extend(['_Generated by SeeRM Intelligence Reports_'])
        
        return '\n'.join(md_parts)
    
    def _send_email_report(self, report: Report, summaries: List[NewClientSummary]):
        """Send report via email."""
        try:
            if not self.aggregator.gmail_client:
                logger.warning("Gmail client not available for email delivery")
                return
            
            subject = f"New Clients Summary - {len(summaries)} new accounts this week"
            
            response = self.aggregator.gmail_client.send_html_email(
                to=self.settings.gmail.user,
                subject=subject,
                html=report.html or "New clients report generated successfully"
            )
            
            report.email_sent = True
            logger.info("New client report emailed",
                       client_count=len(summaries),
                       message_id=response.get("id"))
            
        except Exception as e:
            logger.error("Failed to send new client report email", error=str(e))
    
    def _create_notion_report(self, report: Report, summaries: List[NewClientSummary]) -> Optional[str]:
        """Create report page in Notion."""
        try:
            if not self.notion_client or not self.settings.notion.reports_db_id:
                logger.debug("Notion not configured for report storage")
                return None
            
            # Prepare metadata for the report
            total_value = sum(s.initial_balance for s in summaries)
            
            metadata = {
                "Client Count": len(summaries),
                "Total Value": total_value,
                "Average Value": total_value / len(summaries) if summaries else 0,
                "Duration": f"{report.metadata.duration_seconds:.1f}s",
                "Week Of": report.metadata.parameters.get('week_of', ''),
                "Callsigns": [s.callsign for s in summaries]  # Multi-select if supported
            }
            
            # Create the report page
            page_id = self.notion_client.create_report_page(
                database_id=self.settings.notion.reports_db_id,
                title=report.title,
                report_type="new_clients",
                content_markdown=report.markdown,
                metadata=metadata
            )
            
            if page_id:
                logger.info("New client report created in Notion",
                           client_count=len(summaries),
                           page_id=page_id,
                           report_id=report.metadata.report_id)
            
            return page_id
            
        except Exception as e:
            logger.error("Failed to create Notion report", 
                        client_count=len(summaries),
                        error=str(e))
            return None
    
