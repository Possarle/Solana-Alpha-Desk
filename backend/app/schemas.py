import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now() -> float:
    return time.time()


def valid_address(value: str) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    try:
        n = 0
        for c in value:
            n = n * 58 + alphabet.index(c)
        length = (n.bit_length() + 7) // 8 + len(value) - len(value.lstrip("1"))
        if length != 32:
            raise ValueError()
    except ValueError:
        raise ValueError("Endereço Solana inválido (base58 / 32 bytes)") from None
    return value


class Market(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    chain: Literal["solana"] = "solana"
    address: str
    pool: str
    symbol: str
    name: str = ""
    source: Literal["geckoterminal", "dexscreener"]
    observed_at: float = Field(default_factory=now)
    source_updated_at: float | None = None
    source_cache_seconds: int = 60
    price: float | None = Field(None, ge=0)
    market_cap: float | None = Field(None, ge=0)
    fdv: float | None = Field(None, ge=0)
    liquidity: float | None = Field(None, ge=0)
    volume_24h: float | None = Field(None, ge=0)
    volume_5m: float | None = Field(None, ge=0)
    buys_24h: int | None = Field(None, ge=0)
    sells_24h: int | None = Field(None, ge=0)
    buys_5m: int | None = Field(None, ge=0)
    sells_5m: int | None = Field(None, ge=0)
    buys_1h: int | None = Field(None, ge=0)
    change_5m: float | None = None
    change_24h: float | None = None
    created_at: float | None = None
    links: list[dict] = Field(default_factory=list)
    discovered_by: str = "independent"

    @field_validator("address", "pool")
    @classmethod
    def address_check(cls, v: str) -> str:
        return valid_address(v)

    def age_seconds(self) -> float:
        return now() - self.observed_at + self.source_cache_seconds


class SecurityResult(BaseModel):
    checked_at: float = Field(default_factory=now)
    mint_authority: str | None = None
    freeze_authority: str | None = None
    authority_checked: bool = False
    program: str | None = None
    decimals: int | None = None
    extensions: list[str] = Field(default_factory=list)
    top10_pct: float | None = None
    concentration_resolved: bool = False
    concentration_method: str | None = None
    pool_pct: float | None = None
    largest_accounts_pct: float | None = None
    holders: list[dict] = Field(default_factory=list)
    critical: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class GateCheck(BaseModel):
    stage: str
    label: str
    state: Literal["pass", "fail", "unknown", "skipped"]
    observed: float | str | bool | None = None
    rule: str


class Judgment(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    market: float = Field(ge=0, le=100)
    safety: float = Field(ge=0, le=100)
    momentum: float = Field(ge=0, le=100)
    concentration: float | None = Field(None, ge=0, le=100)
    social: float | None = Field(None, ge=0, le=100)
    smart_money: float | None = Field(None, ge=0, le=100)
    score: float = Field(ge=0, le=100)
    approved: bool
    risk: Literal["critical", "high", "moderate"]
    reasons: list[str]
    critical: list[str]
    unknown: list[str]
    observed: dict
    rules: list[str]
    ai_inferences: list[str] = Field(default_factory=list)
    strategy: str = "independent"
    checks: list[GateCheck] = Field(default_factory=list)


class WalletInput(BaseModel):
    address: str
    label: str = Field("", max_length=80)
    _validate = field_validator("address")(valid_address)


class EvidenceInput(BaseModel):
    address: str
    account: str = Field(max_length=100)
    url: str = Field(pattern=r"^https://", max_length=1000)
    note: str = Field(min_length=5, max_length=2000)
    linkage: Literal["unverified", "manual_evidence"] = "unverified"
    _validate = field_validator("address")(valid_address)
