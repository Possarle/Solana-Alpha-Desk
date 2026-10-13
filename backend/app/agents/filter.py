from ..schemas import Market, now
from ..thresholds import Thresholds


def arithmetic_filter(m: Market, t: Thresholds) -> list[str]:
    reasons = []
    checks = [
        (m.liquidity, t.min_liquidity, "Liquidez"),
        (m.volume_24h, t.min_volume_24h, "Volume 24h"),
        (m.market_cap, t.min_market_cap, "Market cap confirmado"),
    ]
    for value, minimum, label in checks:
        if value is None:
            reasons.append(f"{label}: desconhecido")
        elif value < minimum:
            reasons.append(f"{label} abaixo do mínimo")
    if m.market_cap is not None and m.market_cap > t.max_market_cap:
        reasons.append("Market cap acima do máximo")
    if m.created_at is None:
        reasons.append("Idade do pool desconhecida")
    else:
        age = (now() - m.created_at) / 60
        if age < t.min_age_minutes or age > t.max_age_hours * 60:
            reasons.append("Idade do pool fora da faixa")
    if m.buys_24h is None or m.sells_24h is None:
        reasons.append("Contagem de transações desconhecida")
    elif m.buys_24h + m.sells_24h < t.min_transactions_24h:
        reasons.append("Poucas transações em 24h")
    if m.price is None or m.price <= 0:
        reasons.append("Preço indisponível")
    if m.age_seconds() > t.max_data_age_seconds:
        reasons.append("Dados de mercado antigos")
    return reasons


def trades_filter(m: Market) -> list[str]:
    if m.sells_5m is None or m.buys_5m is None:
        return ["Fluxo recente desconhecido"]
    if m.sells_5m == 0:
        return ["Sem evidência de vendas nos últimos 5 min"]
    if m.buys_5m > 10 * max(m.sells_5m, 1):
        return ["Desequilíbrio extremo entre compras e vendas"]
    return []
