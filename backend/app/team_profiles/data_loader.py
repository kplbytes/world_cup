from __future__ import annotations

import json
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Team, TeamAlias, TeamProfileMatchHistory, TeamRating
from app.team_profiles.feature_engineering import classify_opponent_tier

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORLD_CUP_HISTORY_SNAPSHOT = PROJECT_ROOT / "data" / "seed" / "team-profile-world-cup-history.json"
PROFILE_MATCH_HISTORY_SNAPSHOT = PROJECT_ROOT / "data" / "seed" / "team-profile-match-history.json"


def load_world_cup_history_snapshot(path: Path | None = None) -> dict:
    snapshot_path = path or WORLD_CUP_HISTORY_SNAPSHOT
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def load_profile_match_history_snapshot(path: Path | None = None) -> dict:
    snapshot_path = path or PROFILE_MATCH_HISTORY_SNAPSHOT
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def _name_key(value: str) -> str:
    return " ".join(value.casefold().replace("&", "and").split())


def _team_lookup(session: Session, teams: list[Team]) -> dict[str, Team]:
    lookup: dict[str, Team] = {}
    for team in teams:
        for value in (team.id, team.code, team.name, team.short_name):
            lookup[_name_key(value)] = team
    for alias in session.scalars(select(TeamAlias)):
        team = next((item for item in teams if item.id == alias.team_id), None)
        if team is not None:
            lookup[_name_key(alias.alias)] = team
    return lookup


def seed_mock_history(session: Session, team_ids: set[str] | None = None) -> int:
    """Create deterministic, clearly-labelled profile history for local development."""
    teams = list(session.scalars(select(Team).order_by(Team.id)))
    if team_ids is not None:
        teams = [team for team in teams if team.id in team_ids]
    ratings = {}
    for team in teams:
        rating = session.scalar(select(TeamRating).where(TeamRating.team_id == team.id).order_by(TeamRating.effective_date.desc()).limit(1))
        ratings[team.id] = rating.elo if rating else 1500.0

    delete_query = delete(TeamProfileMatchHistory).where(TeamProfileMatchHistory.source == "seed_mock_v1")
    if team_ids is not None:
        delete_query = delete_query.where(TeamProfileMatchHistory.team_id.in_(team_ids))
    session.execute(delete_query)
    count = 0
    competitions = ["world_cup_2014", "world_cup_2018", "world_cup_2022", "qualifier_2026", "continental_cup"]
    start = date(2014, 6, 12)
    for team_index, team in enumerate(teams):
        seed = int(sha256(team.code.encode()).hexdigest()[:8], 16)
        team_elo = ratings[team.id]
        for index in range(16):
            opponent = teams[(team_index + index * 7 + 1) % len(teams)] if len(teams) > 1 else None
            opponent_elo = ratings.get(opponent.id, 1500.0) if opponent else 1500.0
            strength = (team_elo - opponent_elo) / 300.0
            noise = ((seed >> (index % 16)) & 7) / 10.0 - 0.35
            goals_for = max(0, min(4, round(1.2 + strength + noise)))
            goals_against = max(0, min(4, round(1.15 - strength - noise / 2)))
            result = "win" if goals_for > goals_against else "draw" if goals_for == goals_against else "loss"
            competition = competitions[index % len(competitions)]
            is_world_cup = competition.startswith("world_cup")
            is_qualifier = competition == "qualifier_2026"
            session.add(TeamProfileMatchHistory(
                team_id=team.id,
                match_date=start + timedelta(days=index * 250 + team_index),
                competition=competition,
                stage="knockout" if is_world_cup and index % 5 == 0 else "group",
                opponent_team_id=opponent.id if opponent else None,
                opponent_name=opponent.short_name if opponent else "Historical Opponent",
                opponent_elo=opponent_elo,
                opponent_tier=classify_opponent_tier(opponent_elo),
                is_neutral=is_world_cup,
                is_home=index % 2 == 0,
                goals_for=goals_for,
                goals_against=goals_against,
                result=result,
                points=3 if result == "win" else 1 if result == "draw" else 0,
                is_world_cup=is_world_cup,
                is_qualifier=is_qualifier,
                is_friendly=False,
                source="seed_mock_v1",
            ))
            count += 1
    session.flush()
    return count


