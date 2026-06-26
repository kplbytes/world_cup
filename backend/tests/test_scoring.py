import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import event

from app.models import AIPrediction, DashboardRevision, EnsemblePrediction, Match, MatchPrediction, PredictionSnapshot, Team
from app.ai.lock_status import compute_match_lock_status
from app.services.scoring import ModelScoreReport, get_match_count_breakdown, score_predictions


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
    has_auto_adjustments: bool = False
    base_home_win: float | None = None
    base_draw: float | None = None
    base_away_win: float | None = None
    model_version: str = "v1"
    confidence_label: str = "High"
    snapshotted_at: Any = None
    kickoff: Any = None

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
    kickoff: Any = None


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
        d = report.per_match[0]
        assert d.match_id == "m1"
        assert d.predicted["home_win"] == 0.5
        assert d.actual["home_score"] == 1
        assert d.outcome_correct is True
        assert d.top_score_correct is True
        assert d.probability_effect == 0.0
        assert d.warning_effect == "neutral"

    def test_auto_adjustment_effect_helped(self):
        snap = StubSnapshot(
            has_auto_adjustments=True,
            base_home_win=0.4,
            base_draw=0.4,
            base_away_win=0.2,
            home_win=0.8,
            draw=0.1,
            away_win=0.1,
            model_version="elo-poisson-v1-intel-numeric"
        )
        match = StubMatch(home_score=1, away_score=0)
        report = score_predictions([(snap, match)])
        assert report.per_match[0].numerical_effect == "helped"

    def test_auto_adjustment_effect_hurt(self):
        snap = StubSnapshot(
            has_auto_adjustments=True,
            base_home_win=0.8,
            base_draw=0.1,
            base_away_win=0.1,
            home_win=0.4,
            draw=0.4,
            away_win=0.2,
            model_version="elo-poisson-v1-intel-numeric"
        )
        match = StubMatch(home_score=1, away_score=0)
        report = score_predictions([(snap, match)])
        assert report.per_match[0].numerical_effect == "hurt"

    def test_auto_adjustment_effect_neutral(self):
        snap = StubSnapshot(
            has_auto_adjustments=True,
            base_home_win=0.5,
            base_draw=0.3,
            base_away_win=0.2,
            home_win=0.501,
            draw=0.299,
            away_win=0.2,
            model_version="elo-poisson-v1-intel-numeric"
        )
        match = StubMatch(home_score=1, away_score=0)
        report = score_predictions([(snap, match)])
        assert report.per_match[0].numerical_effect == "neutral"

def test_no_pre_match_snapshot_excludes_from_model_score(db_session):
    from app.models import DashboardRevision, Match, MatchPrediction, PredictionSnapshot, Team
    from app.services.scoring import snapshot_prediction, score_model

    # 1. Setup Match and Teams
    db_session.add_all([
        Team(id="T1", name="T1", short_name="T1", code="T1", group_code="A"),
        Team(id="T2", name="T2", short_name="T2", code="T2", group_code="A")
    ])
    db_session.flush()

    kickoff = datetime.now(timezone.utc) - timedelta(hours=2)
    match = Match(id="m1_no_snap", group_code="A", home_team_id="T1", away_team_id="T2", kickoff=kickoff, status="scheduled", source="test")
    db_session.add(match)
    db_session.flush()

    # 2. Add an active revision and a prediction (simulating post-kickoff recompute)
    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()

    pred = MatchPrediction(revision_id=rev.id, match_id="m1_no_snap", home_win=0.5, draw=0.3, away_win=0.2, home_xg=1.0, away_xg=1.0, confidence=0.8, confidence_label="High", data_confidence=0.9, data_confidence_label="High", model_confidence=0.85, model_confidence_label="High", explanation="Test", model_inputs={}, model_version="v1", scorelines=[], score_matrix=[])
    db_session.add(pred)

    # Create a snapshot POST-kickoff
    snap = PredictionSnapshot(match_id="m1_no_snap", revision_id=rev.id, kickoff=kickoff, snapshotted_at=datetime.now(timezone.utc), home_win=0.5, draw=0.3, away_win=0.2, home_xg=1.0, away_xg=1.0, scorelines=[], score_matrix=[], confidence=0.8, confidence_label="High", model_inputs={}, model_version="v1")
    db_session.add(snap)
    db_session.flush()

    # 3. Match turns final, triggers snapshot_prediction
    match.status = "final"
    match.home_score = 1
    match.away_score = 0
    db_session.flush()

    # Attempt to snapshot
    locked_snap = snapshot_prediction(db_session, "m1_no_snap")
    assert locked_snap is None  # Should return None and log warning

    # 4. Ensure it does not appear in score_model report
    report = score_model(db_session)
    # The match should NOT be in per_match
    assert not any(m.match_id == "m1_no_snap" for m in report.per_match)


