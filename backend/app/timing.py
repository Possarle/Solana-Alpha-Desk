"""Does latency cost money? Retroactive audit of closed shadow trades.

Uses only what the robot recorded: positions and the prices it collected (observations)
from the SAME pool while the trade was alive. Works on history created before this
module existed; newer trades also carry the decision price and data age at entry/exit.
Every figure is an estimate in USD of simulated capital, never realized profit.
"""

import statistics

from sqlalchemy import select

from .db import Observation, Position
from .thresholds import Thresholds

STOPS = {"STOP_LOSS", "TRAILING_STOP", "DAILY_LOSS_LIMIT"}
LEVELS = {"STOP_LOSS", "TRAILING_STOP", "TAKE_PROFIT"}


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _trade(p: Position, obs: list[Observation], defaults: dict) -> dict:
    d = p.data
    th = (d.get("judgment") or {}).get("observed", {}).get("thresholds") or defaults
    qty, entry = float(p.qty), float(p.entry)
    decision = d.get("decision_price") or entry / (1 + d.get("slippage_bps", 0) / 10000)
    exit_mark = d.get("exit_mark")
    if exit_mark is None and d.get("exit_price") is not None and qty:
        exit_mark = d["exit_price"] + d.get("exit_slippage_cost", 0) / qty
    reason = d.get("exit_reason", "?")
    pool = d.get("pool")
    seen = [o for o in obs if o.data.get("pool") == pool and o.data.get("price")]

    # Entry: first price collected AFTER the simulated buy, within 2 minutes.
    after = next((o for o in seen if p.opened_at < o.at <= p.opened_at + 120), None)
    drift_pct = (after.data["price"] / decision - 1) * 100 if after else None
    entry_cost = qty * (after.data["price"] - decision) if after else None

    # Exit: how far beyond the trigger level the simulated sale happened, and how late.
    level = None
    if reason == "STOP_LOSS":
        level = entry * (1 - th.get("stop_loss_pct", 12) / 100)
    elif reason == "TAKE_PROFIT":
        level = entry * (1 + th.get("take_profit_pct", 25) / 100)
    elif reason == "TRAILING_STOP":
        level = float(p.peak) * (1 - th.get("trailing_stop_pct", 10) / 100)
    gap_pct = gap_cost = lag_s = None
    if level and exit_mark:
        gap_pct = (exit_mark / level - 1) * 100
        # Only stops count as a latency cost: > 0 means the sale happened below the stop level.
        gap_cost = qty * (level - exit_mark) if reason in STOPS else None
        down = reason != "TAKE_PROFIT"
        crossed = next(
            (
                o
                for o in seen
                if p.opened_at < o.at <= (p.closed_at or o.at)
                and (o.data["price"] <= level if down else o.data["price"] >= level)
            ),
            None,
        )
        lag_s = (p.closed_at - crossed.at) if crossed and p.closed_at else None
    costs = (
        d.get("entry_fee", 0)
        + d.get("exit_fee", 0)
        + d.get("entry_slippage_cost", 0)
        + d.get("exit_slippage_cost", 0)
    )
    return {
        "id": p.id,
        "symbol": d.get("symbol", "?"),
        "reason": reason,
        "pnl": float(p.pnl or 0),
        "hold_min": ((p.closed_at or p.opened_at) - p.opened_at) / 60,
        "entry_data_age_s": d.get("entry_data_age_s"),
        "exit_data_age_s": d.get("exit_data_age_s"),
        "entry_drift_pct": drift_pct,
        "entry_latency_cost": entry_cost,
        "exit_gap_pct": gap_pct,
        "exit_gap_cost": gap_cost,
        "exit_lag_s": lag_s,
        "costs": costs,
        "samples": len(seen),
    }


def timing_report(db, limit: int = 300) -> dict:
    with db.session() as s:
        closed = s.scalars(
            select(Position)
            .where(Position.status == "closed")
            .order_by(Position.closed_at.desc())
            .limit(limit)
        ).all()
        trades = []
        defaults = Thresholds().model_dump()
        for p in closed:
            obs = s.scalars(
                select(Observation)
                .where(
                    Observation.token_key == p.token_key,
                    Observation.at >= p.opened_at - 300,
                    Observation.at <= (p.closed_at or p.opened_at) + 300,
                )
                .order_by(Observation.at)
            ).all()
            trades.append(_trade(p, obs, defaults))
    pnl = sum(t["pnl"] for t in trades)
    entry_cost = sum(t["entry_latency_cost"] for t in trades if t["entry_latency_cost"] is not None)
    gap_cost = sum(t["exit_gap_cost"] for t in trades if t["exit_gap_cost"] is not None)
    costs = sum(t["costs"] for t in trades)
    reasons: dict[str, dict] = {}
    for t in trades:
        r = reasons.setdefault(t["reason"], {"reason": t["reason"], "trades": 0, "pnl": 0.0, "wins": 0})
        r["trades"] += 1
        r["pnl"] += t["pnl"]
        r["wins"] += t["pnl"] > 0
    latency = entry_cost + gap_cost
    if not trades:
        verdict = "Ainda não há operações shadow encerradas para medir."
    elif latency <= 0:
        verdict = "Nas operações medidas, o atraso não piorou o resultado estimado."
    else:
        share = latency / abs(pnl) * 100 if pnl else None
        verdict = f"O atraso custou cerca de ${latency:.2f} nas operações medidas" + (
            f" (~{share:.0f}% do tamanho do resultado)." if share is not None else "."
        )
    return {
        "trades": len(trades),
        "pnl": pnl,
        "entry_latency_cost": entry_cost,
        "exit_gap_cost": gap_cost,
        "fees_and_slippage": costs,
        "median_entry_data_age_s": _median([t["entry_data_age_s"] for t in trades]),
        "median_entry_drift_pct": _median([t["entry_drift_pct"] for t in trades]),
        "median_exit_gap_pct": _median([t["exit_gap_pct"] for t in trades if t["reason"] in STOPS]),
        "median_exit_lag_s": _median([t["exit_lag_s"] for t in trades]),
        "median_hold_min": _median([t["hold_min"] for t in trades]),
        "by_reason": sorted(reasons.values(), key=lambda r: r["pnl"]),
        "verdict": verdict,
        "method": "Custo de entrada = qtd × (primeiro preço coletado até 2 min após a compra − preço da decisão). "
        "Custo de saída = qtd × (nível do gatilho − preço de saída), só em stops. Atraso de saída = tempo entre "
        "o primeiro preço coletado além do gatilho e a venda simulada. Preços das fontes podem ter até ~60 s "
        "de cache; estimativas, não lucro realizado.",
        "trades_detail": trades[:30],
    }
