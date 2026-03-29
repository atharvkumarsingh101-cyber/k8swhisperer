from agent.graph import graph
from agent.state import ClusterState

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

config = {"configurable": {"thread_id": "test-run-1"}}

print("=== Running K8sWhisperer LangGraph ===\n")
result = graph.invoke(initial_state, config=config)
print(f"\n=== DONE | Result: {result.get('result', 'no action')} ===")