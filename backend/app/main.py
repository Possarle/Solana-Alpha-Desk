import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .config import Settings, settings
from .db import (
    Account,
    Bench,
    Database,
    Decision,
    Evaluation,
    Event,
    Evidence,
    Observation,
    Order,
    Position,
    Token,
    Wallet,
    WalletEvent,
)
from .engine import Engine
from .logging_setup import configure_logging
from .performance import performance
from .schemas import EvidenceInput, Market, WalletInput, now, valid_address
from .thresholds import Thresholds


def position_json(p):
    return {
        "id": p.id,
        "token_key": p.token_key,
        "status": p.status,
        "mode": p.mode,
        "qty": float(p.qty),
        "cost": float(p.cost),
        "entry": float(p.entry),
        "mark": float(p.mark),
        "pnl": float(p.pnl) if p.pnl is not None else float(p.qty * p.mark - p.cost),
        "opened_at": p.opened_at,
        "closed_at": p.closed_at,
        "data": p.data,
    }


class ControlInput(BaseModel):
    action: Literal["pause", "resume", "emergency_stop", "clear_emergency"]


class OpenInput(BaseModel):
    address: str


class LivePrepareInput(BaseModel):
    request_id: UUID
    address: str = Field(min_length=32, max_length=44)
    side: Literal["buy", "sell"]
    usdc: Decimal = Field(default=Decimal("1"), gt=0, le=100, decimal_places=6)


class LiveConfirmInput(BaseModel):
    review_hash: str = Field(pattern="^[0-9a-f]{64}$")
    phrase: str = Field(max_length=100)


class QuoteInput(BaseModel):
    address: str
    usd: float = Field(10, gt=0, le=100)


