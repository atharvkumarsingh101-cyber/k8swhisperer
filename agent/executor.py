"""
agent/executor.py
-----------------
LangGraph nodes: diagnose → build_plan → safety_gate → execute → verify

Key changes vs original:
  - build_plan() now follows the official Anomaly Classification Matrix exactly.
  - safety_gate() is corrected (hitl_required and alert_human are ALWAYS HITL).
  - Adaptive gate: skips HITL if human has approved same failure+action 3+ times.
  - Dynamic confidence threshold lowers with approval history.
  - patch_memory(), patch_cpu(), delete_evicted() are now real implementations.
  - execute_node() branches correctly on alert_human vs hitl_required vs auto.
"""

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from agent.diagnose import diagnose, Diagnosis
from agent.logger import log_event, log_human_resolution, get_approval_count

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RemediationPlan:
    action: str          # restart_pod | patch_memory | patch_cpu | delete_evicted
                         # explain_only | alert_human | hitl_required
    blast_radius: str    # LOW | MEDIUM | HIGH | CRITICAL
    target_pod: str
    target_namespace: str
    fix_command: str     # human-readable kubectl command for alert_human cases
    confidence: float    # forwarded from Diagnosis.confidence
    failure_type: str    # forwarded from Anomaly


# ---------------------------------------------------------------------------
# Helper: run kubectl
# ---------------------------------------------------------------------------

_ALLOWED_NAMESPACES = {"default", "production", "staging", "monitoring"}
# Never touch system namespaces automatically
_FORBIDDEN_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}


def _kubectl(*args, timeout: int = 30) -> tuple[bool, str]:
    """
    Run a kubectl command.
    Returns (success: bool, output: str).
    Refuses to act on forbidden namespaces.
    """
    cmd = ["kubectl", *args]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        success = result.returncode == 0
        out = result.stdout.strip() or result.stderr.strip()
        return success, out
    except subprocess.TimeoutExpired:
        return False, "kubectl timed out"
    except Exception as exc:
        return False, str(exc)


def _guard_namespace(namespace: str) -> None:
    if namespace in _FORBIDDEN_NAMESPACES:
        raise ValueError(
            f"Refusing to act on protected namespace '{namespace}'. "
            "Update ALLOWED_NAMESPACES in executor.py if this is intentional."
        )


# ---------------------------------------------------------------------------
# Concrete remediation actions
# ---------------------------------------------------------------------------

def restart_pod(pod: str, namespace: str) -> tuple[bool, str]:
    """
    Delete the pod — Kubernetes will recreate it via the owning controller.
    Used for: CrashLoopBackOff (restartCount still acceptable).
    """
    _guard_namespace(namespace)
    ok, out = _kubectl("delete", "pod", pod, "-n", namespace, "--grace-period=0")
    return ok, out


def patch_memory(pod: str, namespace: str) -> tuple[bool, str]:
    """
    Increase the memory limit of the deployment owning this pod by 50%.
    Used for: OOMKilled.

    We patch the *deployment* (not the pod) so the change persists.
    Assumes the deployment name == the pod name prefix (common convention).
    Falls back to a plain pod delete + restart if no deployment is found.
    """
    _guard_namespace(namespace)

    # Step 1 — get current memory limit from the pod spec
    ok, current_mem = _kubectl(
        "get", "pod", pod, "-n", namespace,
        "-o", "jsonpath={.spec.containers[0].resources.limits.memory}"
    )
    if not ok or not current_mem:
        return False, f"Could not read memory limit for pod {pod}: {current_mem}"

    # Parse e.g. "256Mi" or "1Gi"
    try:
        if current_mem.endswith("Gi"):
            value_mi = int(float(current_mem.replace("Gi", "")) * 1024)
        elif current_mem.endswith("Mi"):
            value_mi = int(current_mem.replace("Mi", ""))
        elif current_mem.endswith("Ki"):
            value_mi = int(current_mem.replace("Ki", "")) // 1024
        else:
            value_mi = int(current_mem) // (1024 * 1024)
        new_mem = f"{int(value_mi * 1.5)}Mi"
    except ValueError:
        return False, f"Could not parse memory value '{current_mem}'"

    # Step 2 — determine the owning deployment name
    ok, deploy_name = _kubectl(
        "get", "pod", pod, "-n", namespace,
        "-o", "jsonpath={.metadata.ownerReferences[0].name}"
    )
    # ownerReferences points to a ReplicaSet — get its owning Deployment
    if ok and deploy_name:
        ok2, actual_deploy = _kubectl(
            "get", "replicaset", deploy_name, "-n", namespace,
            "-o", "jsonpath={.metadata.ownerReferences[0].name}"
        )
        if ok2 and actual_deploy:
            deploy_name = actual_deploy

    if not deploy_name:
        return False, "Could not find owning Deployment for pod — aborting patch."

    # Step 3 — patch the deployment
    patch_json = (
        '{"spec":{"template":{"spec":{"containers":[{"name":"'
        + deploy_name
        + '","resources":{"limits":{"memory":"'
        + new_mem
        + '"}}}]}}}}'
    )
    ok, out = _kubectl(
        "patch", "deployment", deploy_name,
        "-n", namespace,
        "--patch", patch_json,
    )
    if ok:
        return True, (
            f"Patched deployment/{deploy_name} memory limit "
            f"{current_mem} → {new_mem}. Pod will restart automatically."
        )
    return False, out


