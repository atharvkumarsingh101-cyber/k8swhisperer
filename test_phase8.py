from hitl_server import start_server_background
from agent.graph import graph
from agent.state import ClusterState

# Start FastAPI in background
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

config = {"configurable": {"thread_id": "run-1"}}

print("=== Running K8sWhisperer with Web HITL ===\n")
result = graph.invoke(initial_state, config=config)
print(f"\n=== DONE | Result: {result.get('result', 'no action')} ===")