"""API routes for historical match data."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.historical.health import get_data_health
from app.historical.importer import import_historical_matches
from app.historical.quality import run_quality_checks
from app.historical.queries import get_team_match_history
from app.models import Team

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/historical", tags=["historical"])


@router.get("/health")
def historical_health():
    """Get data health information for historical data."""
    with session_scope() as session:
        return get_data_health(session)


@router.post("/import")
def trigger_import(since: str = "2018-01-01"):
    """Trigger historical data import from CSV.

    Only available in development environment or with explicit admin flag.
    Import failures are rolled back atomically.
    """
    if settings.environment == "production":
        raise HTTPException(status_code=403, detail="Import disabled in production environment")
    try:
        with session_scope() as session:
            stats = import_historical_matches(session, since=since)
            return {
                "total_csv_rows": stats.total_csv_rows,
                "filtered_by_date": stats.filtered_by_date,
                "filtered_future": stats.filtered_future,
                "inserted": stats.inserted,
                "skipped_existing": stats.skipped_existing,
                "unmapped_teams": stats.unmapped_teams,
                "penalty_matches": stats.penalty_matches,
                "errors": stats.errors,
                "unmapped_names": sorted(stats.unmapped_names),
                "historical_teams_created": stats.historical_teams_created,
            }
    except Exception as e:
        logger.error("Historical import failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")


@router.get("/quality")
def historical_quality():
    """Run data quality checks on historical data."""
    with session_scope() as session:
        report = run_quality_checks(session)
        return {
            "total_matches": report.total_matches,
            "duplicate_count": report.duplicate_count,
            "future_match_count": report.future_match_count,
            "score_anomaly_count": report.score_anomaly_count,
            "same_team_count": report.same_team_count,
            "unmapped_team_count": report.unmapped_team_count,
            "neutral_venue_missing_world_cup": report.neutral_venue_missing_world_cup,
            "time_regression_count": report.time_regression_count,
            "competition_type_counts": report.competition_type_counts,
            "is_healthy": report.is_healthy,
            "issues": report.issues,
        }


@router.get("/matches/{team_id}")
def team_match_history(team_id: str, as_of: datetime | None = None):
    """Get match history for a specific team."""
    with session_scope() as session:
        team = session.get(Team, team_id)
        if team is None:
            return {"team_id": team_id, "matches": [], "error": "team not found"}

        cutoff = as_of or datetime.now(timezone.utc)
        matches = get_team_match_history(session, team_id, cutoff)

        return {
            "team_id": team_id,
            "team_code": team.code,
            "team_name": team.short_name,
            "as_of": cutoff.isoformat(),
            "match_count": len(matches),
            "matches": [
                {
                    "kickoff": m.kickoff.isoformat(),
                    "competition": m.competition,
                    "competition_type": m.competition_type,
                    "home_team_raw": m.home_team_raw,
                    "away_team_raw": m.away_team_raw,
                    "home_score": m.home_score,
                    "away_score": m.away_score,
                    "neutral_venue": m.neutral_venue,
                    "went_to_penalties": m.went_to_penalties,
                    "is_home": m.home_team_id == team_id,
                }
                for m in matches
            ],
        }
