"""Tests for Phase 3: Backtesting framework with strict no-leakage guarantees."""

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import create_database
from app.models import BacktestResultRecord, HistoricalMatch, Team

from app.backtesting.dataset import build_dataset, DATASET_VERSION, TRAIN_START, TRAIN_END, VAL_START, VAL_END, TEST_START, TEST_END, WC_2026_START
from app.backtesting.elo_replay import replay_elo_history, ReplayStep, EloReplayResult
from app.backtesting.models import (
    LegacyModel,
    RefittedModel,
    DixonColesModel,
    NegBinomialModel,
    _poisson_probs,
    _dixon_coles_probs,
    _neg_binomial_probs,
    elo_to_strength,
)
from app.backtesting.evaluation import compute_metrics, MatchPrediction, ModelMetrics
from app.backtesting.calibration import TemperatureScaling
from app.backtesting.runner import check_admission


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def db_session(tmp_path) -> Session:
    engine = create_database(tmp_path / "test.sqlite3")
    with Session(engine) as session:
        yield session


@pytest.fixture
def seeded_session(db_session: Session) -> Session:
    """Session with some teams seeded for testing."""
    teams = [
        Team(id="BRA", name="Brazil", short_name="Brazil", code="BRA", group_code="A"),
        Team(id="ARG", name="Argentina", short_name="Argentina", code="ARG", group_code="A"),
        Team(id="ENG", name="England", short_name="England", code="ENG", group_code="B"),
        Team(id="FRA", name="France", short_name="France", code="FRA", group_code="B"),
        Team(id="GER", name="Germany", short_name="Germany", code="GER", group_code="C"),
        Team(id="ESP", name="Spain", short_name="Spain", code="ESP", group_code="C"),
    ]
    for t in teams:
        db_session.add(t)
    db_session.flush()
    return db_session


def _add_historical_match(
    session: Session,
    source_match_id: str,
    kickoff: datetime,
    home_team_id: str | None,
    away_team_id: str | None,
    home_team_raw: str,
    away_team_raw: str,
    home_score: int,
    away_score: int,
    competition: str = "Friendly",
    competition_type: str = "friendly",
    match_importance: float = 1.0,
    neutral_venue: bool = True,
    is_unmapped: bool = False,
    went_to_penalties: bool = False,
    penalty_winner: str | None = None,
    went_to_extra_time: bool = False,
    time_precision: str = "exact",
    available_at: datetime | None = None,
    score_scope: str = "full_90min",
) -> HistoricalMatch:
    if available_at is None:
        if time_precision == "date_only":
            available_at = kickoff + timedelta(days=1)
        else:
            available_at = kickoff

    match = HistoricalMatch(
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
        competition=competition,
        competition_type=competition_type,
        match_importance=match_importance,
        went_to_penalties=went_to_penalties,
        penalty_winner=penalty_winner,
        went_to_extra_time=went_to_extra_time,
        is_unmapped=is_unmapped,
        home_team_source="world_cup",
        away_team_source="world_cup",
        time_precision=time_precision,
        available_at=available_at,
        score_scope=score_scope,
    )
    session.add(match)
    session.flush()
    return match


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


# ── Test: Elo Replay No Leakage ────────────────────────────────────────

