"""Why-no-trade report: uptime from real cycles, blockers once per token."""

from app.db import Decision, Evaluation
from app.funnel import funnel_report
from app.schemas import now


def test_uptime_gaps_and_blockers(db):
    t = now()
    with db.session() as s:
        # 1 h running (cycles every 90 s), then 5 h off, then 10 min running until now.
        for k in range(41):
            s.add(Decision(at=t - 6.2 * 3600 + k * 90, data={"worth_trading": False}))
        for k in range(7):
            s.add(Decision(at=t - 600 + k * 90, data={"worth_trading": False}))
        s.add(
            Decision(
                at=t - 60,
                data={
                    "worth_trading": False,
                    "execution": {"status": "rejected", "reason": "Liquidez insuficiente"},
                },
            )
        )
        rejected = {"approved": False}
        for key, reasons in {
            "a": ["Market cap confirmado: desconhecido"],
            "b": ["Market cap confirmado: desconhecido", "Idade do pool fora da faixa"],
            "c": ["Sem evidência de vendas nos últimos 5 min"],
        }.items():
            # Re-evaluated many times: must still count once per token.
            for k in range(5):
                s.add(
                    Evaluation(
                        token_key="solana:" + key,
                        at=t - k * 90,
                        data=rejected | {"reasons": reasons},
                        security={},
                    )
                )
        s.add(Evaluation(token_key="solana:d", at=t, data={"approved": True, "reasons": []}, security={}))
    r = funnel_report(db, 48)
    assert 1.0 <= r["running_hours"] <= 1.3
    assert r["offline_periods"] == 1 and r["longest_offline_hours"] >= 4.9
    assert r["tokens_evaluated"] == 4 and r["tokens_approved"] == 1 and r["entries"] == 0
    assert r["blockers"][0] == {"reason": "Market cap confirmado: desconhecido", "tokens": 2}
    assert {"reason": "Sem evidência de vendas nos últimos 5 min", "tokens": 1} in r["only_blocker"]
    assert r["entry_blocks"] == [{"reason": "Liquidez insuficiente", "times": 1}]


def test_empty_history(db):
    r = funnel_report(db, 48)
    assert r["cycles"] == 0 and r["running_hours"] == 0 and r["offline_now_minutes"] is None
