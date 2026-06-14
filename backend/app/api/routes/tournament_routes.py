from fastapi import APIRouter

from app.db import session_scope


router = APIRouter()

MAX_SIMULATION_ITERATIONS = 100_000


@router.get("/tournament/bracket")
def tournament_bracket():
    """Get current tournament bracket."""
    from app.tournament.standings import get_current_standings, get_third_placed_ranking
    from app.tournament.bracket import generate_bracket
    with session_scope() as session:
        standings = get_current_standings(session)
        third_placed = get_third_placed_ranking(session)
        bracket = generate_bracket(standings, third_placed)
    return bracket


@router.get("/tournament/projections")
def tournament_projections():
    """Get all teams' tournament progression probabilities."""
    from app.tournament.simulation import run_tournament_simulation
    with session_scope() as session:
        # iterations=5000 is intentionally low for the GET endpoint
        projections = run_tournament_simulation(session, iterations=5000)
    return {
        "projections": [
            {
                "team_id": p.team_id,
                "group_qualify": p.group_qualify,
                "round_of_32": p.round_of_32,
                "round_of_16": p.round_of_16,
                "quarter_final": p.quarter_final,
                "semi_final": p.semi_final,
                "final": p.final,
                "champion": p.champion,
            }
            for p in projections
        ]
    }


@router.post("/tournament/simulate")
def tournament_simulate(iterations: int = 10000, seed: int = 20260613):
    """Run full tournament Monte Carlo simulation."""
    from app.tournament.simulation import run_tournament_simulation
    actual_iterations = min(iterations, MAX_SIMULATION_ITERATIONS)
    with session_scope() as session:
        projections = run_tournament_simulation(session, iterations=actual_iterations, seed=seed)
    return {
        "iterations_requested": iterations,
        "iterations_used": actual_iterations,
        "projections": [
            {
                "team_id": p.team_id,
                "group_qualify": p.group_qualify,
                "round_of_32": p.round_of_32,
                "round_of_16": p.round_of_16,
                "quarter_final": p.quarter_final,
                "semi_final": p.semi_final,
                "final": p.final,
                "champion": p.champion,
            }
            for p in projections
        ]
    }


@router.get("/tournament/team-path")
def tournament_team_path(team_id: str):
    """Get a team's potential tournament path."""
    from app.tournament.simulation import get_team_path
    # TODO: get_team_path() internally calls run_tournament_simulation(iterations=5000);
    # consider adding iteration limit protection there as well.
    with session_scope() as session:
        return get_team_path(session, team_id)


@router.get("/tournament/standings")
def tournament_standings():
    """Get current group standings for all groups."""
    from app.tournament.standings import get_current_standings
    with session_scope() as session:
        return get_current_standings(session)