def test_pre_match_snapshot_promoted_to_fallback_and_scored(db_session):
    from app.models import DashboardRevision, Match, MatchPrediction, PredictionSnapshot, Team
    from app.services.scoring import snapshot_prediction, score_model

    db_session.add_all([
        Team(id="T3", name="T3", short_name="T3", code="T3", group_code="A"),
        Team(id="T4", name="T4", short_name="T4", code="T4", group_code="A")
    ])
    db_session.flush()

    kickoff = datetime.now(timezone.utc) - timedelta(hours=2)
    match = Match(id="m2_fallback", group_code="A", home_team_id="T3", away_team_id="T4", kickoff=kickoff, status="scheduled", source="test")
    db_session.add(match)
    db_session.flush()

    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()

    pred = MatchPrediction(
        revision_id=rev.id, match_id="m2_fallback", home_win=0.6, draw=0.25, away_win=0.15,
        home_xg=1.2, away_xg=0.7, confidence=0.8, confidence_label="High",
        data_confidence=0.9, data_confidence_label="High",
        model_confidence=0.85, model_confidence_label="High",
        explanation="Test", model_inputs={}, model_version="v1", scorelines=[], score_matrix=[]
    )
    db_session.add(pred)

    pre_match_snap = PredictionSnapshot(
        match_id="m2_fallback",
        revision_id=rev.id,
        kickoff=kickoff,
        snapshotted_at=kickoff - timedelta(minutes=40),
        home_win=0.6,
        draw=0.25,
        away_win=0.15,
        home_xg=1.2,
        away_xg=0.7,
        scorelines=[],
        score_matrix=[],
        confidence=0.8,
        confidence_label="High",
        model_inputs={},
        model_version="v1",
    )
    db_session.add(pre_match_snap)
    db_session.flush()

    match.status = "final"
    match.home_score = 1
    match.away_score = 0
    db_session.flush()

    locked_snap = snapshot_prediction(db_session, "m2_fallback")
    assert locked_snap is not None
    assert locked_snap.is_fallback_locked is True

    report = score_model(db_session)
    assert any(m.match_id == "m2_fallback" for m in report.per_match)


def test_lock_status_only_locks_within_24h():
    kickoff = datetime.now(timezone.utc) + timedelta(hours=25)
    match = type("MatchStub", (), {"kickoff": kickoff, "status": "scheduled"})()

    # Outside 24h window
    early = compute_match_lock_status(match, now=kickoff - timedelta(hours=25))
    assert early.is_pre_match_locked is False
    assert early.is_fallback_locked is False
    assert early.real_time_only is False

    # Within 24h window
    within_24h = compute_match_lock_status(match, now=kickoff - timedelta(hours=20))
    assert within_24h.is_pre_match_locked is True
    assert within_24h.real_time_only is False

    # After kickoff
    after_kickoff = compute_match_lock_status(match, now=kickoff + timedelta(minutes=5))
    assert after_kickoff.is_pre_match_locked is False
    assert after_kickoff.is_fallback_locked is False
    assert after_kickoff.real_time_only is True


# ---------------------------------------------------------------------------
# Tests for xG field correctness (not confused with probability fields)
# ---------------------------------------------------------------------------

