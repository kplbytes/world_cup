from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Team, TeamProfileMatchHistory, TeamRating
from app.team_profiles.feature_engineering import classify_opponent_tier


def seed_mock_history(session: Session) -> int:
    """Create deterministic, clearly-labelled profile history for local development."""
    teams = list(session.scalars(select(Team).order_by(Team.id)))
    ratings = {}
    for team in teams:
        rating = session.scalar(select(TeamRating).where(TeamRating.team_id == team.id).order_by(TeamRating.effective_date.desc()).limit(1))
        ratings[team.id] = rating.elo if rating else 1500.0

    session.execute(delete(TeamProfileMatchHistory).where(TeamProfileMatchHistory.source == "seed_mock_v1"))
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
