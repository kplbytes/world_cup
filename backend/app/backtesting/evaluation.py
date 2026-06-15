"""Backtesting evaluation framework with strict no-leakage guarantees."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np


@dataclass
class MatchPrediction:
    """A single match prediction with metadata."""
    source_match_id: str
    available_at: datetime
    home_team_id: str
    away_team_id: str
    home_score: int
    away_score: int
    predicted_home_win: float
    predicted_draw: float
    predicted_away_win: float
    competition_type: str
    neutral_venue: bool
    elo_diff: float  # pre-match Elo difference
    model_name: str
    data_version: str


@dataclass
class ModelMetrics:
    """Metrics for a single model on a dataset split."""
    model_name: str
    split_name: str
    data_version: str
    match_count: int = 0

    # Core metrics
    brier_score: float = 0.0  # Mean of sum((p-o)^2) per match (3-class sum)
    brier_score_avg: float = 0.0  # Mean of avg((p-o)^2) per match (3-class average)
    log_loss: float = 0.0
    ece: float = 0.0  # Expected Calibration Error
    top1_hit_rate: float = 0.0
    draw_recall: float = 0.0  # Recall of actual draws

    # Per-outcome metrics
    home_win_brier: float = 0.0
    draw_brier: float = 0.0
    away_win_brier: float = 0.0

    # Per-outcome calibration
    home_win_ece: float = 0.0
    draw_ece: float = 0.0
    away_win_ece: float = 0.0


@dataclass
class StratifiedMetrics:
    """Metrics broken down by stratification dimensions."""
    by_competition_type: dict[str, ModelMetrics] = field(default_factory=dict)
    by_neutral_venue: dict[str, ModelMetrics] = field(default_factory=dict)
    by_elo_diff_range: dict[str, ModelMetrics] = field(default_factory=dict)
    by_year: dict[str, ModelMetrics] = field(default_factory=dict)
    by_outcome: dict[str, ModelMetrics] = field(default_factory=dict)
    by_confidence: dict[str, ModelMetrics] = field(default_factory=dict)


def compute_metrics(
    predictions: list[MatchPrediction],
    model_name: str,
    split_name: str,
    data_version: str,
) -> ModelMetrics:
    """Compute all metrics for a set of predictions.

    Brier Score calculation:
    - brier_score: mean of sum((p_i - o_i)^2) for i in {home, draw, away} per match
    - brier_score_avg: mean of mean((p_i - o_i)^2) for i in {home, draw, away} per match

    These differ by a factor of 3. All reports must specify which is used.
    """
    if not predictions:
        return ModelMetrics(
            model_name=model_name,
            split_name=split_name,
            data_version=data_version,
        )

    n = len(predictions)
    brier_sum_total = 0.0
    log_loss_total = 0.0
    top1_hits = 0
    actual_draws = 0
    predicted_draws = 0
    correct_draws = 0

    home_win_brier_total = 0.0
    draw_brier_total = 0.0
    away_win_brier_total = 0.0

    for pred in predictions:
        # Actual outcome one-hot
        if pred.home_score > pred.away_score:
            actual = (1.0, 0.0, 0.0)
        elif pred.home_score == pred.away_score:
            actual = (0.0, 1.0, 0.0)
        else:
            actual = (0.0, 0.0, 1.0)

        predicted = (pred.predicted_home_win, pred.predicted_draw, pred.predicted_away_win)

        # Brier score (sum)
        brier = sum((p - o) ** 2 for p, o in zip(predicted, actual))
        brier_sum_total += brier

        # Per-outcome Brier
        home_win_brier_total += (predicted[0] - actual[0]) ** 2
        draw_brier_total += (predicted[1] - actual[1]) ** 2
        away_win_brier_total += (predicted[2] - actual[2]) ** 2

        # Log loss
        eps = 1e-15
        ll = -sum(
            o * math.log(max(p, eps)) for p, o in zip(predicted, actual) if o > 0
        )
        log_loss_total += ll

        # Top-1 hit rate
        max_pred_idx = predicted.index(max(predicted))
        if actual[max_pred_idx] == 1.0:
            top1_hits += 1

        # Draw recall
        if actual[1] == 1.0:
            actual_draws += 1
            if predicted[1] >= predicted[0] and predicted[1] >= predicted[2]:
                predicted_draws += 1
                correct_draws += 1
        else:
            if predicted[1] >= predicted[0] and predicted[1] >= predicted[2]:
                predicted_draws += 1

    draw_recall = correct_draws / actual_draws if actual_draws > 0 else 0.0

    # ECE
    ece = compute_ece(predictions)
    home_ece = _compute_single_ece(predictions, "home_win")
    draw_ece = _compute_single_ece(predictions, "draw")
    away_ece = _compute_single_ece(predictions, "away_win")

    return ModelMetrics(
        model_name=model_name,
        split_name=split_name,
        data_version=data_version,
        match_count=n,
        brier_score=brier_sum_total / n,
        brier_score_avg=brier_sum_total / (3 * n),
        log_loss=log_loss_total / n,
        ece=ece,
        top1_hit_rate=top1_hits / n,
        draw_recall=draw_recall,
        home_win_brier=home_win_brier_total / n,
        draw_brier=draw_brier_total / n,
        away_win_brier=away_win_brier_total / n,
        home_win_ece=home_ece,
        draw_ece=draw_ece,
        away_win_ece=away_ece,
    )


def compute_ece(predictions: list[MatchPrediction], n_bins: int = 10) -> float:
    """Compute Expected Calibration Error across all three classes.

    ECE = sum over bins of |avg_confidence - avg_accuracy| * bin_weight
    Computed by treating each (prediction, outcome) pair as a binary calibration point.
    """
    if not predictions:
        return 0.0

    # Collect all (predicted_prob, is_correct) pairs across all three classes
    points: list[tuple[float, bool]] = []
    for pred in predictions:
        if pred.home_score > pred.away_score:
            actual = (1.0, 0.0, 0.0)
        elif pred.home_score == pred.away_score:
            actual = (0.0, 1.0, 0.0)
        else:
            actual = (0.0, 0.0, 1.0)

        predicted = (pred.predicted_home_win, pred.predicted_draw, pred.predicted_away_win)
        for p, o in zip(predicted, actual):
            points.append((p, o == 1.0))

    if not points:
        return 0.0

    return _ece_from_points(points, n_bins)


def _compute_single_ece(
    predictions: list[MatchPrediction],
    outcome: str,
    n_bins: int = 10,
) -> float:
    """Compute ECE for a single outcome class."""
    if not predictions:
        return 0.0

    points: list[tuple[float, bool]] = []
    for pred in predictions:
        if outcome == "home_win":
            p = pred.predicted_home_win
            correct = pred.home_score > pred.away_score
        elif outcome == "draw":
            p = pred.predicted_draw
            correct = pred.home_score == pred.away_score
        else:  # away_win
            p = pred.predicted_away_win
            correct = pred.home_score < pred.away_score
        points.append((p, correct))

    return _ece_from_points(points, n_bins)


def _ece_from_points(points: list[tuple[float, bool]], n_bins: int) -> float:
    """Compute ECE from a list of (predicted_prob, is_correct) pairs."""
    if not points:
        return 0.0

    total = len(points)
    bin_size = 1.0 / n_bins
    ece = 0.0

    for i in range(n_bins):
        low = i * bin_size
        high = (i + 1) * bin_size if i < n_bins - 1 else 1.0 + 1e-9

        bin_points = [(p, c) for p, c in points if low <= p < high]
        if not bin_points:
            continue

        bin_count = len(bin_points)
        avg_confidence = sum(p for p, _ in bin_points) / bin_count
        avg_accuracy = sum(1.0 for _, c in bin_points if c) / bin_count
        ece += abs(avg_confidence - avg_accuracy) * (bin_count / total)

    return ece


def stratify_and_compute(
    predictions: list[MatchPrediction],
    model_name: str,
    split_name: str,
    data_version: str,
) -> StratifiedMetrics:
    """Compute stratified metrics across multiple dimensions."""
    result = StratifiedMetrics()

    if not predictions:
        return result

    # By competition type
    by_comp: dict[str, list[MatchPrediction]] = {}
    for pred in predictions:
        ct = pred.competition_type or "other"
        by_comp.setdefault(ct, []).append(pred)
    result.by_competition_type = {
        k: compute_metrics(v, model_name, split_name, data_version)
        for k, v in by_comp.items()
    }

    # By neutral venue
    by_venue: dict[str, list[MatchPrediction]] = {"neutral": [], "home": []}
    for pred in predictions:
        key = "neutral" if pred.neutral_venue else "home"
        by_venue[key].append(pred)
    result.by_neutral_venue = {
        k: compute_metrics(v, model_name, split_name, data_version)
        for k, v in by_venue.items() if v
    }

    # By Elo diff range
    elo_ranges = {
        "strong_home": (-float("inf"), -200),
        "moderate_home": (-200, -50),
        "slight_home": (-50, 50),
        "slight_away": (50, 200),
        "moderate_away": (200, float("inf")),
    }
    by_elo: dict[str, list[MatchPrediction]] = {k: [] for k in elo_ranges}
    for pred in predictions:
        for name, (lo, hi) in elo_ranges.items():
            if lo <= pred.elo_diff < hi:
                by_elo[name].append(pred)
                break
    result.by_elo_diff_range = {
        k: compute_metrics(v, model_name, split_name, data_version)
        for k, v in by_elo.items() if v
    }

    # By year
    by_year: dict[str, list[MatchPrediction]] = {}
    for pred in predictions:
        year = str(pred.available_at.year)
        by_year.setdefault(year, []).append(pred)
    result.by_year = {
        k: compute_metrics(v, model_name, split_name, data_version)
        for k, v in by_year.items()
    }

    # By outcome
    by_outcome: dict[str, list[MatchPrediction]] = {"home_win": [], "draw": [], "away_win": []}
    for pred in predictions:
        if pred.home_score > pred.away_score:
            by_outcome["home_win"].append(pred)
        elif pred.home_score == pred.away_score:
            by_outcome["draw"].append(pred)
        else:
            by_outcome["away_win"].append(pred)
    result.by_outcome = {
        k: compute_metrics(v, model_name, split_name, data_version)
        for k, v in by_outcome.items() if v
    }

    # By confidence (based on max predicted probability)
    by_conf: dict[str, list[MatchPrediction]] = {
        "low": [],      # max_prob < 0.40
        "medium": [],   # 0.40 <= max_prob < 0.55
        "high": [],     # max_prob >= 0.55
    }
    for pred in predictions:
        max_prob = max(pred.predicted_home_win, pred.predicted_draw, pred.predicted_away_win)
        if max_prob < 0.40:
            by_conf["low"].append(pred)
        elif max_prob < 0.55:
            by_conf["medium"].append(pred)
        else:
            by_conf["high"].append(pred)
    result.by_confidence = {
        k: compute_metrics(v, model_name, split_name, data_version)
        for k, v in by_conf.items() if v
    }

    return result
