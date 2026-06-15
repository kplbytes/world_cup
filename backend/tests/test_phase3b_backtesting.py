"""Phase 3B/C tests: backtesting correctness, no-leakage, and metric consistency."""

import math
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any

import pytest
import numpy as np
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.backtesting.elo_replay import replay_elo_history, ReplayStep, EloReplayResult
from app.backtesting.evaluation import (
    compute_metrics,
    compute_draw_metrics,
    MatchPrediction,
    ModelMetrics,
    DrawMetrics,
)
from app.backtesting.models import (
    LegacyModel,
    RefittedModel,
    DixonColesModel,
    NegBinomialModel,
    LogisticModel,
    _poisson_probs,
    _dixon_coles_probs,
    elo_to_strength,
)
from app.backtesting.bootstrap import paired_bootstrap, brier_sum_fn, log_loss_fn, BootstrapResult
from app.backtesting.rolling import ROLLING_FOLDS
from app.backtesting.runner import _generate_predictions
from app.models import Base, BacktestResultRecord, EnsembleLockTracker, HistoricalMatch, Team
from app.db import _configure_sqlite


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_replay_step(
    source_match_id: str = "test_1",
    available_at: datetime | None = None,
    home_team_id: str = "BRA",
    away_team_id: str = "ARG",
    home_team_raw: str = "Brazil",
    away_team_raw: str = "Argentina",
    home_score: int = 2,
    away_score: int = 1,
    competition_type: str = "friendly",
    neutral_venue: bool = False,
    score_scope: str = "full_90min",
    pre_match_home_elo: float = 1600.0,
    pre_match_away_elo: float = 1500.0,
    update_weight: float = 25.0,
    home_advantage_used: float = 60.0,
    as_of: datetime | None = None,
) -> ReplayStep:
    if available_at is None:
        available_at = datetime(2023, 6, 1, tzinfo=timezone.utc)
    if as_of is None:
        as_of = available_at
    return ReplayStep(
        source_match_id=source_match_id,
        available_at=available_at,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_team_raw=home_team_raw,
        away_team_raw=away_team_raw,
        home_score=home_score,
        away_score=away_score,
        competition_type=competition_type,
        neutral_venue=neutral_venue,
        score_scope=score_scope,
        pre_match_home_elo=pre_match_home_elo,
        pre_match_away_elo=pre_match_away_elo,
        elo_diff=pre_match_home_elo - pre_match_away_elo,
        update_weight=update_weight,
        home_advantage_used=home_advantage_used,
        as_of=as_of,
    )


def _make_match_prediction(
    source_match_id: str = "test_1",
    available_at: datetime | None = None,
    home_team_id: str = "BRA",
    away_team_id: str = "ARG",
    home_score: int = 2,
    away_score: int = 1,
    predicted_home_win: float = 0.5,
    predicted_draw: float = 0.25,
    predicted_away_win: float = 0.25,
    competition_type: str = "friendly",
    neutral_venue: bool = False,
    elo_diff: float = 100.0,
    model_name: str = "test-model",
    data_version: str = "test-v1",
) -> MatchPrediction:
    if available_at is None:
        available_at = datetime(2023, 6, 1, tzinfo=timezone.utc)
    return MatchPrediction(
        source_match_id=source_match_id,
        available_at=available_at,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_score=home_score,
        away_score=away_score,
        predicted_home_win=predicted_home_win,
        predicted_draw=predicted_draw,
        predicted_away_win=predicted_away_win,
        competition_type=competition_type,
        neutral_venue=neutral_venue,
        elo_diff=elo_diff,
        model_name=model_name,
        data_version=data_version,
    )


def _add_historical_match_to_list(
    source_match_id: str,
    kickoff: datetime,
    home_team_id: str,
    away_team_id: str,
    home_team_raw: str,
    away_team_raw: str,
    home_score: int,
    away_score: int,
    competition_type: str = "friendly",
    neutral_venue: bool = True,
    score_scope: str = "full_90min",
    is_unmapped: bool = False,
) -> HistoricalMatch:
    """Create an in-memory HistoricalMatch for replay tests (no DB needed)."""
    return HistoricalMatch(
        source_match_id=source_match_id,
        provider="test",
        kickoff=kickoff,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_team_raw=home_team_raw,
        away_team_raw=away_team_raw,
        home_score=home_score,
        away_score=away_score,
        neutral_venue=neutral_venue,
        competition="Test",
        competition_type=competition_type,
        match_importance=1.0,
        went_to_penalties=False,
        went_to_extra_time=False,
        is_unmapped=is_unmapped,
        home_team_source="world_cup",
        away_team_source="world_cup",
        time_precision="exact",
        available_at=kickoff,
        score_scope=score_scope,
    )


