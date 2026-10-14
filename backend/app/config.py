from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./alpha.db"
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    jupiter_api_key: SecretStr = SecretStr("")
    dashboard_token: SecretStr = SecretStr("")
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""
    scan_interval: int = Field(90, ge=30)
    risk_interval: int = Field(15, ge=5)
    wallet_interval: int = Field(120, ge=60)
    enable_scheduler: bool = True
    live_enabled: bool = False
    live_wallet_address: str = ""
    live_keypair_path: str = ""
    live_acknowledgement: str = ""
    live_max_order_usdc: float = Field(10, gt=0, le=100)
    live_max_slippage_bps: int = Field(100, ge=1, le=200)
    live_max_price_impact_pct: float = Field(1, gt=0, le=3)
    live_max_fee_lamports: int = Field(50000, ge=5000, le=500000)
    live_daily_fee_lamports: int = Field(500000, ge=5000, le=5000000)
    live_compute_price_micro_lamports: int = Field(1000, ge=0, le=100000)
    live_review_seconds: int = Field(45, ge=15, le=60)
    allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    )


settings = Settings()
