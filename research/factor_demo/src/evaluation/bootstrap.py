"""Bootstrap置信区间模块

用于验证模型相对Baseline的Brier改善是否稳定且显著。
"""

from __future__ import annotations

import numpy as np
from .metrics import evaluate_predictions, brier_score


def bootstrap_brier_comparison(
    predictions_model: np.ndarray,
    predictions_baseline: np.ndarray,
    labels: np.ndarray,
    n_bootstrap: int = 1000,
    confidence_level: float = 0.95,
    random_seed: int = 42,
) -> dict:
    """Bootstrap比较两个模型的Brier Score差异。

    Args:
        predictions_model: 候选模型预测概率 (N, 3)
        predictions_baseline: 基线模型预测概率 (N, 3)
        labels: 真实标签 (N,) - 'H', 'D', 'A'
        n_bootstrap: Bootstrap次数
        confidence_level: 置信水平
        random_seed: 随机种子

    Returns:
        包含Brier差异统计和置信区间的字典
    """
    rng = np.random.RandomState(random_seed)
    n = len(labels)

    # 全量Brier
    result_model = evaluate_predictions(predictions_model, labels)
    result_baseline = evaluate_predictions(predictions_baseline, labels)

    brier_model_full = result_model.brier_score
    brier_baseline_full = result_baseline.brier_score
    brier_diff_full = brier_baseline_full - brier_model_full  # 正值=模型更好

    # One-hot编码用于快速计算
    label_map = {"H": 0, "D": 1, "A": 2}
    y_true_onehot = np.zeros((n, 3))
    for i, label in enumerate(labels):
        y_true_onehot[i, label_map[label]] = 1.0

    # Bootstrap
    diffs = []
    model_briers = []
    baseline_briers = []

    for _ in range(n_bootstrap):
        indices = rng.randint(0, n, size=n)
        boot_true = y_true_onehot[indices]
        boot_model = predictions_model[indices]
        boot_baseline = predictions_baseline[indices]

        bm = float(np.mean(np.sum((boot_model - boot_true) ** 2, axis=1)))
        bb = float(np.mean(np.sum((boot_baseline - boot_true) ** 2, axis=1)))

        model_briers.append(bm)
        baseline_briers.append(bb)
        diffs.append(bb - bm)  # 正值=模型更好

    diffs = np.array(diffs)
    alpha = 1.0 - confidence_level
    ci_lower = float(np.percentile(diffs, 100 * alpha / 2))
    ci_upper = float(np.percentile(diffs, 100 * (1 - alpha / 2)))

    # p-value: 原假设为差异<=0
    p_value = float(np.mean(diffs <= 0))

    return {
        "brier_model_full": brier_model_full,
        "brier_baseline_full": brier_baseline_full,
        "brier_diff_full": brier_diff_full,
        "relative_improvement": brier_diff_full / brier_baseline_full if brier_baseline_full > 0 else 0.0,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "ci_significant": ci_lower > 0,  # CI不跨越0则显著
        "p_value": p_value,
        "mean_diff": float(diffs.mean()),
        "std_diff": float(diffs.std()),
        "n_bootstrap": n_bootstrap,
        "model_brier_mean": float(np.mean(model_briers)),
        "baseline_brier_mean": float(np.mean(baseline_briers)),
    }


def bootstrap_brier_single(
    predictions: np.ndarray,
    labels: np.ndarray,
    n_bootstrap: int = 1000,
    confidence_level: float = 0.95,
    random_seed: int = 42,
) -> dict:
    """单个模型的Bootstrap Brier置信区间。"""
    rng = np.random.RandomState(random_seed)
    n = len(labels)

    label_map = {"H": 0, "D": 1, "A": 2}
    y_true_onehot = np.zeros((n, 3))
    for i, label in enumerate(labels):
        y_true_onehot[i, label_map[label]] = 1.0

    briers = []
    for _ in range(n_bootstrap):
        indices = rng.randint(0, n, size=n)
        boot_true = y_true_onehot[indices]
        boot_pred = predictions[indices]
        b = float(np.mean(np.sum((boot_pred - boot_true) ** 2, axis=1)))
        briers.append(b)

    briers = np.array(briers)
    alpha = 1.0 - confidence_level

    return {
        "brier_mean": float(briers.mean()),
        "brier_std": float(briers.std()),
        "ci_lower": float(np.percentile(briers, 100 * alpha / 2)),
        "ci_upper": float(np.percentile(briers, 100 * (1 - alpha / 2))),
    }
