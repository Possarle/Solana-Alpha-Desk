"""Launch mode (shadow): pump.fun curve fills, rules, separation from the main book."""

import json
from dataclasses import replace

import pytest
from solders.keypair import Keypair
from sqlalchemy import select

from app.config import Settings
from app.db import Account, Position
from app.launch.desk import LaunchDesk, mint_checks
from app.launch.feed import PumpPortalFeed
from app.launch.pumpfun import (
    EXPECTED_SUPPLY_RAW,
    Curve,
    CurveError,
    curve_address,
    decode_curve,
    encode_curve,
    parse_launch,
)
from app.main import create_app
from app.providers.solana import TOKEN_2022, TOKEN_PROGRAM
from app.schemas import Market, now
from app.thresholds import LaunchThresholds

SOL_USD = 150.0
FRESH = Curve(1_073_000_000 * 10**6, 30 * 10**9, 793_100_000 * 10**6, 0, EXPECTED_SUPPLY_RAW, False)


def with_inflow(curve: Curve, sol: float) -> Curve:
    """Curve after `sol` SOL of buys (no fee), as the program would hold it."""
    lamports = int(sol * 10**9)
    out = curve.virtual_token - (curve.virtual_sol * curve.virtual_token) // (curve.virtual_sol + lamports)
    return replace(
        curve,
        virtual_sol=curve.virtual_sol + lamports,
        virtual_token=curve.virtual_token - out,
        real_sol=curve.real_sol + lamports,
        real_token=curve.real_token - out,
    )


def mint_account(program=TOKEN_PROGRAM, mint_auth=None, freeze=None, extensions=()):
    info = {"mintAuthority": mint_auth, "freezeAuthority": freeze, "decimals": 6, "supply": "1"}
    if extensions:
        info["extensions"] = [{"extension": x} for x in extensions]
    return {"owner": program, "data": {"parsed": {"type": "mint", "info": info}}}


def create_event(mint, dev_tokens=30_000_000, dev_sol=0.9, **extra):
    return {
        "txType": "create",
        "mint": mint,
        "symbol": "FIXLAUNCH",
        "name": "Fixture Launch",
        "traderPublicKey": str(Keypair().pubkey()),
        "initialBuy": dev_tokens,
        "solAmount": dev_sol,
        "signature": "sig",
        **extra,
    }


class FakeRPC:
    def __init__(self):
        self.curves, self.mints, self.calls = {}, {}, 0
        self.fail = False

    async def call(self, method, params, ttl=30, priority=10):
        self.calls += 1
        if self.fail:
            raise TimeoutError()
        assert method == "getMultipleAccounts"
        out = []
        for address in params[0]:
            if address in self.curves:
                out.append(encode_curve(self.curves[address], b"\x00" * 32))
            else:
                out.append(self.mints.get(address))
        return {"value": out}


class FakeDex:
    def __init__(self):
        self.pool_quotes = []

    async def tokens(self, addresses):
        from app.providers.solana import SOL

        if addresses == [SOL]:
            return [
                Market(
                    address=SOL,
                    pool=str(Keypair().pubkey()),
                    symbol="SOL",
                    source="dexscreener",
                    price=SOL_USD,
                    liquidity=5e6,
                )
            ]
        return [m for m in self.pool_quotes if m.address in addresses]


@pytest.fixture
def desk(db):
    engine = create_app(Settings(enable_scheduler=False), db).state.engine
    engine.launch.execution.initialize()
    d = LaunchDesk(engine)
    d.rpc, d.dex = FakeRPC(), FakeDex()
    return d


def launch_for(desk, curve, mint_acc=None, **event):
    mint = str(Keypair().pubkey())
    launch = parse_launch(create_event(mint, **event), now() - 20)
    desk.rpc.curves[curve_address(mint)] = curve
    desk.rpc.mints[mint] = mint_acc or mint_account()
    return launch


# ---------- curve and parsing ----------


def test_curve_math_matches_documented_launch_state():
    assert round(FRESH.price_sol * 1e9, 1) == 28.0  # ~28 SOL market cap at creation
    tokens = FRESH.buy(1.0)
    assert 3.2 < (1 / tokens / FRESH.price_sol - 1) * 100 < 3.5  # 1 SOL moves price ~3.3%
    after = with_inflow(FRESH, 1.0)
    assert abs(after.sell(tokens) - 1.0) < 1e-6  # constant product round trip before fees


