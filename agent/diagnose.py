# agent/diagnose.py
import os
import re
from groq import Groq
from agent.state import Anomaly
from agent.monitor import get_pod_logs, describe_pod

client = Groq(api_key=os.environ["GROQ_API_KEY"])

DIAGNOSIS_PROMPT = """You are a Kubernetes SRE expert. Analyze the following pod failure and provide a structured diagnosis.

POD NAME: {pod}
NAMESPACE: {namespace}
FAILURE TYPE: {failure_type}
SEVERITY: {severity}
MESSAGE: {message}
RESTART COUNT: {restart_count}

--- POD LOGS ---
{logs}

--- POD DESCRIBE ---
{describe}

Respond EXACTLY in this format, no extra text:
ROOT_CAUSE: <one sentence explaining why this is failing>
FIX: <specific kubectl command or config change to fix it>
SEVERITY: <LOW|MEDIUM|HIGH|CRITICAL>
CONFIDENCE: <float between 0.0 and 1.0>
EXPLANATION: <2-3 sentences of detailed explanation for the human operator>
"""

def diagnose_anomaly(anomaly: Anomaly) -> dict:
    logs = get_pod_logs(anomaly.pod, anomaly.namespace)
    describe = describe_pod(anomaly.pod, anomaly.namespace)

    prompt = DIAGNOSIS_PROMPT.format(
        pod=anomaly.pod,
        namespace=anomaly.namespace,
        failure_type=anomaly.failure_type,
        severity=anomaly.severity,
        message=anomaly.message,
        restart_count=anomaly.restart_count,
        logs=logs[:3000] if logs else "No logs available",
        describe=describe[:3000] if describe else "No describe output available",
    )

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
    )

    raw = response.choices[0].message.content
    return _parse_diagnosis(raw, anomaly)


def _parse_diagnosis(raw: str, anomaly: Anomaly) -> dict:
    def extract(field):
        pattern = rf"{field}:\s*(.+)"
        match = re.search(pattern, raw, re.IGNORECASE)
        return match.group(1).strip() if match else "Unknown"

    try:
        confidence = float(extract("CONFIDENCE"))
    except ValueError:
        confidence = anomaly.confidence

    return {
        "pod": anomaly.pod,
        "namespace": anomaly.namespace,
        "failure_type": anomaly.failure_type,
        "root_cause": extract("ROOT_CAUSE"),
        "fix": extract("FIX"),
        "severity": extract("SEVERITY"),
        "confidence": confidence,
        "explanation": extract("EXPLANATION"),
        "raw_response": raw,
    }