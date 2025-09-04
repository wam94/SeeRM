"""
Data aggregation layer for SeeRM intelligence system.

Provides unified access to CSV data, Notion intelligence, and other sources
for report generation and analysis.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import pandas as pd
import structlog

from app.core.config import Settings
from app.core.exceptions import ValidationError, WorkflowError
from app.data.csv_parser import CSVProcessor
from app.data.gmail_client import EnhancedGmailClient
from app.data.notion_client import EnhancedNotionClient
from app.utils.reliability import with_retry, track_performance

from .models import (
    CompanyIntelligence, CompanyProfile, Movement, NewsItem, 
    MovementType, NewsType
)

logger = structlog.get_logger(__name__)


class IntelligenceAggregator:
    """
    Unified data aggregation layer for intelligence reports.
    
    Combines data from CSV files, Notion databases, Gmail, and other sources
    to provide comprehensive company intelligence.
    """
    
    def __init__(
        self,
        gmail_client: Optional[EnhancedGmailClient] = None,
        notion_client: Optional[EnhancedNotionClient] = None,
        settings: Optional[Settings] = None
    ):
        self.gmail_client = gmail_client
        self.notion_client = notion_client
        self.settings = settings or Settings()
        self.csv_processor = CSVProcessor(strict_validation=False)
        
        logger.info("Intelligence aggregator initialized",
                   notion_enabled=notion_client is not None)
    
    @track_performance("get_latest_movements")
    @with_retry(max_attempts=3)
    def get_latest_movements(self, days: int = 7) -> List[Movement]:
        """
        Get latest account movements from CSV data.
        
        Args:
            days: Number of days to look back for data
            
        Returns:
            List of Movement objects with current and historical data
        """
        try:
            logger.info("Fetching latest movements", days=days)
            
            # Get CSV data from Gmail if available, otherwise use csv_source_path
            if self.gmail_client:
                df = self.gmail_client.get_latest_csv_from_query(max_messages=5)
                if df is None:
                    logger.warning("No CSV data found in Gmail")
                    return []
            elif self.settings.csv_source_path:
                import pandas as pd
                df = pd.read_csv(self.settings.csv_source_path)
                logger.info("Loaded CSV from file path", path=self.settings.csv_source_path)
            else:
                logger.warning("No CSV data source configured (no Gmail client and no csv_source_path)")
                return []
            
            # Parse into Company objects
            companies = self.csv_processor.parse_companies_csv(df)
            
            # Convert to Movement objects
            movements = []
            for company in companies:
                movement = Movement(
                    callsign=company.callsign,
                    company_name=company.dba or company.callsign,
                    current_balance=company.curr_balance or 0,
                    percentage_change=company.balance_pct_delta_pct,
                    rank=getattr(company, 'rank', None),
                    is_new_account=getattr(company, 'is_new_account', False),
                    products=getattr(company, 'products', [])
                )
                
                # Determine movement type
                if movement.is_new_account:
                    movement.movement_type = MovementType.NEW_ACCOUNT
                elif movement.percentage_change is not None:
                    if movement.percentage_change > 30:
                        movement.movement_type = MovementType.TOP_GAINER
                    elif movement.percentage_change < -30:
                        movement.movement_type = MovementType.TOP_LOSER
                    elif abs(movement.percentage_change) > 10:
                        movement.movement_type = MovementType.SIGNIFICANT_CHANGE
                    else:
                        movement.movement_type = MovementType.STABLE
                
                movements.append(movement)
            
            logger.info("Movements processed",
                       total=len(movements),
                       new_accounts=len([m for m in movements if m.is_new_account]),
                       significant_changes=len([m for m in movements if m.movement_type in [
                           MovementType.TOP_GAINER, MovementType.TOP_LOSER, MovementType.SIGNIFICANT_CHANGE
                       ]]))
            
            return movements
            
        except Exception as e:
            logger.error("Failed to get latest movements", error=str(e))
            raise WorkflowError(f"Failed to fetch movement data: {e}")
    
    @track_performance("get_company_profile")
    @with_retry(max_attempts=3)
    def get_company_profile(self, callsign: str) -> Optional[CompanyProfile]:
        """
        Get company profile from Notion.
        
        Args:
            callsign: Company callsign to lookup
            
        Returns:
            CompanyProfile object or None if not found
        """
        if not self.notion_client or not self.settings.notion.companies_db_id:
            logger.debug("Notion not configured, skipping profile lookup")
            return None
        
        try:
            logger.debug("Fetching company profile", callsign=callsign)
            
            # Get company page from Notion
            companies_data = self.notion_client.get_all_companies_domain_data(
                self.settings.notion.companies_db_id,
                callsigns=[callsign]
            )
            
            if not companies_data or callsign not in companies_data:
                logger.debug("Company not found in Notion", callsign=callsign)
                return None
            
            company_data = companies_data[callsign]
            
            profile = CompanyProfile(
                callsign=callsign,
                company_name=company_data.get('company_name', callsign),
                website=company_data.get('website'),
                domain=company_data.get('domain'),
                owners=company_data.get('owners', []),
                tags=company_data.get('tags', []),
                products=company_data.get('products', []),
                needs_dossier=company_data.get('needs_dossier', False),
                notion_page_id=company_data.get('page_id')
            )
            
            logger.debug("Company profile loaded", callsign=callsign)
            return profile
            
        except Exception as e:
            logger.warning("Failed to get company profile", callsign=callsign, error=str(e))
            return None
    
    @track_performance("get_company_news")
    @with_retry(max_attempts=3)
    def get_company_news(self, callsign: str, days: int = 90) -> List[NewsItem]:
        """
        Get news history for a company from Notion intelligence database.
        
        Args:
            callsign: Company callsign
            days: Number of days of history to retrieve
            
        Returns:
            List of NewsItem objects
        """
        if not self.notion_client or not self.settings.notion.intel_db_id:
            logger.debug("Notion intelligence DB not configured")
            return []
        
        try:
            logger.debug("Fetching company news", callsign=callsign, days=days)
            
            # Calculate date range
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            # Get intelligence data from Notion
            # This would use the existing intel archive functionality
            intel_data = self.notion_client.get_intel_archive_for_company(
                self.settings.notion.intel_db_id,
                callsign,
                start_date,
                end_date
            )
            
            news_items = []
            for item in intel_data:
                news_item = NewsItem(
                    title=item.get('title', 'Untitled'),
                    url=item.get('url', ''),
                    source=item.get('source', ''),
                    published_at=item.get('published_at', ''),
                    summary=item.get('summary'),
                    news_type=self._classify_news_type(item.get('title', '')),
                    relevance_score=item.get('relevance_score', 0.5),
                    sentiment=item.get('sentiment'),
                    company_mentions=[callsign]
                )
                news_items.append(news_item)
            
            logger.debug("Company news loaded", callsign=callsign, count=len(news_items))
            return news_items
            
        except Exception as e:
            logger.warning("Failed to get company news", callsign=callsign, error=str(e))
            return []
    
    @track_performance("get_company_360")
    def get_company_360(self, callsign: str) -> CompanyIntelligence:
        """
        Get complete intelligence profile for a company.
        
        Args:
            callsign: Company callsign
            
        Returns:
            CompanyIntelligence object with all available data
        """
        logger.info("Building 360-degree company intelligence", callsign=callsign)
        
        # Get company profile
        profile = self.get_company_profile(callsign)
        if not profile:
            # Create basic profile from callsign
            profile = CompanyProfile(
                callsign=callsign,
                company_name=callsign
            )
        
        # Get movement data
        movements = self.get_latest_movements()
        movement = next((m for m in movements if m.callsign == callsign), None)
        
        # Get news history
        news_history = self.get_company_news(callsign, days=90)
        
        # Get latest intel
        latest_intel, last_intel_date = self._get_latest_intel(callsign)
        
        intelligence = CompanyIntelligence(
            profile=profile,
            movement=movement,
            news_history=news_history,
            latest_intel=latest_intel,
            last_intel_date=last_intel_date
        )
        
        logger.info("Company intelligence compiled", 
                   callsign=callsign,
                   news_items=len(news_history))
        
        return intelligence
    
    @track_performance("get_portfolio_movements")
    def get_portfolio_movements(self, threshold: float = 10.0) -> Dict[str, List[Movement]]:
        """
        Get significant portfolio movements categorized by type.
        
        Args:
            threshold: Minimum percentage change to be considered significant
            
        Returns:
            Dict categorizing movements by type
        """
        movements = self.get_latest_movements()
        
        categorized = {
            'top_gainers': [],
            'top_losers': [],
            'new_accounts': [],
            'significant_changes': []
        }
        
        for movement in movements:
            if movement.movement_type == MovementType.NEW_ACCOUNT:
                categorized['new_accounts'].append(movement)
            elif movement.movement_type == MovementType.TOP_GAINER:
                categorized['top_gainers'].append(movement)
            elif movement.movement_type == MovementType.TOP_LOSER:
                categorized['top_losers'].append(movement)
            elif movement.movement_type == MovementType.SIGNIFICANT_CHANGE:
                categorized['significant_changes'].append(movement)
        
        return categorized
    
    @track_performance("get_news_stream")
    def get_news_stream(self, days: int = 7) -> List[NewsItem]:
        """
        Get all news across the portfolio for a time period.
        
        Args:
            days: Number of days to look back
            
        Returns:
            List of all NewsItem objects across portfolio
        """
        # Get all companies from movements
        movements = self.get_latest_movements()
        all_news = []
        
        for movement in movements:
            company_news = self.get_company_news(movement.callsign, days=days)
            all_news.extend(company_news)
        
        # Sort by date (most recent first)
        all_news.sort(key=lambda x: x.published_at, reverse=True)
        
        logger.info("News stream compiled", 
                   total_items=len(all_news),
                   days=days,
                   companies=len(movements))
        
        return all_news
    
    def _classify_news_type(self, title: str) -> NewsType:
        """Classify news type based on title keywords."""
        title_lower = title.lower()
        
        if any(word in title_lower for word in ['funding', 'raise', 'series', 'investment', 'investor']):
            return NewsType.FUNDRAISING
        elif any(word in title_lower for word in ['partnership', 'partner', 'collaboration', 'alliance']):
            return NewsType.PARTNERSHIP
        elif any(word in title_lower for word in ['launch', 'release', 'product', 'feature']):
            return NewsType.PRODUCT_LAUNCH
        elif any(word in title_lower for word in ['ceo', 'cto', 'founder', 'executive', 'hire', 'appointment']):
            return NewsType.LEADERSHIP
        elif any(word in title_lower for word in ['acquisition', 'acquire', 'merger', 'bought']):
            return NewsType.ACQUISITION
        elif any(word in title_lower for word in ['announce', 'announcement', 'news']):
            return NewsType.ANNOUNCEMENT
        else:
            return NewsType.OTHER
    
    def _get_latest_intel(self, callsign: str) -> tuple[Optional[str], Optional[datetime]]:
        """Get the latest intelligence summary for a company."""
        news = self.get_company_news(callsign, days=30)
        if not news:
            return None, None
        
        latest = news[0]  # Already sorted by date
        try:
            latest_date = datetime.fromisoformat(latest.published_at.replace('Z', '+00:00'))
        except:
            latest_date = None
            
        return latest.summary or latest.title, latest_date
    
    
    
