import base64

from solders.message import to_bytes_versioned
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from ..providers.solana import SYSTEM_PROGRAM, TOKEN_PROGRAM
from .transaction_guard import associated

MAINNET_GENESIS = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"


def token_account(account, owner, mint):
    if account is None:
        return {"amount": 0, "exists": False}
    raw = base64.b64decode(account["data"][0], validate=True)
    if (
        account["owner"] != TOKEN_PROGRAM
        or len(raw) != 165
        or raw[:32] != bytes(Pubkey.from_string(mint))
        or raw[32:64] != bytes(Pubkey.from_string(owner))
    ):
        raise ValueError("Conta SPL não corresponde à carteira/mint")
    if raw[108] != 1 or int.from_bytes(raw[72:76], "little") or int.from_bytes(raw[129:133], "little"):
        raise ValueError("Conta congelada, delegada ou com close authority")
    return {"amount": int.from_bytes(raw[64:72], "little"), "exists": True}


def decode_balances(values, owner, input_mint, output_mint):
    native = values[0]
    if native is None or native["owner"] != SYSTEM_PROGRAM or native.get("executable"):
        raise ValueError("Carteira SOL inválida")
    return {
        "sol": int(native["lamports"]),
        "input": token_account(values[1], owner, input_mint)["amount"],
        "output": token_account(values[2], owner, output_mint)["amount"],
    }


class LiveChain:
    def __init__(self, rpc):
        self.rpc = rpc

    async def call(self, method, params):
        return await self.rpc.call(method, params, ttl=0, priority=0)

    async def balances(self, owner, input_mint, output_mint, min_slot=0):
        result = await self.call(
            "getMultipleAccounts",
            [
                [owner, associated(owner, input_mint), associated(owner, output_mint)],
                {"encoding": "base64", "commitment": "finalized", "minContextSlot": min_slot},
            ],
        )
        return decode_balances(result["value"], owner, input_mint, output_mint)

    async def verify_accounts(self, build, owner, input_mint, output_mint):
        keys = list(
            dict.fromkeys(a["pubkey"] for a in build["swapInstruction"]["accounts"] if a["isWritable"])
        )
        allowed = {associated(owner, input_mint), associated(owner, output_mint)}
        if owner in keys:
            raise ValueError("Swap tenta tornar carteira SOL gravável")
        if not keys:
            raise ValueError("Swap sem contas")
        result = await self.call(
            "getMultipleAccounts", [keys, {"encoding": "base64", "commitment": "finalized"}]
        )
        for key, account in zip(keys, result["value"], strict=True):
            if account and account["owner"] == TOKEN_PROGRAM:
                raw = base64.b64decode(account["data"][0], validate=True)
                if len(raw) == 165 and raw[32:64] == bytes(Pubkey.from_string(owner)) and key not in allowed:
                    raise ValueError("Swap inclui outra conta de tokens da carteira")

    async def blockhash(self):
        return (await self.call("getLatestBlockhash", [{"commitment": "finalized"}]))["value"]

    async def simulate(self, wire, owner, input_mint, output_mint, before, amount, minimum, max_fee):
        tx = VersionedTransaction.from_bytes(base64.b64decode(wire))
        fee = (
            await self.call(
                "getFeeForMessage",
                [base64.b64encode(to_bytes_versioned(tx.message)).decode(), {"commitment": "confirmed"}],
            )
        )["value"]
        if fee is None or not 0 < fee <= max_fee:
            raise ValueError("Taxa de rede ausente ou excessiva")
        result = await self.call(
            "simulateTransaction",
            [
                wire,
                {
                    "encoding": "base64",
                    "sigVerify": False,
                    "commitment": "confirmed",
                    "accounts": {
                        "encoding": "base64",
                        "addresses": [owner, associated(owner, input_mint), associated(owner, output_mint)],
                    },
                },
            ],
        )
        value = result["value"]
        if value.get("err") or not value.get("accounts"):
            raise ValueError("Simulação on-chain falhou")
        after = decode_balances(value["accounts"], owner, input_mint, output_mint)
        if before["input"] - after["input"] != amount or after["output"] - before["output"] < minimum:
            raise ValueError("Efeitos da simulação divergem da revisão")
        sol_cost = before["sol"] - after["sol"]
        if sol_cost < fee or sol_cost > fee + 2_100_000:
            raise ValueError("Débito SOL não autorizado")
        if after["sol"] < 1_000_000:
            raise ValueError("Reserva SOL insuficiente")
        return {
            "fee_lamports": fee,
            "rent_lamports": max(0, sol_cost - fee),
            "units": value.get("unitsConsumed"),
        }

    async def send_once(self, signed):
        # No application retry. Ambiguity is reconciled by the already-persisted signature.
        response = await self.rpc.http.client.post(
            "",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    signed,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,
                        "preflightCommitment": "confirmed",
                        "maxRetries": 0,
                    },
                ],
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("error") or not data.get("result"):
            raise ValueError("Envio RPC não confirmado")
        return data["result"]

    async def status(self, signature):
        return (await self.call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}]))[
            "value"
        ][0]

    async def transaction(self, signature):
        return await self.call(
            "getTransaction",
            [
                signature,
                {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0},
            ],
        )
