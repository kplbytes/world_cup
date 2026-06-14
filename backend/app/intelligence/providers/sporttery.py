"""Sporttery Intelligence Provider."""

from datetime import datetime, timezone, timedelta
from typing import Any
import logging
import httpx
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import MatchIntelligence, Team, TeamAlias
from app.intelligence.providers.base import IntelligenceProvider
from app.intelligence.quota import QuotaGuard
from app.intelligence.cache import save_intelligence
# TODO: Replace _name_matches usage with app.services.team_matching.match_team() for unified matching
from app.services.market import (
    SportteryRemoteProvider,
    _SPORTTERY_URL,
    _SHANGHAI,
    _sporttery_match_date,
    _name_matches
)
from app.providers.sporttery import normalize_had_prices

logger = logging.getLogger(__name__)

class SportteryIntelligenceProvider(IntelligenceProvider):
    """Provides market odds from Sporttery."""

    def __init__(self, cache_ttl_minutes: int = 5):
        self._cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._last_fetched: datetime | None = None
        self._cached_data: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "sporttery"

    def _fetch_all_with_cache(self, session: Session) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        if self._last_fetched and now - self._last_fetched < self._cache_ttl:
            return self._cached_data

        guard = QuotaGuard(session, self.name, daily_limit=5000)
        if not guard.can_request():
            logger.warning("Sporttery quota exceeded, returning cached/empty data.")
            return self._cached_data

        try:
            remote = SportteryRemoteProvider()
            self._cached_data = remote.fetch_odds()
            self._last_fetched = now
            guard.record_request()
        except Exception as e:
            logger.error(f"Failed to fetch Sporttery data: {e}")
            # If we fail, return whatever we have cached

        return self._cached_data

    def fetch_intelligence(
        self, session: Session, match_id: str, kickoff: Any, home_team_id: str, away_team_id: str
    ) -> list[int]:
        """Fetch Sporttery odds for a single match."""

        raw_matches = self._fetch_all_with_cache(session)
        if not raw_matches:
            return []

        # Find the match
        home_team = session.get(Team, home_team_id)
        away_team = session.get(Team, away_team_id)
        if not home_team or not away_team:
            return []

        # Get all aliases
        home_names = {home_team.name.lower(), home_team.short_name.lower(), home_team.code.lower()}
        away_names = {away_team.name.lower(), away_team.short_name.lower(), away_team.code.lower()}

        for alias in session.scalars(select(TeamAlias).where(TeamAlias.team_id == home_team.id)):
            home_names.add(alias.alias.lower())
        for alias in session.scalars(select(TeamAlias).where(TeamAlias.team_id == away_team.id)):
            away_names.add(alias.alias.lower())

        target_date = _sporttery_match_date(kickoff) if kickoff else ""

        matched_raw = None
        for raw in raw_matches:
            raw_date = raw["match_date"][:10] if raw["match_date"] else ""
            if raw_date != target_date:
                continue

            if _name_matches(home_names, raw["home_team"].lower()) and \
               _name_matches(away_names, raw["away_team"].lower()):
                matched_raw = raw
                break

        if not matched_raw:
            return []

        try:
            # Parse odds
            market = normalize_had_prices(
                matched_raw["had_home"],
                matched_raw["had_draw"],
                matched_raw["had_away"]
            )

            normalized_payload = {
                "home": market.home,
                "draw": market.draw,
                "away": market.away,
                "raw_overround": market.raw_overround,
                "source_match_num": matched_raw["match_num"]
            }
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to parse Sporttery raw payload: {e}")
            return []

        # Save intelligence
        # We consider Sporttery odds to be very high confidence for market data
        intel = save_intelligence(
            session=session,
            match_id=match_id,
            provider=self.name,
            source_url=_SPORTTERY_URL,
            intelligence_type="odds",
            raw_payload=matched_raw,
            normalized_payload=normalized_payload,
            source_confidence=0.9,
            affected_team_id=None,
            affected_player_name=None,
        )

        if intel:
            return [intel.id]
        return []
