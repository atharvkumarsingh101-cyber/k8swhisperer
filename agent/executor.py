"""
agent/executor.py
-----------------
LangGraph nodes: diagnose → build_plan → safety_gate → execute → verify

Key changes vs original:
  - build_plan() follows the official Anomaly Classification Matrix exactly.
  - safety_gate() is corrected (hitl_required and alert_human are ALWAYS HITL).
  - Adaptive gate: skips HITL if human has approved same failure+action 3+ times.
  - Dynamic confidence threshold lowers with approval history.
  - patch_memory(), patch_cpu(), delete_evicted() are now real implementations.
  - execute_node() branches correctly on alert_human vs hitl_required vs auto.
  - FIX Bug 3: patch_memory/patch_cpu now fetch the real container name from the pod.
  - FIX Bug 4: Optional[float] used instead of float | None (Python 3.9 compat).
"""

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Optional  # FIX Bug 4: import Optional

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
_FORBIDDEN_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}


def _kubectl(*args, timeout: int = 30) -> tuple:
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
# FIX Bug 3 helper: get the actual container name from the pod spec
# ---------------------------------------------------------------------------

def _get_container_name(pod: str, namespace: str) -> str:
    """
    Return the first container's name from a running pod.
    This is what kubectl patch needs in the containers[].name field.
    Falls back to the pod name if the call fails.
    """
    ok, name = _kubectl(
        "get", "pod", pod, "-n", namespace,
        "-o", "jsonpath={.spec.containers[0].name}"
    )
    return name.strip() if ok and name.strip() else pod


# ---------------------------------------------------------------------------
# Concrete remediation actions
# ---------------------------------------------------------------------------

def restart_pod(pod: str, namespace: str) -> tuple:
    _guard_namespace(namespace)
    ok, out = _kubectl("delete", "pod", pod, "-n", namespace, "--grace-period=0")
    return ok, out


def patch_memory(pod: str, namespace: str) -> tuple:
    """
    Increase the memory limit of the deployment owning this pod by 50%.
    FIX Bug 3: fetch the real container name from the pod spec before patching.
    """
    _guard_namespace(namespace)

    # Step 1 — get current memory limit
    ok, current_mem = _kubectl(
        "get", "pod", pod, "-n", namespace,
        "-o", "jsonpath={.spec.containers[0].resources.limits.memory}"
    )
    if not ok or not current_mem:
        return False, f"Could not read memory limit for pod {pod}: {current_mem}"

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

    # FIX Bug 3: get the REAL container name (not the deployment name)
    container_name = _get_container_name(pod, namespace)

    # Step 2 — get owning deployment name via ReplicaSet owner chain
    ok, rs_name = _kubectl(
        "get", "pod", pod, "-n", namespace,
        "-o", "jsonpath={.metadata.ownerReferences[0].name}"
    )
    deploy_name = ""
    if ok and rs_name:
        ok2, actual_deploy = _kubectl(
            "get", "replicaset", rs_name, "-n", namespace,
            "-o", "jsonpath={.metadata.ownerReferences[0].name}"
        )
        if ok2 and actual_deploy:
            deploy_name = actual_deploy

    if not deploy_name:
        return False, "Could not find owning Deployment for pod — aborting patch."

    # Step 3 — patch using the REAL container name
    patch_json = (
        '{"spec":{"template":{"spec":{"containers":[{"name":"'
        + container_name  # FIX: was deploy_name — now the actual container name
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
            f"Patched deployment/{deploy_name} container '{container_name}' "
            f"memory limit {current_mem} → {new_mem}. Pod will restart automatically."
        )
    return False, out


