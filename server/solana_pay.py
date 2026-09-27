"""
Solana Pay (USDC) checkout for NonstopOrder kiosks.

Flask blueprint. Mount in the main server with:
    from solana_pay import bp as solana_bp, set_order_paid_hook
    app.register_blueprint(solana_bp)
    set_order_paid_hook(mark_order_paid)   # your existing "paid" logic

Flow
----
1. Kiosk POSTs /api/solana/intent with {order_id, amount_aud}.
   We mint a unique `reference` pubkey, convert AUD -> USDC, and return
   everything the kiosk needs to build a Solana Pay URL + QR.
2. Customer scans, pays from any Solana wallet.
3. Kiosk polls GET /api/solana/status/<reference>. We look the reference up
   on-chain (getSignaturesForAddress), fetch the tx, and verify recipient,
   mint and amount.
4. On success we call ORDER_PAID_HOOK(order_id, signature) so the main app
   marks the order paid exactly like it does after a Tyro approval.

No custody: funds go straight to MERCHANT_WALLET.
Only stdlib + requests; raw JSON-RPC keeps the dependency surface tiny.
"""
import os
import time
import secrets
from decimal import Decimal, ROUND_UP
from urllib.parse import quote

import requests
from flask import Blueprint, request, jsonify

bp = Blueprint("solana_pay", __name__)

# ---------- config ----------
RPC_URL = os.environ.get("SOLANA_RPC_URL", "https://api.devnet.solana.com")
NETWORK = os.environ.get("SOLANA_NETWORK", "devnet")  # devnet | mainnet-beta
MERCHANT_WALLET = os.environ.get("SOLANA_MERCHANT_WALLET", "")
USDC_MINT = os.environ.get(
    "SOLANA_USDC_MINT",
    # devnet USDC (Circle). Mainnet: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
    "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
)
USDC_DECIMALS = 6
LABEL = os.environ.get("SOLANA_PAY_LABEL", "NonstopOrder")
# Static fallback; replace fx_rate() with a live feed before mainnet.
AUD_USDC_RATE = Decimal(os.environ.get("AUD_USDC_RATE", "0.66"))
INTENT_TTL_SEC = int(os.environ.get("SOLANA_INTENT_TTL", "900"))

# In-memory intent store. Swap for the orders table in the main app.
_intents = {}  # reference -> dict

ORDER_PAID_HOOK = None


def set_order_paid_hook(fn):
    """fn(order_id: str, signature: str) -> None"""
    global ORDER_PAID_HOOK
    ORDER_PAID_HOOK = fn


# ---------- helpers ----------
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(raw) - len(raw.lstrip(b"\0"))
    return "1" * pad + out


def new_reference() -> str:
    """Random 32-byte key, base58. Solana Pay only needs it as a read-only
    account in the transfer instruction, so it need not be on-curve."""
    return b58encode(secrets.token_bytes(32))


def fx_rate() -> Decimal:
    """AUD -> USDC. Static for the hackathon; wire to a price feed later."""
    return AUD_USDC_RATE


def aud_to_usdc(amount_aud) -> Decimal:
    usdc = Decimal(str(amount_aud)) * fx_rate()
    return usdc.quantize(Decimal("0.000001"), rounding=ROUND_UP)


