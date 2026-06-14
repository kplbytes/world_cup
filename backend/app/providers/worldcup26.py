"""Free World Cup 2026 provider backed by worldcup26.ir.

This provider is used as a score/status supplement during the tournament.
Its `local_date` field is a venue-local wall clock without timezone metadata,
so refresh logic must not trust it for canonical kickoff or venue updates.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.schemas import SourceMetadata, TournamentMatch, TournamentPayload, TournamentTeam


BASE_URL = "https://worldcup26.ir"
SOURCE_URL = f"{BASE_URL}/api-docs/"
GAMES_URL = f"{BASE_URL}/get/games"
TEAMS_URL = f"{BASE_URL}/get/teams"
STADIUMS_URL = f"{BASE_URL}/get/stadiums"
_TIMEOUT = 15.0


class WorldCup26Provider:
    """Adapter for the public worldcup26.ir World Cup data feed."""

    def __init__(self, timeout: float = _TIMEOUT):
        self._timeout = timeout

    def load(self) -> TournamentPayload:
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            games_response = client.get(GAMES_URL)
            games_response.raise_for_status()
            teams_response = client.get(TEAMS_URL)
            teams_response.raise_for_status()
            stadiums_response = client.get(STADIUMS_URL)
            stadiums_response.raise_for_status()

        fetched_at = datetime.now(timezone.utc)
        return self._normalize(
            raw_games=games_response.json().get("games", []),
            raw_teams=teams_response.json().get("teams", []),
            raw_stadiums=stadiums_response.json().get("stadiums", []),
            fetched_at=fetched_at,
        )

    @classmethod
    def _normalize(
        cls,
        raw_games: list[dict],
        raw_teams: list[dict],
        raw_stadiums: list[dict],
        fetched_at: datetime,
    ) -> TournamentPayload:
        teams = [cls._normalize_team(team) for team in raw_teams]
        teams_by_provider_id = {str(team["id"]): normalized for team, normalized in zip(raw_teams, teams)}
        stadium_names = {
            str(stadium["id"]): stadium.get("name_en") or stadium.get("fifa_name")
            for stadium in raw_stadiums
        }

        matches = [
            cls._normalize_match(match, teams_by_provider_id, stadium_names)
            for match in raw_games
            if match.get("type") == "group" and match.get("group")
        ]
        return TournamentPayload(
            name="FIFA World Cup 2026",
            source=SourceMetadata(
                provider="worldcup26",
                source_url=SOURCE_URL,
                fetched_at=fetched_at,
            ),
            teams=teams,
            matches=matches,
        )

    @staticmethod
    def _normalize_team(raw: dict) -> TournamentTeam:
        fifa_code = str(raw["fifa_code"]).strip().upper()
        group_code = str(raw["groups"]).strip().upper()
        name = raw.get("name_en") or fifa_code
        aliases = [
            alias
            for alias in (raw.get("name_en"), raw.get("name_fa"), raw.get("fifa_code"))
            if alias
        ]
        return TournamentTeam(
            id=fifa_code,
            name=name,
            short_name=name,
            code=fifa_code,
            group_code=group_code,
            flag=raw.get("flag"),
            aliases=list(dict.fromkeys(aliases)),
        )

    @staticmethod
    def _normalize_match(
        raw: dict,
        teams_by_provider_id: dict[str, TournamentTeam],
        stadium_names: dict[str, str | None],
    ) -> TournamentMatch:
        home_team = teams_by_provider_id[str(raw["home_team_id"])]
        away_team = teams_by_provider_id[str(raw["away_team_id"])]
        match_date = datetime.strptime(raw["local_date"], "%m/%d/%Y %H:%M")
        kickoff = match_date.replace(tzinfo=timezone.utc)
        match_day = match_date.strftime("%Y-%m-%d")
        finished = str(raw.get("finished", "")).upper() == "TRUE"
        home_score = int(raw["home_score"]) if finished else None
        away_score = int(raw["away_score"]) if finished else None

        return TournamentMatch(
            id=f"2026-{raw['group']}-{home_team.id}-{away_team.id}-{match_day}",
            group_code=str(raw["group"]).upper(),
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            kickoff=kickoff,
            venue=stadium_names.get(str(raw.get("stadium_id"))),
            status="final" if finished else "scheduled",
            home_score=home_score,
            away_score=away_score,
            source_match_id=str(raw.get("id", "")),
        )
