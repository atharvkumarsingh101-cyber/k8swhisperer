from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from agent.state import ClusterState
from agent.monitor import get_all_pods, get_all_nodes, get_all_deployments
from agent.detector import detect_failures, detect_node_issues, detect_deployment_stall
from agent.diagnose import diagnose_anomaly
from agent.executor import build_plan, safety_gate, execute_plan
from agent.logger import log_event


def observe_node(state):
    print("[GRAPH] observe_node")
    pods = get_all_pods()
    nodes = get_all_nodes()
    deps = get_all_deployments()
    return {"events": [{"pods": pods, "nodes": nodes, "deployments": deps}]}


def detect_node(state):
    print("[GRAPH] detect_node")
    latest = state["events"][-1]
    anomalies = detect_failures(latest["pods"])
    anomalies += detect_node_issues(latest["nodes"].get("items", []))
    anomalies += detect_deployment_stall(latest["deployments"])
    log_event("DETECT", "cluster", str(len(anomalies)) + " anomalies found", {})
    current = anomalies[0] if anomalies else None
    return {"anomalies": anomalies, "current_anomaly": current}


def diagnose_node(state):
    print("[GRAPH] diagnose_node")
    anomaly = state["current_anomaly"]
    if not anomaly:
        return {"diagnosis": "no anomaly"}
    result = diagnose_anomaly(anomaly)
    log_event("DIAGNOSE", anomaly.pod, result["root_cause"], result)
    plan = build_plan(anomaly, result)
    return {"diagnosis": result["root_cause"], "plan": plan}


def safety_gate_node(state):
    print("[GRAPH] safety_gate_node")
    plan = state.get("plan")
    if not plan:
        return {"approved": False}
    auto = safety_gate(plan)
    log_event("SAFETY_GATE", plan.target_pod, "Auto=" + str(auto), {})
    return {"approved": auto}


def hitl_wait_node(state):
    print("[GRAPH] hitl_wait_node - opening browser for approval...")
    plan = state["plan"]
    log_event("HITL_REQUESTED", plan.target_pod, "Waiting for approval: " + plan.action, {})
    from hitl_server import request_approval
    approved = request_approval("run-1", {
        "pod": plan.target_pod,
        "action": plan.action,
        "blast_radius": plan.blast_radius,
        "diagnosis": state["diagnosis"],
    })
    log_event("HITL_DECISION", plan.target_pod, "Decision: " + str(approved), {})
    return {"approved": approved}


def execute_node(state):
    print("[GRAPH] execute_node")
    plan = state["plan"]
    result = execute_plan(plan)
    return {"result": result}


def done_node(state):
    print("[GRAPH] done_node")
    log_event("CYCLE_COMPLETE", "cluster", "Result: " + str(state.get("result", "n/a")), {})
    return {}


def route_after_detect(state):
    if not state.get("current_anomaly"):
        return "done"
    return "diagnose"


def route_after_safety(state):
    if state.get("approved"):
        return "execute"
    return "hitl_wait"


def route_after_hitl(state):
    if state.get("approved"):
        return "execute"
    return "done"


def build_graph():
    builder = StateGraph(ClusterState)
    builder.add_node("observe", observe_node)
    builder.add_node("detect", detect_node)
    builder.add_node("diagnose", diagnose_node)
    builder.add_node("safety_gate", safety_gate_node)
    builder.add_node("hitl_wait", hitl_wait_node)
    builder.add_node("execute", execute_node)
    builder.add_node("done", done_node)
    builder.set_entry_point("observe")
    builder.add_edge("observe", "detect")
    builder.add_conditional_edges("detect", route_after_detect, {"diagnose": "diagnose", "done": "done"})
    builder.add_edge("diagnose", "safety_gate")
    builder.add_conditional_edges("safety_gate", route_after_safety, {"execute": "execute", "hitl_wait": "hitl_wait"})
    builder.add_conditional_edges("hitl_wait", route_after_hitl, {"execute": "execute", "done": "done"})
    builder.add_edge("execute", "done")
    builder.add_edge("done", END)
    memory = MemorySaver()
    return builder.compile(checkpointer=memory)


graph = build_graph()