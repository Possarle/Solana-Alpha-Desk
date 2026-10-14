import asyncio

from ..schemas import Market


class DiscoveryAgent:
    def __init__(self, providers):
        self.providers = providers
        self.seen: list[Market] = []  # every pair observed, including less liquid pools

    async def discover(self) -> tuple[list[Market], list[str]]:
        results = await asyncio.gather(*(p.discover() for p in self.providers), return_exceptions=True)
        best, errors = {}, []
        self.seen = []
        for provider, result in zip(self.providers, results, strict=True):
            if isinstance(result, BaseException):
                errors.append(f"{provider.http.name}: {type(result).__name__}")
                continue
            for m in result:
                self.seen.append(m)
                # Preserve one internally consistent observation from the most liquid pool.
                old = best.get(m.address)
                if old is None or (m.liquidity or 0) > (old.liquidity or 0):
                    best[m.address] = m
        return list(best.values()), errors
