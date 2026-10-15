"""Latency audit: costs computed only from collected prices of the same pool."""

from sqlalchemy import select

from app.db import Observation, Position
from app.execution.simulation import ShadowExecution
from app.schemas import now
from app.timing import timing_report


def observe(db, market, at, price, pool=None):
    data = market.model_copy(update={"price": price, "observed_at": at}).model_dump()
    if pool:
        data["pool"] = pool
    with db.session() as s:
        s.add(Observation(token_key="solana:" + market.address, at=at, data=data))


def test_entry_drift_and_stop_overshoot_are_measured(db, market, judgment, limits):
    e = ShadowExecution(db)
    e.open(market, judgment, limits, "x")
    with db.session() as s:
        p = s.scalar(select(Position))
        p.opened_at -= 600  # the buy happened 10 minutes ago
        opened, qty = p.opened_at, float(p.qty)
    observe(db, market, opened + 20, 1.03)  # price already 3% higher 20 s after the buy
    observe(db, market, opened + 25, 9.99, pool="other-pool-ignored")
    observe(db, market, opened + 300, 0.87)  # first price beyond the 12% stop level
    market.price, market.observed_at = 0.80, now()  # sale happens later, deeper
    result = e.mark_and_manage(market, limits)
    assert result["reason"] == "STOP_LOSS"
    r = timing_report(db)
    t = r["trades_detail"][0]
    assert r["trades"] == 1 and t["samples"] == 2
    assert round(t["entry_drift_pct"], 6) == 3.0
    assert round(r["entry_latency_cost"], 6) == round(qty * 0.03, 6)
    with db.session() as s:
        entry = float(s.scalar(select(Position)).entry)
    level = entry * 0.88
    assert round(r["exit_gap_cost"], 6) == round(qty * (level - 0.80), 6) and r["exit_gap_cost"] > 0
    assert t["exit_lag_s"] is not None and t["exit_lag_s"] > 0
    assert r["by_reason"][0]["reason"] == "STOP_LOSS"
    assert "atraso custou" in r["verdict"]


def test_empty_history_is_explicit(db):
    r = timing_report(db)
    assert r["trades"] == 0 and r["entry_latency_cost"] == 0 and "Ainda não há" in r["verdict"]
