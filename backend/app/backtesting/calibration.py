"""Calibration methods for backtesting predictions.

All calibrators are fitted ONLY on validation set predictions
and evaluated on test set. Calibrated probabilities always sum to 1.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

from app.backtesting.evaluation import MatchPrediction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class Calibrator(ABC):
    """Base class for probability calibrators."""

    @abstractmethod
    def fit(self, predictions: list[MatchPrediction]) -> None:
        """Fit the calibrator on validation set predictions."""
        ...

    @abstractmethod
    def calibrate(self, probs: tuple[float, float, float]) -> tuple[float, float, float]:
        """Calibrate a single (home_win, draw, away_win) probability triple."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


# ---------------------------------------------------------------------------
# Temperature Scaling
# ---------------------------------------------------------------------------

class TemperatureScaling(Calibrator):
    """Learn a single temperature parameter T on validation set.

    Calibrated p = p^(1/T) / sum(p^(1/T))
    T > 1: softens probabilities (more uncertain)
    T < 1: sharpens probabilities (more confident)
    """

    def __init__(self) -> None:
        self._temperature: float = 1.0
        self._name = "temperature-scaling"

    @property
    def name(self) -> str:
        return self._name

    @property
    def temperature(self) -> float:
        return self._temperature

    def fit(self, predictions: list[MatchPrediction]) -> None:
        """Find optimal temperature by minimizing NLL on validation set."""
        if not predictions:
            return

        # Pre-compute data
        data = []
        for pred in predictions:
            if pred.home_score > pred.away_score:
                actual = (1.0, 0.0, 0.0)
            elif pred.home_score == pred.away_score:
                actual = (0.0, 1.0, 0.0)
            else:
                actual = (0.0, 0.0, 1.0)
            data.append((
                (pred.predicted_home_win, pred.predicted_draw, pred.predicted_away_win),
                actual,
            ))

        def neg_log_likelihood(log_t: np.ndarray) -> float:
            t = float(np.exp(log_t[0]))
            nll = 0.0
            eps = 1e-15
            for probs, actual in data:
                calibrated = _apply_temperature(probs, t)
                for p, o in zip(calibrated, actual):
                    if o > 0:
                        nll -= math.log(max(p, eps))
            return nll

        result = minimize(
            neg_log_likelihood,
            x0=np.array([0.0]),  # log(1.0) = 0
            method="L-BFGS-B",
            bounds=[(-2.0, 2.0)],  # T in [exp(-2), exp(2)] ≈ [0.14, 7.39]
            options={"maxiter": 200},
        )

        self._temperature = float(np.exp(result.x[0]))
        logger.info("Temperature scaling: T=%.4f", self._temperature)

    def calibrate(self, probs: tuple[float, float, float]) -> tuple[float, float, float]:
        return _apply_temperature(probs, self._temperature)


def _apply_temperature(
    probs: tuple[float, float, float],
    t: float,
) -> tuple[float, float, float]:
    """Apply temperature scaling to a probability triple."""
    if t <= 0:
        t = 1e-6
    powered = [p ** (1.0 / t) for p in probs]
    total = sum(powered)
    if total < 1e-15:
        return (1.0 / 3, 1.0 / 3, 1.0 / 3)
    return tuple(p / total for p in powered)


# ---------------------------------------------------------------------------
# Isotonic Regression
# ---------------------------------------------------------------------------

