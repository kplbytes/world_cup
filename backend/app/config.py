from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_path: Path = PROJECT_ROOT / "data" / "world-cup.sqlite3"
    football_data_api_token: str | None = None
    refresh_interval_minutes: int = 15
    live_refresh_interval_minutes: int = 2
    snapshot_lock_interval_minutes: int = 1
    enable_scheduled_refresh: bool = False
    simulation_iterations: int = 50_000
    simulation_seed: int = 20260613
    enable_numerical_adjustments: bool = False
    # Data provider tokens
    api_football_token: str | None = None
    sportmonks_token: str | None = None

    # AI provider API keys
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    xiaomi_api_key: str | None = None
    xiaomi_base_url: str = "https://api.xiaomimimo.com/v1"

    # Admin API key for write-endpoint authentication (empty = auth disabled)
    admin_api_key: str = ""

    # AI prediction settings
    app_mode: str = "local"
    enable_ai_prediction: bool = True
    ai_temperature: float = 0.0
    ai_timeout_seconds: int = 30
    ai_max_retries: int = 2
    ai_prompt_version: str = "worldcup-ai-v1"
    ai_run_mode: str = "manual"
    ai_max_concurrent_requests: int = 2
    ai_run_all_max_limit: int = 20

    # CORS allowed origins (comma-separated, default "*" for local dev)
    cors_allowed_origins: str = "*"

    # Local workflow settings
    auto_run_daily_workflow_on_open: bool = False
    auto_run_ai_on_open: bool = False
    workflow_auto_run_cooldown_minutes: int = 60
    workflow_default_hours: int = 48
    workflow_default_since_hours: int = 24
    workflow_default_limit: int = 10
    workflow_default_lock_window_hours: int = 24

    @field_validator("database_path")
    @classmethod
    def resolve_database_path(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return (PROJECT_ROOT / value).resolve()

    @field_validator("app_mode")
    @classmethod
    def validate_app_mode(cls, value: str) -> str:
        allowed = {"local", "test", "production"}
        if value not in allowed:
            raise ValueError(f"app_mode must be one of {allowed}, got '{value}'")
        return value

    @field_validator("ai_run_mode")
    @classmethod
    def validate_ai_run_mode(cls, value: str) -> str:
        allowed = {"manual", "auto"}
        if value not in allowed:
            raise ValueError(f"ai_run_mode must be one of {allowed}, got '{value}'")
        return value


settings = Settings()
