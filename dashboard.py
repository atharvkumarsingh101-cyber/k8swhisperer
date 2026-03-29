from flask import Flask, render_template_string
from agent.logger import get_audit_log

app = Flask(__name__)

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>K8sWhisperer Dashboard</title>
    <meta http-equiv="refresh" content="10">
    <style>
        body { font-family: Arial, sans-serif; background: #0f0f1a; color: #e0e0e0; padding: 20px; }
        h1 { color: #a78bfa; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th { background: #1a1a2e; color: #a78bfa; padding: 12px; text-align: left; }
        td { padding: 10px; border-bottom: 1px solid #2d2d4e; font-size: 13px; }
        tr:hover { background: #1a1a2e; }
        .DETECT { color: #60a5fa; }
        .DIAGNOSE { color: #f59e0b; }
        .SAFETY_GATE { color: #a78bfa; }
        .HITL_REQUESTED { color: #f97316; }
        .HITL_DECISION { color: #34d399; }
        .POD_RESTARTED { color: #f87171; }
        .REMEDIATION_SUCCESS { color: #86efac; }
        .CYCLE_COMPLETE { color: #6ee7b7; }
        .badge { padding: 3px 8px; border-radius: 10px; font-size: 11px; background: #2d2d4e; }
    </style>
</head>
<body>
    <h1>⚡ K8sWhisperer Audit Dashboard</h1>
    <p style="color:#6b7280">Auto-refreshes every 10 seconds | {{ logs|length }} events</p>
    <table>
        <tr>
            <th>Timestamp</th>
            <th>Event</th>
            <th>Pod</th>
            <th>Summary</th>
        </tr>
        {% for log in logs|reverse %}
        <tr>
            <td style="color:#6b7280;font-size:11px">{{ log.timestamp }}</td>
            <td><span class="badge {{ log.event_type }}">{{ log.event_type }}</span></td>
            <td>{{ log.pod }}</td>
            <td>{{ log.summary }}</td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

@app.route("/")
def index():
    logs = get_audit_log()
    return render_template_string(TEMPLATE, logs=logs)

if __name__ == "__main__":
    import webbrowser
    webbrowser.open("http://localhost:5050")
    app.run(port=5050, debug=False)