class IsotonicCalibration(Calibrator):
    """Per-class monotonic calibration using isotonic regression.

    Uses sklearn.isotonic.IsotonicRegression if available,
    otherwise falls back to simple binning.
    """

    def __init__(self, n_bins: int = 15) -> None:
        self._n_bins = n_bins
        self._name = "isotonic-calibration"
        # Per-class calibration maps: list of (bin_edge, calibrated_value)
        self._maps: dict[str, list[tuple[float, float]]] = {}

    @property
    def name(self) -> str:
        return self._name

    def fit(self, predictions: list[MatchPrediction]) -> None:
        """Fit isotonic calibration on validation set."""
        if not predictions:
            return

        try:
            from sklearn.isotonic import IsotonicRegression
            self._fit_sklearn(predictions)
        except ImportError:
            logger.info("sklearn not available, using binning fallback for isotonic calibration")
            self._fit_binning(predictions)

    def _fit_sklearn(self, predictions: list[MatchPrediction]) -> None:
        """Fit using sklearn IsotonicRegression."""
        from sklearn.isotonic import IsotonicRegression

        for outcome in ("home_win", "draw", "away_win"):
            raw_probs = []
            correct = []
            for pred in predictions:
                if outcome == "home_win":
                    p = pred.predicted_home_win
                    c = 1.0 if pred.home_score > pred.away_score else 0.0
                elif outcome == "draw":
                    p = pred.predicted_draw
                    c = 1.0 if pred.home_score == pred.away_score else 0.0
                else:
                    p = pred.predicted_away_win
                    c = 1.0 if pred.home_score < pred.away_score else 0.0
                raw_probs.append(p)
                correct.append(c)

            X = np.array(raw_probs)
            y = np.array(correct)

            iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
            iso.fit(X, y)

            # Store the calibration map as a list of (input, output) pairs
            x_interp = np.linspace(0.0, 1.0, 100)
            y_interp = iso.transform(x_interp)
            self._maps[outcome] = list(zip(x_interp.tolist(), y_interp.tolist()))

    def _fit_binning(self, predictions: list[MatchPrediction]) -> None:
        """Fit using simple binning as fallback."""
        for outcome in ("home_win", "draw", "away_win"):
            raw_probs = []
            correct = []
            for pred in predictions:
                if outcome == "home_win":
                    p = pred.predicted_home_win
                    c = 1.0 if pred.home_score > pred.away_score else 0.0
                elif outcome == "draw":
                    p = pred.predicted_draw
                    c = 1.0 if pred.home_score == pred.away_score else 0.0
                else:
                    p = pred.predicted_away_win
                    c = 1.0 if pred.home_score < pred.away_score else 0.0
                raw_probs.append(p)
                correct.append(c)

            # Bin and compute average accuracy per bin
            bins: dict[int, list[float]] = {}
            for p, c in zip(raw_probs, correct):
                bin_idx = min(int(p * self._n_bins), self._n_bins - 1)
                bins.setdefault(bin_idx, []).append(c)

            # Build calibration map
            cal_map: list[tuple[float, float]] = []
            for bin_idx in sorted(bins.keys()):
                avg_prob = (bin_idx + 0.5) / self._n_bins
                avg_correct = sum(bins[bin_idx]) / len(bins[bin_idx])
                cal_map.append((avg_prob, avg_correct))

            # Ensure monotonicity via PAVA (pool adjacent violators)
            cal_map = _pava(cal_map)
            self._maps[outcome] = cal_map

    def calibrate(self, probs: tuple[float, float, float]) -> tuple[float, float, float]:
        """Calibrate using per-class isotonic maps, then renormalize."""
        outcomes = ("home_win", "draw", "away_win")
        calibrated = []
        for p, outcome in zip(probs, outcomes):
            cal_map = self._maps.get(outcome)
            if not cal_map:
                calibrated.append(p)
                continue
            calibrated.append(_interpolate_map(p, cal_map))

        # Renormalize to sum to 1
        total = sum(calibrated)
        if total < 1e-15:
            return (1.0 / 3, 1.0 / 3, 1.0 / 3)
        return tuple(c / total for c in calibrated)


