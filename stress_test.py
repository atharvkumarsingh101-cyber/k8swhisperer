# stress_test.py
import subprocess
import time

YAMLS = [
    "yamls/crash-loop.yaml",
    "yamls/pending-pod.yaml",
    "yamls/imagepull-backoff.yaml",
]

print("=== K8sWhisperer Stress Test ===")
print("Applying all failure scenarios simultaneously...\n")

for yaml in YAMLS:
    result = subprocess.run(["kubectl", "apply", "-f", yaml],
                            capture_output=True, text=True)
    print(f"Applied {yaml}: {result.stdout.strip()}")

print("\nWaiting 30 seconds for failures to manifest...")
time.sleep(30)

print("\nRunning agent against all failures...\n")

from hitl_server import start_server_background
from agent.graph import graph
from agent.state import ClusterState

start_server_background()

initial_state: ClusterState = {
    "events": [],
    "anomalies": [],
    "current_anomaly": None,
    "diagnosis": "",
    "plan": None,
    "approved": False,
    "result": "",
    "audit_log": [],
}

config = {"configurable": {"thread_id": "stress-test-1"}}
result = graph.invoke(initial_state, config=config)
print(f"\nStress test complete. Result: {result.get('result', 'n/a')}")

print("\nCleaning up...")
subprocess.run(["kubectl", "delete", "pod", "crash-loop-pod",
                "pending-pod", "imagepull-pod", "--ignore-not-found"])
print("Done.")