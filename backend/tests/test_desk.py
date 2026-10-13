"""Offline synthetic scenarios; never provider requests or real trading."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.db import Decision, Evaluation, Position
from app.main import create_app
from app.schemas import SecurityResult, now
from app.telemetry import DeskTelemetry


def engine_fixture(db, market, security):
    e = create_app(Settings(enable_scheduler=False), db).state.engine
    e.discovery.discover = AsyncMock(return_value=([market], []))
    e.security.analyze = AsyncMock(return_value=security)
    e.gecko.refresh = AsyncMock(return_value=market)
    return e


async def test_plan_entry_trace_exit_while_discovery_paused(db, market, security):
    e = engine_fixture(db, market, security)
    try:
        result = await e.scan()
        assert result["execution"]["status"] == "simulated"
        assert result["plan"]["budget"] == result["execution"]["budget"]
        assert result["plan"]["uncertainty_factor"] == 0.5
        assert (
            result["plan"]["stop_trigger"]
            < result["plan"]["estimated_entry"]
            < result["plan"]["take_trigger"]
        )
        assert result["cycle"]["evaluated"] == 1
        assert e.desk.agents["fills"].state == "completed"
        e.gecko.refresh.assert_awaited_once_with(market.pool)
        with db.session() as s:
            decision = s.scalar(select(Decision))
            assert decision.data["entry_evaluation_id"] == result["plan"]["evaluation_id"]
            evaluation = s.get(Evaluation, result["plan"]["evaluation_id"])
            assert evaluation.data["checks"]
            assert evaluation.data["observed"]["thresholds"]["require_resolved_concentration"]
        db.set_config("controls", {"discovery_paused": True})
        market.price = 0.7
        market.observed_at = now()
        result = await e.risk_tick()
        assert result["reason"] == "STOP_LOSS"
        assert e.desk.agents["fills"].data["result"]["side"] == "sell"
        assert e.execution.reconcile()["matches"]
        with db.session() as s:
            assert s.scalar(select(Position)).active_slot is None
    finally:
        await e.stop()


@pytest.mark.parametrize("scenario", ["timeout", "stale", "liquidity", "wrong_mint", "new_authority"])
async def test_revalidation_blocks_changed_or_unavailable_evidence(db, market, security, scenario):
    e = engine_fixture(db, market, security)
    refreshed = market.model_copy(deep=True)
    if scenario == "timeout":
        e.gecko.refresh = AsyncMock(side_effect=TimeoutError())
    else:
        if scenario == "stale":
            refreshed.observed_at = now() - 1000
        if scenario == "liquidity":
            refreshed.liquidity = 10
        if scenario == "wrong_mint":
            refreshed.address = market.pool
        if scenario == "new_authority":
            e.security.analyze = AsyncMock(
                side_effect=[
                    security,
                    SecurityResult(authority_checked=True, critical=["Mint authority ativa"]),
                ]
            )
        e.gecko.refresh = AsyncMock(return_value=refreshed)
    try:
        result = await e.scan()
        assert result["execution"]["status"] == "rejected"
        assert not result["worth_trading"]
        assert result["selected"] is None
        with db.session() as s:
            assert s.scalar(select(Position)) is None
    finally:
        await e.stop()


async def test_unknown_concentration_visible_and_blocked(db, market):
    e = engine_fixture(
        db, market, SecurityResult(authority_checked=True, unknown=["Concentração desconhecida"])
    )
    try:
        result = await e.scan()
        assert not result["worth_trading"]
        e.gecko.refresh.assert_not_awaited()
        assert e.desk.agents["vet"].state == "unknown"
        assert e.desk.agents["fills"].state == "skipped"
        with db.session() as s:
            checks = s.scalar(select(Evaluation)).data["checks"]
            assert (
                next(x for x in checks if x["label"] == "Concentração top 10 holders")["state"] == "unknown"
            )
    finally:
        await e.stop()


async def test_disabled_automation_records_plan_without_order(db, market, security):
    e = engine_fixture(db, market, security)
    db.set_config("thresholds", e.thresholds.model_dump() | {"auto_shadow": False})
    try:
        result = await e.scan()
        assert result["worth_trading"] and result["plan"]["budget"] > 0
        assert result["execution"]["status"] == "skipped"
        with db.session() as s:
            assert s.scalar(select(Position)) is None
    finally:
        await e.stop()


def test_telemetry_bounded_and_restart_never_claims_running_work():
    desk = DeskTelemetry()
    for i in range(100):
        desk.emit("scan", "completed", str(i))
    assert len(desk.snapshot()["events"]) == 80
    assert len({x["id"] for x in desk.events}) == 80
    restarted = DeskTelemetry()
    assert all(a.state == "waiting" and a.at is None for a in restarted.agents.values())
    assert restarted.session != desk.session


def test_desk_authenticated_http_and_websocket(db):
    app = create_app(Settings(enable_scheduler=False, dashboard_token="fixture-only"), db)
    with TestClient(app) as client:
        assert client.get("/api/state").status_code == 401
        client.headers["Authorization"] = "Bearer fixture-only"
        data = client.get("/api/state").json()
        assert len(data["desk"]["agents"]) == 10
        assert data["desk"]["cycle"] is None
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"token": "fixture-only"})
            assert len(ws.receive_json()["desk"]["agents"]) == 10
