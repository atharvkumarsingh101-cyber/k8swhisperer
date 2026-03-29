# agent/monitor.py
import subprocess
import json

def get_all_pods(namespace="--all-namespaces"):
    if namespace == "--all-namespaces":
        cmd = ["kubectl", "get", "pods", "--all-namespaces", "-o", "json"]
    else:
        cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[MONITOR ERROR] {result.stderr}")
        return {"items": []}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"items": []}

def get_pod_logs(pod_name, namespace="default", tail=80):
    result = subprocess.run(
        ["kubectl", "logs", pod_name, "-n", namespace,
         f"--tail={tail}", "--previous"],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout[:3000]

    result = subprocess.run(
        ["kubectl", "logs", pod_name, "-n", namespace, f"--tail={tail}"],
        capture_output=True, text=True
    )
    return (result.stdout or "No logs available")[:3000]

def describe_pod(pod_name, namespace="default"):
    result = subprocess.run(
        ["kubectl", "describe", "pod", pod_name, "-n", namespace],
        capture_output=True, text=True
    )
    full = result.stdout or ""

    events_idx     = full.find("Events:")
    conditions_idx = full.find("Conditions:")

    if conditions_idx != -1:
        return full[conditions_idx:conditions_idx + 2000]
    if events_idx != -1:
        return full[events_idx:events_idx + 1500]
    return full[:2000]

def get_node_status():
    result = subprocess.run(
        ["kubectl", "get", "nodes", "-o", "json"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout).get("items", [])
    except:
        return []

def get_deployments(namespace="default"):
    result = subprocess.run(
        ["kubectl", "get", "deployments", "-n", namespace, "-o", "json"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return {"items": []}
    try:
        return json.loads(result.stdout)
    except:
        return {"items": []}
def get_all_nodes() -> dict:
    """Get all nodes in the cluster."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "nodes", "-o", "json"],
            capture_output=True, text=True, timeout=30
        )
        return json.loads(result.stdout) if result.returncode == 0 else {"items": []}
    except Exception as e:
        print(f"[ERROR] get_all_nodes: {e}")
        return {"items": []}


def get_all_deployments() -> dict:
    """Get all deployments across all namespaces."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "deployments", "-A", "-o", "json"],
            capture_output=True, text=True, timeout=30
        )
        return json.loads(result.stdout) if result.returncode == 0 else {"items": []}
    except Exception as e:
        print(f"[ERROR] get_all_deployments: {e}")
        return {"items": []}