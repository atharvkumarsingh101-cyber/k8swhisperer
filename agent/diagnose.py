"""
agent/diagnose.py
-----------------
Two-LLM diagnosis pipeline.

Primary   → Groq  llama-3.3-70b-versatile   (same as before)
Verifier  → Groq  mixtral-8x7b-32768         (second independent call)

The verifier sees the PRIMARY diagnosis as context and is asked:
  "Do you agree? If not, what would you change?"

Both results are returned inside the Diagnosis object.  The HITL server
reads both and shows them side-by-side so a human can see whether the two
models agree before approving a high-risk action.
"""

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from groq import Groq

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Diagnosis:
    pod_name: str
    namespace: str
    failure_type: str

    # Primary LLM output
    root_cause: str = ""
    fix_suggestion: str = ""
    severity: str = "MEDIUM"
    confidence: float = 0.5
    explanation: str = ""

    # Cross-verification LLM output
    verifier_agrees: Optional[bool] = None          # True / False / None
    verifier_root_cause: str = ""
    verifier_fix_suggestion: str = ""
    verifier_confidence: float = 0.0
    verifier_notes: str = ""                        # disagreement details

    # Raw context fed to LLMs (for audit)
    pod_logs: str = ""
    pod_describe: str = ""


# ---------------------------------------------------------------------------
# kubectl helpers
# ---------------------------------------------------------------------------

def _run(cmd: list, timeout: int = 15) -> str:
    """Run a kubectl command and return stdout (truncated to 4 000 chars)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        out = result.stdout.strip() or result.stderr.strip()
        # Truncate to avoid hitting LLM context limits
        if len(out) > 4000:
            out = out[:2000] + "\n...[truncated]...\n" + out[-1000:]
        return out
    except subprocess.TimeoutExpired:
        return "[kubectl timed out]"
    except Exception as exc:
        return f"[kubectl error: {exc}]"


def fetch_pod_context(pod_name: str, namespace: str) -> tuple[str, str]:
    """Return (logs, describe) for a pod."""
    logs = _run(["kubectl", "logs", pod_name, "-n", namespace,
                 "--tail=100", "--previous"], timeout=10)
    if not logs or "[kubectl" in logs:
        # Pod may not have a previous container — try current
        logs = _run(["kubectl", "logs", pod_name, "-n", namespace,
                     "--tail=100"], timeout=10)

    describe = _run(["kubectl", "describe", "pod", pod_name,
                     "-n", namespace], timeout=10)
    return logs, describe


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

_client: Optional[Groq] = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client


_PRIMARY_MODEL  = "llama-3.3-70b-versatile"
_VERIFIER_MODEL = "mixtral-8x7b-32768"


def _primary_prompt(pod: str, failure: str, logs: str, describe: str) -> str:
    return f"""You are a senior Kubernetes reliability engineer.
A pod has failed and you must diagnose it precisely.

Pod Name   : {pod}
Failure    : {failure}
--- kubectl logs (last 100 lines) ---
{logs}
--- kubectl describe ---
{describe}

Respond EXACTLY in this format (no extra text):
ROOT_CAUSE: <one concise sentence>
FIX: <exact kubectl command or human action needed>
SEVERITY: LOW|MEDIUM|HIGH|CRITICAL
CONFIDENCE: <float 0.0–1.0>
EXPLANATION: <two sentences of reasoning>
"""


def _verifier_prompt(
    pod: str, failure: str, logs: str, describe: str,
    primary_root_cause: str, primary_fix: str,
    primary_severity: str, primary_confidence: str,
) -> str:
    return f"""You are a SECOND senior Kubernetes reliability engineer performing
an independent cross-check of a colleague's diagnosis.

Pod Name   : {pod}
Failure    : {failure}
--- kubectl logs (last 100 lines) ---
{logs}
--- kubectl describe ---
{describe}

Your colleague concluded:
  ROOT_CAUSE : {primary_root_cause}
  FIX        : {primary_fix}
  SEVERITY   : {primary_severity}
  CONFIDENCE : {primary_confidence}

