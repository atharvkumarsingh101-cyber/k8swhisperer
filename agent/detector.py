# agent/detector.py
import subprocess
import json
from agent.state import Anomaly


def get_pod_status(pod: dict) -> dict:
    """Extract key status fields from a pod JSON object."""
    metadata = pod.get("metadata", {})
    status = pod.get("status", {})
    container_statuses = status.get("containerStatuses", [])

    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "default")
    phase = status.get("phase", "Unknown")

    restart_count = 0
    waiting_reason = ""
    waiting_message = ""
    last_terminated_reason = ""  # FIX: track terminated.reason from lastState

    for cs in container_statuses:
        restart_count += cs.get("restartCount", 0)

        state = cs.get("state", {})
        waiting = state.get("waiting", {})
        if waiting:
            waiting_reason = waiting.get("reason", "")
            waiting_message = waiting.get("message", "")

        # FIX: OOMKilled appears in lastState.terminated.reason, not in waiting
        last_state = cs.get("lastState", {})
        terminated = last_state.get("terminated", {})
        if terminated.get("reason"):
            last_terminated_reason = terminated["reason"]

    return {
        "name": name,
        "namespace": namespace,
        "phase": phase,
        "restart_count": restart_count,
        "waiting_reason": waiting_reason,
        "waiting_message": waiting_message,
        "last_terminated_reason": last_terminated_reason,  # FIX: new field
    }


def detect_failures(pods_json: dict) -> list:
    """
    Detect pod-level failures from kubectl get pods -o json output.
    Returns a list of Anomaly objects.
    """
    anomalies = []
    items = pods_json.get("items", []) if isinstance(pods_json, dict) else []

    for pod in items:
        info = get_pod_status(pod)
        name = info["name"]
        namespace = info["namespace"]
        phase = info["phase"]
        restart_count = info["restart_count"]
        waiting_reason = info["waiting_reason"]
        waiting_message = info["waiting_message"]
        last_terminated_reason = info["last_terminated_reason"]  # FIX

        # CrashLoopBackOff
        if waiting_reason == "CrashLoopBackOff" or restart_count >= 5:
            anomalies.append(Anomaly(
                pod=name,
                namespace=namespace,
                failure_type="CrashLoopBackOff",
                severity="HIGH",
                confidence=0.95,
                message=waiting_message or f"Pod has restarted {restart_count} times",
                restart_count=restart_count,
            ))

        # ImagePullBackOff
        elif waiting_reason in ("ImagePullBackOff", "ErrImagePull"):
            anomalies.append(Anomaly(
                pod=name,
                namespace=namespace,
                failure_type="ImagePullBackOff",
                severity="HIGH",
                confidence=0.99,
                message=waiting_message or "Image pull failed",
                restart_count=restart_count,
            ))

        # FIX: OOMKilled — must check lastState.terminated.reason, NOT waiting.reason
        elif last_terminated_reason == "OOMKilled":
            anomalies.append(Anomaly(
                pod=name,
                namespace=namespace,
                failure_type="OOMKilled",
                severity="HIGH",
                confidence=0.99,
                message="Pod was killed due to out-of-memory",
                restart_count=restart_count,
            ))

        # Pending (stuck)
        elif phase == "Pending":
            anomalies.append(Anomaly(
                pod=name,
                namespace=namespace,
                failure_type="Pending",
                severity="MEDIUM",
                confidence=0.90,
                message="Pod is stuck in Pending state",
                restart_count=0,
            ))

        # Evicted
        elif phase == "Failed":
            reason = pod.get("status", {}).get("reason", "")
            if reason == "Evicted":
                anomalies.append(Anomaly(
                    pod=name,
                    namespace=namespace,
                    failure_type="Evicted",
                    severity="MEDIUM",
                    confidence=0.95,
                    message="Pod was evicted",
                    restart_count=0,
                ))

    return anomalies


def detect_node_issues(nodes_list: list) -> list:
    """
    Detect node-level issues like NotReady nodes.
    Expects a list of node objects (the 'items' array from kubectl get nodes -o json).
    """
    issues = []
    if not isinstance(nodes_list, list):
        return issues

    for node in nodes_list:
        if not isinstance(node, dict):
            continue
        name = node.get("metadata", {}).get("name", "unknown")
        conditions = node.get("status", {}).get("conditions", [])
        for condition in conditions:
            if condition.get("type") == "Ready" and condition.get("status") != "True":
                issues.append(Anomaly(
                    pod=name,
                    namespace="kube-system",
                    failure_type="NodeNotReady",
                    severity="CRITICAL",
                    confidence=0.99,
                    message=f"Node {name} is NotReady: {condition.get('message', '')}",
                    restart_count=0,
                ))
    return issues


def detect_deployment_stall(deployments_json: dict) -> list:
    """
    Detect deployments that are stalled (0 ready pods out of desired).
    """
    stalls = []
    items = deployments_json.get("items", []) if isinstance(deployments_json, dict) else []

    for dep in items:
        if not isinstance(dep, dict):
            continue
        name = dep.get("metadata", {}).get("name", "unknown")
        namespace = dep.get("metadata", {}).get("namespace", "default")
        spec = dep.get("spec", {})
        status = dep.get("status", {})

        desired = spec.get("replicas", 1)
        ready = status.get("readyReplicas", 0)

        if desired and desired > 0 and ready == 0:
            stalls.append(Anomaly(
                pod=name,
                namespace=namespace,
                failure_type="DeploymentStall",
                severity="HIGH",
                confidence=0.85,
                message=f"Deployment {name} has 0/{desired} pods ready",
                restart_count=0,
            ))
    return stalls


def detect_cpu_throttling(pods_json: dict) -> list:
    """Detect pods with high CPU usage using kubectl top (requires metrics-server)."""
    warnings = []
    try:
        result = subprocess.run(
            ["kubectl", "top", "pods", "-A", "--no-headers"],
            capture_output=True, text=True, timeout=15
        )
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 3:
                cpu_str = parts[2].replace("m", "")
                try:
                    cpu_val = int(cpu_str)
                    if cpu_val > 800:
                        warnings.append(Anomaly(
                            pod=parts[1],
                            namespace=parts[0],
                            failure_type="CPUThrottling",
                            severity="MEDIUM",
                            confidence=0.75,
                            message=f"Pod using {cpu_val}m CPU — likely throttling",
                            restart_count=0,
                        ))
                except ValueError:
                    pass
    except Exception:
        pass
    return warnings