def load_real_history(session: Session) -> int:
    """Load sourced World Cup 2014/2018/2022 match results for current teams."""
    teams = list(session.scalars(select(Team).order_by(Team.id)))
    team_by_name = _team_lookup(session, teams)

    ratings: dict[str, float] = {}
    for team in teams:
        rating = session.scalar(
            select(TeamRating)
            .where(TeamRating.team_id == team.id)
            .order_by(TeamRating.effective_date.desc())
            .limit(1)
        )
        ratings[team.id] = rating.elo if rating else 1500.0

    session.execute(delete(TeamProfileMatchHistory).where(TeamProfileMatchHistory.source == "historical_real"))

    count = 0
    for match in load_profile_match_history_snapshot()["matches"]:
        t1 = team_by_name.get(_name_key(match["home_team_code"] or match["home_team"]))
        t2 = team_by_name.get(_name_key(match["away_team_code"] or match["away_team"]))
        if t1 is None and t2 is None:
            continue

        match_date = date.fromisoformat(match["date"])

        if t1 is not None:
            opp_id = t2.id if t2 else None
            opp_name = t2.short_name if t2 else match["away_team"]
            opp_elo = ratings.get(opp_id, 1500.0) if opp_id else 1500.0
            gf, ga = match["home_score"], match["away_score"]
            result = "win" if gf > ga else "draw" if gf == ga else "loss"
            session.add(TeamProfileMatchHistory(
                team_id=t1.id,
                match_date=match_date,
                competition=match["competition"],
                stage=match["stage"],
                opponent_team_id=opp_id,
                opponent_name=opp_name,
                opponent_elo=opp_elo,
                opponent_tier=classify_opponent_tier(opp_elo),
                is_neutral=match["neutral"],
                is_home=not match["neutral"],
                goals_for=gf,
                goals_against=ga,
                result=result,
                points=3 if result == "win" else 1 if result == "draw" else 0,
                is_world_cup=match["is_world_cup"],
                is_qualifier=match["is_qualifier"],
                is_friendly=match["is_friendly"],
                source="historical_real",
            ))
            count += 1

        if t2 is not None:
            opp_id = t1.id if t1 else None
            opp_name = t1.short_name if t1 else match["home_team"]
            opp_elo = ratings.get(opp_id, 1500.0) if opp_id else 1500.0
            gf, ga = match["away_score"], match["home_score"]
            result = "win" if gf > ga else "draw" if gf == ga else "loss"
            session.add(TeamProfileMatchHistory(
                team_id=t2.id,
                match_date=match_date,
                competition=match["competition"],
                stage=match["stage"],
                opponent_team_id=opp_id,
                opponent_name=opp_name,
                opponent_elo=opp_elo,
                opponent_tier=classify_opponent_tier(opp_elo),
                is_neutral=match["neutral"],
                is_home=False,
                goals_for=gf,
                goals_against=ga,
                result=result,
                points=3 if result == "win" else 1 if result == "draw" else 0,
                is_world_cup=match["is_world_cup"],
                is_qualifier=match["is_qualifier"],
                is_friendly=match["is_friendly"],
                source="historical_real",
            ))
            count += 1

    session.flush()
    return count


def seed_combined_history(session: Session) -> int:
    """Load real history first, then fill in mock data for teams with <5 records."""
    session.execute(delete(TeamProfileMatchHistory).where(TeamProfileMatchHistory.source == "seed_mock_v1"))
    real_count = load_real_history(session)

    # Count how many real records each team has
    teams = list(session.scalars(select(Team).order_by(Team.id)))
    teams_needing_mock: list[str] = []
    for team in teams:
        record_count = session.scalar(
            select(func.count()).select_from(TeamProfileMatchHistory).where(
                TeamProfileMatchHistory.team_id == team.id,
                TeamProfileMatchHistory.source == "historical_real",
            )
        )
        if record_count < 5:
            teams_needing_mock.append(team.id)

    mock_count = 0
    if teams_needing_mock:
        mock_count = seed_mock_history(session, set(teams_needing_mock))

    return real_count + mock_count
