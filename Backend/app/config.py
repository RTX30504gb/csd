"""Application configuration loaded from environment variables."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Base RPC
    base_rpc_url: str = "https://mainnet.base.org"
    base_chain_id: int = 8453

    # Listener
    block_poll_interval: float = 2.0
    # If set and the persisted checkpoint is below this, start from it
    # anyway. Useful for smoke tests, backfills, and not replaying the
    # entire chain after a wipe. Default: 0 (no override).
    listener_start_block: int = 0

    # PostgreSQL (async driver)
    database_url: str = "postgresql+asyncpg://rug:rug@localhost:5432/rug"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # App
    app_env: str = "dev"
    log_level: str = "INFO"

    # Display
    verbose: bool = False
    progress_bar: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
