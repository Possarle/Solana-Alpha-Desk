"""Launch mode: decide on pump.fun tokens seconds after creation (shadow only).

What CAN be checked in the first seconds, and is: mint/freeze authority, token
program and extensions, how much the creator bought at launch, how much SOL other
wallets put in since creation, how far the price already ran, and the price impact
of our own order on the curve. What CANNOT (volume history, sell evidence, holder
distribution, social) is reported as unknown; an unknown is never a pass.
"""

import time
from collections import Counter, deque

from sqlalchemy import select

from ..db import Account, Position
from ..providers.solana import SOL, TOKEN_2022, TOKEN_PROGRAM
from ..schemas import now
from ..thresholds import LaunchThresholds
from .execution import LaunchExecution
from .feed import PumpPortalFeed
from .pumpfun import CurveError, Launch, curve_address, decode_curve

SAFE_2022_EXTENSIONS = {"metadataPointer", "tokenMetadata"}
STALE_AFTER = 45  # seconds past the planned check: the moment is gone, do not buy late


def mint_checks(account: dict | None) -> tuple[list[str], dict]:
    """Authorities and program from a jsonParsed mint account. Returns (failures, facts)."""
    if not account:
        return ["Mint não encontrado"], {}
    program = account.get("owner")
    parsed = (account.get("data") or {}).get("parsed", {}) if isinstance(account.get("data"), dict) else {}
    info = parsed.get("info", {})
    if parsed.get("type") != "mint" or "mintAuthority" not in info or "freezeAuthority" not in info:
        return ["Permissões do mint não decodificadas"], {"program": program}
    extensions = [x.get("extension", "unknown") for x in info.get("extensions", [])]
    facts = {
        "program": "Token-2022"
        if program == TOKEN_2022
        else "SPL Token"
        if program == TOKEN_PROGRAM
        else program,
        "mint_authority": info["mintAuthority"],
        "freeze_authority": info["freezeAuthority"],
        "decimals": info.get("decimals"),
        "extensions": extensions,
    }
    fails = []
    if program not in (TOKEN_PROGRAM, TOKEN_2022):
        fails.append("Programa do token não reconhecido")
    if info["mintAuthority"]:
        fails.append("Mint authority ativa")
    if info["freezeAuthority"]:
        fails.append("Freeze authority ativa")
    risky = [x for x in extensions if x not in SAFE_2022_EXTENSIONS]
    if program == TOKEN_2022 and risky:
        fails.append("Extensões Token-2022 de risco: " + ", ".join(risky))
    if info.get("decimals") != 6:
        fails.append("Casas decimais fora do padrão pump.fun")
    return fails, facts