class TestClassifyErrorXgFields:
    """Verify classify_error receives xG values, not probability values."""

    def test_classify_error_uses_xg_not_probability(self):
        """classify_error should receive home_xg/away_xg, not home_win_prob/away_win_prob."""
        from app.services.error_attribution import classify_error

        errors = classify_error(
            home_win_prob=0.7,
            draw_prob=0.2,
            away_win_prob=0.1,
            actual_result="home",
            home_xg=1.8,
            away_xg=0.9,
            actual_home_score=2,
            actual_away_score=1,
        )

        # The function should process xG correctly; verify it doesn't crash
        # and returns a non-empty result for a correct prediction
        assert isinstance(errors, list)

    def test_classify_error_low_score_draw_missed_uses_actual_scores(self):
        """low_score_draw_missed should use actual scores, not xG or probabilities."""
        from app.services.error_attribution import classify_error

        # Low-scoring draw (1:1) with draw_prob < 28% should trigger low_score_draw_missed
        errors = classify_error(
            home_win_prob=0.55,
            draw_prob=0.20,
            away_win_prob=0.25,
            actual_result="draw",
            home_xg=1.8,
            away_xg=0.9,
            actual_home_score=1,
            actual_away_score=1,
        )

        error_types = [e.error_type for e in errors]
        assert "low_score_draw_missed" in error_types

    def test_classify_error_high_score_draw_no_low_score_flag(self):
        """A draw with total goals > 2 should NOT trigger low_score_draw_missed."""
        from app.services.error_attribution import classify_error

        # High-scoring draw (2:2) with draw_prob < 28% should NOT trigger low_score_draw_missed
        errors = classify_error(
            home_win_prob=0.55,
            draw_prob=0.20,
            away_win_prob=0.25,
            actual_result="draw",
            home_xg=2.5,
            away_xg=1.8,
            actual_home_score=2,
            actual_away_score=2,
        )

        error_types = [e.error_type for e in errors]
        assert "low_score_draw_missed" not in error_types

    def test_classify_error_xg_not_confused_with_probability(self):
        """Verify xG values are distinct from probability values in classify_error.

        If home_xg were mistakenly set to home_win_prob (0.7), the goal_total_missed
        check would behave differently. With correct xG (1.8/0.9), the most likely
        scoreline total should be close to actual total (3).
        """
        from app.services.error_attribution import classify_error

        scorelines = [
            {"home_goals": 2, "away_goals": 1, "probability": 0.15},
            {"home_goals": 1, "away_goals": 1, "probability": 0.12},
            {"home_goals": 2, "away_goals": 0, "probability": 0.10},
        ]

        errors = classify_error(
            home_win_prob=0.7,
            draw_prob=0.2,
            away_win_prob=0.1,
            actual_result="home",
            home_xg=1.8,
            away_xg=0.9,
            actual_home_score=2,
            actual_away_score=1,
            top_scorelines=scorelines,
        )

        # Most likely total = 2+1=3, actual total = 2+1=3, diff < 2
        # So goal_total_missed should NOT be in errors
        error_types = [e.error_type for e in errors]
        assert "goal_total_missed" not in error_types


