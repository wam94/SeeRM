from __future__ import annotations
from jinja2 import Template
from datetime import datetime

TEMPLATE = Template("""
<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width\">
    <title>{{ subject }}</title>
    <style>
      body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; line-height: 1.45; color: #111; }
      .h1 { font-size: 20px; font-weight: 700; margin: 0 0 8px; }
      .muted { color: #555; }
      table { border-collapse: collapse; width: 100%; }
      th, td { padding: 6px 8px; border-bottom: 1px solid #eee; text-align: left; }
      .badge { display: inline-block; padding: 2px 6px; border-radius: 8px; background: #f0f0f0; font-size: 12px; }
      .good { background: #e6ffed; }
      .bad  { background: #ffeaea; }
      .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, \"Liberation Mono\", monospace; }
      .section { margin: 16px 0 24px; }
    </style>
  </head>
  <body>
    <div class=\"section\">
      <div class=\"h1\">Client Weekly Digest — {{ now_date }}</div>
      <div class=\"muted\">Generated automatically from your Metabase weekly diff.</div>
    </div>

    <div class=\"section\">
      <div class=\"h1\">At a glance</div>
      <div>
        <span class=\"badge\">Accounts: {{ stats.total_accounts }}</span>
        <span class=\"badge\">Changed: {{ stats.changed_accounts }}</span>
        <span class=\"badge\">New: {{ stats.new_accounts }}</span>
        <span class=\"badge\">Removed: {{ stats.removed_accounts }}</span>
        <span class=\"badge\">Product flips: {{ stats.total_product_flips }}</span>
      </div>
    </div>

    {% if top_movers %}
    <div class=\"section\">
      {% if top_pct_gainers or top_pct_losers %}
      <div class="section">
        <div class="h1">Top % balance movers</div>
      
        {% if top_pct_gainers %}
        <div><strong>Biggest % increases</strong></div>
        <table>
          <thead><tr><th>Callsign</th><th>%</th><th>Δ Balance</th></tr></thead>
          <tbody>
            {% for r in top_pct_gainers %}
            <tr>
              <td>{{ r.callsign }}</td>
              <td class="mono">{{ "{:+.2f}%".format(r.pct) }}</td>
              <td class="mono">{{ "" if r.balance_delta is none else "{:,.0f}".format(r.balance_delta) }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
        {% endif %}
      
        {% if top_pct_losers %}
        <div style="margin-top:10px;"><strong>Biggest % decreases</strong></div>
        <table>
          <thead><tr><th>Callsign</th><th>%</th><th>Δ Balance</th></tr></thead>
          <tbody>
            {% for r in top_pct_losers %}
            <tr>
              <td>{{ r.callsign }}</td>
              <td class="mono">{{ "{:+.2f}%".format(r.pct) }}</td>
              <td class="mono">{{ "" if r.balance_delta is none else "{:,.0f}".format(r.balance_delta) }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
        {% endif %}
      </div>
      {% endif %}

    {% if product_starts or product_stops %}
    <div class=\"section\">
      <div class=\"h1\">Product usage changes</div>
      {% if product_starts %}
      <div><strong>Starts</strong></div>
      <ul>
        {% for item in product_starts %}
          <li><span class=\"badge good\">start</span> {{ item.callsign }} → {{ item.product }}</li>
        {% endfor %}
      </ul>
      {% endif %}
      {% if product_stops %}
      <div><strong>Stops</strong></div>
      <ul>
        {% for item in product_stops %}
          <li><span class=\"badge bad\">stop</span> {{ item.callsign }} → {{ item.product }}</li>
        {% endfor %}
      </ul>
      {% endif %}
    </div>
    {% endif %}

    {% if unchanged %}
    <div class=\"section\">
      <div class=\"h1\">Stable accounts (no change)</div>
      <div class=\"muted\">Showing up to 50</div>
      <ul>
        {% for cs in unchanged[:50] %}
        <li>{{ cs }}</li>
        {% endfor %}
      </ul>
    </div>
    {% endif %}

    <div class=\"section muted\">
      <div>— End of report</div>
    </div>
  </body>
</html>
""")

def render_digest(context: dict) -> str:
    now_date = datetime.utcnow().strftime("%Y-%m-%d")
    subject = context.get("subject", f"Client Weekly Digest — {now_date}")
    return TEMPLATE.render(now_date=now_date, subject=subject, **context)
