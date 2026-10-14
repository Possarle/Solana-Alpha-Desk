"""Unfunded fixtures only. No test connects to a chain or sends a financial transaction."""

import asyncio
import base64
import copy
import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from solders.hash import Hash
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from app.config import Settings
from app.db import LiveBook, LiveOrder
from app.execution.live_chain import MAINNET_GENESIS, LiveChain, token_account
from app.execution.supervised import ACK, SupervisedLive
from app.execution.transaction_guard import (
    IDL,
    JUPITER,
    associated,
    compile_review,
    sign_review,
    validate_swap,
)
from app.providers.solana import TOKEN_PROGRAM, USDC
from app.schemas import now
from app.thresholds import Thresholds


def build_fixture(owner, mint, amount=1_000_000, output=2_000_000, slip=100):
    spec = IDL["instructions"][0]
    addresses = {
        "user_transfer_authority": owner,
        "user_source_token_account": associated(owner, USDC),
        "user_destination_token_account": associated(owner, mint),
        "source_mint": USDC,
        "destination_mint": mint,
        "source_token_program": TOKEN_PROGRAM,
        "destination_token_program": TOKEN_PROGRAM,
        "destination_token_account": JUPITER,
        "program": JUPITER,
    }
    accounts = [
        {
            "pubkey": a.get("address") or addresses[a["name"]],
            "isSigner": a.get("signer", False),
            "isWritable": a.get("writable", False),
        }
        for a in spec["accounts"]
    ]
    # route_v2: ExactIn amounts, slip, zero fees, one Saber route (Swap enum variant 0).
    raw = (
        bytes(spec["discriminator"])
        + struct.pack("<QQHHHI", amount, output, slip, 0, 0, 1)
        + struct.pack("<BHBB", 0, 10000, 0, 1)
    )
    return {
        "inputMint": USDC,
        "outputMint": mint,
        "inAmount": str(amount),
        "outAmount": str(output),
        "swapMode": "ExactIn",
        "slippageBps": slip,
        "priceImpactPct": "0.001",
        "transactionVersion": 0,
        "swapInstruction": {
            "programId": JUPITER,
            "data": base64.b64encode(raw).decode(),
            "accounts": accounts,
        },
        "setupInstructions": [],
    }


@pytest.fixture
def key():
    return Keypair()


@pytest.fixture
def build(key, market):
    return build_fixture(str(key.pubkey()), market.address)


def test_guard_compiles_signs_exact_intent(key, build, market, tmp_path):
    owner = str(key.pubkey())
    assert validate_swap(build, owner, USDC, market.address, 1_000_000, 100) == 1_980_000
    wire = compile_review(build, owner, str(Hash.default()), 1000)
    path = tmp_path / "unfunded-test-key.json"
    path.write_text(json.dumps(list(bytes(key))))
    path.chmod(0o600)
    signed, sig = sign_review(wire, str(path), owner)
    tx = VersionedTransaction.from_bytes(base64.b64decode(signed))
    assert tx.verify_with_results() == [True]
    assert str(tx.signatures[0]) == sig
    path.chmod(0o644)
    with pytest.raises(ValueError, match="600"):
        sign_review(wire, str(path), owner)
    path.chmod(0o600)
    with pytest.raises(ValueError, match="corresponde"):
        sign_review(wire, str(path), str(Keypair().pubkey()))


@pytest.mark.parametrize(
    "tamper", ["mint", "amount", "program", "signer", "recipient", "slippage", "extra", "setup", "tip", "fee"]
)
def test_guard_rejects_modified_instructions(key, build, market, tamper):
    b = copy.deepcopy(build)
    if tamper == "mint":
        b["outputMint"] = USDC
    if tamper == "amount":
        b["inAmount"] = "2"
    if tamper == "program":
        b["swapInstruction"]["programId"] = TOKEN_PROGRAM
    if tamper == "signer":
        b["swapInstruction"]["accounts"].append({"pubkey": USDC, "isSigner": True, "isWritable": False})
    if tamper == "recipient":
        b["swapInstruction"]["accounts"][2]["pubkey"] = USDC
    if tamper == "slippage":
        b["slippageBps"] = 500
    if tamper == "extra":
        b["swapInstruction"]["data"] = base64.b64encode(
            base64.b64decode(b["swapInstruction"]["data"]) + b"\x00"
        ).decode()
    if tamper == "setup":
        b["setupInstructions"] = [{"programId": TOKEN_PROGRAM}]
    if tamper == "tip":
        b["tipInstruction"] = {"programId": TOKEN_PROGRAM}
    if tamper == "fee":
        raw = bytearray(base64.b64decode(b["swapInstruction"]["data"]))
        raw[26] = 1
        b["swapInstruction"]["data"] = base64.b64encode(raw).decode()
    with pytest.raises(ValueError):
        validate_swap(b, str(key.pubkey()), USDC, market.address, 1_000_000, 100)


