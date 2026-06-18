"""Calibrate Poisson model parameters against completed match results.

Reads all completed matches from the database, computes predicted probabilities
using the current Poisson model, then does a grid search over key parameters
to find values that minimize the Brier score.

Usage:
    cd backend
    python3 -m scripts.calibrate_model
"""

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import poisson

# Ensure backend/ is on sys.path so `app` imports work
_backend_dir = Path(__file__).resolve().parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from sqlalchemy import select

from app.db import create_database, session_scope
from app.models import Match, TeamRating

_MAX_EXACT_GOALS = 7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _latest_ratings(session) -> dict[str, float]:
    """Return {team_id: elo} for the latest rating of every team."""
    by_team: dict[str, list[TeamRating]] = defaultdict(list)
    for rating in session.scalars(
        select(TeamRating).order_by(TeamRating.team_id, TeamRating.effective_date.desc())
    ):
        by_team[rating.team_id].append(rating)
    missing = [tid for tid, ratings in by_team.items() if not ratings]
    if missing:
        print(f"WARNING: {len(missing)} teams have no ratings, skipping them")
    return {tid: ratings[0].elo for tid, ratings in by_team.items() if ratings}


def _normalize_strengths(ratings: dict[str, float]) -> dict[str, float]:
    """Min-max normalize Elo ratings to [0, 1], same as recompute.py."""
    if not ratings:
        return {}
    minimum = min(ratings.values())
    maximum = max(ratings.values())
    spread = maximum - minimum or 1.0
    return {tid: (elo - minimum) / spread for tid, elo in ratings.items()}


def _poisson_matrix(home_xg: np.ndarray, away_xg: np.ndarray) -> np.ndarray:
    """Compute Poisson goal matrices for arrays of xG values.

    Args:
        home_xg: shape (N,) or (N, P) expected goals for home
        away_xg: shape (N,) or (N, P) expected goals for away (broadcastable with home_xg)

    Returns:
        matrix of shape (*broadcast_shape, 8, 8) with joint goal probabilities
    """
    goals = np.arange(_MAX_EXACT_GOALS + 1)  # (8,)
    # home_probs: (*shape, 8), away_probs: (*shape, 8)
    home_probs = poisson.pmf(goals, home_xg[..., np.newaxis])
    away_probs = poisson.pmf(goals, away_xg[..., np.newaxis])
    # matrix: (*shape, 8, 8)
    matrix = home_probs[..., np.newaxis] * away_probs[..., np.newaxis, :]
    return matrix


