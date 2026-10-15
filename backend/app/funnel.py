"""Why no trade? Uptime and blocking rules, from the robot's own history.

Uptime is inferred from completed cycles: two consecutive cycles closer than
GAP seconds count as continuous running. Blocking rules come from stored
evaluations, counted once per token per reason so a token re-checked every
cycle does not dominate. Nothing here is estimated or synthetic.
"""

from collections import Counter

from sqlalchemy import select

from .db import Decision, Evaluation, Position
from .schemas import now

GAP = 600  # seconds; scan interval is ~90 s after each cycle


def _normalize(reason: str) -> str:
    # Group numeric variants ("Revalidação: ...") under their rule name.
    return reason.split(":")[0].strip() if reason.startswith("Revalidação") else reason.strip()


def funnel_report(db, hours: int = 48) -> dict:
    t = now()
    start = t - hours * 3600
    with db.session() as s:
        cycles = s.scalars(select(Decision.at).where(Decision.at >= start).order_by(Decision.at)).all()
        evaluations = s.scalars(
            select(Evaluation).where(Evaluation.at >= start).order_by(Evaluation.at.desc())
        ).all()
        opened = s.scalars(select(Position).where(Position.opened_at >= start)).all()
        decisions = s.scalars(
            select(Decision).where(Decision.at >= start).order_by(Decision.at.desc()).limit(500)
        ).all()

    running = 0.0
    gaps = []
    for a, b in zip(cycles, cycles[1:], strict=False):
        if b - a <= GAP:
            running += b - a
        else:
            gaps.append(b - a)
    offline_now = (t - cycles[-1]) if cycles else None
    if offline_now is not None and offline_now > GAP:
        gaps.append(offline_now)

    tokens: dict[str, dict] = {}
    for ev in evaluations:  # newest first: keep each token's latest verdict
        tokens.setdefault(ev.token_key, ev.data)
    blockers: Counter = Counter()
    single: Counter = Counter()
    for data in tokens.values():
        if data.get("approved"):
            continue
        reasons = {_normalize(r) for r in data.get("reasons", [])}
        blockers.update(reasons)
        if len(reasons) == 1:
            single.update(reasons)

    entry_blocks: Counter = Counter()
    for d in decisions:
        execution = d.data.get("execution") or {}
        if execution.get("status") in ("rejected", "skipped"):
            entry_blocks[_normalize(execution.get("reason", "?"))] += 1

    approved = sum(1 for d in tokens.values() if d.get("approved"))
    return {
        "hours": hours,
        "cycles": len(cycles),
        "running_hours": round(running / 3600, 1),
        "running_minutes": round(running / 60),
        "offline_periods": len(gaps),
        "longest_offline_hours": round(max(gaps) / 3600, 1) if gaps else 0,
        "offline_now_minutes": round(offline_now / 60) if offline_now is not None else None,
        "last_cycle_at": cycles[-1] if cycles else None,
        "tokens_evaluated": len(tokens),
        "tokens_approved": approved,
        "entries": len(opened),
        "blockers": [{"reason": r, "tokens": n} for r, n in blockers.most_common(10)],
        "only_blocker": [{"reason": r, "tokens": n} for r, n in single.most_common(6)],
        "entry_blocks": [{"reason": r, "times": n} for r, n in entry_blocks.most_common(5)],
        "method": f"Últimas {hours} h. Robô considerado ligado entre ciclos com menos de {GAP // 60} min de "
        "intervalo. Bloqueios contados uma vez por token (última avaliação). 'Único bloqueio' = tokens "
        "que só falharam nessa regra.",
    }
