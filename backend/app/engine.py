import asyncio
import logging

from sqlalchemy import select

from .agents.bench import Benches
from .agents.discovery import DiscoveryAgent
from .agents.filter import arithmetic_filter, trades_filter
from .agents.judge import explain_with_ollama, judge
from .agents.pick import pick
from .agents.security import SecurityAgent
from .agents.smart_money import SmartMoneyAgent
from .agents.social import SocialAgent
from .config import Settings
from .db import Account, Database, Decision, Equity, Evaluation, Event, Observation, Pool, Position, Token
from .execution.live import LiveExecution
from .execution.simulation import ShadowExecution
from .providers.http import SafeHTTP
from .providers.jupiter import Jupiter
from .providers.market import DexScreener, GeckoTerminal
from .providers.solana import SolanaRPC
from .schemas import Market, SecurityResult, now
from .thresholds import Thresholds

logger = logging.getLogger("alpha")


class Engine:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings
        self.gecko, self.dex, self.rpc = GeckoTerminal(), DexScreener(), SolanaRPC(settings.rpc_url)
        self.jupiter = Jupiter(settings.jupiter_api_key.get_secret_value())
        self.discovery = DiscoveryAgent([self.gecko, self.dex])
        self.security, self.smart, self.social = (
            SecurityAgent(self.rpc),
            SmartMoneyAgent(db, self.rpc),
            SocialAgent(db),
        )
        self.benches = Benches(db)
        self.execution = ShadowExecution(db)
        self.live = LiveExecution(self.rpc, self.jupiter, settings.live_enabled)
        self.scan_lock, self.risk_lock = asyncio.Lock(), asyncio.Lock()
        self.tasks, self.last_scan = [], db.config("runtime", {}).get("last_scan")
        self.status = "initializing"
        self.telegram = (
            SafeHTTP("Telegram", "https://api.telegram.org", 15)
            if settings.telegram_bot_token.get_secret_value() and settings.telegram_chat_id
            else None
        )

    @property
    def thresholds(self):
        return Thresholds(**self.db.config("thresholds", Thresholds().model_dump()))

    def controls(self):
        return self.db.config("controls", {"discovery_paused": False})

    def record_market(self, m: Market):
        key = "solana:" + m.address
        with self.db.session() as s:
            old = s.get(Token, key)
            if old and old.updated > m.observed_at:
                return
            old_data = dict(old.data) if old else {}
            s.merge(Token(key=key, data=m.model_dump(), updated=m.observed_at))
            s.merge(Pool(key="solana:" + m.pool, token_key=key, data=m.model_dump()))
            # Same fetched observation is not a second independent price point.
            latest = s.scalar(
                select(Observation)
                .where(Observation.token_key == key)
                .order_by(Observation.id.desc())
                .limit(1)
            )
            if latest is None or latest.at < m.observed_at:
                s.add(Observation(token_key=key, at=m.observed_at, data=m.model_dump()))
            if old and old_data.get("volume_5m") and m.volume_5m and m.volume_5m > old_data["volume_5m"] * 3:
                s.add(
                    Event(
                        kind="volume_spike",
                        level="warning",
                        message=f"Volume 5m acelerou: {m.symbol}",
                        data={"token_key": key},
                    )
                )

    async def evaluate(self, m: Market):
        t = self.thresholds
        key = "solana:" + m.address
        preliminary = arithmetic_filter(m, t) + trades_filter(m)
        sec = SecurityResult(unknown=["On-chain não consultado: bloqueio no filtro inicial"])
        if not preliminary:
            try:
                async with asyncio.timeout(30):
                    sec = await self.security.analyze(m.address)
            except TimeoutError:
                sec = SecurityResult(unknown=["Prazo on-chain excedido; análise não aprovada"])
        judgment = judge(m, sec, t, self.social.analyze(key), self.smart.signals(m.address))
        if self.settings.ollama_model and not preliminary:
            judgment.ai_inferences = await explain_with_ollama(
                self.settings.ollama_url, self.settings.ollama_model, judgment
            )
        with self.db.session() as s:
            row = Evaluation(token_key=key, data=judgment.model_dump(), security=sec.model_dump())
            s.add(row)
            s.flush()
            evaluation_id = row.id
        if not judgment.approved:
            self.benches.reject(key, judgment.reasons, t.rejection_cooldown_seconds)
        else:
            self.db.event(
                "approved",
                f"Candidato shadow aprovado: {m.symbol}",
                data={"token_key": key, "score": judgment.score},
            )
        if sec.critical:
            self.db.event("security", f"Risco crítico: {m.symbol}", "critical", {"risks": sec.critical})
        return judgment, evaluation_id

    async def scan(self):
        if self.scan_lock.locked():
            return {"status": "already_running"}
        async with self.scan_lock:
            if self.controls()["discovery_paused"]:
                self.status = "paused"
                return {"status": "paused"}
            self.status = "scanning"
            try:
                markets, errors = await self.discovery.discover()
                for error in errors:
                    self.db.event("provider", error, "warning")
                # Revisit older discoveries; latest-pool feeds alone would never let them mature.
                with self.db.session() as session:
                    retained = session.scalars(select(Token).order_by(Token.updated).limit(500)).all()
                seen = {m.address for m in markets}
                revisit = [
                    row
                    for row in retained
                    if row.data["address"] not in seen
                    and row.data.get("created_at") is not None
                    and 0 <= now() - row.data["created_at"] <= self.thresholds.max_age_hours * 3600
                ][:20]
                if revisit:
                    try:
                        refreshed = await self.dex.tokens([row.data["address"] for row in revisit])
                        best = {}
                        for item in refreshed:
                            if item.address not in best or (item.liquidity or 0) > (
                                best[item.address].liquidity or 0
                            ):
                                best[item.address] = item
                        markets.extend(best.values())
                        if not refreshed:
                            raise ValueError("No refreshed pairs")
                    except Exception:
                        for row in revisit[:2]:
                            try:
                                refreshed = await self.gecko.refresh(row.data["pool"])
                                if refreshed.address == row.data["address"]:
                                    markets.append(refreshed)
                            except Exception:
                                pass  # retained observation remains visibly stale; never refreshed synthetically
                # Copy Intelligence is fed exclusively by observed public transactions.
                copy_mints = self.db.config("copy_candidates", {}).get("mints", [])
                for mint in copy_mints[:3]:
                    try:
                        pairs = await self.gecko.token_pools(mint)
                        if pairs:
                            m = max(pairs, key=lambda x: x.liquidity or 0)
                            m.discovered_by = "copy_intelligence"
                            markets = [x for x in markets if x.address != mint] + [m]
                    except Exception:
                        self.db.event(
                            "copy_discovery", "Falha ao resolver pool de carteira monitorada", "warning"
                        )
                self.db.set_config("copy_candidates", {"mints": []})
                evaluated, ids = [], {}
                for m in markets:
                    self.record_market(m)
                    if self.benches.blocked("solana:" + m.address):
                        continue
                    j, eid = await self.evaluate(m)
                    evaluated.append((m, j))
                    ids[m.address] = eid
                selection = pick(evaluated)
                if (
                    selection["selected"]
                    and self.thresholds.auto_shadow
                    and not self.controls()["discovery_paused"]
                ):
                    m, j = next(x for x in evaluated if "solana:" + x[0].address == selection["selected"])
                    # The first stages are checked again after potentially slow RPC calls.
                    if arithmetic_filter(m, self.thresholds):
                        selection["execution"] = {
                            "status": "rejected",
                            "reason": "Dados expiraram durante avaliação",
                        }
                    else:
                        selection["execution"] = self.execution.open(
                            m, j, self.thresholds, f"evaluation:{ids[m.address]}"
                        )
                with self.db.session() as s:
                    s.add(
                        Decision(
                            data=selection | {"tokens_received": len(markets), "evaluated": len(evaluated)}
                        )
                    )
                self.last_scan = now()
                self.db.set_config("runtime", {"last_scan": self.last_scan})
                self.status = "running" if markets else "degraded"
                return selection
            except Exception as exc:
                self.status = "degraded"
                self.db.event("scan_error", "Falha de ciclo: " + type(exc).__name__, "critical")
                logger.error("scan_failed:" + type(exc).__name__)
                return {"status": "error"}

    async def risk_tick(self, forced_reason=None):
        async with self.risk_lock:
            with self.db.session() as s:
                p = s.scalar(select(Position).where(Position.active_slot == 1))
            if not p:
                with self.db.session() as s:
                    latest = s.scalar(select(Equity).order_by(Equity.id.desc()).limit(1))
                    account = s.get(Account, 1)
                    if account and (latest is None or now() - latest.at >= 60):
                        s.add(Equity(value=account.cash, data={"mode": "shadow", "open_positions": 0}))
                return None
            try:
                provider = self.gecko if p.data["source"] == "geckoterminal" else self.dex
                m = await provider.refresh(p.data["pool"])
                if "solana:" + m.address != p.token_key:
                    raise ValueError("Pool retornou outro mint")
                self.record_market(m)
                return self.execution.mark_and_manage(m, self.thresholds, forced_reason)
            except Exception as exc:
                self.db.event(
                    "risk_price",
                    "Falha de preço: posição continua reservada; " + type(exc).__name__,
                    "critical",
                )
                with self.db.session() as s:
                    current = s.get(Position, p.id)
                    if current and current.active_slot == 1:
                        current.status = "exit_pending"
                        current.data = current.data | {
                            "pending_reason": forced_reason or "PERSISTENT_PRICE_FAILURE"
                        }
                return {"status": "exit_pending"}

    async def wallet_tick(self):
        mints = await self.smart.poll()
        previous = self.db.config("copy_candidates", {}).get("mints", [])
        self.db.set_config("copy_candidates", {"mints": list(set(previous) | mints)[:20]})
        for mint in mints:
            if self.smart.signals(mint)["simultaneous_10m"]:
                self.db.event(
                    "smart_cluster",
                    "Compras prováveis em múltiplas carteiras em 10 min",
                    "warning",
                    {"mint": mint},
                )

    async def telegram_tick(self):
        if not self.telegram:
            return
        last = self.db.config("telegram_cursor", {}).get("id", 0)
        with self.db.session() as s:
            rows = s.scalars(select(Event).where(Event.id > last).order_by(Event.id).limit(5)).all()
        for row in rows:
            if row.kind in ("approved", "position_open", "position_close", "exit_pending", "smart_cluster"):
                await self.telegram.request(
                    "/bot" + self.settings.telegram_bot_token.get_secret_value() + "/sendMessage",
                    method="POST",
                    body={
                        "chat_id": self.settings.telegram_chat_id,
                        "text": "Alpha Desk [SHADOW] " + row.message,
                    },
                )
            self.db.set_config("telegram_cursor", {"id": row.id})

    async def _loop(self, name, fn, interval):
        while True:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(name + ":" + type(exc).__name__)
                self.db.event("task_error", name + ": " + type(exc).__name__, "warning")
            await asyncio.sleep(interval)

    async def start(self):
        self.execution.initialize()
        self.execution.reconcile()
        self.status = "running"
        if self.settings.enable_scheduler:
            for name, fn, interval in [
                ("discovery", self.scan, self.settings.scan_interval),
                ("risk", self.risk_tick, self.settings.risk_interval),
                ("wallets", self.wallet_tick, self.settings.wallet_interval),
                ("telegram", self.telegram_tick, 30),
            ]:
                self.tasks.append(asyncio.create_task(self._loop(name, fn, interval)))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for provider in (self.gecko, self.dex, self.rpc, self.jupiter):
            await provider.http.close()
        if self.telegram:
            await self.telegram.close()

    def health(self):
        return [p.http.health for p in (self.gecko, self.dex, self.rpc, self.jupiter)]
