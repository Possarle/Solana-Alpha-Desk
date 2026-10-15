"""pump.fun bonding curve: address, decoding and constant-product quotes.

Source: pump-fun/pump-public-docs (PUMP_PROGRAM_README): program id, PDA seeds
["bonding-curve", mint], BondingCurve field order (virtual_token_reserves,
virtual_sol_reserves, real_token_reserves, real_sol_reserves, token_total_supply:
u64 each; complete: bool) and fee_basis_points = 100. Anything that does not match
these expectations is rejected as "unrecognized", never guessed.
"""

import base64
import struct
from dataclasses import dataclass

from solders.pubkey import Pubkey

from ..schemas import valid_address

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TOKEN_DECIMALS = 6
EXPECTED_SUPPLY_RAW = 1_000_000_000 * 10**TOKEN_DECIMALS
LAMPORTS = 10**9
_LAYOUT = struct.Struct("<QQQQQ?")  # after the 8-byte account discriminator


class CurveError(ValueError):
    pass


def curve_address(mint: str) -> str:
    pda, _ = Pubkey.find_program_address(
        [b"bonding-curve", bytes(Pubkey.from_string(mint))], Pubkey.from_string(PUMP_PROGRAM)
    )
    return str(pda)


@dataclass(frozen=True)
class Curve:
    virtual_token: int  # raw token units (6 decimals)
    virtual_sol: int  # lamports
    real_token: int
    real_sol: int
    supply: int
    complete: bool

    @property
    def price_sol(self) -> float:
        """Spot price in SOL per whole token."""
        return (self.virtual_sol / LAMPORTS) / (self.virtual_token / 10**TOKEN_DECIMALS)

    @property
    def real_sol_ui(self) -> float:
        return self.real_sol / LAMPORTS

    def buy(self, sol_in: float) -> float:
        """Whole tokens received for sol_in SOL already net of fees (constant product)."""
        lamports = int(sol_in * LAMPORTS)
        if lamports <= 0:
            return 0.0
        out = self.virtual_token - (self.virtual_sol * self.virtual_token) // (self.virtual_sol + lamports)
        return min(out, self.real_token) / 10**TOKEN_DECIMALS

    def sell(self, tokens: float) -> float:
        """SOL received (before fees) for selling whole tokens back into the curve."""
        raw = int(tokens * 10**TOKEN_DECIMALS)
        if raw <= 0:
            return 0.0
        out = self.virtual_sol - (self.virtual_sol * self.virtual_token) // (self.virtual_token + raw)
        return min(out, self.real_sol) / LAMPORTS


def decode_curve(account: dict | None) -> Curve:
    """Decode a getAccountInfo/getMultipleAccounts value (base64 data) owned by pump.fun."""
    if not account:
        raise CurveError("Conta da curva inexistente")
    if account.get("owner") != PUMP_PROGRAM:
        raise CurveError("Curva não pertence ao programa pump.fun")
    data = account.get("data")
    if not isinstance(data, list) or len(data) < 2 or data[1] != "base64":
        raise CurveError("Dados da curva em formato inesperado")
    raw = base64.b64decode(data[0])
    if len(raw) < 8 + _LAYOUT.size:
        raise CurveError("Curva menor que o layout documentado")
    vt, vs, rt, rs, supply, complete = _LAYOUT.unpack_from(raw, 8)
    if supply != EXPECTED_SUPPLY_RAW or vt <= 0 or vs <= 0 or rt > vt or raw[8 + 40] not in (0, 1):
        raise CurveError("Curva não reconhecida: valores fora do padrão documentado")
    return Curve(vt, vs, rt, rs, supply, complete)


def encode_curve(curve: Curve, extra: bytes = b"") -> dict:
    """Test helper: an account value exactly as the RPC returns it."""
    raw = b"\x17" * 8 + _LAYOUT.pack(
        curve.virtual_token, curve.virtual_sol, curve.real_token, curve.real_sol, curve.supply, curve.complete
    )
    return {"owner": PUMP_PROGRAM, "data": [base64.b64encode(raw + extra).decode(), "base64"]}


@dataclass(frozen=True)
class Launch:
    mint: str
    symbol: str
    name: str
    creator: str | None
    dev_buy_tokens: float | None  # whole tokens bought by the creator in the create tx
    dev_buy_sol: float | None
    signature: str | None
    seen_at: float

    @property
    def dev_buy_pct(self) -> float | None:
        if self.dev_buy_tokens is None:
            return None
        return self.dev_buy_tokens / (EXPECTED_SUPPLY_RAW / 10**TOKEN_DECIMALS) * 100


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse_launch(message: dict, seen_at: float) -> Launch | None:
    """PumpPortal subscribeNewToken event -> Launch. Non-create messages return None."""
    if not isinstance(message, dict) or message.get("txType") != "create" or not message.get("mint"):
        return None
    try:
        mint = valid_address(str(message["mint"]))
    except ValueError:
        return None
    creator = message.get("traderPublicKey")
    try:
        creator = valid_address(str(creator)) if creator else None
    except ValueError:
        creator = None
    return Launch(
        mint=mint,
        symbol=str(message.get("symbol") or "?")[:20],
        name=str(message.get("name") or "")[:60],
        creator=creator,
        dev_buy_tokens=_num(message.get("initialBuy")),
        dev_buy_sol=_num(message.get("solAmount")),
        signature=str(message.get("signature") or "")[:120] or None,
        seen_at=seen_at,
    )
