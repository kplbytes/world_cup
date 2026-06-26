"""Tests for P2 accuracy improvement features.

Covers:
- Error attribution
- Model config loading
- Calibration analysis
- Market comparison
- Model recommendation
- Data quality check
- Post-match report generation
"""

import math
from datetime import datetime, timezone
from typing import Any

import pytest

from app.models import Match, PredictionSnapshot


# ============================================================================
# Error Attribution Tests
# ============================================================================

class TestErrorAttribution:
    def test_favorite_overestimated(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.60, draw_prob=0.25, away_win_prob=0.15,
            actual_result="away", home_xg=1.5, away_xg=0.8,
            actual_home_score=0, actual_away_score=1,
        )
        error_types = [e.error_type for e in errors]
        assert "favorite_overestimated" in error_types

    def test_draw_underestimated(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.50, draw_prob=0.20, away_win_prob=0.30,
            actual_result="draw", home_xg=1.5, away_xg=0.8,
            actual_home_score=1, actual_away_score=1,
        )
        error_types = [e.error_type for e in errors]
        assert "draw_underestimated" in error_types

    def test_underdog_underestimated(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.65, draw_prob=0.20, away_win_prob=0.15,
            actual_result="away", home_xg=1.5, away_xg=0.8,
            actual_home_score=0, actual_away_score=2,
        )
        error_types = [e.error_type for e in errors]
        assert "underdog_underestimated" in error_types

    def test_overconfident_wrong(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.70, draw_prob=0.15, away_win_prob=0.15,
            actual_result="away", home_xg=2.0, away_xg=0.5,
            actual_home_score=0, actual_away_score=1,
        )
        error_types = [e.error_type for e in errors]
        assert "overconfident_wrong" in error_types

    def test_low_confidence_match(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.35, draw_prob=0.33, away_win_prob=0.32,
            actual_result="home", home_xg=1.2, away_xg=1.1,
            actual_home_score=1, actual_away_score=0,
        )
        error_types = [e.error_type for e in errors]
        assert "low_confidence_match" in error_types

    def test_correct_prediction(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.55, draw_prob=0.25, away_win_prob=0.20,
            actual_result="home", home_xg=1.5, away_xg=0.8,
            actual_home_score=2, actual_away_score=1,
        )
        error_types = [e.error_type for e in errors]
        assert "correct" in error_types

    def test_market_disagreed_model_wrong(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.60, draw_prob=0.20, away_win_prob=0.20,
            actual_result="away", home_xg=1.5, away_xg=0.8,
            actual_home_score=0, actual_away_score=1,
            market_home_prob=0.30, market_draw_prob=0.25, market_away_prob=0.45,
        )
        error_types = [e.error_type for e in errors]
        assert "market_disagreed_model_wrong" in error_types

    def test_market_disagreed_model_right(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.30, draw_prob=0.25, away_win_prob=0.45,
            actual_result="away", home_xg=0.8, away_xg=1.5,
            actual_home_score=0, actual_away_score=1,
            market_home_prob=0.55, market_draw_prob=0.25, market_away_prob=0.20,
        )
        error_types = [e.error_type for e in errors]
        assert "market_disagreed_model_right" in error_types

    def test_numerical_helped(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.70, draw_prob=0.15, away_win_prob=0.15,
            actual_result="home", home_xg=1.5, away_xg=0.8,
            actual_home_score=2, actual_away_score=0,
            base_home_win=0.50, base_draw=0.30, base_away_win=0.20,
            has_numerical_adjustments=True,
        )
        error_types = [e.error_type for e in errors]
        assert "numerical_helped" in error_types

    def test_numerical_hurt(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.40, draw_prob=0.30, away_win_prob=0.30,
            actual_result="home", home_xg=1.5, away_xg=0.8,
            actual_home_score=2, actual_away_score=0,
            base_home_win=0.60, base_draw=0.20, base_away_win=0.20,
            has_numerical_adjustments=True,
        )
        error_types = [e.error_type for e in errors]
        assert "numerical_hurt" in error_types

    def test_goal_total_missed(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.50, draw_prob=0.25, away_win_prob=0.25,
            actual_result="home", home_xg=1.5, away_xg=0.8,
            actual_home_score=4, actual_away_score=1,
            top_scorelines=[{"home_goals": 1, "away_goals": 0, "probability": 0.15}],
        )
        error_types = [e.error_type for e in errors]
        assert "goal_total_missed" in error_types

    def test_warning_helped(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.55, draw_prob=0.25, away_win_prob=0.20,
            actual_result="away", home_xg=1.5, away_xg=0.8,
            actual_home_score=0, actual_away_score=1,
            has_auto_adjustments=True,
        )
        error_types = [e.error_type for e in errors]
        assert "warning_helped" in error_types

    def test_warning_hurt(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.55, draw_prob=0.25, away_win_prob=0.20,
            actual_result="home", home_xg=1.5, away_xg=0.8,
            actual_home_score=1, actual_away_score=0,
            has_auto_adjustments=True,
        )
        error_types = [e.error_type for e in errors]
        assert "warning_hurt" in error_types

    def test_market_agreed_but_wrong(self):
        from app.services.error_attribution import classify_error
        errors = classify_error(
            home_win_prob=0.55, draw_prob=0.25, away_win_prob=0.20,
            actual_result="away", home_xg=1.5, away_xg=0.8,
            actual_home_score=0, actual_away_score=1,
            market_home_prob=0.50, market_draw_prob=0.25, market_away_prob=0.25,
        )
        error_types = [e.error_type for e in errors]
        assert "market_agreed_but_wrong" in error_types