def patch_cpu(pod: str, namespace: str) -> tuple:
    """
    Increase the CPU limit of the deployment by 50%.
    FIX Bug 3: fetch the real container name from the pod spec before patching.
    """
    _guard_namespace(namespace)

    ok, current_cpu = _kubectl(
        "get", "pod", pod, "-n", namespace,
        "-o", "jsonpath={.spec.containers[0].resources.limits.cpu}"
    )
    if not ok or not current_cpu:
        return False, f"Could not read CPU limit: {current_cpu}"

    try:
        if current_cpu.endswith("m"):
            value_m = int(current_cpu.replace("m", ""))
        else:
            value_m = int(float(current_cpu) * 1000)
        new_cpu = f"{int(value_m * 1.5)}m"
    except ValueError:
        return False, f"Could not parse CPU value '{current_cpu}'"

    # FIX Bug 3: get the REAL container name
    container_name = _get_container_name(pod, namespace)

    # Get owning deployment via ReplicaSet
    ok, rs_name = _kubectl(
        "get", "pod", pod, "-n", namespace,
        "-o", "jsonpath={.metadata.ownerReferences[0].name}"
    )
    deploy_name = ""
    if ok and rs_name:
        ok2, actual_deploy = _kubectl(
            "get", "replicaset", rs_name, "-n", namespace,
            "-o", "jsonpath={.metadata.ownerReferences[0].name}"
        )
        if ok2 and actual_deploy:
            deploy_name = actual_deploy

    if not deploy_name:
        return False, "Could not find owning Deployment — aborting CPU patch."

    patch_json = (
        '{"spec":{"template":{"spec":{"containers":[{"name":"'
        + container_name  # FIX: was deploy_name — now the actual container name
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
            f"Patched deployment/{deploy_name} container '{container_name}' "
            f"CPU limit {current_cpu} → {new_cpu}."
        )
    return False, out


def delete_evicted(pod: str, namespace: str) -> tuple:
    _guard_namespace(namespace)
    ok, out = _kubectl("delete", "pod", pod, "-n", namespace)
    return ok, out


# ---------------------------------------------------------------------------
# build_plan() — follows the official Anomaly Classification Matrix
# ---------------------------------------------------------------------------

def build_plan(anomaly: Any, diagnosis: Diagnosis) -> RemediationPlan:
    ft = anomaly.failure_type

    if ft == "CrashLoopBackOff":
        action = "restart_pod"
        blast  = "MEDIUM"
        fix_cmd = (
            f"kubectl delete pod {anomaly.pod} "
            f"-n {anomaly.namespace} --grace-period=0"
        )
    elif ft == "OOMKilled":
        action = "patch_memory"
        blast  = "HIGH"
        fix_cmd = (
            f"kubectl patch deployment <deploy> -n {anomaly.namespace} "
            "--patch '{\"spec\":{\"template\":{\"spec\":{\"containers\":"
            "[{\"name\":\"<container>\",\"resources\":"
            "{\"limits\":{\"memory\":\"<new_limit>\"}}}]}}}}'"
        )
    elif ft == "Pending":
        action = "explain_only"
        blast  = "MEDIUM"
        fix_cmd = diagnosis.fix_suggestion or "kubectl describe pod for details."
    elif ft == "ImagePullBackOff":
        action = "alert_human"
        blast  = "LOW"
        fix_cmd = (
            diagnosis.fix_suggestion
            or "Correct the image tag and update the deployment spec."
        )
    elif ft == "CPUThrottling":
        action = "patch_cpu"
        blast  = "LOW"
        fix_cmd = (
            f"kubectl patch deployment <deploy> -n {anomaly.namespace} "
            "--patch '{...cpu limit increase...}'"
        )
    elif ft == "Evicted":
        action = "delete_evicted"
        blast  = "LOW"
        fix_cmd = f"kubectl delete pod {anomaly.pod} -n {anomaly.namespace}"
    elif ft == "DeploymentStall":
        action = "hitl_required"
        blast  = "HIGH"
        fix_cmd = (
            f"kubectl rollout undo deployment/<deploy> -n {anomaly.namespace}"
            " OR kubectl rollout restart deployment/<deploy>"
        )
    elif ft == "NodeNotReady":
        action = "hitl_required"
        blast  = "CRITICAL"
        fix_cmd = (
            "kubectl drain <node> --ignore-daemonsets --delete-emptydir-data"
        )
    else:
        action = "explain_only"
        blast  = "MEDIUM"
        fix_cmd = diagnosis.fix_suggestion or "Manual investigation required."

    return RemediationPlan(
        action=action,
        blast_radius=blast,
        target_pod=anomaly.pod,
        target_namespace=anomaly.namespace,
        fix_command=fix_cmd,
        confidence=diagnosis.confidence,
        failure_type=ft,
    )


# ---------------------------------------------------------------------------
# Adaptive Safety Gate
# ---------------------------------------------------------------------------

_ALWAYS_HITL_ACTIONS = {"hitl_required", "alert_human"}
_AUTO_ELIGIBLE = {"restart_pod", "patch_cpu", "delete_evicted", "explain_only"}
_ALWAYS_HITL_BLAST = {"HIGH", "CRITICAL"}
_AUTO_TRUST_THRESHOLD = 3


