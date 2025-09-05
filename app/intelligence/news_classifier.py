"""
Intelligent news classification using LLM and fallback keyword matching.

Provides hybrid classification system for categorizing company news items
into actionable intelligence categories.
"""

import re
from typing import Dict, List, Optional

import structlog
from openai import OpenAI

from app.core.config import Settings
from app.core.exceptions import WorkflowError

from .models import NewsItem, NewsType

logger = structlog.get_logger(__name__)


class NewsClassifier:
    """
    Hybrid news classification using LLM and keyword fallbacks.

    Classifies news items into categories optimized for executive scanning.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings()
        self._openai_client = None

        # Cache for classification results to avoid re-processing
        self._classification_cache: Dict[str, NewsType] = {}

        logger.info(
            "News classifier initialized",
            openai_available=bool(self.settings.intelligence.openai_api_key),
        )

    @property
    def openai_client(self) -> Optional[OpenAI]:
        """Lazy initialization of OpenAI client."""
        if self._openai_client is None and self.settings.intelligence.openai_api_key:
            try:
                self._openai_client = OpenAI(api_key=self.settings.intelligence.openai_api_key)
                logger.debug("OpenAI client initialized successfully")
            except Exception as e:
                logger.warning("Failed to initialize OpenAI client", error=str(e))
        return self._openai_client

    def classify_news_items(self, news_items: List[NewsItem]) -> List[NewsItem]:
        """
        Classify a batch of news items using intelligent categorization.

        Args:
            news_items: List of NewsItem objects to classify

        Returns:
            Same list with updated news_type classifications
        """
        if not news_items:
            return news_items

        logger.info("Classifying news items", count=len(news_items))

        # Try LLM classification first, fall back to keywords
        if self.openai_client:
            try:
                return self._classify_with_llm(news_items)
            except Exception as e:
                logger.warning("LLM classification failed, falling back to keywords", error=str(e))

        return self._classify_with_keywords(news_items)

    def _classify_with_llm(self, news_items: List[NewsItem]) -> List[NewsItem]:
        """Classify news items using OpenAI LLM."""
        logger.info("Using LLM classification", items=len(news_items))

        # Process in batches to manage API costs and token limits
        batch_size = 20  # Process 20 items at a time
        classified_items = []

        for i in range(0, len(news_items), batch_size):
            batch = news_items[i : i + batch_size]
            try:
                batch_results = self._classify_batch_llm(batch)
                classified_items.extend(batch_results)
            except Exception as e:
                logger.warning(
                    f"LLM batch classification failed for items {i}-{i+len(batch)}", error=str(e)
                )
                # Fall back to keyword classification for this batch
                fallback_results = self._classify_with_keywords(batch)
                classified_items.extend(fallback_results)

        return classified_items

    def _classify_batch_llm(self, news_items: List[NewsItem]) -> List[NewsItem]:
        """Classify a batch of news items using LLM."""
        # Create structured prompt for batch classification
        news_summaries = []
        for i, item in enumerate(news_items):
            # Use summary if available, otherwise title
            text = item.summary or item.title
            news_summaries.append(f"{i+1}. {text}")

        prompt = self._create_classification_prompt(news_summaries)

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-3.5-turbo",  # Cost-effective for classification
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,  # Low temperature for consistent classification
                max_tokens=500,  # Enough for classification results
            )

            classifications = self._parse_llm_response(response.choices[0].message.content)

            # Apply classifications to news items
            for i, item in enumerate(news_items):
                if i < len(classifications):
                    item.news_type = classifications[i]
                else:
                    # Fallback for any missed items
                    item.news_type = self._classify_single_keyword(item)

            logger.info(
                "LLM classification completed",
                batch_size=len(news_items),
                llm_classified=len(classifications),
            )

            return news_items

        except Exception as e:
            logger.error("LLM classification API call failed", error=str(e))
            raise WorkflowError(f"LLM classification failed: {e}")

    def _get_system_prompt(self) -> str:
        """Get the system prompt for LLM classification."""
        return """You are a financial intelligence analyst categorizing company news for portfolio monitoring.

Classify each news item into exactly ONE of these categories:
- funding: Investment rounds, fundraising, venture capital, IPOs
- product_launch: New products, features, service releases, platform launches
- partnerships: Strategic alliances, integrations, collaborations, joint ventures
- leadership: C-suite appointments, key hires, departures, board changes
- growth_metrics: User milestones, revenue announcements, expansion, scaling
- legal_regulatory: Compliance updates, lawsuits, regulatory changes, policy impacts
- technical: Platform issues, outages, security incidents, infrastructure changes
- acquisition: M&A, buyouts, mergers, company purchases
- other_notable: Significant news that doesn't fit other categories

