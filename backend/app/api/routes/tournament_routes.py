from fastapi import APIRouter
import threading
import time as _time

from app.db import session_scope


router = APIRouter()

MAX_SIMULATION_ITERATIONS = 100_000

# ---------------------------------------------------------------------------
# Simple TTL cache for GET /tournament/projections (5 minutes)
# ---------------------------------------------------------------------------
_projections_cache: dict | None = None
_projections_cache_ts: float = 0.0
_PROJECTIONS_CACHE_TTL = 300.0  # seconds
_projections_cache_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Per-team_id TTL cache for GET /tournament/team-path (5 minutes)
# P2-D: get_team_path runs a 5000-iteration Monte Carlo simulation per call.
# Cache the result per team_id for 5 minutes to avoid re-simulating on every
# page refresh. Cache size is capped at 64 teams (LRU-ish eviction).
# ---------------------------------------------------------------------------
_team_path_cache: dict[str, dict] = {}
_team_path_cache_lock = threading.Lock()
_TEAM_PATH_CACHE_TTL = 300.0  # seconds


def invalidate_team_path_cache() -> None:
    """Invalidate the per-team_id cache. Called by invalidate_dashboard_caches()."""
    with _team_path_cache_lock:
        _team_path_cache.clear()


@router.get("/tournament/bracket")
def tournament_bracket():
    """Get current tournament bracket."""
    from app.tournament.knockout import get_knockout_bracket_payload
    with session_scope() as session:
        return get_knockout_bracket_payload(session)


@router.get("/tournament/projections")
def tournament_projections():
    """Get all teams' tournament progression probabilities."""
    global _projections_cache, _projections_cache_ts
    now = _time.monotonic()
    with _projections_cache_lock:
        if _projections_cache is not None and (now - _projections_cache_ts) < _PROJECTIONS_CACHE_TTL:
            return _projections_cache

    from app.tournament.simulation import run_tournament_simulation
    with session_scope() as session:
        # iterations=5000 is intentionally low for the GET endpoint
        projections = run_tournament_simulation(session, iterations=5000)
    result = {
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
    with _projections_cache_lock:
        _projections_cache = result
        _projections_cache_ts = _time.monotonic()
    return result


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
    # P2-D: per-team_id TTL cache (5 min). Monte Carlo simulation with 5000
    # iterations is expensive; per-team caching keeps responses snappy while
    # ensuring different teams are tracked independently.
    now = _time.monotonic()
    with _team_path_cache_lock:
        cached = _team_path_cache.get(team_id)
        if cached is not None and (now - cached["ts"]) < _TEAM_PATH_CACHE_TTL:
            return cached["value"]

    from app.tournament.simulation import get_team_path
    # TODO: get_team_path() internally calls run_tournament_simulation(iterations=5000);
    # consider adding iteration limit protection there as well.
    with session_scope() as session:
        result = get_team_path(session, team_id)

    with _team_path_cache_lock:
        _team_path_cache[team_id] = {"value": result, "ts": _time.monotonic()}
        # Cap cache size to avoid unbounded growth if many different teams
        # are queried. Keep most-recently-written entries (LRU-ish).
        if len(_team_path_cache) > 64:
            oldest_key = min(_team_path_cache, key=lambda k: _team_path_cache[k]["ts"])
            _team_path_cache.pop(oldest_key, None)
    return result


@router.get("/tournament/standings")
def tournament_standings():
    """Get current group standings for all groups."""
    from app.tournament.standings import get_current_standings
    with session_scope() as session:
        return get_current_standings(session)
