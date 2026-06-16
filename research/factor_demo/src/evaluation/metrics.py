"""评估指标模块

主要指标：Brier Score
辅助指标：Log Loss, 命中率, 概率校准
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import log_loss, brier_score_loss


@dataclass
class EvaluationResult:
    """评估结果。"""
    brier_score: float
    brier_home: float
    brier_draw: float
    brier_away: float
    log_loss: float
    accuracy: float
    n_samples: int
    
    # 分项命中率
    home_win_accuracy: float
    draw_accuracy: float
    away_win_accuracy: float
    
    # 校准
    ece: float  # Expected Calibration Error
    
    def __str__(self) -> str:
        return (
            f"Brier={self.brier_score:.4f} "
            f"(H={self.brier_home:.4f} D={self.brier_draw:.4f} A={self.brier_away:.4f}) | "
            f"LogLoss={self.log_loss:.4f} | "
            f"Acc={self.accuracy:.1%} | "
            f"ECE={self.ece:.4f} | "
            f"N={self.n_samples}"
        )


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """计算多分类 Brier Score。
    
    Args:
        y_true: 真实标签，one-hot 编码 (N, 3)
        y_prob: 预测概率 (N, 3)
    
    Returns:
        平均 Brier Score
    """
    return float(np.mean(np.sum((y_prob - y_true) ** 2, axis=1)))


def brier_score_decomposed(y_true: np.ndarray, y_prob: np.ndarray) -> tuple[float, float, float]:
    """分解 Brier Score 为各项。
    
    Returns:
        (brier_home, brier_draw, brier_away)
    """
    brier_h = float(np.mean((y_prob[:, 0] - y_true[:, 0]) ** 2))
    brier_d = float(np.mean((y_prob[:, 1] - y_true[:, 1]) ** 2))
    brier_a = float(np.mean((y_prob[:, 2] - y_true[:, 2]) ** 2))
    return brier_h, brier_d, brier_a


def expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """计算 Expected Calibration Error。
    
    Args:
        y_true: 真实标签 (N,)
        y_prob: 预测概率 (N,) - 正类概率
        n_bins: 分箱数
    
    Returns:
        ECE
    """
    bin_boundaries = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    total = len(y_true)
    
    for i in range(n_bins):
        low, high = bin_boundaries[i], bin_boundaries[i + 1]
        mask = (y_prob > low) & (y_prob <= high)
        if mask.sum() == 0:
            continue
        bin_accuracy = y_true[mask].mean()
        bin_confidence = y_prob[mask].mean()
        ece += (mask.sum() / total) * abs(bin_accuracy - bin_confidence)
    
    return float(ece)


def evaluate_predictions(
    predictions: np.ndarray,
    labels: np.ndarray,
) -> EvaluationResult:
    """评估预测结果。
    
    Args:
        predictions: 预测概率 (N, 3) - [home_win, draw, away_win]
        labels: 真实标签 (N,) - 'H', 'D', 'A'
    
    Returns:
        EvaluationResult
    """
    n = len(labels)
    
    # One-hot 编码
    label_map = {"H": 0, "D": 1, "A": 2}
    y_true_onehot = np.zeros((n, 3))
    for i, label in enumerate(labels):
        y_true_onehot[i, label_map[label]] = 1.0
    
    # Brier Score
    bs = brier_score(y_true_onehot, predictions)
    bs_h, bs_d, bs_a = brier_score_decomposed(y_true_onehot, predictions)
    
    # Log Loss - sklearn expects integer class labels, not one-hot
    true_classes = np.array([label_map[l] for l in labels])
    ll = log_loss(true_classes, predictions, labels=[0, 1, 2])
    
    # 命中率
    pred_classes = np.argmax(predictions, axis=1)
    accuracy = float(np.mean(pred_classes == true_classes))
    
    # 分项命中率
    h_mask = true_classes == 0
    d_mask = true_classes == 1
    a_mask = true_classes == 2
    
    home_acc = float(np.mean(pred_classes[h_mask] == 0)) if h_mask.sum() > 0 else 0.0
    draw_acc = float(np.mean(pred_classes[d_mask] == 1)) if d_mask.sum() > 0 else 0.0
    away_acc = float(np.mean(pred_classes[a_mask] == 2)) if a_mask.sum() > 0 else 0.0
    
    # ECE（用主队胜概率）
    ece = expected_calibration_error(y_true_onehot[:, 0], predictions[:, 0])
    
    return EvaluationResult(
        brier_score=bs,
        brier_home=bs_h,
        brier_draw=bs_d,
        brier_away=bs_a,
        log_loss=ll,
        accuracy=accuracy,
        n_samples=n,
        home_win_accuracy=home_acc,
        draw_accuracy=draw_acc,
        away_win_accuracy=away_acc,
        ece=ece,
    )


def result_to_onehot(result: str) -> np.ndarray:
    """将比赛结果转为 one-hot 编码。"""
    label_map = {"H": 0, "D": 1, "A": 2}
    arr = np.zeros(3)
    arr[label_map[result]] = 1.0
    return arr


def compare_models(results: dict[str, EvaluationResult]) -> str:
    """对比多个模型的评估结果。"""
    lines = ["=" * 80]
    lines.append(f"{'Model':<30} {'Brier':>8} {'LogLoss':>8} {'Acc':>6} {'ECE':>6} {'N':>6}")
    lines.append("-" * 80)
    
    for name, r in sorted(results.items(), key=lambda x: x[1].brier_score):
        lines.append(
            f"{name:<30} {r.brier_score:>8.4f} {r.log_loss:>8.4f} "
            f"{r.accuracy:>5.1%} {r.ece:>6.4f} {r.n_samples:>6d}"
        )
    
    lines.append("=" * 80)
    return "\n".join(lines)
