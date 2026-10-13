"""Read-only network diagnostic. Run from project root with .venv/bin/python scripts/probe_sources.py."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.config import settings
from app.providers.market import GeckoTerminal, DexScreener
from app.providers.solana import SolanaRPC, SOL
from app.providers.jupiter import Jupiter


async def main():
    gecko, dex = GeckoTerminal(), DexScreener()
    rpc = SolanaRPC(settings.rpc_url)
    jupiter = Jupiter(settings.jupiter_api_key.get_secret_value())
    for name, query in [('GeckoTerminal', gecko.discover), ('DexScreener', dex.discover),
                        ('Solana RPC', lambda: rpc.account(SOL)),
                        ('Jupiter quote-only', lambda: jupiter.roundtrip(SOL, 10))]:
        try:
            async with asyncio.timeout(50):
                result = await query()
            print(name, 'OK', f'{len(result)} pares' if isinstance(result, list) else 'resposta válida')
        except Exception as exc:
            print(name, 'INDISPONÍVEL', type(exc).__name__)
    for source in (gecko, dex, rpc, jupiter):
        await source.http.close()


if __name__ == '__main__':
    asyncio.run(main())
