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
    label = _label_from_score(score)
    return score, label


# 向后兼容别名
calculate_confidence = data_confidence


def model_confidence(home_win: float, draw: float, away_win: float) -> tuple[float, str]:
    """模型置信度：基于概率分布的确定性。

    公式：(max(三项) - mean(其余两项)) / (2/3) 归一化到 0..1。
    与 data_confidence 处于同一尺度，使用统一标签阈值
    （≥0.8 高，≥0.6 中，<0.6 低）。
    概率越集中 → 模型越确定；三项接近 → 模型越纠结。
    """
    probs = sorted([home_win, draw, away_win], reverse=True)
    top = probs[0]
    rest_mean = (probs[1] + probs[2]) / 2.0
    raw_score = top - rest_mean
    # 归一化到 0..1（理论最大值 2/3 ≈ 0.667）。
    # 这样与 data_confidence 处于同一尺度，标签语义一致。
    normalized = _bounded(raw_score / (2.0 / 3.0))
    label = _label_from_score(normalized)
    return normalized, label


def overall_confidence(
    data_score: float,
    model_score: float,
    data_weight: float = 0.6,
    model_weight: float = 0.4,
) -> tuple[float, str]:
    """综合置信度：数据置信度与模型置信度的加权融合。

    默认权重：数据 60% + 模型 40%。两者均已归一化到 0..1，
    使用统一的标签阈值（≥0.8 高，≥0.6 中，<0.6 低），
    保证 overall / data / model 三种置信度语义一致。
    """
    blended = (
        data_weight * _bounded(data_score)
        + model_weight * _bounded(model_score)
    ) / max(data_weight + model_weight, 1e-12)
    label = _label_from_score(blended)
    return blended, label


def _label_from_score(score: float) -> str:
    """统一标签阈值：≥0.8 高，≥0.6 中，<0.6 低。"""
    if score >= 0.8:
        return "高"
    if score >= 0.6:
        return "中"
    return "低"


def _bounded(value: float) -> float:
    return min(1.0, max(0.0, value))