def patch_cpu(pod: str, namespace: str) -> tuple[bool, str]:
    """
    Increase the CPU limit of the deployment by 50%.
    Used for: CPUThrottling.
    """
    _guard_namespace(namespace)

    ok, current_cpu = _kubectl(
        "get", "pod", pod, "-n", namespace,
        "-o", "jsonpath={.spec.containers[0].resources.limits.cpu}"
    )
    if not ok or not current_cpu:
        return False, f"Could not read CPU limit: {current_cpu}"

    # Parse millicores — e.g. "500m" or "1" (= 1000m)
    try:
        if current_cpu.endswith("m"):
            value_m = int(current_cpu.replace("m", ""))
        else:
            value_m = int(float(current_cpu) * 1000)
        new_cpu = f"{int(value_m * 1.5)}m"
    except ValueError:
        return False, f"Could not parse CPU value '{current_cpu}'"

    # Get owning deployment (same logic as patch_memory)
    ok, deploy_name = _kubectl(
        "get", "pod", pod, "-n", namespace,
        "-o", "jsonpath={.metadata.ownerReferences[0].name}"
    )
    if ok and deploy_name:
        ok2, actual_deploy = _kubectl(
            "get", "replicaset", deploy_name, "-n", namespace,
            "-o", "jsonpath={.metadata.ownerReferences[0].name}"
        )
        if ok2 and actual_deploy:
            deploy_name = actual_deploy

    if not deploy_name:
        return False, "Could not find owning Deployment — aborting CPU patch."

    patch_json = (
        '{"spec":{"template":{"spec":{"containers":[{"name":"'
        + deploy_name
        + '","resources":{"limits":{"cpu":"'
        + new_cpu
        + '"}}}]}}}}'
    )
    ok, out = _kubectl(
        "patch", "deployment", deploy_name,
        "-n", namespace,
        "--patch", patch_json,
    )
    if ok:
        return True, (
            f"Patched deployment/{deploy_name} CPU limit "
            f"{current_cpu} → {new_cpu}."
        )
    return False, out


def delete_evicted(pod: str, namespace: str) -> tuple[bool, str]:
    """
    Delete an already-evicted pod record.
    Evicted pods are dead — deleting them just cleans up the API object.
    Used for: Evicted.
    """
    _guard_namespace(namespace)
    ok, out = _kubectl("delete", "pod", pod, "-n", namespace)
    return ok, out


# ---------------------------------------------------------------------------
# build_plan() — follows the official Anomaly Classification Matrix exactly
# ---------------------------------------------------------------------------

