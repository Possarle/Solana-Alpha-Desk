import pytest

from app.agents.bench import Benches
from app.agents.filter import arithmetic_filter, trades_filter
from app.agents.judge import judge
from app.agents.pick import pick
from app.agents.position_size import position_size
from app.schemas import SecurityResult, now, valid_address
from app.thresholds import Thresholds


def test_fdv_is_not_market_cap(market, limits):
    market.market_cap, market.fdv = None, 200000
    assert "Market cap confirmado: desconhecido" in arithmetic_filter(market, limits)


def test_unknown_authority_cannot_pass(market, limits):
    result = judge(market, SecurityResult(), limits, {}, {})
    assert not result.approved
    assert result.safety == 0


def test_sell_evidence_is_not_security(market, limits):
    assert trades_filter(market) == []
    assert not judge(market, SecurityResult(), limits, {}, {}).approved


def test_no_trade_and_best_candidate_are_distinct(market, limits):
    bad = judge(market, SecurityResult(), limits, {}, {})
    selection = pick([(market, bad)])
    assert selection["best_candidate"] is not None
    assert selection["selected"] is None
    assert not selection["worth_trading"]


def test_critical_cannot_be_overridden_by_social(market, limits, security):
    security.critical = ["Freeze authority ativa"]
    j = judge(market, security, limits, {"score": 100}, {"score": 100})
    assert not j.approved
    assert j.risk == "critical"


def test_unknown_concentration_strict_and_research_modes(market, limits, security):
    security.concentration_resolved = False
    security.top10_pct = None
    assert not judge(market, security, limits, {}, {}).approved
    limits.require_resolved_concentration = False
    limits.min_score = 50
    j = judge(market, security, limits, {}, {})
    assert j.approved and j.concentration is None
    assert j.risk == "high"
    size = position_size(10000, 10000, market, j, limits)
    assert size["uncertainty_factor"] == 0.5
    assert size["budget"] <= 50


def test_missing_transactions_are_not_zero(market, limits):
    market.buys_24h = None
    assert "Contagem de transações desconhecida" in arithmetic_filter(market, limits)


def test_bench_uses_network_and_address(db):
    b = Benches(db)
    b.reject("solana:mint-a", ["missing evidence"], 60)
    assert b.blocked("solana:mint-a")
    assert not b.blocked("other:mint-a")
    assert not b.blocked("solana:mint-b")


def test_stale_market_rejected(market, limits):
    market.observed_at = now() - 200
    assert "Dados de mercado antigos" in arithmetic_filter(market, limits)


def test_range_validation():
    with pytest.raises(ValueError):
        Thresholds(min_market_cap=10, max_market_cap=5)
    with pytest.raises(ValueError):
        Thresholds(max_position_pct=10)
    with pytest.raises(ValueError):
        Thresholds(min_liquidity=float("nan"))


def test_address_is_32_byte_base58():
    for bad in ["not-an-address", "0" * 44, "1" * 44, "1" * 31]:
        with pytest.raises(ValueError):
            valid_address(bad)
    assert valid_address("11111111111111111111111111111111")