# ============================================================================
# Model Config Tests
# ============================================================================

class TestModelConfig:
    def test_load_configs(self):
        from app.model_configs.model_config_loader import load_configs
        configs = load_configs()
        assert "elo-poisson-v1" in configs
        assert "elo-poisson-v1-drawboost" in configs
        assert configs["elo-poisson-v1-drawboost"].draw_boost == 1.10

    def test_get_config(self):
        from app.model_configs.model_config_loader import get_config
        config = get_config("elo-poisson-v1-drawboost")
        assert config.draw_boost == 1.10
        assert config.name == "elo-poisson-v1-drawboost"

    def test_get_config_unknown_returns_default(self):
        from app.model_configs.model_config_loader import get_config
        config = get_config("nonexistent")
        assert config.name == "nonexistent"
        assert config.draw_boost == 1.00  # default

    def test_list_configs(self):
        from app.model_configs.model_config_loader import list_configs
        configs = list_configs()
        assert len(configs) >= 6
        names = [c["name"] for c in configs]
        assert "elo-poisson-v1" in names
        assert "elo-poisson-v2-calibrated" in names

    def test_v2_calibrated_params(self):
        from app.model_configs.model_config_loader import get_config
        config = get_config("elo-poisson-v2-calibrated")
        assert config.draw_boost == 1.08
        assert config.favorite_dampening == 0.05
        assert config.underdog_boost == 0.02
        assert config.market_blend_weight == 0.10


# ============================================================================
# Calibration Tests
# ============================================================================

class TestCalibration:
    def test_calibration_empty_db(self, db_session):
        from app.services.calibration import compute_calibration
        result = compute_calibration(db_session)
        assert result == []

    def test_calibration_with_data(self, db_session):
        from app.models import Team, DashboardRevision
        from app.services.calibration import compute_calibration

        # Setup
        db_session.add_all([
            Team(id="T1", name="T1", short_name="T1", code="T1", group_code="A"),
            Team(id="T2", name="T2", short_name="T2", code="T2", group_code="A"),
        ])
        db_session.flush()

        rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
        db_session.add(rev)
        db_session.flush()

        kickoff = datetime(2026, 6, 12, 12, tzinfo=timezone.utc)
        match = Match(id="m_cal_1", group_code="A", home_team_id="T1", away_team_id="T2",
                     kickoff=kickoff, status="final", home_score=1, away_score=0, source="test")
        db_session.add(match)

        snap = PredictionSnapshot(
            match_id="m_cal_1", revision_id=rev.id, kickoff=kickoff,
            is_pre_match_locked=True,
            home_win=0.55, draw=0.25, away_win=0.20,
            home_xg=1.5, away_xg=0.8,
            scorelines=[], score_matrix=[], confidence=0.8, confidence_label="High",
            model_inputs={}, model_version="v1",
            snapshotted_at=datetime(2026, 6, 12, 11, tzinfo=timezone.utc),
        )
        db_session.add(snap)
        db_session.flush()

        result = compute_calibration(db_session)
        assert len(result) > 0
        # The 55% prediction should be in the 50-60% bucket
        bucket_50_60 = next((b for b in result if b["label"] == "50-60%"), None)
        assert bucket_50_60 is not None
        assert bucket_50_60["sample_count"] == 1


# ============================================================================
# Market Comparison Tests
# ============================================================================

