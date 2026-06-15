"""Backtesting model implementations with strict no-leakage guarantees.

Model A: Legacy Elo-Poisson (Control) - exact reproduction of production logic
Model B: Refitted Elo-Poisson - scipy-optimized parameters on training set
Model C: Dixon-Coles - Poisson with low-score adjustment and time decay
Model D: Negative Binomial - overdispersed count model
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.stats import nbinom, poisson

from app.backtesting.elo_replay import ReplayStep
from app.prediction.poisson import MatchContext, predict_match

logger = logging.getLogger(__name__)

_MAX_GOALS = 7


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def elo_to_strength(
    home_elo: float,
    away_elo: float,
    all_elos: list[float],
) -> tuple[float, float]:
    """Normalize Elo to [0,1] strength using min-max across all teams."""
    elo_min = min(all_elos)
    elo_max = max(all_elos)
    denom = elo_max - elo_min
    if denom < 1e-9:
        return 0.5, 0.5
    home_s = (home_elo - elo_min) / denom
    away_s = (away_elo - elo_min) / denom
    return float(np.clip(home_s, 0.0, 1.0)), float(np.clip(away_s, 0.0, 1.0))


def _outcome_onehot(home_score: int, away_score: int) -> tuple[float, float, float]:
    """Return one-hot encoding of match outcome (home, draw, away)."""
    if home_score > away_score:
        return (1.0, 0.0, 0.0)
    elif home_score == away_score:
        return (0.0, 1.0, 0.0)
    else:
        return (0.0, 0.0, 1.0)


def _brier_sum(p: tuple[float, float, float], o: tuple[float, float, float]) -> float:
    """Sum-of-squares Brier for one match: sum((p_i - o_i)^2)."""
    return sum((pi - oi) ** 2 for pi, oi in zip(p, o))


# ---------------------------------------------------------------------------
# Model A: Legacy Elo-Poisson (Control)
# ---------------------------------------------------------------------------

@dataclass
class LegacyModel:
    """Exact reproduction of current production Elo-Poisson logic."""

    name: str = "legacy-elo-poisson"

    def predict(
        self,
        step: ReplayStep,
        all_elos: list[float],
    ) -> tuple[float, float, float]:
        """Return (home_win, draw, away_win) using default production parameters."""
        home_s, away_s = elo_to_strength(
            step.pre_match_home_elo,
            step.pre_match_away_elo,
            all_elos,
        )
        ctx = MatchContext(
            data_freshness=1.0,
            ranking_coverage=1.0,
            history_coverage=1.0,
            provider_agreement=1.0,
        )
        result = predict_match(home_s, away_s, ctx)
        return (result.home_win, result.draw, result.away_win)

    def get_parameters(self) -> dict[str, Any]:
        return {
            "base_goal_mean_home": 1.25,
            "base_goal_mean_away": 1.10,
            "strength_coeff_home": 0.90,
            "strength_coeff_away": 0.75,
            "min_xg": 0.20,
            "max_xg": 3.50,
            "draw_boost": 1.00,
            "favorite_dampening": 0.00,
            "underdog_boost": 0.00,
            "home_advantage_elo": 60.0,
        }


# ---------------------------------------------------------------------------
# Model B: Refitted Elo-Poisson
# ---------------------------------------------------------------------------

@dataclass
class RefittedModel:
    """Elo-Poisson with parameters fitted on training set via scipy.optimize."""

    name: str = "refitted-elo-poisson"
    parameters: dict[str, float] = field(default_factory=dict)

    # Default starting point (production values)
    _param_names = [
        "base_goal_home", "base_goal_away",
        "strength_coeff_home", "strength_coeff_away",
        "home_advantage",
        "draw_boost",
        "min_xg", "max_xg",
    ]
    _x0 = [1.25, 1.10, 0.90, 0.75, 60.0, 1.00, 0.20, 3.50]
    _bounds = [
        (0.5, 2.5),   # base_goal_home
        (0.5, 2.5),   # base_goal_away
        (0.1, 2.0),   # strength_coeff_home
        (0.1, 2.0),   # strength_coeff_away
        (0.0, 150.0), # home_advantage (Elo points)
        (0.8, 1.3),   # draw_boost
        (0.05, 0.5),  # min_xg
        (2.0, 5.0),   # max_xg
    ]

    def fit(self, steps: list[ReplayStep]) -> None:
        """Fit parameters on training set to minimize Brier Score."""
        # Pre-compute all Elo values at each step for normalization
        all_elos_per_step = self._compute_all_elos(steps)

        def objective(x: np.ndarray) -> float:
            params = dict(zip(self._param_names, x))
            total_brier = 0.0
            for i, step in enumerate(steps):
                elos = all_elos_per_step[i]
                home_s, away_s = elo_to_strength(
                    step.pre_match_home_elo, step.pre_match_away_elo, elos
                )
                # Compute strength delta with home advantage
                ha_norm = params["home_advantage"] / 400.0 / max(1.0, max(elos) - min(elos) + 1e-9)
                strength_delta = home_s - away_s + (0.0 if step.neutral_venue else ha_norm)

                home_xg = float(np.clip(
                    params["base_goal_home"] + params["strength_coeff_home"] * strength_delta,
                    params["min_xg"], params["max_xg"],
                ))
                away_xg = float(np.clip(
                    params["base_goal_away"] - params["strength_coeff_away"] * strength_delta,
                    params["min_xg"], params["max_xg"],
                ))

                probs = _poisson_probs(home_xg, away_xg, draw_boost=params["draw_boost"])
                actual = _outcome_onehot(step.home_score, step.away_score)
                total_brier += _brier_sum(probs, actual)
            return total_brier / len(steps)

        result = minimize(
            objective,
            x0=np.array(self._x0, dtype=float),
            method="L-BFGS-B",
            bounds=self._bounds,
            options={"maxiter": 500, "ftol": 1e-8},
        )

        self.parameters = dict(zip(self._param_names, result.x.tolist()))
        logger.info("Refitted model parameters: %s", self.parameters)

    def predict(
        self,
        step: ReplayStep,
        all_elos: list[float],
    ) -> tuple[float, float, float]:
        """Predict using fitted parameters."""
        p = self.parameters
        home_s, away_s = elo_to_strength(
            step.pre_match_home_elo, step.pre_match_away_elo, all_elos
        )
        ha_norm = p["home_advantage"] / 400.0 / max(1.0, max(all_elos) - min(all_elos) + 1e-9)
        strength_delta = home_s - away_s + (0.0 if step.neutral_venue else ha_norm)

        home_xg = float(np.clip(
            p["base_goal_home"] + p["strength_coeff_home"] * strength_delta,
            p["min_xg"], p["max_xg"],
        ))
        away_xg = float(np.clip(
            p["base_goal_away"] - p["strength_coeff_away"] * strength_delta,
            p["min_xg"], p["max_xg"],
        ))
        return _poisson_probs(home_xg, away_xg, draw_boost=p["draw_boost"])

    def get_parameters(self) -> dict[str, Any]:
        return dict(self.parameters)

    @staticmethod
    def _compute_all_elos(steps: list[ReplayStep]) -> list[list[float]]:
        """For each step, collect all Elo values from the training set for normalization.

        All steps are from the training set, so using all their Elo values is fine.
        Uses the pre-computed Elo values from ReplayStep (which already have
        correct same-timestamp grouping from replay_elo_history).
        """
        all_elo_values: set[float] = set()
        for step in steps:
            all_elo_values.add(step.pre_match_home_elo)
            all_elo_values.add(step.pre_match_away_elo)

        shared_elos = sorted(all_elo_values) if all_elo_values else [1500.0]
        return [shared_elos] * len(steps)


# ---------------------------------------------------------------------------
# Model C: Dixon-Coles
# ---------------------------------------------------------------------------

@dataclass
class DixonColesModel:
    """Poisson model with Dixon-Coles adjustment and time decay."""

    name: str = "dixon-coles"
    parameters: dict[str, float] = field(default_factory=dict)

    # Base Poisson parameters (fitted alongside rho and xi)
    _param_names = [
        "base_goal_home", "base_goal_away",
        "strength_coeff_home", "strength_coeff_away",
        "home_advantage",
        "draw_boost",
        "min_xg", "max_xg",
        "rho",  # Dixon-Coles correlation parameter
    ]
    _x0 = [1.25, 1.10, 0.90, 0.75, 60.0, 1.00, 0.20, 3.50, -0.1]
    _bounds = [
        (0.5, 2.5),   # base_goal_home
        (0.5, 2.5),   # base_goal_away
        (0.1, 2.0),   # strength_coeff_home
        (0.1, 2.0),   # strength_coeff_away
        (0.0, 150.0), # home_advantage
        (0.8, 1.3),   # draw_boost
        (0.05, 0.5),  # min_xg
        (2.0, 5.0),   # max_xg
        (-0.2, 0.2),  # rho
    ]

    # Time decay parameter (searched separately on validation set)
    xi: float = 0.0

    def fit(self, train_steps: list[ReplayStep], val_steps: list[ReplayStep] | None = None) -> None:
        """Fit parameters on training set, then search xi on validation set."""
        train_elos = RefittedModel._compute_all_elos(train_steps)

        def objective(x: np.ndarray) -> float:
            params = dict(zip(self._param_names, x))
            rho = params["rho"]
            total_brier = 0.0
            for i, step in enumerate(train_steps):
                elos = train_elos[i]
                home_s, away_s = elo_to_strength(
                    step.pre_match_home_elo, step.pre_match_away_elo, elos
                )
                ha_norm = params["home_advantage"] / 400.0 / max(1.0, max(elos) - min(elos) + 1e-9)
                strength_delta = home_s - away_s + (0.0 if step.neutral_venue else ha_norm)

                home_xg = float(np.clip(
                    params["base_goal_home"] + params["strength_coeff_home"] * strength_delta,
                    params["min_xg"], params["max_xg"],
                ))
                away_xg = float(np.clip(
                    params["base_goal_away"] - params["strength_coeff_away"] * strength_delta,
                    params["min_xg"], params["max_xg"],
                ))

                probs = _dixon_coles_probs(home_xg, away_xg, rho, draw_boost=params["draw_boost"])
                actual = _outcome_onehot(step.home_score, step.away_score)
                total_brier += _brier_sum(probs, actual)
            return total_brier / len(train_steps)

        result = minimize(
            objective,
            x0=np.array(self._x0, dtype=float),
            method="L-BFGS-B",
            bounds=self._bounds,
            options={"maxiter": 500, "ftol": 1e-8},
        )

        self.parameters = dict(zip(self._param_names, result.x.tolist()))

        # Search xi (time decay) on validation set
        if val_steps and len(val_steps) > 0:
            self._search_xi(train_steps, val_steps)

        logger.info("Dixon-Coles parameters: %s, xi=%.6f", self.parameters, self.xi)

    def _search_xi(self, train_steps: list[ReplayStep], val_steps: list[ReplayStep]) -> None:
        """Search for optimal xi on validation set using grid search."""
        val_elos = RefittedModel._compute_all_elos(val_steps)
        # Use the last training match time as reference
        ref_time = train_steps[-1].available_at if train_steps else val_steps[0].available_at

        best_xi = 0.0
        best_brier = float("inf")

        for xi_candidate in np.linspace(0.0, 0.01, 21):
            total_brier = 0.0
            for i, step in enumerate(val_steps):
                elos = val_elos[i]
                probs = self._predict_with_xi(step, elos, xi_candidate, ref_time)
                actual = _outcome_onehot(step.home_score, step.away_score)
                total_brier += _brier_sum(probs, actual)
            avg_brier = total_brier / len(val_steps)
            if avg_brier < best_brier:
                best_brier = avg_brier
                best_xi = float(xi_candidate)

        self.xi = best_xi

    def _predict_with_xi(
        self,
        step: ReplayStep,
        all_elos: list[float],
        xi: float,
        ref_time: Any,
    ) -> tuple[float, float, float]:
        """Predict with time-decay weighting applied to lambda."""
        p = self.parameters
        home_s, away_s = elo_to_strength(
            step.pre_match_home_elo, step.pre_match_away_elo, all_elos
        )
        ha_norm = p["home_advantage"] / 400.0 / max(1.0, max(all_elos) - min(all_elos) + 1e-9)
        strength_delta = home_s - away_s + (0.0 if step.neutral_venue else ha_norm)

        home_xg = float(np.clip(
            p["base_goal_home"] + p["strength_coeff_home"] * strength_delta,
            p["min_xg"], p["max_xg"],
        ))
        away_xg = float(np.clip(
            p["base_goal_away"] - p["strength_coeff_away"] * strength_delta,
            p["min_xg"], p["max_xg"],
        ))

        # Apply time decay: more recent matches get higher weight
        if xi > 0 and ref_time is not None:
            days_diff = (ref_time - step.available_at).total_seconds() / 86400.0
            decay = np.exp(-xi * days_diff)
            # Scale lambda towards the mean (1.17) for older matches
            mean_xg = 1.17
            home_xg = mean_xg + (home_xg - mean_xg) * decay
            away_xg = mean_xg + (away_xg - mean_xg) * decay

        rho = p["rho"]
        return _dixon_coles_probs(home_xg, away_xg, rho, draw_boost=p["draw_boost"])

    def predict(
        self,
        step: ReplayStep,
        all_elos: list[float],
    ) -> tuple[float, float, float]:
        """Predict using fitted parameters (no time decay for prediction)."""
        p = self.parameters
        home_s, away_s = elo_to_strength(
            step.pre_match_home_elo, step.pre_match_away_elo, all_elos
        )
        ha_norm = p["home_advantage"] / 400.0 / max(1.0, max(all_elos) - min(all_elos) + 1e-9)
        strength_delta = home_s - away_s + (0.0 if step.neutral_venue else ha_norm)

        home_xg = float(np.clip(
            p["base_goal_home"] + p["strength_coeff_home"] * strength_delta,
            p["min_xg"], p["max_xg"],
        ))
        away_xg = float(np.clip(
            p["base_goal_away"] - p["strength_coeff_away"] * strength_delta,
            p["min_xg"], p["max_xg"],
        ))

        rho = p["rho"]
        return _dixon_coles_probs(home_xg, away_xg, rho, draw_boost=p["draw_boost"])

    def get_parameters(self) -> dict[str, Any]:
        params = dict(self.parameters)
        params["xi"] = self.xi
        return params


# ---------------------------------------------------------------------------
# Model D: Negative Binomial
# ---------------------------------------------------------------------------

@dataclass
class NegBinomialModel:
    """Negative Binomial model replacing Poisson with overdispersed counts."""

    name: str = "neg-binomial"
    parameters: dict[str, float] = field(default_factory=dict)
    alpha: float = 0.1  # dispersion parameter

    _param_names = [
        "base_goal_home", "base_goal_away",
        "strength_coeff_home", "strength_coeff_away",
        "home_advantage",
        "draw_boost",
        "min_xg", "max_xg",
    ]
    _x0 = [1.25, 1.10, 0.90, 0.75, 60.0, 1.00, 0.20, 3.50]
    _bounds = [
        (0.5, 2.5),   # base_goal_home
        (0.5, 2.5),   # base_goal_away
        (0.1, 2.0),   # strength_coeff_home
        (0.1, 2.0),   # strength_coeff_away
        (0.0, 150.0), # home_advantage
        (0.8, 1.3),   # draw_boost
        (0.05, 0.5),  # min_xg
        (2.0, 5.0),   # max_xg
    ]

    def fit(self, steps: list[ReplayStep]) -> None:
        """Fit parameters on training set, estimate alpha from goal data."""
        # Step 1: Estimate alpha (dispersion) from training goals
        self._estimate_alpha(steps)

        # Step 2: Fit other parameters via optimization
        train_elos = RefittedModel._compute_all_elos(steps)
        alpha = self.alpha

        def objective(x: np.ndarray) -> float:
            params = dict(zip(self._param_names, x))
            total_brier = 0.0
            for i, step in enumerate(steps):
                elos = train_elos[i]
                home_s, away_s = elo_to_strength(
                    step.pre_match_home_elo, step.pre_match_away_elo, elos
                )
                ha_norm = params["home_advantage"] / 400.0 / max(1.0, max(elos) - min(elos) + 1e-9)
                strength_delta = home_s - away_s + (0.0 if step.neutral_venue else ha_norm)

                home_xg = float(np.clip(
                    params["base_goal_home"] + params["strength_coeff_home"] * strength_delta,
                    params["min_xg"], params["max_xg"],
                ))
                away_xg = float(np.clip(
                    params["base_goal_away"] - params["strength_coeff_away"] * strength_delta,
                    params["min_xg"], params["max_xg"],
                ))

                probs = _neg_binomial_probs(home_xg, away_xg, alpha, draw_boost=params["draw_boost"])
                actual = _outcome_onehot(step.home_score, step.away_score)
                total_brier += _brier_sum(probs, actual)
            return total_brier / len(steps)

        result = minimize(
            objective,
            x0=np.array(self._x0, dtype=float),
            method="L-BFGS-B",
            bounds=self._bounds,
            options={"maxiter": 500, "ftol": 1e-8},
        )

        self.parameters = dict(zip(self._param_names, result.x.tolist()))
        self.parameters["alpha"] = self.alpha
        logger.info("Neg-Binomial parameters: %s", self.parameters)

    def _estimate_alpha(self, steps: list[ReplayStep]) -> None:
        """Estimate dispersion parameter alpha using method of moments.

        For Negative Binomial: Var = mu + alpha * mu^2
        So alpha = (Var - mu) / mu^2
        """
        goals = []
        for step in steps:
            goals.append(step.home_score)
            goals.append(step.away_score)

        goals_arr = np.array(goals, dtype=float)
        mu = float(np.mean(goals_arr))
        var = float(np.var(goals_arr))

        if mu > 0:
            self.alpha = max(0.01, (var - mu) / (mu ** 2))
        else:
            self.alpha = 0.1

        logger.info("Estimated alpha=%.4f from %d goals (mu=%.3f, var=%.3f)",
                     self.alpha, len(goals), mu, var)

    def predict(
        self,
        step: ReplayStep,
        all_elos: list[float],
    ) -> tuple[float, float, float]:
        """Predict using fitted parameters."""
        p = self.parameters
        home_s, away_s = elo_to_strength(
            step.pre_match_home_elo, step.pre_match_away_elo, all_elos
        )
        ha_norm = p["home_advantage"] / 400.0 / max(1.0, max(all_elos) - min(all_elos) + 1e-9)
        strength_delta = home_s - away_s + (0.0 if step.neutral_venue else ha_norm)

        home_xg = float(np.clip(
            p["base_goal_home"] + p["strength_coeff_home"] * strength_delta,
            p["min_xg"], p["max_xg"],
        ))
        away_xg = float(np.clip(
            p["base_goal_away"] - p["strength_coeff_away"] * strength_delta,
            p["min_xg"], p["max_xg"],
        ))

        return _neg_binomial_probs(home_xg, away_xg, self.alpha, draw_boost=p["draw_boost"])

    def get_parameters(self) -> dict[str, Any]:
        return dict(self.parameters)


# ---------------------------------------------------------------------------
# Internal probability computation functions
# ---------------------------------------------------------------------------

def _poisson_probs(
    home_lambda: float,
    away_lambda: float,
    draw_boost: float = 1.0,
) -> tuple[float, float, float]:
    """Compute (home_win, draw, away_win) from Poisson goal distributions."""
    home_goals = poisson.pmf(np.arange(_MAX_GOALS + 1), home_lambda)
    away_goals = poisson.pmf(np.arange(_MAX_GOALS + 1), away_lambda)

    # Tail probability
    home_tail = max(0.0, 1.0 - float(home_goals.sum()))
    away_tail = max(0.0, 1.0 - float(away_goals.sum()))
    home_goals = np.append(home_goals, home_tail)
    away_goals = np.append(away_goals, away_tail)

    # Normalize
    home_goals = home_goals / home_goals.sum()
    away_goals = away_goals / away_goals.sum()

    matrix = np.outer(home_goals, away_goals)

    home_win = float(np.tril(matrix, k=-1).sum())
    draw = float(np.trace(matrix))
    away_win = float(np.triu(matrix, k=1).sum())

    total = home_win + draw + away_win
    if total < 1e-12:
        return (0.4, 0.2, 0.4)
    home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Apply draw_boost
    if draw_boost != 1.0 and draw > 0:
        draw_boosted = draw * draw_boost
        excess = draw_boosted - draw
        win_total = home_win + away_win
        if win_total > 0:
            home_win -= excess * (home_win / win_total)
            away_win -= excess * (away_win / win_total)
        draw = draw_boosted
        total = home_win + draw + away_win
        home_win, draw, away_win = home_win / total, draw / total, away_win / total

    return (home_win, draw, away_win)


def _dixon_coles_probs(
    home_lambda: float,
    away_lambda: float,
    rho: float,
    draw_boost: float = 1.0,
) -> tuple[float, float, float]:
    """Compute probabilities with Dixon-Coles adjustment for low scores."""
    home_goals = poisson.pmf(np.arange(_MAX_GOALS + 1), home_lambda)
    away_goals = poisson.pmf(np.arange(_MAX_GOALS + 1), away_lambda)

    home_tail = max(0.0, 1.0 - float(home_goals.sum()))
    away_tail = max(0.0, 1.0 - float(away_goals.sum()))
    home_goals = np.append(home_goals, home_tail)
    away_goals = np.append(away_goals, away_tail)

    home_goals = home_goals / home_goals.sum()
    away_goals = away_goals / away_goals.sum()

    matrix = np.outer(home_goals, away_goals)

    # Apply Dixon-Coles tau adjustment for low-score cells
    # tau(i,j) modifies the probability matrix for (0-0, 0-1, 1-0, 1-1) cells
    if rho != 0.0:
        _apply_dc_adjustment(matrix, home_lambda, away_lambda, rho)

    home_win = float(np.tril(matrix, k=-1).sum())
    draw = float(np.trace(matrix))
    away_win = float(np.triu(matrix, k=1).sum())

    total = home_win + draw + away_win
    if total < 1e-12:
        return (0.4, 0.2, 0.4)
    home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Apply draw_boost
    if draw_boost != 1.0 and draw > 0:
        draw_boosted = draw * draw_boost
        excess = draw_boosted - draw
        win_total = home_win + away_win
        if win_total > 0:
            home_win -= excess * (home_win / win_total)
            away_win -= excess * (away_win / win_total)
        draw = draw_boosted
        total = home_win + draw + away_win
        home_win, draw, away_win = home_win / total, draw / total, away_win / total

    return (home_win, draw, away_win)


def _apply_dc_adjustment(
    matrix: np.ndarray,
    home_lambda: float,
    away_lambda: float,
    rho: float,
) -> None:
    """Apply Dixon-Coles adjustment to the score matrix in-place.

    Adjustment factors:
    - (0,0): multiply by (1 - home_lambda * away_lambda * rho)
    - (0,1): multiply by (1 + home_lambda * rho)
    - (1,0): multiply by (1 + away_lambda * rho)
    - (1,1): multiply by (1 - rho)
    """
    # (0,0)
    matrix[0, 0] *= (1.0 - home_lambda * away_lambda * rho)
    # (0,1)
    if matrix.shape[1] > 1:
        matrix[0, 1] *= (1.0 + home_lambda * rho)
    # (1,0)
    if matrix.shape[0] > 1:
        matrix[1, 0] *= (1.0 + away_lambda * rho)
    # (1,1)
    if matrix.shape[0] > 1 and matrix.shape[1] > 1:
        matrix[1, 1] *= (1.0 - rho)

    # Ensure non-negative
    np.clip(matrix, 0.0, None, out=matrix)


def _neg_binomial_probs(
    home_mu: float,
    away_mu: float,
    alpha: float,
    draw_boost: float = 1.0,
) -> tuple[float, float, float]:
    """Compute probabilities using Negative Binomial distribution.

    NB parameterization: Var = mu + alpha * mu^2
    n = 1/alpha, p = 1/(1 + alpha*mu)
    scipy nbinom.pmf(k, n, p) where k=goals
    """
    home_n, home_p = _nb_params(home_mu, alpha)
    away_n, away_p = _nb_params(away_mu, alpha)

    home_goals = np.array([nbinom.pmf(k, home_n, home_p) for k in range(_MAX_GOALS + 1)])
    away_goals = np.array([nbinom.pmf(k, away_n, away_p) for k in range(_MAX_GOALS + 1)])

    # Tail
    home_tail = max(0.0, 1.0 - float(home_goals.sum()))
    away_tail = max(0.0, 1.0 - float(away_goals.sum()))
    home_goals = np.append(home_goals, home_tail)
    away_goals = np.append(away_goals, away_tail)

    home_goals = home_goals / home_goals.sum()
    away_goals = away_goals / away_goals.sum()

    matrix = np.outer(home_goals, away_goals)

    home_win = float(np.tril(matrix, k=-1).sum())
    draw = float(np.trace(matrix))
    away_win = float(np.triu(matrix, k=1).sum())

    total = home_win + draw + away_win
    if total < 1e-12:
        return (0.4, 0.2, 0.4)
    home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Apply draw_boost
    if draw_boost != 1.0 and draw > 0:
        draw_boosted = draw * draw_boost
        excess = draw_boosted - draw
        win_total = home_win + away_win
        if win_total > 0:
            home_win -= excess * (home_win / win_total)
            away_win -= excess * (away_win / win_total)
        draw = draw_boosted
        total = home_win + draw + away_win
        home_win, draw, away_win = home_win / total, draw / total, away_win / total

    return (home_win, draw, away_win)


def _nb_params(mu: float, alpha: float) -> tuple[float, float]:
    """Convert (mu, alpha) to scipy nbinom (n, p) parameters."""
    n = 1.0 / alpha
    p = 1.0 / (1.0 + alpha * mu)
    return n, p


# ---------------------------------------------------------------------------
# Dixon-Coles Audit
# ---------------------------------------------------------------------------

def audit_dixon_coles(
    dc_model: DixonColesModel,
    refitted_model: RefittedModel,
    steps: list[ReplayStep],
    all_elos: list[float],
) -> dict[str, Any]:
    """Audit Dixon-Coles adjustment impact vs Refitted model.

    Returns:
        Dict with:
        - mean_abs_delta: mean absolute probability difference
        - max_abs_delta: max absolute probability difference
        - affected_ratio: fraction of matches with non-zero delta
        - low_xg_deltas: deltas for low-xG matches specifically
        - cell_deltas: average change in 0-0, 1-0, 0-1, 1-1 probabilities
    """
    total_delta = 0.0
    max_delta = 0.0
    affected = 0
    n = len(steps)

    # Track specific score cell changes
    cell_deltas = {"0-0": [], "1-0": [], "0-1": [], "1-1": []}

    for step in steps:
        dc_probs = dc_model.predict(step, all_elos)
        ref_probs = refitted_model.predict(step, all_elos)

        abs_delta = sum(abs(a - b) for a, b in zip(dc_probs, ref_probs))
        total_delta += abs_delta
        max_delta = max(max_delta, abs_delta)

        if abs_delta > 1e-8:
            affected += 1

        # Compute individual cell probabilities for low scores
        rho = dc_model.parameters.get("rho", 0.0)
        if rho != 0.0:
            p = dc_model.parameters
            home_s, away_s = elo_to_strength(
                step.pre_match_home_elo, step.pre_match_away_elo, all_elos
            )
            ha_norm = p["home_advantage"] / 400.0 / max(1.0, max(all_elos) - min(all_elos) + 1e-9)
            strength_delta = home_s - away_s + (0.0 if step.neutral_venue else ha_norm)
            home_xg = float(np.clip(
                p["base_goal_home"] + p["strength_coeff_home"] * strength_delta,
                p["min_xg"], p["max_xg"],
            ))
            away_xg = float(np.clip(
                p["base_goal_away"] - p["strength_coeff_away"] * strength_delta,
                p["min_xg"], p["max_xg"],
            ))

            # Poisson cell probabilities (before DC adjustment)
            from scipy.stats import poisson as sp_poisson
            p00_poisson = float(sp_poisson.pmf(0, home_xg) * sp_poisson.pmf(0, away_xg))
            p01_poisson = float(sp_poisson.pmf(0, home_xg) * sp_poisson.pmf(1, away_xg))
            p10_poisson = float(sp_poisson.pmf(1, home_xg) * sp_poisson.pmf(0, away_xg))
            p11_poisson = float(sp_poisson.pmf(1, home_xg) * sp_poisson.pmf(1, away_xg))

            # After DC adjustment
            p00_dc = p00_poisson * (1.0 - home_xg * away_xg * rho)
            p01_dc = p01_poisson * (1.0 + home_xg * rho)
            p10_dc = p10_poisson * (1.0 + away_xg * rho)
            p11_dc = p11_poisson * (1.0 - rho)

            cell_deltas["0-0"].append(p00_dc - p00_poisson)
            cell_deltas["1-0"].append(p10_dc - p10_poisson)
            cell_deltas["0-1"].append(p01_dc - p01_poisson)
            cell_deltas["1-1"].append(p11_dc - p11_poisson)

    result = {
        "mean_abs_delta": total_delta / n if n > 0 else 0.0,
        "max_abs_delta": max_delta,
        "affected_ratio": affected / n if n > 0 else 0.0,
        "rho": dc_model.parameters.get("rho", 0.0),
        "cell_deltas": {
            k: {
                "mean_delta": float(np.mean(v)) if v else 0.0,
                "max_delta": float(max(v)) if v else 0.0,
                "min_delta": float(min(v)) if v else 0.0,
            }
            for k, v in cell_deltas.items()
        },
    }

    return result


# ---------------------------------------------------------------------------
# Model E: Multinomial Logistic Regression
# ---------------------------------------------------------------------------

@dataclass
class LogisticModel:
    """Direct three-class logistic regression using pre-match features.

    Features (all available before kickoff):
    - elo_diff: pre-match Elo difference (home - away)
    - neutral_venue: 1 if neutral, 0 otherwise
    - is_friendly: 1 if friendly, 0 otherwise
    - is_qualifier: 1 if qualifier, 0 otherwise
    - is_continental: 1 if continental tournament, 0 otherwise
    - is_world_cup: 1 if World Cup, 0 otherwise
    - home_advantage: Elo home advantage points
    - data_completeness: 1.0 (placeholder for future data quality)

    NO future data, NO odds, NO LLM probabilities.
    """

    name: str = "multinomial-logistic"
    parameters: dict[str, Any] = field(default_factory=dict)

    # Coefficients: [home_win, draw, away_win] x [intercept, elo_diff, neutral, friendly, qualifier, continental, world_cup, home_adv]
    _n_classes = 3
    _n_features = 8
    coefficients: np.ndarray | None = None  # shape (3, 8)

    def fit(self, steps: list[ReplayStep]) -> None:
        """Fit multinomial logistic regression on training set."""
        from scipy.optimize import minimize as sp_minimize

        X, y = self._build_features(steps)
        n = len(steps)

        if n < 10:
            logger.warning("Too few samples (%d) for logistic regression", n)
            return

        # Flatten coefficients for optimization
        # Reference class = away_win (class 2), so we only optimize classes 0 and 1
        x0 = np.zeros((self._n_classes - 1) * self._n_features)

        def objective(flat_coefs: np.ndarray) -> float:
            coefs = flat_coefs.reshape(self._n_classes - 1, self._n_features)
            total_ll = 0.0
            for i in range(n):
                probs = self._softmax_with_coefs(X[i], coefs)
                # y[i] is the class index
                eps = 1e-15
                total_ll -= math.log(max(probs[y[i]], eps))
            return total_ll

        result = sp_minimize(
            objective,
            x0=x0,
            method="L-BFGS-B",
            options={"maxiter": 1000, "ftol": 1e-10},
        )

        self.coefficients = np.zeros((self._n_classes, self._n_features))
        fitted = result.x.reshape(self._n_classes - 1, self._n_features)
        self.coefficients[:self._n_classes - 1] = fitted
        # Class 2 (away_win) is the reference with all zeros

        self.parameters = {
            "coefficients": self.coefficients.tolist(),
            "feature_names": self._feature_names(),
        }
        logger.info("Logistic model fitted: %d samples, loss=%.4f", n, result.fun)

    def predict(
        self,
        step: ReplayStep,
        all_elos: list[float],
    ) -> tuple[float, float, float]:
        """Predict using fitted logistic regression."""
        if self.coefficients is None:
            # Unfitted: use Elo-based heuristic
            elo_diff = step.pre_match_home_elo - step.pre_match_away_elo
            ha = 0.0 if step.neutral_venue else 60.0
            adjusted_diff = elo_diff + ha
            # Simple sigmoid-based heuristic
            p_home = 1.0 / (1.0 + math.exp(-adjusted_diff / 400.0))
            p_away = 1.0 - p_home
            draw_base = 0.26  # historical average
            # Adjust draw probability by closeness
            closeness = 1.0 - abs(adjusted_diff) / 600.0
            draw_prob = draw_base * max(0.3, min(1.0, closeness))
            # Normalize
            total = p_home * (1 - draw_prob) + draw_prob + p_away * (1 - draw_prob)
            return (
                p_home * (1 - draw_prob) / total,
                draw_prob / total,
                p_away * (1 - draw_prob) / total,
            )

        features = self._step_to_features(step)
        probs = self._softmax_with_coefs(features, self.coefficients[:self._n_classes - 1])
        return (probs[0], probs[1], probs[2])

    def get_parameters(self) -> dict[str, Any]:
        return dict(self.parameters) if self.parameters else {}

    def _build_features(self, steps: list[ReplayStep]) -> tuple[np.ndarray, list[int]]:
        """Build feature matrix and labels from replay steps."""
        X = np.array([self._step_to_features(s) for s in steps])
        y = []
        for step in steps:
            if step.home_score > step.away_score:
                y.append(0)
            elif step.home_score == step.away_score:
                y.append(1)
            else:
                y.append(2)
        return X, y

    def _step_to_features(self, step: ReplayStep) -> np.ndarray:
        """Convert a ReplayStep to feature vector."""
        ha = 0.0 if step.neutral_venue else 60.0
        return np.array([
            1.0,  # intercept
            (step.pre_match_home_elo - step.pre_match_away_elo) / 400.0,  # normalized elo_diff
            float(step.neutral_venue),
            float(step.competition_type == "friendly"),
            float(step.competition_type in ("qualifier", "continental_qualifier")),
            float(step.competition_type == "continental"),
            float(step.competition_type == "world_cup"),
            ha / 100.0,  # normalized home advantage
        ])

    def _softmax_with_coefs(self, x: np.ndarray, coefs: np.ndarray) -> np.ndarray:
        """Compute softmax probabilities given features and coefficients.

        coefs shape: (n_classes - 1, n_features)
        Reference class (away_win) has implicit score 0.
        """
        scores = np.zeros(self._n_classes)
        for k in range(self._n_classes - 1):
            scores[k] = float(np.dot(coefs[k], x))
        # scores[2] = 0 (reference)

        # Numerically stable softmax
        max_score = max(scores)
        exp_scores = np.exp(scores - max_score)
        probs = exp_scores / exp_scores.sum()
        return probs

    @staticmethod
    def _feature_names() -> list[str]:
        return ["intercept", "elo_diff", "neutral_venue", "is_friendly",
                "is_qualifier", "is_continental", "is_world_cup", "home_advantage"]


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

MODEL_REGISTRY: dict[str, type] = {
    "legacy-elo-poisson": LegacyModel,
    "refitted-elo-poisson": RefittedModel,
    "dixon-coles": DixonColesModel,
    "neg-binomial": NegBinomialModel,
    "multinomial-logistic": LogisticModel,
}
