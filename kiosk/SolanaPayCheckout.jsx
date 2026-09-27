// Solana Pay checkout panel for the NonstopOrder kiosk (React/Vite).
// Deps:  npm i @solana/pay @solana/web3.js bignumber.js
//
// <SolanaPayCheckout orderId="1234" amountAud={18.5}
//                    apiBase="" onPaid={(sig) => goToReceipt(sig)}
//                    onCancel={() => backToPaymentChoice()} />
import { useEffect, useRef, useState } from "react";
import { createQR, encodeURL } from "@solana/pay";
import { PublicKey } from "@solana/web3.js";
import BigNumber from "bignumber.js";

const POLL_MS = 2500;

export default function SolanaPayCheckout({ orderId, amountAud, apiBase = "", onPaid, onCancel }) {
  const [intent, setIntent] = useState(null);
  const [status, setStatus] = useState("creating"); // creating | pending | paid | expired | invalid | error
  const [error, setError] = useState(null);
  const qrRef = useRef(null);

  // 1. create intent
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${apiBase}/api/solana/intent`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ order_id: orderId, amount_aud: amountAud }),
        });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || "intent failed");
        if (!cancelled) { setIntent(j); setStatus("pending"); }
      } catch (e) {
        if (!cancelled) { setError(e.message); setStatus("error"); }
      }
    })();
    return () => { cancelled = true; };
  }, [orderId, amountAud, apiBase]);

  // 2. render QR (client-side encode keeps the QR identical to the server URL)
  useEffect(() => {
    if (!intent || !qrRef.current) return;
    const url = encodeURL({
      recipient: new PublicKey(intent.recipient),
      amount: new BigNumber(intent.amount_usdc),
      splToken: new PublicKey(intent.mint),
      reference: new PublicKey(intent.reference),
      label: "NonstopOrder",
      message: `Order ${orderId}`,
      memo: `NSO-${orderId}`,
    });
    const qr = createQR(url, 360, "transparent");
    qrRef.current.innerHTML = "";
    qr.append(qrRef.current);
  }, [intent, orderId]);

  // 3. poll status
  useEffect(() => {
    if (!intent || status !== "pending") return;
    const t = setInterval(async () => {
      try {
        const r = await fetch(`${apiBase}/api/solana/status/${intent.reference}`);
        const j = await r.json();
        if (j.status === "paid") { setStatus("paid"); onPaid?.(j.signature); }
        else if (j.status === "expired" || j.status === "invalid") setStatus(j.status);
      } catch { /* transient; keep polling */ }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [intent, status, apiBase, onPaid]);

  return (
    <div className="solana-pay">
      <h2>Pay with Solana</h2>
      {status === "creating" && <p>Preparing payment…</p>}
      {status === "error" && <p className="err">Couldn't start Solana payment: {error}</p>}
      {intent && (
        <>
          <p className="amount">
            <strong>{intent.amount_usdc} USDC</strong> <span>(A${intent.amount_aud})</span>
          </p>
          <div ref={qrRef} className="qr" />
          {status === "pending" && <p>Scan with Phantom, Solflare or any Solana wallet.</p>}
          {status === "paid" && <p className="ok">Payment received. Preparing your order.</p>}
          {status === "expired" && <p className="err">This QR expired. Start again.</p>}
          {status === "invalid" && <p className="err">Payment didn't match the order. Please see staff.</p>}
          {intent.network !== "mainnet-beta" && <p className="devnet">devnet — test funds only</p>}
        </>
      )}
      <button onClick={onCancel}>Back</button>
    </div>
  );
}
