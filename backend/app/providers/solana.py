from .http import ProviderError, SafeHTTP

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class SolanaRPC:
    def __init__(self, url: str):
        self.http = SafeHTTP("Solana RPC", url, 30)

    async def call(self, method: str, params: list, ttl: int = 30, priority: int = 10):
        data, _ = await self.http.request(
            "",
            method="POST",
            body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            ttl=ttl,
            priority=priority,
        )
        if "result" not in data:
            raise ProviderError("Solana RPC: resposta incompleta")
        return data["result"]

    async def account(self, address: str):
        return (
            await self.call(
                "getAccountInfo", [address, {"encoding": "jsonParsed", "commitment": "confirmed"}]
            )
        )["value"]

    async def multiple(self, addresses: list[str]):
        return (
            await self.call(
                "getMultipleAccounts", [addresses, {"encoding": "jsonParsed", "commitment": "confirmed"}]
            )
        )["value"]

    async def largest(self, address: str):
        return (await self.call("getTokenLargestAccounts", [address, {"commitment": "confirmed"}], ttl=90))[
            "value"
        ]
