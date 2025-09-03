"""
Render service for HTML and text generation.

Handles template rendering for digests, reports, and notifications.
"""

from datetime import datetime
from typing import Dict, Any, List, Optional
import structlog
from jinja2 import Environment, BaseLoader, TemplateError

from app.core.models import DigestData, CompanyIntelligence
from app.core.exceptions import ValidationError

logger = structlog.get_logger(__name__)


# HTML Template for Weekly Digest
DIGEST_TEMPLATE = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width">
    <title>{{ subject }}</title>
    <style>
      body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; line-height: 1.45; color: #111; }
      .h1 { font-size: 20px; font-weight: 700; margin: 0 0 8px; }
      .muted { color: #555; }
      table { border-collapse: collapse; width: 100%; margin: 8px 0; }
      th, td { padding: 6px 8px; border-bottom: 1px solid #eee; text-align: left; }
      th { background-color: #f8f9fa; font-weight: 600; }
      .badge { display: inline-block; padding: 2px 6px; border-radius: 8px; background: #f0f0f0; font-size: 12px; margin-right: 4px; }
      .good { background: #e6ffed; color: #0d7044; }
      .bad  { background: #ffeaea; color: #d73a49; }
      .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
      .section { margin: 16px 0 24px; }
      .positive { color: #28a745; }
      .negative { color: #dc3545; }
      .footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #eee; font-size: 12px; color: #777; }
    </style>
  </head>
  <body>
    <div class="section">
      <div class="h1">{{ subject }}</div>
      <div class="muted">Generated automatically from your Metabase weekly diff on {{ now_date }}.</div>
    </div>

    <div class="section">
      <div class="h1">At a glance</div>
      <div>
        <span class="badge">Accounts: {{ stats.total_accounts }}</span>
        <span class="badge">Changed: {{ stats.changed_accounts }}</span>
        {% if stats.new_accounts > 0 %}<span class="badge good">New: {{ stats.new_accounts }}</span>{% endif %}
        {% if stats.removed_accounts > 0 %}<span class="badge bad">Removed: {{ stats.removed_accounts }}</span>{% endif %}
        {% if stats.total_product_flips > 0 %}<span class="badge">Product flips: {{ stats.total_product_flips }}</span>{% endif %}
      </div>
    </div>

    {% if top_pct_gainers or top_pct_losers %}
    <div class="section">
      <div class="h1">Top balance movers</div>

      {% if top_pct_gainers %}
      <div style="margin-bottom: 16px;">
        <strong>Biggest percentage increases</strong>
        <table>
          <thead><tr><th>Callsign</th><th>Percentage Change</th><th>Balance Δ</th></tr></thead>
          <tbody>
            {% for r in top_pct_gainers %}
            <tr>
              <td><strong>{{ r.callsign }}</strong></td>
              <td class="mono positive">{{ "+{:.2f}%".format(r.percentage_change) }}</td>
              <td class="mono">
                {% if r.balance_delta is not none %}
                  {{ "{:,.0f}".format(r.balance_delta) }}
                {% else %}
                  —
                {% endif %}
              </td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      {% endif %}

      {% if top_pct_losers %}
      <div>
        <strong>Biggest percentage decreases</strong>
        <table>
          <thead><tr><th>Callsign</th><th>Percentage Change</th><th>Balance Δ</th></tr></thead>
          <tbody>
            {% for r in top_pct_losers %}
            <tr>
              <td><strong>{{ r.callsign }}</strong></td>
              <td class="mono negative">{{ "{:.2f}%".format(r.percentage_change) }}</td>
              <td class="mono">
                {% if r.balance_delta is not none %}
                  {{ "{:,.0f}".format(r.balance_delta) }}
                {% else %}
                  —
                {% endif %}
              </td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      {% endif %}
    </div>
    {% endif %}

    {% if product_starts or product_stops %}
    <div class="section">
      <div class="h1">Product usage changes</div>
      {% if product_starts %}
      <div style="margin-bottom: 12px;">
        <strong>Started using</strong>
        <ul>
          {% for item in product_starts %}
            <li><span class="badge good">start</span> <strong>{{ item.callsign }}</strong> → {{ item.product }}</li>
          {% endfor %}
        </ul>
      </div>
      {% endif %}
      {% if product_stops %}
      <div>
        <strong>Stopped using</strong>
        <ul>
          {% for item in product_stops %}
            <li><span class="badge bad">stop</span> <strong>{{ item.callsign }}</strong> → {{ item.product }}</li>
          {% endfor %}
        </ul>
      </div>
      {% endif %}
    </div>
    {% endif %}

    <div class="footer">
      <div>Generated by SeeRM on {{ now_date }} at {{ now_time }}</div>
      <div style="margin-top: 4px;">
        {% if stats.changed_accounts > 0 %}
          {{ stats.changed_accounts }}/{{ stats.total_accounts }} accounts changed
        {% else %}
          No account changes detected
        {% endif %}
      </div>
    </div>
  </body>
</html>
"""


# Template for Intelligence Reports
INTELLIGENCE_TEMPLATE = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width">
    <title>Weekly Intelligence Report</title>
    <style>
      body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; line-height: 1.45; color: #111; margin: 20px; }
      .h1 { font-size: 24px; font-weight: 700; margin: 0 0 16px; }
      .h2 { font-size: 20px; font-weight: 600; margin: 24px 0 12px; border-bottom: 2px solid #eee; padding-bottom: 4px; }
      .h3 { font-size: 16px; font-weight: 600; margin: 16px 0 8px; }
      .company-section { margin: 24px 0; padding: 16px; border: 1px solid #e1e4e8; border-radius: 8px; }
      .news-item { margin: 8px 0; padding: 8px; border-left: 3px solid #0366d6; background: #f6f8fa; }
      .news-title { font-weight: 600; }
      .news-meta { font-size: 12px; color: #586069; margin-top: 4px; }
      .summary { background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 4px; padding: 12px; margin: 12px 0; }
      .footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #eee; font-size: 12px; color: #777; text-align: center; }
    </style>
  </head>
  <body>
    <div class="h1">Weekly Intelligence Report</div>
    <div style="color: #586069; margin-bottom: 24px;">Generated on {{ now_date }}</div>

    {% for callsign, intel in intelligence_by_company.items() %}
    <div class="company-section">
      <div class="h3">{{ callsign.upper() }}</div>
      
      {% if intel.summary %}
      <div class="summary">
        <strong>Summary:</strong> {{ intel.summary }}
      </div>
      {% endif %}

      {% if intel.news_items %}
      <div class="h4" style="font-weight: 500; margin: 12px 0 8px; color: #586069;">Recent News ({{ intel.news_items|length }} items)</div>
      {% for item in intel.news_items %}
      <div class="news-item">
        {% if item.url %}
          <div class="news-title"><a href="{{ item.url }}" target="_blank">{{ item.title }}</a></div>
        {% else %}
          <div class="news-title">{{ item.title }}</div>
        {% endif %}
        <div class="news-meta">
          {{ item.source }}{% if item.published_at %} • {{ item.published_at }}{% endif %}
        </div>
      </div>
      {% endfor %}
      {% else %}
      <div style="color: #586069; font-style: italic;">No recent news items found.</div>
      {% endif %}
    </div>
    {% endfor %}

    <div class="footer">
      <div>Generated by SeeRM Intelligence System</div>
      <div style="margin-top: 4px;">Total companies: {{ intelligence_by_company|length }}</div>
    </div>
  </body>
</html>
"""


class DigestRenderer:
    """
    Service for rendering HTML digests and reports.
    """
    
    def __init__(self):
        self.jinja_env = Environment(loader=BaseLoader())
        
        # Load templates
        try:
            self.digest_template = self.jinja_env.from_string(DIGEST_TEMPLATE)
            self.intelligence_template = self.jinja_env.from_string(INTELLIGENCE_TEMPLATE)
            
            logger.info("Digest renderer initialized with templates")
            
        except TemplateError as e:
            logger.error("Failed to initialize templates", error=str(e))
            raise ValidationError(f"Template initialization failed: {e}")
    
    def render_digest(self, digest_data: DigestData) -> str:
        """
        Render digest data to HTML.
        
        Args:
            digest_data: Digest data to render
            
        Returns:
            HTML string
            
        Raises:
            ValidationError: On rendering errors
        """
        try:
            now = datetime.now()
            
            # Prepare template context
            context = {
                "subject": digest_data.subject,
                "now_date": now.strftime("%Y-%m-%d"),
                "now_time": now.strftime("%H:%M:%S UTC"),
                "stats": digest_data.stats.dict(),
                "top_pct_gainers": [g.dict() for g in digest_data.top_pct_gainers],
                "top_pct_losers": [l.dict() for l in digest_data.top_pct_losers],
                "product_starts": digest_data.product_starts,
                "product_stops": digest_data.product_stops,
            }
            
            # Render template
            html = self.digest_template.render(**context)
            
            logger.info(
                "Digest rendered successfully",
                html_length=len(html),
                accounts=digest_data.stats.total_accounts,
                gainers=len(digest_data.top_pct_gainers),
                losers=len(digest_data.top_pct_losers)
            )
            
            return html
            
        except TemplateError as e:
            error_msg = f"Template rendering failed: {e}"
            logger.error("Digest rendering failed", error=str(e))
            raise ValidationError(error_msg)
        
        except Exception as e:
            error_msg = f"Unexpected error rendering digest: {e}"
            logger.error("Digest rendering failed", error=str(e))
            raise ValidationError(error_msg)
    
    def render_intelligence_report(
        self,
        intelligence_by_company: Dict[str, CompanyIntelligence]
    ) -> str:
        """
        Render intelligence report to HTML.
        
        Args:
            intelligence_by_company: Dict mapping callsign to intelligence data
            
        Returns:
            HTML string
            
        Raises:
            ValidationError: On rendering errors
        """
        try:
            now = datetime.now()
            
            # Prepare template context
            context = {
                "now_date": now.strftime("%Y-%m-%d"),
                "now_time": now.strftime("%H:%M:%S UTC"),
                "intelligence_by_company": intelligence_by_company,
            }
            
            # Render template
            html = self.intelligence_template.render(**context)
            
            logger.info(
                "Intelligence report rendered successfully",
                html_length=len(html),
                companies=len(intelligence_by_company)
            )
            
            return html
            
        except TemplateError as e:
            error_msg = f"Intelligence template rendering failed: {e}"
            logger.error("Intelligence rendering failed", error=str(e))
            raise ValidationError(error_msg)
        
        except Exception as e:
            error_msg = f"Unexpected error rendering intelligence report: {e}"
            logger.error("Intelligence rendering failed", error=str(e))
            raise ValidationError(error_msg)
    
    def render_dry_run_report(
        self,
        operations: List[Dict[str, Any]],
        title: str = "Dry Run Report"
    ) -> str:
        """
        Render dry run operations report to HTML.
        
        Args:
            operations: List of operations that would be performed
            title: Report title
            
        Returns:
            HTML string
        """
        try:
            now = datetime.now()
            
            # Simple template for dry run reports
            template_str = """
            <!doctype html>
            <html>
              <head>
                <meta charset="utf-8">
                <title>{{ title }}</title>
                <style>
                  body { font-family: Arial, sans-serif; margin: 20px; }
                  .header { color: #0366d6; border-bottom: 2px solid #eee; padding-bottom: 8px; margin-bottom: 16px; }
                  .operation { margin: 8px 0; padding: 8px; border-left: 3px solid #28a745; background: #f6f8fa; }
                  .would-create { border-left-color: #28a745; }
                  .would-update { border-left-color: #ffa500; }
                  .would-delete { border-left-color: #dc3545; }
                  .summary { background: #fff3cd; padding: 12px; border-radius: 4px; margin: 16px 0; }
                </style>
              </head>
              <body>
                <div class="header">
                  <h1>{{ title }}</h1>
                  <p>Generated on {{ now_date }} at {{ now_time }}</p>
                </div>
                
                <div class="summary">
                  <strong>Summary:</strong> {{ operations|length }} operations would be performed.
                </div>
                
                {% for op in operations %}
                <div class="operation {{ op.css_class }}">
                  <strong>{{ op.operation_type|upper }}:</strong> {{ op.description }}
                  {% if op.details %}
                  <div style="margin-top: 4px; font-size: 12px; color: #666;">
                    {{ op.details }}
                  </div>
                  {% endif %}
                </div>
                {% endfor %}
                
                <div style="margin-top: 24px; font-size: 12px; color: #777;">
                  This is a dry run report - no actual changes were made.
                </div>
              </body>
            </html>
            """
            
            # Enhance operations with CSS classes
            enhanced_ops = []
            for op in operations:
                enhanced_op = dict(op)
                if "create" in op.get("operation_type", "").lower():
                    enhanced_op["css_class"] = "would-create"
                elif "update" in op.get("operation_type", "").lower():
                    enhanced_op["css_class"] = "would-update" 
                elif "delete" in op.get("operation_type", "").lower():
                    enhanced_op["css_class"] = "would-delete"
                else:
                    enhanced_op["css_class"] = ""
                enhanced_ops.append(enhanced_op)
            
            template = self.jinja_env.from_string(template_str)
            html = template.render(
                title=title,
                now_date=now.strftime("%Y-%m-%d"),
                now_time=now.strftime("%H:%M:%S UTC"),
                operations=enhanced_ops
            )
            
            logger.info(
                "Dry run report rendered",
                operations_count=len(operations),
                title=title
            )
            
            return html
            
        except Exception as e:
            error_msg = f"Failed to render dry run report: {e}"
            logger.error("Dry run report rendering failed", error=str(e))
            raise ValidationError(error_msg)


def create_digest_renderer() -> DigestRenderer:
    """
    Factory function to create digest renderer.
    
    Returns:
        Configured DigestRenderer
    """
    return DigestRenderer()