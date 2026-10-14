from pydantic import BaseModel, Field

from ..schemas import valid_address
from .http import ProviderError, SafeHTTP
from .solana import USDC


class RouteQuote(BaseModel):
    input_mint: str
    output_mint: str
    in_amount: int = Field(gt=0)
    out_amount: int = Field(gt=0)
    at: float
    router: str
    price_impact_pct: float | None = None
    quote_only: bool = True


class Jupiter:
    def __init__(self, api_key: str = ""):
        headers = {"x-api-key": api_key} if api_key else {}
        self.http = SafeHTTP("Jupiter", "https://api.jup.ag/swap/v2", 20, headers)

    async def quote(self, input_mint: str, output_mint: str, amount: int) -> RouteQuote:
        valid_address(input_mint)
        valid_address(output_mint)
        if amount <= 0:
            raise ValueError("Quantidade em unidades mínimas deve ser positiva")
        data, stamp = await self.http.request(
            "/order",
            params={"inputMint": input_mint, "outputMint": output_mint, "amount": str(amount)},
            ttl=0,
            priority=1,
        )
        if data.get("errorCode") or int(data.get("outAmount", 0)) <= 0:
            raise ProviderError("Jupiter: nenhuma rota válida")
        if data.get("inputMint") != input_mint or data.get("outputMint") != output_mint:
            raise ProviderError("Jupiter: mints divergentes")
        if int(data.get("inAmount", 0)) != amount:
            raise ProviderError("Jupiter: quantidade divergente")
        return RouteQuote(
            input_mint=input_mint,
            output_mint=output_mint,
            in_amount=amount,
            out_amount=int(data["outAmount"]),
            at=stamp,
            router=data.get("router", "unknown"),
            price_impact_pct=float(data["priceImpactPct"])
            if data.get("priceImpactPct") is not None
            else None,
        )

    async def roundtrip(self, mint: str, usd: float) -> dict:
        buy = await self.quote(USDC, mint, int(usd * 1e6))
        sell = await self.quote(mint, USDC, buy.out_amount)
        return {
            "buy": buy.model_dump(),
            "sell": sell.model_dump(),
            "estimated_roundtrip_loss_pct": 100 * (1 - sell.out_amount / buy.in_amount),
            "warning": "Cotação não prova que uma transação será confirmada ou revendida.",
        }

    async def build(self, owner: str, input_mint: str, output_mint: str, amount: int, slippage_bps: int):
        data, _ = await self.http.request(
            "/build",
            params={
                "taker": valid_address(owner),
                "inputMint": valid_address(input_mint),
                "outputMint": valid_address(output_mint),
                "amount": str(amount),
                "slippageBps": str(slippage_bps),
                "maxAccounts": "20",
                "wrapAndUnwrapSol": "false",
                "transactionVersion": "0",
                "platformFeeBps": "0",
            },
            ttl=0,
            priority=0,
        )
        if data.get("errorCode"):
            raise ProviderError("Jupiter build indisponível")
        return data
