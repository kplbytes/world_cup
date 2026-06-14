"""AI prediction schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AIPredictionInput:
    """Structured input for AI prediction."""
    match_id: str
    model_version: str
    prompt: str
    prompt_version: str = "worldcup-ai-v1"


@dataclass
class AIParsedOutput:
    """Parsed AI output with validation metadata."""
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
    profile_factors: list[str] = field(default_factory=list)
    profile_risk_flags: list[str] = field(default_factory=list)
    # Validation metadata
    was_normalized: bool = False
    confidence_was_default: bool = False
    parse_warnings: list[str] = field(default_factory=list)


@dataclass
class AIErrorRecord:
    """Record of an AI prediction error."""
    error_code: str
    error_message: str
    is_retryable: bool = False