@pytest.fixture
def live(db, market, security, key, tmp_path):
    owner = str(key.pubkey())
    keyfile = tmp_path / "unfunded-test-key.json"
    keyfile.write_text(json.dumps(list(bytes(key))))
    keyfile.chmod(0o600)
    cfg = Settings(
        _env_file=None,
        live_enabled=True,
        live_wallet_address=owner,
        live_keypair_path=str(keyfile),
        live_acknowledgement=ACK,
        dashboard_token=SecretStr("x" * 32),
        jupiter_api_key=SecretStr("test-only"),
        enable_scheduler=True,
    )
    engine = SimpleNamespace(
        db=db,
        settings=cfg,
        rpc=SimpleNamespace(),
        thresholds=Thresholds(),
        security=SimpleNamespace(analyze_complete=AsyncMock(return_value=security)),
        social=SimpleNamespace(analyze=lambda _: {}),
        smart=SimpleNamespace(signals=lambda _: {}),
        jupiter=SimpleNamespace(build=AsyncMock(return_value=build_fixture(owner, market.address))),
    )
    sup = SupervisedLive(engine)
    sup.initialize()
    sup.market = AsyncMock(return_value=market)

    async def call(method, params):
        if method == "getGenesisHash":
            return MAINNET_GENESIS
        if method == "getAccountInfo":
            return {"value": {"owner": TOKEN_PROGRAM, "data": {"parsed": {"info": {"decimals": 6}}}}}
        if method == "getBlockHeight":
            return 99
        raise AssertionError(method)

    sup.chain = SimpleNamespace(
        call=AsyncMock(side_effect=call),
        balances=AsyncMock(return_value={"sol": 100_000_000, "input": 1_000_000_000, "output": 0}),
        verify_accounts=AsyncMock(),
        blockhash=AsyncMock(return_value={"blockhash": str(Hash.default()), "lastValidBlockHeight": 100}),
        simulate=AsyncMock(return_value={"fee_lamports": 6400, "rent_lamports": 0, "units": 1000}),
        send_once=AsyncMock(side_effect=TimeoutError),
        status=AsyncMock(return_value=None),
        transaction=AsyncMock(),
    )
    return sup


async def review(live, market):
    return await live.prepare(str(uuid4()), market.address, "buy", "1")


async def confirm(live, r):
    return await live.confirm(r["id"], r["review_hash"], r["preview"]["confirmation_phrase"])


async def test_default_disabled_and_no_provider_called(live, market):
    live.cfg.live_enabled = False
    r = await review(live, market)
    assert r["status"] == "review"
    assert "unsigned" not in r
    with pytest.raises(ValueError, match="desativado"):
        await confirm(live, r)
    live.chain.send_once.assert_not_called()


async def test_duplicate_confirm_timeout_and_restart_do_not_rebroadcast(live, market):
    r = await review(live, market)
    results = await asyncio.gather(confirm(live, r), confirm(live, r))
    assert all(x["status"] == "pending" for x in results)
    assert live.chain.send_once.await_count == 1
    with live.db.session() as s:
        row = s.get(LiveOrder, r["id"])
        assert row.active_slot == 1 and row.data["signature"]
        assert s.get(LiveBook, 1).data["spent_raw"] == 1_000_000
    restarted = SupervisedLive(live.engine)
    restarted.chain = live.chain
    await restarted.poll()
    assert live.chain.send_once.await_count == 1
    with pytest.raises(ValueError, match="assinada"):
        await restarted.cancel(r["id"])
    with pytest.raises(ValueError, match="pendente"):
        await review(live, market)


