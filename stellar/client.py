# stellar/client.py
import os
import time
from stellar_sdk import Keypair, Network, Server, TransactionBuilder, Asset

HORIZON_URL = "https://horizon-testnet.stellar.org"
NETWORK = Network.TESTNET_NETWORK_PASSPHRASE


def get_or_create_keypair() -> Keypair:
    secret = os.environ.get("STELLAR_SECRET_KEY")
    if secret:
        return Keypair.from_secret(secret)
    kp = Keypair.random()
    print(f"[STELLAR] New keypair created!")
    print(f"[STELLAR] Public key: {kp.public_key}")
    print(f"[STELLAR] Secret key: {kp.secret}")
    print(f"[STELLAR] Fund at: https://friendbot.stellar.org?addr={kp.public_key}")
    return kp


def fund_account(public_key: str):
    import urllib.request
    url = f"https://friendbot.stellar.org?addr={public_key}"
    try:
        urllib.request.urlopen(url)
        print(f"[STELLAR] Account funded via Friendbot")
        time.sleep(3)
    except Exception as e:
        print(f"[STELLAR] Funding error (may already be funded): {e}")


def record_approval_on_chain(pod: str, action: str, approved: bool, keypair: Keypair) -> str:
    """
    Record a HITL approval decision on the Stellar testnet blockchain.
    Returns the transaction hash.
    """
    server = Server(HORIZON_URL)

    try:
        account = server.load_account(keypair.public_key)
    except Exception:
        print("[STELLAR] Account not found, funding via Friendbot...")
        fund_account(keypair.public_key)
        account = server.load_account(keypair.public_key)

    decision = "APPROVED" if approved else "REJECTED"
    memo_text = f"{decision}:{pod[:10]}:{action[:8]}"[:28]

    tx = (
        TransactionBuilder(
            source_account=account,
            network_passphrase=NETWORK,
            base_fee=100,
        )
        .add_text_memo(memo_text)
        .append_change_trust_op(
            asset=Asset("HITL", keypair.public_key),
            limit="1000"
        )
        .set_timeout(30)
        .build()
    )

    tx.sign(keypair)

    try:
        response = server.submit_transaction(tx)
        tx_hash = response["hash"]
        print(f"[STELLAR] Decision recorded on blockchain!")
        print(f"[STELLAR] TX Hash: {tx_hash}")
        print(f"[STELLAR] View at: https://stellar.expert/explorer/testnet/tx/{tx_hash}")
        return tx_hash
    except Exception as e:
        print(f"[STELLAR] TX failed: {e}")
        return ""