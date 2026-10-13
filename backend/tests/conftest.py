import pytest

from app.agents.judge import judge
from app.db import Base, Database
from app.execution.simulation import ShadowExecution
from app.schemas import Market, SecurityResult, now
from app.thresholds import Thresholds

MINT = "So11111111111111111111111111111111111111112"
POOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@pytest.fixture
def db(tmp_path):
    db = Database("sqlite:///" + str(tmp_path / "test.db"))
    Base.metadata.create_all(db.engine)
    ShadowExecution(db).initialize()
    yield db
    db.engine.dispose()


@pytest.fixture
def market():
    return Market(
        address=MINT,
        pool=POOL,
        symbol="TEST FIXTURE",
        source="geckoterminal",
        price=1,
        market_cap=200000,
        liquidity=100000,
        volume_24h=200000,
        buys_24h=300,
        sells_24h=250,
        buys_5m=25,
        sells_5m=20,
        buys_1h=150,
        change_5m=4,
        change_24h=10,
        created_at=now() - 3600,
    )


@pytest.fixture
def limits():
    return Thresholds()


@pytest.fixture
def security():
    return SecurityResult(
        authority_checked=True,
        concentration_resolved=True,
        top10_pct=12,
        unknown=["Developers unknown"],
        decimals=6,
    )


@pytest.fixture
def judgment(market, security, limits):
    return judge(market, security, limits, {}, {})
