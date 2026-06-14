"""Base interface for AI prediction providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AIProviderConfig:
    """Configuration for a single AI provider."""
    provider_name: str
    enabled: bool
    api_key_env: str
    base_url_env: str
    default_timeout_seconds: int
    default_temperature: float
    max_retries: int


@dataclass(frozen=True)
class AIModelConfig:
    """Configuration for a single model within a provider."""
    provider_name: str
    model_id: str
    enabled: bool
    model_version: str
    display_name: str
    cost_tier: str  # low, medium, high
    latency_tier: str  # fast, medium, slow
    role: str  # fast_baseline, reasoning_strong, etc.
    ensemble_weight: float = 1.0  # relative weight within the AI pool
    prompt_version: str = "worldcup-ai-v1"
    include_in_ensemble: bool = True  # shadow models set this to False


@dataclass
class AIPredictionRequest:
    """Input to an AI prediction model."""
    match_id: str
    stage: str
    group: str | None
    knockout_round: str | None
    home_team: str
    away_team: str
    kickoff: str
    venue: str | None
    neutral_ground: bool
    system_home_win: float
    system_draw: float
    system_away_win: float
    system_home_xg: float
    system_away_xg: float
    system_model_confidence: float
    system_data_confidence: float
    most_likely_score: str
    market_home_prob: float | None
    market_draw_prob: float | None
    market_away_prob: float | None
    market_divergence: float | None
    market_provider: str | None
    market_fetched_at: str | None
    injuries: list[dict[str, Any]] = field(default_factory=list)
    suspensions: list[dict[str, Any]] = field(default_factory=list)
    lineup_status: dict[str, Any] = field(default_factory=dict)
    roster_warning: str | None = None
    data_completeness: float | None = None
    numerical_adjustment: dict[str, Any] | None = None
    risk_flags: list[str] = field(default_factory=list)
    group_standing_context: str | None = None
    knockout_context: str | None = None
    historical_score_summary: str | None = None
    home_team_profile: dict[str, Any] | None = None
    away_team_profile: dict[str, Any] | None = None


@dataclass
class AIPredictionResult:
    """Parsed output from an AI prediction model."""
    home_win: float
    draw: float
    away_win: float
    confidence: float
    risk_flags: list[str] = field(default_factory=list)
    key_factors: list[str] = field(default_factory=list)
    reason: str = ""
    uncertainties: list[str] = field(default_factory=list)
    disagreement_with_system: str = ""
    disagreement_with_market: str = ""
    recommended_label: str = "uncertain"
    # Parse metadata
    parse_warnings: list[str] = field(default_factory=list)


@dataclass
class AIPredictionError:
    """Error result from an AI prediction call."""
    error_code: str
    error_message: str
    raw_response: str | None = None
    is_retryable: bool = False


class AIProviderBase(ABC):
    """Abstract base class for AI prediction providers."""

    def __init__(self, provider_config: AIProviderConfig) -> None:
        self.config = provider_config

    @abstractmethod
    async def predict(
        self,
        model_config: AIModelConfig,
        prompt: str,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> tuple[str | None, AIPredictionError | None, int]:
        """Call the AI model and return (raw_response_text, error, latency_ms).

        Returns:
            Tuple of (raw_response, error, latency_ms).
            If error is not None, raw_response may still contain partial data.
        """
        ...

    def get_api_key(self) -> str | None:
        """Get API key from settings, return None if not set.

        Uses getattr to auto-discover settings fields, so adding a new
        provider only requires adding the field to Settings + .env.
        """
        from app.config import settings
        # Convention: api_key_env="DEEPSEEK_API_KEY" → settings.deepseek_api_key
        attr = self.config.api_key_env.lower()
        value = getattr(settings, attr, "")
        return value or None

    def get_base_url(self) -> str | None:
        """Get base URL from settings, return None if not set."""
        from app.config import settings
        attr = self.config.base_url_env.lower()
        value = getattr(settings, attr, "")
        return value or None

    def is_configured(self) -> bool:
        """Check if the provider has required credentials."""
        return self.get_api_key() is not None and len(self.get_api_key() or "") > 0