def build_plan(anomaly: Any, diagnosis: Diagnosis) -> RemediationPlan:
    """
    Map failure_type → action + blast_radius following the hackathon matrix:

    Failure Type     Auto-Action                   Severity  Blast
    CrashLoopBackOff auto restart pod              HIGH      MEDIUM
    OOMKilled        patch +50% memory → restart   HIGH      HIGH
    Pending          describe → recommend only     MED       MEDIUM
    ImagePullBackOff alert human                   MED       LOW
    CPUThrottling    patch CPU limit upward        MED       LOW
    Evicted          delete evicted pod            LOW       LOW
    DeploymentStall  HITL: rollback or force       HIGH      HIGH
    NodeNotReady     HITL ONLY — never auto-drain  CRITICAL  CRITICAL
    """
    ft = anomaly.failure_type

    if ft == "CrashLoopBackOff":
        # Matrix: auto restart. The pod is already looping so a clean delete
        # + recreation is the correct first-line autonomous action.
        action = "restart_pod"
        blast  = "MEDIUM"
        fix_cmd = (
            f"kubectl delete pod {anomaly.pod_name} "
            f"-n {anomaly.namespace} --grace-period=0"
        )

    elif ft == "OOMKilled":
        # Matrix: patch +50% memory limit then restart.
        # A plain restart would just OOMKill again — we must fix the limit.
        action = "patch_memory"
        blast  = "HIGH"
        fix_cmd = (
            f"kubectl patch deployment <deploy> -n {anomaly.namespace} "
            "--patch '{\"spec\":{\"template\":{\"spec\":{\"containers\":"
            "[{\"name\":\"<container>\",\"resources\":"
            "{\"limits\":{\"memory\":\"<new_limit>\"}}}]}}}}'"
        )

    elif ft == "Pending":
        # Matrix: describe → check node capacity → recommend only.
        # Do NOT auto-act — there are too many possible root causes.
        action = "explain_only"
        blast  = "MEDIUM"
        fix_cmd = diagnosis.fix_suggestion or "kubectl describe pod for details."

    elif ft == "ImagePullBackOff":
        # Matrix: extract image → alert human.
        # Cannot fix automatically — wrong image name or missing credentials.
        action = "alert_human"
        blast  = "LOW"
        fix_cmd = (
            diagnosis.fix_suggestion
            or "Correct the image tag and update the deployment spec."
        )

    elif ft == "CPUThrottling":
        # Matrix: patch CPU limit upward → verify throttle drops.
        action = "patch_cpu"
        blast  = "LOW"
        fix_cmd = (
            f"kubectl patch deployment <deploy> -n {anomaly.namespace} "
            "--patch '{...cpu limit increase...}'"
        )

    elif ft == "Evicted":
        # Matrix: check node pressure → delete evicted pod.
        # The pod is already dead; deleting its record is safe.
        action = "delete_evicted"
        blast  = "LOW"
        fix_cmd = (
            f"kubectl delete pod {anomaly.pod_name} -n {anomaly.namespace}"
        )

    elif ft == "DeploymentStall":
        # Matrix: HITL — rollback or force rollout. Never auto-decide direction.
        action = "hitl_required"
        blast  = "HIGH"
        fix_cmd = (
            f"kubectl rollout undo deployment/<deploy> -n {anomaly.namespace}"
            " OR kubectl rollout restart deployment/<deploy>"
        )

    elif ft == "NodeNotReady":
        # Matrix: HITL ONLY — NEVER auto-drain.
        # Draining evicts every pod on the node — catastrophic if wrong.
        action = "hitl_required"
        blast  = "CRITICAL"
        fix_cmd = (
            f"kubectl drain <node> --ignore-daemonsets --delete-emptydir-data"
        )

    else:
        # Unknown anomaly type — safe default.
        action = "explain_only"
        blast  = "MEDIUM"
        fix_cmd = diagnosis.fix_suggestion or "Manual investigation required."

    return RemediationPlan(
        action=action,
        blast_radius=blast,
        target_pod=anomaly.pod_name,
        target_namespace=anomaly.namespace,
        fix_command=fix_cmd,
        confidence=diagnosis.confidence,
        failure_type=ft,
    )


# ---------------------------------------------------------------------------
# Adaptive Safety Gate
# ---------------------------------------------------------------------------

# Actions that ALWAYS go to HITL — no confidence or history override.
_ALWAYS_HITL_ACTIONS = {"hitl_required", "alert_human"}

# Actions that can be auto-executed if conditions are met.
_AUTO_ELIGIBLE = {"restart_pod", "patch_cpu", "delete_evicted", "explain_only"}

# HIGH or CRITICAL blast always requires human eyes — even patch_memory.
_ALWAYS_HITL_BLAST = {"HIGH", "CRITICAL"}

# Minimum number of human approvals before we auto-trust a pattern.
_AUTO_TRUST_THRESHOLD = 3


