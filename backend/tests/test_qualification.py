import pytest

from app.domain.standings import MatchResult
from app.simulation.qualification import (
    SimulatedMatch,
    SimulationTournament,
    simulate_qualification,
)


def simple_matrix():
    return [
        [0.10, 0.08, 0.02],
        [0.16, 0.24, 0.08],
        [0.08, 0.16, 0.08],
    ]


def tournament_fixture(all_completed=False):
    groups = {}
    completed = []
    remaining = []
    for group in "ABCDEFGHIJKL":
        teams = [f"{group}{number}" for number in range(1, 5)]
        groups[group] = teams
        pairings = [(0, 1), (2, 3), (0, 2), (1, 3), (0, 3), (1, 2)]
        for index, (home, away) in enumerate(pairings):
            if all_completed:
                completed.append(MatchResult(teams[home], teams[away], 2 if home == 0 else 1, 0))
            else:
                remaining.append(
                    SimulatedMatch(
                        id=f"{group}-{index}",
                        group_code=group,
                        home_team_id=teams[home],
                        away_team_id=teams[away],
                        score_matrix=simple_matrix(),
                    )
                )
    return SimulationTournament(groups=groups, completed=completed, remaining=remaining)


def test_simulation_is_reproducible_and_returns_all_teams():
    tournament = tournament_fixture()

    first = simulate_qualification(tournament, iterations=2000, seed=20260613)
    second = simulate_qualification(tournament, iterations=2000, seed=20260613)

    assert first == second
    assert len(first.teams) == 48
    assert all(
        team.first + team.second + team.third + team.fourth == pytest.approx(1.0)
        for team in first.teams
    )
    assert all(0.0 <= team.qualify <= 1.0 for team in first.teams)


def test_completed_groups_have_certain_placements_independent_of_seed():
    tournament = tournament_fixture(all_completed=True)

    first = simulate_qualification(tournament, iterations=20, seed=1)
    second = simulate_qualification(tournament, iterations=20, seed=999)

    assert first.teams == second.teams
    assert first.seed == 1
    assert second.seed == 999
    assert all(
        sorted([team.first, team.second, team.third, team.fourth]) == [0.0, 0.0, 0.0, 1.0]
        for team in first.teams
    )
