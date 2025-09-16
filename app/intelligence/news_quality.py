"""Scoring and filtering helpers for news collection quality control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import tldextract
from dateutil import parser as date_parser

from app.core.config import IntelligenceConfig
from app.core.models import Company
from app.intelligence.models import NewsItem

# Baseline domain preferences. These can be extended via environment configuration.
DEFAULT_TRUSTED_DOMAINS: Dict[str, float] = {
    "reuters.com": 2.0,
    "wsj.com": 1.8,
    "bloomberg.com": 1.8,
    "financialtimes.com": 1.6,
    "techcrunch.com": 1.5,
    "theinformation.com": 1.5,
    "cnbc.com": 1.4,
    "forbes.com": 1.3,
    "prnewswire.com": 1.0,
    "businesswire.com": 1.0,
    "globenewswire.com": 0.8,
}

DEFAULT_DEMOTED_DOMAINS: Dict[str, float] = {
    "prnewswire.com": -0.2,
    "businesswire.com": -0.2,
    "globenewswire.com": -0.3,
    "medium.com": -0.6,
    "pinterest.com": -1.0,
}

DEFAULT_BLOCKED_DOMAINS = {
    "facebook.com",
    "twitter.com",
    "linkedin.com",
    "instagram.com",
    "youtube.com",
    "tiktok.com",
    "eventbrite.com",
    "glassdoor.com",
    "lever.co",
    "greenhouse.io",
    "jobvite.com",
    "reddit.com",
}

DEFAULT_POSITIVE_KEYWORDS = [
    "acquire",
    "acquisition",
    "raises",
    "raising",
    "funding",
    "launch",
    "partnership",
    "partners",
    "merger",
    "expands",
    "series a",
    "series b",
    "growth",
]

DEFAULT_NEGATIVE_KEYWORDS = [
    "hiring",
    "we're hiring",
    "webinar",
    "job opening",
    "job alert",
    "podcast",
    "tips",
    "blog post",
    "best practices",
    "how to",
]

MIN_SCORE = 0.2


@dataclass
class QualityPreferences:
    """Aggregated scoring preferences loaded from configuration."""

    trusted_domains: Dict[str, float]
    demoted_domains: Dict[str, float]
    blocked_domains: List[str]
    positive_keywords: List[str]
    negative_keywords: List[str]
    trusted_sources_for_queries: List[str]


class NewsQualityScorer:
    """Score and filter news items based on heuristics and preferences."""

    def __init__(self, config: IntelligenceConfig):
        """Initialise scorer with preferences derived from configuration."""
        self.preferences = self._build_preferences(config)

    @property
    def blocked_domains(self) -> List[str]:
        """Return the list of blocked domains."""
        return self.preferences.blocked_domains

    def _build_preferences(self, config: IntelligenceConfig) -> QualityPreferences:
        """Construct quality preferences from configuration and defaults."""
        trusted = dict(DEFAULT_TRUSTED_DOMAINS)
        for domain in config.trusted_domains:
            trusted[domain.lower()] = 1.5

        demoted = dict(DEFAULT_DEMOTED_DOMAINS)
        for domain in config.demoted_domains:
            demoted[domain.lower()] = -0.7

        blocked = list(DEFAULT_BLOCKED_DOMAINS)
        blocked.extend(domain.lower() for domain in config.blocked_domains)

        positive_keywords = list(DEFAULT_POSITIVE_KEYWORDS)
        positive_keywords.extend(k.lower() for k in config.positive_keywords)

        negative_keywords = list(DEFAULT_NEGATIVE_KEYWORDS)
        negative_keywords.extend(k.lower() for k in config.negative_keywords)

        trusted_for_queries = sorted(trusted.keys())[:10]

        return QualityPreferences(
            trusted_domains=trusted,
            demoted_domains=demoted,
            blocked_domains=blocked,
            positive_keywords=positive_keywords,
            negative_keywords=negative_keywords,
            trusted_sources_for_queries=trusted_for_queries,
        )

    def _extract_domain(self, url: str, fallback: str = "") -> str:
        """Return the registered domain for a URL."""
        if not url:
            url = fallback
        ext = tldextract.extract(url)
        if ext.registered_domain:
            return ext.registered_domain.lower()
        return ext.domain.lower() if ext.domain else fallback.lower()

    def _score_domain(self, domain: str) -> Tuple[float, bool]:
        """Return domain score and whether the domain is blocked."""
        domain = domain.lower()
        if not domain:
            return 0.0, False
        if domain in (d.lower() for d in self.preferences.blocked_domains):
            return -100.0, True
        if domain in self.preferences.trusted_domains:
            return self.preferences.trusted_domains[domain], False
        if domain in self.preferences.demoted_domains:
            return self.preferences.demoted_domains[domain], False
        return 0.0, False

    def _score_keywords(self, text: str) -> float:
        """Score text based on presence of positive and negative keywords."""
        text_lower = text.lower()
        positive = sum(1 for kw in self.preferences.positive_keywords if kw in text_lower)
        negative = sum(1 for kw in self.preferences.negative_keywords if kw in text_lower)
        return positive * 0.6 - negative * 0.8

    def _score_recency(self, published_at: Optional[str]) -> float:
        """Boost or penalise items based on published date."""
        if not published_at:
            return 0.0
        try:
            dt = date_parser.parse(str(published_at))
        except Exception:
            return 0.0
        delta = datetime.utcnow() - dt
        days = max(delta.total_seconds() / 86400, 0)
        if days < 0:
            return 0.5
        if days > 14:
            return -0.4
        return max(0.0, 1.2 - 0.15 * days)

    def _score_company_match(self, company: Company, text: str) -> float:
        """Assign a score when company identifiers appear in the text."""
        text = text.lower()
        score = 0.0
        if company.callsign and company.callsign.lower() in text:
            score += 0.5
        if company.dba and company.dba.lower() in text:
            score += 0.5
        if company.aka_names:
            for aka in company.aka_names.split(","):
                aka = aka.strip().lower()
                if aka and aka in text:
                    score += 0.3
        return score

    def score_item(self, company: Company, item: NewsItem) -> Tuple[float, bool]:
        """Compute a relevance score for an individual news item."""
        domain = self._extract_domain(item.url, item.source)
        domain_score, blocked = self._score_domain(domain)
        if blocked:
            return -999.0, True

        text = " ".join(filter(None, [item.title, item.summary or ""]))
        keyword_score = self._score_keywords(text)
        recency_score = self._score_recency(item.published_at)
        company_match_score = self._score_company_match(company, text)

        base = domain_score + keyword_score + recency_score + company_match_score

        if domain not in self.preferences.trusted_domains and domain_score == 0:
            base -= 0.2  # slight penalty for unknown domains

        return base, False

    def rank_items(
        self, company: Company, items: Iterable[NewsItem], max_items: int
    ) -> List[NewsItem]:
        """Score, filter, and rank items for a company."""
        scored: List[Tuple[float, NewsItem]] = []
        for item in items:
            score, blocked = self.score_item(company, item)
            if blocked or score < MIN_SCORE:
                continue
            item.relevance_score = score
            scored.append((score, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:max_items]]

    def build_query_variants(
        self,
        company: Company,
        domains: Sequence[str],
        base_terms: Sequence[str],
    ) -> List[str]:
        """Generate focused Google CSE queries."""
        queries: List[str] = []
        primary = base_terms[0] if base_terms else company.callsign
        if primary:
            queries.append(f'"{primary}" news')
            refined = (
                f'"{primary}" '
                "(acquisition OR acquires OR raises OR funding OR partnership OR launch)"
            )
            queries.append(refined)

        for domain in domains:
            queries.append(f'site:{domain} "{primary}"')

        for trusted in self.preferences.trusted_sources_for_queries:
            if trusted not in domains:
                queries.append(f'"{primary}" site:{trusted}')

        unique_queries = []
        seen = set()
        for query in queries:
            q = query.strip()
            if not q or q in seen:
                continue
            seen.add(q)
            unique_queries.append(q)
        return unique_queries


__all__ = ["NewsQualityScorer"]