class TestMarketComparison:
    def test_market_comparison_no_data(self, db_session):
        from app.services.market_comparison import compute_market_comparison
        result = compute_market_comparison(db_session)
        assert result["market_sample_count"] == 0

    def test_market_comparison_with_data(self, db_session):
        from app.models import Team, DashboardRevision, MarketSnapshot

        db_session.add_all([
            Team(id="T1", name="T1", short_name="T1", code="T1", group_code="A"),
            Team(id="T2", name="T2", short_name="T2", code="T2", group_code="A"),
        ])
        db_session.flush()

        rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
        db_session.add(rev)
        db_session.flush()

        kickoff = datetime(2026, 6, 12, 12, tzinfo=timezone.utc)
        match = Match(id="m_mkt_1", group_code="A", home_team_id="T1", away_team_id="T2",
                     kickoff=kickoff, status="final", home_score=1, away_score=0, source="test")
        db_session.add(match)
        db_session.flush()

        snap = PredictionSnapshot(
            match_id="m_mkt_1", revision_id=rev.id, kickoff=kickoff,
            is_pre_match_locked=True,
            home_win=0.55, draw=0.25, away_win=0.20,
            home_xg=1.5, away_xg=0.8,
            scorelines=[], score_matrix=[], confidence=0.8, confidence_label="High",
            model_inputs={}, model_version="v1",
            snapshotted_at=datetime(2026, 6, 12, 11, tzinfo=timezone.utc),
        )
        db_session.add(snap)
        db_session.flush()

        market = MarketSnapshot(
            match_id="m_mkt_1", provider="sporttery",
            home_probability=0.50, draw_probability=0.28, away_probability=0.22,
            raw_overround=1.06,
        )
        db_session.add(market)
        db_session.flush()

        from app.services.market_comparison import compute_market_comparison
        result = compute_market_comparison(db_session)
        assert result["market_sample_count"] == 1
        assert "model_brier" in result
        assert "market_brier" in result
        assert "blended_brier" in result


# ============================================================================
# Model Recommendation Tests
# ============================================================================

class TestModelRecommendation:
    def test_recommendation_no_data(self, db_session):
        from app.services.model_recommendation import get_model_recommendation
        result = get_model_recommendation(db_session)
        assert result["recommended_model_version"] == "elo-poisson-v1"
        assert result["confidence"] == "low"

    def test_recommendation_with_data(self, db_session):
        from app.models import Team, DashboardRevision

        db_session.add_all([
            Team(id="T1", name="T1", short_name="T1", code="T1", group_code="A"),
            Team(id="T2", name="T2", short_name="T2", code="T2", group_code="A"),
        ])
        db_session.flush()

        rev = DashboardRevision(active=True, model_version="elo-poisson-v1", simulation_iterations=1, simulation_seed=1)
        db_session.add(rev)
        db_session.flush()

        # Create 6 scored matches to meet min_samples
        for i in range(6):
            kickoff = datetime(2026, 6, 10 + i, 12, tzinfo=timezone.utc)
            match = Match(id=f"m_rec_{i}", group_code="A", home_team_id="T1", away_team_id="T2",
                         kickoff=kickoff, status="final", home_score=1, away_score=0, source="test")
            db_session.add(match)
            snap = PredictionSnapshot(
                match_id=f"m_rec_{i}", revision_id=rev.id, kickoff=kickoff,
                is_pre_match_locked=True,
                home_win=0.55, draw=0.25, away_win=0.20,
                home_xg=1.5, away_xg=0.8,
                scorelines=[], score_matrix=[], confidence=0.8, confidence_label="High",
                model_inputs={}, model_version="elo-poisson-v1",
                snapshotted_at=datetime(2026, 6, 10 + i, 11, tzinfo=timezone.utc),
            )
            db_session.add(snap)

        db_session.flush()

        from app.services.model_recommendation import get_model_recommendation
        result = get_model_recommendation(db_session)
        assert "recommended_model_version" in result
        assert "reason" in result
        assert "fallback_model_version" in result

    def test_recommendation_can_reuse_precomputed_version_scores(self, db_session, monkeypatch):
        from app.services import model_recommendation

        def _should_not_be_called(_session):
            raise AssertionError("model_score_by_version should not run when version_scores are provided")

        monkeypatch.setattr(model_recommendation, "model_score_by_version", _should_not_be_called)

        result = model_recommendation.get_model_recommendation(
            db_session,
            version_scores=[
                {
                    "model_version": "elo-poisson-v1",
                    "sample_count": 8,
                    "brier": 0.25,
                    "logloss": 0.6,
                    "overconfident_wrong_count": 1,
                }
            ],
        )

        assert result["recommended_model_version"] == "elo-poisson-v1"


