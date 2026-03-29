from agent.monitor import get_all_pods
from agent.detector import detect_failures
from agent.diagnose import diagnose_anomaly
from agent.executor import build_plan, safety_gate, execute_plan

pods = get_all_pods()
failures = detect_failures(pods)

if not failures:
    print('No failures detected')
else:
    for anomaly in failures[:2]:
        print(f'\n--- {anomaly.failure_type} | {anomaly.pod} ---')
        diagnosis = diagnose_anomaly(anomaly)
        plan = build_plan(anomaly, diagnosis)
        print(f'Plan: {plan.action} | Blast: {plan.blast_radius} | Confidence: {plan.confidence}')
        auto = safety_gate(plan)
        print(f'Auto-execute: {auto}')
        if auto:
            result = execute_plan(plan)
            print(f'Result: {result}')
        else:
            print('HITL required — waiting for human approval')