Do you agree? Think independently, then respond EXACTLY in this format:
AGREE: YES|NO|PARTIAL
ROOT_CAUSE: <your own root cause — can match colleague if you agree>
FIX: <your own fix suggestion>
CONFIDENCE: <float 0.0–1.0>
NOTES: <one sentence explaining agreement or disagreement>
"""


def _parse_primary(text: str) -> dict:
    def extract(pattern, default=""):
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else default

    raw_conf = extract(r"CONFIDENCE:\s*([0-9.]+)", "0.5")
    try:
        confidence = float(raw_conf)
    except ValueError:
        confidence = 0.5

    return {
        "root_cause": extract(r"ROOT_CAUSE:\s*(.+)"),
        "fix_suggestion": extract(r"FIX:\s*(.+)"),
        "severity": extract(r"SEVERITY:\s*(LOW|MEDIUM|HIGH|CRITICAL)", "MEDIUM"),
        "confidence": min(1.0, max(0.0, confidence)),
        "explanation": extract(r"EXPLANATION:\s*(.+)"),
    }


def _parse_verifier(text: str) -> dict:
    def extract(pattern, default=""):
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else default

    agree_raw = extract(r"AGREE:\s*(YES|NO|PARTIAL)", "PARTIAL").upper()
    agrees = True if agree_raw == "YES" else (False if agree_raw == "NO" else None)

    raw_conf = extract(r"CONFIDENCE:\s*([0-9.]+)", "0.5")
    try:
        confidence = float(raw_conf)
    except ValueError:
        confidence = 0.5

    return {
        "verifier_agrees": agrees,
        "verifier_root_cause": extract(r"ROOT_CAUSE:\s*(.+)"),
        "verifier_fix_suggestion": extract(r"FIX:\s*(.+)"),
        "verifier_confidence": min(1.0, max(0.0, confidence)),
        "verifier_notes": extract(r"NOTES:\s*(.+)"),
    }


def _call_llm(model: str, prompt: str, retries: int = 2) -> str:
    """Call Groq with exponential backoff on rate-limit errors."""
    client = _get_client()
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.1,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                return f"[LLM error: {exc}]"
    return "[LLM error: exhausted retries]"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def diagnose(pod_name: str, namespace: str, failure_type: str) -> Diagnosis:
    """
    Run full two-LLM diagnosis for a failed pod.

    Returns a Diagnosis object with both primary and verifier fields populated.
    Called from agent/executor.py in the diagnose_node.
    """
    diag = Diagnosis(
        pod_name=pod_name,
        namespace=namespace,
        failure_type=failure_type,
    )

    # 1. Gather raw cluster context
    logs, describe = fetch_pod_context(pod_name, namespace)
    diag.pod_logs = logs
    diag.pod_describe = describe

    # 2. Primary LLM call (LLaMA 3.3 70b)
    primary_raw = _call_llm(
        _PRIMARY_MODEL,
        _primary_prompt(pod_name, failure_type, logs, describe),
    )
    # Detect LLM error — mark confidence 0.0 so callers know the result is unreliable
    if primary_raw.startswith("[LLM error"):
        diag.root_cause     = "LLM unavailable"
        diag.fix_suggestion = ""
        diag.severity       = "MEDIUM"
        diag.confidence     = 0.0
        diag.explanation    = primary_raw
        return diag

    parsed = _parse_primary(primary_raw)
    diag.root_cause     = parsed["root_cause"]
    diag.fix_suggestion = parsed["fix_suggestion"]
    diag.severity       = parsed["severity"]
    diag.confidence     = parsed["confidence"]
    diag.explanation    = parsed["explanation"]

    # 3. Cross-verification LLM call (Mixtral 8x7b)
    verifier_raw = _call_llm(
        _VERIFIER_MODEL,
        _verifier_prompt(
            pod_name, failure_type, logs, describe,
            diag.root_cause, diag.fix_suggestion,
            diag.severity, str(diag.confidence),
        ),
    )
    vparsed = _parse_verifier(verifier_raw)
    diag.verifier_agrees         = vparsed["verifier_agrees"]
    diag.verifier_root_cause     = vparsed["verifier_root_cause"]
    diag.verifier_fix_suggestion = vparsed["verifier_fix_suggestion"]
    diag.verifier_confidence     = vparsed["verifier_confidence"]
    diag.verifier_notes          = vparsed["verifier_notes"]

    return diag
