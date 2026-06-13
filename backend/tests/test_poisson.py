import math

import pytest

from app.prediction.poisson import MatchContext, predict_match


def neutral_context(**overrides):
    values = {
        "data_freshness": 1.0,
        "ranking_coverage": 1.0,
        "history_coverage": 1.0,
        "provider_agreement": 1.0,
        "recent_form_delta": 0.0,
        "host_advantage": 0.0,
    }
    values.update(overrides)
    return MatchContext(**values)


def test_prediction_probabilities_cover_the_full_outcome_space():
    prediction = predict_match(0.72, 0.51, neutral_context())

    assert prediction.home_xg > prediction.away_xg
    assert prediction.home_win + prediction.draw + prediction.away_win == pytest.approx(1.0)
    assert sum(sum(row) for row in prediction.score_matrix) == pytest.approx(1.0)
    assert len(prediction.scorelines) == 3


def test_stale_missing_data_lowers_confidence_without_changing_probability_normalization():
    prediction = predict_match(
        0.80,
        0.35,
        neutral_context(data_freshness=0.2, history_coverage=0.0),
    )

    assert prediction.confidence < 0.6
    assert prediction.confidence_label == "低"
    assert prediction.home_win + prediction.draw + prediction.away_win == pytest.approx(1.0)


def test_prediction_rejects_non_finite_strength():
    with pytest.raises(ValueError, match="finite"):
        predict_match(math.nan, 0.5, neutral_context())


def test_manual_adjustments_shift_expected_goals():
    base = predict_match(0.60, 0.55, neutral_context())
    adjusted = predict_match(
        0.60,
        0.55,
        neutral_context(
            home_attack_adjustment=-0.20,
            away_attack_adjustment=0.10,
            away_defense_adjustment=0.05,
        ),
    )

    assert adjusted.home_xg < base.home_xg
    assert adjusted.away_xg > base.away_xg
