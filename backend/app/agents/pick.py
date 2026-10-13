from ..schemas import Judgment, Market


def pick(candidates: list[tuple[Market, Judgment]]) -> dict:
    ranked = sorted(candidates, key=lambda pair: pair[1].score, reverse=True)
    approved = [p for p in ranked if p[1].approved]
    return {
        "best_candidate": "solana:" + ranked[0][0].address if ranked else None,
        "worth_trading": bool(approved),
        "selected": "solana:" + approved[0][0].address if approved else None,
        "message": "Candidato aprovado; aguardando autorização de risco"
        if approved
        else "NO TRADE — Nenhuma oportunidade aprovada.",
    }