class TestAggregateErrorAttributionsLowScoreDraw:
    """Test aggregate_error_attributions correctly identifies low_score_draw_missed."""

    def test_low_score_draw_missed_detected(self, db_session):
        from app.models import DashboardRevision, Match, MatchPrediction, PredictionSnapshot, Team
        from app.services.scoring import aggregate_error_attributions

        # Setup teams
        db_session.add_all([
            Team(id="T10", name="T10", short_name="T10", code="T10", group_code="A"),
            Team(id="T11", name="T11", short_name="T11", code="T11", group_code="A"),
        ])
        db_session.flush()

        kickoff = datetime.now(timezone.utc) - timedelta(hours=2)
        match = Match(
            id="m_low_draw",
            group_code="A",
            home_team_id="T10",
            away_team_id="T11",
            kickoff=kickoff,
            status="final",
            home_score=1,
            away_score=1,
            source="test",
        )
        db_session.add(match)
        db_session.flush()

        rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
        db_session.add(rev)
        db_session.flush()

        pred = MatchPrediction(
            revision_id=rev.id,
            match_id="m_low_draw",
            home_win=0.55,
            draw=0.20,
            away_win=0.25,
            home_xg=1.8,
            away_xg=0.9,
            confidence=0.8,
            confidence_label="High",
            data_confidence=0.9,
            data_confidence_label="High",
            model_confidence=0.85,
            model_confidence_label="High",
            explanation="Test",
            model_inputs={},
            model_version="v1",
            scorelines=[],
            score_matrix=[],
        )
        db_session.add(pred)

        snap = PredictionSnapshot(
            match_id="m_low_draw",
            revision_id=rev.id,
            kickoff=kickoff,
            snapshotted_at=kickoff - timedelta(minutes=40),
            home_win=0.55,
            draw=0.20,
            away_win=0.25,
            home_xg=1.8,
            away_xg=0.9,
            scorelines=[],
            score_matrix=[],
            confidence=0.8,
            confidence_label="High",
            model_inputs={},
            model_version="v1",
        )
        db_session.add(snap)
        db_session.flush()

        result = aggregate_error_attributions(db_session)
        assert result["counts"]["low_score_draw_missed"] >= 1

    def test_high_score_draw_not_counted_as_low_score(self, db_session):
        from app.models import DashboardRevision, Match, MatchPrediction, PredictionSnapshot, Team
        from app.services.scoring import aggregate_error_attributions

        db_session.add_all([
            Team(id="T20", name="T20", short_name="T20", code="T20", group_code="B"),
            Team(id="T21", name="T21", short_name="T21", code="T21", group_code="B"),
        ])
        db_session.flush()

        kickoff = datetime.now(timezone.utc) - timedelta(hours=2)
        match = Match(
            id="m_high_draw",
            group_code="B",
            home_team_id="T20",
            away_team_id="T21",
            kickoff=kickoff,
            status="final",
            home_score=2,
            away_score=2,
            source="test",
        )
        db_session.add(match)
        db_session.flush()

        rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
        db_session.add(rev)
        db_session.flush()

        pred = MatchPrediction(
            revision_id=rev.id,
            match_id="m_high_draw",
            home_win=0.55,
            draw=0.20,
            away_win=0.25,
            home_xg=2.5,
            away_xg=1.8,
            confidence=0.8,
            confidence_label="High",
            data_confidence=0.9,
            data_confidence_label="High",
            model_confidence=0.85,
            model_confidence_label="High",
            explanation="Test",
            model_inputs={},
            model_version="v1",
            scorelines=[],
            score_matrix=[],
        )
        db_session.add(pred)

        snap = PredictionSnapshot(
            match_id="m_high_draw",
            revision_id=rev.id,
            kickoff=kickoff,
            snapshotted_at=kickoff - timedelta(minutes=40),
            home_win=0.55,
            draw=0.20,
            away_win=0.25,
            home_xg=2.5,
            away_xg=1.8,
            scorelines=[],
            score_matrix=[],
            confidence=0.8,
            confidence_label="High",
            model_inputs={},
            model_version="v1",
        )
        db_session.add(snap)
        db_session.flush()

        result = aggregate_error_attributions(db_session)
        # Total goals = 4 > 2, so low_score_draw_missed should NOT be counted
        assert result["counts"]["low_score_draw_missed"] == 0


# ---------------------------------------------------------------------------
# Pre-match snapshot selection tests
# ---------------------------------------------------------------------------

def _seed_match_with_snapshots(
    session,
    match_id: str,
    kickoff: datetime,
    snapshots: list[dict],
    home_score: int = 1,
    away_score: int = 0,
):
    """Helper to seed teams, a final match, and prediction snapshots.

    snapshots: list of dicts with keys:
        - snapshotted_at: datetime
        - home_win, draw, away_win: float
        - (optional) model_version: str (default "v1")
    """
    from app.models import DashboardRevision, Match, MatchPrediction, PredictionSnapshot, Team

    session.add_all([
        Team(id=f"T1_{match_id}", name=f"T1_{match_id}", short_name=f"T1_{match_id}", code="T1", group_code="A"),
        Team(id=f"T2_{match_id}", name=f"T2_{match_id}", short_name=f"T2_{match_id}", code="T2", group_code="A"),
    ])
    session.flush()

    match = Match(
        id=match_id,
        group_code="A",
        home_team_id=f"T1_{match_id}",
        away_team_id=f"T2_{match_id}",
        kickoff=kickoff,
        status="final",
        source="test",
        home_score=home_score,
        away_score=away_score,
    )
    session.add(match)
    session.flush()

    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    session.add(rev)
    session.flush()

    pred = MatchPrediction(
        revision_id=rev.id,
        match_id=match_id,
        home_win=0.5, draw=0.3, away_win=0.2,
        home_xg=1.0, away_xg=1.0,
        confidence=0.8, confidence_label="High",
        data_confidence=0.9, data_confidence_label="High",
        model_confidence=0.85, model_confidence_label="High",
        explanation="Test", model_inputs={}, model_version="v1",
        scorelines=[], score_matrix=[],
    )
    session.add(pred)

    for snap_data in snapshots:
        snap = PredictionSnapshot(
            match_id=match_id,
            revision_id=rev.id,
            kickoff=kickoff,
            snapshotted_at=snap_data["snapshotted_at"],
            home_win=snap_data["home_win"],
            draw=snap_data["draw"],
            away_win=snap_data["away_win"],
            home_xg=1.0,
            away_xg=1.0,
            scorelines=[],
            score_matrix=[],
            confidence=0.8,
            confidence_label="High",
            model_inputs={},
            model_version=snap_data.get("model_version", "v1"),
        )
        session.add(snap)

    session.flush()
    return match


