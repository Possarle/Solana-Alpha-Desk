from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select

from ..db import Wallet, WalletEvent
from ..providers.solana import SOL, USDC
from ..schemas import now, valid_address


def parse_transaction(tx: dict, owner: str) -> dict:
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return {"kind": "failed", "deltas": [], "fee_sol": (meta.get("fee") or 0) / 1e9}
    deltas = defaultdict(Decimal)
    for label, sign in [("preTokenBalances", -1), ("postTokenBalances", 1)]:
        for b in meta.get(label, []):
            if b.get("owner") == owner:
                q = b["uiTokenAmount"]
                deltas[b["mint"]] += sign * Decimal(q["amount"]) / Decimal(10) ** q["decimals"]
    rows = [
        {"mint": mint, "delta": float(q), "direction": "inflow" if q > 0 else "outflow"}
        for mint, q in deltas.items()
        if q != 0
    ]
    noncash = [x for x in rows if x["mint"] not in (USDC, SOL)]
    quote = deltas.get(USDC, Decimal(0))
    probable = len(noncash) == 1 and quote * Decimal(str(noncash[0]["delta"])) < 0
    return {
        "kind": "probable_swap" if probable else "transfer_or_unclassified",
        "deltas": rows,
        "side": ("buy" if noncash[0]["delta"] > 0 else "sell") if probable else None,
        "mint": noncash[0]["mint"] if probable else None,
        "quote_usdc_delta": float(quote),
        "fee_sol": (meta.get("fee") or 0) / 1e9,
        "method": "Deltas de saldo; swap provável só com USDC oposto e um único token. Transferências não geram lucro.",
    }


class SmartMoneyAgent:
    def __init__(self, db, rpc):
        self.db, self.rpc = db, rpc

    async def poll(self) -> set[str]:
        candidates = set()
        with self.db.session() as s:
            wallets = s.scalars(select(Wallet)).all()
        for wallet in wallets:
            try:
                signatures = await self.rpc.call(
                    "getSignaturesForAddress",
                    [wallet.address, {"limit": 10, "commitment": "confirmed"}],
                    ttl=20,
                )
                for sig in reversed(signatures):
                    key = wallet.address + ":" + sig["signature"]
                    with self.db.session() as s:
                        exists = s.get(WalletEvent, key)
                    if exists:
                        continue
                    tx = await self.rpc.call(
                        "getTransaction",
                        [
                            sig["signature"],
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                                "commitment": "confirmed",
                            },
                        ],
                        ttl=300,
                    )
                    if tx is None:
                        continue  # retry on next poll; null is not confirmation
                    parsed = parse_transaction(tx, wallet.address)
                    stamp = tx.get("blockTime") or now()
                    with self.db.session() as s:
                        s.add(
                            WalletEvent(
                                key=key,
                                wallet=wallet.address,
                                at=stamp,
                                data=parsed | {"signature": sig["signature"]},
                            )
                        )
                    if parsed.get("side") == "buy" and now() - stamp < 600:
                        candidates.add(valid_address(parsed["mint"]))
                        self.db.event(
                            "smart_money",
                            f"Entrada provável em carteira monitorada: {wallet.label or wallet.address[:8]}",
                            data={"mint": parsed["mint"], "wallet": wallet.address},
                        )
                with self.db.session() as s:
                    row = s.get(Wallet, wallet.address)
                    if row:
                        row.data = {
                            "last_poll": now(),
                            "status": "active",
                            "coverage": "Últimas 10 assinaturas por consulta; lacunas possíveis",
                        }
            except Exception as exc:
                with self.db.session() as s:
                    row = s.get(Wallet, wallet.address)
                    if row:
                        row.data = {"last_poll": now(), "status": "unavailable", "error": type(exc).__name__}
        return candidates

    def signals(self, mint: str) -> dict:
        with self.db.session() as s:
            rows = s.scalars(select(WalletEvent).where(WalletEvent.at > now() - 600)).all()
            active = set(s.scalars(select(Wallet.address)).all())
        buys = [
            x
            for x in rows
            if x.wallet in active and x.data.get("mint") == mint and x.data.get("side") == "buy"
        ]
        wallets = sorted({x.wallet for x in buys})
        return {
            "wallets": wallets,
            "count": len(wallets),
            "recurrences": len(buys),
            "simultaneous_10m": len(wallets) >= 2,
            "score": min(100, len(wallets) * 25) if wallets else None,
        }

    def report(self, address: str) -> dict:
        with self.db.session() as s:
            rows = s.scalars(
                select(WalletEvent).where(WalletEvent.wallet == address).order_by(WalletEvent.at)
            ).all()
        # Average-cost inventory for the observed USDC swaps only; transfers taint inventory.
        inventory, realized, fees, tainted = {}, 0.0, 0.0, set()
        for row in rows:
            d = row.data
            fees += d.get("fee_sol", 0)
            if d.get("kind") != "probable_swap":
                tainted.update(x["mint"] for x in d.get("deltas", []))
                continue
            mint = d["mint"]
            delta = next(x["delta"] for x in d["deltas"] if x["mint"] == mint)
            qty, cost = inventory.get(mint, (0.0, 0.0))
            if delta > 0:
                inventory[mint] = (qty + delta, cost - d["quote_usdc_delta"])
            elif qty >= -delta and mint not in tainted:
                basis = cost * (-delta / qty)
                realized += d["quote_usdc_delta"] - basis
                inventory[mint] = (qty + delta, cost - basis)
            else:
                tainted.add(mint)
        return {
            "sample_realized_usdc_before_network_fees": realized,
            "fees_sol": fees,
            "open_inventory": [
                {"mint": k, "qty": v[0], "cost_usdc": v[1]} for k, v in inventory.items() if v[0] > 0
            ],
            "unreliable_mints": sorted(tainted),
            "profitable": None,
            "method": "Custo médio apenas nos swaps prováveis USDC observados. Sem marcação integral, taxas USD ou histórico completo; não classifica lucratividade.",
        }
