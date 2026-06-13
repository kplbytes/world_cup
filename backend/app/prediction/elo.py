from dataclasses import dataclass
from datetime import date
from math import log1p


@dataclass(frozen=True)
class EloPair:
    home: float
    away: float


@dataclass(frozen=True)
class RatedMatch:
    played_on: date
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    weight: float = 30.0
    neutral: bool = True


def update_elo(
    home: float,
    away: float,
    home_goals: int,
    away_goals: int,
    weight: float = 30.0,
    home_advantage: float = 0.0,
) -> EloPair:
    expected_home = 1.0 / (1.0 + 10 ** ((away - (home + home_advantage)) / 400.0))
    if home_goals > away_goals:
        actual_home = 1.0
    elif home_goals < away_goals:
        actual_home = 0.0
    else:
        actual_home = 0.5
    goal_margin = abs(home_goals - away_goals)
    margin_multiplier = 1.0 if goal_margin <= 1 else 1.0 + 0.5 * log1p(goal_margin - 1)
    change = weight * margin_multiplier * (actual_home - expected_home)
    return EloPair(home=home + change, away=away - change)


def replay_elo(
    matches: list[RatedMatch],
    cutoff: date,
    initial_rating: float = 1500.0,
) -> dict[str, float]:
    ratings: dict[str, float] = {}
    for match in sorted(matches, key=lambda item: item.played_on):
        if match.played_on > cutoff:
            continue
        home = ratings.get(match.home_team, initial_rating)
        away = ratings.get(match.away_team, initial_rating)
        updated = update_elo(
            home,
            away,
            match.home_goals,
            match.away_goals,
            weight=match.weight,
            home_advantage=0.0 if match.neutral else 60.0,
        )
        ratings[match.home_team] = updated.home
        ratings[match.away_team] = updated.away
    return ratings


def recent_form(team: str, matches: list[RatedMatch], limit: int = 5) -> str:
    results: list[str] = []
    for match in sorted(matches, key=lambda item: item.played_on):
        if team not in (match.home_team, match.away_team):
            continue
        team_goals, opponent_goals = (
            (match.home_goals, match.away_goals)
            if team == match.home_team
            else (match.away_goals, match.home_goals)
        )
        results.append("W" if team_goals > opponent_goals else "L" if team_goals < opponent_goals else "D")
    return "".join(results[-limit:])

