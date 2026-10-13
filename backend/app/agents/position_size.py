from ..schemas import Judgment, Market
from ..thresholds import Thresholds


def position_size(equity: float, cash: float, m: Market, j: Judgment, t: Thresholds) -> dict:
    uncertainty_factor = 0.5 if j.unknown else 1.0
    # Exposure capped at 0.05% of reported pool liquidity, additionally to portfolio cap.
    budget = min(equity * t.max_position_pct / 100 * uncertainty_factor, (m.liquidity or 0) * 0.0005, cash)
    slip = max(20.0, budget / max((m.liquidity or 0) / 2, 1) * 10000)
    return {
        "budget": round(max(0, budget), 6),
        "slippage_bps": slip,
        "uncertainty_factor": uncertainty_factor,
        "method": "Menor entre capital×limite×incerteza, liquidez×0.0005 e caixa; slippage heurístico.",
    }
