"""Paired bootstrap significance testing for model comparison."""

import numpy as np
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class BootstrapResult:
    """Result of a paired bootstrap test."""
    model_a: str
    model_b: str  # baseline (usually legacy)
    metric_name: str

    # Differences (model_a - model_b)
    observed_diff: float = 0.0
    bootstrap_mean_diff: float = 0.0
    bootstrap_std_diff: float = 0.0

    # Confidence interval
    ci_lower_95: float = 0.0
    ci_upper_95: float = 0.0

    # Probability that model_a is better than model_b
    p_better: float = 0.0

    # Sample info
    n_matches: int = 0
    n_bootstrap: int = 0

    # Per-fold results (if available)
    per_fold: dict[str, dict] = field(default_factory=dict)

    @property
    def is_significant(self) -> bool:
        """Whether the difference is statistically significant (95% CI doesn't include 0)."""
        return self.ci_lower_95 > 0 or self.ci_upper_95 < 0

    @property
    def conclusion(self) -> str:
        """Human-readable conclusion."""
        if not self.is_significant:
            return "inconclusive"
        if self.observed_diff < 0:
            return f"{self.model_a} significantly better"
        else:
            return f"{self.model_a} significantly worse"


def paired_bootstrap(
    predictions_a: list,  # list of MatchPrediction
    predictions_b: list,  # list of MatchPrediction (baseline)
    metric_fn: Callable,
    metric_name: str,
    n_bootstrap: int = 5000,
    seed: int = 42,
) -> BootstrapResult:
    """Run paired bootstrap test comparing two models.

    Both prediction lists must be aligned by source_match_id.
    Samples are drawn by match (same matches for both models).
    """
    rng = np.random.RandomState(seed)

    # Align predictions by match ID
    a_by_id = {p.source_match_id: p for p in predictions_a}
    b_by_id = {p.source_match_id: p for p in predictions_b}
    common_ids = sorted(set(a_by_id.keys()) & set(b_by_id.keys()))

    if not common_ids:
        return BootstrapResult(
            model_a=getattr(predictions_a[0], 'model_name', 'a') if predictions_a else 'a',
            model_b=getattr(predictions_b[0], 'model_name', 'b') if predictions_b else 'b',
            metric_name=metric_name,
            n_matches=0,
            n_bootstrap=n_bootstrap,
        )

    aligned_a = [a_by_id[mid] for mid in common_ids]
    aligned_b = [b_by_id[mid] for mid in common_ids]
    n = len(common_ids)

    # Observed difference
    obs_a = metric_fn(aligned_a)
    obs_b = metric_fn(aligned_b)
    observed_diff = obs_a - obs_b

    # Bootstrap
    diffs = np.zeros(n_bootstrap)
    for i in range(n_bootstrap):
        indices = rng.randint(0, n, size=n)
        boot_a = [aligned_a[idx] for idx in indices]
        boot_b = [aligned_b[idx] for idx in indices]
        diffs[i] = metric_fn(boot_a) - metric_fn(boot_b)

    # Statistics
    bootstrap_mean = float(np.mean(diffs))
    bootstrap_std = float(np.std(diffs))
    ci_lower = float(np.percentile(diffs, 2.5))
    ci_upper = float(np.percentile(diffs, 97.5))
    p_better = float(np.mean(diffs < 0))  # probability that a < b (lower is better for Brier/LogLoss)

    return BootstrapResult(
        model_a=aligned_a[0].model_name if aligned_a else 'a',
        model_b=aligned_b[0].model_name if aligned_b else 'b',
        metric_name=metric_name,
        observed_diff=observed_diff,
        bootstrap_mean_diff=bootstrap_mean,
        bootstrap_std_diff=bootstrap_std,
        ci_lower_95=ci_lower,
        ci_upper_95=ci_upper,
        p_better=p_better,
        n_matches=n,
        n_bootstrap=n_bootstrap,
    )


# Metric functions for bootstrap
def brier_sum_fn(preds: list) -> float:
    """Compute mean brier_sum for a list of MatchPrediction."""
    if not preds:
        return 0.0
    total = 0.0
    for pred in preds:
        if pred.home_score > pred.away_score:
            actual = (1.0, 0.0, 0.0)
        elif pred.home_score == pred.away_score:
            actual = (0.0, 1.0, 0.0)
        else:
            actual = (0.0, 0.0, 1.0)
        predicted = (pred.predicted_home_win, pred.predicted_draw, pred.predicted_away_win)
        total += sum((p - o) ** 2 for p, o in zip(predicted, actual))
    return total / len(preds)


def log_loss_fn(preds: list) -> float:
    """Compute mean log loss for a list of MatchPrediction."""
    import math
    if not preds:
        return 0.0
    total = 0.0
    eps = 1e-15
    for pred in preds:
        if pred.home_score > pred.away_score:
            actual = (1.0, 0.0, 0.0)
        elif pred.home_score == pred.away_score:
            actual = (0.0, 1.0, 0.0)
        else:
            actual = (0.0, 0.0, 1.0)
        predicted = (pred.predicted_home_win, pred.predicted_draw, pred.predicted_away_win)
        ll = -sum(o * math.log(max(p, eps)) for p, o in zip(predicted, actual) if o > 0)
        total += ll
    return total / len(preds)


def top1_accuracy_fn(preds: list) -> float:
    """Compute top-1 accuracy for a list of MatchPrediction."""
    if not preds:
        return 0.0
    hits = 0
    for pred in preds:
        predicted = (pred.predicted_home_win, pred.predicted_draw, pred.predicted_away_win)
        max_idx = predicted.index(max(predicted))
        if max_idx == 0 and pred.home_score > pred.away_score:
            hits += 1
        elif max_idx == 1 and pred.home_score == pred.away_score:
            hits += 1
        elif max_idx == 2 and pred.home_score < pred.away_score:
            hits += 1
    return hits / len(preds)
