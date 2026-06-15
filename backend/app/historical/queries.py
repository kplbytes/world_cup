"""Query interfaces for historical match data with as_of semantics.

Supports both WC teams (teams table) and historical teams (historical_teams table).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HistoricalMatch


def get_historical_matches(
    session: Session,
    as_of: datetime,
    team_ids: list[str] | None = None,
) -> list[HistoricalMatch]:
    """Get historical matches strictly before as_of timestamp.

    Rules:
    - Only returns matches where available_at < as_of (strict less than)
    - available_at accounts for time_precision: date_only records are
      visible the day after the match date; exact records are visible
      immediately after kickoff.
    - If team_ids provided, only matches involving those teams
      (team_ids can be WC team IDs or HistoricalTeam IDs)
    - Excludes is_unmapped records
    - Excludes real_time_only records (future feature)
    """
    query = (
        select(HistoricalMatch)
        .where(HistoricalMatch.available_at < as_of)
        .where(HistoricalMatch.is_unmapped.is_(False))
    )

    if team_ids:
        query = query.where(
            (HistoricalMatch.home_team_id.in_(team_ids))
            | (HistoricalMatch.away_team_id.in_(team_ids))
        )

    return list(session.scalars(query.order_by(HistoricalMatch.kickoff)))


def get_team_match_history(
    session: Session,
    team_id: str,
    as_of: datetime,
) -> list[HistoricalMatch]:
    """Get all matches for a team before as_of, ordered by kickoff desc.

    team_id can be a WC team ID (e.g., "BRA") or a HistoricalTeam ID
    (e.g., "ht_ARG"). The query matches against both home_team_id and
    away_team_id regardless of source.
    """
    query = (
        select(HistoricalMatch)
        .where(HistoricalMatch.available_at < as_of)
        .where(HistoricalMatch.is_unmapped.is_(False))
        .where(
            (HistoricalMatch.home_team_id == team_id)
            | (HistoricalMatch.away_team_id == team_id)
        )
        .order_by(HistoricalMatch.kickoff.desc())
    )
    return list(session.scalars(query))


def get_team_match_history_90min(
    session: Session,
    team_id: str,
    as_of: datetime,
) -> list[HistoricalMatch]:
    """Get matches suitable for 90-minute model (excludes after_extra_time_or_unknown)."""
    query = (
        select(HistoricalMatch)
        .where(HistoricalMatch.available_at < as_of)
        .where(HistoricalMatch.is_unmapped.is_(False))
        .where(HistoricalMatch.score_scope == "full_90min")
        .where(
            (HistoricalMatch.home_team_id == team_id)
            | (HistoricalMatch.away_team_id == team_id)
        )
        .order_by(HistoricalMatch.kickoff.desc())
    )
    return list(session.scalars(query))