class LaunchDesk:
    def __init__(self, engine, feed: PumpPortalFeed | None = None):
        self.engine, self.db, self.rpc, self.dex = engine, engine.db, engine.rpc, engine.dex
        self.execution = LaunchExecution(engine.db)
        self.feed = feed or PumpPortalFeed(self.on_launch)
        self.queue: deque[tuple[float, Launch]] = deque(maxlen=400)
        self.recent: deque[dict] = deque(maxlen=60)
        self.stats: Counter = Counter()
        self.reasons: Counter = Counter()
        self.checks: deque[float] = deque()
        self.sol: tuple[float, float] | None = None  # (usd, fetched_at)
        self.started_at = now()

    @property
    def thresholds(self) -> LaunchThresholds:
        return LaunchThresholds(**self.db.config("launch_thresholds", LaunchThresholds().model_dump()))

    # ---------- intake (no network) ----------
    def _record(self, launch: Launch, verdict: str, reasons: list[str], **facts):
        self.stats[verdict] += 1
        for r in reasons:
            self.reasons[r.split(":")[0]] += 1
        self.recent.appendleft(
            {
                "mint": launch.mint,
                "symbol": launch.symbol,
                "name": launch.name,
                "seen_at": launch.seen_at,
                "checked_at": now(),
                "verdict": verdict,
                "reasons": reasons,
                **facts,
            }
        )

    def on_launch(self, launch: Launch):
        t = self.thresholds
        self.stats["received"] += 1
        if not t.enabled:
            return
        dev = launch.dev_buy_pct
        if dev is not None and dev > t.max_dev_buy_pct:
            self._record(launch, "rejected", [f"Criador comprou {dev:.1f}% do supply"], dev_buy_pct=dev)
            return
        self.queue.append((launch.seen_at + t.confirm_seconds, launch))

    # ---------- checks ----------
    def _budget_ok(self, t: LaunchThresholds) -> bool:
        cutoff = time.monotonic() - 60
        while self.checks and self.checks[0] < cutoff:
            self.checks.popleft()
        return len(self.checks) < t.max_checks_per_minute

    async def sol_price(self) -> float | None:
        if self.sol and now() - self.sol[1] < 60:
            return self.sol[0]
        try:
            pairs = await self.dex.tokens([SOL])
        except Exception:
            pairs = []
        pairs = [p for p in pairs if p.price and p.liquidity]
        if not pairs:
            return self.sol[0] if self.sol and now() - self.sol[1] < 300 else None
        best = max(pairs, key=lambda p: p.liquidity or 0)
        self.sol = (best.price, now())
        return best.price

    async def tick(self):
        """Process candidates whose confirmation time arrived."""
        t = self.thresholds
        while self.queue and self.queue[0][0] <= now():
            due, launch = self.queue[0]
            if now() - due > STALE_AFTER:
                self.queue.popleft()
                self._record(launch, "skipped", ["Verificação atrasada: limite de consultas"])
                continue
            if not self._budget_ok(t):
                return  # wait for budget; may become stale and be skipped honestly
            self.queue.popleft()
            self.checks.append(time.monotonic())
            await self.check(launch, t)

    async def check(self, launch: Launch, t: LaunchThresholds) -> dict:
        key = curve_address(launch.mint)
        reasons: list[str] = []
        facts: dict = {"age_s": round(now() - launch.seen_at, 1), "dev_buy_pct": launch.dev_buy_pct}
        try:
            values = (
                await self.rpc.call(
                    "getMultipleAccounts",
                    [[key, launch.mint], {"encoding": "jsonParsed", "commitment": "confirmed"}],
                    ttl=0,
                    priority=15,
                )
            )["value"]
            curve = decode_curve(values[0])
        except CurveError as exc:
            self._record(launch, "rejected", [str(exc)], **facts)
            return {"approved": False}
        except Exception as exc:  # RPC failure is never an approval
            self._record(launch, "error", ["Consulta on-chain falhou: " + type(exc).__name__], **facts)
            return {"approved": False}
        fails, mint_facts = mint_checks(values[1])
        reasons += fails
        facts |= {"mint_info": mint_facts}
        sol_usd = await self.sol_price()
        buyers = curve.real_sol_ui - launch.dev_buy_sol if launch.dev_buy_sol is not None else None
        facts |= {
            "buyers_sol": round(buyers, 3) if buyers is not None else None,
            "curve_sol": round(curve.real_sol_ui, 3),
            "price_sol": curve.price_sol,
            "mcap_usd": curve.price_sol * curve.supply / 10**6 * sol_usd if sol_usd else None,
            "sol_usd": sol_usd,
        }
        if curve.complete:
            reasons.append("Já saiu da curva (migrou)")
        if launch.dev_buy_pct is None:
            reasons.append("Compra do criador desconhecida")
        if buyers is None:
            reasons.append("Entrada de terceiros desconhecida")
        elif buyers < t.min_buyers_sol:
            reasons.append(f"Pouca compra de terceiros: {buyers:.2f} SOL")
        if sol_usd is None:
            reasons.append("Preço do SOL indisponível")
        elif facts["mcap_usd"] > t.max_entry_mcap_usd:
            reasons.append(f"Já subiu demais: market cap ${facts['mcap_usd']:,.0f}")
        unknown = [
            "Volume e vendas ainda não existem",
            "Distribuição de holders",
            "Identidade do criador",
            "Social",
        ]
        verdict = {"approved": not reasons, "reasons": reasons, "unknown": unknown, "facts": facts}
        if reasons:
            self._record(launch, "rejected", reasons, **facts)
            return verdict
        result = self.execution.open_curve(launch, key, curve, sol_usd, t, f"launch:{launch.mint}", verdict)
        self._record(
            launch,
            "bought" if result["status"] == "simulated" else "approved",
            [] if result["status"] == "simulated" else [result.get("reason", "Entrada bloqueada")],
            **facts,
            execution=result,
        )
        return verdict | {"execution": result}

    # ---------- exits ----------
    async def risk_tick(self, forced_reason: str | None = None):
        with self.db.session() as s:
            p = s.scalar(select(Position).where(Position.active_slot == self.execution.slot))
        if not p:
            return None
        t = self.thresholds
        try:
            value = (
                await self.rpc.call(
                    "getMultipleAccounts",
                    [[p.data["pool"]], {"encoding": "jsonParsed", "commitment": "confirmed"}],
                    ttl=0,
                    priority=0,
                )
            )["value"][0]
            curve = decode_curve(value)
        except Exception:
            curve = None
        migrated = None
        if curve is not None and curve.complete:
            try:
                mint = p.token_key.removeprefix("solana:")
                pools = [m for m in await self.dex.tokens([mint]) if m.price and m.liquidity]
                migrated = max(pools, key=lambda m: m.liquidity or 0) if pools else None
            except Exception:
                migrated = None
        return self.execution.manage_curve(curve, await self.sol_price(), t, forced_reason, migrated)

    # ---------- reporting ----------
    def snapshot(self) -> dict:
        with self.db.session() as s:
            account = s.get(Account, self.execution.account_id)
            positions = s.scalars(
                select(Position).where(Position.mode == "launch").order_by(Position.id.desc()).limit(200)
            ).all()
            equity = float(self.execution.equity(s, account)) if account else None
        closed = [p for p in positions if p.status == "closed"]
        pnls = [float(p.pnl or 0) for p in closed]
        active = next((p for p in positions if p.active_slot == self.execution.slot), None)
        return {
            "thresholds": self.thresholds.model_dump(),
            "feed": self.feed.health,
            "since": self.started_at,
            "stats": dict(self.stats) | {"queued": len(self.queue)},
            "top_reasons": [{"reason": r, "count": n} for r, n in self.reasons.most_common(8)],
            "recent": list(self.recent)[:40],
            "account": None
            if account is None
            else {
                "initial": float(account.initial),
                "cash": float(account.cash),
                "equity": equity,
                "pnl": equity - float(account.initial),
                "halted": account.halted,
            },
            "active": None
            if active is None
            else {
                "symbol": active.data.get("symbol"),
                "token_key": active.token_key,
                "status": active.status,
                "entry": float(active.entry),
                "mark": float(active.mark),
                "cost": float(active.cost),
                "pnl_pct": (float(active.mark) / float(active.entry) - 1) * 100,
                "opened_at": active.opened_at,
                "entry_age_s": active.data.get("entry_age_s"),
                "pending_reason": active.data.get("pending_reason"),
            },
            "closed": {
                "trades": len(closed),
                "pnl": sum(pnls),
                "wins": sum(x > 0 for x in pnls),
                "expectancy": sum(pnls) / len(pnls) if pnls else None,
                "by_reason": dict(Counter(p.data.get("exit_reason", "?") for p in closed)),
                "last": [
                    {
                        "symbol": p.data.get("symbol"),
                        "pnl": float(p.pnl or 0),
                        "reason": p.data.get("exit_reason"),
                        "hold_min": ((p.closed_at or p.opened_at) - p.opened_at) / 60,
                        "closed_at": p.closed_at,
                    }
                    for p in closed[:10]
                ],
            },
            "method": "Modo lançamento SHADOW: compra e venda calculadas na curva do pump.fun com taxa de 1% e "
            "impacto do tamanho da ordem. Contadores do feed reiniciam quando o robô reinicia; posições e "
            "resultados ficam no banco.",
        }
