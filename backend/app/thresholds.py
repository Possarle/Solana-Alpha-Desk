"""Editable research hypotheses; scores are not calibrated probabilities."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    min_age_minutes: float = Field(15, ge=0, le=4320)
    max_age_hours: float = Field(72, gt=0, le=8760)
    min_liquidity: float = Field(12000, ge=1000)
    min_volume_24h: float = Field(40000, ge=0)
    min_market_cap: float = Field(60000, ge=0)
    max_market_cap: float = Field(8000000, gt=0)
    min_transactions_24h: int = Field(150, ge=1)
    max_top10_pct: float = Field(40, gt=0, le=100)
    min_score: float = Field(65, ge=0, le=100)
    max_position_pct: float = Field(1, gt=0, le=1)
    max_daily_loss_pct: float = Field(3, gt=0, le=10)
    stop_loss_pct: float = Field(12, gt=0, le=90)
    take_profit_pct: float = Field(25, gt=0, le=1000)
    trailing_stop_pct: float = Field(10, gt=0, le=90)
    max_hold_minutes: int = Field(180, ge=1, le=10080)
    liquidity_drop_pct: float = Field(30, gt=0, le=100)
    max_slippage_bps: int = Field(200, ge=10, le=500)
    fee_bps: int = Field(30, ge=0, le=300)
    fixed_cost_usd: float = Field(0.05, ge=0, le=10)
    max_data_age_seconds: int = Field(150, ge=30, le=300)
    rejection_cooldown_seconds: int = Field(300, ge=30, le=86400)
    require_resolved_concentration: bool = True
    auto_shadow: bool = True

    @model_validator(mode="after")
    def valid_ranges(self):
        if self.min_market_cap >= self.max_market_cap:
            raise ValueError("Capitalização mínima deve ser menor que máxima")
        if self.min_age_minutes >= self.max_age_hours * 60:
            raise ValueError("Idade mínima deve ser menor que máxima")
        return self