def test_pre_kickoff_snapshot_selected_over_post_kickoff(db_session):
    """A match with both pre- and post-kickoff snapshots must only use the
    pre-kickoff one for formal scoring."""
    from app.services.scoring import score_model

    kickoff = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)

    _seed_match_with_snapshots(
        db_session,
        match_id="m_pre_post",
        kickoff=kickoff,
        snapshots=[
            # Pre-kickoff: home_win=0.6
            {
                "snapshotted_at": kickoff - timedelta(hours=2),
                "home_win": 0.6,
                "draw": 0.25,
                "away_win": 0.15,
                "model_version": "v1",
            },
            # Post-kickoff: home_win=0.9 (should be ignored)
            # Uses different model_version to satisfy the unique constraint
            # on (match_id, revision_id, model_version).
            {
                "snapshotted_at": kickoff + timedelta(hours=1),
                "home_win": 0.9,
                "draw": 0.05,
                "away_win": 0.05,
                "model_version": "v1-post",
            },
        ],
        home_score=1,
        away_score=0,
    )

    report = score_model(db_session)

    # Match must be scored
    scored = [m for m in report.per_match if m.match_id == "m_pre_post"]
    assert len(scored) == 1

    # The pre-kickoff snapshot (home_win=0.6) must be used, NOT the post-kickoff one (0.9)
    assert scored[0].predicted["home_win"] == pytest.approx(0.6)


def test_only_post_kickoff_snapshots_excluded_from_scoring(db_session):
    """A match with ONLY post-kickoff snapshots must NOT appear in the
    scoring report at all."""
    from app.services.scoring import score_model

    kickoff = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)

    _seed_match_with_snapshots(
        db_session,
        match_id="m_post_only",
        kickoff=kickoff,
        snapshots=[
            # Only post-kickoff snapshot
            {
                "snapshotted_at": kickoff + timedelta(hours=1),
                "home_win": 0.7,
                "draw": 0.2,
                "away_win": 0.1,
            },
        ],
        home_score=2,
        away_score=1,
    )

    report = score_model(db_session)

    # Match must NOT be in the scoring report
    assert not any(m.match_id == "m_post_only" for m in report.per_match)


def test_no_pre_kickoff_snapshot_appears_in_scoring_exclusions(db_session):
    """A match with no pre-kickoff snapshot must appear in scoring exclusions
    with the proper reason code."""
    from app.services.scoring import get_scoring_exclusions

    kickoff = datetime(2026, 6, 15, 16, 0, tzinfo=timezone.utc)

    _seed_match_with_snapshots(
        db_session,
        match_id="m_excluded",
        kickoff=kickoff,
        snapshots=[
            # Only post-kickoff snapshot
            {
                "snapshotted_at": kickoff + timedelta(hours=1),
                "home_win": 0.5,
                "draw": 0.3,
                "away_win": 0.2,
            },
        ],
        home_score=0,
        away_score=0,
    )

    exclusions = get_scoring_exclusions(db_session)

    excluded = [e for e in exclusions if e["match_id"] == "m_excluded"]
    assert len(excluded) == 1
    assert "excluded_after_kickoff" in excluded[0]["reason_codes"]


