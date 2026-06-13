from pathlib import Path

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
    simulation_iterations: int = 50_000
    simulation_seed: int = 20260613


settings = Settings()

