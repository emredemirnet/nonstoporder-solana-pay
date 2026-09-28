# nonstoporder-solana-pay

Solana Pay (USDC) checkout for [NonstopOrder](https://nonstoporder.com) self-ordering kiosks.
Built during **Colosseum Crypto World's Fair 2026** (Payments & Remittance).

NonstopOrder is a live, Tyro EFTPOS-certified kiosk platform for Australian food trucks and
caravans. This repo adds a second payment rail: the kiosk shows a Solana Pay QR, the customer
pays in USDC from any Solana wallet, and the order confirms the moment the transfer lands.
No custody — funds settle straight to the vendor's wallet.

> The core product (menus, kitchen display, Tyro flow) lives in a separate private repo and is
> not part of this submission. This repo is the integration module only.

## Architecture

```
Kiosk (React/Vite)                      Backend (Flask)                     Solana
─────────────────                       ───────────────                     ──────
POST /api/solana/intent ──────────────► mint reference, AUD→USDC,
                        ◄────────────── return recipient/amount/mint/ref
encodeURL() + createQR()  → QR shown
                                                                 customer scans, wallet
                                                                 signs USDC transfer ──► chain
GET /api/solana/status/:ref (poll) ───► getSignaturesForAddress(ref)
                                        getTransaction → verify mint,
                                        recipient, amount
                        ◄────────────── {status: paid, signature}
                                        ORDER_PAID_HOOK(order_id, sig)
show "Payment received"                 → order marked paid (same path as Tyro)
```

## Layout

| Path | What |
|---|---|
| `server/solana_pay.py` | Flask blueprint: intent creation, on-chain verification, paid hook |
| `server/dev_server.py` | Standalone dev server on :5002 for testing without the main app |
| `kiosk/SolanaPayCheckout.jsx` | React panel: fetches intent, renders QR, polls status |
| `.env.example` | Config (network, RPC, merchant wallet, USDC mint, FX rate) |

## Run locally

```bash
pip install -r server/requirements.txt
cp .env.example .env            # set SOLANA_MERCHANT_WALLET to a devnet wallet pubkey
set -a; source .env; set +a
python3 server/dev_server.py
```

```bash
# create an intent
curl -s -X POST localhost:5002/api/solana/intent \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"demo-1","amount_aud":18.50}'
# poll
curl -s localhost:5002/api/solana/status/<reference>
```

Pay the returned `url` from a devnet wallet (Phantom → Settings → Developer → Testnet mode),
using devnet USDC from https://faucet.circle.com. Status flips to `paid` after confirmation.

Kiosk side: `npm i @solana/pay @solana/web3.js bignumber.js`, drop `SolanaPayCheckout.jsx`
into the payment step and pass `orderId`, `amountAud`, `onPaid`.

## Integrating into the main app

```python
from solana_pay import bp as solana_bp, set_order_paid_hook
app.register_blueprint(solana_bp)
set_order_paid_hook(mark_order_paid)  # reuse the Tyro "approved" path
```

Replace the in-memory `_intents` dict with a column on `orders`
(`solana_reference`, `solana_amount_usdc`, `solana_signature`) before production.

## Status / roadmap

- [x] Transfer-request URL + QR
- [x] On-chain verification via JSON-RPC (mint, recipient, amount, tx success)
- [x] Paid hook into existing order flow
- [x] First end-to-end devnet payment: [5sKonK2B…](https://explorer.solana.com/tx/5sKonK2B17uV3coCFr4STd2QoikvU5cRPoSdj7ebZTQg6bA2c7rkaZYwiE9bHxP8kXoqAqVLCDNiAt32yQWx1h5i?cluster=devnet)
- [ ] Persist intents on the orders table
- [ ] Live AUD→USDC rate
- [ ] Mainnet config + dedicated RPC
- [ ] Field test on a live kiosk

## License

MIT
