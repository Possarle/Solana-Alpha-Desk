"""Launch-mode shadow book: own account (id 2), own slot (2), fills on the pump.fun curve.

Entries and exits are priced with the bonding-curve constant product, including the
documented 1% fee and the price impact of the order size. After a token migrates off
the curve, the exit uses the first public pool price available; until then the exit is
pending. Nothing here signs or sends a transaction.
"""

from decimal import Decimal

from sqlalchemy import select

from ..agents.risk import DATA_REASONS, exit_reason
from ..db import Equity, Event, Order, Position
from ..execution.simulation import D, ShadowExecution
from ..schemas import Market, now
from ..thresholds import LaunchThresholds
from .pumpfun import Curve, Launch


def curve_market(launch_mint: str, curve_key: str, symbol: str, curve: Curve, sol_usd: float, created_at):
    """A Market snapshot read straight from the chain: no source cache delay."""
    price = curve.price_sol * sol_usd
    return Market(
        address=launch_mint,
        pool=curve_key,
        symbol=symbol,
        source="pumpfun",
        source_cache_seconds=0,
        price=price,
        market_cap=price * curve.supply / 10**6,
        fdv=price * curve.supply / 10**6,
        liquidity=curve.real_sol_ui * sol_usd,
        created_at=created_at,
        discovered_by="launch",
    )