@pytest.mark.parametrize(
    "change", ["expired", "balance", "policy", "threshold", "hash", "phrase", "halt", "cost"]
)
async def test_confirmation_fail_closed(live, market, monkeypatch, change):
    r = await review(live, market)
    if change == "expired":
        monkeypatch.setattr("app.execution.supervised.now", lambda: r["preview"]["expires_at"] + 1)
    if change == "balance":
        live.chain.balances.return_value = {"sol": 1, "input": 1, "output": 1}
    if change == "policy":
        live.cfg.live_max_slippage_bps = 50
    if change == "threshold":
        live.engine.thresholds.min_score = 99
    if change == "hash":
        r["review_hash"] = "0" * 64
    if change == "phrase":
        r["preview"]["confirmation_phrase"] = "anything"
    if change == "halt":
        live.halt()
    if change == "cost":
        live.chain.simulate.return_value = {"fee_lamports": 7000, "rent_lamports": 0}
    with pytest.raises(ValueError):
        await confirm(live, r)
    live.chain.send_once.assert_not_called()


async def test_prepare_idempotency_and_exposure(live, market):
    request_id = str(uuid4())
    r = await live.prepare(request_id, market.address, "buy", "1")
    assert await live.prepare(request_id, market.address, "buy", "1") == r
    assert live.engine.jupiter.build.await_count == 1
    with pytest.raises(ValueError, match="intenção"):
        await live.prepare(request_id, market.address, "buy", "2")
    await live.cancel(request_id)
    with pytest.raises(ValueError, match="limite"):
        await live.prepare(str(uuid4()), market.address, "buy", "6")


async def test_unknown_concentration_blocks(live, market, security):
    security.concentration_resolved = False
    with pytest.raises(ValueError, match="Concentração"):
        await review(live, market)
    live.engine.jupiter.build.assert_not_called()


async def test_wrong_chain_blocks(live, market):
    live.chain.call.side_effect = None
    live.chain.call.return_value = "devnet"
    with pytest.raises(ValueError, match="mainnet"):
        await review(live, market)
    live.engine.jupiter.build.assert_not_called()


def tx_fixture(p, failed=False):
    wallet = p["wallet"]
    keys = [wallet, associated(wallet, p["input_mint"]), associated(wallet, p["output_mint"])]

    def tok(index, mint, amount):
        return {
            "accountIndex": index,
            "mint": mint,
            "owner": wallet,
            "uiTokenAmount": {"amount": str(amount)},
        }

    return {
        "slot": 77,
        "transaction": {"message": {"accountKeys": keys}},
        "meta": {
            "err": {"InstructionError": [0, "error"]} if failed else None,
            "fee": 6400,
            "preBalances": [100_000_000, 0, 0],
            "postBalances": [99_993_600, 0, 0],
            "preTokenBalances": [tok(1, p["input_mint"], 1_000_000_000)],
            "postTokenBalances": [tok(1, p["input_mint"], 999_000_000), tok(2, p["output_mint"], 2_000_000)],
        },
    }


@pytest.mark.parametrize("outcome", ["success", "failed", "diverged", "confirmed_only"])
async def test_finalization_and_reconciliation(live, market, outcome):
    r = await review(live, market)
    await confirm(live, r)
    live.chain.status.return_value = {
        "confirmationStatus": "confirmed" if outcome == "confirmed_only" else "finalized"
    }
    live.chain.transaction.return_value = tx_fixture(r["preview"], outcome == "failed")
    live.chain.balances.return_value = {
        "sol": 99_993_600,
        "input": 1_000_000_000 if outcome == "failed" else 999_000_000,
        "output": 0 if outcome == "failed" else 2_000_000,
    }
    if outcome == "diverged":
        live.chain.balances.return_value["output"] = 1
    await live.poll()
    with live.db.session() as s:
        row = s.get(LiveOrder, r["id"])
        book = s.get(LiveBook, 1).data
        if outcome == "success":
            assert row.status == "finalized" and row.active_slot is None
            assert book["position"]["qty_raw"] == 2_000_000 and book["position"]["entry_price_usdc"] == 0.5
        elif outcome == "failed":
            assert row.status == "failed" and row.active_slot is None and not book.get("position")
        elif outcome == "diverged":
            assert row.status == "reconciliation_required" and row.active_slot == 1 and book["halted"]
        else:
            assert row.status == "pending" and row.active_slot == 1


async def test_write_ahead_signature(live, market):
    async def send(signed):
        with live.db.session() as s:
            row = s.get(LiveOrder, r["id"])
            assert row.status == "pending" and row.data["signature"]
            return row.data["signature"]

    live.chain.send_once.side_effect = send
    r = await review(live, market)
    await confirm(live, r)


