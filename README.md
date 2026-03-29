\# K8sWhisperer 🤖



An autonomous Kubernetes healing agent with cryptographic Human-In-The-Loop (HITL) approval.



\## Architecture

Observe → Detect → Diagnose → Safety Gate → HITL (Web UI + Stellar) → Execute → Audit Log



\## Quickstart

```bash

\# Install deps

pip install -r requirements.txt



\# Start minikube

minikube start



\# Apply RBAC

kubectl apply -f k8s/rbac.yaml



\# Apply failure scenarios

kubectl apply -f yamls/crash-loop.yaml

kubectl apply -f yamls/imagepull-backoff.yaml



\# Run agent

python test\_phase8.py



\# View dashboard

python dashboard.py

```



\## Stack

\- LangGraph — agent orchestration

\- Groq LLaMA 3.3 — AI diagnosis

\- FastAPI — HITL web interface

\- Stellar Testnet — cryptographic approval record

\- MCP — kubectl as typed tools

```

