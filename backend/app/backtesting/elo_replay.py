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


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is UTC-aware. SQLite returns naive datetimes."""
    if dt is None:
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
    warmup_cutoff: datetime | None = None,
) -> EloReplayResult:
    """Replay Elo ratings over historical matches in chronological order.

    Same-timestamp matches are grouped: all predictions in a group use the same
    pre-match Elo, then all Elo updates are applied together. This prevents
    same-day match order from affecting predictions.

    Args:
        matches: HistoricalMatch objects, must be sorted by available_at
        k_factors: K-factor by competition_type
        home_advantage: Elo points added for home advantage (non-neutral)
        initial_rating: Starting Elo for new teams
        skip_non_90min: Skip matches with score_scope != "full_90min"
        warmup_cutoff: If provided, matches before this date are used for Elo
            warm-up only (their ReplaySteps are NOT included in results).
    """
    if k_factors is None:
        k_factors = DEFAULT_K_FACTORS

    # Normalize SQLite naive datetimes to UTC-aware
    for m in matches:
        if m.available_at and m.available_at.tzinfo is None:
            m.available_at = m.available_at.replace(tzinfo=timezone.utc)

    ratings: dict[str, float] = {}
    steps: list[ReplayStep] = []

    # Sort by available_at
    sorted_matches = sorted(matches, key=lambda m: m.available_at)

    # Group by available_at
    from itertools import groupby
    groups = []
    for key, group in groupby(sorted_matches, key=lambda m: m.available_at):
        groups.append((key, list(group)))

    for available_at, group_matches in groups:
        # Phase 1: Record pre-match Elo for all matches in this group
        group_steps = []
        for match in group_matches:
            if skip_non_90min and match.score_scope != "full_90min":
                continue
            if match.is_unmapped:
                continue
            if not match.home_team_id or not match.away_team_id:
                continue

            home_elo = ratings.get(match.home_team_id, initial_rating)
            away_elo = ratings.get(match.away_team_id, initial_rating)
            k = k_factors.get(match.competition_type, k_factors.get("other", 25.0))
            ha = 0.0 if match.neutral_venue else home_advantage

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
                as_of=available_at,
            )
            group_steps.append(step)

        # Phase 2: Update Elo for all matches in this group
        for step in group_steps:
            result = update_elo(
                step.pre_match_home_elo, step.pre_match_away_elo,
                step.home_score, step.away_score,
                weight=step.update_weight,
                home_advantage=step.home_advantage_used,
            )
            ratings[step.home_team_id] = result.home
            ratings[step.away_team_id] = result.away

        # Add steps to results (unless warmup)
        if warmup_cutoff is None or available_at >= warmup_cutoff:
            steps.extend(group_steps)

    return EloReplayResult(
        steps=steps,
        final_ratings=ratings,
        initial_rating=initial_rating,
    )
