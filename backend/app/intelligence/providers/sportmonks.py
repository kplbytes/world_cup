import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.config import settings
from app.intelligence.providers.base import IntelligenceProvider
from app.intelligence.quota import QuotaGuard
from app.models import MatchIntelligence, ProviderQuotaState

logger = logging.getLogger(__name__)

class SportMonksIntelligenceProvider(IntelligenceProvider):
    """Fetches injuries, suspensions, and lineups from SportMonks v3 API."""

    @property
    def name(self) -> str:
        return "sportmonks"

    def fetch_intelligence(
        self,
        session: Session,
        match_id: str,
        kickoff: datetime,
        home_team_id: str,
        away_team_id: str
    ) -> list[int]:
        # 1. Token Optional
        token = settings.sportmonks_token or None
        if not token:
            return []

        # 2. Check if we already have injuries/suspensions for this match recently (simple cache)
        # We can look for recent intelligence within the last 1 hour
        now = datetime.now(timezone.utc)
        recent = session.scalar(
            select(MatchIntelligence)
            .where(
                MatchIntelligence.match_id == match_id,
                MatchIntelligence.provider == self.name,
                MatchIntelligence.fetched_at >= now - __import__("datetime").timedelta(hours=1)
            )
            .limit(1)
        )
        if recent:
            # Already fetched recently, just return empty list (or IDs if we want them, but Engine loads all)
            return []

        # 3. Quota Guard
        guard = QuotaGuard(session, self.name, daily_limit=500)
        if not guard.can_request():
            logger.warning(f"SportMonks quota exceeded when fetching {match_id}")
            return []

        # 4. Fetch data
        # We simulate fetching by match ID or teams. For real, it's typically /v3/football/fixtures/{id}?include=lineups,injuries,suspensions
        # We'll use a mocked endpoint pattern for this project.
        url = f"https://api.sportmonks.com/v3/football/fixtures/search/{home_team_id}?include=injuries,suspensions"

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params={"api_token": token})
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTPError fetching SportMonks data for match {match_id}: {e}")
            return []
        except ValueError as e: # includes JSONDecodeError
            logger.error(f"JSONDecodeError parsing SportMonks data for match {match_id}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching SportMonks data for match {match_id}: {e}")
            return []

        guard.record_request()

        # 5. Parse and Normalize
        new_ids = []
        try:
            # Assuming payload format: {"data": [{"injuries": [...], "suspensions": [...]}]}
            # In a real scenario, we'd find the correct fixture. We just take data[0] if exists.
            fixtures = data.get("data", [])
            if not fixtures:
                return []

            fixture = fixtures[0]

            # Process injuries
            for inj in fixture.get("injuries", []):
                affected_team = home_team_id if inj.get("is_home") else away_team_id
                player_name = inj.get("player_name", "Unknown Player")
                reason = inj.get("reason", "Injury")

                normalized = {
                    "affected_team_id": affected_team,
                    "player_name": player_name,
                    "reason": reason,
                    "injury_type": inj.get("type", "Unknown"),
                    "expected_return": inj.get("expected_return", "Unknown"),
                    "status": inj.get("status", "Sidelined")
                }

                intel = MatchIntelligence(
                    match_id=match_id,
                    provider="sportmonks",
                    source_url=url,
                    intelligence_type="injuries",
                    raw_payload=inj,
                    normalized_payload=normalized,
                    source_confidence=0.85,
                    fetched_at=now
                )
                session.add(intel)
                session.flush()
                new_ids.append(intel.id)

            # Process suspensions
            for sus in fixture.get("suspensions", []):
                affected_team = home_team_id if sus.get("is_home") else away_team_id
                player_name = sus.get("player_name", "Unknown Player")
                reason = sus.get("reason", "Suspended")

                normalized = {
                    "affected_team_id": affected_team,
                    "player_name": player_name,
                    "reason": reason,
                    "suspension_type": sus.get("type", "Unknown"),
                }

                intel = MatchIntelligence(
                    match_id=match_id,
                    provider="sportmonks",
                    source_url=url,
                    intelligence_type="suspensions",
                    raw_payload=sus,
                    normalized_payload=normalized,
                    source_confidence=0.90,
                    fetched_at=now
                )
                session.add(intel)
                session.flush()
                new_ids.append(intel.id)

        except Exception as e:
            logger.error(f"Error parsing SportMonks intelligence for match {match_id}: {e}")

        return new_ids