async def test_risk_monitor_runs_when_live_disabled(live, market):
    live.cfg.live_enabled = False
    with live.db.session() as s:
        book = s.get(LiveBook, 1)
        book.data = {
            "position": {
                "mint": market.address,
                "entry_price_usdc": 2,
                "entry_market": market.model_dump(),
                "opened_at": now(),
            }
        }
    await live.poll()
    assert live.status()["book"]["exit_signal"] == "STOP_LOSS"
    live.chain.send_once.assert_not_called()


def token_fixture(owner, mint, amount):
    from solders.pubkey import Pubkey

    raw = bytearray(165)
    raw[:32] = bytes(Pubkey.from_string(mint))
    raw[32:64] = bytes(Pubkey.from_string(owner))
    raw[64:72] = amount.to_bytes(8, "little")
    raw[108] = 1
    return {"owner": TOKEN_PROGRAM, "data": [base64.b64encode(raw).decode(), "base64"]}


@pytest.mark.parametrize("offset", [72, 108, 129])
def test_frozen_delegated_or_close_authority_rejected(key, market, offset):
    a = token_fixture(str(key.pubkey()), market.address, 1)
    raw = bytearray(base64.b64decode(a["data"][0]))
    raw[offset] = 2 if offset == 108 else 1
    a["data"][0] = base64.b64encode(raw).decode()
    with pytest.raises(ValueError):
        token_account(a, str(key.pubkey()), market.address)


async def test_rpc_broadcast_no_retry():
    client = SimpleNamespace(post=AsyncMock(side_effect=TimeoutError))
    chain = LiveChain(SimpleNamespace(http=SimpleNamespace(client=client)))
    with pytest.raises(TimeoutError):
        await chain.send_once("unfunded-fixture-not-a-transaction")
    assert client.post.await_count == 1


async def test_confirmed_sell_releases_position_only_after_reconciliation(live, market):
    r = await review(live, market)
    await confirm(live, r)
    live.chain.status.return_value = {"confirmationStatus": "finalized"}
    live.chain.transaction.return_value = tx_fixture(r["preview"])
    live.chain.balances.return_value = {"sol": 99_993_600, "input": 999_000_000, "output": 2_000_000}
    await live.poll()
    # Build the opposite route using the same checked ABI, with source/destination swapped.
    owner = live.cfg.live_wallet_address
    sell = build_fixture(owner, market.address, amount=2_000_000, output=1_100_000)
    sell["inputMint"], sell["outputMint"] = market.address, USDC
    accounts = sell["swapInstruction"]["accounts"]
    accounts[1]["pubkey"], accounts[2]["pubkey"] = associated(owner, market.address), associated(owner, USDC)
    accounts[3]["pubkey"], accounts[4]["pubkey"] = market.address, USDC
    live.engine.jupiter.build.return_value = sell
    live.chain.balances.return_value = {"sol": 99_993_600, "input": 2_000_000, "output": 999_000_000}
    r = await live.prepare(str(uuid4()), market.address, "sell", "1")
    await confirm(live, r)
    assert live.status()["book"]["position"]
    tx = tx_fixture(r["preview"])
    tx["meta"]["preBalances"][0] = 99_993_600
    tx["meta"]["postBalances"][0] = 99_987_200

    def tok(i, m, a):
        return {"accountIndex": i, "mint": m, "owner": owner, "uiTokenAmount": {"amount": str(a)}}

    tx["meta"]["preTokenBalances"] = [tok(1, market.address, 2_000_000), tok(2, USDC, 999_000_000)]
    tx["meta"]["postTokenBalances"] = [tok(1, market.address, 0), tok(2, USDC, 1_000_100_000)]
    live.chain.transaction.return_value = tx
    live.chain.balances.return_value = {"sol": 99_987_200, "input": 0, "output": 1_000_100_000}
    await live.poll()
    state = live.status()
    assert not state["book"].get("position")
    assert state["orders"][0]["result"]["pnl_usdc_before_sol_costs"] == 0.1


