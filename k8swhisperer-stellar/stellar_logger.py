"""
stellar_logger.py
-----------------
Bridge between K8sWhisperer's audit log and the Soroban smart contract
on Stellar testnet.

Every time an audit event is logged in the pipeline, call:
    StellarLogger.log_event(event_type, pod, detail, timestamp)

This sends a transaction to the deployed AuditLog contract on Stellar testnet,
creating an immutable on-chain record of every K8s healing decision.

Setup:
    pip install stellar-sdk
    Set CONTRACT_ID and SECRET_KEY in .env (or environment variables).
"""

import os
import time
from typing import Optional

from stellar_sdk import Keypair, Network, SorobanServer, TransactionBuilder
from stellar_sdk.soroban_rpc import GetTransactionStatus
from stellar_sdk import scval
from dotenv import load_dotenv

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
SOROBAN_RPC_URL = os.getenv("SOROBAN_RPC_URL", "https://soroban-testnet.stellar.org")
CONTRACT_ID     = os.getenv("CONTRACT_ID", "")          # Set after deploy
SECRET_KEY      = os.getenv("STELLAR_SECRET_KEY", "")   # Funded testnet keypair
NETWORK_PASSPHRASE = Network.TESTNET_NETWORK_PASSPHRASE


class StellarLogger:
    """Sends K8sWhisperer audit events to the Soroban AuditLog contract."""

    def __init__(self):
        if not CONTRACT_ID:
            print("[StellarLogger] WARNING: CONTRACT_ID not set. On-chain logging disabled.")
            self._enabled = False
            return
        if not SECRET_KEY:
            print("[StellarLogger] WARNING: STELLAR_SECRET_KEY not set. On-chain logging disabled.")
            self._enabled = False
            return

        self._keypair = Keypair.from_secret(SECRET_KEY)
        self._server  = SorobanServer(SOROBAN_RPC_URL)
        self._enabled = True
        print(f"[StellarLogger] Ready. Contract: {CONTRACT_ID[:12]}...")

    def log_event(
        self,
        event_type: str,
        pod: str,
        detail: str,
        timestamp: Optional[int] = None,
    ) -> Optional[str]:
        """
        Call the contract's log_event function.
        Returns the transaction hash on success, None on failure.
        """
        if not self._enabled:
            return None

        ts = timestamp or int(time.time())

        try:
            account = self._server.load_account(self._keypair.public_key)

            # Build the Soroban invocation
            tx = (
                TransactionBuilder(
                    source_account=account,
                    network_passphrase=NETWORK_PASSPHRASE,
                    base_fee=100,
                )
                .append_invoke_contract_function_op(
                    contract_id=CONTRACT_ID,
                    function_name="log_event",
                    parameters=[
                        scval.to_string(event_type),
                        scval.to_string(pod),
                        scval.to_string(detail),
                        scval.to_uint64(ts),
                    ],
                )
                .set_timeout(30)
                .build()
            )

            # Simulate to get the footprint / fee
            sim = self._server.simulate_transaction(tx)
            if sim.error:
                print(f"[StellarLogger] Simulate error: {sim.error}")
                return None

            tx = self._server.prepare_transaction(tx, sim)
            tx.sign(self._keypair)

            response = self._server.send_transaction(tx)
            tx_hash  = response.hash
            print(f"[StellarLogger] Submitted tx: {tx_hash}")

            # Poll until confirmed
            for _ in range(30):
                result = self._server.get_transaction(tx_hash)
                if result.status == GetTransactionStatus.SUCCESS:
                    print(f"[StellarLogger] Confirmed: {tx_hash}")
                    return tx_hash
                if result.status == GetTransactionStatus.FAILED:
                    print(f"[StellarLogger] Transaction failed: {tx_hash}")
                    return None
                time.sleep(1)

            print(f"[StellarLogger] Timeout waiting for: {tx_hash}")
            return tx_hash

        except Exception as exc:
            print(f"[StellarLogger] Error: {exc}")
            return None

    def get_events(self) -> list:
        """
        Read all stored events from the contract (read-only simulation).
        Returns a list of dicts with keys: event_type, pod, detail, timestamp.
        """
        if not self._enabled:
            return []

        try:
            account = self._server.load_account(self._keypair.public_key)

            tx = (
                TransactionBuilder(
                    source_account=account,
                    network_passphrase=NETWORK_PASSPHRASE,
                    base_fee=100,
                )
                .append_invoke_contract_function_op(
                    contract_id=CONTRACT_ID,
                    function_name="get_events",
                    parameters=[],
                )
                .set_timeout(30)
                .build()
            )

            sim = self._server.simulate_transaction(tx)
            if sim.error or not sim.results:
                return []

            # Parse the returned Vec<AuditEvent>
            result_xdr = sim.results[0].xdr
            from stellar_sdk import xdr as stellarxdr
            import base64
            raw = stellarxdr.SCVal.from_xdr(result_xdr)
            events = []
            if raw.vec:
                for item in raw.vec.sc_vec:
                    fields = item.map.sc_map if item.map else []
                    entry = {}
                    for kv in fields:
                        key = kv.key.sym.sc_symbol.decode()
                        val = kv.val
                        if val.str:
                            entry[key] = val.str.sc_string.decode()
                        elif val.u64:
                            entry[key] = val.u64.uint64
                    events.append(entry)
            return events

        except Exception as exc:
            print(f"[StellarLogger] get_events error: {exc}")
            return []


# ── Singleton ────────────────────────────────────────────────────────────────
_logger: Optional[StellarLogger] = None


def get_logger() -> StellarLogger:
    global _logger
    if _logger is None:
        _logger = StellarLogger()
    return _logger


def log_audit_event(event_type: str, pod: str, detail: str) -> Optional[str]:
    """
    Convenience function — call this from agent/logger.py after every
    existing log_event() call to mirror events on-chain.

    Example integration in agent/logger.py:
        from stellar_logger import log_audit_event
        log_audit_event(event_type, pod_name, json.dumps(data)[:200])
    """
    return get_logger().log_event(event_type, pod, detail)


# ── CLI test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger = StellarLogger()
    tx = logger.log_event(
        event_type="HITL_DECISION",
        pod="crash-loop-pod-test",
        detail="approved: delete pod",
    )
    print(f"Transaction hash: {tx}")

    events = logger.get_events()
    print(f"Events on chain: {len(events)}")
    for e in events:
        print(f"  {e}")
