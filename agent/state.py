# agent/state.py
from typing import TypedDict, List, Optional, Annotated
from dataclasses import dataclass, field
import datetime
import operator

@dataclass
class Anomaly:
    pod: str
    namespace: str
    failure_type: str
    severity: str
    confidence: float
    message: str
    restart_count: int = 0
    detected_at: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")

@dataclass
class RemediationPlan:
    action: str
    target_pod: str
    namespace: str
    parameters: dict
    confidence: float
    blast_radius: str

@dataclass
class LogEntry:
    timestamp: str
    event_type: str
    pod: str
    summary: str
    data: dict
    stellar_tx_hash: str = ""

class ClusterState(TypedDict):
    events: List[dict]
    anomalies: List[Anomaly]
    current_anomaly: Optional[Anomaly]
    diagnosis: str
    plan: Optional[RemediationPlan]
    approved: bool
    result: str
    audit_log: Annotated[List[dict], operator.add]