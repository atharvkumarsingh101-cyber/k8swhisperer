# mcp_server.py
import subprocess
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("K8sWhisperer")


@mcp.tool()
def get_pods(namespace: str = "default") -> str:
    """Get all pods in a namespace."""
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode == 0:
        data = json.loads(result.stdout)
        pods = [
            {
                "name": p["metadata"]["name"],
                "phase": p["status"].get("phase", "Unknown"),
                "namespace": p["metadata"]["namespace"]
            }
            for p in data.get("items", [])
        ]
        return json.dumps(pods, indent=2)
    return f"Error: {result.stderr}"


@mcp.tool()
def get_logs(pod: str, namespace: str = "default") -> str:
    """Get logs from a pod."""
    result = subprocess.run(
        ["kubectl", "logs", pod, "-n", namespace, "--tail=50"],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout if result.returncode == 0 else f"Error: {result.stderr}"


@mcp.tool()
def delete_pod(pod: str, namespace: str = "default") -> str:
    """Delete a pod to trigger restart."""
    result = subprocess.run(
        ["kubectl", "delete", "pod", pod, "-n", namespace, "--ignore-not-found"],
        capture_output=True, text=True, timeout=30
    )
    return "Deleted successfully" if result.returncode == 0 else f"Error: {result.stderr}"


@mcp.tool()
def describe_pod_tool(pod: str, namespace: str = "default") -> str:
    """Describe a pod for detailed info."""
    result = subprocess.run(
        ["kubectl", "describe", "pod", pod, "-n", namespace],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout if result.returncode == 0 else f"Error: {result.stderr}"


@mcp.tool()
def send_alert(message: str) -> str:
    """Send an alert message to the audit log."""
    from agent.logger import log_event
    log_event("MCP_ALERT", "mcp", message, {})
    return f"Alert logged: {message}"


if __name__ == "__main__":
    mcp.run()