def create_app(cfg: Settings = settings, database: Database | None = None):
    db = database or Database(cfg.database_url)
    engine = Engine(db, cfg)
    lock_file = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal lock_file
        configure_logging()
        # One scheduler per database deployment. Docker/API run a single worker.
        if cfg.enable_scheduler and os.name == "posix":
            import fcntl

            lock_file = open(".alpha-scheduler.lock", "a")
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock_file.close()
                raise RuntimeError("Outra instância está executando o scheduler neste diretório") from None
        await engine.start()
        yield
        await engine.stop()
        if lock_file:
            lock_file.close()

    app = FastAPI(title="Solana Alpha Desk", version="0.4.0", lifespan=lifespan)
    app.state.engine, app.state.db = engine, db
    origins = [x.strip() for x in cfg.allowed_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        from starlette.responses import JSONResponse

        if request.url.path.startswith("/api"):
            expected = cfg.dashboard_token.get_secret_value()
            provided = request.headers.get("authorization", "").removeprefix("Bearer ")
            if expected and not hmac.compare_digest(expected, provided):
                return JSONResponse({"detail": "Token do dashboard necessário"}, status_code=401)
            if request.method not in ("GET", "OPTIONS"):
                origin = request.headers.get("origin")
                same_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
                if origin and origin not in origins and origin != same_origin:
                    return JSONResponse({"detail": "Origem não autorizada"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def snapshot():
        with db.session() as s:
            tokens = s.scalars(select(Token).order_by(Token.updated.desc()).limit(500)).all()
            evaluations = s.scalars(select(Evaluation).order_by(Evaluation.id.desc()).limit(3000)).all()
            latest = {}
            for e in evaluations:
                latest.setdefault(e.token_key, e)
            account = s.get(Account, 1)
            positions = s.scalars(select(Position).order_by(Position.id.desc()).limit(500)).all()
            active = [p for p in positions if p.active_slot == 1]
            equity = engine.execution.equity(s, account)
            blocks = s.scalars(select(Bench).where(Bench.until > now())).all()
        scanner = [
            t.data
            | {
                "key": t.key,
                "judgment": latest[t.key].data if t.key in latest else None,
                "evaluated_at": latest[t.key].at if t.key in latest else None,
                "stale": Market(**t.data).age_seconds() > engine.thresholds.max_data_age_seconds,
            }
            for t in tokens
        ]
        events = db.rows(Event, 60)
        return {
            "time": now(),
            "desk": engine.desk.snapshot(),
            "status": engine.status,
            "mode": "shadow",
            "live_available": False,
            "last_scan": engine.last_scan,
            "controls": engine.controls(),
            "scanning": engine.scan_lock.locked(),
            "account": {
                "initial": float(account.initial),
                "cash": float(account.cash),
                "equity": float(equity),
                "pnl": float(equity - account.initial),
                "halted": account.halted,
                "day_start": float(account.day_start),
                "day_timezone": "UTC",
                "valuation_stale": any(
                    now() - p.data.get("last_mark_at", 0) > engine.thresholds.max_data_age_seconds
                    for p in active
                ),
            },
            "tokens": scanner,
            "positions": [position_json(p) for p in positions],
            "counts": {
                "tokens": len(tokens),
                "approved": sum(
                    bool(x["judgment"] and x["judgment"]["approved"] and not x["stale"]) for x in scanner
                ),
                "rejected": sum(bool(x["judgment"] and not x["judgment"]["approved"]) for x in scanner),
                "open": len(active),
            },
            "events": [
                {
                    "id": e.id,
                    "at": e.at,
                    "kind": e.kind,
                    "level": e.level,
                    "message": e.message,
                    "data": e.data,
                }
                for e in events
            ],
            "decisions": [{"id": x.id, "at": x.at, **x.data} for x in db.rows(Decision, 30)],
            "services": engine.health(),
            "thresholds": engine.thresholds.model_dump(),
            "blocks": [{"key": b.key, "until": b.until, **b.data} for b in blocks],
        }

    @app.get("/healthz")
    def health():
        return {"status": "ok", "mode": "shadow", "version": "0.4.0"}

    @app.get("/api/state")
    def state():
        return snapshot()

    @app.get("/api/performance")
    def stats():
        return performance(db)

    @app.get("/api/report")
    def report():
        return {
            "generated_at": now(),
            "mode": "shadow",
            "currency": "USD",
            "disclaimer": "Resultados simulados, não realizados em blockchain.",
            "performance": performance(db),
            "thresholds": engine.thresholds.model_dump(),
            "decisions": [{"at": row.at, **row.data} for row in db.rows(Decision, 300)],
        }

    @app.post("/api/scan", status_code=202)
    async def scan():
        if not engine.scan_lock.locked():
            task = asyncio.create_task(engine.scan())
            engine.tasks.append(task)
            task.add_done_callback(lambda t: engine.tasks.remove(t) if t in engine.tasks else None)
        return {"status": "queued"}

    @app.put("/api/thresholds")
    def thresholds(value: Thresholds):
        previous = engine.thresholds.model_dump()
        db.set_config("thresholds", value.model_dump())
        with db.session() as s:
            s.execute(delete(Bench))
        db.event(
            "settings",
            "Limites atualizados; bloqueios reiniciados para reavaliação",
            data={"previous": previous, "current": value.model_dump()},
        )
        return value

    @app.post("/api/control")
    async def control(value: ControlInput):
        controls = engine.controls()
        if value.action in ("pause", "resume"):
            controls["discovery_paused"] = value.action == "pause"
            db.set_config("controls", controls)
            engine.status = "paused" if controls["discovery_paused"] else "running"
        else:
            if value.action == "clear_emergency" and not engine.execution.reconcile()["matches"]:
                raise HTTPException(409, "Reconciliação divergente: entradas continuam bloqueadas")
            with db.session() as s:
                account = engine.execution._lock(s)
                if value.action == "clear_emergency":
                    equity = engine.execution.equity(s, account)
                    engine.execution._roll_day(account, equity)
                    if engine.execution.daily_breach(account, equity, engine.thresholds):
                        raise HTTPException(409, "Perda diária permanece acima do limite")
                account.halted = value.action == "emergency_stop"
            if value.action == "emergency_stop":
                task = asyncio.create_task(engine.risk_tick("EMERGENCY_STOP"))
                engine.tasks.append(task)
                task.add_done_callback(lambda t: engine.tasks.remove(t) if t in engine.tasks else None)
        db.event("control", value.action, "warning")
        return {"status": "ok"}

    @app.get("/api/tokens/{address}")
    def detail(address: str):
        key = "solana:" + address
        with db.session() as s:
            token = s.get(Token, key)
            if not token:
                raise HTTPException(404, "Token não coletado")
            prices = s.scalars(
                select(Observation)
                .where(Observation.token_key == key)
                .order_by(Observation.id.desc())
                .limit(300)
            ).all()[::-1]
            evaluations = s.scalars(
                select(Evaluation).where(Evaluation.token_key == key).order_by(Evaluation.id.desc()).limit(20)
            ).all()
        return {
            "market": token.data,
            "prices": [
                {"at": x.at, "price": x.data.get("price"), "liquidity": x.data.get("liquidity")}
                for x in prices
            ],
            "evaluations": [
                {"id": e.id, "at": e.at, "judgment": e.data, "security": e.security} for e in evaluations
            ],
            "social": engine.social.analyze(key),
            "smart": engine.smart.signals(address),
        }

    @app.post("/api/shadow/open")
    async def open_shadow(value: OpenInput):
        try:
            address = valid_address(value.address)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        with db.session() as s:
            token = s.get(Token, "solana:" + address)
            ev = s.scalar(
                select(Evaluation)
                .where(Evaluation.token_key == "solana:" + address)
                .order_by(Evaluation.id.desc())
                .limit(1)
            )
        if not token or not ev:
            raise HTTPException(404, "Token sem avaliação")
        if engine.controls()["discovery_paused"]:
            raise HTTPException(409, "Descoberta pausada; entradas pausadas")
        # Re-evaluate persisted objective evidence against current limits, without resetting observation age.
        from .agents.judge import judge
        from .schemas import SecurityResult

        m = Market(**token.data)
        if now() - ev.at > engine.thresholds.max_data_age_seconds:
            raise HTTPException(409, "Avaliação expirada; aguarde novo ciclo")
        j = judge(
            m,
            SecurityResult(**ev.security),
            engine.thresholds,
            engine.social.analyze(token.key),
            engine.smart.signals(address),
        )
        return engine.execution.open(m, j, engine.thresholds, f"evaluation:{ev.id}")

    @app.post("/api/shadow/close")
    async def close_shadow():
        return await engine.risk_tick("MANUAL_EXIT") or {"status": "no_position"}

    @app.get("/api/orders")
    def orders():
        return [{"key": x.key, "at": x.at, **x.data} for x in db.rows(Order, 200)]

    @app.post("/api/route")
    async def route(value: QuoteInput):
        try:
            return await engine.live.prepare(valid_address(value.address), value.usd)
        except Exception as exc:
            raise HTTPException(503, "Rota indisponível: " + type(exc).__name__) from None

    @app.get("/api/live/status")
    def live_status():
        return engine.supervised.status()

    @app.post("/api/live/prepare")
    async def live_prepare(value: LivePrepareInput):
        try:
            return await engine.supervised.prepare(
                str(value.request_id), value.address, value.side, value.usdc
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/live/{order_id}/confirm")
    async def live_confirm(order_id: UUID, value: LiveConfirmInput):
        try:
            return await engine.supervised.confirm(str(order_id), value.review_hash, value.phrase)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/live/{order_id}/cancel")
    async def live_cancel(order_id: UUID):
        try:
            return await engine.supervised.cancel(str(order_id))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/live/control")
    def live_control(value: ControlInput):
        if len(cfg.dashboard_token.get_secret_value()) < 32:
            raise HTTPException(409, "Configure DASHBOARD_TOKEN com 32 caracteres ou mais")
        try:
            if value.action == "emergency_stop":
                engine.supervised.halt()
            elif value.action == "clear_emergency":
                engine.supervised.resume()
            else:
                raise ValueError("Ação real inválida")
            return engine.supervised.status()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/live/reconcile")
    async def live_reconcile():
        try:
            await engine.supervised.poll()
            return engine.supervised.status()
        except Exception as exc:
            raise HTTPException(503, "Reconciliação indisponível: " + type(exc).__name__) from None

    @app.get("/api/wallets")
    def wallets():
        with db.session() as s:
            rows = s.scalars(select(Wallet)).all()
            events = s.scalars(select(WalletEvent).order_by(WalletEvent.at.desc()).limit(100)).all()
        return {
            "wallets": [
                {
                    "address": w.address,
                    "label": w.label,
                    **w.data,
                    "performance": engine.smart.report(w.address),
                }
                for w in rows
            ],
            "events": [{"at": e.at, "wallet": e.wallet, **e.data} for e in events],
        }

    @app.post("/api/wallets")
    def add_wallet(value: WalletInput):
        with db.session() as s:
            existing = s.get(Wallet, value.address)
            if existing:
                existing.label = value.label
            else:
                count = len(s.scalars(select(Wallet)).all())
                if count >= 5:
                    raise HTTPException(409, "MVP: máximo de 5 carteiras para preservar o orçamento RPC")
                s.add(Wallet(address=value.address, label=value.label, data={"status": "pending"}))
        return {"status": "ok"}

    @app.delete("/api/wallets/{address}")
    def remove_wallet(address: str):
        with db.session() as s:
            s.execute(delete(Wallet).where(Wallet.address == address))
        return {"status": "ok", "history": "preserved"}

    @app.post("/api/evidence")
    def evidence(value: EvidenceInput):
        with db.session() as s:
            s.add(Evidence(token_key="solana:" + value.address, data=value.model_dump()))
        return {"status": "saved"}

    @app.post("/api/reconcile")
    def reconcile():
        return engine.execution.reconcile()

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        origin = ws.headers.get("origin")
        host = ws.headers.get("host", "")
        if origin and origin not in origins and origin not in (f"http://{host}", f"https://{host}"):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            # Credentials in first frame, never query-string/server access logs.
            message = await asyncio.wait_for(ws.receive_json(), timeout=10)
            expected = cfg.dashboard_token.get_secret_value()
            if expected and not hmac.compare_digest(expected, str(message.get("token", ""))):
                await ws.close(code=1008)
                return
            while True:
                await ws.send_json(snapshot())
                await asyncio.sleep(3)
        except (WebSocketDisconnect, TimeoutError, RuntimeError):
            pass

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