def _dynamic_confidence_threshold(failure_type: str, action: str) -> float:
    base = 0.80
    approvals = get_approval_count(failure_type, action)
    return max(0.55, base - (approvals * 0.03))


def safety_gate(plan: RemediationPlan, anomaly: Any) -> bool:
    failure_type = anomaly if isinstance(anomaly, str) else plan.failure_type

    if plan.action in _ALWAYS_HITL_ACTIONS:
        return False
    if plan.blast_radius in _ALWAYS_HITL_BLAST:
        return False

    threshold = _dynamic_confidence_threshold(failure_type, plan.action)
    if plan.confidence < threshold:
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

    return True


# ---------------------------------------------------------------------------
# Verify pod health after remediation
# ---------------------------------------------------------------------------

def verify_pod_healthy(pod: str, namespace: str, max_polls: int = 12) -> bool:
    parts = pod.split('-')
    base_name = "-".join(parts[:-2]) if len(parts) >= 3 else pod
    print(f"[VERIFY] Looking for new replacement pods matching '{base_name}'...")
    current_pod = pod

    for i in range(max_polls):
        time.sleep(10)
        ok, out = _kubectl(
            "get", "pods", "-n", namespace,
            "--no-headers",
            "-o", "custom-columns=:metadata.name,:status.phase"
        )
        if not ok:
            continue
        matching_pods = [
            line.split() for line in out.split('\n')
            if line.startswith(base_name) and len(line.split()) == 2
        ]
        if not matching_pods:
            print(f"[VERIFY] Poll {i+1}/{max_polls}: No pods found for '{base_name}'")
            continue
        current_pod, phase = matching_pods[0]
        print(f"[VERIFY] Poll {i+1}/{max_polls}: {current_pod} phase={phase}")
        if phase == "Running":
            break
    else:
        return False

    print(f"[VERIFY] {current_pod} is Running — watching for 2 minutes for re-crash...")
    for _ in range(12):
        time.sleep(10)
        ok, phase = _kubectl(
            "get", "pod", current_pod, "-n", namespace,
            "-o", "jsonpath={.status.phase}"
        )
        if ok and phase not in ("Running", "Succeeded"):
            print(f"[VERIFY] Re-crash detected on {current_pod} (phase={phase})")
            return False

    print("[VERIFY] Pod is stable. Resolution confirmed.")
    return True


# FIX Bug 4: use Optional[float] instead of float | None (Python 3.9 compatible)
def execute_plan(
    plan: RemediationPlan,
    anomaly: Any,
    hitl_approved: bool = False,
    alerted_at_epoch: Optional[float] = None,  # FIX: was float | None
) -> dict:
    state = {
        "plan": plan,
        "anomaly": anomaly,
        "hitl_approved": hitl_approved,
        "alerted_at_epoch": alerted_at_epoch or time.time(),
    }
    return execute_node(state)


# ---------------------------------------------------------------------------
# LangGraph node functions
# ---------------------------------------------------------------------------

def diagnose_node(state: dict) -> dict:
    anomaly = state["anomaly"]
    diag = diagnose(
        pod_name=anomaly.pod,
        namespace=anomaly.namespace,
        failure_type=anomaly.failure_type,
    )
    log_event("DIAGNOSIS_COMPLETE", {
        "pod": anomaly.pod,
        "failure_type": anomaly.failure_type,
        "root_cause": diag.root_cause,
        "severity": diag.severity,
        "confidence": diag.confidence,
        "verifier_agrees": diag.verifier_agrees,
    })
    return {"diagnosis": diag}


def plan_node(state: dict) -> dict:
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
    plan      = state["plan"]
    pod       = plan.target_pod
    ns        = plan.target_namespace
    approved  = state.get("hitl_approved", False)
    alerted_at = state.get("alerted_at_epoch", time.time())

    result = {"success": False, "output": ""}

    if plan.action == "alert_human":
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
        ok, out = _kubectl(*plan.fix_command.split())
        result = {"success": ok, "output": out, "result": "executed" if ok else "failed"}
        log_event("HITL_EXECUTED", {"pod": pod, "action": plan.action, "output": out})
        return result

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
        return {
            "result": "resolved" if healthy else "re_crashed",
            "success": healthy,
            "output": out,
        }

    return {"result": "ok" if ok else "failed", "success": ok, "output": out}