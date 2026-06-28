from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


GROUP_CODES = tuple("ABCDEFGHIJKL")


class SourceMetadata(BaseModel):
    provider: str
    source_url: str
    fetched_at: datetime


class TournamentTeam(BaseModel):
    id: str = Field(pattern=r"^[A-Z]{3}$")
    name: str = Field(min_length=1)
    short_name: str = Field(min_length=1)
    code: str = Field(pattern=r"^[A-Z]{3}$")
    group_code: str = Field(pattern=r"^[A-L]$")
    flag: str | None = None
    aliases: list[str] = Field(default_factory=list)


class TournamentMatch(BaseModel):
    id: str = Field(min_length=1)
    group_code: str | None = Field(default=None, pattern=r"^[A-L]$")
    home_team_id: str = Field(pattern=r"^[A-Z]{3}$")
    away_team_id: str = Field(pattern=r"^[A-Z]{3}$")
    kickoff: datetime
    venue: str | None = None
    status: Literal["scheduled", "live", "final"] = "scheduled"
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)
    home_advance: bool | None = None
    away_advance: bool | None = None
    went_to_extra_time: bool | None = None
    went_to_penalties: bool | None = None
    source_match_id: str | None = None

    @model_validator(mode="after")
    def final_matches_require_scores(self):
        if self.status == "final" and (self.home_score is None or self.away_score is None):
            raise ValueError("final matches require both scores")
        if self.home_advance and self.away_advance:
            raise ValueError("only one side can advance")
        if self.status != "final" and (self.home_advance or self.away_advance):
            raise ValueError("advance flags require a final match")
        if self.home_team_id == self.away_team_id:
            raise ValueError("a team cannot play itself")
        return self


class TournamentPayload(BaseModel):
    name: str
    source: SourceMetadata
    teams: list[TournamentTeam]
    matches: list[TournamentMatch]

    @model_validator(mode="after")
    def validate_group_stage(self):
        if len(self.teams) != 48:
            raise ValueError("the tournament requires exactly 48 teams")

        team_ids = [team.id for team in self.teams]
        if len(set(team_ids)) != len(team_ids):
            raise ValueError("team IDs must be unique")

        match_ids = [match.id for match in self.matches]
        if len(set(match_ids)) != len(match_ids):
            raise ValueError("match IDs must be unique")

        teams_by_id = {team.id: team for team in self.teams}
        group_matches = [match for match in self.matches if match.group_code]
        if len(group_matches) != 72:
            raise ValueError("the group stage requires exactly 72 matches")
        for group_code in GROUP_CODES:
            group_teams = [team for team in self.teams if team.group_code == group_code]
            group_specific_matches = [match for match in group_matches if match.group_code == group_code]
            if len(group_teams) != 4:
                raise ValueError(f"Group {group_code} requires exactly four teams")
            if len(group_specific_matches) != 6:
                raise ValueError(f"Group {group_code} requires exactly six matches")

        for match in self.matches:
            if match.home_team_id not in teams_by_id or match.away_team_id not in teams_by_id:
                raise ValueError(f"match {match.id} references an unknown team")
            if match.group_code:
                if teams_by_id[match.home_team_id].group_code != match.group_code:
                    raise ValueError(f"home team is not in Group {match.group_code}")
                if teams_by_id[match.away_team_id].group_code != match.group_code:
                    raise ValueError(f"away team is not in Group {match.group_code}")
        return self


class ManualAdjustmentCreate(BaseModel):
    match_id: str = Field(min_length=1)
    adjustment_type: str = Field(min_length=1, max_length=32)
    affected_team_id: str = Field(pattern=r"^[A-Z]{3}$")
    attack_delta: float = 0.0
    defense_delta: float = 0.0
    confidence: Literal["low", "medium", "high"] = "medium"
    note: str = Field(min_length=1)
    created_by: str = Field(default="manual", min_length=1, max_length=40)


class ManualAdjustmentResponse(BaseModel):
    id: int
    match_id: str
    adjustment_type: str
    affected_team_id: str
    affected_team_name: str
    attack_delta: float
    defense_delta: float
    confidence: str
    note: str
    created_by: str
    created_at: datetime
