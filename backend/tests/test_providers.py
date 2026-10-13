import httpx
import pytest

from app.agents.security import SecurityAgent
from app.agents.smart_money import parse_transaction
from app.providers.http import ProviderError, SafeHTTP
from app.providers.jupiter import Jupiter
from app.providers.market import gecko_market
from app.providers.solana import SOL, TOKEN_2022, TOKEN_PROGRAM, USDC
from app.schemas import now


def test_gecko_null_cap_does_not_use_fdv():
    row = {
        "attributes": {
            "address": USDC,
            "name": "TEST / SOL",
            "fdv_usd": "10000",
            "market_cap_usd": None,
            "base_token_price_usd": ".01",
            "reserve_in_usd": "12000",
        },
        "relationships": {"base_token": {"data": {"id": "solana_" + SOL}}},
    }
    market = gecko_market(row, [], now())
    assert market.fdv == 10000 and market.market_cap is None
    assert market.buys_5m is None


async def test_cache_preserves_observation_timestamp():
    count = 0

    def handler(req):
        nonlocal count
        count += 1
        return httpx.Response(200, json={"ok": True})

    h = SafeHTTP("test", "https://example.test", 60000, transport=httpx.MockTransport(handler))
    first = await h.request("/x", ttl=60)
    second = await h.request("/x", ttl=60)
    assert first == second and count == 1
    await h.close()


async def test_api_failure_cannot_be_success_and_opens_circuit():
    h = SafeHTTP(
        "test", "https://example.test", 60000, transport=httpx.MockTransport(lambda r: httpx.Response(403))
    )
    for _ in range(3):
        with pytest.raises(ProviderError):
            await h.request("/x")
    assert h.health["state"] == "unavailable"
    requests = h.health["requests"]
    with pytest.raises(ProviderError):
        await h.request("/x")
    assert h.health["requests"] == requests
    await h.close()


async def test_retry_500_then_success(monkeypatch):
    count = 0

    def handler(req):
        nonlocal count
        count += 1
        return httpx.Response(500 if count == 1 else 200, json={})

    h = SafeHTTP("test", "https://example.test", 60000, transport=httpx.MockTransport(handler))
    await h.request("/retry")
    assert count == 2 and h.health["state"] == "active"
    await h.close()


async def test_security_pools_are_not_counted_as_wallets():
    class RPC:
        async def account(self, a):
            return {
                "owner": TOKEN_PROGRAM,
                "data": {
                    "parsed": {
                        "type": "mint",
                        "info": {
                            "mintAuthority": None,
                            "freezeAuthority": None,
                            "decimals": 6,
                            "supply": "1000",
                        },
                    }
                },
            }

        async def largest(self, a):
            return [{"address": USDC, "amount": "900"}]

        async def multiple(self, a):
            if a == [USDC]:
                return [{"data": {"parsed": {"info": {"owner": SOL, "mint": SOL}}}}]
            return [{"owner": TOKEN_PROGRAM, "executable": False}]

    r = await SecurityAgent(RPC()).analyze(SOL)
    assert r.authority_checked
    assert r.largest_accounts_pct == 90
    assert r.top10_pct is None and not r.concentration_resolved
    assert r.holders[0]["classification"] == "program_or_pool_candidate"


async def test_authority_rpc_failure_is_unknown():
    class RPC:
        async def account(self, a):
            raise ProviderError("fail")

    r = await SecurityAgent(RPC()).analyze(SOL)
    assert not r.authority_checked and r.unknown


async def test_token2022_is_blocked():
    class RPC:
        async def account(self, a):
            return {
                "owner": TOKEN_2022,
                "data": {
                    "parsed": {
                        "type": "mint",
                        "info": {
                            "mintAuthority": None,
                            "freezeAuthority": None,
                            "decimals": 6,
                            "supply": "1000",
                            "extensions": [{"extension": "transferHook"}],
                        },
                    }
                },
            }

        async def largest(self, a):
            return []

    r = await SecurityAgent(RPC()).analyze(SOL)
    assert r.extensions == ["transferHook"]
    assert any("Token-2022" in x for x in r.critical)


def tx_balance(mint, amount):
    return {"owner": SOL, "mint": mint, "uiTokenAmount": {"amount": str(amount), "decimals": 0}}


def test_transfer_is_not_a_purchase():
    tx = {
        "meta": {
            "err": None,
            "fee": 5000,
            "preTokenBalances": [],
            "postTokenBalances": [tx_balance(TOKEN_PROGRAM, 10)],
        }
    }
    r = parse_transaction(tx, SOL)
    assert r["kind"] == "transfer_or_unclassified" and r["side"] is None


def test_probable_usdc_swap_and_failed_tx():
    tx = {
        "meta": {
            "err": None,
            "fee": 5000,
            "preTokenBalances": [tx_balance(USDC, 100)],
            "postTokenBalances": [tx_balance(USDC, 90), tx_balance(TOKEN_PROGRAM, 10)],
        }
    }
    r = parse_transaction(tx, SOL)
    assert r["kind"] == "probable_swap" and r["side"] == "buy"
    tx["meta"]["err"] = {"InstructionError": [0, "x"]}
    assert parse_transaction(tx, SOL)["kind"] == "failed"


async def test_jupiter_route_validates_mints():
    j = Jupiter()
    await j.http.close()
    j.http = SafeHTTP(
        "j",
        "https://example.test",
        60000,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "inputMint": SOL,
                    "outputMint": USDC,
                    "inAmount": "100",
                    "outAmount": "10",
                    "router": "fixture",
                },
            )
        ),
    )
    with pytest.raises(ProviderError):
        await j.quote(USDC, SOL, 100)
    await j.http.close()


async def test_http_priority_respects_budget():
    calls = []

    def handler(req):
        calls.append((req.url.path, __import__("time").monotonic()))
        return httpx.Response(200, json={})

    h = SafeHTTP("test", "https://example.test", 6000, transport=httpx.MockTransport(handler))
    await h.request("/first")
    low = __import__("asyncio").create_task(h.request("/discovery", priority=10))
    high = __import__("asyncio").create_task(h.request("/risk", priority=0))
    await __import__("asyncio").gather(low, high)
    assert [c[0] for c in calls] == ["/first", "/risk", "/discovery"]
    assert calls[1][1] - calls[0][1] >= 0.009
    await h.close()


async def test_live_reconciliation_mismatch_is_explicit():
    from app.execution.live import LiveExecution

    class RPC:
        async def call(self, *a, **kw):
            return {"value": [{"account": {"data": {"parsed": {"info": {"tokenAmount": {"amount": "50"}}}}}}]}

    result = await LiveExecution(RPC(), None).reconcile(SOL, USDC, 100)
    assert not result["matches"] and result["actual_raw"] == 50