# ============================================================================
# Data Quality Tests
# ============================================================================

class TestDataQuality:
    def test_data_quality_clean_db(self, db_session):
        from app.services.data_quality import check_data_quality
        result = check_data_quality(db_session)
        assert "checks" in result
        assert "summary" in result
        # Clean DB should have pass for most checks
        assert result["summary"]["total_checks"] >= 8

    def test_data_quality_duplicate_match(self, db_session):
        """This tests the check itself works, not that we can actually create duplicates (PK prevents it)."""
        from app.services.data_quality import check_data_quality
        result = check_data_quality(db_session)
        # The duplicate check should run without error
        dup_check = next((c for c in result["checks"] if c["check"] == "duplicate_match_id"), None)
        assert dup_check is not None
        assert dup_check["status"] == "pass"


# ============================================================================
# Poisson Config-Driven Prediction Tests
# ============================================================================

class TestConfigDrivenPrediction:
    def test_predict_with_draw_boost(self):
        from app.prediction.poisson import predict_match, MatchContext
        from app.model_configs.model_config_loader import get_config

        ctx = MatchContext(
            data_freshness=0.9, ranking_coverage=1.0, history_coverage=0.65,
            provider_agreement=0.9, home_name="A", away_name="B",
        )
        base = predict_match(0.7, 0.3, ctx)
        boosted = predict_match(0.7, 0.3, ctx, config=get_config("elo-poisson-v1-drawboost"))

        # Draw boost should increase draw probability
        assert boosted.draw > base.draw

    def test_predict_with_favorite_dampening(self):
        from app.prediction.poisson import predict_match, MatchContext
        from app.model_configs.model_config_loader import get_config

        ctx = MatchContext(
            data_freshness=0.9, ranking_coverage=1.0, history_coverage=0.65,
            provider_agreement=0.9, home_name="A", away_name="B",
        )
        base = predict_match(0.7, 0.3, ctx)
        dampened = predict_match(0.7, 0.3, ctx, config=get_config("elo-poisson-v1-favorite-dampened"))

        # Favorite dampening should reduce max prob
        assert max(dampened.home_win, dampened.away_win) < max(base.home_win, base.away_win)

    def test_predict_with_upset_boost(self):
        from app.prediction.poisson import predict_match, MatchContext
        from app.model_configs.model_config_loader import get_config

        ctx = MatchContext(
            data_freshness=0.9, ranking_coverage=1.0, history_coverage=0.65,
            provider_agreement=0.9, home_name="A", away_name="B",
        )
        base = predict_match(0.7, 0.3, ctx)
        upset = predict_match(0.7, 0.3, ctx, config=get_config("elo-poisson-v1-upset"))

        # Underdog prob should increase
        assert min(upset.home_win, upset.away_win) > min(base.home_win, base.away_win)

    def test_blend_with_market(self):
        from app.prediction.poisson import blend_with_market

        model = {"home_win": 0.60, "draw": 0.25, "away_win": 0.15}
        market = {"home_win": 0.45, "draw": 0.30, "away_win": 0.25}

        blended = blend_with_market(model, market, 0.20)
        assert abs(sum(blended.values()) - 1.0) < 0.001
        assert blended["home_win"] < model["home_win"]  # Pulled toward market
        assert blended["away_win"] > model["away_win"]

    def test_default_config_same_as_no_config(self):
        from app.prediction.poisson import predict_match, MatchContext

        ctx = MatchContext(
            data_freshness=0.9, ranking_coverage=1.0, history_coverage=0.65,
            provider_agreement=0.9, home_name="A", away_name="B",
        )
        from app.model_configs.model_config_loader import get_config
        default_config = get_config("elo-poisson-v1")

        base = predict_match(0.7, 0.3, ctx)
        with_config = predict_match(0.7, 0.3, ctx, config=default_config)

        # Default config should produce same results as no config
        assert abs(base.home_win - with_config.home_win) < 0.001
        assert abs(base.draw - with_config.draw) < 0.001
        assert abs(base.away_win - with_config.away_win) < 0.001


# ============================================================================
# Scoring Integration Tests
# ============================================================================