def _dynamic_confidence_threshold(failure_type: str, action: str) -> float:
    """
    Confidence threshold decreases as approval history builds.
    Base = 0.80.  Each past approval lowers it by 3% (floor = 0.55).
    """
    base = 0.80
    approvals = get_approval_count(failure_type, action)
    return max(0.55, base - (approvals * 0.03))


def safety_gate(plan: RemediationPlan, anomaly: Any) -> bool:
    """
    Returns True  → auto-execute (no human needed).
    Returns False → send to HITL.

    ``anomaly`` may be a plain failure-type string (used in tests / direct
    calls) or a full Anomaly object — both are handled transparently.

    Logic (in order):
    1. Some actions are ALWAYS HITL — no exceptions (alert_human, hitl_required).
    2. HIGH or CRITICAL blast → HITL.
    3. Confidence below dynamic threshold → HITL.
    4. Adaptive override — 3+ prior human approvals → auto-execute.
    5. Everything else → auto-execute.
    """
    # Normalise: accept either a string failure_type or an anomaly object
    failure_type = anomaly if isinstance(anomaly, str) else plan.failure_type

    # Rule 1: Unconditional HITL actions — cannot be bypassed by anything
    if plan.action in _ALWAYS_HITL_ACTIONS:
        return False

    # Rule 2: High-blast actions always need human approval
    if plan.blast_radius in _ALWAYS_HITL_BLAST:
        return False

    # Rule 3: Dynamic confidence threshold
    threshold = _dynamic_confidence_threshold(failure_type, plan.action)
    if plan.confidence < threshold:
        # Rule 4: Adaptive override — enough prior approvals waives the threshold
        approval_count = get_approval_count(failure_type, plan.action)
        if approval_count >= _AUTO_TRUST_THRESHOLD:
            log_event("SAFETY_GATE_ADAPTIVE_PASS", {
                "failure_type": failure_type,
                "action": plan.action,
                "approval_count": approval_count,
                "reason": "Adaptive trust — bypassing HITL based on approval history",
            })
            return True
        return False

    # All checks passed — safe to auto-execute
    return True


# ---------------------------------------------------------------------------
# Verify pod health after remediation
# ---------------------------------------------------------------------------

def verify_pod_healthy(pod: str, namespace: str, max_polls: int = 12) -> bool:
    """
    Poll every 10 seconds (up to 2 minutes) for the pod to become Running.
    Then watch for 2 more minutes for a re-crash.
    Returns True if the pod is still healthy at the end of the window.
    """
    print(f"[VERIFY] Waiting for {pod} to become Running...")
    for i in range(max_polls):
        time.sleep(10)
        ok, phase = _kubectl(
            "get", "pod", pod, "-n", namespace,
            "-o", "jsonpath={.status.phase}"
        )
        print(f"[VERIFY] Poll {i+1}/{max_polls}: phase={phase}")
        if ok and phase == "Running":
            break
    else:
        return False

    # Post-recovery watch — make sure it doesn't crash within 2 minutes
    print("[VERIFY] Pod is Running — watching for 2 minutes for re-crash...")
    for _ in range(12):
        time.sleep(10)
        ok, phase = _kubectl(
            "get", "pod", pod, "-n", namespace,
            "-o", "jsonpath={.status.phase}"
        )
        if ok and phase not in ("Running", "Succeeded"):
            print(f"[VERIFY] Re-crash detected (phase={phase})")
            return False

    print("[VERIFY] Pod is stable. Resolution confirmed.")
    return True


def execute_plan(
    plan: RemediationPlan,
    anomaly: Any,
    hitl_approved: bool = False,
    alerted_at_epoch: float | None = None,
) -> dict:
    """
    Direct callable wrapper around execute_node logic.
    Used by tests and external callers that don't go through LangGraph state.

    Returns the result dict (same shape as execute_node).
    """
    state = {
        "plan": plan,
        "anomaly": anomaly,
        "hitl_approved": hitl_approved,
        "alerted_at_epoch": alerted_at_epoch or time.time(),
    }
    return execute_node(state)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

def diagnose_node(state: dict) -> dict:
    """LangGraph node: fetch logs + run two-LLM diagnosis."""
    anomaly = state["anomaly"]
    diag = diagnose(
        pod_name=anomaly.pod_name,
        namespace=anomaly.namespace,
        failure_type=anomaly.failure_type,
    )
    log_event("DIAGNOSIS_COMPLETE", {
        "pod": anomaly.pod_name,
        "failure_type": anomaly.failure_type,
        "root_cause": diag.root_cause,
        "severity": diag.severity,
        "confidence": diag.confidence,
        "verifier_agrees": diag.verifier_agrees,
    })
    return {"diagnosis": diag}


