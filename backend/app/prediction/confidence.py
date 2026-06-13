from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceInputs:
    data_freshness: float
    ranking_coverage: float
    history_coverage: float
    provider_agreement: float


def data_confidence(values: ConfidenceInputs) -> tuple[float, str]:
    """数据置信度：衡量输入数据的质量和新鲜度。

    加权组合四个维度（新鲜度 35%、排名覆盖 25%、历史覆盖 25%、来源一致 15%），
    ≥0.8 → 高，≥0.6 → 中，<0.6 → 低。
    """
    score = (
        0.35 * _bounded(values.data_freshness)
        + 0.25 * _bounded(values.ranking_coverage)
        + 0.25 * _bounded(values.history_coverage)
        + 0.15 * _bounded(values.provider_agreement)
    )
    label = "高" if score >= 0.8 else "中" if score >= 0.6 else "低"
    return score, label


# 向后兼容别名
calculate_confidence = data_confidence


def model_confidence(home_win: float, draw: float, away_win: float) -> tuple[float, str]:
    """模型置信度：基于概率分布的确定性。

    公式：max(三项) - mean(其余两项)。
    ≥0.30 → 高，≥0.15 → 中，<0.15 → 低。
    概率越集中 → 模型越确定；三项接近 → 模型越纠结。
    """
    probs = sorted([home_win, draw, away_win], reverse=True)
    top = probs[0]
    rest_mean = (probs[1] + probs[2]) / 2.0
    score = top - rest_mean
    label = "高" if score >= 0.30 else "中" if score >= 0.15 else "低"
    return score, label


def _bounded(value: float) -> float:
    return min(1.0, max(0.0, value))

