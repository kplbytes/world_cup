import pytest

from app.prediction.confidence import (
    ConfidenceInputs,
    data_confidence,
    model_confidence,
)


# ---------------------------------------------------------------------------
# data_confidence — 沿用原有加权公式，只改函数名
# ---------------------------------------------------------------------------

class TestDataConfidence:
    def test_perfect_inputs_yield_high_confidence(self):
        score, label = data_confidence(
            ConfidenceInputs(
                data_freshness=1.0,
                ranking_coverage=1.0,
                history_coverage=1.0,
                provider_agreement=1.0,
            )
        )
        assert score == pytest.approx(1.0)
        assert label == "高"

    def test_stale_data_yields_low_confidence(self):
        score, label = data_confidence(
            ConfidenceInputs(
                data_freshness=0.2,
                ranking_coverage=0.5,
                history_coverage=0.0,
                provider_agreement=0.3,
            )
        )
        assert score < 0.6
        assert label == "低"

    def test_moderate_inputs_yield_medium_confidence(self):
        score, label = data_confidence(
            ConfidenceInputs(
                data_freshness=0.7,
                ranking_coverage=0.8,
                history_coverage=0.5,
                provider_agreement=0.6,
            )
        )
        assert 0.6 <= score < 0.8
        assert label == "中"

    def test_values_are_clamped_to_zero_one(self):
        score, _ = data_confidence(
            ConfidenceInputs(
                data_freshness=2.0,
                ranking_coverage=-1.0,
                history_coverage=0.5,
                provider_agreement=0.5,
            )
        )
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# model_confidence — 基于概率分布的确定性
# ---------------------------------------------------------------------------

class TestModelConfidence:
    def test_uniform_probabilities_yield_low_confidence(self):
        """三项概率均等 → 模型不确定 → 低"""
        score, label = model_confidence(0.33, 0.34, 0.33)
        assert label == "低"
        assert score < 0.6

    def test_strong_favorite_yields_high_confidence(self):
        """主队胜率 0.85 → 模型非常确定 → 高"""
        score, label = model_confidence(0.85, 0.10, 0.05)
        assert label == "高"
        assert score >= 0.8

    def test_moderate_favorite_yields_medium_confidence(self):
        """适度偏向某一结果 → 中

        新归一化语义：score 0..1（除以 2/3），
        0.6 ≤ score < 0.8 → 中。
        使用 (0.65, 0.20, 0.15) → 归一化 score ≈ 0.71。
        """
        score, label = model_confidence(0.65, 0.20, 0.15)
        assert label == "中"
        assert 0.6 <= score < 0.8

    def test_away_favorite_also_works(self):
        """客队高胜率同样高置信度"""
        score, label = model_confidence(0.10, 0.15, 0.75)
        assert label == "高"
        assert score >= 0.8

    def test_draw_slightly_favored_low_confidence(self):
        """平局微幅领先，三项接近 → 低"""
        score, label = model_confidence(0.30, 0.38, 0.32)
        assert label == "低"

    def test_probabilities_need_not_sum_to_one(self):
        """函数内部不强制校验概率和，只做集中度计算"""
        # 即使概率不归一化，公式仍然能给出合理的集中度度量
        score, label = model_confidence(0.90, 0.05, 0.05)
        assert label == "高"

    def test_score_is_normalized_to_zero_one(self):
        """归一化后 score 应在 0..1 范围内，与 data_confidence 同尺度"""
        # Strong favorite: raw = 0.775, normalized ≈ 1.16, clipped to 1.0
        score, _ = model_confidence(0.85, 0.10, 0.05)
        assert 0.0 <= score <= 1.0
        # Uniform: raw ≈ 0.01, normalized ≈ 0.015
        score_uniform, _ = model_confidence(0.33, 0.34, 0.33)
        assert 0.0 <= score_uniform <= 1.0
