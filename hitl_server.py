# hitl_server.py
import threading
import uvicorn
import webbrowser
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from agent.logger import log_event

app = FastAPI()

# Shared state between graph and web server
_pending = {}       # thread_id -> approval_data
_decisions = {}     # thread_id -> True/False
_events = {}        # thread_id -> threading.Event


def request_approval(thread_id: str, data: dict) -> bool:
    """Called by hitl_wait_node. Blocks until human decides."""
    _pending[thread_id] = data
    _events[thread_id] = threading.Event()

    # Open browser automatically
    webbrowser.open(f"http://localhost:8000/approve/{thread_id}")

    # Block here until human clicks Approve or Reject
    _events[thread_id].wait(timeout=300)  # 5 min timeout

    decision = _decisions.get(thread_id, False)

    # Cleanup
    _pending.pop(thread_id, None)
    _decisions.pop(thread_id, None)
    _events.pop(thread_id, None)

    return decision


@app.get("/approve/{thread_id}", response_class=HTMLResponse)
def approval_page(thread_id: str):
    data = _pending.get(thread_id)
    if not data:
        return HTMLResponse("<h2>No pending approval found.</h2>", status_code=404)

    html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>K8sWhisperer — HITL Approval</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            background: #0f0f1a;
            color: #e0e0e0;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }}
        .card {{
            background: #1a1a2e;
            border: 1px solid #7c3aed;
            border-radius: 12px;
            padding: 40px;
            max-width: 560px;
            width: 100%;
            box-shadow: 0 0 30px rgba(124,58,237,0.3);
        }}
        h1 {{ color: #a78bfa; margin-top: 0; }}
        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: bold;
            margin-bottom: 20px;
        }}
        .HIGH {{ background: #7f1d1d; color: #fca5a5; }}
        .MEDIUM {{ background: #78350f; color: #fcd34d; }}
        .LOW {{ background: #14532d; color: #86efac; }}
        .CRITICAL {{ background: #4c0519; color: #f9a8d4; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        td {{ padding: 10px; border-bottom: 1px solid #2d2d4e; }}
        td:first-child {{ color: #a78bfa; font-weight: bold; width: 140px; }}
        .buttons {{ display: flex; gap: 16px; margin-top: 24px; }}
        .btn {{
            flex: 1;
            padding: 14px;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            transition: opacity 0.2s;
        }}
        .btn:hover {{ opacity: 0.85; }}
        .approve {{ background: #16a34a; color: white; }}
        .reject  {{ background: #dc2626; color: white; }}
        .result  {{ text-align: center; font-size: 20px; margin-top: 20px; display: none; }}
    </style>
</head>
<body>
<div class="card">
    <h1>⚠️ K8sWhisperer — Approval Required</h1>
    <span class="badge {data.get('blast_radius','LOW')}">{data.get('blast_radius','?')} BLAST RADIUS</span>
    <table>
        <tr><td>Pod</td><td>{data.get('pod','?')}</td></tr>
        <tr><td>Action</td><td>{data.get('action','?')}</td></tr>
        <tr><td>Diagnosis</td><td>{data.get('diagnosis','?')}</td></tr>
        <tr><td>Blast Radius</td><td>{data.get('blast_radius','?')}</td></tr>
    </table>
    <div class="buttons">
        <button class="btn approve" onclick="decide('approve')">✅ Approve</button>
        <button class="btn reject"  onclick="decide('reject')">❌ Reject</button>
    </div>
    <div class="result" id="result"></div>
</div>
<script>
function decide(choice) {{
    fetch('/decide/{thread_id}/' + choice, {{method: 'POST'}})
        .then(r => r.json())
        .then(d => {{
            document.querySelector('.buttons').style.display = 'none';
            const r = document.getElementById('result');
            r.style.display = 'block';
            r.innerHTML = choice === 'approve'
                ? '✅ <strong>Approved!</strong> Agent is executing...'
                : '❌ <strong>Rejected.</strong> Action cancelled.';
            r.style.color = choice === 'approve' ? '#86efac' : '#fca5a5';
        }});
}}
</script>
</body>
</html>
"""
    return HTMLResponse(html)


@app.post("/decide/{thread_id}/{choice}")
def decide(thread_id: str, choice: str):
    approved = choice == "approve"
    _decisions[thread_id] = approved
    log_event("HITL_DECISION", thread_id,
              "APPROVED" if approved else "REJECTED", {"choice": choice})
    if thread_id in _events:
        _events[thread_id].set()
    return {"status": "ok", "approved": approved}


def start_server():
    """Start FastAPI in a background thread."""
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


def start_server_background():
    t = threading.Thread(target=start_server, daemon=True)
    t.start()