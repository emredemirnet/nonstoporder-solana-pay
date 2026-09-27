"""Standalone dev server so the module can be tested without the main app.
    export SOLANA_MERCHANT_WALLET=<your devnet wallet pubkey>
    python3 server/dev_server.py   # http://localhost:5002
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from flask import Flask
from flask_cors import CORS
import solana_pay

app = Flask(__name__)
CORS(app)
app.register_blueprint(solana_pay.bp)
solana_pay.set_order_paid_hook(
    lambda order_id, sig: print(f"[order {order_id}] PAID via Solana tx {sig}"))

if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5002)), debug=True)
