from dataclasses import dataclass
from math import sqrt
from time import perf_counter

import numpy as np

from app.domain.standings import MatchResult, rank_group, rank_third_placed


@dataclass(frozen=True)
class SimulatedMatch:
    id: str
    group_code: str
    home_team_id: str
    away_team_id: str
    score_matrix: list[list[float]]


@dataclass(frozen=True)
class SimulationTournament:
    groups: dict[str, list[str]]
    completed: list[MatchResult]
    remaining: list[SimulatedMatch]


@dataclass(frozen=True)
class TeamQualification:
    team_id: str
    first: float
    second: float
    third: float
    fourth: float
    qualify: float
    standard_error: float


@dataclass(frozen=True)
class QualificationResult:
    teams: list[TeamQualification]
    iterations: int
    seed: int


def simulate_qualification(
    tournament: SimulationTournament,
    iterations: int = 50_000,
    seed: int = 20260613,
) -> QualificationResult:
    _validate_tournament(tournament, iterations)
    rng = np.random.default_rng(seed)
    samples = {
        match.id: _sample_match(rng, match.score_matrix, iterations)
        for match in tournament.remaining
    }
    team_ids = sorted(team for teams in tournament.groups.values() for team in teams)
    placement_counts = {team_id: [0, 0, 0, 0] for team_id in team_ids}
    qualification_counts = {team_id: 0 for team_id in team_ids}
    completed_by_group = {
        group: [
            match
            for match in tournament.completed
            if match.home_team_id in teams and match.away_team_id in teams
        ]
        for group, teams in tournament.groups.items()
    }
    remaining_by_group = {
        group: [match for match in tournament.remaining if match.group_code == group]
        for group in tournament.groups
    }

    for iteration in range(iterations):
        tables = {}
        for group, teams in tournament.groups.items():
            sampled_results = [
                MatchResult(
                    match.home_team_id,
                    match.away_team_id,
                    int(samples[match.id][iteration, 0]),
                    int(samples[match.id][iteration, 1]),
                )
                for match in remaining_by_group[group]
            ]
            table = rank_group(teams, completed_by_group[group] + sampled_results)
            tables[group] = table
            for position, row in enumerate(table):
                placement_counts[row.team_id][position] += 1
            qualification_counts[table[0].team_id] += 1
            qualification_counts[table[1].team_id] += 1

        third_ranking = rank_third_placed([table[2] for table in tables.values()])
        for row in third_ranking.qualified:
            qualification_counts[row.team_id] += 1

    teams = []
    for team_id in team_ids:
        placements = [count / iterations for count in placement_counts[team_id]]
        qualify = qualification_counts[team_id] / iterations
        teams.append(
            TeamQualification(
                team_id=team_id,
                first=placements[0],
                second=placements[1],
                third=placements[2],
                fourth=placements[3],
                qualify=qualify,
                standard_error=sqrt(qualify * (1.0 - qualify) / iterations),
            )
        )
    return QualificationResult(teams=teams, iterations=iterations, seed=seed)


def _sample_match(
    rng: np.random.Generator,
    score_matrix: list[list[float]],
    iterations: int,
) -> np.ndarray:
    matrix = np.asarray(score_matrix, dtype=float)
    if matrix.ndim != 2 or matrix.size == 0 or not np.isfinite(matrix).all():
        raise ValueError("score matrix must be a finite two-dimensional matrix")
    if (matrix < 0).any() or matrix.sum() <= 0:
        raise ValueError("score matrix probabilities must be non-negative")
    probabilities = matrix.ravel() / matrix.sum()
    sampled = rng.choice(matrix.size, size=iterations, p=probabilities)
    return np.column_stack(np.unravel_index(sampled, matrix.shape))


def _validate_tournament(tournament: SimulationTournament, iterations: int) -> None:
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if sorted(tournament.groups) != list("ABCDEFGHIJKL"):
        raise ValueError("simulation requires groups A-L")
    all_teams = [team for teams in tournament.groups.values() for team in teams]
    if len(all_teams) != 48 or len(set(all_teams)) != 48:
        raise ValueError("simulation requires 48 unique teams")
    if any(len(teams) != 4 for teams in tournament.groups.values()):
        raise ValueError("each group requires four teams")


def _benchmark(iterations: int) -> None:
    groups = {
        group: [f"{group}{number}" for number in range(1, 5)]
        for group in "ABCDEFGHIJKL"
    }
    matrix = [[0.12, 0.10, 0.03], [0.15, 0.25, 0.08], [0.05, 0.14, 0.08]]
    remaining = []
    for group, teams in groups.items():
        for index, (home, away) in enumerate(((0, 1), (2, 3), (0, 2), (1, 3), (0, 3), (1, 2))):
            remaining.append(
                SimulatedMatch(f"{group}-{index}", group, teams[home], teams[away], matrix)
            )
    started = perf_counter()
    simulate_qualification(
        SimulationTournament(groups=groups, completed=[], remaining=remaining),
        iterations=iterations,
    )
    print(f"{iterations} simulations completed in {perf_counter() - started:.3f}s")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--iterations", type=int, default=50_000)
    args = parser.parse_args()
    if args.benchmark:
        _benchmark(args.iterations)

