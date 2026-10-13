from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text

from ..agents.position_size import position_size
from ..agents.risk import exit_reason
from ..db import Account, Equity, Event, Order, Position
from ..schemas import Judgment, Market, now
from ..thresholds import Thresholds


def D(value) -> Decimal:
    return Decimal(str(value))


def utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class SimulationExecution:
    mode = "shadow"

    def __init__(self, db):
        self.db = db

    def _lock(self, s):
        if self.db.engine.dialect.name == "sqlite":
            s.execute(text("BEGIN IMMEDIATE"))
        return s.scalar(select(Account).where(Account.id == 1).with_for_update())

    def initialize(self):
        with self.db.session() as s:
            account = self._lock(s)
            if account is None:
                s.add(
                    Account(
                        id=1, initial=D(10000), cash=D(10000), day=utc_day(), day_start=D(10000), halted=False
                    )
                )
                s.add(Equity(value=D(10000), data={"mode": "shadow"}))

    def equity(self, s, account) -> Decimal:
        positions = s.scalars(select(Position).where(Position.active_slot == 1)).all()
        return account.cash + sum((p.qty * p.mark for p in positions), D(0))

    def daily_breach(self, account, equity, t) -> bool:
        return equity <= account.day_start * (1 - D(t.max_daily_loss_pct) / 100)

    def _roll_day(self, account, equity):
        if account.day != utc_day():
            account.day, account.day_start = utc_day(), equity
            # Daily lock requires explicit review/resume. It is never silently reset.

    def open(self, m: Market, j: Judgment, t: Thresholds, key: str) -> dict:
        with self.db.session() as s:
            account = self._lock(s)
            existing = s.get(Order, key)
            if existing:
                return existing.data | {"duplicate": True}
            equity = self.equity(s, account)
            self._roll_day(account, equity)
            reason = None
            if account.halted:
                reason = "Bot em parada de emergência"
            elif not j.approved:
                reason = "Avaliação rejeitada"
            elif m.age_seconds() > t.max_data_age_seconds or not m.price:
                reason = "Preço ausente ou antigo"
            elif not m.liquidity or m.liquidity < t.min_liquidity:
                reason = "Liquidez insuficiente"
            elif self.daily_breach(account, equity, t):
                reason = "Limite diário de perda"
                account.halted = True
            elif s.scalar(select(Position).where(Position.active_slot == 1)):
                reason = "Já existe posição ou encerramento pendente"
            size = position_size(float(equity), float(account.cash), m, j, t)
            if reason is None and size["slippage_bps"] > t.max_slippage_bps:
                reason = "Slippage excessivo"
            if reason is None and size["budget"] <= t.fixed_cost_usd * 2:
                reason = "Capital insuficiente após custos"
            if reason:
                result = {"status": "rejected", "reason": reason, "mode": self.mode}
                s.add(Order(key=key, data=result))
                return result
            budget, slip = D(size["budget"]), D(size["slippage_bps"]) / 10000
            fee = budget * D(t.fee_bps) / 10000 + D(t.fixed_cost_usd)
            fill = D(m.price) * (1 + slip)
            qty = (budget - fee) / fill
            p = Position(
                token_key="solana:" + m.address,
                active_slot=1,
                status="open",
                mode=self.mode,
                qty=qty,
                cost=budget,
                entry=fill,
                mark=D(m.price),
                peak=D(m.price),
                opened_at=now(),
                data={
                    "symbol": m.symbol,
                    "pool": m.pool,
                    "source": m.source,
                    "strategy": j.strategy,
                    "entry_liquidity": m.liquidity,
                    "entry_fee": float(fee),
                    "slippage_bps": float(slip * 10000),
                    "entry_slippage_cost": float(qty * (fill - D(m.price))),
                    "last_mark_at": m.observed_at,
                    "judgment": j.model_dump(),
                    "size": size,
                },
            )
            account.cash -= budget
            s.add(p)
            s.flush()
            result = {
                "status": "simulated",
                "position_id": p.id,
                "budget": float(budget),
                "qty": float(qty),
                "fill": float(fill),
                "fee": float(fee),
                "mode": self.mode,
                "token_key": p.token_key,
                "side": "buy",
            }
            s.add(Order(key=key, data=result))
            s.add(
                Event(
                    kind="position_open",
                    level="info",
                    message=f"Entrada {self.mode} estimada: {m.symbol}",
                    data=result,
                )
            )
            return result

    def mark_and_manage(self, m: Market, t: Thresholds, forced_reason: str | None = None):
        with self.db.session() as s:
            account = self._lock(s)
            p = s.scalar(select(Position).where(Position.active_slot == 1))
            if not p or p.token_key != "solana:" + m.address:
                return None
            fresh = bool(m.price and m.age_seconds() <= t.max_data_age_seconds)
            if fresh:
                p.mark = D(m.price)
                p.peak = max(p.peak, p.mark)
                p.data = p.data | {"last_mark_at": m.observed_at}
            equity = self.equity(s, account)
            self._roll_day(account, equity)
            breach = self.daily_breach(account, equity, t)
            if breach:
                account.halted = True
            reason = (
                forced_reason or p.data.get("pending_reason") or exit_reason(p, m, t, account.halted, breach)
            )
            if not reason:
                s.add(Equity(value=equity, data={"mode": p.mode, "mark_fresh": fresh}))
                return None
            # A triggered exit never frees the slot until a valid simulated fill can be recorded.
            if (
                not fresh
                or not m.liquidity
                or m.liquidity < 100
                or (p.qty * p.mark) > D(m.liquidity) * D(".001")
            ):
                changed = p.status != "exit_pending"
                p.status = "exit_pending"
                p.data = p.data | {"pending_reason": reason}
                if changed:
                    s.add(
                        Event(
                            kind="exit_pending",
                            level="critical",
                            message="Saída pendente: preço/liquidez não executável",
                            data={"position_id": p.id, "reason": reason},
                        )
                    )
                return {"status": "exit_pending", "reason": reason}
            slip = max(D(20), p.qty * p.mark / (D(m.liquidity) / 2) * 10000)
            if slip > t.max_slippage_bps:
                p.status = "exit_pending"
                p.data = p.data | {"pending_reason": reason}
                return {"status": "exit_pending", "reason": "Slippage excessivo"}
            fill = p.mark * (1 - slip / 10000)
            gross = p.qty * fill
            fee = gross * D(t.fee_bps) / 10000 + D(t.fixed_cost_usd)
            proceeds = max(D(0), gross - fee)
            account.cash += proceeds
            p.pnl, p.status, p.active_slot, p.closed_at = proceeds - p.cost, "closed", None, now()
            p.data = p.data | {
                "exit_reason": reason,
                "exit_fee": float(fee),
                "exit_price": float(fill),
                "exit_slippage_cost": float(p.qty * (p.mark - fill)),
            }
            result = {
                "status": "simulated",
                "side": "sell",
                "position_id": p.id,
                "reason": reason,
                "pnl": float(p.pnl),
                "fee": float(fee),
                "mode": p.mode,
            }
            s.add(Order(key=f"close:{p.id}", data=result))
            s.add(
                Event(
                    kind="position_close", level="warning", message=f"Saída estimada: {reason}", data=result
                )
            )
            s.add(Equity(value=account.cash, data={"mode": p.mode}))
            # Closing fees can cross the daily limit; latch before a subsequent entry.
            if self.daily_breach(account, account.cash, t):
                account.halted = True
            return result

    def reconcile(self) -> dict:
        with self.db.session() as s:
            account = self._lock(s)
            positions = s.scalars(select(Position)).all()
            expected = account.initial + sum((p.pnl or D(0) for p in positions if p.status == "closed"), D(0))
            expected -= sum((p.cost for p in positions if p.active_slot == 1), D(0))
            ok = abs(expected - account.cash) < D(".000001")
            if not ok:
                account.halted = True
                s.add(
                    Event(
                        kind="reconciliation",
                        level="critical",
                        message="Divergência de caixa; novas entradas bloqueadas",
                        data={},
                    )
                )
            return {"matches": ok, "expected_cash": float(expected), "recorded_cash": float(account.cash)}


class ShadowExecution(SimulationExecution):
    mode = "shadow"


class PaperExecution(SimulationExecution):
    mode = "paper"
