import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.schemas import SourceMetadata, TournamentMatch, TournamentPayload, TournamentTeam


SOURCE_URL = "https://github.com/openfootball/worldcup.json/tree/master/2026"
MATCHES_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
TEAMS_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.teams.json"
_TIME_PATTERN = re.compile(r"^(\d{2}):(\d{2}) UTC([+-]\d{1,2})$")


class OpenFootballProvider:
    def __init__(self, matches_path: Path, teams_path: Path):
        self.matches_path = matches_path
        self.teams_path = teams_path

    @classmethod
    def from_files(cls, matches_path: str | Path, teams_path: str | Path):
        return cls(Path(matches_path), Path(teams_path))

    def load(self) -> TournamentPayload:
        raw_tournament = json.loads(self.matches_path.read_text(encoding="utf-8"))
        raw_teams = json.loads(self.teams_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromtimestamp(
            max(self.matches_path.stat().st_mtime, self.teams_path.stat().st_mtime),
            tz=timezone.utc,
        )

        return self._normalize(raw_tournament, raw_teams, fetched_at)

    @classmethod
    def from_remote(cls, timeout: float = 15.0):
        return OpenFootballRemoteProvider(timeout=timeout)

    @classmethod
    def _normalize(
        cls,
        raw_tournament: dict,
        raw_teams: list[dict],
        fetched_at: datetime,
    ) -> TournamentPayload:
        teams = [cls._normalize_team(team) for team in raw_teams]
        code_by_name = {team.name: team.id for team in teams}
        aliases = {
            alias: team.id
            for team in teams
            for alias in team.aliases
        }
        code_by_name.update(aliases)

        matches = [
            cls._normalize_match(match, code_by_name)
            for match in raw_tournament["matches"]
            if match.get("group")
        ]
        return TournamentPayload(
            name=raw_tournament["name"],
            source=SourceMetadata(
                provider="openfootball",
                source_url=SOURCE_URL,
                fetched_at=fetched_at,
            ),
            teams=teams,
            matches=matches,
        )

    @staticmethod
    def _normalize_team(raw: dict) -> TournamentTeam:
        normalized_name = raw.get("name_normalised") or raw["name"]
        aliases = list(dict.fromkeys([raw["name"], normalized_name]))
        return TournamentTeam(
            id=raw["fifa_code"],
            name=normalized_name,
            short_name=raw["name"],
            code=raw["fifa_code"],
            group_code=raw["group"],
            flag=raw.get("flag_icon"),
            aliases=aliases,
        )

    @staticmethod
    def _normalize_match(raw: dict, code_by_name: dict[str, str]) -> TournamentMatch:
        group_code = raw["group"].removeprefix("Group ")
        home_id = code_by_name[raw["team1"]]
        away_id = code_by_name[raw["team2"]]
        kickoff = _parse_kickoff(raw["date"], raw["time"])
        final_score = raw.get("score", {}).get("ft")
        match_id = f"2026-{group_code}-{home_id}-{away_id}-{raw['date']}"
        return TournamentMatch(
            id=match_id,
            group_code=group_code,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff=kickoff,
            venue=raw.get("ground"),
            status="final" if final_score else "scheduled",
            home_score=final_score[0] if final_score else None,
            away_score=final_score[1] if final_score else None,
            source_match_id=match_id,
        )


def _parse_kickoff(date_value: str, time_value: str) -> datetime:
    match = _TIME_PATTERN.fullmatch(time_value)
    if not match:
        raise ValueError(f"unsupported OpenFootball time: {time_value}")
    hour, minute, offset = map(int, match.groups())
    local_timezone = timezone(timedelta(hours=offset))
    return datetime.fromisoformat(f"{date_value}T{hour:02d}:{minute:02d}:00").replace(
        tzinfo=local_timezone
    ).astimezone(timezone.utc)


class OpenFootballRemoteProvider:
    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def load(self) -> TournamentPayload:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            matches_response = client.get(MATCHES_URL)
            matches_response.raise_for_status()
            teams_response = client.get(TEAMS_URL)
            teams_response.raise_for_status()
        return OpenFootballProvider._normalize(
            matches_response.json(),
            teams_response.json(),
            datetime.now(timezone.utc),
        )
