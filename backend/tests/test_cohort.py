"""Counterfactual cohort: only same-pool prices count; vanished tokens are 'sem dado'."""

from solders.keypair import Keypair

import app.cohort as cohort_module
from app.agents.judge import judge
from app.cohort import CohortTracker
from app.db import Observation
from app.schemas import SecurityResult

T0 = 1_800_000_000.0


def other_address() -> str:
    return str(Keypair().pubkey())


class FakeDex:
    def __init__(self, markets):
        self.markets, self.calls = markets, []

    async def tokens(self, addresses):
        self.calls.append(list(addresses))
        return [m for m in self.markets if m.address in addresses]


def at(monkeypatch, seconds):
    monkeypatch.setattr(cohort_module, "now", lambda: T0 + seconds)


async def test_both_outcomes_registered_and_same_pool_measured(db, market, judgment, limits, monkeypatch):
    market.observed_at = T0
    tracker = CohortTracker(db)
    rejected = judge(market, SecurityResult(), limits, {}, {})
    assert judgment.approved and not rejected.approved
    tracker.register(market, judgment)
    tracker.register(market, rejected)
    tracker.register(market, judgment)  # repeated evaluation is not a new sample

    later = market.model_copy(update={"price": 1.2, "observed_at": T0 + 16 * 60})
    other_pool = market.model_copy(
        update={"pool": other_address(), "price": 9.0, "observed_at": T0 + 16 * 60}
    )
    dex = FakeDex([other_pool, later])
    at(monkeypatch, 16 * 60)
    result = await tracker.tick(dex)
    assert result["requested"] == 1 and dex.calls == [[market.address]]

    report = tracker.report()
    approved = report["groups"]["approved"]["horizons"]["15"]
    assert report["groups"]["approved"]["tokens"] == 1 and report["groups"]["rejected"]["tokens"] == 1
    assert round(approved["median_pct"], 6) == 20.0  # 9.0 from another pool was ignored
    assert report["groups"]["all_first"]["tokens"] == 1


async def test_vanished_token_is_missing_not_a_loss(db, market, judgment, monkeypatch):
    market.observed_at = T0
    tracker = CohortTracker(db)
    tracker.register(market, judgment)
    at(monkeypatch, 16 * 60)
    await tracker.tick(FakeDex([]))
    at(monkeypatch, 30 * 60)  # 15 min window closed without a price
    await tracker.tick(FakeDex([]))
    h15 = tracker.report()["groups"]["approved"]["horizons"]["15"]
    assert h15["missing"] == 1 and h15["measured"] == 0 and h15["median_pct"] is None


async def test_scanner_observation_reused_without_request(db, market, judgment, monkeypatch):
    market.observed_at = T0
    tracker = CohortTracker(db)
    tracker.register(market, judgment)
    with db.session() as s:
        seen = market.model_copy(update={"price": 0.5})
        s.add(Observation(token_key="solana:" + market.address, at=T0 + 61 * 60, data=seen.model_dump()))
    dex = FakeDex([])
    at(monkeypatch, 62 * 60)
    await tracker.tick(dex)
    h60 = tracker.report()["groups"]["approved"]["horizons"]["60"]
    assert h60["measured"] == 1 and round(h60["median_pct"], 6) == -50.0
    # 15 min window already expired; 60 min was served by the scanner, so no request was needed.
    assert dex.calls == []


async def test_source_failure_fabricates_nothing(db, market, judgment, monkeypatch):
    market.observed_at = T0
    tracker = CohortTracker(db)
    tracker.register(market, judgment)

    class Broken:
        async def tokens(self, addresses):
            raise RuntimeError("down")

    at(monkeypatch, 16 * 60)
    result = await tracker.tick(Broken())
    assert result["filled"] == 0
    assert tracker.report()["groups"]["approved"]["horizons"]["15"]["pending"] == 1


async def test_engine_cycle_feeds_pools_to_security_and_registers_cohort(db, market, security):
    from unittest.mock import AsyncMock

    from app.config import Settings
    from app.db import CohortEntry, Pool
    from app.main import create_app

    e = create_app(Settings(enable_scheduler=False), db).state.engine
    second_pool = market.model_copy(update={"pool": other_address(), "liquidity": 1000})
    e.discovery.discover = AsyncMock(return_value=([market], []))
    e.discovery.seen = [market, second_pool]
    e.security.analyze = AsyncMock(return_value=security)
    e.gecko.refresh = AsyncMock(return_value=market)
    try:
        await e.scan()
        pools = e.security.analyze.call_args.kwargs["pools"]
        assert pools[0] == market.pool and second_pool.pool in pools
        with db.session() as s:
            assert s.get(Pool, "solana:" + second_pool.pool) is not None
            entry = s.get(CohortEntry, "solana:" + market.address + ":approved")
            assert entry is not None and entry.data["pool"] == market.pool
    finally:
        await e.stop()
