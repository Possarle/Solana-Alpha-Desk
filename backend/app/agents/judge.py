import httpx
from pydantic import BaseModel, Field

from ..schemas import Judgment, Market, SecurityResult
from ..thresholds import Thresholds
from .filter import arithmetic_filter, market_checks, trades_filter


class LocalInference(BaseModel):
    observations: list[str] = Field(default_factory=list, max_length=5)


def judge(m: Market, s: SecurityResult, t: Thresholds, social: dict, smart: dict) -> Judgment:
    reasons = arithmetic_filter(m, t) + trades_filter(m)
    critical = list(s.critical)
    unknown = list(s.unknown)
    if not s.authority_checked:
        reasons.append("Permissões on-chain não verificadas")
    if not s.concentration_resolved:
        unknown.append("Concentração de holders não resolvida")
        if t.require_resolved_concentration:
            reasons.append("Concentração não resolvida: política estrita")
    if s.top10_pct is not None and s.top10_pct > t.max_top10_pct:
        critical.append("Concentração top 10 acima do limite")
    market = min(
        100,
        (m.liquidity or 0) / max(t.min_liquidity, 1) * 30
        + (m.volume_24h or 0) / max(t.min_volume_24h, 1) * 15,
    )
    momentum = max(
        0, min(100, 40 + (m.change_5m or 0) * 2 + min(30, (m.buys_5m or 0) / max(m.sells_5m or 0, 1) * 10))
    )
    safety = 0 if critical or not s.authority_checked else 80 if s.concentration_resolved else 45
    concentration = max(0, 100 - s.top10_pct) if s.top10_pct is not None else None
    score = round(market * 0.35 + safety * 0.35 + momentum * 0.2 + (concentration or 0) * 0.1, 1)
    # Social/manual and wallet signals are contextual only; they cannot override risk gates.
    if score < t.min_score:
        reasons.append("Score abaixo do mínimo")
    reasons.extend(critical)
    approved = not reasons
    if approved:
        reasons = ["Filtros aritméticos e permissões avaliadas; candidato shadow sujeito ao risco"]
    return Judgment(
        market=market,
        safety=safety,
        momentum=momentum,
        concentration=concentration,
        social=social.get("score"),
        smart_money=smart.get("score"),
        score=score,
        approved=approved,
        risk="critical" if critical else "high" if unknown else "moderate",
        reasons=reasons,
        critical=critical,
        unknown=unknown,
        observed={
            "thresholds": t.model_dump(),
            "security_checked_at": s.checked_at,
            "price": m.price,
            "source": m.source,
            "observed_at": m.observed_at,
            "liquidity": m.liquidity,
            "buys_5m": m.buys_5m,
            "sells_5m": m.sells_5m,
            "buy_acceleration": (m.buys_5m * 12 / m.buys_1h) if m.buys_5m is not None and m.buys_1h else None,
            "smart_wallets": smart.get("wallets", []),
        },
        rules=[
            "market×0.35 + safety×0.35 + momentum×0.20 + concentration×0.10",
            "FDV não substitui market cap",
            "Vendas recentes não garantem saída",
            "Score heurístico; não representa probabilidade de lucro",
        ],
        strategy=m.discovered_by,
        checks=[{k: v for k, v in c.items() if k != "reason"} for c in market_checks(m, t)]
        + [
            {
                "stage": "scan",
                "label": "Fluxo de compras e vendas",
                "observed": m.sells_5m,
                "state": "unknown"
                if m.buys_5m is None or m.sells_5m is None
                else "fail"
                if trades_filter(m)
                else "pass",
                "rule": "Vendas > 0; compras <= 10×vendas; não garante saída",
            },
            {
                "stage": "vet",
                "label": "Mint authority",
                "observed": s.mint_authority,
                "state": "unknown" if not s.authority_checked else "fail" if s.mint_authority else "pass",
                "rule": "Permissão revogada",
            },
            {
                "stage": "vet",
                "label": "Freeze authority",
                "observed": s.freeze_authority,
                "state": "unknown" if not s.authority_checked else "fail" if s.freeze_authority else "pass",
                "rule": "Permissão revogada",
            },
            {
                "stage": "vet",
                "label": "Concentração top 10 holders",
                "observed": s.top10_pct,
                "state": "unknown"
                if not s.concentration_resolved
                else "fail"
                if s.top10_pct is None or s.top10_pct > t.max_top10_pct
                else "pass",
                "rule": f"<= {t.max_top10_pct}%; política "
                + ("estrita" if t.require_resolved_concentration else "pesquisa shadow com incerteza"),
            },
            {
                "stage": "judge",
                "label": "Score mínimo",
                "observed": score,
                "state": "pass" if score >= t.min_score else "fail",
                "rule": f">= {t.min_score}; não é probabilidade",
            },
        ],
    )


async def explain_with_ollama(url: str, model: str, judgment: Judgment) -> list[str]:
    if not model:
        return []
    # Only structured numerical evidence; untrusted project/social prose is excluded.
    payload = {
        "model": model,
        "stream": False,
        "format": LocalInference.model_json_schema(),
        "prompt": "Resuma em português até 3 inferências incertas. Não dê ordens. Dados: "
        + str(
            {
                "scores": [judgment.market, judgment.safety, judgment.momentum],
                "approved": judgment.approved,
                "unknown_count": len(judgment.unknown),
            }
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(url.rstrip("/") + "/api/generate", json=payload)
            response.raise_for_status()
            return LocalInference.model_validate_json(response.json()["response"]).observations
    except Exception:
        return ["Modelo local indisponível; decisão determinística preservada."]
