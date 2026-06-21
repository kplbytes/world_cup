"""概率校准分析模块"""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


def calibration_curve(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = 10,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """计算校准曲线。
    
    Args:
        y_true: 真实标签 (N,)
        y_prob: 预测概率 (N,)
        n_bins: 分箱数
    
    Returns:
        (bin_centers, actual_frequencies, bin_counts)
    """
    bin_boundaries = np.linspace(0, 1, n_bins + 1)
    bin_centers = []
    actual_freqs = []
    bin_counts = []
    
    for i in range(n_bins):
        low, high = bin_boundaries[i], bin_boundaries[i + 1]
        mask = (y_prob > low) & (y_prob <= high)
        count = mask.sum()
        
        if count > 0:
            bin_centers.append((low + high) / 2)
            actual_freqs.append(float(y_true[mask].mean()))
            bin_counts.append(int(count))
    
    return np.array(bin_centers), np.array(actual_freqs), np.array(bin_counts)


def reliability_diagram_data(
    predictions: np.ndarray,
    labels: np.ndarray,
    n_bins: int = 10,
) -> dict:
    """生成可靠性图数据。
    
    Args:
        predictions: (N, 3) 预测概率
        labels: (N,) 真实标签 'H', 'D', 'A'
        n_bins: 分箱数
    
    Returns:
        各类别的校准数据
    """
    label_map = {"H": 0, "D": 1, "A": 2}
    result = {}
    
    for name, idx in label_map.items():
        y_true = (np.array([label_map[l] for l in labels]) == idx).astype(float)
        y_prob = predictions[:, idx]

        centers, freqs, counts = calibration_curve(y_true, y_prob, n_bins)
        result[name] = {
            "bin_centers": centers.tolist(),
            "actual_frequencies": freqs.tolist(),
            "counts": counts.tolist(),
        }

    return result


def platt_scale_calibration(
    train_probs: np.ndarray,
    train_labels: np.ndarray,
    test_probs: np.ndarray,
) -> np.ndarray:
    """应用 Platt 缩放（逻辑回归校准）校准概率输出。

    对每个类别独立训练一个 Logistic 回归，将原始概率映射为校准概率。

    Args:
        train_probs: 训练集原始概率 (N_train, C)
        train_labels: 训练集真实标签 (N_train,)，整数编码
        test_probs: 测试集原始概率 (N_test, C)

    Returns:
        校准后的测试集概率 (N_test, C)
    """
    n_classes = train_probs.shape[1]
    calibrated = np.zeros_like(test_probs)

    for c in range(n_classes):
        binary_labels = (train_labels == c).astype(int)
        lr = LogisticRegression(C=1e10, solver="lbfgs", max_iter=1000)
        lr.fit(train_probs[:, c:c + 1], binary_labels)
        calibrated[:, c] = lr.predict_proba(test_probs[:, c:c + 1])[:, 1]

    # 归一化使每行和为1
    row_sums = calibrated.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    calibrated = calibrated / row_sums

    return calibrated


def isotonic_calibration(
    train_probs: np.ndarray,
    train_labels: np.ndarray,
    test_probs: np.ndarray,
) -> np.ndarray:
    """应用等距回归校准概率输出。

    对每个类别独立训练一个 Isotonic Regression，将原始概率映射为校准概率。

    Args:
        train_probs: 训练集原始概率 (N_train, C)
        train_labels: 训练集真实标签 (N_train,)，整数编码
        test_probs: 测试集原始概率 (N_test, C)

    Returns:
        校准后的测试集概率 (N_test, C)
    """
    n_classes = train_probs.shape[1]
    calibrated = np.zeros_like(test_probs)

    for c in range(n_classes):
        binary_labels = (train_labels == c).astype(float)
        ir = IsotonicRegression(out_of_bounds="clip")
        ir.fit(train_probs[:, c], binary_labels)
        calibrated[:, c] = ir.transform(test_probs[:, c])

    # 归一化使每行和为1
    row_sums = calibrated.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    calibrated = calibrated / row_sums

    return calibrated


def calibration_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = 10,
) -> dict:
    """计算综合校准指标。

    包括 ECE、MCE 和 Brier Score 分解（可靠性、分辨率、不确定性）。

    Args:
        y_true: 真实标签 (N,)，二值（0/1）
        y_prob: 预测概率 (N,)，正类概率
        n_bins: 分箱数

    Returns:
        包含 ece, mce, brier, reliability, resolution, uncertainty 的字典
    """
    n = len(y_true)
    bin_boundaries = np.linspace(0, 1, n_bins + 1)

    # ECE 和 MCE
    ece = 0.0
    mce = 0.0

    # Brier Score 分解所需
    overall_accuracy = float(y_true.mean())

    reliability = 0.0
    resolution = 0.0

    for i in range(n_bins):
        low, high = bin_boundaries[i], bin_boundaries[i + 1]
        mask = (y_prob > low) & (y_prob <= high)
        count = int(mask.sum())

        if count == 0:
            continue

        bin_accuracy = float(y_true[mask].mean())
        bin_confidence = float(y_prob[mask].mean())
        bin_weight = count / n

        # ECE
        ece += bin_weight * abs(bin_accuracy - bin_confidence)

        # MCE
        gap = abs(bin_accuracy - bin_confidence)
        if gap > mce:
            mce = gap

        # Brier Score 分解
        reliability += bin_weight * (bin_accuracy - bin_confidence) ** 2
        resolution += bin_weight * (bin_accuracy - overall_accuracy) ** 2

    # 不确定性
    uncertainty = overall_accuracy * (1.0 - overall_accuracy)

    # Brier Score
    brier = float(np.mean((y_prob - y_true) ** 2))

    return {
        "ece": float(ece),
        "mce": float(mce),
        "brier": brier,
        "reliability": float(reliability),
        "resolution": float(resolution),
        "uncertainty": float(uncertainty),
    }
