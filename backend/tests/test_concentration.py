"""Scanner concentration: pool vaults proven from pool state, never assumed."""

import base64

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from app.agents.security import SecurityAgent
from app.providers.solana import SYSTEM_PROGRAM, TOKEN_PROGRAM

DEX = Pubkey.from_string("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc")
OTHER_PROGRAM = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")


def wallet() -> str:
    return str(Keypair().pubkey())


def pda(seed: bytes, program: Pubkey = DEX) -> str:
    return str(Pubkey.find_program_address([seed], program)[0])


MINT = wallet()
POOL = pda(b"pool")


class FakeRPC:
    """accounts: list of (token_account, owner, raw_amount); supply in raw units."""

    def __init__(self, accounts, supply=1_000_000, pool_data=None, owner_infos=None):
        self.accounts, self.supply = accounts, supply
        self.pool_data = pool_data
        self.owner_infos = owner_infos or {}

    async def account(self, address):
        info = {"mintAuthority": None, "freezeAuthority": None, "decimals": 6, "supply": str(self.supply)}
        return {"owner": TOKEN_PROGRAM, "data": {"parsed": {"type": "mint", "info": info}}}

    async def largest(self, address):
        return [{"address": a, "amount": str(n)} for a, _, n in self.accounts]

    async def multiple(self, addresses):
        token_accounts = {a: o for a, o, _ in self.accounts}
        if addresses and addresses[0] in token_accounts:
            return [
                {"data": {"parsed": {"info": {"owner": token_accounts[a], "mint": MINT}}}} for a in addresses
            ]
        return [self.owner_infos.get(a, {"owner": SYSTEM_PROGRAM, "executable": False}) for a in addresses]

    async def call(self, method, params, ttl=30, priority=10):
        assert method == "getMultipleAccounts" and params[1]["encoding"] == "base64"
        values = []
        for pool in params[0]:
            if self.pool_data is None or pool != POOL:
                values.append(None)
            else:
                values.append(
                    {"owner": str(DEX), "data": [base64.b64encode(self.pool_data).decode(), "base64"]}
                )
        return {"value": values}


def pool_state(*vaults: str) -> bytes:
    return b"\x00" * 8 + b"".join(bytes(Pubkey.from_string(v)) for v in vaults) + b"\x01" * 40


async def test_verified_vault_excluded_and_wallets_counted():
    vault = wallet()
    holders = [(wallet(), wallet(), n) for n in (100_000, 50_000, 30_000)]
    rpc = FakeRPC([(vault, pda(b"authority"), 600_000), *holders], pool_data=pool_state(vault))
    r = await SecurityAgent(rpc).analyze(MINT, pools=[POOL])
    assert r.concentration_resolved and r.concentration_method == "complete_accounts"
    assert r.top10_pct == 18.0  # 180k of 1M; the 600k pool vault is not a holder
    assert r.pool_pct == 60.0
    kinds = {h["classification"] for h in r.holders}
    assert "verified_pool_vault" in kinds and "wallet" in kinds


async def test_unlinked_program_address_is_counted_conservatively():
    vault, unknown_vault = wallet(), wallet()
    rpc = FakeRPC(
        [(vault, POOL, 500_000), (unknown_vault, pda(b"lock", OTHER_PROGRAM), 300_000)],
        pool_data=pool_state(),
    )
    r = await SecurityAgent(rpc).analyze(MINT, pools=[POOL])
    # Owner == observed pool proves liquidity; an unrelated PDA (lock, curve, other pool) still counts.
    assert r.concentration_resolved and r.top10_pct == 30.0
    unknown = next(h for h in r.holders if h["sample_pct"] == 30.0)
    assert unknown["classification"] == "program_or_pool_candidate" and unknown["counted"]


async def test_full_sample_uses_upper_bound_for_unseen_holders():
    vault = wallet()
    whales = [(wallet(), wallet(), 10_000) for _ in range(4)]
    # Same owner spread over 15 accounts: grouped as ONE holder.
    shared = wallet()
    spread = [(wallet(), shared, 2_000) for _ in range(15)]
    rpc = FakeRPC([(vault, pda(b"a"), 400_000), *whales, *spread], pool_data=pool_state(vault))
    r = await SecurityAgent(rpc).analyze(MINT, pools=[POOL])
    assert r.concentration_method == "top20_upper_bound"
    # 4 whales (40k) + shared owner (30k) + 5 unseen slots × smallest sampled account (2k)
    assert r.top10_pct == 8.0
    assert any("limite superior" in n for n in r.notes)


async def test_unreadable_pool_keeps_concentration_unresolved():
    rpc = FakeRPC([(wallet(), wallet(), 900_000)], pool_data=None)
    r = await SecurityAgent(rpc).analyze(MINT, pools=[POOL])
    assert not r.concentration_resolved and r.top10_pct is None
    assert any("pool observado não pôde ser lido" in u for u in r.unknown)


async def test_same_dex_program_account_is_liquidity_not_holder():
    other_pool = pda(b"pool-2")
    vault = wallet()
    rpc = FakeRPC(
        [(vault, other_pool, 300_000), (wallet(), wallet(), 50_000)],
        pool_data=pool_state(),
        owner_infos={other_pool: {"owner": str(DEX), "executable": False}},
    )
    r = await SecurityAgent(rpc).analyze(MINT, pools=[POOL])
    assert r.top10_pct == 5.0
    excluded = next(h for h in r.holders if h["owner"] == other_pool)
    assert excluded["classification"] == "same_dex_program_account" and not excluded["counted"]