def test_decode_rejects_anything_unexpected():
    good = encode_curve(FRESH, b"\x00" * 32)
    assert decode_curve(good) == FRESH
    with pytest.raises(CurveError):
        decode_curve(good | {"owner": TOKEN_PROGRAM})
    with pytest.raises(CurveError):
        decode_curve(encode_curve(replace(FRESH, supply=123)))
    with pytest.raises(CurveError):
        decode_curve(None)


def test_parse_only_create_events_with_valid_mint():
    mint = str(Keypair().pubkey())
    launch = parse_launch(create_event(mint), 1.0)
    assert launch.mint == mint and launch.dev_buy_pct == 3.0
    assert parse_launch({"message": "Successfully subscribed"}, 1.0) is None
    assert parse_launch(create_event("not-an-address"), 1.0) is None
    assert parse_launch(create_event(mint) | {"txType": "buy"}, 1.0) is None


def test_mint_checks_allow_only_metadata_extensions():
    assert mint_checks(mint_account())[0] == []
    assert mint_checks(mint_account(TOKEN_2022, extensions=("metadataPointer", "tokenMetadata")))[0] == []
    assert "Mint authority ativa" in mint_checks(mint_account(mint_auth="x"))[0]
    fails = mint_checks(mint_account(TOKEN_2022, extensions=("transferHook",)))[0]
    assert any("transferHook" in f for f in fails)


# ---------- decision flow ----------


async def test_buys_confirmed_launch_and_keeps_books_separate(desk, db):
    launch = launch_for(desk, with_inflow(FRESH, 0.9 + 2.5))  # creator 0.9 SOL + 2.5 SOL from others
    verdict = await desk.check(launch, LaunchThresholds())
    assert verdict["approved"] and verdict["execution"]["status"] == "simulated"
    assert verdict["facts"]["buyers_sol"] == 2.5
    assert desk.recent[0]["mint"] == launch.mint and desk.recent[0]["mint_info"]["program"] == "SPL Token"
    with db.session() as s:
        p = s.scalar(select(Position).where(Position.mode == "launch"))
        assert p.active_slot == 2 and float(p.cost) == 50.0  # 0.5% of $10k launch capital
        assert float(s.get(Account, 2).cash) == 9950.0 and float(s.get(Account, 1).cash) == 10000.0
    assert desk.execution.reconcile()["matches"]
    assert desk.engine.execution.reconcile()["matches"]  # main book untouched


@pytest.mark.parametrize(
    "event,curve,mint_acc,reason",
    [
        ({"dev_tokens": None}, None, None, "Compra do criador desconhecida"),
        ({}, FRESH, None, "Pouca compra de terceiros"),
        ({}, None, mint_account(freeze="x"), "Freeze authority ativa"),
        ({}, "ran", None, "Já subiu demais"),
        ({}, "migrated", None, "Já saiu da curva"),
    ],
)
async def test_rejections_are_explicit(desk, db, event, curve, mint_acc, reason):
    if curve is None:
        curve = with_inflow(FRESH, 3.4)
    elif curve == "ran":
        curve = with_inflow(FRESH, 60)  # ~$37k market cap at $150/SOL
    elif curve == "migrated":
        curve = replace(with_inflow(FRESH, 3.4), complete=True)
    launch = launch_for(desk, curve, mint_acc, **event)
    verdict = await desk.check(launch, LaunchThresholds())
    assert not verdict["approved"] and any(reason in r for r in verdict["reasons"])
    with db.session() as s:
        assert s.scalar(select(Position).where(Position.mode == "launch")) is None


async def test_rpc_failure_and_dev_prefilter_never_buy(desk, db):
    desk.rpc.fail = True
    launch = launch_for(desk, with_inflow(FRESH, 3.4))
    assert not (await desk.check(launch, LaunchThresholds()))["approved"]
    big_dev = parse_launch(create_event(str(Keypair().pubkey()), dev_tokens=200_000_000), now())
    desk.on_launch(big_dev)
    assert not desk.queue and desk.recent[0]["reasons"][0].startswith("Criador comprou 20.0%")


async def test_queue_waits_confirmation_and_respects_budget(desk):
    t = LaunchThresholds(confirm_seconds=15, max_checks_per_minute=1)
    desk.engine.db.set_config("launch_thresholds", t.model_dump())
    first = launch_for(desk, with_inflow(FRESH, 3.4))
    second = launch_for(desk, with_inflow(FRESH, 3.4))
    desk.on_launch(replace(first, seen_at=now() - 20))
    desk.on_launch(replace(second, seen_at=now() - 18))
    young = launch_for(desk, with_inflow(FRESH, 3.4))
    desk.on_launch(replace(young, seen_at=now()))
    await desk.tick()
    assert desk.rpc.calls == 1  # budget: one check per minute; the young one is still waiting
    assert len(desk.queue) == 2


