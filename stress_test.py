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

# --- UPDATED IMPORTS ---
from hitl_server import run_hitl_server
from agent.graph import graph
from agent.state import ClusterState

# --- UPDATED FUNCTION CALL ---
run_hitl_server()

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
for yaml in YAMLS:
    subprocess.run(["kubectl", "delete", "-f", yaml, "--ignore-not-found"])
print("Done.")