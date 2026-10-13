"""Preparation only: this distribution deliberately contains no signer or broadcast path."""

from typing import Protocol

from ..schemas import valid_address


class Signer(Protocol):
    def sign(self, transaction: bytes) -> bytes: ...


class LiveExecution:
    def __init__(self, rpc, jupiter, enabled: bool = False):
        self.rpc, self.jupiter, self.enabled = rpc, jupiter, enabled

    async def prepare(self, mint: str, budget: float):
        return await self.jupiter.roundtrip(valid_address(mint), budget)

    async def reconcile(self, owner: str, mint: str, expected_raw: int):
        result = await self.rpc.call(
            "getTokenAccountsByOwner",
            [
                valid_address(owner),
                {"mint": valid_address(mint)},
                {"encoding": "jsonParsed", "commitment": "finalized"},
            ],
            ttl=0,
            priority=0,
        )
        actual = sum(
            int(x["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) for x in result["value"]
        )
        return {"matches": actual == expected_raw, "actual_raw": actual, "expected_raw": expected_raw}

    async def confirmed(self, signature: str):
        result = await self.rpc.call(
            "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}], ttl=0, priority=0
        )
        value = result["value"][0]
        return bool(value and value.get("err") is None and value.get("confirmationStatus") == "finalized")

    async def submit(self, *_args, **_kwargs):
        raise PermissionError(
            "LIVE bloqueado nesta versão: assinatura e broadcast não implementados. Alterar LIVE_ENABLED não libera ordens."
        )
