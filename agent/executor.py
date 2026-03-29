# agent/executor.py
import subprocess
import time
from agent.state import Anomaly, RemediationPlan
from agent.logger import log_event

# Anomaly types that execute automatically (no human approval needed)
AUTO_FIX = {"CrashLoopBackOff", "ImagePullBackOff", "Evicted", "PredictedCrashLoop"}

# Anomaly types that always require human approval
HITL_REQUIRED = {"OOMKilled", "NodeNotReady", "DeploymentStalled"}


def build_plan(anomaly: Anomaly, diagnosis: dict) -> RemediationPlan:
    """Build a remediation plan from anomaly + diagnosis."""

    if anomaly.failure_type in ("CrashLoopBackOff", "Evicted", "PredictedCrashLoop"):
        return RemediationPlan(
            action="restart_pod",
            target_pod=anomaly.pod,
            namespace=anomaly.namespace,
            parameters={"reason": diagnosis["root_cause"]},
            confidence=diagnosis["confidence"],
            blast_radius="LOW",
        )

    elif anomaly.failure_type == "ImagePullBackOff":
        return RemediationPlan(
            action="alert_human",
            target_pod=anomaly.pod,
            namespace=anomaly.namespace,
            parameters={"fix": diagnosis["fix"]},
            confidence=diagnosis["confidence"],
            blast_radius="LOW",
        )

    elif anomaly.failure_type == "OOMKilled":
        return RemediationPlan(
            action="restart_pod",
            target_pod=anomaly.pod,
            namespace=anomaly.namespace,
            parameters={"reason": "OOMKilled - needs memory limit increase after restart"},
            confidence=diagnosis["confidence"],
            blast_radius="MEDIUM",
        )

    elif anomaly.failure_type == "Pending":
        return RemediationPlan(
            action="explain_only",
            target_pod=anomaly.pod,
            namespace=anomaly.namespace,
            parameters={"explanation": diagnosis["explanation"]},
            confidence=diagnosis["confidence"],
            blast_radius="NONE",
        )

    elif anomaly.failure_type == "NodeNotReady":
        return RemediationPlan(
            action="alert_human",
            target_pod=anomaly.pod,
            namespace=anomaly.namespace,
            parameters={"explanation": diagnosis["explanation"]},
            confidence=diagnosis["confidence"],
            blast_radius="CRITICAL",
        )

    else:
        return RemediationPlan(
            action="explain_only",
            target_pod=anomaly.pod,
            namespace=anomaly.namespace,
            parameters={"explanation": diagnosis.get("explanation", "Unknown issue")},
            confidence=diagnosis["confidence"],
            blast_radius="UNKNOWN",
        )


def safety_gate(plan: RemediationPlan) -> bool:
    """
    Returns True if action can proceed automatically.
    Returns False if human approval is required.
    """
    # Always require HITL for critical blast radius
    if plan.blast_radius in ("CRITICAL", "HIGH"):
        return False

    # Always require HITL for specific anomaly actions
    if plan.action == "alert_human":
        return False

    # Confidence too low — require human
    if plan.confidence < 0.80:
        return False

    return True


def restart_pod(pod: str, namespace: str) -> bool:
    """Delete the pod so Kubernetes recreates it."""
    try:
        result = subprocess.run(
            ["kubectl", "delete", "pod", pod, "-n", namespace, "--ignore-not-found"],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[ERROR] Failed to delete pod: {e}")
        return False


def verify_pod_healthy(pod: str, namespace: str, retries: int = 12, interval: int = 10) -> bool:
    """Poll until pod is Running or timeout."""
    print(f"[VERIFY] Waiting for {pod} to become healthy...")
    for i in range(retries):
        try:
            result = subprocess.run(
                ["kubectl", "get", "pod", pod, "-n", namespace,
                 "-o", "jsonpath={.status.phase}"],
                capture_output=True, text=True, timeout=10
            )
            phase = result.stdout.strip()
            print(f"[VERIFY] Attempt {i+1}/{retries} — phase: {phase}")
            if phase == "Running":
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def execute_plan(plan: RemediationPlan) -> str:
    """Execute the remediation plan and return result string."""

    log_event("PLAN_CREATED", plan.target_pod,
              f"Action: {plan.action} | Blast: {plan.blast_radius}",
              {"action": plan.action, "parameters": plan.parameters,
               "confidence": plan.confidence})

    if plan.action == "explain_only":
        msg = f"[EXPLAIN] {plan.parameters.get('explanation', '')}"
        print(msg)
        log_event("EXPLAIN_ONLY", plan.target_pod, msg, plan.parameters)
        return "explained"

    if plan.action == "alert_human":
        msg = f"[ALERT] Human intervention required for {plan.target_pod}. Fix: {plan.parameters.get('fix') or plan.parameters.get('explanation')}"
        print(msg)
        log_event("HUMAN_ALERT", plan.target_pod, msg, plan.parameters)
        return "alerted"

    if plan.action == "restart_pod":
        print(f"[EXEC] Restarting pod {plan.target_pod} in namespace {plan.namespace}...")
        success = restart_pod(plan.target_pod, plan.namespace)

        if success:
            log_event("POD_RESTARTED", plan.target_pod,
                      f"Pod deleted for restart", plan.parameters)
            healthy = verify_pod_healthy(plan.target_pod, plan.namespace)
            if healthy:
                log_event("REMEDIATION_SUCCESS", plan.target_pod,
                          "Pod recovered successfully", {})
                return "success"
            else:
                log_event("REMEDIATION_TIMEOUT", plan.target_pod,
                          "Pod did not recover in time", {})
                return "timeout"
        else:
            log_event("REMEDIATION_FAILED", plan.target_pod,
                      "Failed to delete pod", {})
            return "failed"

    return "unknown_action"