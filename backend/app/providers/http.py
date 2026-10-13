"""Conservative, per-provider priority queue with bounded cache and retry budget."""

import asyncio
import copy
import itertools
import json
import random
import time

import httpx


class ProviderError(Exception):
    pass


class SafeHTTP:
    def __init__(self, name: str, base: str, rpm: int, headers: dict | None = None, transport=None):
        self.name, self.interval = name, 60 / rpm
        self.client = httpx.AsyncClient(base_url=base, headers=headers, timeout=12, transport=transport)
        self.queue = asyncio.PriorityQueue(maxsize=100)
        self.counter = itertools.count()
        self.worker = None
        self.cache: dict = {}
        self.next_request = 0.0
        self.failures = 0
        self.open_until = 0.0
        self.health = {
            "name": name,
            "state": "idle",
            "requests": 0,
            "errors": 0,
            "rpm_budget": rpm,
            "last_ok": None,
            "detail": "Aguardando consulta",
        }

    async def _work(self):
        while True:
            await asyncio.sleep(max(0, self.next_request - time.monotonic()))
            item = await self.queue.get()
            _, _, future, method, path, kwargs = item
            if future.cancelled():
                self.queue.task_done()
                continue
            try:
                self.next_request = time.monotonic() + self.interval
                self.health["requests"] += 1
                response = await self.client.request(method, path, **kwargs)
                if response.status_code == 429:
                    # Respect provider backoff across every caller, not just the retrying request.
                    delay = 60.0
                    try:
                        if not response.headers.get("retry-after") and not response.headers.get(
                            "x-ratelimit-reset"
                        ):
                            raise ValueError("no backoff header")
                        delay = max(
                            float(response.headers.get("retry-after", 0)),
                            float(response.headers.get("x-ratelimit-reset", 0)) - time.time(),
                            1,
                        )
                    except ValueError:
                        pass
                    self.next_request = max(self.next_request, time.monotonic() + delay)
                if not future.done():
                    future.set_result(response)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            finally:
                self.queue.task_done()

    async def request(
        self, path: str, *, method: str = "GET", params=None, body=None, ttl: int = 0, priority: int = 10
    ):
        cache_key = json.dumps([path, method, params, body], sort_keys=True)
        cached = self.cache.get(cache_key)
        if cached and time.time() - cached[1] < ttl:
            return copy.deepcopy(cached)
        if time.monotonic() < self.open_until:
            raise ProviderError(f"{self.name}: circuit breaker aberto")
        if self.worker is None:
            self.worker = asyncio.create_task(self._work())
        for attempt in range(3):
            try:
                future = asyncio.get_running_loop().create_future()
                kwargs = {"params": params}
                if body is not None:
                    kwargs["json"] = body
                await asyncio.wait_for(
                    self.queue.put((priority, next(self.counter), future, method, path, kwargs)), 5
                )
                response = await asyncio.wait_for(future, 35)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and data.get("error"):
                    raise ProviderError(f"{self.name}: erro JSON-RPC/API")
                self.failures = 0
                stamp = time.time()
                self.health.update(state="active", detail="Última consulta concluída", last_ok=stamp)
                result = (data, stamp)
                if ttl:
                    if len(self.cache) >= 500:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[cache_key] = result
                return copy.deepcopy(result)
            except (httpx.HTTPError, ValueError, ProviderError, TimeoutError) as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                self.health["errors"] += 1
                self.health.update(
                    state="limited" if status == 429 else "unavailable",
                    detail=f"HTTP {status}" if status else type(exc).__name__,
                )
                if status and status not in (408, 429) and status < 500:
                    break
                if attempt < 2:
                    await asyncio.sleep((2**attempt) + random.random() / 4)
        self.failures += 1
        if self.failures >= 3:
            self.open_until = time.monotonic() + 60
        raise ProviderError(f"{self.name}: {self.health['detail']}")

    async def close(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        await self.client.aclose()
