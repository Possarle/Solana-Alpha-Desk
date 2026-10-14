"""Counterfactual cohort: did the filters pick better tokens than no filter at all?

Every evaluated token with a price is registered at evaluation time, approved or not.
Its pool price is then re-observed at fixed horizons. Only prices actually fetched from
the SAME pool count; a token that vanished from public feeds is "sem dado", never -100%
nor 0%. Gross price change only: costs, slippage and fills are NOT applied here.
"""

import logging
import statistics

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from .db import CohortEntry, Database, Observation
from .schemas import Judgment, Market, now

logger = logging.getLogger("alpha")

HORIZONS = (15, 60, 240)  # minutes


def window(minutes: int) -> tuple[float, float]:
    """Accepted measurement window in seconds after evaluation."""
    start = minutes * 60
    return start, start + max(600, start * 0.25)


class CohortTracker:
    def __init__(self, db: Database):
        self.db = db

    def register(self, m: Market, j: Judgment):
        if not m.price:
            return
        token_key = "solana:" + m.address
        key = f"{token_key}:{'approved' if j.approved else 'rejected'}"
        with self.db.session() as s:
            if s.get(CohortEntry, key) is not None:
                return  # first evaluation per (token, outcome) only; no repeated sampling
            s.add(
                CohortEntry(
                    key=key,
                    token_key=token_key,
                    at=m.observed_at,
                    approved=j.approved,
                    done=False,
                    data={
                        "symbol": m.symbol,
                        "pool": m.pool,
                        "source": m.source,
                        "ref_price": m.price,
                        "score": j.score,
                        "strategy": m.discovered_by,
                        "reason": j.reasons[0] if j.reasons else "",
                        "returns": {},
                    },
                )
            )

    def _due(self, row: CohortEntry, t: float) -> tuple[list[int], bool]:
        """Horizons that can be measured now, and whether any horizon expired unmeasured."""
        due, changed = [], False
        returns = row.data["returns"]
        for h in HORIZONS:
            if str(h) in returns:
                continue
            start, end = window(h)
            if t > row.at + end:
                returns[str(h)] = {"missing": True}
                changed = True
            elif t >= row.at + start:
                due.append(h)
        return due, changed

    @staticmethod
    def _fill(row: CohortEntry, due: list[int], price: float, at: float, origin: str) -> bool:
        filled = False
        for h in due:
            start, end = window(h)
            if row.at + start <= at <= row.at + end and price > 0:
                row.data["returns"][str(h)] = {
                    "ret_pct": (price / row.data["ref_price"] - 1) * 100,
                    "at": at,
                    "origin": origin,
                }
                filled = True
        return filled

    async def tick(self, dex) -> dict:
        t = now()
        with self.db.session() as s:
            rows = s.scalars(
                select(CohortEntry).where(CohortEntry.done.is_(False)).order_by(CohortEntry.at).limit(500)
            ).all()
            pending: dict[str, list[tuple[CohortEntry, list[int]]]] = {}
            for row in rows:
                row.data = dict(row.data, returns=dict(row.data["returns"]))
                due, _ = self._due(row, t)
                if due:
                    # Prices the scanner already collected from the same pool cost no request.
                    obs = s.scalars(
                        select(Observation)
                        .where(
                            Observation.token_key == row.token_key,
                            Observation.at >= row.at + window(min(due))[0],
                            Observation.at <= row.at + window(max(due))[1],
                        )
                        .order_by(Observation.at)
                    ).all()
                    for o in obs:
                        if o.data.get("pool") == row.data["pool"] and o.data.get("price"):
                            self._fill(row, due, o.data["price"], o.at, "scanner")
                    due = [h for h in due if str(h) not in row.data["returns"]]
                if due:
                    pending.setdefault(row.data["pool"], []).append((row, due))
                row.done = len(row.data["returns"]) == len(HORIZONS)
            # One shared DexScreener request (max 30 mints), earliest expiry first.
            order = sorted(pending, key=lambda p: min(r.at + window(min(d))[1] for r, d in pending[p]))[:30]
            mints = list(dict.fromkeys(pending[p][0][0].token_key.removeprefix("solana:") for p in order))
            fetched = 0
            if mints:
                try:
                    markets = await dex.tokens(mints)
                except Exception as exc:  # unavailable source never fabricates a return
                    logger.warning("cohort_fetch:" + type(exc).__name__)
                    markets = []
                for m in markets:
                    for row, due in pending.get(m.pool, []):
                        if m.price and self._fill(row, due, m.price, m.observed_at, "dexscreener_same_pool"):
                            fetched += 1
                        row.done = len(row.data["returns"]) == len(HORIZONS)
            for row in rows:  # in-place JSON edits are invisible to the ORM after an autoflush
                flag_modified(row, "data")
        return {"tracked": len(rows), "requested": len(mints), "filled": fetched}

    def report(self) -> dict:
        with self.db.session() as s:
            rows = s.scalars(select(CohortEntry).order_by(CohortEntry.at.desc()).limit(5000)).all()
        first: dict[str, CohortEntry] = {}
        for row in sorted(rows, key=lambda r: r.at):
            first.setdefault(row.token_key, row)
        groups = {
            "approved": [r for r in rows if r.approved],
            "rejected": [r for r in rows if not r.approved],
            "all_first": list(first.values()),
        }
        result = {}
        for name, items in groups.items():
            result[name] = {"tokens": len(items), "horizons": {}}
            for h in HORIZONS:
                values = [
                    r.data["returns"][str(h)]["ret_pct"]
                    for r in items
                    if "ret_pct" in r.data["returns"].get(str(h), {})
                ]
                missing = sum(1 for r in items if r.data["returns"].get(str(h), {}).get("missing"))
                result[name]["horizons"][str(h)] = {
                    "measured": len(values),
                    "missing": missing,
                    "pending": len(items) - len(values) - missing,
                    "median_pct": statistics.median(values) if values else None,
                    "mean_pct": statistics.fmean(values) if values else None,
                    "positive_pct": sum(v > 0 for v in values) / len(values) * 100 if values else None,
                    "worst_pct": min(values) if values else None,
                }
        enough = result["approved"]["horizons"]["60"]["measured"] >= 10
        return {
            "available": enough,
            "horizons_minutes": list(HORIZONS),
            "groups": result,
            "reason": "Variação bruta do preço no MESMO pool após a avaliação. Não inclui taxas, slippage, "
            "falha de execução nem tokens que sumiram das fontes (contados como 'sem dado')."
            + ("" if enough else " Amostra de aprovados ainda pequena (< 10 medições em 60 min)."),
            "recent": [
                {
                    "token_key": r.token_key,
                    "symbol": r.data["symbol"],
                    "approved": r.approved,
                    "score": r.data["score"],
                    "at": r.at,
                    "reason": r.data["reason"],
                    "returns": {k: v.get("ret_pct") for k, v in r.data["returns"].items()},
                }
                for r in rows[:40]
            ],
        }
