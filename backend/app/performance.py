from sqlalchemy import select

from .db import Equity, Order, Position


def performance(db):
    with db.session() as s:
        positions = s.scalars(select(Position).order_by(Position.id)).all()
        curve = s.scalars(select(Equity).order_by(Equity.id.desc()).limit(3000)).all()[::-1]
        orders = s.scalars(select(Order)).all()
    closed = [p for p in positions if p.status == "closed"]
    pnls = [float(p.pnl) for p in closed]
    wins, losses = [x for x in pnls if x > 0], [x for x in pnls if x < 0]
    peak, points = 0, []
    for x in curve:
        v = float(x.value)
        peak = max(peak, v)
        points.append({"at": x.at, "equity": v, "drawdown": (v / peak - 1) * 100 if peak else 0})
    strategies = {}
    for p in closed:
        key = p.data.get("strategy", "independent")
        strategies.setdefault(key, {"strategy": key, "trades": 0, "pnl": 0})
        strategies[key]["trades"] += 1
        strategies[key]["pnl"] += float(p.pnl)
    return {
        "label": "Resultados simulados; USD nominal, sem impostos",
        "closed": len(closed),
        "realized_simulated": sum(pnls),
        "win_rate": len(wins) / len(pnls) * 100 if pnls else None,
        "expectancy": sum(pnls) / len(pnls) if pnls else None,
        "average_win": sum(wins) / len(wins) if wins else None,
        "average_loss": sum(losses) / len(losses) if losses else None,
        "estimated_fees": sum(o.data.get("fee", 0) for o in orders if o.data.get("status") == "simulated"),
        "estimated_slippage_cost": sum(
            p.data.get("entry_slippage_cost", 0) + p.data.get("exit_slippage_cost", 0) for p in positions
        ),
        "curve": points,
        "strategies": list(strategies.values()),
        "filter_comparison": {
            "available": False,
            "reason": "Sem coorte contrafactual acompanhada; comparação com/sem filtros não estimada.",
        },
    }