def rpc(method, params):
    r = requests.post(
        RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


def build_pay_url(reference: str, amount_usdc: Decimal, order_id: str) -> str:
    # https://docs.solanapay.com/spec#transfer-request
    amt = format(amount_usdc.normalize(), "f")
    return (
        f"solana:{MERCHANT_WALLET}"
        f"?amount={amt}"
        f"&spl-token={USDC_MINT}"
        f"&reference={reference}"
        f"&label={quote(LABEL)}"
        f"&message={quote(f'Order {order_id}')}"
        f"&memo={quote(f'NSO-{order_id}')}"
    )


def find_transfer(tx, expected_amount: Decimal):
    """Check a jsonParsed tx for a USDC credit to MERCHANT_WALLET >= amount."""
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return None, "tx_failed"
    pre = {b["accountIndex"]: b for b in meta.get("preTokenBalances", [])}
    for b in meta.get("postTokenBalances", []):
        if b.get("mint") != USDC_MINT or b.get("owner") != MERCHANT_WALLET:
            continue
        after = Decimal(b["uiTokenAmount"]["amount"])
        before = Decimal(pre.get(b["accountIndex"], {})
                         .get("uiTokenAmount", {}).get("amount", "0"))
        received = (after - before) / (Decimal(10) ** USDC_DECIMALS)
        if received >= expected_amount:
            return received, None
        return received, "amount_too_low"
    return None, "no_usdc_transfer_to_merchant"


def check_reference(reference: str):
    """Look the reference up on-chain. Returns (status, detail)."""
    sigs = rpc("getSignaturesForAddress", [reference, {"limit": 5}])
    if not sigs:
        return "pending", None
    for s in sigs:
        if s.get("err"):
            continue
        sig = s["signature"]
        tx = rpc("getTransaction", [sig, {"encoding": "jsonParsed",
                                          "commitment": "confirmed",
                                          "maxSupportedTransactionVersion": 0}])
        if not tx:
            continue
        received, err = find_transfer(tx, _intents[reference]["amount_usdc"])
        if err:
            return "invalid", {"signature": sig, "reason": err,
                               "received": str(received)}
        return "paid", {"signature": sig, "received": str(received),
                        "confirmation": s.get("confirmationStatus")}
    return "pending", None


# ---------- routes ----------
@bp.post("/api/solana/intent")
def create_intent():
    if not MERCHANT_WALLET:
        return jsonify(error="SOLANA_MERCHANT_WALLET not configured"), 500
    data = request.get_json(force=True) or {}
    order_id = str(data.get("order_id") or "").strip()
    amount_aud = data.get("amount_aud")
    if not order_id or amount_aud is None:
        return jsonify(error="order_id and amount_aud required"), 400

    reference = new_reference()
    amount_usdc = aud_to_usdc(amount_aud)
    _intents[reference] = {
        "order_id": order_id,
        "amount_aud": str(amount_aud),
        "amount_usdc": amount_usdc,
        "created_at": time.time(),
        "status": "pending",
    }
    return jsonify({
        "reference": reference,
        "url": build_pay_url(reference, amount_usdc, order_id),
        "recipient": MERCHANT_WALLET,
        "amount_usdc": str(amount_usdc),
        "amount_aud": str(amount_aud),
        "mint": USDC_MINT,
        "network": NETWORK,
        "expires_in": INTENT_TTL_SEC,
    })


@bp.get("/api/solana/status/<reference>")
def intent_status(reference):
    intent = _intents.get(reference)
    if not intent:
        return jsonify(error="unknown reference"), 404
    if intent["status"] == "paid":
        return jsonify(status="paid", **intent["result"])
    if time.time() - intent["created_at"] > INTENT_TTL_SEC:
        intent["status"] = "expired"
        return jsonify(status="expired")

    try:
        status, detail = check_reference(reference)
    except Exception as e:  # RPC hiccup: stay pending, kiosk keeps polling
        return jsonify(status="pending", rpc_error=str(e))

    if status == "paid":
        intent["status"] = "paid"
        intent["result"] = detail
        if ORDER_PAID_HOOK:
            ORDER_PAID_HOOK(intent["order_id"], detail["signature"])
        return jsonify(status="paid", **detail)
    if status == "invalid":
        return jsonify(status="invalid", **detail)
    return jsonify(status="pending")


@bp.get("/api/solana/config")
def config():
    """Kiosk reads this for network + label. No secrets here."""
    return jsonify(network=NETWORK, recipient=MERCHANT_WALLET, mint=USDC_MINT,
                   label=LABEL, aud_usdc_rate=str(fx_rate()))
