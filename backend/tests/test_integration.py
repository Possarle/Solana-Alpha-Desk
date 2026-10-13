from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.db import Evaluation, Position, Token
from app.main import create_app
from app.providers.solana import SOL
from app.schemas import now


def test_http_settings_wallets_evidence_auth_and_websocket(db):
    app = create_app(Settings(enable_scheduler=False, dashboard_token="test-only"), db)
    with TestClient(app) as client:
        assert client.get("/api/state").status_code == 401
        client.headers["Authorization"] = "Bearer test-only"
        state = client.get("/api/state").json()
        assert state["mode"] == "shadow" and not state["live_available"]
        assert state["tokens"] == []
        settings = state["thresholds"]
        settings["min_score"] = 72
        assert client.put("/api/thresholds", json=settings).status_code == 200
        assert client.get("/api/state").json()["thresholds"]["min_score"] == 72
        assert client.put("/api/thresholds", json=settings | {"max_position_pct": 99}).status_code == 422
        assert client.post("/api/wallets", json={"address": "bad"}).status_code == 422
        assert client.post("/api/wallets", json={"address": SOL, "label": "Test"}).status_code == 200
        assert len(client.get("/api/wallets").json()["wallets"]) == 1
        assert client.delete("/api/wallets/" + SOL).status_code == 200
        assert (
            client.post(
                "/api/evidence",
                json={
                    "address": SOL,
                    "account": "test",
                    "url": "https://example.org",
                    "note": "fixture public evidence",
                },
            ).status_code
            == 200
        )
        assert client.post("/api/control", json={"action": "pause"}).status_code == 200
        assert client.get("/api/state").json()["controls"]["discovery_paused"]
        assert (
            client.post(
                "/api/control", json={"action": "resume"}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"token": "test-only"})
            assert ws.receive_json()["mode"] == "shadow"
        assert client.post("/api/reconcile").json()["matches"]


async def test_end_to_end_fixture_discovery_security_open_close(db, market, security):
    app = create_app(Settings(enable_scheduler=False), db)
    e = app.state.engine
    e.discovery.discover = AsyncMock(return_value=([market], []))
    e.security.analyze = AsyncMock(return_value=security)
    result = await e.scan()
    assert result["worth_trading"]
    assert result["execution"]["status"] == "simulated"
    with db.session() as s:
        assert s.scalar(select(Token)).data["symbol"] == "TEST FIXTURE"
        assert s.scalar(select(Evaluation)).data["approved"]
        assert s.scalar(select(Position)).status == "open"
    e.db.set_config("controls", {"discovery_paused": True})
    market.price = 0.7
    market.observed_at = now()
    e.gecko.refresh = AsyncMock(return_value=market)
    result = await e.risk_tick()
    assert result["reason"] == "STOP_LOSS"
    with db.session() as s:
        assert s.scalar(select(Position)).status == "closed"
    assert e.execution.reconcile()["matches"]
    assert (await e.scan())["status"] == "paused"
    await e.stop()


async def test_failures_do_not_open_positions(db):
    app = create_app(Settings(enable_scheduler=False), db)
    e = app.state.engine
    e.discovery.discover = AsyncMock(return_value=([], ["sources unavailable"]))
    result = await e.scan()
    assert not result["worth_trading"]
    with db.session() as s:
        assert s.scalar(select(Position)) is None
    await e.stop()


async def test_risk_rpc_failure_reserves_then_retries(db, market, judgment):
    app = create_app(Settings(enable_scheduler=False), db)
    e = app.state.engine
    e.execution.open(market, judgment, e.thresholds, "entry")
    e.gecko.refresh = AsyncMock(side_effect=TimeoutError())
    assert (await e.risk_tick())["status"] == "exit_pending"
    with db.session() as s:
        assert s.scalar(select(Position)).active_slot == 1
    e.gecko.refresh = AsyncMock(return_value=market)
    result = await e.risk_tick()
    assert result["status"] == "simulated"
    assert e.execution.reconcile()["matches"]
    await e.stop()


def test_manual_open_rechecks_updated_limits(db, market, judgment, security):
    app = create_app(Settings(enable_scheduler=False), db)
    with TestClient(app) as client:
        e = app.state.engine
        e.record_market(market)
        with db.session() as s:
            s.add(
                Evaluation(
                    token_key="solana:" + market.address,
                    data=judgment.model_dump(),
                    security=security.model_dump(),
                )
            )
        thresholds = client.get("/api/state").json()["thresholds"]
        thresholds["min_liquidity"] = 999999
        client.put("/api/thresholds", json=thresholds)
        result = client.post("/api/shadow/open", json={"address": market.address}).json()
        assert result["status"] == "rejected"
        with db.session() as s:
            assert s.scalar(select(Position)) is None


async def test_retained_discoveries_are_refreshed_after_leaving_new_feed(db, market):
    app = create_app(Settings(enable_scheduler=False), db)
    engine = app.state.engine
    engine.record_market(market)
    engine.discovery.discover = AsyncMock(return_value=([], []))
    engine.dex.tokens = AsyncMock(return_value=[market])
    engine.security.analyze = AsyncMock(
        return_value=__import__("app.schemas", fromlist=["SecurityResult"]).SecurityResult()
    )
    await engine.scan()
    engine.dex.tokens.assert_awaited_once_with([market.address])
    assert engine.last_scan is not None
    await engine.stop()


def test_reconciliation_mismatch_cannot_be_cleared(db):
    from decimal import Decimal

    from app.db import Account

    app = create_app(Settings(enable_scheduler=False), db)
    with TestClient(app) as client:
        with db.session() as session:
            session.get(Account, 1).cash -= Decimal(1)
        assert client.post("/api/control", json={"action": "clear_emergency"}).status_code == 409
        assert client.get("/api/state").json()["account"]["halted"]


def test_export_is_labelled_simulation_and_has_no_credentials(db):
    app = create_app(Settings(enable_scheduler=False), db)
    with TestClient(app) as client:
        report = client.get("/api/report").json()
        assert report["mode"] == "shadow"
        assert report["performance"]["closed"] == 0
        assert "rpc_url" not in report and "dashboard_token" not in report