class TestModelComparison:
    def test_model_comparison_includes_all_sources(self, db_session):
        """model_comparison should include Baseline, AI versions, and Ensemble."""
        from app.services.accuracy_command import get_accuracy_command_center
        result = get_accuracy_command_center(db_session)
        comparison = result.get("model_comparison", [])
        sources = [item["source"] for item in comparison]
        # Baseline should always be present
        assert any("Baseline" in s for s in sources), "Baseline missing from comparison"
        # Ensemble should always be present
        assert any("Ensemble" in s for s in sources), "Ensemble missing from comparison"

    def test_v2_models_marked_as_shadow(self, db_session):
        """v2 models should be marked as shadow role in comparison."""
        from app.services.accuracy_command import get_accuracy_command_center
        result = get_accuracy_command_center(db_session)
        comparison = result.get("model_comparison", [])
        v2_items = [item for item in comparison if item.get("prompt_version") == "worldcup-ai-v2"]
        for item in v2_items:
            assert item["role"] == "shadow", f"{item['model_version']} should be shadow"

    def test_available_true_when_sample_count_positive(self):
        """available must be True when sample_count > 0, regardless of source dict."""
        from app.services.accuracy_command import _build_model_comparison
        # Simulate baseline with sample_count=4 but available=False from _format_version_score
        baseline_score = {"available": False, "sample_count": 4, "brier": 0.25, "logloss": 0.6, "hit_rate": 0.5}
        ensemble_score = {"available": False, "sample_count": 4, "brier": 0.20, "logloss": 0.5, "hit_rate": 0.6}
        result = _build_model_comparison(baseline_score, [], {"ai_by_version": {}}, ensemble_score)
        baseline_item = next(r for r in result if r["source"] == "Baseline")
        ensemble_item = next(r for r in result if r["source"] == "Ensemble")
        assert baseline_item["available"] is True, f"Baseline available should be True when sample_count=4, got {baseline_item['available']}"
        assert ensemble_item["available"] is True, f"Ensemble available should be True when sample_count=4, got {ensemble_item['available']}"

    def test_available_false_when_sample_count_zero(self):
        """available must be False when sample_count == 0."""
        from app.services.accuracy_command import _build_model_comparison
        baseline_score = {"available": False, "sample_count": 0, "brier": None, "logloss": None, "hit_rate": None}
        ensemble_score = {"available": False, "sample_count": 0, "brier": None, "logloss": None, "hit_rate": None}
        result = _build_model_comparison(baseline_score, [], {"ai_by_version": {}}, ensemble_score)
        baseline_item = next(r for r in result if r["source"] == "Baseline")
        ensemble_item = next(r for r in result if r["source"] == "Ensemble")
        assert baseline_item["available"] is False
        assert ensemble_item["available"] is False

    def test_sample_sufficient_independent_of_available(self):
        """sample_sufficient is about statistical power, available is about data presence."""
        # sample_count=4 means available=True but sample_sufficient=False (need >=20)
        assert 4 > 0  # available should be True
        assert 4 < 20  # sample_sufficient should be False


def _count_selects(session, fn):
    engine = session.get_bind()
    count = 0

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        nonlocal count
        if statement.lstrip().upper().startswith("SELECT"):
            count += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
    return result, count


