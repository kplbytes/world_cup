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
    simulation_iterations: int = 50_000
    simulation_seed: int = 20260613
    enable_numerical_adjustments: bool = False
    # Data provider tokens
    api_football_token: str = ""
    sportmonks_token: str = ""

    # AI provider API keys
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    xiaomi_api_key: str = ""
    xiaomi_base_url: str = "https://api.xiaomimimo.com/v1"

    # AI prediction settings
    app_mode: str = "local"
    enable_ai_prediction: bool = False
    ai_temperature: float = 0.0
    ai_timeout_seconds: int = 30
    ai_max_retries: int = 2
    ai_prompt_version: str = "worldcup-ai-v1"
    ai_run_mode: str = "manual"
    ai_max_concurrent_requests: int = 2
    ai_run_all_max_limit: int = 20

    # Local workflow settings
    auto_run_daily_workflow_on_open: bool = True
    auto_run_ai_on_open: bool = True
    workflow_auto_run_cooldown_minutes: int = 60
    workflow_default_hours: int = 48
    workflow_default_since_hours: int = 24
    workflow_default_limit: int = 10
    workflow_default_lock_window_minutes: int = 45

    @field_validator("database_path")
    @classmethod
    def resolve_database_path(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return (PROJECT_ROOT / value).resolve()


settings = Settings()
