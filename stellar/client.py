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
    return kp


def fund_account(public_key: str):
    import urllib.request, ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"https://friendbot.stellar.org?addr={public_key}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, context=ctx)
        print(f"[STELLAR] Account funded via Friendbot")
        time.sleep(3)
    except Exception as e:
        print(f"[STELLAR] Funding error (may already be funded): {e}")


def record_approval_on_chain(pod: str, action: str, approved: bool, keypair: Keypair) -> str:
    server = Server(HORIZON_URL)

    try:
        account = server.load_account(keypair.public_key)
    except Exception:
        print("[STELLAR] Account not found, funding via Friendbot...")
        fund_account(keypair.public_key)
        try:
            account = server.load_account(keypair.public_key)
        except Exception as e:
            print(f"[STELLAR] Could not load account: {e}")
            return ""

    decision = "APPROVED" if approved else "REJECTED"
    memo_text = f"{decision}:{pod[:10]}:{action[:8]}"[:28]

    # Send 1 XLM to self — simplest valid transaction, just carries the memo
    tx = (
        TransactionBuilder(
            source_account=account,
            network_passphrase=NETWORK,
            base_fee=100,
        )
        .add_text_memo(memo_text)
        .append_payment_op(
            destination=keypair.public_key,
            asset=Asset.native(),
            amount="1",
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