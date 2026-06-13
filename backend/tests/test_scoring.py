import math
from dataclasses import dataclass
from typing import Any

import pytest

from app.models import Match, PredictionSnapshot
from app.services.scoring import ModelScoreReport, score_predictions


# ---------------------------------------------------------------------------
# Lightweight stubs for pure-function testing (no DB required)
# ---------------------------------------------------------------------------

@dataclass
class StubSnapshot:
    match_id: str = "m1"
    home_win: float = 0.5
    draw: float = 0.3
    away_win: float = 0.2
    home_xg: float = 1.5
    away_xg: float = 1.0
    scorelines: list[dict[str, Any]] | None = None

    def __post_init__(self):
        if self.scorelines is None:
            self.scorelines = [
                {"home_goals": 1, "away_goals": 0, "probability": 0.15},
                {"home_goals": 1, "away_goals": 1, "probability": 0.12},
                {"home_goals": 2, "away_goals": 1, "probability": 0.10},
            ]


@dataclass
class StubMatch:
    id: str = "m1"
    home_team_id: str = "t1"
    away_team_id: str = "t2"
    home_score: int = 1
    away_score: int = 0
    status: str = "final"


def _pair(snap=None, match=None):
    return (snap or StubSnapshot(), match or StubMatch())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScorePredictions:
    def test_empty_input_returns_zero_report(self):
        report = score_predictions([])
        assert report.matches_scored == 0
        assert report.brier_score == 0.0
        assert report.outcome_hit_rate == 0.0

    def test_perfect_prediction_yields_zero_brier_and_full_hit_rate(self):
        """Model predicts 100% home win, actual result is home win."""
        snap = StubSnapshot(home_win=1.0, draw=0.0, away_win=0.0,
                           home_xg=2.0, away_xg=0.0,
                           scorelines=[{"home_goals": 2, "away_goals": 0, "probability": 1.0}])
        match = StubMatch(home_score=2, away_score=0)
        report = score_predictions([_pair(snap, match)])

        assert report.brier_score == pytest.approx(0.0)
        assert report.outcome_hit_rate == pytest.approx(1.0)
        assert report.top_score_hit_rate == pytest.approx(1.0)
        assert report.xg_mae == pytest.approx(0.0)

    def test_worst_prediction_yields_max_brier_and_zero_hit_rate(self):
        """Model predicts 100% away win, actual result is home win."""
        snap = StubSnapshot(home_win=0.0, draw=0.0, away_win=1.0,
                           home_xg=0.0, away_xg=3.0)
        match = StubMatch(home_score=2, away_score=0)
        report = score_predictions([_pair(snap, match)])

        assert report.brier_score == pytest.approx(2.0)
        assert report.outcome_hit_rate == pytest.approx(0.0)
        assert report.top_score_hit_rate == pytest.approx(0.0)

    def test_known_brier_score_calculation(self):
        """Verify Brier score with known values."""
        snap = StubSnapshot(home_win=0.6, draw=0.25, away_win=0.15)
        match = StubMatch(home_score=1, away_score=0)  # home wins
        report = score_predictions([_pair(snap, match)])

        # Brier = (0.6-1)^2 + (0.25-0)^2 + (0.15-0)^2 = 0.16 + 0.0625 + 0.0225 = 0.245
        assert report.brier_score == pytest.approx(0.245)

    def test_log_loss_for_perfect_prediction(self):
        """Log loss approaches 0 for near-perfect predictions."""
        snap = StubSnapshot(home_win=0.99, draw=0.005, away_win=0.005,
                           home_xg=2.0, away_xg=0.0)
        match = StubMatch(home_score=1, away_score=0)
        report = score_predictions([_pair(snap, match)])
        assert report.log_loss < 0.02

    def test_draw_outcome_detected_correctly(self):
        snap = StubSnapshot(home_win=0.2, draw=0.5, away_win=0.3)
        match = StubMatch(home_score=1, away_score=1)
        report = score_predictions([_pair(snap, match)])
        assert report.outcome_hit_rate == pytest.approx(1.0)
        assert report.per_match[0].outcome_correct is True

    def test_xg_mae_calculation(self):
        snap = StubSnapshot(home_xg=1.5, away_xg=0.8)
        match = StubMatch(home_score=2, away_score=1)
        report = score_predictions([_pair(snap, match)])
        # MAE = (|1.5-2| + |0.8-1|) / 2 = (0.5 + 0.2) / 2 = 0.35
        assert report.xg_mae == pytest.approx(0.35)

    def test_multiple_matches_averaged_correctly(self):
        pairs = [
            _pair(
                StubSnapshot(home_win=0.7, draw=0.2, away_win=0.1, home_xg=1.0, away_xg=1.0),
                StubMatch(id="m1", home_score=1, away_score=0),
            ),
            _pair(
                StubSnapshot(home_win=0.1, draw=0.2, away_win=0.7, home_xg=0.5, away_xg=1.5,
                            match_id="m2"),
                StubMatch(id="m2", home_score=0, away_score=2),
            ),
        ]
        report = score_predictions(pairs)
        assert report.matches_scored == 2
        assert report.outcome_hit_rate == pytest.approx(1.0)
        assert 0 < report.brier_score < 1.0

    def test_top_score_hit_requires_exact_scoreline_in_top_3(self):
        snap = StubSnapshot(
            scorelines=[
                {"home_goals": 1, "away_goals": 0, "probability": 0.15},
                {"home_goals": 0, "away_goals": 0, "probability": 0.10},
                {"home_goals": 2, "away_goals": 1, "probability": 0.08},
            ]
        )
        match = StubMatch(home_score=1, away_score=0)
        report = score_predictions([_pair(snap, match)])
        assert report.top_score_hit_rate == pytest.approx(1.0)

    def test_top_score_miss_when_actual_not_in_top_3(self):
        snap = StubSnapshot(
            scorelines=[
                {"home_goals": 1, "away_goals": 0, "probability": 0.15},
                {"home_goals": 0, "away_goals": 0, "probability": 0.10},
                {"home_goals": 2, "away_goals": 1, "probability": 0.08},
            ]
        )
        match = StubMatch(home_score=3, away_score=2)
        report = score_predictions([_pair(snap, match)])
        assert report.top_score_hit_rate == pytest.approx(0.0)

    def test_per_match_details_populated(self):
        report = score_predictions([_pair()])
        assert len(report.per_match) == 1
        detail = report.per_match[0]
        assert detail.match_id == "m1"
        assert "home_win" in detail.predicted
        assert "home_score" in detail.actual
        assert isinstance(detail.outcome_correct, bool)
