# agent/detector.py
from agent.state import Anomaly

def detect_failures(pods_json: dict) -> list:
    anomalies = []

    for pod in pods_json.get("items", []):
        name      = pod["metadata"]["name"]
        namespace = pod["metadata"]["namespace"]
        phase     = pod["status"].get("phase", "Unknown")

        container_statuses = pod["status"].get("containerStatuses", [])
        init_statuses      = pod["status"].get("initContainerStatuses", [])

        for cs in container_statuses + init_statuses:
            state         = cs.get("state", {})
            last_state    = cs.get("lastState", {})
            restart_count = cs.get("restartCount", 0)
            waiting       = state.get("waiting", {})

            # CrashLoopBackOff
            if waiting.get("reason") == "CrashLoopBackOff":
                anomalies.append(Anomaly(
                    pod=name, namespace=namespace,
                    failure_type="CrashLoopBackOff",
                    severity="HIGH" if restart_count > 5 else "MEDIUM",
                    confidence=0.95,
                    message=f"Pod has restarted {restart_count} times and keeps crashing",
                    restart_count=restart_count
                ))

            # ImagePullBackOff
            if waiting.get("reason") in ["ImagePullBackOff", "ErrImagePull"]:
                anomalies.append(Anomaly(
                    pod=name, namespace=namespace,
                    failure_type="ImagePullBackOff",
                    severity="MEDIUM",
                    confidence=0.99,
                    message=waiting.get("message", "Cannot pull container image"),
                    restart_count=restart_count
                ))

            # OOMKilled
            for term_state in [last_state.get("terminated", {}),
                               state.get("terminated", {})]:
                if term_state.get("reason") == "OOMKilled":
                    anomalies.append(Anomaly(
                        pod=name, namespace=namespace,
                        failure_type="OOMKilled",
                        severity="HIGH",
                        confidence=0.99,
                        message="Container exceeded memory limit (exit code 137)",
                        restart_count=restart_count
                    ))
                    break

        # Pending Pod
        if phase == "Pending" and len(container_statuses) == 0:
            reason = "Pod cannot be scheduled"
            for cond in pod["status"].get("conditions", []):
                if cond.get("type") == "PodScheduled" and cond.get("status") == "False":
                    reason = cond.get("message", reason)
                    break
            anomalies.append(Anomaly(
                pod=name, namespace=namespace,
                failure_type="Pending",
                severity="MEDIUM",
                confidence=0.90,
                message=reason,
                restart_count=0
            ))

        # Evicted Pod
        if pod["status"].get("reason") == "Evicted":
            anomalies.append(Anomaly(
                pod=name, namespace=namespace,
                failure_type="Evicted",
                severity="LOW",
                confidence=0.99,
                message=pod["status"].get("message", "Pod was evicted due to node pressure"),
                restart_count=0
            ))

    # De-duplicate
    seen, unique = set(), []
    for a in anomalies:
        key = f"{a.pod}:{a.namespace}:{a.failure_type}"
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique

def detect_node_issues(nodes: list) -> list:
    anomalies = []
    for node in nodes:
        name = node["metadata"]["name"]
        for cond in node["status"].get("conditions", []):
            if cond.get("type") == "Ready" and cond.get("status") == "False":
                anomalies.append(Anomaly(
                    pod=name, namespace="cluster",
                    failure_type="NodeNotReady",
                    severity="CRITICAL",
                    confidence=0.99,
                    message=cond.get("message", "Node is not ready"),
                    restart_count=0
                ))
    return anomalies

def detect_deployment_stall(deployments_json: dict) -> list:
    anomalies = []
    for dep in deployments_json.get("items", []):
        name      = dep["metadata"]["name"]
        namespace = dep["metadata"]["namespace"]
        status    = dep.get("status", {})
        spec      = dep.get("spec", {})

        desired = spec.get("replicas", 0)
        updated = status.get("updatedReplicas", 0)
        ready   = status.get("readyReplicas", 0)

        if desired > 0 and (updated != desired or ready != desired):
            anomalies.append(Anomaly(
                pod=name, namespace=namespace,
                failure_type="DeploymentStalled",
                severity="HIGH",
                confidence=0.85,
                message=f"Deployment has {ready}/{desired} ready replicas, {updated} updated",
                restart_count=0
            ))
    return anomalies

def predict_upcoming_crashloop(pods_json: dict, history: dict) -> list:
    warnings = []
    for pod in pods_json.get("items", []):
        name = pod["metadata"]["name"]
        for cs in pod["status"].get("containerStatuses", []):
            current = cs.get("restartCount", 0)
            past    = history.get(name, [])

            if len(past) >= 2 and past[-1] > past[-2] and current > past[-1]:
                warnings.append(Anomaly(
                    pod=name,
                    namespace=pod["metadata"]["namespace"],
                    failure_type="PredictedCrashLoop",
                    severity="MEDIUM",
                    confidence=0.72,
                    message=f"Restart count rising: {past[-2]}→{past[-1]}→{current}. CrashLoop likely.",
                    restart_count=current
                ))
    return warnings