"""Intelligence Pipeline to fetch from all providers."""

import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match
from app.intelligence.providers.sporttery import SportteryIntelligenceProvider
from app.intelligence.providers.api_football import ApiFootballIntelligenceProvider
from app.intelligence.providers.sportmonks import SportMonksIntelligenceProvider

logger = logging.getLogger(__name__)

def run_intelligence_pipeline(session: Session) -> bool:
    """Run all enabled intelligence providers to fetch new data.

    Returns True if any new intelligence was persisted.
    """
    # We only fetch for matches that are not final and within next 24 hours maybe?
    # Or just all upcoming matches.
    now = datetime.now(timezone.utc)
    threshold = now + timedelta(hours=48)

    # We want to fetch intelligence for matches that have not finished.
    # To save quota, we only query for matches happening soon (e.g. next 48 hours).
    matches = list(session.scalars(
        select(Match)
        .where(Match.status != "final")
    ))

    # Sort matches by kickoff to prioritize soonest
    matches.sort(key=lambda m: m.kickoff.replace(tzinfo=timezone.utc) if m.kickoff.tzinfo is None else m.kickoff)

    providers = [
        SportteryIntelligenceProvider(),
        ApiFootballIntelligenceProvider(),
        SportMonksIntelligenceProvider(),
    ]

    inserted_any = False

    for match in matches:
        kickoff = match.kickoff
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)

        # Optional: only fetch if within 48h
        if kickoff > threshold:
            continue

        for provider in providers:
            try:
                ids = provider.fetch_intelligence(
                    session=session,
                    match_id=match.id,
                    kickoff=kickoff,
                    home_team_id=match.home_team_id,
                    away_team_id=match.away_team_id
                )
                if ids:
                    inserted_any = True
            except Exception as e:
                logger.error(f"Error fetching intelligence from {provider.name} for match {match.id}: {e}")

    session.commit()
    return inserted_any