# ---------- exits on the curve ----------


async def open_position(desk):
    curve = with_inflow(FRESH, 3.4)
    launch = launch_for(desk, curve)
    assert (await desk.check(launch, LaunchThresholds()))["execution"]["status"] == "simulated"
    return launch, curve


async def test_take_profit_on_curve(desk, db):
    launch, curve = await open_position(desk)
    desk.rpc.curves[curve_address(launch.mint)] = with_inflow(curve, 12)  # strong buying
    result = await desk.risk_tick()
    assert result["reason"] == "TAKE_PROFIT" and result["pnl"] > 0
    assert desk.execution.reconcile()["matches"]


async def test_dev_dump_hits_stop(desk):
    launch, curve = await open_position(desk)
    dumped = replace(curve, virtual_sol=curve.virtual_sol - 2 * 10**9, real_sol=curve.real_sol - 2 * 10**9)
    desk.rpc.curves[curve_address(launch.mint)] = dumped
    result = await desk.risk_tick()
    assert result["reason"] in ("STOP_LOSS", "LIQUIDITY_DROP") and result["pnl"] < 0


async def test_migration_waits_for_pool_price_then_exits(desk, db):
    launch, curve = await open_position(desk)
    desk.rpc.curves[curve_address(launch.mint)] = replace(with_inflow(curve, 60), complete=True)
    assert (await desk.risk_tick()) == {"status": "exit_pending", "reason": "MIGRATED"}
    with db.session() as s:
        p = s.scalar(select(Position).where(Position.mode == "launch"))
        entry = float(p.entry)
    desk.dex.pool_quotes = [
        Market(
            address=launch.mint,
            pool=str(Keypair().pubkey()),
            symbol="FIXLAUNCH",
            source="dexscreener",
            source_cache_seconds=0,
            price=entry * 3,
            liquidity=80000,
        )
    ]
    result = await desk.risk_tick()
    assert result["status"] == "simulated" and result["reason"] == "MIGRATED" and result["pnl"] > 0


async def test_failed_read_keeps_position_until_fresh_data(desk, db):
    launch, curve = await open_position(desk)
    desk.rpc.fail = True
    assert (await desk.risk_tick())["status"] == "exit_pending"
    desk.rpc.fail = False
    assert await desk.risk_tick() is None  # data back, no trigger: keep holding
    with db.session() as s:
        assert s.scalar(select(Position).where(Position.mode == "launch")).status == "open"


# ---------- feed ----------


class FakeSocket:
    def __init__(self, messages):
        self.messages, self.sent = messages, []

    async def send(self, data):
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for m in self.messages:
            yield m


class FakeConnect:
    def __init__(self, socket):
        self.socket = socket

    def __call__(self, url, **kw):
        return self

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, *a):
        return False


async def test_feed_subscribes_once_and_delivers_launches():
    mint = str(Keypair().pubkey())
    got = []
    socket = FakeSocket(
        [json.dumps({"message": "Successfully subscribed"}), "not json", json.dumps(create_event(mint))]
    )
    feed = PumpPortalFeed(got.append, connect=FakeConnect(socket))
    assert await feed.session() == 1
    assert socket.sent == [{"method": "subscribeNewToken"}]
    assert got[0].mint == mint and feed.health["errors"] == 1 and feed.health["state"] == "active"


def test_api_exposes_launch_state_and_settings(db):
    from fastapi.testclient import TestClient

    app = create_app(Settings(enable_scheduler=False), db)
    with TestClient(app) as client:
        state = client.get("/api/launch").json()
        assert state["account"]["equity"] == 10000 and state["thresholds"]["confirm_seconds"] == 15
        new = state["thresholds"] | {"confirm_seconds": 5}
        assert client.put("/api/launch/thresholds", json=new).status_code == 200
        assert client.get("/api/launch").json()["thresholds"]["confirm_seconds"] == 5
        assert client.put("/api/launch/thresholds", json=new | {"max_position_pct": 5}).status_code == 422
        assert any(s["name"] == "PumpPortal" for s in client.get("/api/state").json()["services"])
