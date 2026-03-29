# agent/logger.py
import json
import datetime
import os

LOG_FILE = "audit_log.json"

def log_event(event_type: str, pod: str, summary: str, data: dict, stellar_tx_hash: str = ""):
    entry = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "event_type": event_type,
        "pod": pod,
        "summary": summary,
        "data": data,
        "stellar_tx_hash": stellar_tx_hash,
    }

    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            try:
                logs = json.load(f)
            except json.JSONDecodeError:
                logs = []

    logs.append(entry)

    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)

    print(f"[AUDIT] {entry['timestamp']} | {event_type} | {pod} | {summary}")
    return entry


def get_audit_log():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []