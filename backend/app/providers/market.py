from datetime import datetime

from ..schemas import Market
from .http import ProviderError, SafeHTTP


def number(value):
    return float(value) if value is not None else None


def gecko_market(item: dict, included: list, stamp: float) -> Market:
    a = item["attributes"]
    base_id = item["relationships"]["base_token"]["data"]["id"]
    metadata = next((x["attributes"] for x in included if x["id"] == base_id), {})
    tx = a.get("transactions", {})
    address = metadata.get("address") or base_id.removeprefix("solana_")
    return Market(
        address=address,
        pool=a["address"],
        source="geckoterminal",
        observed_at=stamp,
        symbol=metadata.get("symbol") or a.get("name", "?").split(" / ")[0],
        name=metadata.get("name", ""),
        price=number(a.get("base_token_price_usd")),
        market_cap=number(a.get("market_cap_usd")),
        fdv=number(a.get("fdv_usd")),
        liquidity=number(a.get("reserve_in_usd")),
        volume_24h=number(a.get("volume_usd", {}).get("h24")),
        volume_5m=number(a.get("volume_usd", {}).get("m5")),
        buys_24h=tx.get("h24", {}).get("buys"),
        sells_24h=tx.get("h24", {}).get("sells"),
        buys_5m=tx.get("m5", {}).get("buys"),
        sells_5m=tx.get("m5", {}).get("sells"),
        buys_1h=tx.get("h1", {}).get("buys"),
        change_5m=number(a.get("price_change_percentage", {}).get("m5")),
        change_24h=number(a.get("price_change_percentage", {}).get("h24")),
        created_at=datetime.fromisoformat(a["pool_created_at"].replace("Z", "+00:00")).timestamp()
        if a.get("pool_created_at")
        else None,
    )


def dex_market(a: dict, stamp: float) -> Market:
    tx = a.get("txns", {})
    return Market(
        address=a["baseToken"]["address"],
        pool=a["pairAddress"],
        source="dexscreener",
        observed_at=stamp,
        symbol=a["baseToken"].get("symbol", "?"),
        name=a["baseToken"].get("name", ""),
        price=number(a.get("priceUsd")),
        market_cap=number(a.get("marketCap")),
        fdv=number(a.get("fdv")),
        liquidity=number((a.get("liquidity") or {}).get("usd")),
        volume_24h=number(a.get("volume", {}).get("h24")),
        volume_5m=number(a.get("volume", {}).get("m5")),
        buys_24h=tx.get("h24", {}).get("buys"),
        sells_24h=tx.get("h24", {}).get("sells"),
        buys_5m=tx.get("m5", {}).get("buys"),
        sells_5m=tx.get("m5", {}).get("sells"),
        buys_1h=tx.get("h1", {}).get("buys"),
        change_5m=number((a.get("priceChange") or {}).get("m5")),
        change_24h=number((a.get("priceChange") or {}).get("h24")),
        created_at=a["pairCreatedAt"] / 1000 if a.get("pairCreatedAt") else None,
        links=(a.get("info") or {}).get("socials", []),
    )


class GeckoTerminal:
    def __init__(self):
        self.http = SafeHTTP(
            "GeckoTerminal",
            "https://api.geckoterminal.com/api/v2",
            8,
            {"Accept": "application/json;version=20230203"},
        )

    async def discover(self):
        data, stamp = await self.http.request(
            "/networks/solana/new_pools", params={"include": "base_token", "page": 1}, ttl=65
        )
        return self._parse(data, stamp)

    def _parse(self, data, stamp):
        items = data.get("data", [])
        items = items if isinstance(items, list) else [items]
        markets = []
        for item in items:
            try:
                markets.append(gecko_market(item, data.get("included", []), stamp))
            except (ValueError, KeyError, TypeError):
                self.http.health["errors"] += 1
        return markets

    async def refresh(self, pool: str):
        data, stamp = await self.http.request(
            f"/networks/solana/pools/{pool}", params={"include": "base_token"}, ttl=10, priority=0
        )
        rows = self._parse(data, stamp)
        if not rows:
            raise ProviderError("GeckoTerminal: pool sem preço válido")
        return rows[0]

    async def token_pools(self, address: str):
        data, stamp = await self.http.request(
            f"/networks/solana/tokens/{address}/pools", params={"include": "base_token"}, ttl=65
        )
        return [m for m in self._parse(data, stamp) if m.address == address]


class DexScreener:
    def __init__(self):
        # Single shared budget stays below the lowest documented endpoint limit.
        self.http = SafeHTTP("DexScreener", "https://api.dexscreener.com", 30)

    async def discover(self):
        profiles, _ = await self.http.request("/token-profiles/latest/v1", ttl=65)
        if isinstance(profiles, dict):
            profiles = [profiles]
        addresses = [p["tokenAddress"] for p in profiles if p.get("chainId") == "solana"][:30]
        if not addresses:
            return []
        data, stamp = await self.http.request("/tokens/v1/solana/" + ",".join(addresses), ttl=45)
        markets = []
        for row in data:
            try:
                if row.get("chainId") == "solana":
                    markets.append(dex_market(row, stamp))
            except (ValueError, KeyError, TypeError):
                self.http.health["errors"] += 1
        return markets

    async def refresh(self, pool: str):
        data, stamp = await self.http.request(f"/latest/dex/pairs/solana/{pool}", ttl=10, priority=0)
        if not data.get("pairs"):
            raise ProviderError("DexScreener: pool não encontrado")
        return dex_market(data["pairs"][0], stamp)

    async def tokens(self, addresses: list[str]):
        if not addresses:
            return []
        data, stamp = await self.http.request("/tokens/v1/solana/" + ",".join(addresses[:30]), ttl=45)
        markets = []
        for row in data:
            try:
                if row.get("chainId") == "solana" and row.get("baseToken", {}).get("address") in addresses:
                    markets.append(dex_market(row, stamp))
            except (ValueError, KeyError, TypeError):
                self.http.health["errors"] += 1
        return markets
