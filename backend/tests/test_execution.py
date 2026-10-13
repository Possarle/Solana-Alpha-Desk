import concurrent.futures
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import Account, Order, Position
from app.execution.live import LiveExecution
from app.execution.simulation import ShadowExecution
from app.schemas import now


def test_atomic_duplicate_order(db, market, judgment, limits):
    e = ShadowExecution(db)
    first = e.open(market, judgment, limits, "same-key")
    second = e.open(market, judgment, limits, "same-key")
    assert first["status"] == "simulated" and second["duplicate"]
    with db.session() as s:
        assert len(s.scalars(select(Position)).all()) == 1
        assert len(s.scalars(select(Order)).all()) == 1
        assert float(s.get(Account, 1).cash) == 10000 - first["budget"]


def test_concurrent_orders_respect_single_position(db, market, judgment, limits):
    e = ShadowExecution(db)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda k: e.open(market, judgment, limits, k), ["a", "b"]))
    assert sum(r["status"] == "simulated" for r in results) == 1
    assert e.reconcile()["matches"]


def test_stale_execution_and_critical_rejected(db, market, judgment, limits):
    e = ShadowExecution(db)
    market.observed_at = now() - 500
    assert e.open(market, judgment, limits, "stale")["status"] == "rejected"
    market.observed_at = now()
    judgment.approved = False
    assert e.open(market, judgment, limits, "unsafe")["status"] == "rejected"


def test_stop_loss_closes_once_and_costs_count(db, market, judgment, limits):
    e = ShadowExecution(db)
    opened = e.open(market, judgment, limits, "entry")
    market.price = 0.8
    result = e.mark_and_manage(market, limits)
    assert result["reason"] == "STOP_LOSS"
    assert result["pnl"] < -0.2 * opened["budget"]
    assert e.mark_and_manage(market, limits) is None
    assert e.reconcile()["matches"]
    with db.session() as s:
        assert s.get(Position, opened["position_id"]).active_slot is None
        assert len(s.scalars(select(Order)).all()) == 2


def test_missing_price_keeps_position_reserved(db, market, judgment, limits):
    e = ShadowExecution(db)
    opened = e.open(market, judgment, limits, "entry")
    market.observed_at = now() - 500
    result = e.mark_and_manage(market, limits)
    assert result["status"] == "exit_pending"
    with db.session() as s:
        assert s.get(Position, opened["position_id"]).active_slot == 1
    market.observed_at, market.price = now(), 0.99
    assert e.open(market, judgment, limits, "another")["status"] == "rejected"
    assert e.mark_and_manage(market, limits)["reason"] == "STALE_PRICE"
    assert e.reconcile()["matches"]


def test_liquidity_failure_does_not_free_slot(db, market, judgment, limits):
    e = ShadowExecution(db)
    e.open(market, judgment, limits, "entry")
    market.liquidity = 0
    assert e.mark_and_manage(market, limits)["status"] == "exit_pending"
    with db.session() as s:
        assert s.scalar(select(Position)).active_slot == 1


def test_kill_switch_blocks_entry(db, market, judgment, limits):
    with db.session() as s:
        s.get(Account, 1).halted = True
    assert ShadowExecution(db).open(market, judgment, limits, "x")["status"] == "rejected"


def test_daily_limit_includes_open_marks_and_latches(db, market, judgment, limits):
    e = ShadowExecution(db)
    limits.max_daily_loss_pct = 0.1
    e.open(market, judgment, limits, "x")
    market.price = 0.5
    result = e.mark_and_manage(market, limits)
    assert result["reason"] == "DAILY_LOSS_LIMIT"
    with db.session() as s:
        assert s.get(Account, 1).halted


def test_reconciliation_detects_tampering(db):
    with db.session() as s:
        s.get(Account, 1).cash -= Decimal("1")
    assert not ShadowExecution(db).reconcile()["matches"]
    with db.session() as s:
        assert s.get(Account, 1).halted


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("take", "TAKE_PROFIT"),
        ("trail", "TRAILING_STOP"),
        ("time", "MAX_HOLD_TIME"),
        ("liquidity", "LIQUIDITY_DROP"),
        ("emergency", "EMERGENCY_STOP"),
    ],
)
def test_exit_triggers(db, market, judgment, limits, scenario, expected):
    e = ShadowExecution(db)
    e.open(market, judgment, limits, "x")
    if scenario == "take":
        market.price = 1.5
    if scenario == "trail":
        with db.session() as s:
            s.scalar(select(Position)).peak = Decimal("1.2")
    if scenario == "time":
        with db.session() as s:
            s.scalar(select(Position)).opened_at = now() - 20000
    if scenario == "liquidity":
        market.liquidity = 60000
    if scenario == "emergency":
        with db.session() as s:
            s.get(Account, 1).halted = True
    assert e.mark_and_manage(market, limits)["reason"] == expected
    assert e.reconcile()["matches"]


async def test_live_never_broadcasts_even_with_flag():
    with pytest.raises(PermissionError):
        await LiveExecution(None, None, True).submit("anything")


async def test_live_confirmation_requires_finalized_and_no_error():
    class RPC:
        value = None

        async def call(self, *a, **kw):
            return {"value": [self.value]}

    rpc = RPC()
    live = LiveExecution(rpc, None)
    assert not await live.confirmed("s")
    rpc.value = {"confirmationStatus": "confirmed", "err": None}
    assert not await live.confirmed("s")
    rpc.value = {"confirmationStatus": "finalized", "err": None}
    assert await live.confirmed("s")
    rpc.value = {"confirmationStatus": "finalized", "err": {"err": "failure"}}
    assert not await live.confirmed("s")


def test_slippage_cap_blocks_entry(db, market, judgment, limits):
    limits.max_slippage_bps = 10
    result = ShadowExecution(db).open(market, judgment, limits, "slip")
    assert result["status"] == "rejected" and result["reason"] == "Slippage excessivo"
    with db.session() as session:
        assert session.scalar(select(Position)) is None


def test_illiquid_entry_blocked_even_with_previous_approval(db, market, judgment, limits):
    market.liquidity = 100
    result = ShadowExecution(db).open(market, judgment, limits, "illiquid")
    assert result["status"] == "rejected" and result["reason"] == "Liquidez insuficiente"
