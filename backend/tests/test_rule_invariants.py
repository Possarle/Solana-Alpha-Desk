"""Randomized rule audit: hundreds of price paths, both books, invariants checked on every exit."""

import random
from dataclasses import replace

from sqlalchemy import select

from app.db import Account, Position
from app.execution.simulation import ShadowExecution
from app.launch.desk import LaunchDesk
from app.launch.pumpfun import curve_address
from app.thresholds import LaunchThresholds
from tests.test_launch import FRESH, FakeDex, FakeRPC, launch_for, with_inflow

EPS = 1e-9


def check_exit(p: Position, t, reason: str):
    entry, mark, peak = float(p.entry), float(p.data["exit_mark"]), float(p.peak)
    if reason == "STOP_LOSS":
        assert mark <= entry * (1 - t.stop_loss_pct / 100) + EPS
    if reason == "TAKE_PROFIT":
        assert mark >= entry * (1 + t.take_profit_pct / 100) - EPS
    if reason == "TRAILING_STOP":
        assert peak >= entry * (1 + t.trailing_activation_pct / 100) - EPS  # armed only after the gain
        assert mark <= peak * (1 - t.trailing_stop_pct / 100) + EPS


def test_main_book_random_paths(db, market, judgment, limits):
    rng = random.Random(7)
    e = ShadowExecution(db)
    exits = 0
    for trade in range(150):
        market.price, market.liquidity = 1.0, 1_000_000
        opened = e.open(market, judgment, limits, f"t{trade}")
        if opened["status"] != "simulated":
            assert opened["reason"] == "Limite diário de perda"
            break
        with db.session() as s:
            equity = float(s.get(Account, 1).cash) + opened["budget"]
        assert opened["budget"] <= equity * limits.max_position_pct / 100 + EPS
        for _ in range(400):
            market.price = max(1e-6, market.price * (1 + rng.gauss(0, 0.04)))
            result = e.mark_and_manage(market, limits)
            if result:
                assert result["status"] == "simulated"
                with db.session() as s:
                    p = s.get(Position, result["position_id"])
                    check_exit(p, limits, result["reason"])
                exits += 1
                break
        assert e.reconcile()["matches"]
        if e.mark_and_manage(market, limits) is None:
            with db.session() as s:
                p = s.scalar(select(Position).where(Position.active_slot == 1))
                if p:  # still open after 400 steps: force out, still must reconcile
                    s.get(Position, p.id).opened_at -= 10**6
            e.mark_and_manage(market, limits)
        assert e.reconcile()["matches"]
    assert exits > 50


async def test_launch_book_random_paths(db):
    from app.config import Settings
    from app.main import create_app

    engine = create_app(Settings(enable_scheduler=False), db).state.engine
    engine.launch.execution.initialize()
    desk = LaunchDesk(engine)
    desk.rpc, desk.dex = FakeRPC(), FakeDex()
    t = LaunchThresholds()
    rng = random.Random(11)
    exits = 0
    for _ in range(80):
        curve = with_inflow(FRESH, 0.9 + rng.uniform(1.0, 6.0))
        launch = launch_for(desk, curve)
        verdict = await desk.check(launch, t)
        if not verdict.get("execution") or verdict["execution"]["status"] != "simulated":
            reasons = verdict.get("reasons") or [verdict.get("execution", {}).get("reason", "")]
            assert any("Limite diário" in r or "parada" in r or "subiu demais" in r for r in reasons), reasons
            continue
        assert verdict["execution"]["budget"] <= 10_000 * 1.5 * t.max_position_pct / 100
        key = curve_address(launch.mint)
        for _ in range(300):
            c = desk.rpc.curves[key]
            flow = rng.gauss(0.1, 1.5)
            if flow < 0 and c.real_sol + int(flow * 1e9) <= 0:
                flow = -c.real_sol_ui * 0.9
            if flow >= 0:
                c = with_inflow(c, flow)
            else:
                drop = int(-flow * 1e9)
                c = replace(c, virtual_sol=c.virtual_sol - drop, real_sol=c.real_sol - drop)
            desk.rpc.curves[key] = c
            result = await desk.risk_tick()
            if result and result["status"] == "simulated":
                with db.session() as s:
                    p = s.get(Position, result["position_id"])
                    if result["reason"] != "LIQUIDITY_DROP":
                        check_exit(p, t, result["reason"])
                exits += 1
                break
        assert desk.execution.reconcile()["matches"]
        assert engine.execution.reconcile()["matches"]  # main book never touched
        with db.session() as s:
            if s.scalar(select(Position).where(Position.active_slot == 2)):
                await desk.risk_tick("MANUAL_EXIT")
    assert exits > 20