Be precise and consistent. Focus on the primary business impact, not secondary effects."""

    def _create_classification_prompt(self, news_summaries: List[str]) -> str:
        """Create the classification prompt for a batch of news items."""
        prompt_parts = [
            "Classify each news item below. Respond with only the category name for each item (one per line):",
            "",
        ]
        prompt_parts.extend(news_summaries)
        prompt_parts.extend(["", "Classifications (one per line):"])

        return "\n".join(prompt_parts)

    def _parse_llm_response(self, response: str) -> List[NewsType]:
        """Parse LLM response into NewsType classifications."""
        if not response:
            return []

        lines = [line.strip().lower() for line in response.split("\n") if line.strip()]
        classifications = []

        # Map response text to NewsType values
        category_mapping = {
            "funding": NewsType.FUNDING,
            "product_launch": NewsType.PRODUCT_LAUNCH,
            "partnerships": NewsType.PARTNERSHIPS,
            "leadership": NewsType.LEADERSHIP,
            "growth_metrics": NewsType.GROWTH_METRICS,
            "legal_regulatory": NewsType.LEGAL_REGULATORY,
            "technical": NewsType.TECHNICAL,
            "acquisition": NewsType.ACQUISITION,
            "other_notable": NewsType.OTHER_NOTABLE,
        }

        for line in lines:
            # Handle numbered responses (e.g., "1. funding")
            cleaned = re.sub(r"^\d+\.?\s*", "", line)

            if cleaned in category_mapping:
                classifications.append(category_mapping[cleaned])
            else:
                # Try partial matching
                matched = False
                for key, value in category_mapping.items():
                    if key in cleaned or cleaned in key:
                        classifications.append(value)
                        matched = True
                        break

                if not matched:
                    classifications.append(NewsType.OTHER_NOTABLE)

        return classifications

    def _classify_with_keywords(self, news_items: List[NewsItem]) -> List[NewsItem]:
        """Classify news items using enhanced keyword matching."""
        logger.info("Using keyword classification", items=len(news_items))

        for item in news_items:
            item.news_type = self._classify_single_keyword(item)

        return news_items

    def _classify_single_keyword(self, item: NewsItem) -> NewsType:
        """Classify a single news item using keyword patterns."""
        # Combine title and summary for analysis
        text = f"{item.title} {item.summary or ''}".lower()

        # Enhanced keyword patterns
        keyword_patterns = {
            NewsType.FUNDING: [
                r"\b(funding|fundrais|investment|investor|venture|capital|series\s+[abc]|ipo|raised?\s+\$|round|valuation)\b"
            ],
            NewsType.PRODUCT_LAUNCH: [
                r"\b(launch|release|debut|unveil|introduce|announce.*product|new.*feature|platform|beta|version)\b"
            ],
            NewsType.PARTNERSHIPS: [
                r"\b(partnership|partner|collaboration|alliance|integration|joint\s+venture|team.*up|deal|agreement)\b"
            ],
            NewsType.LEADERSHIP: [
                r"\b(ceo|cto|cfo|founder|president|director|executive|hire|appointment|resign|departure|joins?\s+as|named\s+as)\b"
            ],
            NewsType.GROWTH_METRICS: [
                r"\b(million\s+users|growth|revenue|quarterly|annual|milestone|expansion|scale|reaches?|crosses?)\b"
            ],
            NewsType.LEGAL_REGULATORY: [
                r"\b(lawsuit|legal|compliance|regulatory|sec|ftc|gdpr|privacy|court|settlement|investigation)\b"
            ],
            NewsType.TECHNICAL: [
                r"\b(outage|downtime|security|breach|incident|infrastructure|technical|platform.*issue|bug|fix)\b"
            ],
            NewsType.ACQUISITION: [
                r"\b(acquire|acquisition|merger|bought|purchase|buyout|m&a|takes?\s+over|deal\s+worth)\b"
            ],
        }

        # Check patterns in priority order
        for news_type, patterns in keyword_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text):
                    return news_type

        return NewsType.OTHER_NOTABLE

    def get_category_display_info(self) -> Dict[NewsType, Dict[str, str]]:
        """Get display information for each category."""
        return {
            NewsType.FUNDING: {"emoji": "💰", "title": "Funding & Investment"},
            NewsType.PRODUCT_LAUNCH: {"emoji": "🚀", "title": "Product Launches"},
            NewsType.PARTNERSHIPS: {"emoji": "🤝", "title": "Partnerships & Alliances"},
            NewsType.LEADERSHIP: {"emoji": "👔", "title": "Leadership Changes"},
            NewsType.GROWTH_METRICS: {"emoji": "📈", "title": "Growth & Metrics"},
            NewsType.LEGAL_REGULATORY: {"emoji": "⚖️", "title": "Legal & Regulatory"},
            NewsType.TECHNICAL: {"emoji": "🔧", "title": "Technical & Infrastructure"},
            NewsType.ACQUISITION: {"emoji": "🏢", "title": "Acquisitions & M&A"},
            NewsType.OTHER_NOTABLE: {"emoji": "📰", "title": "Other Notable News"},
        }


def create_news_classifier(settings: Optional[Settings] = None) -> NewsClassifier:
    """Factory function to create news classifier."""
    return NewsClassifier(settings)
