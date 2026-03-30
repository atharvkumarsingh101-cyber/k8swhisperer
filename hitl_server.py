"""
hitl_server.py
--------------
Human-in-the-Loop approval server for K8sWhisperer.

Routes:
  GET  /               → Full HITL dashboard (all pending + history)
  GET  /health         → {"status": "ok", "pending": N}
  GET  /pending        → JSON list of pending decisions
  GET  /hitl/<token>   → Approve/Reject page for a specific alert
  POST /decide         → {"token": ..., "approved": true/false}
  POST /inject_test    → Inject a fake alert for demo/testing
"""

import os
import secrets
import threading
import time
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template_string, request

try:
    from agent.logger import log_event, get_past_resolutions
except Exception:
    def log_event(event_type, data=None): pass
    def get_past_resolutions(ft): return []

app  = Flask(__name__)
HOST = os.environ.get("HITL_HOST", "127.0.0.1")
PORT = int(os.environ.get("HITL_PORT", "5051"))

# In-memory store: token → entry dict
_pending: dict = {}
_history: list = []   # decided entries (for the dashboard history panel)


# ---------------------------------------------------------------------------
# Dashboard template
# ---------------------------------------------------------------------------

_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <meta http-equiv="refresh" content="10"/>
  <title>K8sWhisperer — HITL Dashboard</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           background: #f8fafc; color: #1e293b; min-height: 100vh; }

    nav { background: #ffffff; border-bottom: 1px solid #e2e8f0;
          padding: 0 2rem; height: 56px;
          display: flex; align-items: center; justify-content: space-between;
          box-shadow: 0 1px 3px rgba(0,0,0,.06); }
    nav .brand { font-weight: 800; font-size: 1.1rem; color: #0f172a; }
    nav .brand span { color: #f59e0b; }
    nav .live { font-size: 0.75rem; color: #94a3b8; }
    nav .live span { display: inline-block; width: 8px; height: 8px;
                     border-radius: 50%; background: #22c55e;
                     margin-right: 5px; animation: pulse 1.5s infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

    main { max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem; }

    /* ── SUMMARY CARDS ── */
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr));
             gap: 1rem; margin-bottom: 2rem; }
    .card { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px;
            padding: 1.25rem 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,.04); }
    .card .label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: .07em;
                   color: #94a3b8; font-weight: 600; margin-bottom: 0.4rem; }
    .card .value { font-size: 2rem; font-weight: 800; }
    .card.amber .value { color: #d97706; }
    .card.green .value { color: #16a34a; }
    .card.red   .value { color: #dc2626; }
    .card.blue  .value { color: #2563eb; }

    /* ── SECTION HEADER ── */
    .sh { font-size: 1rem; font-weight: 700; color: #0f172a;
          margin: 1.5rem 0 0.75rem; display: flex; align-items: center; gap: 0.5rem; }

    /* ── ALERT CARDS ── */
    .alert-grid { display: flex; flex-direction: column; gap: 1rem; margin-bottom: 2rem; }
    .alert-card { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px;
                  padding: 1.5rem; position: relative; overflow: hidden;
                  box-shadow: 0 1px 3px rgba(0,0,0,.04); }
    .alert-card::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0;
                          width: 4px; background: #f59e0b; }
    .alert-card.critical::before { background: #dc2626; }
    .alert-card.low::before      { background: #16a34a; }

    .alert-header { display: flex; align-items: center; justify-content: space-between;
                    margin-bottom: 1rem; flex-wrap: wrap; gap: 0.5rem; }
    .alert-title { font-size: 1rem; font-weight: 700; color: #0f172a; }
    .badges { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .badge { display: inline-block; padding: 2px 10px; border-radius: 20px;
             font-size: 0.72rem; font-weight: 700; }
    .badge-amber    { background: #fef3c7; color: #92400e; }
    .badge-red      { background: #fee2e2; color: #991b1b; }
    .badge-green    { background: #dcfce7; color: #166534; }
    .badge-blue     { background: #dbeafe; color: #1e40af; }
    .badge-purple   { background: #ede9fe; color: #6d28d9; }

    .alert-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;
                    margin-bottom: 1rem; }
    @media(max-width:640px) { .alert-grid-2 { grid-template-columns: 1fr; } }

    .diag-box { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
                padding: 0.85rem 1rem; }
    .diag-box .diag-label { font-size: 0.7rem; text-transform: uppercase;
                            letter-spacing: .07em; color: #2563eb; font-weight: 700;
                            margin-bottom: 0.4rem; }
    .diag-box p { font-size: 0.85rem; color: #475569; line-height: 1.5; }
    .diag-box code { font-size: 0.8rem; color: #166534; background: #dcfce7;
                     padding: 2px 6px; border-radius: 4px; word-break: break-all; }

    .btn-row { display: flex; gap: 1rem; justify-content: flex-end; margin-top: 1rem; }
    .btn { padding: 0.6rem 2rem; border: none; border-radius: 8px; font-size: 0.9rem;
           font-weight: 700; cursor: pointer; transition: opacity .15s; }
    .btn:hover { opacity: 0.85; }
    .btn-approve { background: #16a34a; color: #fff; }
    .btn-reject  { background: #dc2626; color: #fff; }
    .btn-test    { background: #6366f1; color: #fff; padding: 0.55rem 1.5rem;
                   font-size: 0.85rem; }

    /* ── HISTORY TABLE ── */
    .table-wrap { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px;
                  overflow: hidden; margin-bottom: 2rem;
                  box-shadow: 0 1px 3px rgba(0,0,0,.04); }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    thead th { background: #f8fafc; padding: 10px 14px; text-align: left;
               font-size: 0.7rem; text-transform: uppercase; letter-spacing: .07em;
               color: #64748b; font-weight: 600; border-bottom: 1px solid #e2e8f0; }
    tbody td { padding: 10px 14px; border-bottom: 1px solid #f1f5f9; color: #475569; }
    tbody tr:last-child td { border-bottom: none; }
    tbody tr:hover { background: #f8fafc; }
    .decision-yes { color: #16a34a; font-weight: 700; }
    .decision-no  { color: #dc2626; font-weight: 700; }

    .empty { text-align: center; padding: 3rem; color: #94a3b8; font-size: 0.9rem; }
    footer { text-align: center; padding: 2rem; font-size: 0.75rem; color: #94a3b8; }
  </style>
</head>
<body>
<nav>
  <div class="brand">⚡ K8s<span>Whisperer</span> — HITL Console</div>
  <div class="live"><span></span>Auto-refresh 10s · {{ now }}</div>
</nav>

<main>

  <!-- Summary Cards -->
  <div class="cards">
    <div class="card amber">
      <div class="label">Pending Decisions</div>
      <div class="value">{{ pending | length }}</div>
    </div>
    <div class="card green">
      <div class="label">Approved Today</div>
      <div class="value">{{ approved_count }}</div>
    </div>
    <div class="card red">
      <div class="label">Rejected Today</div>
      <div class="value">{{ rejected_count }}</div>
    </div>
    <div class="card blue">
      <div class="label">Total Decisions</div>
      <div class="value">{{ history | length }}</div>
    </div>
  </div>

  <!-- Inject Test Alert -->
  <div class="sh">🧪 Inject Test Alert for Demo</div>
  <div style="display:flex;gap:0.75rem;flex-wrap:wrap;margin-bottom:2rem;">
    <button class="btn btn-test" onclick="inject('CrashLoopBackOff','restart_pod','MEDIUM')">💥 CrashLoopBackOff</button>
    <button class="btn btn-test" onclick="inject('ImagePullBackOff','alert_human','LOW')">🖼 ImagePullBackOff</button>
    <button class="btn btn-test" onclick="inject('OOMKilled','patch_memory','HIGH')">💾 OOMKilled</button>
    <button class="btn btn-test" onclick="inject('NodeNotReady','hitl_required','CRITICAL')">🔴 NodeNotReady</button>
  </div>

  <!-- Pending Alerts -->
  <div class="sh">🚨 Pending Human Decisions ({{ pending | length }})</div>
  <div class="alert-grid">
    {% if pending %}
      {% for entry in pending %}
      <div class="alert-card {{ 'critical' if entry.blast_radius in ('HIGH','CRITICAL') else 'low' if entry.blast_radius == 'LOW' else '' }}">
        <div class="alert-header">
          <div class="alert-title">{{ entry.failure_type }} · <code style="font-size:0.9rem;color:#94a3b8;">{{ entry.pod_name }}</code></div>
          <div class="badges">
            <span class="badge badge-amber">{{ entry.action }}</span>
            <span class="badge {{ 'badge-red' if entry.blast_radius in ('HIGH','CRITICAL') else 'badge-green' if entry.blast_radius == 'LOW' else 'badge-blue' }}">{{ entry.blast_radius }}</span>
            <span class="badge badge-purple">conf: {{ "%.0f"|format(entry.confidence * 100) }}%</span>
          </div>
        </div>

        <div class="alert-grid-2">
          <div class="diag-box">
            <div class="diag-label">🤖 Primary LLM (LLaMA 3.3)</div>
            <p>{{ entry.primary_root_cause or '—' }}</p>
            <br/>
            <code>{{ entry.primary_fix or '—' }}</code>
          </div>
          <div class="diag-box">
            <div class="diag-label">🔍 Verifier LLM (Mixtral) · {{ '✅ Agrees' if entry.verifier_agrees else '❌ Disagrees' if entry.verifier_agrees == false else '⚠️ Partial' }}</div>
            <p>{{ entry.verifier_root_cause or '—' }}</p>
            <br/>
            <code>{{ entry.verifier_fix or '—' }}</code>
          </div>
        </div>

        <div class="btn-row">
          <span style="font-size:0.78rem;color:#64748b;align-self:center;">
            ns: {{ entry.namespace }} · requested: {{ entry.requested_at[:19]|replace('T',' ') if entry.requested_at else '—' }}
          </span>
          <button class="btn btn-reject"  onclick="decide('{{ entry.token }}', false)">✗ Reject</button>
          <button class="btn btn-approve" onclick="decide('{{ entry.token }}', true)">✓ Approve</button>
        </div>
      </div>
      {% endfor %}
    {% else %}
      <div class="empty">✅ No pending decisions — agent is running autonomously</div>
    {% endif %}
  </div>

  <!-- Decision History -->
  <div class="sh">📋 Decision History</div>
  <div class="table-wrap">
    {% if history %}
    <table>
      <thead>
        <tr>
          <th>Time</th><th>Pod</th><th>Failure</th><th>Action</th><th>Blast</th><th>Decision</th>
        </tr>
      </thead>
      <tbody>
        {% for h in history|reverse %}
        <tr>
          <td style="white-space:nowrap;color:#475569;">{{ h.decided_at[:19]|replace('T',' ') if h.decided_at else '—' }}</td>
          <td><code style="color:#93c5fd;">{{ h.pod_name }}</code></td>
          <td>{{ h.failure_type }}</td>
          <td>{{ h.action }}</td>
          <td>{{ h.blast_radius }}</td>
          <td class="{{ 'decision-yes' if h.result else 'decision-no' }}">
            {{ '✓ APPROVED' if h.result else '✗ REJECTED' }}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
      <div class="empty">No decisions made yet.</div>
    {% endif %}
  </div>

</main>
<footer>K8sWhisperer · Human-in-the-Loop Kubernetes Healing Agent</footer>

<script>
async function decide(token, approved) {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = approved ? 'Approving…' : 'Rejecting…';
  try {
    const r = await fetch('/decide', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token, approved})
    });
    const d = await r.json();
    if (d.ok) location.reload();
    else alert('Error: ' + JSON.stringify(d));
  } catch(e) {
    alert('Request failed: ' + e);
    btn.disabled = false;
  }
}

async function inject(failure_type, action, blast_radius) {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = 'Injecting…';
  try {
    const r = await fetch('/inject_test', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({failure_type, action, blast_radius})
    });
    const d = await r.json();
    if (d.ok) location.reload();
    else alert('Error: ' + JSON.stringify(d));
  } catch(e) {
    alert('Failed: ' + e);
  } finally {
    btn.disabled = false;
    btn.textContent = failure_type;
  }
}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Individual HITL decision page (linked from agent terminal output)
# ---------------------------------------------------------------------------

_HITL_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>HITL Decision — {{ entry.failure_type }}</title>
  <style>
    body { font-family: system-ui, sans-serif; background:#f8fafc; color:#1e293b;
           display:flex; justify-content:center; align-items:flex-start;
           min-height:100vh; padding:2rem; }
    .card { background:#ffffff; border:1px solid #e2e8f0; border-radius:16px;
            padding:2rem; max-width:700px; width:100%;
            box-shadow: 0 4px 12px rgba(0,0,0,.06); }
    h1 { font-size:1.3rem; font-weight:800; color:#0f172a; margin-bottom:0.5rem; }
    .meta { font-size:0.82rem; color:#94a3b8; margin-bottom:1.5rem; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:1rem; margin-bottom:1.5rem; }
    .box { background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:1rem; }
    .box .lbl { font-size:0.68rem; text-transform:uppercase; letter-spacing:.07em;
                color:#2563eb; font-weight:700; margin-bottom:0.5rem; }
    .box p { font-size:0.85rem; color:#475569; line-height:1.5; }
    .box code { display:block; margin-top:0.5rem; font-size:0.8rem; color:#166534;
                background:#dcfce7; padding:6px 8px; border-radius:4px;
                word-break:break-all; }
    .btns { display:flex; gap:1rem; justify-content:flex-end; margin-top:1.5rem; }
    .btn { padding:0.7rem 2.5rem; border:none; border-radius:8px; font-size:1rem;
           font-weight:700; cursor:pointer; }
    .approve { background:#16a34a; color:#fff; }
    .reject  { background:#dc2626; color:#fff; }
    .badge { display:inline-block; padding:2px 10px; border-radius:20px;
             font-size:0.72rem; font-weight:700; margin-right:6px; }
    .b-red    { background:#fee2e2; color:#991b1b; }
    .b-amber  { background:#fef3c7; color:#92400e; }
    .b-green  { background:#dcfce7; color:#166534; }
    .b-purple { background:#ede9fe; color:#6d28d9; }
    .decided { text-align:center; padding:2rem; font-size:1.2rem; font-weight:700; }
    .decided.yes { color:#16a34a; } .decided.no { color:#dc2626; }
  </style>
</head>
<body>
<div class="card">
  <h1>🚨 Human Decision Required</h1>
  <div class="meta">
    Pod: <strong>{{ entry.pod_name }}</strong> ·
    Namespace: {{ entry.namespace }} ·
    Requested: {{ entry.requested_at[:19]|replace('T',' ') if entry.requested_at else '—' }}
  </div>

  <div style="margin-bottom:1rem;">
    <span class="badge b-amber">{{ entry.failure_type }}</span>
    <span class="badge b-amber">Action: {{ entry.action }}</span>
    <span class="badge {{ 'b-red' if entry.blast_radius in ('HIGH','CRITICAL') else 'b-green' if entry.blast_radius == 'LOW' else 'b-purple' }}">
      Blast: {{ entry.blast_radius }}
    </span>
    <span class="badge b-purple">Confidence: {{ "%.0f"|format(entry.confidence * 100) }}%</span>
  </div>

  <div class="row">
    <div class="box">
      <div class="lbl">🤖 Primary LLM — LLaMA 3.3 70b</div>
      <p>{{ entry.primary_root_cause or '—' }}</p>
      <code>{{ entry.primary_fix or '—' }}</code>
    </div>
    <div class="box">
      <div class="lbl">🔍 Verifier — Mixtral 8x7b · {{ '✅ Agrees' if entry.verifier_agrees else '❌ Disagrees' if entry.verifier_agrees == false else '⚠️ Partial' }}</div>
      <p>{{ entry.verifier_root_cause or '—' }}</p>
      <code>{{ entry.verifier_fix or '—' }}</code>
      {% if entry.verifier_notes %}
      <p style="margin-top:0.5rem;font-size:0.78rem;color:#94a3b8;">{{ entry.verifier_notes }}</p>
      {% endif %}
    </div>
  </div>

  {% if entry.result is none %}
  <div class="btns">
    <button class="btn reject"  onclick="decide(false)">✗ Reject</button>
    <button class="btn approve" onclick="decide(true)">✓ Approve</button>
  </div>
  {% else %}
  <div class="decided {{ 'yes' if entry.result else 'no' }}">
    {{ '✓ APPROVED' if entry.result else '✗ REJECTED' }} — Decision recorded.
    <br/><a href="/" style="color:#60a5fa;font-size:0.85rem;">← Back to dashboard</a>
  </div>
  {% endif %}
</div>

<script>
async function decide(approved) {
  const btns = document.querySelectorAll('.btn');
  btns.forEach(b => b.disabled = true);
  const r = await fetch('/decide', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({token: '{{ entry.token }}', approved})
  });
  const d = await r.json();
  if (d.ok) location.reload();
  else alert('Error: ' + JSON.stringify(d));
}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    pending = [e for e in _pending.values() if e["result"] is None]
    approved_count = sum(1 for h in _history if h["result"] is True)
    rejected_count = sum(1 for h in _history if h["result"] is False)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return render_template_string(
        _DASHBOARD,
        pending=pending,
        history=_history,
        approved_count=approved_count,
        rejected_count=rejected_count,
        now=now,
    )


@app.route("/health")
def health():
    pending_count = sum(1 for e in _pending.values() if e["result"] is None)
    return jsonify({"status": "ok", "pending": pending_count})


@app.route("/pending")
def pending_list():
    return jsonify([
        {k: v for k, v in e.items() if k != "event"}
        for e in _pending.values()
        if e["result"] is None
    ])


@app.route("/hitl/<token>")
def hitl_page(token):
    entry = _pending.get(token)
    if not entry:
        return f"<h2 style='font-family:sans-serif;padding:2rem;'>Token not found or expired.</h2>", 404
    return render_template_string(_HITL_PAGE, entry=entry)


@app.route("/decide", methods=["POST"])
def decide():
    data     = request.get_json(force=True) or {}
    token    = data.get("token", "")
    approved = bool(data.get("approved", False))
    entry    = _pending.get(token)
    if not entry:
        return jsonify({"ok": False, "error": "token not found"})
    entry["result"]     = approved
    entry["decided_at"] = datetime.now(timezone.utc).isoformat()
    entry.get("event") and entry["event"].set()
    _history.append({k: v for k, v in entry.items() if k != "event"})
    log_event("HITL_DECISION", {
        "pod": entry.get("pod_name"),
        "failure_type": entry.get("failure_type"),
        "action": entry.get("action"),
        "approved": approved,
    })
    return jsonify({"ok": True, "approved": approved})


@app.route("/inject_test", methods=["POST"])
def inject_test():
    """Inject a fake pending alert — for demos and testing."""
    data         = request.get_json(force=True) or {}
    token        = secrets.token_urlsafe(32)
    now          = datetime.now(timezone.utc).isoformat()
    failure_type = data.get("failure_type", "CrashLoopBackOff")
    action       = data.get("action", "restart_pod")
    blast        = data.get("blast_radius", "MEDIUM")

    _pending[token] = {
        "token":             token,
        "pod_name":          data.get("pod_name", f"demo-pod-{failure_type[:4].lower()}"),
        "namespace":         data.get("namespace", "default"),
        "failure_type":      failure_type,
        "action":            action,
        "blast_radius":      blast,
        "confidence":        data.get("confidence", 0.87),
        "primary_root_cause": data.get("primary_root_cause",
                               f"Container failing due to {failure_type}"),
        "primary_fix":       data.get("primary_fix",
                               f"kubectl delete pod demo-pod -n default"),
        "primary_severity":  "HIGH" if blast in ("HIGH","CRITICAL") else "MEDIUM",
        "verifier_agrees":   True,
        "verifier_root_cause": "Independent analysis confirms the same root cause.",
        "verifier_fix":      "Agreed — same remediation recommended.",
        "verifier_confidence": 0.83,
        "verifier_notes":    "",
        "alerted_at_epoch":  time.time(),
        "requested_at":      now,
        "event":             threading.Event(),
        "result":            None,
        "decided_at":        None,
    }
    return jsonify({"ok": True, "token": token,
                    "url": f"http://{HOST}:{PORT}/hitl/{token}"})


# ---------------------------------------------------------------------------
# request_approval() — called by the agent graph to block until human decides
# ---------------------------------------------------------------------------

def request_approval(pod_name: str, data: dict, timeout: int = 300) -> bool:
    """
    Register a pending approval request and block until the human decides
    (or timeout seconds pass, defaulting to reject on timeout).
    """
    token = secrets.token_urlsafe(32)
    now   = datetime.now(timezone.utc).isoformat()
    ev    = threading.Event()

    diag = data.get("diagnosis", {})
    if isinstance(diag, str):
        primary_root_cause = diag
        primary_fix        = data.get("fix_command", "")
    else:
        primary_root_cause = getattr(diag, "root_cause", str(diag))
        primary_fix        = getattr(diag, "fix_suggestion", "")

    _pending[token] = {
        "token":              token,
        "pod_name":           pod_name,
        "namespace":          data.get("namespace", "default"),
        "failure_type":       data.get("failure_type", "Unknown"),
        "action":             data.get("action", "unknown"),
        "blast_radius":       data.get("blast_radius", "MEDIUM"),
        "confidence":         data.get("confidence", 0.0),
        "primary_root_cause": primary_root_cause,
        "primary_fix":        primary_fix,
        "primary_severity":   data.get("severity", "MEDIUM"),
        "verifier_agrees":    data.get("verifier_agrees", None),
        "verifier_root_cause": data.get("verifier_root_cause", ""),
        "verifier_fix":       data.get("verifier_fix", ""),
        "verifier_confidence": data.get("verifier_confidence", 0.0),
        "verifier_notes":     data.get("verifier_notes", ""),
        "alerted_at_epoch":   time.time(),
        "requested_at":       now,
        "event":              ev,
        "result":             None,
        "decided_at":         None,
    }

    print(f"\n[HITL] Decision required → http://{HOST}:{PORT}/hitl/{token}")
    print(f"[HITL] Or open dashboard → http://{HOST}:{PORT}/\n")

    ev.wait(timeout=timeout)

    entry = _pending.get(token, {})
    approved = bool(entry.get("result", False))

    if not entry.get("decided_at"):
        print(f"[HITL] Timeout — defaulting to REJECT for {pod_name}")
        entry["result"]     = False
        entry["decided_at"] = datetime.now(timezone.utc).isoformat()
        _history.append({k: v for k, v in entry.items() if k != "event"})

    return approved


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_hitl_server():
    """Start HITL server in a background daemon thread."""
    import threading
    t = threading.Thread(
        target=lambda: app.run(host=HOST, port=PORT, debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()
    print(f"[HITL] Dashboard running at http://{HOST}:{PORT}")
    return t


if __name__ == "__main__":
    print(f"[HITL] Starting server at http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=True, use_reloader=True)