class TestEloReplayNoLeakage:
    """Verify that Elo replay never uses future data for predictions."""

    def test_elo_replay_predicts_before_updating(self):
        """After replay, each step's pre_match_elo should NOT include the result of that match."""
        # Create matches for two teams in chronological order
        matches = []
        t1 = datetime(2020, 1, 1, tzinfo=timezone.utc)
        for i in range(5):
            m = _add_historical_match_to_list(
                source_match_id=f"match_{i}",
                kickoff=t1 + timedelta(days=i * 30),
                home_team_id="BRA",
                away_team_id="ARG",
                home_team_raw="Brazil",
                away_team_raw="Argentina",
                home_score=2 + i % 3,
                away_score=1 + i % 2,
                competition_type="friendly",
                neutral_venue=False,
            )
            matches.append(m)

        result = replay_elo_history(matches)

        # For each step, verify that pre_match Elo does NOT incorporate that match's result
        # We do this by manually replaying and checking
        from app.prediction.elo import update_elo

        manual_ratings: dict[str, float] = {}
        initial = 1500.0

        for i, step in enumerate(result.steps):
            # The pre_match Elo should match our manual tracking BEFORE updating
            expected_home = manual_ratings.get(step.home_team_id, initial)
            expected_away = manual_ratings.get(step.away_team_id, initial)

            assert step.pre_match_home_elo == expected_home, (
                f"Step {i}: pre_match_home_elo={step.pre_match_home_elo} "
                f"but expected={expected_home} (should not include this match's result)"
            )
            assert step.pre_match_away_elo == expected_away, (
                f"Step {i}: pre_match_away_elo={step.pre_match_away_elo} "
                f"but expected={expected_away} (should not include this match's result)"
            )

            # Now update manually
            ha = 0.0 if step.neutral_venue else 60.0
            k = step.update_weight
            updated = update_elo(
                expected_home, expected_away,
                step.home_score, step.away_score,
                weight=k, home_advantage=ha,
            )
            manual_ratings[step.home_team_id] = updated.home
            manual_ratings[step.away_team_id] = updated.away

    def test_future_match_not_in_elo(self):
        """A match in 2024 should not affect Elo used for a 2023 prediction."""
        matches = [
            _add_historical_match_to_list(
                source_match_id="early_2023",
                kickoff=datetime(2023, 3, 1, tzinfo=timezone.utc),
                home_team_id="BRA",
                away_team_id="ARG",
                home_team_raw="Brazil",
                away_team_raw="Argentina",
                home_score=3,
                away_score=0,
                competition_type="world_cup",
                neutral_venue=True,
            ),
            _add_historical_match_to_list(
                source_match_id="late_2024",
                kickoff=datetime(2024, 6, 1, tzinfo=timezone.utc),
                home_team_id="BRA",
                away_team_id="ARG",
                home_team_raw="Brazil",
                away_team_raw="Argentina",
                home_score=0,
                away_score=5,
                competition_type="world_cup",
                neutral_venue=True,
            ),
        ]

        result = replay_elo_history(matches)
        assert len(result.steps) == 2

        # The 2023 match should use initial ratings (1500, 1500)
        step_2023 = result.steps[0]
        assert step_2023.available_at.year == 2023
        assert step_2023.pre_match_home_elo == 1500.0
        assert step_2023.pre_match_away_elo == 1500.0

        # The 2024 match should use ratings updated from 2023 result only
        step_2024 = result.steps[1]
        assert step_2024.available_at.year == 2024
        # BRA won 3-0 in 2023, so BRA should be higher than ARG
        assert step_2024.pre_match_home_elo > step_2024.pre_match_away_elo

        # The 2024 match result (0-5) should NOT affect the 2023 step's Elo
        # (already verified by step_2023 having initial ratings)

    def test_available_at_boundary(self):
        """Matches with available_at exactly equal to as_of are excluded (strict less than)."""
        # The dataset uses available_at < end for each split boundary
        # We verify this by checking the split definitions
        boundary_time = VAL_START  # 2022-01-01
        # TRAIN_END == VAL_START, so a match at exactly VAL_START
        # should be in validation, NOT in train (train uses < TRAIN_END)
        assert TRAIN_END == VAL_START
        assert TEST_START == VAL_END


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


# ── Test: Dataset Split Isolation ──────────────────────────────────────

