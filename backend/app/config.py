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
    allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    )


settings = Settings()