def predict_probs_vectorized(
    home_strength: np.ndarray,   # (N,)
    away_strength: np.ndarray,   # (N,)
    base_goal_home: np.ndarray,  # (P,)
    base_goal_away: np.ndarray,  # (P,)
    str_coeff_home: np.ndarray,  # (P,)
    str_coeff_away: np.ndarray,  # (P,)
    draw_boost: np.ndarray,      # (P,)
    favorite_dampening: np.ndarray,  # (P,)
    min_xg: float = 0.20,
    max_xg: float = 3.50,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute (home_win, draw, away_win) for all N matches x P parameter combos.

    Returns arrays of shape (N, P).
    """
    N = len(home_strength)
    P = len(base_goal_home)

    # Expand match data: (N, 1) to broadcast with (1, P) -> (N, P)
    hs = home_strength[:, np.newaxis]  # (N, 1)
    aws = away_strength[:, np.newaxis]  # (N, 1)
    strength_delta = hs - aws  # (N, 1)

    bgh = base_goal_home[np.newaxis, :]  # (1, P)
    bga = base_goal_away[np.newaxis, :]
    sch = str_coeff_home[np.newaxis, :]
    sca = str_coeff_away[np.newaxis, :]
    db = draw_boost[np.newaxis, :]
    fd = favorite_dampening[np.newaxis, :]

    # xG: (N, P)
    home_xg = np.clip(bgh + sch * strength_delta, min_xg, max_xg)
    away_xg = np.clip(bga - sca * strength_delta, min_xg, max_xg)

    # Poisson matrices: (N, P, 8, 8)
    matrix = _poisson_matrix(home_xg, away_xg)

    # Sum regions: (N, P)
    home_win = np.tril(matrix, k=-1).sum(axis=(-2, -1))
    draw = np.trace(matrix, axis1=-2, axis2=-1)
    away_win = np.triu(matrix, k=1).sum(axis=(-2, -1))
    total = home_win + draw + away_win
    home_win = home_win / total
    draw = draw / total
    away_win = away_win / total

    # Apply draw_boost: (N, P)
    draw_boosted = draw * db
    excess = draw_boosted - draw
    denom = home_win + away_win
    # Avoid division by zero
    safe_denom = np.where(denom > 0, denom, 1.0)
    home_win = home_win - excess * (home_win / safe_denom)
    away_win = away_win - excess * (away_win / safe_denom)
    draw = draw_boosted
    total = home_win + draw + away_win
    home_win = home_win / total
    draw = draw / total
    away_win = away_win / total

    # Apply favorite_dampening: (N, P)
    probs = np.stack([home_win, draw, away_win], axis=-1)  # (N, P, 3)
    max_idx = probs.argmax(axis=-1)  # (N, P)
    uniform = 1.0 / 3.0
    excess_fd = probs.max(axis=-1) - uniform  # (N, P)
    excess_fd = np.maximum(excess_fd, 0.0)

    # reduction per match-param combo
    reduction = excess_fd * fd  # (N, P)

    # Subtract from max, distribute to others
    # Build mask for which outcome is max: (N, P, 3)
    max_mask = np.zeros_like(probs, dtype=bool)
    n_idx, p_idx = np.meshgrid(np.arange(N), np.arange(P), indexing='ij')
    max_mask[n_idx, p_idx, max_idx] = True

    # Subtract reduction from max outcome
    probs[max_mask] -= reduction.ravel()

    # Distribute to other outcomes proportionally
    other_mask = ~max_mask
    other_sums = np.where(other_mask, probs, 0.0).sum(axis=-1, keepdims=True)
    safe_other_sums = np.where(other_sums > 0, other_sums, 1.0)
    distribution = reduction[..., np.newaxis] * np.where(other_mask, probs, 0.0) / safe_other_sums
    # If other_sums == 0, distribute equally
    equal_dist = reduction[..., np.newaxis] / 2.0  # 2 other outcomes
    distribution = np.where(other_sums > 0, distribution, np.where(other_mask, equal_dist, 0.0))
    probs = probs + distribution

    # Renormalize
    total = probs.sum(axis=-1, keepdims=True)
    probs = probs / total

    return probs[:, :, 0], probs[:, :, 1], probs[:, :, 2]


def brier_score_vec(
    home_win: np.ndarray,  # (N, P)
    draw: np.ndarray,
    away_win: np.ndarray,
    outcomes: np.ndarray,  # (N, 3) one-hot
) -> np.ndarray:  # (P,)
    """Compute mean Brier score per parameter combo."""
    pred = np.stack([home_win, draw, away_win], axis=-1)  # (N, P, 3)
    out = outcomes[:, np.newaxis, :]  # (N, 1, 3)
    sq = ((pred - out) ** 2).sum(axis=-1)  # (N, P)
    return sq.mean(axis=0)  # (P,)


def log_loss_vec(
    home_win: np.ndarray,
    draw: np.ndarray,
    away_win: np.ndarray,
    outcomes: np.ndarray,
    eps: float = 1e-15,
) -> np.ndarray:
    """Compute mean log loss per parameter combo."""
    pred = np.stack([home_win, draw, away_win], axis=-1)  # (N, P, 3)
    out = outcomes[:, np.newaxis, :]  # (N, 1, 3)
    p_actual = (pred * out).sum(axis=-1)  # (N, P)
    p_actual = np.maximum(p_actual, eps)
    return (-np.log(p_actual)).mean(axis=0)  # (P,)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    create_database()
    with session_scope() as session:
        # 1. Read all completed matches
        matches = list(session.scalars(
            select(Match).where(Match.status == "final")
            .where(Match.home_score.is_not(None))
            .where(Match.away_score.is_not(None))
        ))

        if not matches:
            print("No completed matches found in the database. Cannot calibrate.")
            return

        print(f"Found {len(matches)} completed matches for calibration.\n")

        # 2. Get Elo ratings and normalize strengths
        ratings = _latest_ratings(session)
        strengths = _normalize_strengths(ratings)

        # 3. Build match data arrays
        home_strengths = []
        away_strengths = []
        outcomes = []
        skipped = 0
        for m in matches:
            if m.home_team_id not in strengths or m.away_team_id not in strengths:
                skipped += 1
                continue
            home_strengths.append(strengths[m.home_team_id])
            away_strengths.append(strengths[m.away_team_id])
            if m.home_score > m.away_score:
                outcomes.append([1, 0, 0])
            elif m.home_score == m.away_score:
                outcomes.append([0, 1, 0])
            else:
                outcomes.append([0, 0, 1])

        if skipped:
            print(f"Skipped {skipped} matches due to missing team ratings.")
        print(f"Using {len(home_strengths)} matches for calibration.\n")

        if len(home_strengths) < 5:
            print("Too few matches for reliable calibration. Results may be unreliable.")

        home_str = np.array(home_strengths)
        away_str = np.array(away_strengths)
        outcomes_arr = np.array(outcomes, dtype=np.float64)

        # 4. Evaluate current default parameters
        current_params = {
            "base_goal_mean_home": 1.25,
            "base_goal_mean_away": 1.10,
            "strength_coeff_home": 0.90,
            "strength_coeff_away": 0.75,
            "draw_boost": 1.00,
            "favorite_dampening": 0.00,
        }

        def evaluate_single(params: dict) -> tuple[float, float]:
            hw, d, aw = predict_probs_vectorized(
                home_str, away_str,
                np.array([params["base_goal_mean_home"]]),
                np.array([params["base_goal_mean_away"]]),
                np.array([params["strength_coeff_home"]]),
                np.array([params["strength_coeff_away"]]),
                np.array([params["draw_boost"]]),
                np.array([params["favorite_dampening"]]),
            )
            bs = brier_score_vec(hw, d, aw, outcomes_arr)[0]
            ll = log_loss_vec(hw, d, aw, outcomes_arr)[0]
            return float(bs), float(ll)

        current_brier, current_ll = evaluate_single(current_params)
        print("=== Current Default Parameters ===")
        for k, v in current_params.items():
            print(f"  {k}: {v}")
        print(f"  Brier score: {current_brier:.6f}")
        print(f"  Log loss:    {current_ll:.6f}")
        print()

        # 5. Grid search — build all parameter combos as flat arrays
        print("=== Grid Search ===")
        print("Building parameter grid...\n")

        bgh_range = np.arange(0.80, 1.65, 0.05)
        bga_range = np.arange(0.70, 1.45, 0.05)
        sch_range = np.arange(0.50, 1.35, 0.05)
        sca_range = np.arange(0.40, 1.15, 0.05)
        db_range = np.arange(1.00, 1.22, 0.02)
        fd_range = np.arange(0.00, 0.16, 0.01)

        total_combos = len(bgh_range) * len(bga_range) * len(sch_range) * len(sca_range) * len(db_range) * len(fd_range)
        print(f"Total parameter combinations: {total_combos}")

        # Process in chunks to manage memory (each chunk processes a slice of base_goal_home)
        best_brier = float("inf")
        best_ll = float("inf")
        best_params = {}
        evaluated = 0

        for bgh in bgh_range:
            # For each base_goal_home value, build the full grid of other params
            # This keeps memory manageable: ~17 * other_dims combos at a time
            grid_bga, grid_sch, grid_sca, grid_db, grid_fd = np.meshgrid(
                bga_range, sch_range, sca_range, db_range, fd_range, indexing='ij'
            )
            P_chunk = grid_bga.size  # number of combos in this chunk

            flat_bgh = np.full(P_chunk, round(bgh, 2))
            flat_bga = grid_bga.ravel()
            flat_sch = grid_sch.ravel()
            flat_sca = grid_sca.ravel()
            flat_db = grid_db.ravel()
            flat_fd = grid_fd.ravel()

            hw, d, aw = predict_probs_vectorized(
                home_str, away_str,
                flat_bgh, flat_bga, flat_sch, flat_sca, flat_db, flat_fd,
            )
            bs = brier_score_vec(hw, d, aw, outcomes_arr)  # (P_chunk,)
            ll = log_loss_vec(hw, d, aw, outcomes_arr)  # (P_chunk,)

            best_in_chunk = int(bs.argmin())
            if bs[best_in_chunk] < best_brier:
                best_brier = float(bs[best_in_chunk])
                best_ll = float(ll[best_in_chunk])
                best_params = {
                    "base_goal_mean_home": round(float(flat_bgh[best_in_chunk]), 2),
                    "base_goal_mean_away": round(float(flat_bga[best_in_chunk]), 2),
                    "strength_coeff_home": round(float(flat_sch[best_in_chunk]), 2),
                    "strength_coeff_away": round(float(flat_sca[best_in_chunk]), 2),
                    "draw_boost": round(float(flat_db[best_in_chunk]), 2),
                    "favorite_dampening": round(float(flat_fd[best_in_chunk]), 2),
                }

            evaluated += P_chunk
            print(f"  Processed {evaluated}/{total_combos} combos... best Brier so far: {best_brier:.6f}")

        print(f"\nEvaluated {evaluated} combinations.\n")

        # 6. Print results
        print("=== Best Parameters (by Brier Score) ===")
        for k, v in best_params.items():
            print(f"  {k}: {v}")
        print(f"  Brier score: {best_brier:.6f}")
        print(f"  Log loss:    {best_ll:.6f}")
        print()

        improvement = current_brier - best_brier
        pct = (improvement / current_brier * 100) if current_brier > 0 else 0
        print(f"Improvement over defaults: Brier {improvement:.6f} ({pct:.1f}%)")
        print()

        # 7. Print YAML snippet for easy copy-paste
        print("=== YAML config snippet ===")
        print("  elo-poisson-v2-calibrated:")
        print("    _base: elo_poisson_base")
        for k, v in best_params.items():
            print(f"    {k}: {v}")
        print('    description: "Calibrated v2: grid-search optimized against completed matches"')


if __name__ == "__main__":
    main()