class TestScoringIntegration:
    def test_score_with_error_attribution(self):
        from app.services.scoring import score_predictions
        from dataclasses import dataclass

        @dataclass
        class StubSnap:
            home_win: float = 0.5
            draw: float = 0.3
            away_win: float = 0.2
            home_xg: float = 1.5
            away_xg: float = 1.0
            scorelines: list = None
            has_auto_adjustments: bool = False
            base_home_win: float = None
            base_draw: float = None
            base_away_win: float = None
            model_version: str = "elo-poisson-v1"
            confidence_label: str = "High"
            snapshotted_at: datetime = datetime(2026, 6, 12, 12, tzinfo=timezone.utc)

            def __post_init__(self):
                if self.scorelines is None:
                    self.scorelines = [{"home_goals": 1, "away_goals": 0, "probability": 0.15}]

        @dataclass
        class StubMatch:
            id: str = "m1"
            home_team_id: str = "T1"
            away_team_id: str = "T2"
            home_score: int = 0
            away_score: int = 1
            status: str = "final"
            kickoff: datetime = datetime(2026, 6, 12, 12, tzinfo=timezone.utc)

        snap = StubSnap(home_win=0.60, draw=0.25, away_win=0.15)
        match = StubMatch()
        report = score_predictions([(snap, match)])

        assert report.matches_scored == 1
        detail = report.per_match[0]
        assert not detail.outcome_correct  # predicted home, actual away
        assert len(detail.error_types) > 0  # Should have error attribution
        assert "favorite_overestimated" in detail.error_types or "overconfident_wrong" in detail.error_types

    def test_model_score_by_version(self, db_session):
        from app.models import Team, DashboardRevision
        from app.services.scoring import model_score_by_version

        db_session.add_all([
            Team(id="T1", name="T1", short_name="T1", code="T1", group_code="A"),
            Team(id="T2", name="T2", short_name="T2", code="T2", group_code="A"),
        ])
        db_session.flush()

        rev = DashboardRevision(active=True, model_version="elo-poisson-v1", simulation_iterations=1, simulation_seed=1)
        db_session.add(rev)
        db_session.flush()

        kickoff = datetime(2026, 6, 12, 12, tzinfo=timezone.utc)
        match = Match(id="m_ver_1", group_code="A", home_team_id="T1", away_team_id="T2",
                     kickoff=kickoff, status="final", home_score=1, away_score=0, source="test")
        db_session.add(match)

        snap = PredictionSnapshot(
            match_id="m_ver_1", revision_id=rev.id, kickoff=kickoff,
            is_pre_match_locked=True,
            home_win=0.55, draw=0.25, away_win=0.20,
            home_xg=1.5, away_xg=0.8,
            scorelines=[], score_matrix=[], confidence=0.8, confidence_label="High",
            model_inputs={}, model_version="elo-poisson-v1",
            snapshotted_at=datetime(2026, 6, 12, 11, tzinfo=timezone.utc),
        )
        db_session.add(snap)
        db_session.flush()

        result = model_score_by_version(db_session)
        assert len(result) >= 1
        assert result[0]["model_version"] == "elo-poisson-v1"
        assert result[0]["sample_count"] == 1
        assert "brier" in result[0]
        assert "hit_rate" in result[0]

    def test_model_score_details(self, db_session):
        from app.models import Team, DashboardRevision
        from app.services.scoring import model_score_details

        db_session.add_all([
            Team(id="T1", name="T1", short_name="T1", code="T1", group_code="A"),
            Team(id="T2", name="T2", short_name="T2", code="T2", group_code="A"),
        ])
        db_session.flush()

        rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
        db_session.add(rev)
        db_session.flush()

        kickoff = datetime(2026, 6, 12, 12, tzinfo=timezone.utc)
        match = Match(id="m_det_1", group_code="A", home_team_id="T1", away_team_id="T2",
                     kickoff=kickoff, status="final", home_score=1, away_score=0, source="test")
        db_session.add(match)

        snap = PredictionSnapshot(
            match_id="m_det_1", revision_id=rev.id, kickoff=kickoff,
            is_pre_match_locked=True,
            home_win=0.55, draw=0.25, away_win=0.20,
            home_xg=1.5, away_xg=0.8,
            scorelines=[], score_matrix=[], confidence=0.8, confidence_label="High",
            model_inputs={}, model_version="v1",
            snapshotted_at=datetime(2026, 6, 12, 11, tzinfo=timezone.utc),
        )
        db_session.add(snap)
        db_session.flush()

        result = model_score_details(db_session)
        assert len(result) >= 1
        detail = result[0]
        assert "error_types" in detail
        assert "error_reasons" in detail
        assert "suggested_fixes" in detail
        assert "actual_result" in detail
        assert "outcome_hit" in detail
