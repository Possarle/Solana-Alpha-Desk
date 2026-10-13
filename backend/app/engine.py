import asyncio
import logging

from sqlalchemy import select

from .agents.bench import Benches
from .agents.discovery import DiscoveryAgent
from .agents.filter import arithmetic_filter, trades_filter
from .agents.judge import explain_with_ollama, judge
from .agents.pick import pick
from .agents.position_size import position_size
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
from .telemetry import DeskTelemetry
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
        self.desk = DeskTelemetry()
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
        self.desk.emit(
            "scan",
            "rejected" if preliminary else "completed",
            "; ".join(preliminary) if preliminary else "Filtros de mercado atendidos",
            key,
        )
        self.desk.emit(
            "vet",
            "skipped" if preliminary else "running",
            "Bloqueado no filtro inicial" if preliminary else "Consultando permissões e maiores contas",
            key,
        )
        sec = SecurityResult(unknown=["On-chain não consultado: bloqueio no filtro inicial"])
        if not preliminary:
            try:
                async with asyncio.timeout(30):
                    sec = await self.security.analyze(m.address)
            except TimeoutError:
                sec = SecurityResult(unknown=["Prazo on-chain excedido; análise não aprovada"])
        if not preliminary:
            self.desk.emit(
                "vet",
                "rejected"
                if sec.critical
                else "unknown"
                if sec.unknown or not sec.authority_checked or not sec.concentration_resolved
                else "completed",
                "; ".join(sec.critical or sec.unknown) or "Verificações disponíveis concluídas",
                key,
            )
        social, smart = self.social.analyze(key), self.smart.signals(m.address)
        self.desk.emit(
            "social",
            "unknown",
            f"{len(social['evidence'])} evidências manuais; autenticidade não auditada",
            key,
        )
        self.desk.emit("judge", "running", "Aplicando regras determinísticas", key)
        t = self.thresholds
        judgment = judge(m, sec, t, social, smart)
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
        self.desk.emit(
            "judge",
            "completed" if judgment.approved else "rejected",
            "; ".join(judgment.reasons),
            key,
            score=judgment.score,
            evaluation_id=evaluation_id,
        )
        return judgment, evaluation_id

    async def scan(self):
        if self.scan_lock.locked():
            return {"status": "already_running"}
        async with self.scan_lock:
            if self.controls()["discovery_paused"]:
                self.status = "paused"
                self.desk.emit("crawler", "paused", "Descoberta pausada; risco permanece independente")
                return {"status": "paused"}
            self.status = "scanning"
            self.desk.start_cycle()
            self.desk.emit("crawler", "running", "Consultando novos pools e tokens públicos")
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
                self.desk.cycle["received"] = len(markets)
                self.desk.emit(
                    "crawler",
                    "completed" if markets else "error",
                    f"{len(markets)} mercados recebidos; {len(errors)} falhas de fonte",
                )
                evaluated = []
                for m in markets:
                    self.record_market(m)
                    if self.benches.blocked("solana:" + m.address):
                        self.desk.cycle["blocked"] += 1
                        continue
                    j, eid = await self.evaluate(m)
                    evaluated.append((m, j))
                    self.desk.cycle["evaluated"] += 1
                    self.desk.cycle["approved" if j.approved else "rejected"] += 1
                selection = pick(evaluated)
                self.desk.emit(
                    "chief",
                    "completed" if selection["selected"] else "rejected",
                    selection["message"],
                    selection["selected"] or selection["best_candidate"],
                )
                if selection["selected"]:
                    m, j = next(x for x in evaluated if "solana:" + x[0].address == selection["selected"])
                    await self.prepare_entry(m, selection)
                else:
                    self.desk.emit("size", "skipped", "Sem candidato aprovado; orçamento não alocado")
                    self.desk.emit("fills", "skipped", "NO TRADE — nenhuma entrada enviada")
                self.desk.cycle["finished_at"] = now()
                selection["cycle"] = dict(self.desk.cycle)
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
                self.desk.cycle["finished_at"] = now()
                self.desk.cycle["error"] = type(exc).__name__
                for name in ("crawler", "scan", "vet", "judge", "chief", "size", "fills"):
                    if self.desk.agents[name].state == "running":
                        self.desk.emit(name, "error", "Ciclo interrompido: " + type(exc).__name__)
                self.db.event("scan_error", "Falha de ciclo: " + type(exc).__name__, "critical")
                logger.error("scan_failed:" + type(exc).__name__)
                return {"status": "error"}

    async def prepare_entry(self, candidate: Market, selection: dict):
        """Refresh and re-evaluate the winner, then submit only to the atomic shadow gate."""
        key = "solana:" + candidate.address
        try:
            self.desk.emit("chief", "running", "Revalidando mercado do candidato antes da entrada", key)
            provider = self.gecko if candidate.source == "geckoterminal" else self.dex
            m = await provider.refresh(candidate.pool)
            if m.address != candidate.address:
                raise ValueError("Pool retornou outro mint")
            m.discovered_by = candidate.discovered_by
            self.record_market(m)
            j, eid = await self.evaluate(m)
            selection["entry_evaluation_id"] = eid
            changed = j.observed.get("thresholds") != self.thresholds.model_dump()
            if not j.approved or arithmetic_filter(m, self.thresholds) or changed:
                selection["worth_trading"] = False
                selection["selected"] = None
                selection["execution"] = {
                    "status": "rejected",
                    "reason": "Configuração mudou durante avaliação"
                    if changed
                    else "Revalidação: " + "; ".join(j.reasons),
                }
                selection["message"] = "NO TRADE — candidato não passou na revalidação."
                self.desk.emit("chief", "rejected", selection["message"], key)
                self.desk.emit("size", "skipped", "Revalidação bloqueou alocação", key)
            else:
                t = self.thresholds
                with self.db.session() as session:
                    account = session.get(Account, 1)
                    size = position_size(
                        float(self.execution.equity(session, account)), float(account.cash), m, j, t
                    )
                entry = m.price * (1 + size["slippage_bps"] / 10000)
                plan = size | {
                    "token_key": key,
                    "symbol": m.symbol,
                    "observed_at": m.observed_at,
                    "estimated_entry": entry,
                    "estimated_entry_fee": size["budget"] * t.fee_bps / 10000 + t.fixed_cost_usd,
                    "stop_trigger": entry * (1 - t.stop_loss_pct / 100),
                    "take_trigger": entry * (1 + t.take_profit_pct / 100),
                    "max_hold_minutes": t.max_hold_minutes,
                    "evaluation_id": eid,
                    "mode": "shadow",
                    "thresholds": t.model_dump(),
                }
                selection["plan"] = plan
                self.desk.emit(
                    "size", "completed", f"Orçamento shadow calculado: ${size['budget']:.2f}", key, **size
                )
                if not t.auto_shadow or self.controls()["discovery_paused"]:
                    selection["execution"] = {
                        "status": "skipped",
                        "reason": "Automação desativada ou descoberta pausada",
                    }
                else:
                    self.desk.emit(
                        "fills", "running", "Verificando limite diário, posição e idempotência", key
                    )
                    selection["execution"] = self.execution.open(m, j, t, f"evaluation:{eid}")
                selection["message"] = (
                    "Entrada SHADOW registrada; risco acompanha a posição."
                    if selection["execution"]["status"] == "simulated"
                    else "Candidato aprovado; entrada bloqueada ou aguardando automação."
                )
                self.desk.emit("chief", "completed", selection["message"], key)
        except Exception as exc:
            selection["execution"] = {
                "status": "rejected",
                "reason": "Revalidação indisponível: " + type(exc).__name__,
            }
            selection["worth_trading"] = False
            selection["selected"] = None
            selection["message"] = "NO TRADE — não foi possível revalidar o candidato."
            self.desk.emit("chief", "error", selection["message"], key)
            self.desk.emit("size", "skipped", "Falha de fonte; nenhuma alocação autorizada", key)
        result = selection["execution"]
        self.desk.emit(
            "fills",
            "completed" if result["status"] == "simulated" else result["status"],
            result.get("reason", "Entrada simulada registrada"),
            key,
            result=result,
        )

    async def risk_tick(self, forced_reason=None):
        async with self.risk_lock:
            with self.db.session() as s:
                p = s.scalar(select(Position).where(Position.active_slot == 1))
            if not p:
                self.desk.emit("risk", "idle", "Sem posição aberta; aguardando próxima verificação")
                with self.db.session() as s:
                    latest = s.scalar(select(Equity).order_by(Equity.id.desc()).limit(1))
                    account = s.get(Account, 1)
                    if account and (latest is None or now() - latest.at >= 60):
                        s.add(Equity(value=account.cash, data={"mode": "shadow", "open_positions": 0}))
                return None
            self.desk.emit("risk", "running", "Atualizando preço e avaliando gatilhos de saída", p.token_key)
            try:
                provider = self.gecko if p.data["source"] == "geckoterminal" else self.dex
                m = await provider.refresh(p.data["pool"])
                if "solana:" + m.address != p.token_key:
                    raise ValueError("Pool retornou outro mint")
                self.record_market(m)
                result = self.execution.mark_and_manage(m, self.thresholds, forced_reason)
                self.desk.emit(
                    "risk",
                    "blocked" if result and result["status"] == "exit_pending" else "completed",
                    result.get("reason", "Posição acompanhada")
                    if result
                    else "Preço atualizado; nenhum gatilho de saída",
                    p.token_key,
                )
                if result:
                    self.desk.emit(
                        "fills",
                        "completed" if result["status"] == "simulated" else "blocked",
                        "Saída SHADOW: " + result.get("reason", result["status"]),
                        p.token_key,
                        result=result,
                    )
                return result
            except Exception as exc:
                self.desk.emit(
                    "risk", "error", "Falha de preço; saída pendente e posição reservada", p.token_key
                )
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
        self.desk.emit("smart", "running", "Consultando transações de carteiras monitoradas")
        mints = await self.smart.poll()
        self.desk.emit(
            "smart", "completed", f"{len(mints)} tokens com sinais neste ciclo; não comprova rentabilidade"
        )
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
                self.desk.emit(
                    {"wallets": "smart", "discovery": "crawler", "risk": "risk"}.get(name, "crawler"),
                    "error",
                    name + ": " + type(exc).__name__,
                )
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
