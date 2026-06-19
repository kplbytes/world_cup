from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Team, TeamProfileMatchHistory, TeamRating
from app.team_profiles.feature_engineering import classify_opponent_tier

# Curated real World Cup 2014/2018/2022 results for 2026 participants.
# Each entry: (competition, stage, date_str, team1_id, team1_goals, team2_id, team2_goals, neutral)
_REAL_WORLD_CUP_RESULTS: list[tuple[str, str, str, str, int, str, int, bool]] = [
    # --- World Cup 2014 ---
    ("world_cup_2014", "group", "2014-06-12", "BRA", 3, "CRO", 1, True),
    ("world_cup_2014", "group", "2014-06-13", "ESP", 1, "NED", 5, True),
    ("world_cup_2014", "group", "2014-06-13", "MEX", 1, "CMR", 0, True),
    ("world_cup_2014", "group", "2014-06-14", "ENG", 1, "ITA", 2, True),
    ("world_cup_2014", "group", "2014-06-18", "ESP", 0, "CHI", 2, True),
    ("world_cup_2014", "group", "2014-06-18", "NED", 3, "AUS", 2, True),
    ("world_cup_2014", "group", "2014-06-18", "CRO", 4, "CMR", 0, True),
    ("world_cup_2014", "group", "2014-06-23", "NED", 2, "CHI", 0, True),
    ("world_cup_2014", "group", "2014-06-23", "ESP", 3, "AUS", 0, True),
    ("world_cup_2014", "group", "2014-06-23", "CRO", 1, "MEX", 3, True),
    ("world_cup_2014", "group", "2014-06-24", "ENG", 0, "CRC", 0, True),
    ("world_cup_2014", "group", "2014-06-25", "POR", 2, "USA", 1, True),
    ("world_cup_2014", "group", "2014-06-26", "NGA", 2, "ARG", 3, True),
    ("world_cup_2014", "group", "2014-06-26", "BIH", 3, "IRN", 1, True),
    ("world_cup_2014", "group", "2014-06-25", "GER", 1, "USA", 0, True),
    ("world_cup_2014", "group", "2014-06-26", "BEL", 1, "KOR", 0, True),
    ("world_cup_2014", "group", "2014-06-26", "ALG", 1, "RUS", 1, True),
    ("world_cup_2014", "knockout", "2014-06-28", "BRA", 1, "CHI", 1, True),
    ("world_cup_2014", "knockout", "2014-06-28", "COL", 2, "URU", 0, True),
    ("world_cup_2014", "knockout", "2014-06-30", "GER", 2, "ALG", 1, True),
    ("world_cup_2014", "knockout", "2014-07-01", "ARG", 1, "SUI", 0, True),
    ("world_cup_2014", "knockout", "2014-07-04", "BRA", 2, "COL", 1, True),
    ("world_cup_2014", "knockout", "2014-07-04", "GER", 1, "FRA", 0, True),
    ("world_cup_2014", "knockout", "2014-07-05", "NED", 0, "CRC", 0, True),
    ("world_cup_2014", "knockout", "2014-07-05", "ARG", 1, "BEL", 0, True),
    ("world_cup_2014", "knockout", "2014-07-08", "GER", 7, "BRA", 1, True),
    ("world_cup_2014", "knockout", "2014-07-09", "NED", 0, "ARG", 0, True),
    ("world_cup_2014", "knockout", "2014-07-12", "BRA", 0, "NED", 3, True),
    ("world_cup_2014", "knockout", "2014-07-13", "GER", 1, "ARG", 0, True),
    # --- World Cup 2018 ---
    ("world_cup_2018", "group", "2018-06-14", "RUS", 5, "KSA", 0, True),
    ("world_cup_2018", "group", "2018-06-15", "EGY", 0, "URU", 1, True),
    ("world_cup_2018", "group", "2018-06-15", "POR", 3, "ESP", 3, True),
    ("world_cup_2018", "group", "2018-06-16", "FRA", 2, "AUS", 1, True),
    ("world_cup_2018", "group", "2018-06-16", "ARG", 1, "ISL", 1, True),
    ("world_cup_2018", "group", "2018-06-17", "GER", 0, "MEX", 1, True),
    ("world_cup_2018", "group", "2018-06-17", "BRA", 1, "SUI", 1, True),
    ("world_cup_2018", "group", "2018-06-18", "SWE", 1, "KOR", 0, True),
    ("world_cup_2018", "group", "2018-06-18", "BEL", 3, "PAN", 0, True),
    ("world_cup_2018", "group", "2018-06-19", "ENG", 2, "TUN", 1, True),
    ("world_cup_2018", "group", "2018-06-19", "COL", 1, "JPN", 2, True),
    ("world_cup_2018", "group", "2018-06-19", "POL", 1, "SEN", 2, True),
    ("world_cup_2018", "group", "2018-06-20", "POR", 1, "MAR", 0, True),
    ("world_cup_2018", "group", "2018-06-21", "ARG", 0, "CRO", 3, True),
    ("world_cup_2018", "group", "2018-06-23", "GER", 2, "SWE", 1, True),
    ("world_cup_2018", "group", "2018-06-23", "MEX", 0, "SWE", 3, True),
    ("world_cup_2018", "group", "2018-06-24", "ENG", 6, "PAN", 1, True),
    ("world_cup_2018", "group", "2018-06-25", "POR", 1, "IRN", 1, True),
    ("world_cup_2018", "group", "2018-06-26", "FRA", 0, "DEN", 0, True),
    ("world_cup_2018", "group", "2018-06-26", "ARG", 2, "NGA", 1, True),
    ("world_cup_2018", "group", "2018-06-27", "GER", 0, "KOR", 2, True),
    ("world_cup_2018", "group", "2018-06-27", "MEX", 0, "SWE", 3, True),
    ("world_cup_2018", "group", "2018-06-28", "JPN", 0, "POL", 1, True),
    ("world_cup_2018", "group", "2018-06-28", "SEN", 0, "COL", 1, True),
    ("world_cup_2018", "knockout", "2018-06-30", "FRA", 4, "ARG", 3, True),
    ("world_cup_2018", "knockout", "2018-07-02", "BRA", 2, "MEX", 0, True),
    ("world_cup_2018", "knockout", "2018-07-02", "BEL", 3, "JPN", 2, True),
    ("world_cup_2018", "knockout", "2018-07-06", "FRA", 2, "URU", 0, True),
    ("world_cup_2018", "knockout", "2018-07-06", "BRA", 1, "BEL", 2, True),
    ("world_cup_2018", "knockout", "2018-07-07", "SWE", 0, "ENG", 2, True),
    ("world_cup_2018", "knockout", "2018-07-07", "CRO", 2, "RUS", 2, True),
    ("world_cup_2018", "knockout", "2018-07-10", "FRA", 1, "BEL", 0, True),
    ("world_cup_2018", "knockout", "2018-07-11", "CRO", 2, "ENG", 1, True),
    ("world_cup_2018", "knockout", "2018-07-14", "BEL", 2, "ENG", 0, True),
    ("world_cup_2018", "knockout", "2018-07-15", "FRA", 4, "CRO", 2, True),
    # --- World Cup 2022 ---
    ("world_cup_2022", "group", "2022-11-20", "QAT", 0, "ECU", 2, True),
    ("world_cup_2022", "group", "2022-11-21", "ENG", 6, "IRN", 2, True),
    ("world_cup_2022", "group", "2022-11-22", "ARG", 1, "KSA", 2, True),
    ("world_cup_2022", "group", "2022-11-22", "MEX", 0, "POL", 0, True),
    ("world_cup_2022", "group", "2022-11-23", "BEL", 1, "CAN", 0, True),
    ("world_cup_2022", "group", "2022-11-23", "ESP", 7, "CRC", 0, True),
    ("world_cup_2022", "group", "2022-11-24", "URU", 0, "KOR", 0, True),
    ("world_cup_2022", "group", "2022-11-24", "POR", 3, "GHA", 2, True),
    ("world_cup_2022", "group", "2022-11-24", "BRA", 2, "SRB", 0, True),
    ("world_cup_2022", "group", "2022-11-25", "NED", 2, "SEN", 0, True),
    ("world_cup_2022", "group", "2022-11-26", "ARG", 2, "MEX", 0, True),
    ("world_cup_2022", "group", "2022-11-26", "FRA", 2, "DEN", 1, True),
    ("world_cup_2022", "group", "2022-11-27", "JPN", 0, "CRC", 1, True),
    ("world_cup_2022", "group", "2022-11-27", "BEL", 0, "MAR", 2, True),
    ("world_cup_2022", "group", "2022-11-28", "BRA", 1, "SUI", 0, True),
    ("world_cup_2022", "group", "2022-11-28", "POR", 2, "URU", 0, True),
    ("world_cup_2022", "group", "2022-11-29", "NED", 2, "QAT", 0, True),
    ("world_cup_2022", "group", "2022-11-30", "ARG", 2, "POL", 0, True),
    ("world_cup_2022", "group", "2022-11-30", "MEX", 2, "KSA", 1, True),
    ("world_cup_2022", "group", "2022-11-30", "FRA", 0, "TUN", 1, True),
    ("world_cup_2022", "group", "2022-12-01", "ESP", 1, "JPN", 2, True),
    ("world_cup_2022", "group", "2022-12-01", "GER", 4, "CRC", 2, True),
    ("world_cup_2022", "group", "2022-12-01", "BEL", 0, "CRO", 0, True),
    ("world_cup_2022", "group", "2022-12-02", "POR", 1, "KOR", 2, True),
    ("world_cup_2022", "group", "2022-12-02", "GHA", 0, "URU", 2, True),
    ("world_cup_2022", "group", "2022-12-02", "BRA", 0, "CMR", 1, True),
    ("world_cup_2022", "group", "2022-12-02", "SUI", 3, "SRB", 2, True),
    ("world_cup_2022", "knockout", "2022-12-03", "NED", 3, "USA", 1, True),
    ("world_cup_2022", "knockout", "2022-12-03", "ARG", 2, "AUS", 1, True),
    ("world_cup_2022", "knockout", "2022-12-04", "FRA", 3, "POL", 1, True),
    ("world_cup_2022", "knockout", "2022-12-04", "ENG", 3, "SEN", 0, True),
    ("world_cup_2022", "knockout", "2022-12-05", "JPN", 1, "CRO", 1, True),
    ("world_cup_2022", "knockout", "2022-12-05", "BRA", 4, "KOR", 1, True),
    ("world_cup_2022", "knockout", "2022-12-06", "MAR", 0, "ESP", 0, True),
    ("world_cup_2022", "knockout", "2022-12-06", "POR", 6, "SUI", 1, True),
    ("world_cup_2022", "knockout", "2022-12-09", "CRO", 2, "BRA", 1, True),
    ("world_cup_2022", "knockout", "2022-12-09", "NED", 2, "ARG", 2, True),
    ("world_cup_2022", "knockout", "2022-12-10", "MAR", 1, "POR", 0, True),
    ("world_cup_2022", "knockout", "2022-12-10", "ENG", 1, "FRA", 2, True),
    ("world_cup_2022", "knockout", "2022-12-13", "ARG", 3, "CRO", 0, True),
    ("world_cup_2022", "knockout", "2022-12-14", "FRA", 2, "MAR", 0, True),
    ("world_cup_2022", "knockout", "2022-12-17", "CRO", 2, "MAR", 1, True),
    ("world_cup_2022", "knockout", "2022-12-18", "ARG", 3, "FRA", 3, True),
]


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
    """Load real World Cup 2014/2018/2022 match results for 2026 World Cup teams.

    Only matches where at least one team is a 2026 participant are included.
    Creates TeamProfileMatchHistory records with source="historical_real".
    """
    # Build lookup of 2026 WC teams by code
    teams = list(session.scalars(select(Team).order_by(Team.id)))
    team_by_code: dict[str, Team] = {t.code: t for t in teams}

    # Build elo lookup
    ratings: dict[str, float] = {}
    for team in teams:
        rating = session.scalar(
            select(TeamRating)
            .where(TeamRating.team_id == team.id)
            .order_by(TeamRating.effective_date.desc())
            .limit(1)
        )
        ratings[team.id] = rating.elo if rating else 1500.0

    # Delete previous real history records
    session.execute(delete(TeamProfileMatchHistory).where(TeamProfileMatchHistory.source == "historical_real"))

    count = 0
    for competition, stage, date_str, t1_code, t1_goals, t2_code, t2_goals, neutral in _REAL_WORLD_CUP_RESULTS:
        t1 = team_by_code.get(t1_code)
        t2 = team_by_code.get(t2_code)
        # Only include matches where at least one team is in the 2026 WC
        if t1 is None and t2 is None:
            continue

        match_date = date.fromisoformat(date_str)
        is_world_cup = competition.startswith("world_cup")

        # Create record from team1's perspective
        if t1 is not None:
            opp_id = t2.id if t2 else None
            opp_name = t2.short_name if t2 else t2_code
            opp_elo = ratings.get(opp_id, 1500.0) if opp_id else 1500.0
            gf, ga = t1_goals, t2_goals
            result = "win" if gf > ga else "draw" if gf == ga else "loss"
            session.add(TeamProfileMatchHistory(
                team_id=t1.id,
                match_date=match_date,
                competition=competition,
                stage=stage,
                opponent_team_id=opp_id,
                opponent_name=opp_name,
                opponent_elo=opp_elo,
                opponent_tier=classify_opponent_tier(opp_elo),
                is_neutral=neutral,
                is_home=True,
                goals_for=gf,
                goals_against=ga,
                result=result,
                points=3 if result == "win" else 1 if result == "draw" else 0,
                is_world_cup=is_world_cup,
                is_qualifier=False,
                is_friendly=False,
                source="historical_real",
            ))
            count += 1

        # Create record from team2's perspective
        if t2 is not None:
            opp_id = t1.id if t1 else None
            opp_name = t1.short_name if t1 else t1_code
            opp_elo = ratings.get(opp_id, 1500.0) if opp_id else 1500.0
            gf, ga = t2_goals, t1_goals
            result = "win" if gf > ga else "draw" if gf == ga else "loss"
            session.add(TeamProfileMatchHistory(
                team_id=t2.id,
                match_date=match_date,
                competition=competition,
                stage=stage,
                opponent_team_id=opp_id,
                opponent_name=opp_name,
                opponent_elo=opp_elo,
                opponent_tier=classify_opponent_tier(opp_elo),
                is_neutral=neutral,
                is_home=False,
                goals_for=gf,
                goals_against=ga,
                result=result,
                points=3 if result == "win" else 1 if result == "draw" else 0,
                is_world_cup=is_world_cup,
                is_qualifier=False,
                is_friendly=False,
                source="historical_real",
            ))
            count += 1

    session.flush()
    return count


def seed_combined_history(session: Session) -> int:
    """Load real history first, then fill in mock data for teams with <5 records."""
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
