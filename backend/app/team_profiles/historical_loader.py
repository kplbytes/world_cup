"""Load real historical match data into TeamProfileMatchHistory for profile computation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import HistoricalMatch, Team, TeamProfileMatchHistory, TeamRating
from app.team_profiles.feature_engineering import classify_opponent_tier

logger = logging.getLogger(__name__)

HISTORICAL_PROFILE_SOURCE = "historical_profile_v1"


def _classify_competition(competition_type: str, competition: str) -> tuple[bool, bool, bool]:
    """Return (is_world_cup, is_qualifier, is_friendly)."""
    ct = competition_type.lower()
    comp = competition.lower()
    if "world_cup" in ct or "world_cup" in comp or "world cup" in comp:
        return True, False, False
    if "qualifier" in ct or "qualifying" in ct:
        return False, True, False
    if "friendly" in ct or "international" in ct:
        return False, False, True
    return False, False, False


def _classify_stage(competition: str, competition_type: str, went_to_extra_time: bool) -> str:
    """Classify match stage as 'knockout' or 'group'."""
    comp = competition.lower()
    ct = competition_type.lower()
    if "knockout" in ct or "round of 16" in ct or "quarter" in ct or "semi" in ct or "final" in ct:
        return "knockout"
    if "group" in ct or "group" in comp:
        return "group"
    if went_to_extra_time:
        return "knockout"
    return "group"


def load_historical_match_history(
    session: Session,
    as_of: datetime | None = None,
) -> dict:
    """Load real match history from historical_matches into TeamProfileMatchHistory.

    Only uses matches where:
    - Both team_ids are mapped (not NULL)
    - score_scope = 'full_90min'
    - available_at <= as_of (if provided)

    Returns summary dict with counts.
    """
    cutoff = as_of or datetime.now(timezone.utc)

    # Delete existing historical_profile_v1 rows first
    session.execute(
        delete(TeamProfileMatchHistory)
        .where(TeamProfileMatchHistory.source == HISTORICAL_PROFILE_SOURCE)
    )
    session.flush()

    # Build set of valid team IDs (only World Cup teams in the teams table)
    # Historical matches use "ht_" prefixed IDs (e.g., ht_BRA), teams table uses bare IDs (e.g., BRA)
    valid_team_ids = set(session.scalars(select(Team.id)))
    # Build mapping from historical ht_ IDs to team table IDs
    hist_id_to_team_id: dict[str, str] = {}
    for tid in valid_team_ids:
        hist_id_to_team_id[f"ht_{tid}"] = tid
    # Also include bare IDs in case some matches use them directly
    for tid in valid_team_ids:
        hist_id_to_team_id[tid] = tid

    # Query mapped historical matches where BOTH teams can be resolved to teams table
    query = (
        select(HistoricalMatch)
        .where(HistoricalMatch.home_team_id.in_(hist_id_to_team_id.keys()))
        .where(HistoricalMatch.away_team_id.in_(hist_id_to_team_id.keys()))
        .where(HistoricalMatch.score_scope == "full_90min")
    )
    if as_of:
        query = query.where(HistoricalMatch.available_at <= as_of)

    matches = list(session.scalars(query.order_by(HistoricalMatch.kickoff)))

    # Get latest Elo for each team for opponent_tier classification
    team_elos: dict[str, float] = {}
    for team in session.scalars(select(Team)):
        rating = session.scalar(
            select(TeamRating)
            .where(TeamRating.team_id == team.id)
            .order_by(TeamRating.effective_date.desc())
            .limit(1)
        )
        team_elos[team.id] = rating.elo if rating else 1500.0

    # Build team lookup for names
    teams_by_id = {t.id: t for t in session.scalars(select(Team))}

    count = 0
    teams_with_data: set[str] = set()

    for m in matches:
        kickoff = m.kickoff
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        match_date = kickoff.date()

        # Map historical IDs (ht_BRA) to team table IDs (BRA)
        home_tid = hist_id_to_team_id[m.home_team_id]
        away_tid = hist_id_to_team_id[m.away_team_id]

        is_wc, is_qual, is_friendly = _classify_competition(m.competition_type, m.competition)
        stage = _classify_stage(m.competition, m.competition_type, bool(m.went_to_extra_time))

        # Use 90min scores if available, otherwise full-time scores
        home_score = m.home_score_90min if m.home_score_90min is not None else m.home_score
        away_score = m.away_score_90min if m.away_score_90min is not None else m.away_score

        # Determine home/away
        is_neutral = bool(m.neutral_venue)

        # Home team perspective
        home_result = "win" if home_score > away_score else ("draw" if home_score == away_score else "loss")
        home_points = 3 if home_result == "win" else (1 if home_result == "draw" else 0)
        away_elo = team_elos.get(away_tid, 1500.0)

        session.add(TeamProfileMatchHistory(
            team_id=home_tid,
            match_date=match_date,
            competition=m.competition,
            stage=stage,
            opponent_team_id=away_tid,
            opponent_name=teams_by_id[away_tid].short_name,
            opponent_elo=away_elo,
            opponent_tier=classify_opponent_tier(away_elo),
            is_neutral=is_neutral,
            is_home=not is_neutral,  # Home team is "home" only if not neutral venue
            goals_for=home_score,
            goals_against=away_score,
            result=home_result,
            points=home_points,
            is_world_cup=is_wc,
            is_qualifier=is_qual,
            is_friendly=is_friendly,
            source=HISTORICAL_PROFILE_SOURCE,
        ))
        count += 1
        teams_with_data.add(home_tid)

        # Away team perspective
        away_result = "win" if away_score > home_score else ("draw" if away_score == home_score else "loss")
        away_points = 3 if away_result == "win" else (1 if away_result == "draw" else 0)
        home_elo = team_elos.get(home_tid, 1500.0)

        session.add(TeamProfileMatchHistory(
            team_id=away_tid,
            match_date=match_date,
            competition=m.competition,
            stage=stage,
            opponent_team_id=home_tid,
            opponent_name=teams_by_id[home_tid].short_name,
            opponent_elo=home_elo,
            opponent_tier=classify_opponent_tier(home_elo),
            is_neutral=is_neutral,
            is_home=False,
            goals_for=away_score,
            goals_against=home_score,
            result=away_result,
            points=away_points,
            is_world_cup=is_wc,
            is_qualifier=is_qual,
            is_friendly=is_friendly,
            source=HISTORICAL_PROFILE_SOURCE,
        ))
        count += 1
        teams_with_data.add(away_tid)

    session.flush()

    return {
        "rows_inserted": count,
        "matches_processed": len(matches),
        "teams_with_data": len(teams_with_data),
        "source": HISTORICAL_PROFILE_SOURCE,
    }