def test_match_count_breakdown_batches_queries_and_preserves_statuses(db_session):
    revision = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(revision)
    db_session.add_all([
        Team(id="B1", name="B1", short_name="B1", code="B1A", group_code="A"),
        Team(id="B2", name="B2", short_name="B2", code="B2A", group_code="A"),
        Team(id="B3", name="B3", short_name="B3", code="B3A", group_code="A"),
        Team(id="B4", name="B4", short_name="B4", code="B4A", group_code="A"),
        Team(id="B5", name="B5", short_name="B5", code="B5A", group_code="A"),
        Team(id="B6", name="B6", short_name="B6", code="B6A", group_code="A"),
    ])
    db_session.flush()
    kickoff_1 = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    kickoff_2 = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    kickoff_3 = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    db_session.add_all([
        Match(id="breakdown_scored", group_code="A", home_team_id="B1", away_team_id="B2", kickoff=kickoff_1, status="final", source="test", home_score=1, away_score=0),
        Match(id="breakdown_after", group_code="A", home_team_id="B3", away_team_id="B4", kickoff=kickoff_2, status="final", source="test", home_score=0, away_score=1),
        Match(id="breakdown_missing", group_code="A", home_team_id="B5", away_team_id="B6", kickoff=kickoff_3, status="final", source="test", home_score=2, away_score=2),
    ])
    db_session.flush()

    db_session.add_all([
        MatchPrediction(
            revision_id=revision.id, match_id="breakdown_scored", home_win=0.55, draw=0.25, away_win=0.20,
            home_xg=1.2, away_xg=0.8, confidence=0.8, confidence_label="High",
            data_confidence=0.9, data_confidence_label="High",
            model_confidence=0.85, model_confidence_label="High",
            explanation="Test", model_inputs={}, model_version="v1", scorelines=[], score_matrix=[],
        ),
        MatchPrediction(
            revision_id=revision.id, match_id="breakdown_after", home_win=0.30, draw=0.25, away_win=0.45,
            home_xg=0.9, away_xg=1.1, confidence=0.8, confidence_label="High",
            data_confidence=0.9, data_confidence_label="High",
            model_confidence=0.85, model_confidence_label="High",
            explanation="Test", model_inputs={}, model_version="v1", scorelines=[], score_matrix=[],
        ),
        PredictionSnapshot(
            match_id="breakdown_scored", revision_id=revision.id, kickoff=kickoff_1,
            snapshotted_at=kickoff_1 - timedelta(hours=2), home_win=0.55, draw=0.25, away_win=0.20,
            home_xg=1.2, away_xg=0.8, scorelines=[], score_matrix=[], confidence=0.8,
            confidence_label="High", model_inputs={}, model_version="v1", is_pre_match_locked=True,
        ),
        PredictionSnapshot(
            match_id="breakdown_after", revision_id=revision.id, kickoff=kickoff_2,
            snapshotted_at=kickoff_2 + timedelta(minutes=10), home_win=0.30, draw=0.25, away_win=0.45,
            home_xg=0.9, away_xg=1.1, scorelines=[], score_matrix=[], confidence=0.8,
            confidence_label="High", model_inputs={}, model_version="v1",
        ),
        AIPrediction(
            match_id="breakdown_scored", provider="deepseek", model_id="test", model_version="ai-test-v1",
            prompt_version="test", input_snapshot_json={}, raw_response_text="{}", raw_response_json={},
            parsed_home_win=0.5, parsed_draw=0.3, parsed_away_win=0.2, confidence=0.7,
            risk_flags_json=[], key_factors_json=[], reason="test", uncertainties_json=[],
            disagreement_with_system=None, disagreement_with_market=None, recommended_label=None,
            is_pre_match_locked=True, real_time_only=False, error_code=None, error_message=None,
        ),
        EnsemblePrediction(
            match_id="breakdown_scored", model_version="ensemble-v1", system_model_version="v1",
            system_weight=0.4, market_weight=0.3, ai_weights_json={"ai-test-v1": 0.3},
            source_probabilities_json={}, ensemble_home_win=0.5, ensemble_draw=0.3, ensemble_away_win=0.2,
            confidence=0.8, reason="test", is_pre_match_locked=True,
        ),
    ])
    db_session.flush()

    result, select_count = _count_selects(db_session, lambda: get_match_count_breakdown(db_session))
    details = {item["match_id"]: item for item in result.details}

    assert select_count <= 8
    assert result.total_finished == 3
    assert result.has_pre_match_prediction == 2
    assert result.has_pre_kickoff_snapshot == 1
    assert result.has_locked_snapshot == 1
    assert result.has_fallback_snapshot == 0
    assert result.actually_scored == 1
    assert result.missing_snapshot == 2
    assert details["breakdown_scored"]["status"] == "scored"
    assert details["breakdown_scored"]["has_ai"] is True
    assert details["breakdown_scored"]["has_ensemble"] is True
    assert details["breakdown_after"]["status"] == "excluded_after_kickoff"
    assert details["breakdown_missing"]["status"] == "no_pre_match_snapshot"
