"""Optional football-data.org provider for live World Cup match results.

This provider uses the football-data.org REST API (free tier) to fetch
current match scores and statuses. It requires a free API token configured
in the ``FOOTBALL_DATA_API_TOKEN`` environment variable.

When no token is configured, ``load()`` raises ``NotConfigured`` so the
refresh orchestrator can record the failure without blocking other
providers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.config import settings
from app.schemas import (
    SourceMetadata,
    TournamentMatch,
    TournamentPayload,
    TournamentTeam,
)
from app.services.team_matching import resolve_code_alias


COMPETITION_CODE = "WC"
BASE_URL = "https://api.football-data.org/v4"
SOURCE_URL = f"https://www.football-data.org/competitions/{COMPETITION_CODE}/matches"
_TIMEOUT = 15.0


class NotConfigured(RuntimeError):
    """Raised when the football-data.org API token is not configured."""


class FootballDataProvider:
    """Adapter for the football-data.org World Cup match feed."""

    def __init__(
        self,
        token: str | None = None,
        timeout: float = _TIMEOUT,
        canonical_teams: list[TournamentTeam] | None = None,
    ):
        self._token = token or settings.football_data_api_token
        self._timeout = timeout
        self._canonical_teams = canonical_teams or []

    def load(self) -> TournamentPayload:
        if not self._token:
            raise NotConfigured(
                "football-data.org provider not configured: "
                "set FOOTBALL_DATA_API_TOKEN in .env"
            )

        headers = {"X-Auth-Token": self._token}
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            matches_resp = client.get(
                f"{BASE_URL}/competitions/{COMPETITION_CODE}/matches",
                headers=headers,
                params={"stage": "GROUP_STAGE"},
            )
            matches_resp.raise_for_status()

        raw = matches_resp.json()
        fetched_at = datetime.now(timezone.utc)

        matches = [
            self._normalize_match(m)
            for m in raw.get("matches", [])
            if m.get("stage") == "GROUP_STAGE" and m.get("group")
        ]

        if not self._canonical_teams:
            teams = self._extract_teams(matches)
        else:
            teams = self._canonical_teams

        return TournamentPayload(
            name=raw.get("competition", {}).get("name", "FIFA World Cup 2026"),
            source=SourceMetadata(
                provider="football-data",
                source_url=SOURCE_URL,
                fetched_at=fetched_at,
            ),
            teams=teams,
            matches=matches,
        )

    @staticmethod
    def _normalize_match(raw: dict) -> TournamentMatch:
        group_label = raw.get("group", "")
        group_code = group_label.replace("Group ", "").removeprefix("GROUP_").strip()

        home_team = raw.get("homeTeam", {})
        away_team = raw.get("awayTeam", {})
        home_code = (home_team.get("tla") or home_team.get("shortName", ""))[:3].upper()
        away_code = (away_team.get("tla") or away_team.get("shortName", ""))[:3].upper()
        home_code = resolve_code_alias(home_code)
        away_code = resolve_code_alias(away_code)

        utc_date = raw.get("utcDate", "")
        kickoff = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))

        status = raw.get("status", "SCHEDULED")
        score = raw.get("score", {})
        full_time = score.get("fullTime", {})
        home_goals = full_time.get("home")
        away_goals = full_time.get("away")

        if status == "FINISHED" and home_goals is not None and away_goals is not None:
            match_status = "final"
        elif status in ("IN_PLAY", "PAUSED"):
            match_status = "live"
        else:
            match_status = "scheduled"

        match_date = kickoff.strftime("%Y-%m-%d")
        match_id = f"2026-{group_code}-{home_code}-{away_code}-{match_date}"

        return TournamentMatch(
            id=match_id,
            group_code=group_code,
            home_team_id=home_code,
            away_team_id=away_code,
            kickoff=kickoff,
            venue=raw.get("venue"),
            status=match_status,
            home_score=home_goals if match_status in ("final", "live") else None,
            away_score=away_goals if match_status in ("final", "live") else None,
            source_match_id=str(raw.get("id", "")),
        )

    @staticmethod
    def _extract_teams(matches: list[TournamentMatch]) -> list[TournamentTeam]:
        seen: dict[str, TournamentTeam] = {}
        for match in matches:
            if match.home_team_id not in seen:
                seen[match.home_team_id] = TournamentTeam(
                    id=match.home_team_id,
                    name=match.home_team_id,
                    short_name=match.home_team_id,
                    code=match.home_team_id,
                    group_code=match.group_code,
                )
            if match.away_team_id not in seen:
                seen[match.away_team_id] = TournamentTeam(
                    id=match.away_team_id,
                    name=match.away_team_id,
                    short_name=match.away_team_id,
                    code=match.away_team_id,
                    group_code=match.group_code,
                )
        return list(seen.values())


def is_configured() -> bool:
    """Return True when the API token is available."""
    return bool(settings.football_data_api_token)
