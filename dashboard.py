"""
dashboard.py
------------
K8sWhisperer audit dashboard — clean white professional UI.

Shows:
  - Live summary cards (total events, auto-resolved, human decisions, alerts)
  - Full audit log table with event-type colour coding
  - Past human resolutions panel (the memory bank for HITL)
  - Auto-refreshes every 15 seconds
"""

import json
import os
from datetime import datetime, timezone
from flask import Flask, render_template_string

try:
    from agent.logger import get_audit_log, get_past_resolutions
except Exception:  # agent package unavailable in test environment
    def get_audit_log():
        return []

    def get_past_resolutions(failure_type: str):
        return []

app = Flask(__name__)
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("DASHBOARD_PORT", "5050"))


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <meta http-equiv="refresh" content="15"/>
  <title>K8sWhisperer — Dashboard</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f8fafc;
      color: #1e293b;
      min-height: 100vh;
    }

    /* ── NAV ── */
    nav {
      background: #ffffff;
      border-bottom: 1px solid #e2e8f0;
      padding: 0 2rem;
      height: 56px;
      display: flex; align-items: center; justify-content: space-between;
    }
    nav .brand { font-weight: 800; font-size: 1.1rem; color: #0f172a; letter-spacing: -0.02em; }
    nav .brand span { color: #3b82f6; }
    nav .refresh { font-size: 0.75rem; color: #94a3b8; }

    /* ── MAIN ── */
    main { max-width: 1200px; margin: 0 auto; padding: 2rem 1.5rem; }

    /* ── SUMMARY CARDS ── */
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
    .card {
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      padding: 1.25rem 1.5rem;
      box-shadow: 0 1px 3px rgba(0,0,0,.04);
    }
    .card .label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: .07em; color: #94a3b8; font-weight: 600; margin-bottom: 0.4rem; }
    .card .value { font-size: 2rem; font-weight: 800; color: #0f172a; }
    .card .sub   { font-size: 0.78rem; color: #64748b; margin-top: 0.2rem; }
    .card.blue  .value { color: #3b82f6; }
    .card.green .value { color: #22c55e; }
    .card.amber .value { color: #f59e0b; }
    .card.red   .value { color: #ef4444; }

    /* ── SECTION HEADER ── */
    .section-header {
      display: flex; align-items: center; gap: 0.5rem;
      font-size: 1rem; font-weight: 700; color: #0f172a;
      margin-bottom: 0.75rem; margin-top: 0.25rem;
    }

    /* ── LOG TABLE ── */
    .table-wrap {
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,.04);
      margin-bottom: 2rem;
    }
    table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
    thead th {
      background: #f8fafc;
      padding: 10px 14px;
      text-align: left;
      font-size: 0.72rem; text-transform: uppercase; letter-spacing: .07em;
      color: #64748b; font-weight: 600;
      border-bottom: 1px solid #e2e8f0;
    }
    tbody tr:hover { background: #f8fafc; }
    tbody td { padding: 10px 14px; border-bottom: 1px solid #f1f5f9; color: #334155; vertical-align: top; }
    tbody tr:last-child td { border-bottom: none; }
    code { background: #f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 0.82rem; color: #334155; word-break: break-all; }

    /* ── EVENT TYPE BADGES ── */
    .ev {
      display: inline-block; padding: 2px 9px; border-radius: 20px;
      font-size: 0.72rem; font-weight: 700; white-space: nowrap;
    }
    .ev-ANOMALY_DETECTED  { background:#dbeafe; color:#1d4ed8; }
    .ev-DIAGNOSIS_COMPLETE{ background:#ede9fe; color:#6d28d9; }
    .ev-AUTO_EXECUTE      { background:#dcfce7; color:#166534; }
    .ev-RESOLUTION_COMPLETE{ background:#d1fae5; color:#065f46; }
    .ev-HITL_REQUESTED    { background:#fef3c7; color:#92400e; }
    .ev-HITL_DECISION     { background:#fee2e2; color:#991b1b; }
    .ev-HUMAN_ALERT       { background:#ffedd5; color:#c2410c; }
    .ev-HUMAN_RESOLUTION  { background:#cffafe; color:#155e75; }
    .ev-SAFETY_GATE       { background:#f3f4f6; color:#374151; }
    .ev-PLAN_CREATED      { background:#faf5ff; color:#7c3aed; }
    .ev-HITL_TIMEOUT      { background:#fecaca; color:#7f1d1d; }

    /* ── RESOLUTIONS PANEL ── */
    .res-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
    .res-card {
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-left: 4px solid #3b82f6;
      border-radius: 0 12px 12px 0;
      padding: 1rem 1.25rem;
      box-shadow: 0 1px 3px rgba(0,0,0,.04);
    }
    .res-card .failure-type { font-weight: 700; font-size: 0.9rem; color: #1e293b; margin-bottom: 0.4rem; }
    .res-card .meta { font-size: 0.78rem; color: #64748b; margin-bottom: 0.5rem; }
    .res-card .fix  { font-size: 0.82rem; background: #f1f5f9; border-radius: 6px; padding: 0.5rem 0.65rem; color: #334155; word-break: break-all; }

    /* ── EMPTY STATE ── */
    .empty { text-align: center; padding: 3rem; color: #94a3b8; font-size: 0.9rem; }

    /* ── FOOTER ── */
    footer { text-align: center; padding: 2rem; font-size: 0.75rem; color: #94a3b8; }
  </style>
</head>
<body>
<nav>
  <div class="brand">⚙️ K8s<span>Whisperer</span></div>
  <div class="refresh">Auto-refresh every 15s · {{ now }}</div>
</nav>

<main>

  <!-- Summary Cards -->
  <div class="cards">
    <div class="card blue">
      <div class="label">Total Events</div>
      <div class="value">{{ stats.total }}</div>
      <div class="sub">All audit log entries</div>
    </div>
    <div class="card green">
      <div class="label">Auto-Resolved</div>
      <div class="value">{{ stats.auto }}</div>
      <div class="sub">No human needed</div>
    </div>
    <div class="card amber">
      <div class="label">Human Decisions</div>
      <div class="value">{{ stats.hitl }}</div>
      <div class="sub">HITL approvals + rejections</div>
    </div>
    <div class="card red">
      <div class="label">Human Alerts</div>
      <div class="value">{{ stats.alerts }}</div>
      <div class="sub">alert_human triggered</div>
    </div>
    <div class="card">
      <div class="label">Resolutions Logged</div>
      <div class="value">{{ stats.resolutions }}</div>
      <div class="sub">In HITL memory bank</div>
    </div>
  </div>

  <!-- Past Human Resolutions (Memory Bank) -->
  {% if resolutions %}
  <div class="section-header">📖 HITL Memory Bank — Past Human Resolutions</div>
  <div class="res-grid">
    {% for r in resolutions %}
    <div class="res-card">
      <div class="failure-type">{{ r.failure_type }}</div>
      <div class="meta">
        Pod: <strong>{{ r.pod_name }}</strong> ·
        {{ r.timestamp[:19]|replace('T',' ') }} ·
        Fixed in {{ r.duration_minutes }} min
      </div>
      <div class="fix">{{ r.fix_applied or '(no fix recorded)' }}</div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  <!-- Audit Log -->
  <div class="section-header">📋 Audit Log (newest first)</div>
  <div class="table-wrap">
    {% if log %}
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Event Type</th>
          <th>Pod</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        {% for entry in log %}
        <tr>
          <td style="white-space:nowrap;color:#94a3b8;">{{ entry.timestamp[:19]|replace('T',' ') }}</td>
          <td><span class="ev ev-{{ entry.event_type }}">{{ entry.event_type }}</span></td>
          <td><code>{{ entry.data.get('pod', entry.data.get('pod_name', '—')) }}</code></td>
          <td>{{ entry | format_data | safe }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div class="empty">No events logged yet. Start the agent to begin monitoring.</div>
    {% endif %}
  </div>

</main>
<footer>K8sWhisperer · Human-in-the-Loop Kubernetes Healing Agent</footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Template filter
# ---------------------------------------------------------------------------

@app.template_filter("format_data")
def format_data(entry: dict) -> str:
    """Render the most useful fields from an audit entry's data dict."""
    d = entry.get("data", {})
    parts = []

    field_order = [
        "failure_type", "action", "blast_radius", "severity",
        "confidence", "decision", "approved", "root_cause",
        "fix_command", "fix_applied", "output", "healthy",
        "duration_minutes", "reason",
    ]
    for k in field_order:
        if k in d:
            v = d[k]
            if isinstance(v, bool):
                v = "✓ Yes" if v else "✗ No"
            elif isinstance(v, float):
                v = f"{v:.2f}"
            elif isinstance(v, str) and len(v) > 80:
                v = v[:77] + "…"
            parts.append(f"<b>{k}:</b> {v}")

    return "  ·  ".join(parts) if parts else "—"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    raw_log = get_audit_log()
    # Newest first
    log = list(reversed(raw_log))

    # Summary stats
    stats = {
        "total": len(raw_log),
        "auto": sum(1 for e in raw_log if e.get("event_type") == "AUTO_EXECUTE"),
        "hitl": sum(1 for e in raw_log if e.get("event_type") == "HITL_DECISION"),
        "alerts": sum(1 for e in raw_log if e.get("event_type") == "HUMAN_ALERT"),
        "resolutions": sum(1 for e in raw_log if e.get("event_type") == "HUMAN_RESOLUTION"),
    }

    # Collect all unique failure types that have human resolutions
    resolution_failure_types = set()
    resolutions = []
    for entry in reversed(raw_log):
        if entry.get("event_type") == "HUMAN_RESOLUTION":
            d = entry.get("data", {})
            ft = d.get("failure_type", "Unknown")
            resolutions.append({
                "failure_type": ft,
                "pod_name": d.get("pod_name", "—"),
                "timestamp": entry.get("timestamp", ""),
                "fix_applied": d.get("fix_applied", ""),
                "duration_minutes": d.get("duration_minutes", 0),
            })

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    return render_template_string(
        _TEMPLATE,
        log=log,
        stats=stats,
        resolutions=resolutions,
        now=now,
    )


@app.route("/api/log")
def api_log():
    from flask import jsonify
    return jsonify(get_audit_log())


@app.route("/api/stats")
def api_stats():
    from flask import jsonify
    raw = get_audit_log()
    return jsonify({
        "total": len(raw),
        "by_type": {
            et: sum(1 for e in raw if e.get("event_type") == et)
            for et in set(e.get("event_type") for e in raw)
        }
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_dashboard():
    """Start the dashboard in a background thread."""
    import threading
    t = threading.Thread(
        target=lambda: app.run(host=HOST, port=PORT, debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()
    print(f"[DASHBOARD] Running at http://{HOST}:{PORT}")
    return t


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=True)
