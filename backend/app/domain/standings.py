from dataclasses import dataclass, replace
from itertools import groupby


@dataclass(frozen=True)
class MatchResult:
    home_team_id: str
    away_team_id: str
    home_score: int
    away_score: int


@dataclass(frozen=True)
class StandingRow:
    team_id: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0
    tiebreak_uncertain: bool = False

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


@dataclass(frozen=True)
class ThirdPlaceRanking:
    qualified: list[StandingRow]
    eliminated: list[StandingRow]


def rank_group(team_ids: list[str], matches: list[MatchResult]) -> list[StandingRow]:
    if len(team_ids) != 4 or len(set(team_ids)) != 4:
        raise ValueError("a group requires four unique teams")
    unknown = {
        team_id
        for match in matches
        for team_id in (match.home_team_id, match.away_team_id)
        if team_id not in team_ids
    }
    if unknown:
        raise ValueError(f"match references unknown teams: {sorted(unknown)}")

    rows = _calculate_rows(team_ids, matches)
    overall = sorted(rows, key=_ranking_key)
    ranked: list[StandingRow] = []
    for _, tied_group in groupby(overall, key=_overall_values):
        tied = list(tied_group)
        if len(tied) == 1:
            ranked.extend(tied)
            continue
        ranked.extend(_resolve_head_to_head(tied, matches))
    return ranked


def rank_third_placed(rows: list[StandingRow]) -> ThirdPlaceRanking:
    if len(rows) != 12:
        raise ValueError("third-place ranking requires twelve teams")
    ranked = sorted(rows, key=_ranking_key)
    return ThirdPlaceRanking(qualified=ranked[:8], eliminated=ranked[8:])


def _calculate_rows(team_ids: list[str], matches: list[MatchResult]) -> list[StandingRow]:
    values = {team_id: StandingRow(team_id=team_id) for team_id in team_ids}
    for match in matches:
        home_points, away_points = _points(match.home_score, match.away_score)
        home = values[match.home_team_id]
        away = values[match.away_team_id]
        values[home.team_id] = replace(
            home,
            played=home.played + 1,
            won=home.won + (match.home_score > match.away_score),
            drawn=home.drawn + (match.home_score == match.away_score),
            lost=home.lost + (match.home_score < match.away_score),
            goals_for=home.goals_for + match.home_score,
            goals_against=home.goals_against + match.away_score,
            points=home.points + home_points,
        )
        values[away.team_id] = replace(
            away,
            played=away.played + 1,
            won=away.won + (match.away_score > match.home_score),
            drawn=away.drawn + (match.away_score == match.home_score),
            lost=away.lost + (match.away_score < match.home_score),
            goals_for=away.goals_for + match.away_score,
            goals_against=away.goals_against + match.home_score,
            points=away.points + away_points,
        )
    return list(values.values())


def _resolve_head_to_head(tied: list[StandingRow], matches: list[MatchResult]) -> list[StandingRow]:
    tied_ids = {row.team_id for row in tied}
    mini_matches = [
        match
        for match in matches
        if match.home_team_id in tied_ids and match.away_team_id in tied_ids
    ]
    mini_rows = _calculate_rows(sorted(tied_ids), mini_matches)
    mini_by_id = {row.team_id: row for row in mini_rows}
    ordered = sorted(
        tied,
        key=lambda row: (
            -mini_by_id[row.team_id].points,
            -mini_by_id[row.team_id].goal_difference,
            -mini_by_id[row.team_id].goals_for,
            row.team_id,
        ),
    )

    result: list[StandingRow] = []
    for _, unresolved_group in groupby(
        ordered,
        key=lambda row: (
            mini_by_id[row.team_id].points,
            mini_by_id[row.team_id].goal_difference,
            mini_by_id[row.team_id].goals_for,
        ),
    ):
        unresolved = list(unresolved_group)
        uncertain = len(unresolved) > 1
        result.extend(replace(row, tiebreak_uncertain=uncertain) for row in unresolved)
    return result


def _points(home_score: int, away_score: int) -> tuple[int, int]:
    if home_score > away_score:
        return 3, 0
    if away_score > home_score:
        return 0, 3
    return 1, 1


def _overall_values(row: StandingRow) -> tuple[int, int, int]:
    return row.points, row.goal_difference, row.goals_for


def _ranking_key(row: StandingRow) -> tuple[int, int, int, str]:
    return -row.points, -row.goal_difference, -row.goals_for, row.team_id