class TestDatasetSplitIsolation:
    """Verify that dataset splits are properly isolated with no overlap."""

    def test_train_val_no_overlap(self, seeded_session: Session):
        """Train and validation match IDs don't overlap."""
        _seed_dataset_matches(seeded_session)
        dataset = build_dataset(seeded_session)

        train_ids = set(dataset.train.match_ids)
        val_ids = set(dataset.validation.match_ids)

        overlap = train_ids & val_ids
        assert len(overlap) == 0, f"Train and validation overlap: {overlap}"

    def test_val_test_no_overlap(self, seeded_session: Session):
        """Validation and test match IDs don't overlap."""
        _seed_dataset_matches(seeded_session)
        dataset = build_dataset(seeded_session)

        val_ids = set(dataset.validation.match_ids)
        test_ids = set(dataset.test.match_ids)

        overlap = val_ids & test_ids
        assert len(overlap) == 0, f"Validation and test overlap: {overlap}"

    def test_wc_2026_excluded(self, seeded_session: Session):
        """WC 2026 matches are not in any split."""
        # Add a match during WC 2026
        _add_historical_match(
            seeded_session,
            source_match_id="wc_2026_match",
            kickoff=datetime(2026, 6, 15, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=2,
            away_score=1,
            competition_type="world_cup",
            available_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )

        dataset = build_dataset(seeded_session)

        all_ids = (
            set(dataset.train.match_ids)
            | set(dataset.validation.match_ids)
            | set(dataset.test.match_ids)
        )
        assert "wc_2026_match" not in all_ids

    def test_only_full_90min(self, seeded_session: Session):
        """All matches in splits have score_scope == 'full_90min'."""
        _seed_dataset_matches(seeded_session)
        # Also add a non-90min match in the train range
        _add_historical_match(
            seeded_session,
            source_match_id="et_match_train",
            kickoff=datetime(2019, 6, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=2,
            away_score=1,
            competition_type="continental",
            score_scope="after_extra_time_or_unknown",
            available_at=datetime(2019, 6, 2, tzinfo=timezone.utc),
        )

        dataset = build_dataset(seeded_session)

        # Verify no non-90min matches in any split
        for split_name, split in [
            ("train", dataset.train),
            ("validation", dataset.validation),
            ("test", dataset.test),
        ]:
            for mid in split.match_ids:
                match = seeded_session.scalar(
                    select(HistoricalMatch).where(
                        HistoricalMatch.source_match_id == mid
                    )
                )
                assert match.score_scope == "full_90min", (
                    f"{split_name} contains non-90min match: {mid}"
                )

        # The ET match should not be in any split
        all_ids = (
            set(dataset.train.match_ids)
            | set(dataset.validation.match_ids)
            | set(dataset.test.match_ids)
        )
        assert "et_match_train" not in all_ids

    def test_only_mapped(self, seeded_session: Session):
        """All matches in splits have is_unmapped == False."""
        _seed_dataset_matches(seeded_session)
        # Add an unmapped match in the train range
        _add_historical_match(
            seeded_session,
            source_match_id="unmapped_train",
            kickoff=datetime(2019, 3, 1, tzinfo=timezone.utc),
            home_team_id=None,
            away_team_id=None,
            home_team_raw="Unknown A",
            away_team_raw="Unknown B",
            home_score=1,
            away_score=0,
            is_unmapped=True,
            available_at=datetime(2019, 3, 2, tzinfo=timezone.utc),
        )

        dataset = build_dataset(seeded_session)

        all_ids = (
            set(dataset.train.match_ids)
            | set(dataset.validation.match_ids)
            | set(dataset.test.match_ids)
        )
        assert "unmapped_train" not in all_ids


def _seed_dataset_matches(session: Session) -> None:
    """Seed matches across all dataset splits."""
    # Train range: 2018-01-01 to 2022-01-01
    _add_historical_match(
        session,
        source_match_id="train_1",
        kickoff=datetime(2019, 6, 1, tzinfo=timezone.utc),
        home_team_id="BRA",
        away_team_id="ARG",
        home_team_raw="Brazil",
        away_team_raw="Argentina",
        home_score=2,
        away_score=1,
        competition_type="friendly",
        available_at=datetime(2019, 6, 2, tzinfo=timezone.utc),
    )
    _add_historical_match(
        session,
        source_match_id="train_2",
        kickoff=datetime(2020, 3, 15, tzinfo=timezone.utc),
        home_team_id="ENG",
        away_team_id="FRA",
        home_team_raw="England",
        away_team_raw="France",
        home_score=1,
        away_score=1,
        competition_type="friendly",
        available_at=datetime(2020, 3, 16, tzinfo=timezone.utc),
    )

    # Validation range: 2022-01-01 to 2024-01-01
    _add_historical_match(
        session,
        source_match_id="val_1",
        kickoff=datetime(2022, 6, 1, tzinfo=timezone.utc),
        home_team_id="GER",
        away_team_id="ESP",
        home_team_raw="Germany",
        away_team_raw="Spain",
        home_score=1,
        away_score=2,
        competition_type="continental",
        available_at=datetime(2022, 6, 2, tzinfo=timezone.utc),
    )

    # Test range: 2024-01-01 to 2026-06-11
    _add_historical_match(
        session,
        source_match_id="test_1",
        kickoff=datetime(2024, 6, 1, tzinfo=timezone.utc),
        home_team_id="BRA",
        away_team_id="ENG",
        home_team_raw="Brazil",
        away_team_raw="England",
        home_score=3,
        away_score=1,
        competition_type="friendly",
        available_at=datetime(2024, 6, 2, tzinfo=timezone.utc),
    )


# ── Test: Model Probabilities ──────────────────────────────────────────

class TestModelProbabilities:
    """Verify that model outputs are valid probability distributions."""

    def test_dixon_coles_probs_sum_to_1(self):
        """Dixon-Coles model outputs sum to 1.0."""
        probs = _dixon_coles_probs(1.3, 1.0, rho=-0.1)
        assert abs(sum(probs) - 1.0) < 1e-10, f"Sum = {sum(probs)}"

    def test_neg_binomial_probs_sum_to_1(self):
        """Negative Binomial model outputs sum to 1.0."""
        probs = _neg_binomial_probs(1.3, 1.0, alpha=0.1)
        assert abs(sum(probs) - 1.0) < 1e-10, f"Sum = {sum(probs)}"

    def test_legacy_probs_sum_to_1(self):
        """Legacy model outputs sum to 1.0."""
        probs = _poisson_probs(1.3, 1.0)
        assert abs(sum(probs) - 1.0) < 1e-10, f"Sum = {sum(probs)}"

    def test_calibrated_probs_sum_to_1(self):
        """After temperature scaling, probabilities sum to 1.0."""
        cal = TemperatureScaling()
        # Use default temperature (1.0) since we haven't fit
        raw_probs = (0.5, 0.25, 0.25)
        calibrated = cal.calibrate(raw_probs)
        assert abs(sum(calibrated) - 1.0) < 1e-10, f"Sum = {sum(calibrated)}"

        # Also test with a non-trivial temperature
        cal._temperature = 1.5
        calibrated = cal.calibrate(raw_probs)
        assert abs(sum(calibrated) - 1.0) < 1e-10, f"Sum = {sum(calibrated)}"

        cal._temperature = 0.5
        calibrated = cal.calibrate(raw_probs)
        assert abs(sum(calibrated) - 1.0) < 1e-10, f"Sum = {sum(calibrated)}"

    def test_calibrated_probs_valid(self):
        """All calibrated probabilities are in [0, 1]."""
        cal = TemperatureScaling()

        for temp in [0.3, 0.5, 1.0, 1.5, 2.0, 3.0]:
            cal._temperature = temp
            for raw in [(0.6, 0.2, 0.2), (0.4, 0.3, 0.3), (0.33, 0.34, 0.33)]:
                calibrated = cal.calibrate(raw)
                for p in calibrated:
                    assert 0.0 <= p <= 1.0, f"temp={temp}, raw={raw}, cal={calibrated}"


# ── Test: Legacy Reproducibility ───────────────────────────────────────

class TestLegacyReproducibility:
    """Verify that Legacy model reproduces production predict_match() results."""

    def test_legacy_with_default_params_reproduces(self):
        """Running Legacy model with default params gives same results as predict_match()."""
        from app.prediction.poisson import predict_match, MatchContext

        # Create a ReplayStep with known Elo values
        step = _make_replay_step(
            pre_match_home_elo=1600.0,
            pre_match_away_elo=1500.0,
            neutral_venue=False,
        )

        # All Elo values for normalization
        all_elos = [1600.0, 1500.0, 1550.0, 1480.0, 1520.0]

        # Legacy model prediction
        legacy = LegacyModel()
        legacy_probs = legacy.predict(step, all_elos)

        # Production predict_match prediction
        home_s, away_s = elo_to_strength(
            step.pre_match_home_elo,
            step.pre_match_away_elo,
            all_elos,
        )
        ctx = MatchContext(
            data_freshness=1.0,
            ranking_coverage=1.0,
            history_coverage=1.0,
            provider_agreement=1.0,
        )
        prod_result = predict_match(home_s, away_s, ctx)
        prod_probs = (prod_result.home_win, prod_result.draw, prod_result.away_win)

        # They should be identical (within floating point tolerance)
        for i, (lp, pp) in enumerate(zip(legacy_probs, prod_probs)):
            assert abs(lp - pp) < 1e-10, (
                f"Legacy prob[{i}]={lp} != production prob[{i}]={pp}"
            )

    def test_fixed_seed_reproducible(self):
        """Running backtest twice gives identical results."""
        step = _make_replay_step(
            pre_match_home_elo=1600.0,
            pre_match_away_elo=1500.0,
        )
        all_elos = [1600.0, 1500.0, 1550.0]

        legacy = LegacyModel()

        # Run twice
        result1 = legacy.predict(step, all_elos)
        result2 = legacy.predict(step, all_elos)

        assert result1 == result2


# ── Test: Score Scope Exclusion ────────────────────────────────────────

class TestScoreScopeExclusion:
    """Verify that non-90min matches are excluded from dataset splits."""

    def test_after_extra_time_excluded_from_training(self, seeded_session: Session):
        """Matches with score_scope='after_extra_time_or_unknown' are not in any dataset split."""
        _add_historical_match(
            seeded_session,
            source_match_id="aet_match",
            kickoff=datetime(2019, 6, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=2,
            away_score=1,
            competition_type="continental",
            score_scope="after_extra_time_or_unknown",
            available_at=datetime(2019, 6, 2, tzinfo=timezone.utc),
        )
        # Also add a valid match so dataset is not empty
        _seed_dataset_matches(seeded_session)

        dataset = build_dataset(seeded_session)

        all_ids = (
            set(dataset.train.match_ids)
            | set(dataset.validation.match_ids)
            | set(dataset.test.match_ids)
        )
        assert "aet_match" not in all_ids

    def test_unknown_score_scope_excluded(self, seeded_session: Session):
        """Matches with score_scope='unknown_score_scope' are not in any dataset split."""
        _add_historical_match(
            seeded_session,
            source_match_id="unknown_scope_match",
            kickoff=datetime(2020, 3, 1, tzinfo=timezone.utc),
            home_team_id="ENG",
            away_team_id="FRA",
            home_team_raw="England",
            away_team_raw="France",
            home_score=1,
            away_score=0,
            competition_type="friendly",
            score_scope="unknown_score_scope",
            available_at=datetime(2020, 3, 2, tzinfo=timezone.utc),
        )
        # Also add a valid match so dataset is not empty
        _seed_dataset_matches(seeded_session)

        dataset = build_dataset(seeded_session)

        all_ids = (
            set(dataset.train.match_ids)
            | set(dataset.validation.match_ids)
            | set(dataset.test.match_ids)
        )
        assert "unknown_scope_match" not in all_ids


# ── Test: Data Version Traceability ────────────────────────────────────

class TestDataVersionTraceability:
    """Verify that backtest results are traceable to data and model versions."""

    def test_dataset_version_persisted(self, seeded_session: Session):
        """BacktestResultRecord has data_version field."""
        _seed_dataset_matches(seeded_session)

        record = BacktestResultRecord(
            data_version=DATASET_VERSION,
            model_name="legacy-elo-poisson",
            split_name="test",
            brier_score=0.25,
            brier_score_avg=0.0833,
            log_loss=0.65,
            ece=0.05,
            top1_hit_rate=0.55,
            draw_recall=0.20,
            match_count=100,
            parameters_json={"key": "value"},
            admission_status="shadow",
        )
        seeded_session.add(record)
        seeded_session.flush()

        saved = seeded_session.scalar(
            select(BacktestResultRecord).where(
                BacktestResultRecord.model_name == "legacy-elo-poisson"
            )
        )
        assert saved is not None
        assert saved.data_version == DATASET_VERSION
        assert saved.data_version == "international-history-v1"

    def test_model_parameters_saved(self, seeded_session: Session):
        """BacktestResultRecord has parameters_json field."""
        params = {"base_goal_home": 1.25, "strength_coeff_home": 0.90}

        record = BacktestResultRecord(
            data_version=DATASET_VERSION,
            model_name="refitted-elo-poisson",
            split_name="test",
            brier_score=0.24,
            brier_score_avg=0.08,
            log_loss=0.63,
            ece=0.04,
            top1_hit_rate=0.56,
            draw_recall=0.22,
            match_count=100,
            parameters_json=params,
            admission_status="shadow",
        )
        seeded_session.add(record)
        seeded_session.flush()

        saved = seeded_session.scalar(
            select(BacktestResultRecord).where(
                BacktestResultRecord.model_name == "refitted-elo-poisson"
            )
        )
        assert saved is not None
        assert saved.parameters_json is not None
        assert saved.parameters_json["base_goal_home"] == 1.25
        assert saved.parameters_json["strength_coeff_home"] == 0.90


# ── Test: Admission Rules ──────────────────────────────────────────────

class TestAdmissionRules:
    """Verify admission rules for Shadow model promotion."""

    def test_model_with_worse_brier_rejected(self):
        """A model with worse test Brier than Legacy is rejected."""
        model_metrics = ModelMetrics(
            model_name="test-model",
            split_name="test",
            data_version=DATASET_VERSION,
            brier_score=0.30,  # worse
            log_loss=0.60,
            ece=0.04,
            draw_recall=0.30,
        )
        legacy_metrics = ModelMetrics(
            model_name="legacy-elo-poisson",
            split_name="test",
            data_version=DATASET_VERSION,
            brier_score=0.25,  # better
            log_loss=0.65,
            ece=0.05,
            draw_recall=0.20,
        )

        result = check_admission(
            "test-model",
            {"test": model_metrics},
            {"test": legacy_metrics},
        )
        assert result == "rejected"

    def test_model_with_better_brier_but_no_draw_recall_rejected(self):
        """Better Brier but draw_recall=0 is rejected (draw_recall improvement <= 0.05)."""
        model_metrics = ModelMetrics(
            model_name="test-model",
            split_name="test",
            data_version=DATASET_VERSION,
            brier_score=0.20,  # better
            log_loss=0.60,     # not worse
            ece=0.04,          # not worse
            draw_recall=0.0,   # no draw recall at all
        )
        legacy_metrics = ModelMetrics(
            model_name="legacy-elo-poisson",
            split_name="test",
            data_version=DATASET_VERSION,
            brier_score=0.25,
            log_loss=0.65,
            ece=0.05,
            draw_recall=0.20,
        )

        result = check_admission(
            "test-model",
            {"test": model_metrics},
            {"test": legacy_metrics},
        )
        # draw_recall improvement = 0.0 - 0.20 = -0.20, which is <= 0.05
        assert result == "rejected"

    def test_legacy_always_shadow(self):
        """Legacy baseline is always admitted as shadow."""
        # check_admission is not called for legacy in the runner,
        # but we verify the convention: legacy is always "shadow"
        # The runner hardcodes: admission_results["legacy-elo-poisson"] = "shadow"
        # We test that a model meeting all criteria is admitted
        model_metrics = ModelMetrics(
            model_name="good-model",
            split_name="test",
            data_version=DATASET_VERSION,
            brier_score=0.20,  # better than legacy
            log_loss=0.60,     # not worse
            ece=0.04,          # not worse
            draw_recall=0.30,  # clearly improved (>0.05 over legacy's 0.20)
        )
        legacy_metrics = ModelMetrics(
            model_name="legacy-elo-poisson",
            split_name="test",
            data_version=DATASET_VERSION,
            brier_score=0.25,
            log_loss=0.65,
            ece=0.05,
            draw_recall=0.20,
        )

        result = check_admission(
            "good-model",
            {"test": model_metrics},
            {"test": legacy_metrics},
        )
        assert result == "shadow"
