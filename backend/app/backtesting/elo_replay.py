"""Elo historical replay engine - no future data leakage."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

from app.prediction.elo import update_elo


# Default K-factors by competition type
DEFAULT_K_FACTORS = {
    "friendly": 20.0,
    "qualifier": 30.0,
    "continental_qualifier": 30.0,
    "continental": 35.0,
    "world_cup": 40.0,
    "other": 25.0,
}

# Home advantage in Elo points (0 for neutral, positive for home advantage)
DEFAULT_HOME_ADVANTAGE = 60.0
INITIAL_RATING = 1500.0


@dataclass
class ReplayStep:
    """One step in the Elo replay - prediction then update."""
    source_match_id: str
    available_at: datetime
    home_team_id: str
    away_team_id: str
    home_team_raw: str
    away_team_raw: str
    home_score: int
    away_score: int
    competition_type: str
    neutral_venue: bool
    score_scope: str
    # Pre-match Elo (for prediction)
    pre_match_home_elo: float
    pre_match_away_elo: float
    elo_diff: float  # home - away
    # Update info
    update_weight: float
    home_advantage_used: float
    as_of: datetime


@dataclass
class EloReplayResult:
    """Result of an Elo replay over a dataset."""
    steps: list[ReplayStep] = field(default_factory=list)
    final_ratings: dict[str, float] = field(default_factory=dict)
    initial_rating: float = INITIAL_RATING


def replay_elo_history(
    matches: list,  # list of HistoricalMatch objects
    k_factors: dict[str, float] | None = None,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    initial_rating: float = INITIAL_RATING,
    skip_non_90min: bool = True,
) -> EloReplayResult:
    """Replay Elo ratings over historical matches in chronological order.

    For each match:
    1. Record pre-match Elo (for prediction)
    2. Compute expected result
    3. Update Elo with actual result

    This ensures no future data leakage: each prediction only uses
    Elo ratings built from earlier matches.

    Args:
        matches: HistoricalMatch objects, must be sorted by available_at
        k_factors: K-factor by competition_type
        home_advantage: Elo points added for home advantage (non-neutral)
        initial_rating: Starting Elo for new teams
        skip_non_90min: Skip matches with score_scope != "full_90min"

    Returns:
        EloReplayResult with all steps and final ratings
    """
    if k_factors is None:
        k_factors = DEFAULT_K_FACTORS

    ratings: dict[str, float] = {}
    steps: list[ReplayStep] = []

    # Sort by available_at (chronological order)
    sorted_matches = sorted(matches, key=lambda m: m.available_at)

    for match in sorted_matches:
        # Skip non-90min matches for Elo updates
        if skip_non_90min and match.score_scope != "full_90min":
            continue

        # Skip unmapped matches
        if match.is_unmapped:
            continue

        # Skip matches without team IDs
        if not match.home_team_id or not match.away_team_id:
            continue

        # Get pre-match Elo
        home_elo = ratings.get(match.home_team_id, initial_rating)
        away_elo = ratings.get(match.away_team_id, initial_rating)

        # Determine K-factor
        k = k_factors.get(match.competition_type, k_factors.get("other", 25.0))

        # Determine home advantage
        ha = 0.0 if match.neutral_venue else home_advantage

        # Record pre-match state
        step = ReplayStep(
            source_match_id=match.source_match_id,
            available_at=match.available_at,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            home_team_raw=match.home_team_raw,
            away_team_raw=match.away_team_raw,
            home_score=match.home_score,
            away_score=match.away_score,
            competition_type=match.competition_type,
            neutral_venue=match.neutral_venue,
            score_scope=match.score_scope,
            pre_match_home_elo=home_elo,
            pre_match_away_elo=away_elo,
            elo_diff=home_elo - away_elo,
            update_weight=k,
            home_advantage_used=ha,
            as_of=match.available_at,
        )
        steps.append(step)

        # Update Elo with actual result
        result = update_elo(
            home_elo, away_elo,
            match.home_score, match.away_score,
            weight=k,
            home_advantage=ha,
        )
        ratings[match.home_team_id] = result.home
        ratings[match.away_team_id] = result.away

    return EloReplayResult(
        steps=steps,
        final_ratings=ratings,
        initial_rating=initial_rating,
    )
