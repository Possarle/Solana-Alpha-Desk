"""PumpPortal real-time feed of new pump.fun tokens (free subscribeNewToken).

PumpPortal asks clients to keep ONE websocket connection and send subscriptions
over it; repeated reconnect storms can be timed out. This client keeps a single
connection, reconnects with exponential backoff and never opens parallel sockets.
Docs: https://pumpportal.fun/data-api/real-time
"""

import asyncio
import json
import logging
from collections.abc import Callable

from ..schemas import now
from .pumpfun import Launch, parse_launch

logger = logging.getLogger("alpha")
URL = "wss://pumpportal.fun/api/data"


class PumpPortalFeed:
    def __init__(self, on_launch: Callable[[Launch], None], connect=None, url: str = URL):
        self.on_launch, self.url = on_launch, url
        self._connect = connect
        self.health = {
            "name": "PumpPortal",
            "state": "idle",
            "requests": 0,
            "errors": 0,
            "rpm_budget": 0,
            "last_ok": None,
            "detail": "Aguardando conexão em tempo real",
        }

    def _connector(self):
        if self._connect is None:
            from websockets.asyncio.client import connect

            self._connect = connect
        return self._connect

    async def session(self) -> int:
        """One connection lifetime; returns how many launches were delivered."""
        delivered = 0
        async with self._connector()(self.url, open_timeout=15, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({"method": "subscribeNewToken"}))
            self.health |= {"state": "active", "detail": "Conectado; recebendo lançamentos do pump.fun"}
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    self.health["errors"] += 1
                    continue
                launch = parse_launch(message, now())
                if launch is None:
                    continue
                self.health["requests"] += 1
                self.health["last_ok"] = now()
                delivered += 1
                try:
                    self.on_launch(launch)
                except Exception as exc:  # a bad handler must not drop the shared connection
                    logger.error("launch_handler:" + type(exc).__name__)
        return delivered

    async def run(self):
        backoff = 2
        while True:
            try:
                delivered = await self.session()
                self.health |= {"state": "limited", "detail": "Conexão encerrada pelo servidor; reconectando"}
                if delivered:
                    backoff = 2
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.health["errors"] += 1
                self.health |= {"state": "unavailable", "detail": "Falha de conexão: " + type(exc).__name__}
                logger.warning("pumpportal:" + type(exc).__name__)
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)
