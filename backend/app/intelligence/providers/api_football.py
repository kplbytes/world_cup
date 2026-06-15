"""API-Football Intelligence Provider."""

import logging
import httpx
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.config import settings
from app.models import MatchIntelligence, Team, TeamAlias
from app.intelligence.providers.base import IntelligenceProvider
from app.intelligence.quota import QuotaGuard
from app.intelligence.cache import get_cached_intelligence, save_intelligence

logger = logging.getLogger(__name__)

class ApiFootballIntelligenceProvider(IntelligenceProvider):
    """Provides fixtures and lineups intelligence from API-Football."""

    def __init__(self, cache_ttl_minutes: int = 15):
        self.token = settings.api_football_token or None
        self._cache_ttl = cache_ttl_minutes
        self.base_url = "https://v3.football.api-sports.io"
        self._headers = {
            "x-apisports-key": self.token,
            "x-rapidapi-host": "v3.football.api-sports.io"
        }

    @property
    def name(self) -> str:
        return "api-football"

    def _fetch_url(self, url: str, session: Session, guard: QuotaGuard) -> dict[str, Any] | None:
        if not self.token:
            return None

        if not guard.can_request():
            logger.warning(f"{self.name} quota exceeded.")
            return None

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, headers=self._headers)
                response.raise_for_status()
                guard.record_request()

                data = response.json()
                if "errors" in data and data["errors"]:
                    logger.error(f"API-Football returned errors: {data['errors']}")
                    return None
                return data
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None
        except ValueError as e:
            logger.error(f"Failed to parse JSON from {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching {url}: {e}")
            return None

    @staticmethod
    def _is_date_accessible(target_date: str) -> bool:
        """Check if target_date is within the API-Football free plan accessible range.

        Free plans only allow access to dates from (today - 2 days) to (today + 2 days).
        """
        try:
            target = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).date()
        except ValueError:
            return False
        today = datetime.now(timezone.utc).date()
        # Free plan allows today ± 2 days (conservative estimate)
        min_date = today - timedelta(days=2)
        max_date = today + timedelta(days=2)
        return min_date <= target <= max_date

    def fetch_intelligence(
        self, session: Session, match_id: str, kickoff: Any, home_team_id: str, away_team_id: str
    ) -> list[int]:
        if not self.token:
            return []

        guard = QuotaGuard(session, self.name, daily_limit=100)

        # 1. We need to find the fixture ID. For simplicity in P1C-1, we assume we fetch
        # fixtures by date if we don't know it, or maybe we just mock it for now.
        # But wait, to make it work we need a mapping or we search by date.
        # Let's search by date if we don't have it cached.

        target_date = kickoff.strftime("%Y-%m-%d") if kickoff else datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Skip if date is outside free plan accessible range
        if not self._is_date_accessible(target_date):
            logger.debug(f"Skipping {self.name} fetch for {match_id}: date {target_date} outside free plan range")
            return []

        # Check cache for fixtures first to get the fixture ID
        cached_fixture = get_cached_intelligence(session, match_id, self.name, "fixtures", max_age_minutes=self._cache_ttl)

        fixture_id = None
        if cached_fixture and "fixture_id" in cached_fixture:
            fixture_id = cached_fixture["fixture_id"]

        inserted_ids = []

        if not fixture_id:
            # Fetch fixtures for the date
            url = f"{self.base_url}/fixtures?date={target_date}"
            data = self._fetch_url(url, session, guard)

            if data and "response" in data:
                home_team = session.get(Team, home_team_id)
                away_team = session.get(Team, away_team_id)

                # Simple name matching
                home_names = {home_team.name.lower(), home_team.short_name.lower()} if home_team else set()
                away_names = {away_team.name.lower(), away_team.short_name.lower()} if away_team else set()

                matched_fixture = None
                for item in data.get("response", []):
                    f_home = item.get("teams", {}).get("home", {}).get("name", "").lower()
                    f_away = item.get("teams", {}).get("away", {}).get("name", "").lower()

                    if (any(name in f_home for name in home_names) or f_home in home_names) and \
                       (any(name in f_away for name in away_names) or f_away in away_names):
                        matched_fixture = item
                        break

                if matched_fixture:
                    fixture_id = matched_fixture.get("fixture", {}).get("id")

                    if fixture_id:
                        status = matched_fixture.get("fixture", {}).get("status", {}).get("short", "")
                        intel = save_intelligence(
                            session=session,
                            match_id=match_id,
                            provider=self.name,
                            source_url=url,
                            intelligence_type="fixtures",
                            raw_payload=matched_fixture,
                            normalized_payload={
                                "fixture_id": fixture_id,
                                "status": status,
                            },
                            source_confidence=0.9
                        )
                        inserted_ids.append(intel.id)

        # 2. Fetch Lineups if we have fixture_id
        if fixture_id:
            # Check if we already have official lineups
            # We can check cache with a very large TTL if official
            cached_lineups = get_cached_intelligence(session, match_id, self.name, "lineups", max_age_minutes=self._cache_ttl)
            if cached_lineups and cached_lineups.get("is_official") is True:
                # Already have official lineup, don't fetch
                pass
            else:
                lineups_url = f"{self.base_url}/fixtures/lineups?fixture={fixture_id}"
                data = self._fetch_url(lineups_url, session, guard)

                if data and "response" in data and len(data["response"]) == 2:
                    resp = data["response"]

                    home_data = resp[0]
                    away_data = resp[1]

                    # API-Football lineups are official if they are present and confirmed?
                    # Usually if response is not empty, it means they are confirmed for this fixture.
                    # Or we can check if startXI is not empty.
                    is_official = len(home_data.get("startXI", [])) > 0

                    normalized_lineups = {
                        "is_official": is_official,
                        "home_formation": home_data.get("formation"),
                        "away_formation": away_data.get("formation"),
                        "home_starters": [player.get("player", {}).get("name") for player in home_data.get("startXI", [])],
                        "away_starters": [player.get("player", {}).get("name") for player in away_data.get("startXI", [])]
                    }

                    intel = save_intelligence(
                        session=session,
                        match_id=match_id,
                        provider=self.name,
                        source_url=lineups_url,
                        intelligence_type="lineups",
                        raw_payload=data,
                        normalized_payload=normalized_lineups,
                        source_confidence=0.8
                    )
                    inserted_ids.append(intel.id)

        return inserted_ids
