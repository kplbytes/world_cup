"""概率校准分析模块"""

from __future__ import annotations

import numpy as np


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
