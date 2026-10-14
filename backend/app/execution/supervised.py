"""Human-reviewed mainnet orders. Independent ledger; scanner cannot call submit."""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text

from ..agents.judge import judge
from ..db import LiveBook, LiveOrder, Token
from ..providers.solana import TOKEN_PROGRAM, USDC
from ..schemas import Market, now, valid_address
from .live_chain import MAINNET_GENESIS, LiveChain
from .transaction_guard import compile_review, sign_review, validate_swap

ACK = "I_ACCEPT_SUPERVISED_MAINNET"
TERMINAL = {"cancelled", "expired", "failed", "finalized"}


def day():
    return datetime.now(timezone.utc).date().isoformat()


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def public_order(row):
    return {
        "id": row.id,
        "at": row.at,
        "status": row.status,
        **{
            k: row.data[k]
            for k in ("preview", "review_hash", "signature", "message", "result")
            if k in row.data
        },
    }


class SupervisedLive:
    def __init__(self, engine):
        self.engine, self.db, self.cfg = engine, engine.db, engine.settings
        self.chain = LiveChain(engine.rpc)
        self.lock = asyncio.Lock()

    def initialize(self):
        with self.db.session() as s:
            if not s.get(LiveBook, 1):
                s.add(LiveBook(id=1, data={"halted": False}))

    def locked_book(self, s):
        if self.db.engine.dialect.name == "sqlite":
            s.execute(text("BEGIN IMMEDIATE"))
        return s.scalar(select(LiveBook).where(LiveBook.id == 1).with_for_update())

    def blockers(self, send=False):
        reasons = []
        try:
            valid_address(self.cfg.live_wallet_address)
        except ValueError:
            reasons.append("Configure o endereço público LIVE_WALLET_ADDRESS")
        if len(self.cfg.dashboard_token.get_secret_value()) < 32:
            reasons.append("DASHBOARD_TOKEN deve ter pelo menos 32 caracteres")
        if not self.cfg.jupiter_api_key.get_secret_value():
            reasons.append("Configure JUPITER_API_KEY no backend")
        if send:
            if not self.cfg.live_enabled:
                reasons.append("LIVE_ENABLED permanece desativado")
            if self.cfg.live_acknowledgement != ACK:
                reasons.append("Aceite explícito de mainnet ausente")
            if not self.cfg.live_keypair_path:
                reasons.append("Arquivo protegido de assinatura não configurado")
            if not self.cfg.enable_scheduler:
                reasons.append("Monitoramento precisa estar ativo")
        return reasons

    def policy(self):
        return {
            k: v
            for k, v in self.cfg.model_dump().items()
            if k.startswith("live_")
            and k not in ("live_keypair_path", "live_acknowledgement", "live_enabled")
        }

    @staticmethod
    def public_book(data):
        # Preserve u64 token precision through JavaScript's JSON number boundary.
        result = {k: str(v) if k.endswith("_raw") else v for k, v in data.items()}
        if result.get("position"):
            result["position"] = {
                k: str(v) if k.endswith("_raw") else v for k, v in result["position"].items()
            }
        return result

    def status(self):
        with self.db.session() as s:
            book = s.get(LiveBook, 1)
            rows = s.scalars(select(LiveOrder).order_by(LiveOrder.at.desc()).limit(50)).all()
        return {
            "mode": "supervised_live",
            "wallet": self.cfg.live_wallet_address,
            "prepare_blockers": self.blockers(),
            "send_blockers": self.blockers(True),
            "book": self.public_book(book.data if book else {}),
            "orders": [public_order(r) for r in rows],
            "limits": {
                "max_order_usdc": self.cfg.live_max_order_usdc,
                "max_position_pct": 1,
                "daily_spend_pct": 3,
                "max_slippage_bps": self.cfg.live_max_slippage_bps,
                "max_fee_lamports": self.cfg.live_max_fee_lamports,
            },
        }

    def fail(self, order_id, message):
        with self.db.session() as s:
            self.locked_book(s)
            row = s.get(LiveOrder, order_id)
            if row and not row.data.get("signature"):
                row.status, row.active_slot = "failed", None
                row.data = row.data | {"message": message}
        self.db.event("live_blocked", message, "warning", {"order_id": order_id})

    async def market(self, mint):
        with self.db.session() as s:
            row = s.get(Token, "solana:" + mint)
        if not row:
            raise ValueError("Token precisa estar no scanner")
        provider = self.engine.gecko if row.data["source"] == "geckoterminal" else self.engine.dex
        m = await provider.refresh(row.data["pool"])
        if m.address != mint:
            raise ValueError("Pool retornou outro mint")
        return m

    async def prepare(self, order_id, mint, side, usdc):
        async with self.lock:
            reasons = self.blockers()
            if reasons:
                raise ValueError("; ".join(reasons))
            if side not in ("buy", "sell"):
                raise ValueError("Lado inválido")
            if not Decimal(str(usdc)).is_finite() or not 0 < Decimal(str(usdc)) <= 100:
                raise ValueError("Valor inválido")
            owner = self.cfg.live_wallet_address
            valid_address(mint)
            if mint == USDC:
                raise ValueError("Escolha um ativo diferente de USDC")
            intent = {"mint": mint, "side": side, "usdc": str(usdc), "wallet": owner}
            with self.db.session() as s:
                book = self.locked_book(s)
                old = s.get(LiveOrder, order_id)
                if old:
                    if old.data["intent"] != intent:
                        raise ValueError("Identificador já usado para outra intenção")
                    return public_order(old)
                if s.scalar(select(LiveOrder).where(LiveOrder.active_slot == 1)):
                    raise ValueError("Existe ordem em revisão ou pendente")
                if book.data.get("wallet", owner) != owner:
                    raise ValueError("Carteira diverge do livro persistido")
                if side == "buy" and (book.data.get("halted") or book.data.get("position")):
                    raise ValueError("Entrada bloqueada: parada ou posição existente")
                if side == "sell" and book.data.get("position", {}).get("mint") != mint:
                    raise ValueError("Não existe posição real correspondente")
                s.add(LiveOrder(id=order_id, active_slot=1, status="preparing", data={"intent": intent}))
            try:
                if await self.chain.call("getGenesisHash", []) != MAINNET_GENESIS:
                    raise ValueError("RPC não corresponde à Solana mainnet")
                security = None
                if side == "buy":
                    async with asyncio.timeout(90):
                        security = await self.engine.security.analyze_complete(mint)
                    if not security.concentration_resolved or security.top10_pct is None:
                        raise ValueError("Concentração integral não comprovada; LIVE bloqueado")
                m = await self.market(mint)
                thresholds = self.engine.thresholds.model_copy(deep=True)
                t = thresholds.model_copy(update={"require_resolved_concentration": True})
                judgment = (
                    judge(
                        m,
                        security,
                        t,
                        self.engine.social.analyze("solana:" + mint),
                        self.engine.smart.signals(mint),
                    )
                    if security
                    else None
                )
                if judgment and not judgment.approved:
                    raise ValueError("; ".join(judgment.reasons))
                if m.age_seconds() > t.max_data_age_seconds:
                    raise ValueError("Mercado antigo")
                im, om = (USDC, mint) if side == "buy" else (mint, USDC)
                # Exits do not require a profitable score, but only ordinary SPL tokens are supported.
                decimals = {}
                for address in (im, om):
                    account = (
                        await self.chain.call(
                            "getAccountInfo", [address, {"encoding": "jsonParsed", "commitment": "finalized"}]
                        )
                    )["value"]
                    if not account or account["owner"] != TOKEN_PROGRAM:
                        raise ValueError("Token-2022 ou programa não suportado")
                    decimals[address] = int(account["data"]["parsed"]["info"]["decimals"])
                    if not 0 <= decimals[address] <= 18:
                        raise ValueError("Precisão não suportada")
                before = await self.chain.balances(owner, im, om)
                cash = before["input"] if side == "buy" else before["output"]
                with self.db.session() as s:
                    book = self.locked_book(s)
                    data = dict(book.data)
                    if "cash_raw" in data and data["cash_raw"] != cash:
                        raise ValueError("Saldo USDC diverge; reconciliação manual necessária")
                    if data.get("day") != day():
                        data.update(day=day(), day_start_raw=cash, spent_raw=0, fee_reserved=0)
                    data.update(wallet=owner, cash_raw=cash)
                    if side == "buy":
                        if before["output"] != 0:
                            raise ValueError("Carteira já possui o ativo; não misturar posições externas")
                        amount = int(Decimal(str(usdc)) * 1_000_000)
                        cap = min(
                            int(self.cfg.live_max_order_usdc * 1e6),
                            int(cash * t.max_position_pct / 100),
                            int((m.liquidity or 0) * 0.0005 * 1e6),
                        )
                        if judgment.unknown:
                            cap //= 2
                        if not 0 < amount <= cap:
                            raise ValueError("Valor excede limite real por posição/liquidez/incerteza")
                        if data["spent_raw"] + amount > int(
                            data["day_start_raw"] * min(3, t.max_daily_loss_pct) / 100
                        ):
                            raise ValueError("Orçamento diário conservador esgotado")
                    else:
                        amount = int(data["position"]["qty_raw"])
                        if before["input"] != amount:
                            raise ValueError("Saldo do ativo diverge da posição")
                    if (
                        data["fee_reserved"] + self.cfg.live_max_fee_lamports
                        > self.cfg.live_daily_fee_lamports
                    ):
                        raise ValueError("Limite diário de taxas SOL")
                    book.data = data
                slip = min(t.max_slippage_bps, self.cfg.live_max_slippage_bps)
                build = await self.engine.jupiter.build(owner, im, om, amount, slip)
                quoted_at = now()
                from decimal import InvalidOperation

                try:
                    impact = Decimal(str(build["priceImpactPct"]))
                except (KeyError, InvalidOperation):
                    raise ValueError("Impacto de preço desconhecido") from None
                if not impact.is_finite() or abs(impact) * 100 > self.cfg.live_max_price_impact_pct:
                    raise ValueError("Impacto de preço excessivo")
                minimum = validate_swap(build, owner, im, om, amount, slip)
                await self.chain.verify_accounts(build, owner, im, om)
                bh = await self.chain.blockhash()
                wire = compile_review(
                    build, owner, bh["blockhash"], self.cfg.live_compute_price_micro_lamports
                )
                simulation = await self.chain.simulate(
                    wire, owner, im, om, before, amount, minimum, self.cfg.live_max_fee_lamports
                )
                expires = quoted_at + self.cfg.live_review_seconds
                if now() >= expires:
                    raise ValueError("Cotação expirou durante preparação")
                preview = {
                    "wallet": owner,
                    "chain": "solana-mainnet",
                    "side": side,
                    "mint": mint,
                    "symbol": m.symbol,
                    "input_mint": im,
                    "output_mint": om,
                    "input_decimals": decimals[im],
                    "output_decimals": decimals[om],
                    "input_raw": str(amount),
                    "expected_output_raw": str(build["outAmount"]),
                    "minimum_output_raw": str(minimum),
                    "slippage_bps": slip,
                    "price_impact_pct": float(impact * 100),
                    "expires_at": expires,
                    "last_valid_block_height": bh["lastValidBlockHeight"],
                    **simulation,
                    "unknown": judgment.unknown
                    if judgment
                    else ["Saída supervisionada; preço não garantido"],
                    "security_notes": security.notes if security else [],
                    "confirmation_phrase": "EXECUTAR " + order_id[:8],
                    "warning": "Operação REAL. Stop e alvo exigem revisão humana da saída. USDC é referência, não garantia de USD.",
                }
                data = {
                    "intent": intent,
                    "preview": preview,
                    "unsigned": wire,
                    "before": before,
                    "thresholds": thresholds.model_dump(),
                    "policy": self.policy(),
                    "market": m.model_dump(),
                    "evidence": judgment.model_dump() if judgment else None,
                }
                data["review_hash"] = digest(data)
                with self.db.session() as s:
                    self.locked_book(s)
                    row = s.get(LiveOrder, order_id)
                    row.data, row.status = data, "review"
                self.db.event(
                    "live_review",
                    "Ordem real preparada; nenhuma assinatura enviada",
                    data={"order_id": order_id},
                )
                return public_order(row)
            except Exception as exc:
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "Preparação interrompida: " + type(exc).__name__
                )
                self.fail(order_id, message)
                raise ValueError(message) from None

    async def confirm(self, order_id, review_hash, phrase):
        async with self.lock:
            reasons = self.blockers(True)
            if reasons:
                raise ValueError("; ".join(reasons))
            with self.db.session() as s:
                book = self.locked_book(s)
                row = s.get(LiveOrder, order_id)
                if not row:
                    raise ValueError("Ordem inexistente")
                d = dict(row.data)
                if row.status != "review":
                    return public_order(row)
                p = d["preview"]
                if review_hash != d["review_hash"] or phrase != p["confirmation_phrase"]:
                    raise ValueError("Confirmação não corresponde à revisão")
                if digest({k: v for k, v in d.items() if k != "review_hash"}) != review_hash:
                    raise ValueError("Revisão adulterada")
                if (
                    p["wallet"] != self.cfg.live_wallet_address
                    or d["thresholds"] != self.engine.thresholds.model_dump()
                    or d["policy"] != self.policy()
                ):
                    raise ValueError("Configuração mudou; prepare outra revisão")
                if book.data.get("halted") and p["side"] == "buy":
                    raise ValueError("Parada real ativa")
                row.status = "validating"
            try:
                if (
                    now() >= p["expires_at"]
                    or Market(**d["market"]).age_seconds() > self.engine.thresholds.max_data_age_seconds
                ):
                    raise ValueError("Revisão expirada")
                before = await self.chain.balances(p["wallet"], p["input_mint"], p["output_mint"])
                if before != d["before"]:
                    raise ValueError("Saldo mudou desde a revisão")
                simulation = await self.chain.simulate(
                    d["unsigned"],
                    p["wallet"],
                    p["input_mint"],
                    p["output_mint"],
                    before,
                    int(p["input_raw"]),
                    int(p["minimum_output_raw"]),
                    self.cfg.live_max_fee_lamports,
                )
                if (
                    simulation["fee_lamports"] > p["fee_lamports"]
                    or simulation["rent_lamports"] > p["rent_lamports"]
                ):
                    raise ValueError("Custos aumentaram; nova revisão necessária")
                height = await self.chain.call("getBlockHeight", [{"commitment": "confirmed"}])
                if height > p["last_valid_block_height"] or now() >= p["expires_at"]:
                    raise ValueError("Validade esgotada")
                if self.blockers(True):
                    raise ValueError("Envio foi desativado")
                if d["thresholds"] != self.engine.thresholds.model_dump() or d["policy"] != self.policy():
                    raise ValueError("Configuração mudou")
                signed, signature = sign_review(d["unsigned"], self.cfg.live_keypair_path, p["wallet"])
                # Write-ahead signature + exposure reservation BEFORE the only network attempt.
                with self.db.session() as s:
                    book = self.locked_book(s)
                    if book.data.get("halted") and p["side"] == "buy":
                        raise ValueError("Parada real ativa")
                    data = dict(book.data)
                    if data.get("day") != day():
                        raise ValueError("Dia mudou; prepare nova revisão")
                    data["fee_reserved"] += self.cfg.live_max_fee_lamports
                    if p["side"] == "buy":
                        data["spent_raw"] += int(p["input_raw"])
                    book.data = data
                    row = s.get(LiveOrder, order_id)
                    if row.status != "validating":
                        raise ValueError("Estado da ordem mudou")
                    row.status, row.data = (
                        "pending",
                        d
                        | {
                            "signature": signature,
                            "message": "Envio iniciado; aguardando confirmação e reconciliação",
                        },
                    )
                try:
                    response_sig = await self.chain.send_once(signed)
                    if response_sig != signature:
                        raise ValueError("Assinatura RPC divergente")
                except Exception:
                    self.db.event(
                        "live_pending",
                        "Resultado do envio incerto; NÃO reenviar. Reconciliação pela assinatura persistida.",
                        "critical",
                        {"order_id": order_id},
                    )
                with self.db.session() as s:
                    return public_order(s.get(LiveOrder, order_id))
            except Exception as exc:
                self.fail(
                    order_id,
                    "Confirmação bloqueada: "
                    + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__),
                )
                raise ValueError(
                    "Não foi possível concluir a confirmação; confira o estado da ordem"
                ) from None

    async def cancel(self, order_id):
        async with self.lock:
            with self.db.session() as s:
                self.locked_book(s)
                row = s.get(LiveOrder, order_id)
                if not row:
                    raise ValueError("Ordem inexistente")
                if row.data.get("signature"):
                    raise ValueError("Transação já assinada: não pode cancelar nem liberar a posição")
                row.status, row.active_slot = "cancelled", None
                row.data = row.data | {"message": "Cancelada antes do envio"}
                return public_order(row)

    def halt(self):
        with self.db.session() as s:
            book = self.locked_book(s)
            book.data = book.data | {"halted": True}
        self.db.event("live_halt", "Novas entradas reais bloqueadas; nenhuma venda automática", "critical")

    def resume(self):
        with self.db.session() as s:
            book = self.locked_book(s)
            if book.data.get("warning") or s.scalar(select(LiveOrder).where(LiveOrder.active_slot == 1)):
                raise ValueError("Resolva a ordem pendente/divergência antes de reativar")
            book.data = book.data | {"halted": False}
        self.db.event("live_resume", "Entradas supervisionadas reativadas; limites preservados", "warning")

    async def poll(self):
        async with self.lock:
            with self.db.session() as s:
                row = s.scalar(select(LiveOrder).where(LiveOrder.active_slot == 1))
                book = s.get(LiveBook, 1)
            if row and row.data.get("signature"):
                await self.reconcile_order(row)
            elif row and row.status == "review" and now() >= row.data["preview"]["expires_at"]:
                with self.db.session() as s:
                    self.locked_book(s)
                    current = s.get(LiveOrder, row.id)
                    current.status, current.active_slot = "expired", None
                    current.data = current.data | {"message": "Revisão expirou sem envio"}
            # No automatic retry, even after process restart.
            if book and book.data.get("position"):
                await self.watch_position(book.data)

    async def reconcile_order(self, row):
        d, p = row.data, row.data["preview"]
        status = await self.chain.status(d["signature"])
        if not status or status.get("confirmationStatus") != "finalized":
            # Null status is not a failure proof; reservation intentionally retained.
            return
        tx = await self.chain.transaction(d["signature"])
        if not tx or not tx.get("meta"):
            return
        before = d["before"]
        after = await self.chain.balances(p["wallet"], p["input_mint"], p["output_mint"], tx["slot"])
        meta = tx["meta"]
        if meta.get("err"):
            matches = (
                before["input"] == after["input"]
                and before["output"] == after["output"]
                and before["sol"] - after["sol"] == meta["fee"]
            )
            state, result = "failed", {"fee_lamports": meta["fee"], "onchain_error": True}
        else:
            keys = tx["transaction"]["message"]["accountKeys"]
            keys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
            from .transaction_guard import associated

            deltas = []
            for mint in (p["input_mint"], p["output_mint"]):
                ata = associated(p["wallet"], mint)

                def amount(items):
                    entries = [
                        x
                        for x in items
                        if keys[x["accountIndex"]] == ata
                        and x["mint"] == mint
                        and x.get("owner") == p["wallet"]
                    ]
                    return int(entries[0]["uiTokenAmount"]["amount"]) if entries else 0

                deltas.append(amount(meta["postTokenBalances"]) - amount(meta["preTokenBalances"]))
            idx = keys.index(p["wallet"])
            native_delta = meta["postBalances"][idx] - meta["preBalances"][idx]
            matches = (
                deltas[0] == -int(p["input_raw"])
                and deltas[1] >= int(p["minimum_output_raw"])
                and after["input"] == before["input"] + deltas[0]
                and after["output"] == before["output"] + deltas[1]
                and after["sol"] == before["sol"] + native_delta
                and 0 < meta["fee"] <= p["fee_lamports"]
                and meta["fee"] <= -native_delta <= p["fee_lamports"] + p["rent_lamports"]
            )
            state, result = (
                "finalized",
                {
                    "input_raw": str(-deltas[0]),
                    "output_raw": str(deltas[1]),
                    "fee_lamports": meta["fee"],
                    "slot": tx["slot"],
                },
            )
        with self.db.session() as s:
            book = self.locked_book(s)
            current = s.get(LiveOrder, row.id)
            if current.active_slot != 1:
                return
            if not matches:
                book.data = book.data | {
                    "halted": True,
                    "warning": "Divergência on-chain: reserva mantida; revisão técnica necessária",
                }
                current.status = "reconciliation_required"
                current.data = current.data | {"message": "Saldos divergem; nenhuma nova ordem será liberada"}
                return
            data = dict(book.data)
            data["cash_raw"] = after["input"] if p["side"] == "buy" else after["output"]
            if state == "finalized" and p["side"] == "buy":
                data["position"] = {
                    "mint": p["mint"],
                    "symbol": p["symbol"],
                    "qty_raw": int(result["output_raw"]),
                    "cost_usdc_raw": int(p["input_raw"]),
                    "opened_at": now(),
                    "decimals": p["output_decimals"],
                    "entry_price_usdc": int(p["input_raw"])
                    / 1e6
                    / (int(result["output_raw"]) / 10 ** p["output_decimals"]),
                    "entry_market": d["market"],
                    "entry_order": row.id,
                }
            if state == "finalized" and p["side"] == "sell":
                result["pnl_usdc_before_sol_costs"] = (
                    int(result["output_raw"]) - data["position"]["cost_usdc_raw"]
                ) / 1e6
                data.pop("position", None)
            data.pop("warning", None)
            book.data = data
            current.status, current.active_slot = state, None
            current.data = current.data | {
                "result": result,
                "message": "Resultado finalizado e saldos reconciliados",
            }
        self.db.event(
            "live_" + state,
            "Ordem real " + state + "; saldos reconciliados",
            data={"order_id": row.id, "signature": d["signature"]},
        )

    async def watch_position(self, data):
        position = data["position"]
        try:
            if data.get("wallet"):
                balances = await self.chain.balances(data["wallet"], position["mint"], USDC)
                if balances["input"] != position["qty_raw"] or balances["output"] != data["cash_raw"]:
                    with self.db.session() as session:
                        book = self.locked_book(session)
                        book.data = book.data | {
                            "halted": True,
                            "warning": "Saldo da posição diverge: reconciliação técnica necessária",
                            "exit_signal": "BALANCE_MISMATCH",
                        }
                    self.db.event(
                        "live_balance_mismatch", "Saldo real diverge da posição registrada", "critical"
                    )
                    return
            m = await self.market(position["mint"])
            entry = position["entry_price_usdc"]
            high = max(position.get("high_price", entry), m.price or 0)
            pct = (m.price / entry - 1) * 100 if m.price and entry else None
            t = self.engine.thresholds
            reason = None
            if pct is None or m.age_seconds() > t.max_data_age_seconds:
                reason = "PRICE_UNAVAILABLE"
            elif pct <= -t.stop_loss_pct:
                reason = "STOP_LOSS"
            elif pct >= t.take_profit_pct:
                reason = "TAKE_PROFIT"
            elif m.price <= high * (1 - t.trailing_stop_pct / 100):
                reason = "TRAILING_STOP"
            elif now() - position["opened_at"] > t.max_hold_minutes * 60:
                reason = "MAX_HOLD_TIME"
            elif m.liquidity is None or m.liquidity < position["entry_market"]["liquidity"] * (
                1 - t.liquidity_drop_pct / 100
            ):
                reason = "LIQUIDITY_DROP"
        except Exception:
            high = position.get("high_price", position["entry_price_usdc"])
            reason = "PRICE_UNAVAILABLE"
        with self.db.session() as s:
            book = self.locked_book(s)
            if not book.data.get("position"):
                return
            updated = book.data | {
                "risk_checked_at": now(),
                "exit_signal": reason,
                "position": book.data["position"] | {"high_price": high},
            }
            previous = book.data.get("exit_signal")
            book.data = updated
        if reason and reason != previous:
            self.db.event(
                "live_exit_signal",
                "Saída real exige revisão humana: " + reason,
                "critical",
                {"mint": position["mint"]},
            )
