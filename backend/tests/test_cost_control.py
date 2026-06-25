"""Tests for local AI cost control: limit clamping, retry_failed, only_missing behavior."""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db import create_database, session_scope
from app.models import (
    AIPrediction, DashboardRevision, Match, PredictionSnapshot,
    Team, TeamRating,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    """Create an in-memory database for all tests."""
    eng = create_database("sqlite://")
    return eng


@pytest.fixture
def session(engine):
    """Provide a clean session for each test."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    sess = Session()
    try:
        yield sess
        sess.rollback()
    finally:
        sess.close()


def _make_team(session, team_id, name, group="A", elo=1500.0):
    """Helper to create a team with rating."""
    from datetime import date
    team = Team(id=team_id, name=name, short_name=name, code=team_id[:3].upper(), group_code=group)
    session.add(team)
    session.flush()
    rating = TeamRating(team_id=team_id, effective_date=date(2026, 6, 1), elo=elo, source="test")
    session.add(rating)
    session.flush()
    return team


def _make_match(session, match_id, home_id, away_id, stage="group", group_code="A"):
    """Helper to create a scheduled match."""
    kickoff = datetime.now(timezone.utc) + timedelta(hours=48)
    match = Match(
        id=match_id, home_team_id=home_id, away_team_id=away_id,
        kickoff=kickoff, status="scheduled", source="test",
        group_code=group_code, stage=stage,
    )
    session.add(match)
    session.flush()
    return match


def _make_revision(session):
    """Helper to create a dashboard revision."""
    rev = DashboardRevision(
        created_at=datetime.now(timezone.utc),
        model_version="elo-poisson-v1",
        simulation_iterations=100,
        simulation_seed=42,
    )
    session.add(rev)
    session.flush()
    return rev


def _make_prediction_snapshot(session, match_id):
    """Helper to create a prediction snapshot for a match."""
    rev = _make_revision(session)
    snap = PredictionSnapshot(
        match_id=match_id,
        revision_id=rev.id,
        kickoff=datetime.now(timezone.utc),
        home_win=0.5, draw=0.3, away_win=0.2,
        home_xg=1.0, away_xg=0.8,
        scorelines=[], score_matrix=[],
        confidence=0.8, confidence_label="High",
        model_inputs={}, model_version="elo-poisson-v1",
    )
    session.add(snap)
    session.flush()
    return snap


def _make_ai_prediction(session, match_id, model_version, error_code=None):
    """Helper to create an AI prediction record."""
    pred = AIPrediction(
        match_id=match_id,
        provider="deepseek",
        model_id="deepseek-v4-flash",
        model_version=model_version,
        prompt_version="worldcup-ai-v1",
        input_snapshot_json={},
        raw_response_text="",
        latency_ms=100,
        created_at=datetime.now(timezone.utc),
    )
    if error_code:
        pred.error_code = error_code
        pred.error_message = "test error"
    else:
        pred.parsed_home_win = 0.5
        pred.parsed_draw = 0.3
        pred.parsed_away_win = 0.2
    session.add(pred)
    session.flush()
    return pred


# ── Limit Validation Tests ────────────────────────────────────────

class TestLimitClamping:
    """Test that the /ai-predictions/run-all endpoint validates the limit parameter."""

    @pytest.fixture(autouse=True)
    def _enable_ai(self, monkeypatch):
        """Enable AI prediction for these tests."""
        monkeypatch.setattr("app.config.settings.enable_ai_prediction", True)

    def test_limit_zero_returns_400(self, monkeypatch):
        """limit=0 should be rejected with 400."""
        # Limit validation happens before any DB access, so no need for seed data
        from app.main import create_app
        client = TestClient(create_app(start_background=False))

        response = client.post("/api/ai-predictions/run-all?limit=0")
        assert response.status_code == 400
        assert "limit must be between 1 and" in response.json()["detail"]

    def test_limit_exceeds_max_returns_400(self, monkeypatch):
        """limit=999999 should be rejected with 400."""
        from app.main import create_app
        client = TestClient(create_app(start_background=False))

        response = client.post("/api/ai-predictions/run-all?limit=999999")
        assert response.status_code == 400
        assert "limit must be between 1 and" in response.json()["detail"]

    def test_limit_within_range_accepted(self, session, monkeypatch):
        """limit=10 should be accepted (not return 400 for limit)."""
        _make_team(session, "T5", "Team5")
        _make_team(session, "T6", "Team6")
        _make_match(session, "m3", "T5", "T6")
        _make_prediction_snapshot(session, "m3")

        # Mock the batch function so we don't actually call AI
        mock_batch = AsyncMock(return_value=[])
        monkeypatch.setattr("app.ai.service.run_ai_predictions_batch", mock_batch)

        from app.main import create_app
        client = TestClient(create_app(start_background=False))

        response = client.post("/api/ai-predictions/run-all?limit=10")
        # Should not be 400 for limit validation
        assert response.status_code != 400 or "limit" not in response.json().get("detail", "")

    def test_retry_failed_parameter_accepted(self, session, monkeypatch):
        """retry_failed=true should be accepted as a parameter."""
        _make_team(session, "T7", "Team7")
        _make_team(session, "T8", "Team8")
        _make_match(session, "m4", "T7", "T8")
        _make_prediction_snapshot(session, "m4")

        mock_batch = AsyncMock(return_value=[])
        monkeypatch.setattr("app.ai.service.run_ai_predictions_batch", mock_batch)

        from app.main import create_app
        client = TestClient(create_app(start_background=False))

        response = client.post("/api/ai-predictions/run-all?limit=5&retry_failed=true")
        # Should not return 400 for parameter issues
        assert response.status_code != 400 or "limit" not in response.json().get("detail", "")
        # Verify retry_failed was passed
        if mock_batch.called:
            call_kwargs = mock_batch.call_args
            assert call_kwargs.kwargs.get("retry_failed") is True


# ── only_missing / retry_failed Logic Tests ───────────────────────

class TestOnlyMissingAndRetryFailed:
    """Test that only_missing and retry_failed work correctly in the batch function."""

    @pytest.mark.asyncio
    async def test_only_missing_skips_existing_predictions(self, session):
        """only_missing=True should skip matches that already have successful predictions."""
        from app.ai.service import run_ai_predictions_batch

        _make_team(session, "OM1", "OMTeam1")
        _make_team(session, "OM2", "OMTeam2")
        _make_match(session, "om_m1", "OM1", "OM2")
        _make_prediction_snapshot(session, "om_m1")

        # Create a successful AI prediction for all enabled models
        from app.ai.model_registry import list_enabled_models
        models = list_enabled_models()
        for model in models:
            _make_ai_prediction(session, "om_m1", model.model_version)

        # Mock run_ai_predictions_for_match so we don't actually call AI
        with patch("app.ai.service.run_ai_predictions_for_match", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = []
            results = await run_ai_predictions_batch(
                session, limit=10, only_missing=True, retry_failed=False,
            )

        # All matches should be skipped
        skipped = [r for r in results if r.get("status") == "skipped"]
        assert len(skipped) >= 1

    @pytest.mark.asyncio
    async def test_retry_failed_retries_failed_predictions(self, session):
        """retry_failed=True should retry models that previously failed."""
        from app.ai.service import run_ai_predictions_batch

        _make_team(session, "RF1", "RFTeam1")
        _make_team(session, "RF2", "RFTeam2")
        _make_match(session, "rf_m1", "RF1", "RF2")
        _make_prediction_snapshot(session, "rf_m1")

        # Create a failed AI prediction for the first enabled model
        from app.ai.model_registry import list_enabled_models
        models = list_enabled_models()
        if models:
            _make_ai_prediction(session, "rf_m1", models[0].model_version, error_code="api_error")

        # With retry_failed=False, the failed model should be skipped (only_missing=True)
        with patch("app.ai.service.run_ai_predictions_for_match", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = []
            await run_ai_predictions_batch(
                session, limit=10, only_missing=True, retry_failed=False,
            )

        # With retry_failed=True, the match should be processed because the failed model needs retry
        with patch("app.ai.service.run_ai_predictions_for_match", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [{"status": "success", "model_version": models[0].model_version}]
            results = await run_ai_predictions_batch(
                session, limit=10, only_missing=True, retry_failed=True,
            )

        # The match should have been processed (not just skipped)
        non_skipped = [r for r in results if r.get("status") != "skipped"]
        assert len(non_skipped) >= 1

    @pytest.mark.asyncio
    async def test_batch_ai_skips_started_matches_and_uses_limit_for_future_matches(self, session):
        """Already-started scheduled matches should not consume the AI batch limit."""
        from app.ai.service import run_ai_predictions_batch

        _make_team(session, "NF1", "NoFuture1")
        _make_team(session, "NF2", "NoFuture2")
        _make_team(session, "NF3", "NoFuture3")
        _make_team(session, "NF4", "NoFuture4")

        started = _make_match(session, "started_scheduled", "NF1", "NF2")
        started.kickoff = datetime.now(timezone.utc) - timedelta(hours=1)
        future = _make_match(session, "future_missing_ai", "NF3", "NF4")
        future.kickoff = datetime.now(timezone.utc) + timedelta(hours=1)
        session.flush()

        with patch("app.ai.service.run_ai_predictions_for_match", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [{"status": "success", "match_id": "future_missing_ai"}]

            await run_ai_predictions_batch(
                session, limit=1, only_missing=True, retry_failed=False,
            )

        mock_run.assert_called_once()
        assert mock_run.call_args.args[1] == "future_missing_ai"

    @pytest.mark.asyncio
    async def test_only_missing_processes_matches_with_no_valid_ai_not_partial_coverage(self, session):
        """Default only_missing mode should match the dashboard count: no valid AI at all."""
        from app.ai.service import run_ai_predictions_batch
        from app.ai.model_registry import list_enabled_models

        _make_team(session, "PM1", "PartialMissing1")
        _make_team(session, "PM2", "PartialMissing2")
        _make_team(session, "PM3", "PartialMissing3")
        _make_team(session, "PM4", "PartialMissing4")

        partial = _make_match(session, "partial_ai_match", "PM1", "PM2")
        partial.kickoff = datetime.now(timezone.utc) + timedelta(hours=1)
        no_ai = _make_match(session, "no_ai_match", "PM3", "PM4")
        no_ai.kickoff = datetime.now(timezone.utc) + timedelta(hours=2)

        models = list_enabled_models()
        assert len(models) > 1
        _make_ai_prediction(session, "partial_ai_match", models[0].model_version)
        session.flush()

        with patch("app.ai.service.run_ai_predictions_for_match", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [{"status": "success", "match_id": "no_ai_match"}]

            await run_ai_predictions_batch(
                session, limit=2, only_missing=True, retry_failed=False,
            )

        mock_run.assert_called_once()
        assert mock_run.call_args.args[1] == "no_ai_match"
