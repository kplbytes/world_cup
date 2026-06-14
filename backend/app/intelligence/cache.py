from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MatchIntelligence


def get_cached_intelligence(
    session: Session,
    match_id: str,
    provider: str,
    intelligence_type: str,
    max_age_minutes: int,
) -> dict[str, Any] | None:
    """Return the normalized_payload of the most recent intelligence if within max_age."""
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(minutes=max_age_minutes)

    row = session.scalar(
        select(MatchIntelligence)
        .where(
            MatchIntelligence.match_id == match_id,
            MatchIntelligence.provider == provider,
            MatchIntelligence.intelligence_type == intelligence_type,
            MatchIntelligence.fetched_at >= threshold,
        )
        .order_by(MatchIntelligence.fetched_at.desc())
        .limit(1)
    )
    if row:
        return row.normalized_payload
    return None


def save_intelligence(
    session: Session,
    match_id: str,
    provider: str,
    source_url: str,
    intelligence_type: str,
    raw_payload: dict[str, Any],
    normalized_payload: dict[str, Any],
    source_confidence: float,
    affected_team_id: str | None = None,
    affected_player_name: str | None = None,
) -> MatchIntelligence:
    row = MatchIntelligence(
        match_id=match_id,
        provider=provider,
        source_url=source_url,
        intelligence_type=intelligence_type,
        affected_team_id=affected_team_id,
        affected_player_name=affected_player_name,
        raw_payload=raw_payload,
        normalized_payload=normalized_payload,
        source_confidence=source_confidence,
    )
    session.add(row)
    session.flush()
    return row
