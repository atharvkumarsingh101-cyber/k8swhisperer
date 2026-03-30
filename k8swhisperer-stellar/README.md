# K8sWhisperer — Stellar On-Chain Audit Log

## Project Title
K8sWhisperer: Autonomous Kubernetes Healing Agent with Stellar Blockchain Audit Trail

## Project Description
K8sWhisperer is an autonomous Kubernetes healing agent that detects pod failures, diagnoses root causes using AI, and either auto-resolves them or routes them through a Human-in-the-Loop (HITL) approval flow. Every decision the agent makes — auto-fix, human approval, rejection, alert — is recorded as an immutable event on the Stellar blockchain via a Soroban smart contract.

This creates a tamper-proof, cryptographically verifiable audit trail of every healing action taken in your cluster. Instead of audit logs that live in a database someone can edit, every K8s remediation decision is permanently recorded on-chain.

## Project Vision
Kubernetes operations teams need trust. When an autonomous agent restarts pods, scales deployments, or alerts on-call engineers, there must be an irrefutable record of what happened, when, and why. Traditional log files can be deleted or altered. A Stellar blockchain record cannot.

K8sWhisperer bridges autonomous AI operations with blockchain-level accountability — every HITL decision, every auto-remediation, every human alert is on-chain forever.

## Key Features
- Soroban smart contract stores all audit events (event type, pod name, detail, timestamp) immutably on Stellar testnet
- React + Tailwind frontend reads events live from the contract using stellar-sdk
- Python bridge (`stellar_logger.py`) integrates directly into the existing K8sWhisperer pipeline
- Manual event logging form in the UI — submit any event type directly to the chain
- Clickable block explorer links for every transaction — one click to verify on Stellar Expert
- Auto-refreshes every 15 seconds, polling the contract for new events
- Stats bar shows total on-chain events, HITL decisions, auto-resolutions, and human alerts

## Deployed Smart Contract Details

### Contract ID
```
<!-- REPLACE THIS after running: stellar contract deploy -->
CXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

### Block Explorer
View the deployed contract on Stellar Expert (testnet):
```
https://stellar.expert/explorer/testnet/contract/CXXX...
```

> **Screenshot:** Add a screenshot of the block explorer page showing the deployed contract here after deployment.
> Example: `![Block Explorer](./docs/block-explorer.png)`

## UI Screenshots

> Add screenshots of the running React dashboard here after deployment.
> - `![Dashboard](./docs/dashboard.png)`
> - `![Event Card](./docs/event-card.png)`

## Demo

- **Live app:** (deploy to Vercel/Netlify and add URL here)
- **Demo video:** (record a Loom/YouTube walkthrough and add URL here)

## Project Setup Guide

### Prerequisites
- Node.js 18+
- Python 3.10+
- Rust (for building the Soroban contract)
- Stellar CLI

---

### Step 1 — Install Rust

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# Add the WASM target required by Soroban
rustup target add wasm32-unknown-unknown
```

---

### Step 2 — Install the Stellar CLI

```bash
# macOS / Linux
cargo install --locked stellar-cli --features opt

# Verify
stellar --version
```

---

### Step 3 — Generate and fund a testnet keypair

```bash
# Generate a new keypair
stellar keys generate --network testnet mykey

# Check your public key
stellar keys address mykey

# Fund it via Friendbot (free testnet XLM)
curl "https://friendbot.stellar.org?addr=$(stellar keys address mykey)"
```

---

### Step 4 — Build and deploy the Soroban contract

```bash
cd contracts/audit-log

# Build
stellar contract build

# Deploy to testnet
stellar contract deploy \
  --wasm target/wasm32-unknown-unknown/release/audit_log.wasm \
  --network testnet \
  --source mykey

# This prints your CONTRACT_ID — copy it, you need it everywhere
```

---

### Step 5 — Configure environment variables

**Root (Python bridge):**
```bash
cp .env.example .env
# Edit .env:
#   CONTRACT_ID=C...your contract id...
#   STELLAR_SECRET_KEY=S...your secret key...
```

**Frontend:**
```bash
cp frontend/.env.example frontend/.env
# Edit frontend/.env:
#   REACT_APP_CONTRACT_ID=C...your contract id...
#   REACT_APP_STELLAR_SECRET_KEY=S...your secret key...
```

---

### Step 6 — Run the React frontend

```bash
cd frontend
npm install
npm start
# Opens at http://localhost:3000
```

---

### Step 7 — Integrate the Python bridge into K8sWhisperer

In your existing `agent/logger.py`, add two lines after every `log_event()` call:

```python
from stellar_logger import log_audit_event

# Inside your existing log_event function, add:
log_audit_event(event_type, pod_name, json.dumps(data)[:200])
```

Or test the bridge standalone:
```bash
pip install -r requirements.txt
python stellar_logger.py
```

---

### Step 8 — Verify on-chain

After logging events, visit:
```
https://stellar.expert/explorer/testnet/contract/YOUR_CONTRACT_ID
```
You will see every invocation of `log_event` listed as a transaction.

---

## Future Scope

- **Mainnet deployment** — move from testnet to Stellar mainnet for production clusters
- **Multi-sig HITL approvals** — require M-of-N human approvers to sign a transaction before a risky action executes
- **Token-gated access** — use Stellar custom assets to control who can call `log_event` (prevent spoofing)
- **On-chain HITL voting** — instead of the current web UI, approvals happen as Stellar transactions, making the approval itself the blockchain record
- **Cross-cluster audit federation** — multiple K8sWhisperer instances across clusters all write to the same contract, giving a single unified audit ledger
- **Webhook from contract events** — use Stellar's event streaming to trigger Slack/PagerDuty alerts when specific event types land on-chain

---

## Repository Structure

```
k8swhisperer-stellar/
├── contracts/
│   ├── Cargo.toml                  # Workspace
│   └── audit-log/
│       ├── Cargo.toml
│       └── src/
│           └── lib.rs              # Soroban smart contract (Rust)
├── frontend/
│   ├── public/
│   │   └── index.html
│   ├── src/
│   │   ├── stellar.js              # stellar-sdk integration (calls contract)
│   │   ├── App.js                  # Main dashboard
│   │   ├── index.js
│   │   ├── index.css
│   │   └── components/
│   │       ├── EventCard.js
│   │       ├── StatsBar.js
│   │       └── LogForm.js
│   ├── package.json
│   ├── tailwind.config.js
│   └── .env.example
├── stellar_logger.py               # Python bridge — pipeline → blockchain
├── .env.example
├── .gitignore
└── README.md
```