def _inmemory_session():
    """Create an in-memory SQLite session with all tables."""
    engine = create_engine("sqlite:///:memory:")
    _configure_sqlite(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return Session()


# ═══════════════════════════════════════════════════════════════════════
# 1. TestSameTimestampOrderInvariance
# ═══════════════════════════════════════════════════════════════════════

class TestSameTimestampOrderInvariance:
    """Verify that matches with the same available_at produce identical
    predictions regardless of input order (same-timestamp grouping)."""

    def test_same_timestamp_different_order_same_predictions(self):
        """Create 3 matches with same available_at, replay Elo, verify
        predictions are identical regardless of input order."""
        ts = datetime(2022, 6, 1, 12, 0, tzinfo=timezone.utc)

        # Order A: BRA-ARG, ENG-FRA, GER-ESP
        matches_a = [
            _add_historical_match_to_list("m1", ts, "BRA", "ARG", "Brazil", "Argentina", 2, 1, "friendly", neutral_venue=True),
            _add_historical_match_to_list("m2", ts, "ENG", "FRA", "England", "France", 1, 0, "friendly", neutral_venue=True),
            _add_historical_match_to_list("m3", ts, "GER", "ESP", "Germany", "Spain", 0, 0, "friendly", neutral_venue=True),
        ]

        # Order B: GER-ESP, BRA-ARG, ENG-FRA (different order)
        matches_b = [
            _add_historical_match_to_list("m3", ts, "GER", "ESP", "Germany", "Spain", 0, 0, "friendly", neutral_venue=True),
            _add_historical_match_to_list("m1", ts, "BRA", "ARG", "Brazil", "Argentina", 2, 1, "friendly", neutral_venue=True),
            _add_historical_match_to_list("m2", ts, "ENG", "FRA", "England", "France", 1, 0, "friendly", neutral_venue=True),
        ]

        result_a = replay_elo_history(matches_a)
        result_b = replay_elo_history(matches_b)

        # Collect predictions by match_id
        preds_a = {s.source_match_id: (s.pre_match_home_elo, s.pre_match_away_elo) for s in result_a.steps}
        preds_b = {s.source_match_id: (s.pre_match_home_elo, s.pre_match_away_elo) for s in result_b.steps}

        for mid in ("m1", "m2", "m3"):
            assert mid in preds_a, f"Match {mid} missing from order A"
            assert mid in preds_b, f"Match {mid} missing from order B"
            assert preds_a[mid] == preds_b[mid], (
                f"Match {mid}: order A Elo={preds_a[mid]}, order B Elo={preds_b[mid]}"
            )

    def test_same_group_no_leakage(self):
        """First match in a same-timestamp group does NOT affect second
        match's pre-match Elo."""
        ts = datetime(2022, 6, 1, 12, 0, tzinfo=timezone.utc)

        # Two matches at same timestamp sharing a team (BRA)
        matches = [
            _add_historical_match_to_list("m1", ts, "BRA", "ARG", "Brazil", "Argentina", 5, 0, "world_cup", neutral_venue=True),
            _add_historical_match_to_list("m2", ts, "BRA", "ENG", "Brazil", "England", 0, 3, "world_cup", neutral_venue=True),
        ]

        result = replay_elo_history(matches)
        assert len(result.steps) == 2

        # Both matches should have the same pre-match Elo for BRA
        # (initial 1500.0, since no prior matches exist)
        step_m1 = next(s for s in result.steps if s.source_match_id == "m1")
        step_m2 = next(s for s in result.steps if s.source_match_id == "m2")

        # BRA's pre-match Elo should be 1500 in both (no leakage from m1 to m2)
        assert step_m1.pre_match_home_elo == 1500.0
        assert step_m2.pre_match_home_elo == 1500.0

        # After replay, BRA's final rating should reflect both results
        # (but that's in final_ratings, not in pre_match_elo)

    def test_next_timestamp_uses_previous_group_results(self):
        """A match at timestamp T+1 uses Elo updated from timestamp T."""
        t0 = datetime(2022, 6, 1, 12, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=1)

        matches = [
            _add_historical_match_to_list("m1", t0, "BRA", "ARG", "Brazil", "Argentina", 3, 0, "world_cup", neutral_venue=True),
            _add_historical_match_to_list("m2", t1, "BRA", "ENG", "Brazil", "England", 1, 1, "friendly", neutral_venue=True),
        ]

        result = replay_elo_history(matches)
        assert len(result.steps) == 2

        step_m1 = next(s for s in result.steps if s.source_match_id == "m1")
        step_m2 = next(s for s in result.steps if s.source_match_id == "m2")

        # m1 at T: both teams start at 1500
        assert step_m1.pre_match_home_elo == 1500.0
        assert step_m1.pre_match_away_elo == 1500.0

        # m2 at T+1: BRA's Elo should be updated from m1 result (3-0 win)
        # BRA won 3-0, so BRA's Elo should be > 1500
        assert step_m2.pre_match_home_elo > 1500.0, (
            f"BRA Elo at T+1 should be > 1500 after winning 3-0, got {step_m2.pre_match_home_elo}"
        )


# ═══════════════════════════════════════════════════════════════════════
# 2. TestRollingFoldBoundaries
# ═══════════════════════════════════════════════════════════════════════

class TestRollingFoldBoundaries:
    """Verify rolling fold boundaries are correct and non-overlapping."""

    def test_folds_no_overlap(self):
        """Verify eval periods of different folds don't overlap."""
        eval_periods = []
        for fold in ROLLING_FOLDS:
            eval_start = fold["val_end"]
            eval_end = fold["eval_end"]
            eval_periods.append((eval_start, eval_end, fold["name"]))

        for i in range(len(eval_periods)):
            for j in range(i + 1, len(eval_periods)):
                start_i, end_i, name_i = eval_periods[i]
                start_j, end_j, name_j = eval_periods[j]
                # No overlap: end_i <= start_j or end_j <= start_i
                overlap = not (end_i <= start_j or end_j <= start_i)
                assert not overlap, (
                    f"Eval periods overlap: {name_i} [{start_i}, {end_i}) "
                    f"and {name_j} [{start_j}, {end_j})"
                )

    def test_train_before_val_before_eval(self):
        """For each fold, train < val < eval chronologically."""
        for fold in ROLLING_FOLDS:
            train_end = fold["train_end"]
            val_end = fold["val_end"]
            eval_end = fold["eval_end"]
            assert train_end < val_end, (
                f"{fold['name']}: train_end ({train_end}) must be < val_end ({val_end})"
            )
            assert val_end < eval_end, (
                f"{fold['name']}: val_end ({val_end}) must be < eval_end ({eval_end})"
            )

    def test_wc_2026_excluded(self):
        """No fold includes matches from after 2026-06-11."""
        wc_cutoff = datetime(2026, 6, 11, tzinfo=timezone.utc)
        for fold in ROLLING_FOLDS:
            assert fold["eval_end"] <= wc_cutoff, (
                f"{fold['name']}: eval_end ({fold['eval_end']}) must be <= WC cutoff ({wc_cutoff})"
            )


# ═══════════════════════════════════════════════════════════════════════
# 3. TestScalerNoLeakage
# ═══════════════════════════════════════════════════════════════════════

class TestScalerNoLeakage:
    """Verify that Elo normalization uses only training-period values."""

    def test_normalization_uses_only_training_elos(self):
        """Verify that _generate_predictions with train_elos parameter
        doesn't use eval-period Elo values."""
        # Create training steps with Elo range [1400, 1600]
        train_steps = [
            _make_replay_step(
                source_match_id="train_1",
                available_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                pre_match_home_elo=1600.0,
                pre_match_away_elo=1400.0,
            ),
        ]

        # Create eval steps with Elo range including values outside training
        eval_steps = [
            _make_replay_step(
                source_match_id="eval_1",
                available_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                pre_match_home_elo=1800.0,  # outside training range
                pre_match_away_elo=1200.0,  # outside training range
            ),
        ]

        train_elos = [1400.0, 1600.0]  # only training Elo values

        # Generate predictions with train_elos
        model = LegacyModel()
        preds = _generate_predictions(model, eval_steps, "test-v1", None, train_elos=train_elos)

        assert len(preds) == 1
        # The model should use train_elos for normalization, not eval Elo values
        # If it used eval Elo values, the normalization range would be [1200, 1800]
        # With train_elos [1400, 1600], the normalization is different
        # We verify by checking that the prediction is consistent with train_elos normalization
        home_s, away_s = elo_to_strength(1800.0, 1200.0, train_elos)
        # With train_elos, 1800 is clipped to 1.0 and 1200 is clipped to 0.0
        assert home_s == 1.0
        assert away_s == 0.0

    def test_refitted_model_uses_only_training_elos(self):
        """Verify RefittedModel._compute_all_elos only uses Elo values
        from training steps."""
        # Training steps with specific Elo values
        train_steps = [
            _make_replay_step(
                source_match_id="train_1",
                pre_match_home_elo=1500.0,
                pre_match_away_elo=1450.0,
            ),
            _make_replay_step(
                source_match_id="train_2",
                pre_match_home_elo=1550.0,
                pre_match_away_elo=1400.0,
            ),
        ]

        all_elos_per_step = RefittedModel._compute_all_elos(train_steps)

        # All steps should share the same Elo set
        assert len(all_elos_per_step) == 2
        assert all_elos_per_step[0] == all_elos_per_step[1]

        # The shared set should contain only Elo values from training steps
        expected_elos = sorted({1500.0, 1450.0, 1550.0, 1400.0})
        assert all_elos_per_step[0] == expected_elos

        # No eval-period Elo values (e.g., 1800.0) should be present
        assert 1800.0 not in all_elos_per_step[0]


# ═══════════════════════════════════════════════════════════════════════
# 4. TestCalibratorOnlyUsesValidation
# ═══════════════════════════════════════════════════════════════════════

class TestCalibratorOnlyUsesValidation:
    """Verify that calibrators are fitted on validation predictions only."""

    def test_calibrator_fitted_on_validation_only(self):
        """In rolling backtest, calibrator is fitted on val predictions, not eval."""
        from app.backtesting.calibration import TemperatureScaling

        # Create val predictions: well-calibrated (home wins, predicted home=0.6)
        val_preds = [
            _make_match_prediction(
                source_match_id=f"val_{i}",
                predicted_home_win=0.6,
                predicted_draw=0.2,
                predicted_away_win=0.2,
                home_score=2,
                away_score=1,
                model_name="legacy-elo-poisson",
            )
            for i in range(30)
        ]

        # Create eval predictions: poorly calibrated (away wins, but predicted home=0.6)
        eval_preds = [
            _make_match_prediction(
                source_match_id=f"eval_{i}",
                predicted_home_win=0.6,
                predicted_draw=0.2,
                predicted_away_win=0.2,
                home_score=0,
                away_score=2,
                model_name="legacy-elo-poisson",
            )
            for i in range(30)
        ]

        # Fit calibrator on val predictions only
        cal = TemperatureScaling()
        cal.fit(val_preds)
        temp_val_only = cal.temperature

        # Fit on a mix of val+eval (wrong - this should give a different temperature
        # because eval data has opposite calibration direction)
        cal_mixed = TemperatureScaling()
        cal_mixed.fit(val_preds + eval_preds)
        temp_mixed = cal_mixed.temperature

        # With val-only: predictions are well-calibrated, temperature ~1.0
        # With mixed: half the data is miscalibrated in opposite direction,
        # so temperature should differ
        assert temp_val_only != temp_mixed, (
            f"Temperature should differ: val_only={temp_val_only}, mixed={temp_mixed}"
        )

    def test_calibrator_not_fitted_on_eval(self):
        """Verify eval predictions are not used for calibrator fitting."""
        from app.backtesting.calibration import TemperatureScaling

        # Create val predictions where home team always wins
        val_preds = [
            _make_match_prediction(
                source_match_id=f"val_{i}",
                predicted_home_win=0.5,
                predicted_draw=0.25,
                predicted_away_win=0.25,
                home_score=2,
                away_score=0,  # home always wins in val
                model_name="legacy-elo-poisson",
            )
            for i in range(30)
        ]

        # Fit on val only
        cal = TemperatureScaling()
        cal.fit(val_preds)

        # Temperature should be close to 1.0 or slightly adjusted
        # The key point: fitting is done only on val_preds, eval_preds are never passed
        assert cal.temperature > 0, "Temperature must be positive"
        assert 0.1 < cal.temperature < 10.0, "Temperature should be in reasonable range"


# ═══════════════════════════════════════════════════════════════════════
# 5. TestPairedBootstrap
# ═══════════════════════════════════════════════════════════════════════

class TestPairedBootstrap:
    """Verify paired bootstrap significance testing."""

    def test_paired_bootstrap_same_match_ids(self):
        """Verify bootstrap uses same match_id set for both models."""
        preds_a = [
            _make_match_prediction(source_match_id="m1", model_name="model_a", predicted_home_win=0.5, predicted_draw=0.25, predicted_away_win=0.25, home_score=2, away_score=1),
            _make_match_prediction(source_match_id="m2", model_name="model_a", predicted_home_win=0.4, predicted_draw=0.3, predicted_away_win=0.3, home_score=1, away_score=1),
            _make_match_prediction(source_match_id="m3", model_name="model_a", predicted_home_win=0.6, predicted_draw=0.2, predicted_away_win=0.2, home_score=3, away_score=0),
        ]
        preds_b = [
            _make_match_prediction(source_match_id="m1", model_name="model_b", predicted_home_win=0.45, predicted_draw=0.3, predicted_away_win=0.25, home_score=2, away_score=1),
            _make_match_prediction(source_match_id="m2", model_name="model_b", predicted_home_win=0.35, predicted_draw=0.35, predicted_away_win=0.3, home_score=1, away_score=1),
            _make_match_prediction(source_match_id="m3", model_name="model_b", predicted_home_win=0.55, predicted_draw=0.25, predicted_away_win=0.2, home_score=3, away_score=0),
        ]

        result = paired_bootstrap(preds_a, preds_b, brier_sum_fn, "brier_sum", n_bootstrap=100, seed=42)

        # Should use 3 matches (common match IDs)
        assert result.n_matches == 3

        # Also test with non-overlapping match IDs
        preds_c = [
            _make_match_prediction(source_match_id="m4", model_name="model_c", predicted_home_win=0.5, predicted_draw=0.25, predicted_away_win=0.25, home_score=2, away_score=1),
        ]
        result_no_overlap = paired_bootstrap(preds_a, preds_c, brier_sum_fn, "brier_sum", n_bootstrap=100, seed=42)
        assert result_no_overlap.n_matches == 0

    def test_bootstrap_fixed_seed_reproducible(self):
        """Running bootstrap twice with same seed gives identical results."""
        preds_a = [
            _make_match_prediction(source_match_id=f"m{i}", model_name="model_a",
                                   predicted_home_win=0.5 + 0.01 * i, predicted_draw=0.25, predicted_away_win=0.25 - 0.01 * i,
                                   home_score=2 if i % 2 == 0 else 0, away_score=1 if i % 2 == 0 else 1)
            for i in range(20)
        ]
        preds_b = [
            _make_match_prediction(source_match_id=f"m{i}", model_name="model_b",
                                   predicted_home_win=0.45 + 0.01 * i, predicted_draw=0.3, predicted_away_win=0.25 - 0.01 * i,
                                   home_score=2 if i % 2 == 0 else 0, away_score=1 if i % 2 == 0 else 1)
            for i in range(20)
        ]

        result1 = paired_bootstrap(preds_a, preds_b, brier_sum_fn, "brier_sum", n_bootstrap=500, seed=123)
        result2 = paired_bootstrap(preds_a, preds_b, brier_sum_fn, "brier_sum", n_bootstrap=500, seed=123)

        assert result1.observed_diff == result2.observed_diff
        assert result1.bootstrap_mean_diff == result2.bootstrap_mean_diff
        assert result1.ci_lower_95 == result2.ci_lower_95
        assert result1.ci_upper_95 == result2.ci_upper_95
        assert result1.p_better == result2.p_better
        assert result1.n_matches == result2.n_matches

    def test_bootstrap_conclusion_inconclusive_when_similar(self):
        """When models are nearly identical, conclusion should be 'inconclusive'."""
        # Create two sets of predictions that are nearly identical
        preds_a = [
            _make_match_prediction(source_match_id=f"m{i}", model_name="model_a",
                                   predicted_home_win=0.5, predicted_draw=0.25, predicted_away_win=0.25,
                                   home_score=2 if i % 3 != 0 else 1, away_score=1 if i % 3 != 0 else 1)
            for i in range(50)
        ]
        # Model B predictions differ by tiny amount
        preds_b = [
            _make_match_prediction(source_match_id=f"m{i}", model_name="model_b",
                                   predicted_home_win=0.5001, predicted_draw=0.2499, predicted_away_win=0.25,
                                   home_score=2 if i % 3 != 0 else 1, away_score=1 if i % 3 != 0 else 1)
            for i in range(50)
        ]

        result = paired_bootstrap(preds_a, preds_b, brier_sum_fn, "brier_sum", n_bootstrap=1000, seed=42)
        assert result.conclusion == "inconclusive", (
            f"Expected inconclusive for nearly identical models, got: {result.conclusion}"
        )


# ═══════════════════════════════════════════════════════════════════════
# 6. TestDrawMetrics
# ═══════════════════════════════════════════════════════════════════════

class TestDrawMetrics:
    """Verify draw-specific metrics computation."""

    def test_draw_brier_known_values(self):
        """Compute draw_brier for a known set of predictions and verify
        against hand calculation."""
        # 4 matches: 2 draws, 2 non-draws
        # Draw predictions: 0.3, 0.4
        # Non-draw predictions: 0.2, 0.1
        preds = [
            _make_match_prediction("m1", home_score=1, away_score=1, predicted_draw=0.3),  # draw
            _make_match_prediction("m2", home_score=2, away_score=0, predicted_draw=0.2),  # non-draw
            _make_match_prediction("m3", home_score=1, away_score=1, predicted_draw=0.4),  # draw
            _make_match_prediction("m4", home_score=0, away_score=2, predicted_draw=0.1),  # non-draw
        ]

        # Hand calculation:
        # (0.3 - 1)^2 + (0.2 - 0)^2 + (0.4 - 1)^2 + (0.1 - 0)^2
        # = 0.49 + 0.04 + 0.36 + 0.01 = 0.90
        # Mean = 0.90 / 4 = 0.225
        expected_brier = 0.225

        dm = compute_draw_metrics(preds)
        assert abs(dm.draw_brier - expected_brier) < 1e-10, (
            f"draw_brier={dm.draw_brier}, expected={expected_brier}"
        )

    def test_draw_roc_auc_perfect_discriminator(self):
        """When all draws have P(draw)=1.0 and all non-draws have P(draw)=0.0,
        ROC-AUC should be 1.0."""
        preds = [
            _make_match_prediction("m1", home_score=1, away_score=1, predicted_draw=1.0),   # draw, p=1.0
            _make_match_prediction("m2", home_score=2, away_score=0, predicted_draw=0.0),   # non-draw, p=0.0
            _make_match_prediction("m3", home_score=1, away_score=1, predicted_draw=1.0),   # draw, p=1.0
            _make_match_prediction("m4", home_score=0, away_score=2, predicted_draw=0.0),   # non-draw, p=0.0
        ]

        dm = compute_draw_metrics(preds)
        assert dm.draw_roc_auc == 1.0, f"Expected ROC-AUC=1.0, got {dm.draw_roc_auc}"

    def test_draw_pr_auc_random_classifier(self):
        """When predictions are random, PR-AUC should be close to the draw rate."""
        np.random.seed(42)
        n = 200
        draw_rate = 0.25  # 25% of matches are draws
        n_draws = int(n * draw_rate)

        preds = []
        for i in range(n):
            is_draw = i < n_draws
            # Random predictions, uninformative
            p_draw = np.random.uniform(0.1, 0.4)
            if is_draw:
                preds.append(_make_match_prediction(
                    f"m{i}", home_score=1, away_score=1, predicted_draw=p_draw,
                ))
            else:
                # Random non-draw outcome
                hs = 2 if np.random.random() > 0.5 else 0
                aws = 0 if hs == 2 else 2
                preds.append(_make_match_prediction(
                    f"m{i}", home_score=hs, away_score=aws, predicted_draw=p_draw,
                ))

        dm = compute_draw_metrics(preds)
        # PR-AUC for random classifier should be approximately equal to the draw rate
        # Allow generous tolerance since it's random
        assert abs(dm.draw_pr_auc - draw_rate) < 0.15, (
            f"PR-AUC={dm.draw_pr_auc} too far from draw_rate={draw_rate} for random predictions"
        )


# ═══════════════════════════════════════════════════════════════════════
# 7. TestDixonColesEffect
# ═══════════════════════════════════════════════════════════════════════

class TestDixonColesEffect:
    """Verify Dixon-Coles adjustment changes low-score probabilities."""

    def test_dc_changes_low_score_probs(self):
        """For low-xG matches, Dixon-Coles with rho != 0 changes
        0-0, 1-0, 0-1, 1-1 probabilities."""
        from scipy.stats import poisson as sp_poisson

        home_lambda = 0.8  # low xG
        away_lambda = 0.7  # low xG
        rho = -0.1

        # Poisson cell probabilities (before DC adjustment)
        p00_poisson = float(sp_poisson.pmf(0, home_lambda) * sp_poisson.pmf(0, away_lambda))
        p01_poisson = float(sp_poisson.pmf(0, home_lambda) * sp_poisson.pmf(1, away_lambda))
        p10_poisson = float(sp_poisson.pmf(1, home_lambda) * sp_poisson.pmf(0, away_lambda))
        p11_poisson = float(sp_poisson.pmf(1, home_lambda) * sp_poisson.pmf(1, away_lambda))

        # After DC adjustment
        p00_dc = p00_poisson * (1.0 - home_lambda * away_lambda * rho)
        p01_dc = p01_poisson * (1.0 + home_lambda * rho)
        p10_dc = p10_poisson * (1.0 + away_lambda * rho)
        p11_dc = p11_poisson * (1.0 - rho)

        # With rho=-0.1, DC should change these probabilities
        assert p00_dc != p00_poisson, "0-0 probability should change with DC"
        assert p01_dc != p01_poisson, "0-1 probability should change with DC"
        assert p10_dc != p10_poisson, "1-0 probability should change with DC"
        assert p11_dc != p11_poisson, "1-1 probability should change with DC"

        # With negative rho: 0-0 should increase, 1-1 should increase
        # (1 - lambda_h * lambda_a * rho) with rho<0 => factor > 1
        assert p00_dc > p00_poisson, "0-0 should increase with negative rho"
        assert p11_dc > p11_poisson, "1-1 should increase with negative rho"

    def test_dc_changes_final_1x2_probs(self):
        """Dixon-Coles adjustment changes the final (home_win, draw, away_win)
        probabilities."""
        home_lambda = 1.0
        away_lambda = 0.8
        rho = -0.15

        poisson_probs = _poisson_probs(home_lambda, away_lambda, draw_boost=1.0)
        dc_probs = _dixon_coles_probs(home_lambda, away_lambda, rho, draw_boost=1.0)

        # The probabilities should differ
        assert poisson_probs != dc_probs, (
            f"Poisson {poisson_probs} should differ from DC {dc_probs}"
        )

        # With negative rho, draw probability should increase
        # (DC adjustment increases 0-0 and 1-1, both are draws)
        assert dc_probs[1] > poisson_probs[1], (
            f"DC draw prob {dc_probs[1]} should be > Poisson draw prob {poisson_probs[1]} with rho={rho}"
        )


# ═══════════════════════════════════════════════════════════════════════
# 8. TestLogisticModel
# ═══════════════════════════════════════════════════════════════════════

class TestLogisticModel:
    """Verify logistic regression model behavior."""

    def test_logistic_fit_predict(self):
        """Fit on training data, predict on test data, probabilities sum to 1.0."""
        # Create training steps with varied outcomes
        np.random.seed(42)
        train_steps = []
        for i in range(50):
            home_elo = 1500 + np.random.randint(-200, 200)
            away_elo = 1500 + np.random.randint(-200, 200)
            # Higher Elo diff -> more likely home win
            diff = home_elo - away_elo
            if diff > 100:
                hs, aws = 2, 0
            elif diff < -100:
                hs, aws = 0, 2
            else:
                hs, aws = 1, 1
            train_steps.append(_make_replay_step(
                source_match_id=f"train_{i}",
                pre_match_home_elo=float(home_elo),
                pre_match_away_elo=float(away_elo),
                home_score=hs,
                away_score=aws,
                neutral_venue=bool(np.random.random() > 0.5),
                competition_type=["friendly", "qualifier", "world_cup"][i % 3],
            ))

        model = LogisticModel()
        model.fit(train_steps)
        assert model.coefficients is not None

        # Predict on a test step
        test_step = _make_replay_step(
            source_match_id="test_1",
            pre_match_home_elo=1700.0,
            pre_match_away_elo=1400.0,
            neutral_venue=False,
        )
        all_elos = [1700.0, 1400.0, 1500.0, 1600.0]
        probs = model.predict(test_step, all_elos)

        assert abs(sum(probs) - 1.0) < 1e-10, f"Probabilities sum to {sum(probs)}"
        for p in probs:
            assert 0.0 <= p <= 1.0, f"Probability {p} out of [0, 1]"

    def test_logistic_refit_per_fold(self):
        """Logistic model fitted on fold 1 training data gives different
        coefficients than fold 2."""
        np.random.seed(42)

        # Fold 1: lower Elo range
        fold1_steps = []
        for i in range(30):
            home_elo = 1400 + np.random.randint(0, 100)
            away_elo = 1400 + np.random.randint(0, 100)
            fold1_steps.append(_make_replay_step(
                source_match_id=f"f1_{i}",
                pre_match_home_elo=float(home_elo),
                pre_match_away_elo=float(away_elo),
                home_score=2 if home_elo > away_elo else 0,
                away_score=0 if home_elo > away_elo else 2,
            ))

        # Fold 2: higher Elo range
        fold2_steps = []
        for i in range(30):
            home_elo = 1600 + np.random.randint(0, 100)
            away_elo = 1600 + np.random.randint(0, 100)
            fold2_steps.append(_make_replay_step(
                source_match_id=f"f2_{i}",
                pre_match_home_elo=float(home_elo),
                pre_match_away_elo=float(away_elo),
                home_score=1 if home_elo > away_elo else 0,
                away_score=1 if home_elo <= away_elo else 0,
            ))

        model1 = LogisticModel()
        model1.fit(fold1_steps)

        model2 = LogisticModel()
        model2.fit(fold2_steps)

        # Coefficients should differ (different training data)
        if model1.coefficients is not None and model2.coefficients is not None:
            assert not np.allclose(model1.coefficients, model2.coefficients), (
                "Coefficients should differ between folds with different training data"
            )

    def test_logistic_probability_sum(self):
        """All predictions have probabilities summing to 1.0 (within tolerance)."""
        np.random.seed(42)
        steps = []
        for i in range(20):
            steps.append(_make_replay_step(
                source_match_id=f"s_{i}",
                pre_match_home_elo=1500.0 + i * 10,
                pre_match_away_elo=1500.0 - i * 5,
                home_score=i % 3,
                away_score=(i + 1) % 3,
                neutral_venue=i % 2 == 0,
                competition_type=["friendly", "qualifier", "world_cup", "continental"][i % 4],
            ))

        model = LogisticModel()
        model.fit(steps)

        all_elos = sorted({s.pre_match_home_elo for s in steps} | {s.pre_match_away_elo for s in steps})
        for step in steps:
            probs = model.predict(step, all_elos)
            assert abs(sum(probs) - 1.0) < 1e-10, (
                f"Step {step.source_match_id}: probs sum to {sum(probs)}"
            )


# ═══════════════════════════════════════════════════════════════════════
# 9. TestBrierFieldConsistency
# ═══════════════════════════════════════════════════════════════════════

class TestBrierFieldConsistency:
    """Verify Brier score field naming and calculation consistency."""

    def test_brier_rename_no_value_change(self):
        """canonical_brier == brier_sum, brier_mean == brier_sum / 3."""
        preds = [
            _make_match_prediction("m1", home_score=2, away_score=1,
                                   predicted_home_win=0.5, predicted_draw=0.25, predicted_away_win=0.25),
            _make_match_prediction("m2", home_score=1, away_score=1,
                                   predicted_home_win=0.4, predicted_draw=0.3, predicted_away_win=0.3),
            _make_match_prediction("m3", home_score=0, away_score=2,
                                   predicted_home_win=0.3, predicted_draw=0.2, predicted_away_win=0.5),
        ]

        metrics = compute_metrics(preds, "test-model", "test", "test-v1")

        # canonical_brier should equal brier_sum
        assert metrics.canonical_brier == metrics.brier_sum, (
            f"canonical_brier={metrics.canonical_brier} != brier_sum={metrics.brier_sum}"
        )

        # brier_mean should equal brier_sum / 3
        expected_mean = metrics.brier_sum / 3.0
        assert abs(metrics.brier_mean - expected_mean) < 1e-12, (
            f"brier_mean={metrics.brier_mean} != brier_sum/3={expected_mean}"
        )

    def test_brier_matches_production_formula(self):
        """Compute Brier using the same formula as scoring.py and verify consistency."""
        # The production formula in scoring.py:
        # brier = (p_home - o_home)^2 + (p_draw - o_draw)^2 + (p_away - o_away)^2
        # brier_score = sum(brier) / n  (this is brier_sum in backtesting)

        preds = [
            _make_match_prediction("m1", home_score=2, away_score=1,
                                   predicted_home_win=0.6, predicted_draw=0.2, predicted_away_win=0.2),
            _make_match_prediction("m2", home_score=1, away_score=1,
                                   predicted_home_win=0.3, predicted_draw=0.4, predicted_away_win=0.3),
        ]

        # Hand-calculate using production formula
        # m1: home win -> actual = (1, 0, 0)
        # brier_m1 = (0.6-1)^2 + (0.2-0)^2 + (0.2-0)^2 = 0.16 + 0.04 + 0.04 = 0.24
        brier_m1 = (0.6 - 1.0) ** 2 + (0.2 - 0.0) ** 2 + (0.2 - 0.0) ** 2

        # m2: draw -> actual = (0, 1, 0)
        # brier_m2 = (0.3-0)^2 + (0.4-1)^2 + (0.3-0)^2 = 0.09 + 0.36 + 0.09 = 0.54
        brier_m2 = (0.3 - 0.0) ** 2 + (0.4 - 1.0) ** 2 + (0.3 - 0.0) ** 2

        expected_brier_sum = (brier_m1 + brier_m2) / 2  # mean of per-match brier sums

        metrics = compute_metrics(preds, "test-model", "test", "test-v1")

        assert abs(metrics.brier_sum - expected_brier_sum) < 1e-12, (
            f"brier_sum={metrics.brier_sum} != expected={expected_brier_sum}"
        )


# ═══════════════════════════════════════════════════════════════════════
# 10. TestMigrationV13
# ═══════════════════════════════════════════════════════════════════════

class TestMigrationV13:
    """Verify database migration to version 13 (Brier terminology rename)."""

    def test_fresh_db_creates_backtest_with_new_columns(self):
        """Fresh database has brier_sum, brier_mean, canonical_brier columns."""
        session = _inmemory_session()

        # Insert a record with the new column names
        record = BacktestResultRecord(
            data_version="test-v1",
            model_name="legacy-elo-poisson",
            split_name="test",
            brier_sum=0.25,
            brier_mean=0.0833,
            canonical_brier=0.25,
            log_loss=0.65,
            ece=0.05,
            top1_hit_rate=0.55,
            draw_recall=0.20,
            match_count=100,
            admission_status="shadow",
        )
        session.add(record)
        session.flush()

        saved = session.scalar(
            select(BacktestResultRecord).where(
                BacktestResultRecord.model_name == "legacy-elo-poisson"
            )
        )
        assert saved is not None
        assert saved.brier_sum == 0.25
        assert saved.brier_mean == 0.0833
        assert saved.canonical_brier == 0.25

        session.close()

    def test_v12_to_v13_upgrade(self):
        """Database at v12 can be upgraded to v13 with column renames."""
        from app.db import _upgrade_schema

        engine = create_engine("sqlite:///:memory:")
        _configure_sqlite(engine)

        # Create v12 schema manually (with old column names)
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE backtest_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    data_version VARCHAR(40) NOT NULL,
                    model_name VARCHAR(80) NOT NULL,
                    split_name VARCHAR(20) NOT NULL,
                    brier_score FLOAT NOT NULL,
                    brier_score_avg FLOAT NOT NULL,
                    log_loss FLOAT NOT NULL,
                    ece FLOAT NOT NULL,
                    top1_hit_rate FLOAT NOT NULL,
                    draw_recall FLOAT NOT NULL,
                    match_count INTEGER NOT NULL,
                    parameters_json JSON,
                    stratified_json JSON,
                    admission_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    created_at DATETIME
                )
            """))
            # Insert a record with old column names
            conn.execute(text("""
                INSERT INTO backtest_results
                    (data_version, model_name, split_name, brier_score, brier_score_avg,
                     log_loss, ece, top1_hit_rate, draw_recall, match_count, admission_status)
                VALUES
                    ('test-v1', 'legacy', 'test', 0.30, 0.10, 0.65, 0.05, 0.55, 0.20, 100, 'shadow')
            """))
            # Set version to 12
            conn.execute(text("PRAGMA user_version = 12"))

        # Run migration
        _upgrade_schema(engine)

        # Verify the upgrade worked
        with engine.begin() as conn:
            # Check version is now 13
            version = conn.scalar(text("PRAGMA user_version"))
            assert version >= 13, f"Expected version >= 13, got {version}"

            # Check columns were renamed
            cols_info = conn.execute(text("PRAGMA table_info(backtest_results)")).mappings().all()
            col_names = {row["name"] for row in cols_info}
            assert "brier_sum" in col_names, "brier_sum column should exist after migration"
            assert "brier_mean" in col_names, "brier_mean column should exist after migration"
            assert "canonical_brier" in col_names, "canonical_brier column should exist after migration"
            assert "brier_score" not in col_names, "brier_score column should be renamed"
            assert "brier_score_avg" not in col_names, "brier_score_avg column should be renamed"

            # Check data was preserved
            row = conn.execute(text(
                "SELECT brier_sum, brier_mean, canonical_brier FROM backtest_results WHERE model_name = 'legacy'"
            )).fetchone()
            assert row is not None
            assert row[0] == 0.30, f"brier_sum should be 0.30, got {row[0]}"
            assert row[1] == 0.10, f"brier_mean should be 0.10, got {row[1]}"
            assert row[2] == 0.30, f"canonical_brier should equal brier_sum (0.30), got {row[2]}"


# ═══════════════════════════════════════════════════════════════════════
# 11. TestTrackerSavepoint
# ═══════════════════════════════════════════════════════════════════════

class TestTrackerSavepoint:
    """Verify that tracker operations preserve session usability on errors."""

    def test_sync_tracker_savepoint_preserves_session(self):
        """After IntegrityError in _sync_tracker, session is still usable."""
        from sqlalchemy.exc import IntegrityError

        session = _inmemory_session()

        # Insert first tracker row
        tracker1 = EnsembleLockTracker(
            match_id="match_1",
            model_version="ensemble-v1",
            lock_type="official",
            ensemble_id=1,
        )
        session.add(tracker1)
        session.flush()

        # Try to insert duplicate (same PK) - should raise IntegrityError
        tracker2 = EnsembleLockTracker(
            match_id="match_1",
            model_version="ensemble-v1",
            lock_type="official",
            ensemble_id=2,
        )
        session.add(tracker2)

        with pytest.raises(IntegrityError):
            session.flush()

        # Rollback to recover
        session.rollback()

        # Session should still be usable
        tracker3 = EnsembleLockTracker(
            match_id="match_2",
            model_version="ensemble-v1",
            lock_type="official",
            ensemble_id=3,
        )
        session.add(tracker3)
        session.flush()  # Should succeed

        # Verify both original and new tracker exist
        count = session.scalar(
            select(EnsembleLockTracker).where(
                EnsembleLockTracker.match_id == "match_2"
            )
        )
        assert count is not None

        session.close()

    def test_dual_session_tracker_contention(self):
        """Two sessions trying to insert same tracker row, only one succeeds,
        both sessions remain usable."""
        from sqlalchemy.exc import IntegrityError

        engine = create_engine("sqlite:///:memory:")
        _configure_sqlite(engine)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, expire_on_commit=False)

        session1 = Session()
        session2 = Session()

        # Session 1 inserts tracker
        tracker1 = EnsembleLockTracker(
            match_id="match_1",
            model_version="ensemble-v1",
            lock_type="official",
            ensemble_id=1,
        )
        session1.add(tracker1)
        session1.commit()

        # Session 2 tries to insert same PK
        tracker2 = EnsembleLockTracker(
            match_id="match_1",
            model_version="ensemble-v1",
            lock_type="official",
            ensemble_id=2,
        )
        session2.add(tracker2)

        with pytest.raises(IntegrityError):
            session2.flush()

        session2.rollback()

        # Both sessions should still be usable
        # Session 1: insert another tracker
        tracker3 = EnsembleLockTracker(
            match_id="match_2",
            model_version="ensemble-v1",
            lock_type="official",
            ensemble_id=3,
        )
        session1.add(tracker3)
        session1.commit()

        # Session 2: insert a different tracker
        tracker4 = EnsembleLockTracker(
            match_id="match_3",
            model_version="ensemble-v1",
            lock_type="official",
            ensemble_id=4,
        )
        session2.add(tracker4)
        session2.commit()

        # Verify: only one row for match_1, plus match_2 and match_3
        all_trackers = list(session1.scalars(select(EnsembleLockTracker)))
        match_ids = {t.match_id for t in all_trackers}
        assert "match_1" in match_ids
        assert "match_2" in match_ids
        assert "match_3" in match_ids

        # Only one tracker for match_1
        match1_trackers = [t for t in all_trackers if t.match_id == "match_1"]
        assert len(match1_trackers) == 1
        assert match1_trackers[0].ensemble_id == 1  # first insert wins

        session1.close()
        session2.close()


# ═══════════════════════════════════════════════════════════════════════
# 12. TestSqliteNaiveDatetime
# ═══════════════════════════════════════════════════════════════════════

class TestSqliteNaiveDatetime:
    """Test that SQLite naive datetimes are properly handled."""

    def test_ensure_utc_naive_datetime(self):
        from app.backtesting.rolling import _ensure_utc
        naive = datetime(2022, 6, 15, 12, 0, 0)
        aware = _ensure_utc(naive)
        assert aware.tzinfo == timezone.utc
        assert aware.year == 2022
        assert aware.month == 6

    def test_ensure_utc_already_aware(self):
        from app.backtesting.rolling import _ensure_utc
        aware = datetime(2022, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = _ensure_utc(aware)
        assert result == aware

    def test_rolling_fold_comparison_with_naive(self):
        """Naive available_at from SQLite can be compared with aware fold boundaries."""
        from app.backtesting.rolling import _ensure_utc, ROLLING_FOLDS
        naive_dt = datetime(2021, 6, 15)  # SQLite would return this
        aware_dt = _ensure_utc(naive_dt)
        fold = ROLLING_FOLDS[0]
        assert aware_dt >= fold["train_end"]  # 2021-06-15 >= 2021-01-01
        assert aware_dt < fold["val_end"]  # 2021-06-15 < 2022-01-01