async def test_simulation_checks_actual_account_deltas(key, market, build):
    from app.providers.solana import SYSTEM_PROGRAM

    owner = str(key.pubkey())
    wire = compile_review(build, owner, str(Hash.default()), 1000)
    values = [
        {"owner": SYSTEM_PROGRAM, "lamports": 99_993_600},
        token_fixture(owner, USDC, 999_000_000),
        token_fixture(owner, market.address, 2_000_000),
    ]

    async def call(method, params):
        if method == "getFeeForMessage":
            return {"value": 6400}
        return {"value": {"err": None, "accounts": values, "unitsConsumed": 9000}}

    chain = LiveChain(SimpleNamespace())
    chain.call = AsyncMock(side_effect=call)
    before = {"sol": 100_000_000, "input": 1_000_000_000, "output": 0}
    result = await chain.simulate(wire, owner, USDC, market.address, before, 1_000_000, 1_980_000, 50000)
    assert result["fee_lamports"] == 6400
    values[2] = token_fixture(owner, market.address, 1)
    with pytest.raises(ValueError, match="Efeitos"):
        await chain.simulate(wire, owner, USDC, market.address, before, 1_000_000, 1_980_000, 50000)


async def test_enumeration_requires_full_supply_and_groups_owners(security, market):
    from app.agents.security import SecurityAgent

    mint = market.address
    info = {"supply": "100", "decimals": 6, "mintAuthority": None, "freezeAuthority": None}
    rows = [
        {
            "pubkey": str(i),
            "account": {
                "owner": TOKEN_PROGRAM,
                "data": {
                    "parsed": {
                        "type": "account",
                        "info": {
                            "mint": mint,
                            "owner": str(i // 2),
                            "tokenAmount": {"amount": "1", "decimals": 6},
                        },
                    }
                },
            },
        }
        for i in range(100)
    ]

    async def call(method, params, **kwargs):
        if method == "getAccountInfo":
            return {
                "context": {"slot": 1},
                "value": {"owner": TOKEN_PROGRAM, "data": {"parsed": {"info": info}}},
            }
        return {"value": rows}

    agent = SecurityAgent(SimpleNamespace(call=AsyncMock(side_effect=call)))
    agent.analyze = AsyncMock(side_effect=lambda _: security.model_copy(deep=True))
    result = await agent.analyze_complete(mint)
    assert result.concentration_resolved and result.top10_pct == 20
    rows.pop()
    result = await agent.analyze_complete(mint)
    assert not result.concentration_resolved


async def test_api_requires_auth_and_default_does_not_send(db):
    import httpx

    from app.main import create_app

    cfg = Settings(_env_file=None, enable_scheduler=False, dashboard_token=SecretStr("x" * 32))
    app = create_app(cfg, db)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/live/status")).status_code == 401
            client.headers["Authorization"] = "Bearer " + "x" * 32
            data = (await client.get("/api/live/status")).json()
            assert data["orders"] == [] and data["send_blockers"]
            assert (
                await client.post(
                    "/api/live/prepare",
                    json={"request_id": str(uuid4()), "side": "buy", "address": USDC, "usdc": 1},
                )
            ).status_code == 409
            assert (
                await client.post(
                    "/api/live/prepare",
                    json={"request_id": str(uuid4()), "side": "buy", "address": USDC, "usdc": "NaN"},
                )
            ).status_code == 422


def test_mainnet_uses_full_genesis_hash():
    assert str(Hash.from_string(MAINNET_GENESIS)) == "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"


async def test_daily_budget_survives_restart(live, market):
    r = await review(live, market)
    await live.cancel(r["id"])
    with live.db.session() as s:
        book = s.get(LiveBook, 1)
        book.data = book.data | {"spent_raw": 30_000_000}
    live.initialize()
    with pytest.raises(ValueError, match="diário"):
        await review(live, market)
    live.engine.jupiter.build.reset_mock()
    assert live.status()["book"]["spent_raw"] == "30000000"


async def test_settings_change_during_prepare_does_not_reauthorize(live, market):
    async def simulate(*args):
        live.engine.thresholds.min_score = 99
        return {"fee_lamports": 6400, "rent_lamports": 0, "units": 1000}

    live.chain.simulate.side_effect = simulate
    r = await review(live, market)
    with pytest.raises(ValueError, match="Configuração"):
        await confirm(live, r)
    live.chain.send_once.assert_not_called()


def test_public_ledger_preserves_u64_precision():
    value = 2**64 - 1
    assert SupervisedLive.public_book({"position": {"qty_raw": value}, "cash_raw": 1})["position"][
        "qty_raw"
    ] == str(value)