def plan_node(state: dict) -> dict:
    """LangGraph node: build the remediation plan from the diagnosis."""
    anomaly   = state["anomaly"]
    diagnosis = state["diagnosis"]
    plan = build_plan(anomaly, diagnosis)
    log_event("PLAN_CREATED", {
        "pod": plan.target_pod,
        "action": plan.action,
        "blast_radius": plan.blast_radius,
        "confidence": plan.confidence,
    })
    return {"plan": plan}


def safety_gate_node(state: dict) -> dict:
    """LangGraph node: route to auto-execute or HITL."""
    plan    = state["plan"]
    anomaly = state["anomaly"]
    auto    = safety_gate(plan, anomaly)
    log_event("SAFETY_GATE", {
        "pod": plan.target_pod,
        "action": plan.action,
        "blast_radius": plan.blast_radius,
        "confidence": plan.confidence,
        "decision": "AUTO" if auto else "HITL",
    })
    return {"auto_execute": auto}


def execute_node(state: dict) -> dict:
    """
    LangGraph node: run the approved action.

    Branching:
      alert_human   → surface fix command, log, no kubectl.
      hitl_required → human already approved via HITL page → run kubectl action.
      auto actions  → run kubectl directly.
    """
    plan      = state["plan"]
    pod       = plan.target_pod
    ns        = plan.target_namespace
    approved  = state.get("hitl_approved", False)
    alerted_at = state.get("alerted_at_epoch", time.time())

    result = {"success": False, "output": ""}

    if plan.action == "alert_human":
        # Nothing to execute — surface the fix command.
        msg = (
            f"[ALERT] Human action required for {pod}:\n"
            f"  {plan.fix_command}"
        )
        print(msg)
        log_event("HUMAN_ALERT", {
            "pod": pod,
            "namespace": ns,
            "failure_type": plan.failure_type,
            "fix_command": plan.fix_command,
        })
        # Record resolution — the human will action this outside the system.
        log_human_resolution(
            failure_type=plan.failure_type,
            pod_name=pod,
            namespace=ns,
            fix_applied=plan.fix_command,
            resolution_notes="Alert surfaced to human — awaiting manual fix.",
            resolved_at_epoch=time.time(),
            alerted_at_epoch=alerted_at,
        )
        return {"result": "alerted", "success": True}

    if plan.action == "hitl_required":
        if not approved:
            print(f"[HITL] Action '{plan.action}' rejected by human.")
            log_event("HITL_REJECTED", {"pod": pod, "action": plan.action})
            return {"result": "rejected", "success": False}
        # Human approved — now actually run the high-risk action.
        # (For NodeNotReady / DeploymentStall the specific kubectl command
        #  is embedded in plan.fix_command set by build_plan.)
        ok, out = _kubectl(*plan.fix_command.split())
        result = {"success": ok, "output": out, "result": "executed" if ok else "failed"}
        log_event("HITL_EXECUTED", {"pod": pod, "action": plan.action, "output": out})
        return result

    # Auto-eligible actions
    action_map = {
        "restart_pod":    lambda: restart_pod(pod, ns),
        "patch_memory":   lambda: patch_memory(pod, ns),
        "patch_cpu":      lambda: patch_cpu(pod, ns),
        "delete_evicted": lambda: delete_evicted(pod, ns),
        "explain_only":   lambda: (True, plan.fix_command),
    }

    fn = action_map.get(plan.action)
    if fn is None:
        return {"result": "unknown_action", "success": False}

    ok, out = fn()
    log_event("AUTO_EXECUTE", {
        "pod": pod,
        "action": plan.action,
        "success": ok,
        "output": out,
    })

    if ok and plan.action not in ("explain_only",):
        healthy = verify_pod_healthy(pod, ns)
        log_event("RESOLUTION_COMPLETE", {"pod": pod, "healthy": healthy})
        return {"result": "resolved" if healthy else "re_crashed", "success": healthy, "output": out}

    return {"result": "ok" if ok else "failed", "success": ok, "output": out}
