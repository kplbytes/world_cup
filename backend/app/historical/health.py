"""Data health API for the frontend to check historical data status."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import HistoricalMatch, Team, TeamProfile, TeamProfileMatchHistory


def get_data_health(session: Session) -> dict:
    """Return data health information for the frontend."""
    now = datetime.now(timezone.utc)

    # Total historical matches
    total_matches = session.scalar(
        select(func.count(HistoricalMatch.id))
    ) or 0

    # Time coverage range
    earliest = session.scalar(
        select(func.min(HistoricalMatch.kickoff))
    )
    latest = session.scalar(
        select(func.max(HistoricalMatch.kickoff))
    )

    # National team coverage (how many of the 48 WC teams have data)
    all_teams = list(session.scalars(select(Team.id)))
    teams_with_data = set()
    if all_teams:
        home_teams = session.scalars(
            select(HistoricalMatch.home_team_id).where(
                HistoricalMatch.home_team_id.in_(all_teams),
                HistoricalMatch.is_unmapped.is_(False),
            )
        )
        away_teams = session.scalars(
            select(HistoricalMatch.away_team_id).where(
                HistoricalMatch.away_team_id.in_(all_teams),
                HistoricalMatch.is_unmapped.is_(False),
            )
        )
        teams_with_data = set(home_teams) | set(away_teams)

    # Last update time
    last_update = session.scalar(
        select(func.max(HistoricalMatch.fetched_at))
    )

    # Unmapped team count
    unmapped_count = session.scalar(
        select(func.count(HistoricalMatch.id)).where(HistoricalMatch.is_unmapped.is_(True))
    ) or 0

    # Mock record count (from TeamProfileMatchHistory where source='seed_mock_v1')
    mock_count = session.scalar(
        select(func.count(TeamProfileMatchHistory.id)).where(
            TeamProfileMatchHistory.source == "seed_mock_v1"
        )
    ) or 0

    # Whether official predictions use real historical data
    # Check if any TeamProfile has source_summary_json indicating real data
    real_profiles = session.scalar(
        select(func.count(TeamProfile.id)).where(
            TeamProfile.source_summary_json.contains("real")
        )
    ) or 0
    mock_profiles = session.scalar(
        select(func.count(TeamProfile.id)).where(
            TeamProfile.source_summary_json.contains("seed_mock_v1")
        )
    ) or 0

    uses_real_data = total_matches > 0 and real_profiles > 0

    # Time precision counts
    date_only_count = session.scalar(
        select(func.count(HistoricalMatch.id)).where(HistoricalMatch.time_precision == "date_only")
    ) or 0
    exact_count = session.scalar(
        select(func.count(HistoricalMatch.id)).where(HistoricalMatch.time_precision == "exact")
    ) or 0

    # Extra time / penalty match count
    excluded_extra_time_count = session.scalar(
        select(func.count(HistoricalMatch.id)).where(HistoricalMatch.score_scope == "after_extra_time_or_unknown")
    ) or 0

    # Score scope breakdown
    score_scope_full_90min = session.scalar(
        select(func.count(HistoricalMatch.id)).where(HistoricalMatch.score_scope == "full_90min")
    ) or 0
    score_scope_after_extra_time = excluded_extra_time_count
    score_scope_unknown = session.scalar(
        select(func.count(HistoricalMatch.id)).where(HistoricalMatch.score_scope == "unknown_score_scope")
    ) or 0

    return {
        "total_historical_matches": total_matches,
        "time_coverage": {
            "earliest": earliest.isoformat() if earliest else None,
            "latest": latest.isoformat() if latest else None,
        },
        "national_team_coverage": {
            "total_teams": len(all_teams),
            "teams_with_data": len(teams_with_data),
            "coverage_rate": len(teams_with_data) / len(all_teams) if all_teams else 0.0,
        },
        "last_update": last_update.isoformat() if last_update else None,
        "unmapped_team_count": unmapped_count,
        "mock_record_count": mock_count,
        "real_profile_count": real_profiles,
        "mock_profile_count": mock_profiles,
        "uses_real_data": uses_real_data,
        "date_only_count": date_only_count,
        "exact_count": exact_count,
        "excluded_extra_time_count": excluded_extra_time_count,
        "score_scope": {
            "full_90min": score_scope_full_90min,
            "after_extra_time_or_unknown": score_scope_after_extra_time,
            "unknown_score_scope": score_scope_unknown,
        },
        "checked_at": now.isoformat(),
    }