def _pava(cal_map: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Pool Adjacent Violators Algorithm to enforce monotonicity."""
    if len(cal_map) <= 1:
        return cal_map

    result = list(cal_map)
    i = 0
    while i < len(result) - 1:
        if result[i][1] > result[i + 1][1]:
            # Violation: pool
            pooled_x = (result[i][0] + result[i + 1][0]) / 2
            pooled_y = (result[i][1] + result[i + 1][1]) / 2
            result[i] = (pooled_x, pooled_y)
            result.pop(i + 1)
            # Backtrack
            if i > 0:
                i -= 1
        else:
            i += 1
    return result


def _interpolate_map(x: float, cal_map: list[tuple[float, float]]) -> float:
    """Linear interpolation in a calibration map."""
    if not cal_map:
        return x
    if x <= cal_map[0][0]:
        return cal_map[0][1]
    if x >= cal_map[-1][0]:
        return cal_map[-1][1]
    for i in range(len(cal_map) - 1):
        x0, y0 = cal_map[i]
        x1, y1 = cal_map[i + 1]
        if x0 <= x <= x1:
            if abs(x1 - x0) < 1e-15:
                return (y0 + y1) / 2
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return x


# ---------------------------------------------------------------------------
# Multiclass Logistic Calibration
# ---------------------------------------------------------------------------

class LogisticCalibration(Calibrator):
    """Multiclass logistic calibration: learn a weight and bias per class.

    For each class k: calibrated_k = exp(w_k * log(p_k) + b_k)
    Then normalize: calibrated_k / sum(calibrated_j)
    """

    def __init__(self) -> None:
        self._name = "logistic-calibration"
        self._weights: dict[str, float] = {}
        self._biases: dict[str, float] = {}

    @property
    def name(self) -> str:
        return self._name

    def fit(self, predictions: list[MatchPrediction]) -> None:
        """Fit logistic calibration on validation set."""
        if not predictions:
            return

        # Pre-compute data
        data = []
        for pred in predictions:
            if pred.home_score > pred.away_score:
                actual = (1.0, 0.0, 0.0)
            elif pred.home_score == pred.away_score:
                actual = (0.0, 1.0, 0.0)
            else:
                actual = (0.0, 0.0, 1.0)
            data.append((
                (pred.predicted_home_win, pred.predicted_draw, pred.predicted_away_win),
                actual,
            ))

        # Parameters: w_home, b_home, w_draw, b_draw, w_away, b_away
        # (away bias is fixed at 0 for identifiability)
        def neg_log_likelihood(params: np.ndarray) -> float:
            w_h, b_h, w_d, b_d, w_a = params
            b_a = 0.0
            weights = {"home_win": w_h, "draw": w_d, "away_win": w_a}
            biases = {"home_win": b_h, "draw": b_d, "away_win": b_a}
            nll = 0.0
            eps = 1e-15
            for probs, actual in data:
                calibrated = _apply_logistic(probs, weights, biases)
                for p, o in zip(calibrated, actual):
                    if o > 0:
                        nll -= math.log(max(p, eps))
            return nll

        result = minimize(
            neg_log_likelihood,
            x0=np.array([1.0, 0.0, 1.0, 0.0, 1.0]),
            method="L-BFGS-B",
            bounds=[
                (0.1, 5.0),   # w_home
                (-3.0, 3.0),  # b_home
                (0.1, 5.0),   # w_draw
                (-3.0, 3.0),  # b_draw
                (0.1, 5.0),   # w_away
            ],
            options={"maxiter": 200},
        )

        self._weights = {
            "home_win": float(result.x[0]),
            "draw": float(result.x[2]),
            "away_win": float(result.x[4]),
        }
        self._biases = {
            "home_win": float(result.x[1]),
            "draw": float(result.x[3]),
            "away_win": 0.0,
        }
        logger.info("Logistic calibration: weights=%s, biases=%s", self._weights, self._biases)

    def calibrate(self, probs: tuple[float, float, float]) -> tuple[float, float, float]:
        return _apply_logistic(probs, self._weights, self._biases)


def _apply_logistic(
    probs: tuple[float, float, float],
    weights: dict[str, float],
    biases: dict[str, float],
) -> tuple[float, float, float]:
    """Apply logistic calibration to a probability triple."""
    outcomes = ("home_win", "draw", "away_win")
    eps = 1e-15
    logits = []
    for p, outcome in zip(probs, outcomes):
        log_p = math.log(max(p, eps))
        w = weights.get(outcome, 1.0)
        b = biases.get(outcome, 0.0)
        logits.append(w * log_p + b)

    # Softmax
    max_logit = max(logits)
    exp_logits = [math.exp(l - max_logit) for l in logits]
    total = sum(exp_logits)
    if total < 1e-15:
        return (1.0 / 3, 1.0 / 3, 1.0 / 3)
    return tuple(e / total for e in exp_logits)


# ---------------------------------------------------------------------------
# Calibrator registry
# ---------------------------------------------------------------------------

CALIBRATOR_REGISTRY: dict[str, type[Calibrator]] = {
    "temperature-scaling": TemperatureScaling,
    "isotonic-calibration": IsotonicCalibration,
    "logistic-calibration": LogisticCalibration,
}
