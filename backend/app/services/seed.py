from dataclasses import dataclass
from hashlib import sha256
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataSnapshot, Match, Team, TeamAlias, TeamRating
from app.schemas import GROUP_CODES, TournamentPayload


@dataclass(frozen=True)
class SeedResult:
    groups: list[str]
    team_count: int
    match_count: int


def seed_tournament(session: Session, payload: TournamentPayload) -> SeedResult:
    for incoming in payload.teams:
        team = session.get(Team, incoming.id)
        if team is None:
            team = Team(id=incoming.id)
            session.add(team)
        team.name = incoming.name
        team.short_name = incoming.short_name
        team.code = incoming.code
        team.group_code = incoming.group_code
        team.flag_url = incoming.flag

        existing_aliases = set(
            session.scalars(
                select(TeamAlias.alias).where(
                    TeamAlias.team_id == incoming.id,
                    TeamAlias.provider == payload.source.provider,
                )
            )
        )
        for alias in incoming.aliases:
            if alias not in existing_aliases:
                session.add(
                    TeamAlias(
                        team_id=incoming.id,
                        provider=payload.source.provider,
                        alias=alias,
                    )
                )

    session.flush()
    for incoming in payload.matches:
        match = session.get(Match, incoming.id)
        if match is None:
            match = Match(id=incoming.id)
            session.add(match)
        match.group_code = incoming.group_code
        match.home_team_id = incoming.home_team_id
        match.away_team_id = incoming.away_team_id
        match.kickoff = incoming.kickoff
        match.venue = incoming.venue
        match.source = payload.source.provider
        match.source_match_id = incoming.source_match_id
        match.source_updated_at = payload.source.fetched_at
        # Protect: don't overwrite status/scores of finalized matches
        if match.status != "final":
            match.status = incoming.status
            match.home_score = incoming.home_score
            match.away_score = incoming.away_score

    checksum = sha256(payload.model_dump_json().encode()).hexdigest()
    existing_snapshot = session.scalar(
        select(DataSnapshot).where(
            DataSnapshot.provider == payload.source.provider,
            DataSnapshot.checksum == checksum,
        )
    )
    if existing_snapshot is None:
        session.add(
            DataSnapshot(
                provider=payload.source.provider,
                source_url=payload.source.source_url,
                fetched_at=payload.source.fetched_at,
                status="available",
                checksum=checksum,
                coverage={"teams": len(payload.teams), "matches": len(payload.matches)},
            )
        )
    session.flush()
    return SeedResult(
        groups=list(GROUP_CODES),
        team_count=len(payload.teams),
        match_count=len(payload.matches),
    )


def seed_ratings(session: Session, path: str | Path) -> int:
    raw = Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw)
    effective_date = datetime.fromisoformat(payload["retrieved_at"]).date()
    count = 0
    for incoming in payload["ratings"]:
        if session.get(Team, incoming["team_id"]) is None:
            raise ValueError(f"rating references unknown team {incoming['team_id']}")
        existing = session.scalar(
            select(TeamRating).where(
                TeamRating.team_id == incoming["team_id"],
                TeamRating.effective_date == effective_date,
                TeamRating.source == payload["source"],
            )
        )
        if existing is None:
            session.add(
                TeamRating(
                    team_id=incoming["team_id"],
                    effective_date=effective_date,
                    fifa_rank=None,
                    fifa_points=None,
                    elo=float(incoming["elo"]),
                    source=payload["source"],
                )
            )
        else:
            existing.elo = float(incoming["elo"])
        count += 1
    checksum = sha256(raw.encode()).hexdigest()
    snapshot = session.scalar(
        select(DataSnapshot).where(
            DataSnapshot.provider == "world_football_elo",
            DataSnapshot.checksum == checksum,
        )
    )
    if snapshot is None:
        session.add(
            DataSnapshot(
                provider="world_football_elo",
                source_url=payload["source_url"],
                fetched_at=datetime.fromisoformat(payload["retrieved_at"]),
                status="available",
                checksum=checksum,
                coverage={"teams": count},
            )
        )
    session.flush()
    return count


def seed_team_aliases(session: Session, path: str | Path) -> int:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    inserted = 0
    for team_id, aliases in payload["aliases"].items():
        if session.get(Team, team_id) is None:
            raise ValueError(f"alias references unknown team {team_id}")
        existing = set(
            session.scalars(
                select(TeamAlias.alias).where(
                    TeamAlias.team_id == team_id,
                    TeamAlias.provider == payload["provider"],
                )
            )
        )
        for alias in aliases:
            if alias not in existing:
                session.add(
                    TeamAlias(
                        team_id=team_id,
                        provider=payload["provider"],
                        alias=alias,
                    )
                )
                existing.add(alias)
                inserted += 1
    session.flush()
    return inserted
