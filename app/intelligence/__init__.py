"""
Intelligence and reporting module for SeeRM.

Provides data aggregation, analysis, and automated report generation
combining CSV data, Notion intelligence, and external sources.
"""

from .data_aggregator import IntelligenceAggregator
from .models import CompanyIntelligence, Movement, NewsItem, Report

__all__ = ["IntelligenceAggregator", "CompanyIntelligence", "NewsItem", "Movement", "Report"]
