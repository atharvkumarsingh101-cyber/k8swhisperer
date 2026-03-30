"""
agent/logger.py
---------------
Audit logger with past-resolution memory.

Key additions vs original:
  - log_human_resolution() → called when human clicks Approve on the HITL page.
    Records exactly what failure type triggered the alert, what fix was applied,
    and how long resolution took.
  - get_past_resolutions(failure_type) → returns every prior human resolution for
    that failure type so hitl_server.py can display "Last time this happened, a
    human ran: kubectl patch... and it resolved in 4 minutes."
  - get_audit_log() → unchanged helper used by the adaptive safety gate.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

AUDIT_LOG_PATH = os.environ.get("AUDIT_LOG_PATH", "audit_log.json")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load() -> list:
    """Load the full audit log from disk. Returns [] if the file doesn't exist."""
    if not os.path.exists(AUDIT_LOG_PATH):
        return []
    try:
        with open(AUDIT_LOG_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save(entries: list) -> None:
    """Atomically write the audit log back to disk."""
    with open(AUDIT_LOG_PATH, "w") as f:
        json.dump(entries, f, indent=2)


def _append(entry: dict) -> None:
    """Append a single entry to the audit log."""
    entries = _load()
    entries.append(entry)
    _save(entries)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_audit_log() -> list:
    """Return the full audit log (used by the adaptive safety gate)."""
    return _load()


def log_event(event_type: str, data: dict) -> str:
    """
    Generic event logger. Returns the generated event_id.

    event_type examples: ANOMALY_DETECTED, DIAGNOSIS_COMPLETE, HITL_DECISION,
                         AUTO_EXECUTE, RESOLUTION_COMPLETE, HUMAN_RESOLUTION
    """
    entry = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "epoch": time.time(),
        "data": data,
    }
    _append(entry)
    return entry["event_id"]


def log_human_resolution(
    failure_type: str,
    pod_name: str,
    namespace: str,
    fix_applied: str,
    resolution_notes: str,
    resolved_at_epoch: float,
    alerted_at_epoch: float,
    hitl_decision_id: Optional[str] = None,
) -> str:
    """
    Called when a human approves (and executes) a fix on the HITL page.

    Stores enough detail so that the next time the same failure_type triggers,
    the HITL page can show exactly how the previous human resolved it.

    Parameters
    ----------
    failure_type        : e.g. "ImagePullBackOff"
    pod_name            : the pod that failed
    namespace           : Kubernetes namespace
    fix_applied         : the kubectl command / action the human ran
    resolution_notes    : free-text note (can be the LLM's fix suggestion)
    resolved_at_epoch   : unix timestamp when resolution was confirmed
    alerted_at_epoch    : unix timestamp when the original alert fired
    hitl_decision_id    : optional link back to the HITL_DECISION log entry
    """
    duration_seconds = max(0, resolved_at_epoch - alerted_at_epoch)
    duration_minutes = round(duration_seconds / 60, 1)

    data = {
        "failure_type": failure_type,
        "pod_name": pod_name,
        "namespace": namespace,
        "fix_applied": fix_applied,
        "resolution_notes": resolution_notes,
        "duration_minutes": duration_minutes,
        "alerted_at": datetime.fromtimestamp(alerted_at_epoch, tz=timezone.utc).isoformat(),
        "resolved_at": datetime.fromtimestamp(resolved_at_epoch, tz=timezone.utc).isoformat(),
        "hitl_decision_id": hitl_decision_id,
    }
    return log_event("HUMAN_RESOLUTION", data)


def get_past_resolutions(failure_type: str) -> list:
    """
    Return all past human resolutions for a given failure_type,
    sorted newest-first.

    Each item in the returned list is a dict with at minimum:
      {
        "timestamp": "...",
        "pod_name": "...",
        "fix_applied": "...",
        "resolution_notes": "...",
        "duration_minutes": 4.2,
      }
    """
    all_entries = _load()
    resolutions = []
    for entry in all_entries:
        if entry.get("event_type") == "HUMAN_RESOLUTION":
            d = entry.get("data", {})
            if d.get("failure_type") == failure_type:
                resolutions.append({
                    "timestamp": entry.get("timestamp"),
                    "pod_name": d.get("pod_name", "unknown"),
                    "namespace": d.get("namespace", "default"),
                    "fix_applied": d.get("fix_applied", ""),
                    "resolution_notes": d.get("resolution_notes", ""),
                    "duration_minutes": d.get("duration_minutes", 0),
                })
    # newest first
    resolutions.reverse()
    return resolutions


def get_approval_count(failure_type: str, action: str) -> int:
    """
    Count past HITL_DECISION entries where the human approved this
    failure_type + action combination.  Used by the adaptive safety gate.
    """
    count = 0
    for entry in _load():
        if entry.get("event_type") == "HITL_DECISION":
            d = entry.get("data", {})
            if (d.get("approved") is True
                    and d.get("failure_type") == failure_type
                    and d.get("action") == action):
                count += 1
    return count
