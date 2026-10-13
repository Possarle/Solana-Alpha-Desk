from ..schemas import Market, now
from ..thresholds import Thresholds


def exit_reason(p, m: Market, t: Thresholds, halted: bool, daily_breach: bool) -> str | None:
    if daily_breach:
        return "DAILY_LOSS_LIMIT"
    if halted:
        return "EMERGENCY_STOP"
    if not m.price or m.age_seconds() > t.max_data_age_seconds:
        return "STALE_PRICE"
    change = (m.price / float(p.entry) - 1) * 100
    if change <= -t.stop_loss_pct:
        return "STOP_LOSS"
    if change >= t.take_profit_pct:
        return "TAKE_PROFIT"
    if m.price <= float(p.peak) * (1 - t.trailing_stop_pct / 100):
        return "TRAILING_STOP"
    if now() - p.opened_at >= t.max_hold_minutes * 60:
        return "MAX_HOLD_TIME"
    if m.liquidity is None:
        return "UNKNOWN_LIQUIDITY"
    if m.liquidity <= p.data["entry_liquidity"] * (1 - t.liquidity_drop_pct / 100):
        return "LIQUIDITY_DROP"
    if m.change_5m is not None and m.change_5m < -20:
        return "MARKET_DETERIORATION"
    return None
