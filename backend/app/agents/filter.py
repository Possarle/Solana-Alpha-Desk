from ..schemas import Market, now
from ..thresholds import Thresholds


def market_checks(m: Market, t: Thresholds) -> list[dict]:
    """One source of truth for filtering and the visible evidence checklist."""
    age = (now() - m.created_at) / 60 if m.created_at is not None else None
    tx = m.buys_24h + m.sells_24h if m.buys_24h is not None and m.sells_24h is not None else None
    specs = [
        (
            "Liquidez",
            m.liquidity,
            t.min_liquidity,
            None,
            "Liquidez abaixo do mínimo",
            "Liquidez: desconhecido",
        ),
        (
            "Volume 24h",
            m.volume_24h,
            t.min_volume_24h,
            None,
            "Volume 24h abaixo do mínimo",
            "Volume 24h: desconhecido",
        ),
        (
            "Market cap confirmado",
            m.market_cap,
            t.min_market_cap,
            t.max_market_cap,
            "Market cap acima do máximo"
            if m.market_cap is not None and m.market_cap > t.max_market_cap
            else "Market cap confirmado abaixo do mínimo",
            "Market cap confirmado: desconhecido",
        ),
        (
            "Idade do pool (min)",
            age,
            t.min_age_minutes,
            t.max_age_hours * 60,
            "Idade do pool fora da faixa",
            "Idade do pool desconhecida",
        ),
        (
            "Transações 24h",
            tx,
            t.min_transactions_24h,
            None,
            "Poucas transações em 24h",
            "Contagem de transações desconhecida",
        ),
        ("Preço", m.price, 0, None, "Preço indisponível", "Preço indisponível"),
        (
            "Idade dos dados (s)",
            m.age_seconds(),
            0,
            t.max_data_age_seconds,
            "Dados de mercado antigos",
            "Dados de mercado antigos",
        ),
    ]
    result = []
    for label, value, minimum, maximum, failure, missing in specs:
        passed = value is not None and value >= minimum and (maximum is None or value <= maximum)
        if label == "Preço" and not value:
            passed = False
        result.append(
            {
                "stage": "scan",
                "label": label,
                "observed": value,
                "state": "unknown" if value is None else "pass" if passed else "fail",
                "rule": "> 0"
                if label == "Preço"
                else f">= {minimum}" + (f" e <= {maximum}" if maximum is not None else ""),
                "reason": None if passed else missing if value is None else failure,
            }
        )
    return result


def arithmetic_filter(m: Market, t: Thresholds) -> list[str]:
    return [c["reason"] for c in market_checks(m, t) if c["reason"]]


def trades_filter(m: Market) -> list[str]:
    if m.sells_5m is None or m.buys_5m is None:
        return ["Fluxo recente desconhecido"]
    if m.sells_5m == 0:
        return ["Sem evidência de vendas nos últimos 5 min"]
    if m.buys_5m > 10 * max(m.sells_5m, 1):
        return ["Desequilíbrio extremo entre compras e vendas"]
    return []