class LaunchExecution(ShadowExecution):
    mode = "launch"
    account_id = 2
    slot = 2

    def open_curve(
        self,
        launch: Launch,
        curve_key: str,
        curve: Curve,
        sol_usd: float,
        t: LaunchThresholds,
        key: str,
        verdict: dict,
    ) -> dict:
        with self.db.session() as s:
            account = self._lock(s)
            existing = s.get(Order, key)
            if existing:
                return existing.data | {"duplicate": True}
            equity = self.equity(s, account)
            self._roll_day(account, equity)
            budget = min(equity * D(t.max_position_pct) / 100, account.cash)
            fee = budget * D(t.fee_bps) / 10000 + D(t.fixed_cost_usd)
            spend_sol = float(budget - fee) / sol_usd if sol_usd > 0 else 0
            tokens = curve.buy(spend_sol) if spend_sol > 0 else 0
            spot = curve.price_sol * sol_usd
            reason = None
            if account.halted:
                reason = "Modo lançamento em parada"
            elif equity <= account.day_start * (1 - D(t.max_daily_loss_pct) / 100):
                reason = "Limite diário de perda"
                account.halted = True
            elif s.scalar(select(Position).where(Position.active_slot == self.slot)):
                reason = "Já existe posição de lançamento"
            elif curve.complete:
                reason = "Token já saiu da curva"
            elif tokens <= 0 or budget <= fee * 2:
                reason = "Capital insuficiente após custos"
            if reason is None:
                fill = float(budget - fee) / tokens
                impact = (fill / spot - 1) * 100
                if impact > t.max_price_impact_pct:
                    reason = f"Impacto no preço {impact:.1f}% acima do limite"
            if reason:
                result = {"status": "rejected", "reason": reason, "mode": self.mode}
                s.add(Order(key=key, data=result))
                return result
            qty, entry = D(tokens), D(fill)
            p = Position(
                token_key="solana:" + launch.mint,
                active_slot=self.slot,
                status="open",
                mode=self.mode,
                qty=qty,
                cost=budget,
                entry=entry,
                mark=D(spot),
                peak=D(spot),
                opened_at=now(),
                data={
                    "symbol": launch.symbol,
                    "pool": curve_key,
                    "source": "pumpfun",
                    "strategy": "launch",
                    "created_at": launch.seen_at,
                    "entry_liquidity": curve.real_sol_ui * sol_usd,
                    "entry_fee": float(fee),
                    "entry_slippage_cost": float(budget - fee) - tokens * spot,
                    "price_impact_pct": impact,
                    "decision_price": spot,
                    "entry_age_s": round(now() - launch.seen_at, 1),
                    "sol_usd": sol_usd,
                    "last_mark_at": now(),
                    "verdict": verdict,
                },
            )
            account.cash -= budget
            s.add(p)
            s.flush()
            result = {
                "status": "simulated",
                "side": "buy",
                "position_id": p.id,
                "budget": float(budget),
                "qty": tokens,
                "fill": fill,
                "fee": float(fee),
                "mode": self.mode,
                "token_key": p.token_key,
            }
            s.add(Order(key=key, data=result))
            s.add(
                Event(
                    kind="launch_open", message=f"Lançamento: entrada shadow em {launch.symbol}", data=result
                )
            )
            return result

    def manage_curve(
        self,
        curve: Curve | None,
        sol_usd: float | None,
        t: LaunchThresholds,
        forced_reason: str | None = None,
        migrated_market: Market | None = None,
    ):
        """curve=None means the read failed. migrated_market is a public pool quote after migration."""
        with self.db.session() as s:
            account = self._lock(s)
            p = s.scalar(select(Position).where(Position.active_slot == self.slot))
            if not p:
                return None
            on_curve = curve is not None and not curve.complete and bool(sol_usd)
            if on_curve:
                m = curve_market(
                    p.token_key.removeprefix("solana:"),
                    p.data["pool"],
                    p.data.get("symbol", "?"),
                    curve,
                    sol_usd,
                    p.data.get("created_at"),
                )
            elif migrated_market is not None:
                m = migrated_market
            else:
                m = None
            fresh = bool(m and m.price and m.age_seconds() <= t.max_data_age_seconds)
            if fresh:
                p.mark = D(m.price)
                p.peak = max(p.peak, p.mark)
                p.data = p.data | {"last_mark_at": m.observed_at}
            equity = self.equity(s, account)
            self._roll_day(account, equity)
            breach = equity <= account.day_start * (1 - D(t.max_daily_loss_pct) / 100)
            if breach:
                account.halted = True
            pending = p.data.get("pending_reason")
            if pending in DATA_REASONS and fresh:
                p.status = "open"
                p.data = {k: v for k, v in p.data.items() if k != "pending_reason"}
                pending = None
            migrated = curve is not None and curve.complete
            reason = forced_reason or pending or ("MIGRATED" if migrated else None)
            if reason is None:
                reason = exit_reason(p, m, t, account.halted, breach) if m else "STALE_PRICE"
            if not reason:
                s.add(Equity(value=equity, data={"mode": self.mode}))
                return None
            qty = float(p.qty)
            if on_curve and fresh:
                gross = D(curve.sell(qty) * sol_usd)
            elif fresh and m.liquidity and m.liquidity >= 100:
                slip = max(Decimal(20), D(qty * float(p.mark)) / (D(m.liquidity) / 2) * 10000)
                if slip > 500:
                    gross = None
                else:
                    gross = D(qty) * p.mark * (1 - slip / 10000)
            else:
                gross = None
            if gross is None:
                changed = p.status != "exit_pending"
                p.status = "exit_pending"
                p.data = p.data | {"pending_reason": reason}
                if changed:
                    s.add(
                        Event(
                            kind="exit_pending",
                            level="warning",
                            message="Lançamento: saída pendente sem preço executável",
                            data={"position_id": p.id, "reason": reason},
                        )
                    )
                return {"status": "exit_pending", "reason": reason}
            fee = gross * D(t.fee_bps) / 10000 + D(t.fixed_cost_usd)
            proceeds = max(D(0), gross - fee)
            account.cash += proceeds
            p.pnl, p.status, p.active_slot, p.closed_at = proceeds - p.cost, "closed", None, now()
            p.data = p.data | {
                "exit_reason": reason,
                "exit_fee": float(fee),
                "exit_mark": float(p.mark),
                "exit_price": float(gross) / qty if qty else 0.0,
                "exit_slippage_cost": max(0.0, qty * float(p.mark) - float(gross)),
                "exit_venue": "curve" if on_curve else "pool",
            }
            result = {
                "status": "simulated",
                "side": "sell",
                "position_id": p.id,
                "reason": reason,
                "pnl": float(p.pnl),
                "fee": float(fee),
                "mode": self.mode,
            }
            s.add(Order(key=f"launch-close:{p.id}", data=result))
            s.add(
                Event(
                    kind="launch_close", level="warning", message=f"Lançamento: saída {reason}", data=result
                )
            )
            s.add(Equity(value=account.cash, data={"mode": self.mode}))
            if account.cash <= account.day_start * (1 - D(t.max_daily_loss_pct) / 100):
                account.halted = True
            return result
