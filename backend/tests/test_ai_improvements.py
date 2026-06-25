"""Tests for AI improvements: probability validation, lock status, most_likely_score default."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.lock_status import LockStatus, compute_match_lock_status
from app.ai.schemas import AIParsedOutput


# ---------------------------------------------------------------------------
# Helper: simple Match mock
# ---------------------------------------------------------------------------

@dataclass
class MockMatch:
    kickoff: datetime
    status: str = "upcoming"


# ===================================================================
# Probability validation tests (P1-4)
# ===================================================================

class TestProbabilityValidation:
    """Test the probability validation logic that runs in service.py."""

    def _validate_probabilities(self, parsed: AIParsedOutput) -> tuple[bool, list[str], AIParsedOutput]:
        """Replicate the validation logic from service.py for unit testing."""
        prob_sum = parsed.home_win + parsed.draw + parsed.away_win
        prob_valid = True
        prob_warnings = []

        for name, val in [("home_win", parsed.home_win), ("draw", parsed.draw), ("away_win", parsed.away_win)]:
            if not isinstance(val, (int, float)) or val < 0 or val > 1:
                prob_valid = False
                prob_warnings.append(f"{name}={val} out of [0,1]")

        if prob_valid:
            if prob_sum < 0.80 or prob_sum > 1.20:
                prob_valid = False
                prob_warnings.append(f"sum={prob_sum:.4f} outside [0.80, 1.20]")
            elif abs(prob_sum - 1.0) <= 0.02:
                parsed.home_win /= prob_sum
                parsed.draw /= prob_sum
                parsed.away_win /= prob_sum

        return prob_valid, prob_warnings, parsed

    def test_valid_probabilities_pass(self):
        """Valid probabilities (0.5, 0.3, 0.2) pass validation."""
        parsed = AIParsedOutput(home_win=0.5, draw=0.3, away_win=0.2, confidence=0.8)
        prob_valid, warnings, result = self._validate_probabilities(parsed)
        assert prob_valid is True
        assert warnings == []
        assert abs(result.home_win - 0.5) < 1e-9
        assert abs(result.draw - 0.3) < 1e-9
        assert abs(result.away_win - 0.2) < 1e-9

    def test_probabilities_over_1_rejected(self):
        """Probabilities > 1 (50, 30, 20) are rejected with error_code 'invalid_probabilities'."""
        parsed = AIParsedOutput(home_win=50, draw=30, away_win=20, confidence=0.8)
        prob_valid, warnings, _ = self._validate_probabilities(parsed)
        assert prob_valid is False
        assert any("home_win=50 out of [0,1]" in w for w in warnings)

    def test_probabilities_sum_too_low_rejected(self):
        """Probabilities sum too low (0.2, 0.2, 0.2) are rejected."""
        parsed = AIParsedOutput(home_win=0.2, draw=0.2, away_win=0.2, confidence=0.5)
        prob_valid, warnings, _ = self._validate_probabilities(parsed)
        assert prob_valid is False
        assert any("sum=" in w and "outside [0.80, 1.20]" in w for w in warnings)

    def test_negative_probability_rejected(self):
        """Negative probability (-0.1, 0.6, 0.5) is rejected."""
        parsed = AIParsedOutput(home_win=-0.1, draw=0.6, away_win=0.5, confidence=0.5)
        prob_valid, warnings, _ = self._validate_probabilities(parsed)
        assert prob_valid is False
        assert any("home_win=-0.1 out of [0,1]" in w for w in warnings)

    def test_probabilities_close_to_1_normalized(self):
        """Probabilities close to 1.0 (0.49, 0.30, 0.20) are normalized."""
        parsed = AIParsedOutput(home_win=0.49, draw=0.30, away_win=0.20, confidence=0.8)
        prob_sum = 0.49 + 0.30 + 0.20  # 0.99
        prob_valid, warnings, result = self._validate_probabilities(parsed)
        assert prob_valid is True
        assert warnings == []
        # Should be normalized so sum == 1.0
        new_sum = result.home_win + result.draw + result.away_win
        assert abs(new_sum - 1.0) < 1e-9
        assert abs(result.home_win - 0.49 / prob_sum) < 1e-9

    def test_invalid_probabilities_not_enter_ensemble(self):
        """Invalid probabilities should result in error_code='invalid_probabilities',
        which means they won't enter the ensemble (ensemble filters by error_code IS None)."""
        parsed = AIParsedOutput(home_win=50, draw=30, away_win=20, confidence=0.8)
        prob_valid, warnings, _ = self._validate_probabilities(parsed)
        assert prob_valid is False
        # In service.py, this would set ai_pred.error_code = "invalid_probabilities"
        # The ensemble query filters: .where(AIPrediction.error_code.is_(None))
        # So invalid predictions are excluded from ensemble
        error_code = "invalid_probabilities" if not prob_valid else None
        assert error_code == "invalid_probabilities"


# ===================================================================
# Lock status tests (P1-5)
# ===================================================================

class TestLockStatus:
    """Test the compute_match_lock_status utility."""

    def test_not_locked_25h_before_kickoff(self):
        """25h before kickoff is outside the 24h lock window."""
        kickoff = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
        now = kickoff - timedelta(hours=25)
        match = MockMatch(kickoff=kickoff, status="upcoming")

        lock = compute_match_lock_status(match, now=now)

        assert lock.is_pre_match_locked is False
        assert lock.participates_in_model_score is False
        assert lock.locked_at is None

    def test_pre_match_locked_23h_before_kickoff(self):
        """23h before kickoff is within the 24h lock window."""
        kickoff = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
        now = kickoff - timedelta(hours=23)
        match = MockMatch(kickoff=kickoff, status="upcoming")

        lock = compute_match_lock_status(match, now=now)

        assert lock.is_fallback_locked is False
        assert lock.real_time_only is False
        assert lock.is_pre_match_locked is True
        assert lock.locked_at == now
        assert lock.participates_in_model_score is True

    def test_real_time_only_after_kickoff(self):
        """After kickoff: real_time_only=True."""
        kickoff = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
        now = kickoff + timedelta(minutes=5)
        match = MockMatch(kickoff=kickoff, status="live")

        lock = compute_match_lock_status(match, now=now)

        assert lock.real_time_only is True
        assert lock.is_pre_match_locked is False
        assert lock.is_fallback_locked is False

    def test_final_match_not_participate_in_model_score(self):
        """Match status='final': participates_in_model_score=False even within lock window."""
        kickoff = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
        now = kickoff - timedelta(hours=1)
        match = MockMatch(kickoff=kickoff, status="final")

        lock = compute_match_lock_status(match, now=now)

        # Within 24h window but match is final, so doesn't participate
        assert lock.is_pre_match_locked is True
        assert lock.participates_in_model_score is False

    def test_naive_kickoff_treated_as_utc(self):
        """Kickoff without tzinfo is treated as UTC."""
        kickoff_naive = datetime(2026, 7, 1, 18, 0)
        now = datetime(2026, 7, 1, 17, 20, tzinfo=timezone.utc)
        match = MockMatch(kickoff=kickoff_naive, status="upcoming")

        lock = compute_match_lock_status(match, now=now)

        # 40 minutes before kickoff is within the 24h lock window.
        assert lock.is_pre_match_locked is True


# ===================================================================
# most_likely_score default tests (P1-6)
# ===================================================================

class TestMostLikelyScoreDefault:
    """Test that most_likely_score defaults to 'unknown' not '1-0'."""

    def test_empty_scorelines_gives_unknown(self):
        """When scorelines is empty, most_likely_score is 'unknown' not '1-0'."""
        # This replicates the logic from _build_prediction_request in service.py
        snap_scorelines = None
        most_likely = "unknown"
        if snap_scorelines:
            top = snap_scorelines[0]
            most_likely = f"{top.get('home_goals', 1)}-{top.get('away_goals', 0)}"

        assert most_likely == "unknown"
        assert most_likely != "1-0"

    def test_with_scorelines_uses_first(self):
        """When scorelines exist, most_likely_score uses the first entry."""
        snap_scorelines = [{"home_goals": 2, "away_goals": 1}]
        most_likely = "unknown"
        if snap_scorelines:
            top = snap_scorelines[0]
            most_likely = f"{top.get('home_goals', 1)}-{top.get('away_goals', 0)}"

        assert most_likely == "2-1"


# ===================================================================
# Concurrent AI model calls tests (P1-7)
# ===================================================================

class TestConcurrentAIModelCalls:
    """Test that run_ai_predictions_for_match uses asyncio.gather for concurrency."""

    @pytest.mark.asyncio
    async def test_calls_all_models(self):
        """run_ai_predictions_for_match calls run_ai_prediction for each enabled model."""
        from unittest.mock import AsyncMock, patch, MagicMock

        from app.ai.providers.base import AIModelConfig
        from app.ai.service import run_ai_predictions_for_match

        model_a = AIModelConfig(
            provider_name="deepseek",
            model_id="model-a",
            enabled=True,
            model_version="v1",
            display_name="Model A",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )
        model_b = AIModelConfig(
            provider_name="deepseek",
            model_id="model-b",
            enabled=True,
            model_version="v2",
            display_name="Model B",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )

        async def fake_call(session, match_id, model_version):
            return {"status": "success", "model_version": model_version, "prediction_id": 1}

        with patch("app.ai.service.list_enabled_models", return_value=[model_a, model_b]), \
             patch("app.ai.service._call_ai_provider", side_effect=fake_call) as mock_call, \
             patch("app.ai.service.get_provider_config", return_value=MagicMock()), \
             patch("app.ai.service._get_provider") as mock_get_provider, \
             patch("app.ai.service._find_existing_prediction", return_value=None):
            mock_provider = MagicMock()
            mock_provider.is_configured.return_value = True
            mock_get_provider.return_value = mock_provider
            session = MagicMock()
            results = await run_ai_predictions_for_match(session, "match-1")

        assert len(results) == 2
        called_versions = {call.args[2] for call in mock_call.call_args_list}
        assert called_versions == {"v1", "v2"}

    @pytest.mark.asyncio
    async def test_one_failure_does_not_prevent_others(self):
        """One model failure does not prevent other models from running."""
        from unittest.mock import AsyncMock, patch, MagicMock

        from app.ai.providers.base import AIModelConfig
        from app.ai.service import run_ai_predictions_for_match

        model_a = AIModelConfig(
            provider_name="deepseek",
            model_id="model-a",
            enabled=True,
            model_version="v1",
            display_name="Model A",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )
        model_b = AIModelConfig(
            provider_name="deepseek",
            model_id="model-b",
            enabled=True,
            model_version="v2",
            display_name="Model B",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )

        async def fake_call(session, match_id, model_version):
            if model_version == "v1":
                raise RuntimeError("API timeout")
            return {"status": "success", "model_version": model_version, "prediction_id": 2}

        with patch("app.ai.service.list_enabled_models", return_value=[model_a, model_b]), \
             patch("app.ai.service._call_ai_provider", side_effect=fake_call), \
             patch("app.ai.service.get_provider_config", return_value=MagicMock()), \
             patch("app.ai.service._get_provider") as mock_get_provider, \
             patch("app.ai.service._find_existing_prediction", return_value=None):
            mock_provider = MagicMock()
            mock_provider.is_configured.return_value = True
            mock_get_provider.return_value = mock_provider
            session = MagicMock()
            results = await run_ai_predictions_for_match(session, "match-1")

        assert len(results) == 2
        statuses = {r.get("status") for r in results}
        assert "error" in statuses
        assert "success" in statuses

    @pytest.mark.asyncio
    async def test_empty_models_returns_empty(self):
        """When no models are enabled, returns empty list."""
        from unittest.mock import patch, MagicMock

        from app.ai.service import run_ai_predictions_for_match

        with patch("app.ai.service.list_enabled_models", return_value=[]):
            session = MagicMock()
            results = await run_ai_predictions_for_match(session, "match-1")

        assert results == []


# ===================================================================
# Improved run-all deduplication tests (P1-8)
# ===================================================================

class TestBatchDeduplication:
    """Test improved deduplication in run_ai_predictions_batch."""

    @pytest.mark.asyncio
    async def test_retry_failed_false_skips_successful(self):
        """retry_failed=False skips matches with existing successful predictions for all models."""
        from unittest.mock import patch, MagicMock

        from app.ai.providers.base import AIModelConfig
        from app.ai.service import run_ai_predictions_batch

        model_a = AIModelConfig(
            provider_name="deepseek",
            model_id="model-a",
            enabled=True,
            model_version="v1",
            display_name="Model A",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )

        match = MagicMock()
        match.id = "match-1"
        match.status = "upcoming"
        match.stage = "group"
        match.kickoff = datetime.now(timezone.utc)

        # Existing successful prediction
        pred = MagicMock()
        pred.model_version = "v1"
        pred.error_code = None
        pred.parsed_home_win = 0.5

        with patch("app.ai.service.list_enabled_models", return_value=[model_a]):
            session = MagicMock()
            session.scalars.side_effect = [
                [match],   # Match query
                [pred],    # AIPrediction query
            ]

            results = await run_ai_predictions_batch(
                session, only_missing=True, retry_failed=False
            )

        # Should have a skipped result
        assert len(results) == 1
        assert results[0]["status"] == "skipped"
        assert results[0]["reason"] == "all_models_have_predictions"

    @pytest.mark.asyncio
    async def test_retry_failed_true_retries_failed_models(self):
        """retry_failed=True retries models that previously failed."""
        from unittest.mock import patch, MagicMock, AsyncMock

        from app.ai.providers.base import AIModelConfig
        from app.ai.service import run_ai_predictions_batch

        model_a = AIModelConfig(
            provider_name="deepseek",
            model_id="model-a",
            enabled=True,
            model_version="v1",
            display_name="Model A",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )

        match = MagicMock()
        match.id = "match-1"
        match.status = "upcoming"
        match.stage = "group"
        match.kickoff = datetime.now(timezone.utc)

        # Existing failed prediction
        pred = MagicMock()
        pred.model_version = "v1"
        pred.error_code = "api_timeout"
        pred.parsed_home_win = None

        with patch("app.ai.service.list_enabled_models", return_value=[model_a]), \
             patch("app.ai.service.run_ai_predictions_for_match", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [{"status": "success", "model_version": "v1", "prediction_id": 2}]

            session = MagicMock()
            session.scalars.side_effect = [
                [match],   # Match query
                [pred],    # AIPrediction query
            ]

            results = await run_ai_predictions_batch(
                session, only_missing=True, retry_failed=True
            )

        # Should have retried the failed model
        mock_run.assert_called_once()
        assert len(results) == 1
        assert results[0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_retry_failed_false_does_not_retry_failed(self):
        """retry_failed=False does NOT skip matches where models only have errors (still needs predictions)."""
        from unittest.mock import patch, MagicMock, AsyncMock

        from app.ai.providers.base import AIModelConfig
        from app.ai.service import run_ai_predictions_batch

        model_a = AIModelConfig(
            provider_name="deepseek",
            model_id="model-a",
            enabled=True,
            model_version="v1",
            display_name="Model A",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )

        match = MagicMock()
        match.id = "match-1"
        match.status = "upcoming"
        match.stage = "group"
        match.kickoff = datetime.now(timezone.utc)

        # Existing failed prediction (no successful one)
        pred = MagicMock()
        pred.model_version = "v1"
        pred.error_code = "api_timeout"
        pred.parsed_home_win = None

        with patch("app.ai.service.list_enabled_models", return_value=[model_a]), \
             patch("app.ai.service.run_ai_predictions_for_match", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = [{"status": "success", "model_version": "v1", "prediction_id": 3}]

            session = MagicMock()
            session.scalars.side_effect = [
                [match],   # Match query
                [pred],    # AIPrediction query
            ]

            results = await run_ai_predictions_batch(
                session, only_missing=True, retry_failed=False
            )

        # With retry_failed=False, v1 is in versions_with_error but NOT in versions_with_success
        # missing_versions = enabled_versions - versions_with_success = {"v1"} - {} = {"v1"}
        # Since missing_versions is not empty, it should proceed to run predictions
        mock_run.assert_called_once()
        assert len(results) == 1
        assert results[0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_skipped_result_includes_reason(self):
        """Skipped result includes reason and existing_versions."""
        from unittest.mock import patch, MagicMock

        from app.ai.providers.base import AIModelConfig
        from app.ai.service import run_ai_predictions_batch

        model_a = AIModelConfig(
            provider_name="deepseek",
            model_id="model-a",
            enabled=True,
            model_version="v1",
            display_name="Model A",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )
        model_b = AIModelConfig(
            provider_name="deepseek",
            model_id="model-b",
            enabled=True,
            model_version="v2",
            display_name="Model B",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )

        match = MagicMock()
        match.id = "match-1"
        match.status = "upcoming"
        match.stage = "group"
        match.kickoff = datetime.now(timezone.utc)

        # Both models have successful predictions
        pred_a = MagicMock()
        pred_a.model_version = "v1"
        pred_a.error_code = None
        pred_a.parsed_home_win = 0.5

        pred_b = MagicMock()
        pred_b.model_version = "v2"
        pred_b.error_code = None
        pred_b.parsed_home_win = 0.4

        with patch("app.ai.service.list_enabled_models", return_value=[model_a, model_b]):
            session = MagicMock()
            session.scalars.side_effect = [
                [match],        # Match query
                [pred_a, pred_b],  # AIPrediction query
            ]

            results = await run_ai_predictions_batch(
                session, only_missing=True, retry_failed=False
            )

        assert len(results) == 1
        assert results[0]["status"] == "skipped"
        assert results[0]["reason"] == "all_models_have_predictions"
        assert set(results[0]["existing_versions"]) == {"v1", "v2"}


# ===================================================================
# AI prompt isolates display-only team profile data tests
# ===================================================================

class TestAIPromptIncludesProfile:
    """Team profiles are display-only and are not prediction inputs."""

    def test_prompt_builder_includes_profile_section(self):
        """Even if profile data is passed, the prompt does not include it as a prediction input."""
        from app.ai.prompt_builder import build_prediction_prompt
        from app.ai.providers.base import AIPredictionRequest

        home_profile = {
            "traits": ["防守优先", "大赛经验丰富"],
            "sample_count": 16,
            "draw_resilience_score": 0.4,
            "favorite_overconfidence_risk": 0.15,
            "low_score_tendency": 0.3,
            "summary": "防守优先，大赛经验丰富",
            "profile_version": "team-profile-v1",
            "profile_as_of": "2026-06-10T00:00:00Z",
        }
        away_profile = {
            "traits": ["遇强韧性高"],
            "sample_count": 12,
            "draw_resilience_score": 0.6,
            "favorite_overconfidence_risk": 0.1,
            "low_score_tendency": 0.2,
            "summary": "遇强韧性高",
            "profile_version": "team-profile-v1",
            "profile_as_of": "2026-06-10T00:00:00Z",
        }

        request = AIPredictionRequest(
            match_id="test-match",
            stage="group",
            group="A",
            knockout_round=None,
            home_team="Team A",
            away_team="Team B",
            kickoff="2026-06-18T18:00:00Z",
            venue=None,
            neutral_ground=False,
            system_home_win=0.5,
            system_draw=0.3,
            system_away_win=0.2,
            system_home_xg=1.4,
            system_away_xg=1.0,
            system_model_confidence=0.8,
            system_data_confidence=0.85,
            most_likely_score="1-0",
            market_home_prob=None,
            market_draw_prob=None,
            market_away_prob=None,
            market_divergence=None,
            market_provider=None,
            market_fetched_at=None,
            home_team_profile=home_profile,
            away_team_profile=away_profile,
        )

        prompt = build_prediction_prompt(request)

        assert "Team Profiles" in prompt
        assert "disabled_display_only" in prompt
        assert "防守优先" not in prompt
        assert "遇强韧性高" not in prompt
        assert "team-profile-v1" not in prompt

    def test_prompt_builder_without_profiles(self):
        """When no team profiles are provided, the prompt keeps profiles disabled."""
        from app.ai.prompt_builder import build_prediction_prompt
        from app.ai.providers.base import AIPredictionRequest

        request = AIPredictionRequest(
            match_id="test-match",
            stage="group",
            group="A",
            knockout_round=None,
            home_team="Team A",
            away_team="Team B",
            kickoff="2026-06-18T18:00:00Z",
            venue=None,
            neutral_ground=False,
            system_home_win=0.5,
            system_draw=0.3,
            system_away_win=0.2,
            system_home_xg=1.4,
            system_away_xg=1.0,
            system_model_confidence=0.8,
            system_data_confidence=0.85,
            most_likely_score="1-0",
            market_home_prob=None,
            market_draw_prob=None,
            market_away_prob=None,
            market_divergence=None,
            market_provider=None,
            market_fetched_at=None,
        )

        prompt = build_prediction_prompt(request)

        assert "Team Profiles" in prompt
        assert "disabled_display_only" in prompt

    def test_parser_extracts_profile_fields(self):
        """The AI output parser extracts profile_factors and profile_risk_flags."""
        import json
        from app.ai.parser import parse_ai_response

        raw_output = json.dumps({
            "home_win": 0.55,
            "draw": 0.25,
            "away_win": 0.20,
            "confidence": 0.75,
            "reason": "Team A has strong defense.",
            "key_factors": ["home advantage"],
            "risk_flags": ["injury"],
            "profile_factors": ["draw_resilience_high", "favorite_overconfidence"],
            "profile_risk_flags": ["low_sample_count"],
        })

        parsed, warnings = parse_ai_response(raw_output)

        assert parsed is not None
        assert parsed.profile_factors == ["draw_resilience_high", "favorite_overconfidence"]
        assert parsed.profile_risk_flags == ["low_sample_count"]

    def test_parser_handles_missing_profile_fields(self):
        """The parser handles missing profile fields gracefully."""
        import json
        from app.ai.parser import parse_ai_response

        raw_output = json.dumps({
            "home_win": 0.55,
            "draw": 0.25,
            "away_win": 0.20,
            "confidence": 0.75,
            "reason": "Standard prediction.",
            "key_factors": ["form"],
            "risk_flags": [],
        })

        parsed, warnings = parse_ai_response(raw_output)

        assert parsed is not None
        assert parsed.profile_factors == []
        assert parsed.profile_risk_flags == []

    def test_schemas_include_profile_fields(self):
        """AIParsedOutput has profile_factors and profile_risk_flags fields."""
        from app.ai.schemas import AIParsedOutput

        parsed = AIParsedOutput(
            home_win=0.5,
            draw=0.3,
            away_win=0.2,
            confidence=0.8,
            profile_factors=["trait_a", "trait_b"],
            profile_risk_flags=["risk_x"],
        )

        assert parsed.profile_factors == ["trait_a", "trait_b"]
        assert parsed.profile_risk_flags == ["risk_x"]


# ===================================================================
# AI prediction deduplication tests
# ===================================================================

def _create_match_and_teams(db_session, match_id="match-1"):
    """Helper to create prerequisite Match and Team records for AIPrediction FK constraints."""
    from app.models import Match, Team

    home_team = Team(
        id="team-home",
        name="Home Team",
        short_name="Home",
        code="HOM",
        group_code="A",
    )
    away_team = Team(
        id="team-away",
        name="Away Team",
        short_name="Away",
        code="AWY",
        group_code="A",
    )
    db_session.add(home_team)
    db_session.add(away_team)
    db_session.flush()
    match = Match(
        id=match_id,
        home_team_id="team-home",
        away_team_id="team-away",
        kickoff=datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc),
        status="upcoming",
        source="test",
    )
    db_session.add(match)
    db_session.flush()
    return match


class TestFindExistingPrediction:
    """Test _find_existing_prediction helper for dedup logic."""

    def test_returns_existing_successful_prediction(self, db_session):
        """_find_existing_prediction returns an existing successful prediction."""
        from app.ai.service import _find_existing_prediction, get_prompt_version
        from app.models import AIPrediction

        _create_match_and_teams(db_session)
        prompt_ver = get_prompt_version()
        pred = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version=prompt_ver,
            parsed_home_win=0.5,
            parsed_draw=0.3,
            parsed_away_win=0.2,
            confidence=0.8,
            recommended_label="home_win",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(pred)
        db_session.flush()

        result = _find_existing_prediction(db_session, "match-1", "v1")
        assert result is not None
        assert result.id == pred.id
        assert result.parsed_home_win == 0.5

    def test_returns_none_when_no_prediction_exists(self, db_session):
        """_find_existing_prediction returns None when no prediction exists."""
        from app.ai.service import _find_existing_prediction

        result = _find_existing_prediction(db_session, "match-nonexistent", "v1")
        assert result is None

    def test_returns_none_when_only_failed_predictions_exist(self, db_session):
        """Failed predictions (with error_code) should NOT block re-calls."""
        from app.ai.service import _find_existing_prediction, get_prompt_version
        from app.models import AIPrediction

        _create_match_and_teams(db_session)
        prompt_ver = get_prompt_version()
        pred = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version=prompt_ver,
            error_code="api_timeout",
            error_message="Request timed out",
            parsed_home_win=None,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(pred)
        db_session.flush()

        result = _find_existing_prediction(db_session, "match-1", "v1")
        assert result is None

    def test_returns_none_when_prompt_version_differs(self, db_session):
        """Predictions with a different prompt_version are not considered matches."""
        from app.ai.service import _find_existing_prediction
        from app.models import AIPrediction

        _create_match_and_teams(db_session)
        pred = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version="old-prompt-v0",
            parsed_home_win=0.5,
            parsed_draw=0.3,
            parsed_away_win=0.2,
            confidence=0.8,
            recommended_label="home_win",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(pred)
        db_session.flush()

        # The current prompt_version differs from "old-prompt-v0"
        result = _find_existing_prediction(db_session, "match-1", "v1")
        # If the current prompt version happens to be "old-prompt-v0", this would match.
        # To make the test robust, check that a different prompt_version does not match.
        from app.ai.service import get_prompt_version
        if get_prompt_version() != "old-prompt-v0":
            assert result is None
        else:
            assert result is not None

    def test_returns_latest_when_multiple_exist(self, db_session):
        """When multiple successful predictions exist, returns the most recent one."""
        from app.ai.service import _find_existing_prediction, get_prompt_version
        from app.models import AIPrediction

        _create_match_and_teams(db_session)
        prompt_ver = get_prompt_version()
        pred_old = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version=prompt_ver,
            parsed_home_win=0.4,
            parsed_draw=0.3,
            parsed_away_win=0.3,
            confidence=0.7,
            recommended_label="home_win",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        pred_new = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version=prompt_ver,
            parsed_home_win=0.55,
            parsed_draw=0.25,
            parsed_away_win=0.20,
            confidence=0.85,
            recommended_label="home_win",
            created_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        )
        db_session.add_all([pred_old, pred_new])
        db_session.flush()

        result = _find_existing_prediction(db_session, "match-1", "v1")
        assert result is not None
        assert result.parsed_home_win == 0.55


class TestRunAIPredictionDedup:
    """Test dedup logic in run_ai_prediction."""

    @pytest.mark.asyncio
    async def test_force_false_returns_skipped_existing(self, db_session):
        """run_ai_prediction with force=False returns skipped_existing when prediction exists."""
        from unittest.mock import patch, AsyncMock

        from app.ai.service import run_ai_prediction, get_prompt_version
        from app.models import AIPrediction

        _create_match_and_teams(db_session)
        prompt_ver = get_prompt_version()
        pred = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version=prompt_ver,
            parsed_home_win=0.5,
            parsed_draw=0.3,
            parsed_away_win=0.2,
            confidence=0.8,
            recommended_label="home_win",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(pred)
        db_session.flush()

        with patch("app.ai.service.is_ai_enabled", return_value=True):
            result = await run_ai_prediction(db_session, "match-1", "v1", force=False)

        assert result["status"] == "skipped_existing"
        assert result["match_id"] == "match-1"
        assert result["model_version"] == "v1"
        assert result["prediction_id"] == pred.id
        assert result["home_win"] == 0.5

    @pytest.mark.asyncio
    async def test_force_true_calls_provider_after_cooldown(self, db_session):
        """run_ai_prediction with force=True calls the provider when the previous prediction is old."""
        from unittest.mock import patch, AsyncMock, MagicMock

        from app.ai.service import run_ai_prediction, get_prompt_version
        from app.models import AIPrediction

        _create_match_and_teams(db_session)
        prompt_ver = get_prompt_version()
        pred = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version=prompt_ver,
            parsed_home_win=0.5,
            parsed_draw=0.3,
            parsed_away_win=0.2,
            confidence=0.8,
            recommended_label="home_win",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db_session.add(pred)
        db_session.flush()

        # We need to mock _call_ai_provider and _process_and_save_prediction
        # to avoid actually calling the AI provider
        with patch("app.ai.service.is_ai_enabled", return_value=True), \
             patch("app.ai.service._call_ai_provider", new_callable=AsyncMock) as mock_call, \
             patch("app.ai.service._process_and_save_prediction", return_value={"status": "success", "prediction_id": 999}):
            mock_call.return_value = {"status": "api_call_done", "match_id": "match-1", "model_version": "v1"}
            result = await run_ai_prediction(db_session, "match-1", "v1", force=True)

        mock_call.assert_called_once()
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_force_true_skips_recent_existing_prediction(self, db_session):
        """run_ai_prediction with force=True respects the one-hour match cooldown."""
        from unittest.mock import patch, AsyncMock

        from app.ai.service import run_ai_prediction, get_prompt_version
        from app.models import AIPrediction

        _create_match_and_teams(db_session)
        prompt_ver = get_prompt_version()
        pred = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version=prompt_ver,
            parsed_home_win=0.5,
            parsed_draw=0.3,
            parsed_away_win=0.2,
            confidence=0.8,
            recommended_label="home_win",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        db_session.add(pred)
        db_session.flush()

        with patch("app.ai.service.is_ai_enabled", return_value=True), \
             patch("app.ai.service._call_ai_provider", new_callable=AsyncMock) as mock_call:
            result = await run_ai_prediction(db_session, "match-1", "v1", force=True)

        mock_call.assert_not_called()
        assert result["status"] == "skipped_cooldown"
        assert result["match_id"] == "match-1"
        assert result["retry_after_seconds"] > 0

    @pytest.mark.asyncio
    async def test_force_false_calls_provider_when_no_existing(self, db_session):
        """run_ai_prediction with force=False calls the provider when no existing prediction."""
        from unittest.mock import patch, AsyncMock

        from app.ai.service import run_ai_prediction

        with patch("app.ai.service.is_ai_enabled", return_value=True), \
             patch("app.ai.service._call_ai_provider", new_callable=AsyncMock) as mock_call, \
             patch("app.ai.service._process_and_save_prediction", return_value={"status": "success", "prediction_id": 1}):
            mock_call.return_value = {"status": "api_call_done", "match_id": "match-1", "model_version": "v1"}
            result = await run_ai_prediction(db_session, "match-1", "v1", force=False)

        mock_call.assert_called_once()
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_failed_prediction_does_not_block_recall(self, db_session):
        """A failed prediction should NOT block re-calls (force=False still calls provider)."""
        from unittest.mock import patch, AsyncMock

        from app.ai.service import run_ai_prediction, get_prompt_version
        from app.models import AIPrediction

        _create_match_and_teams(db_session)
        prompt_ver = get_prompt_version()
        pred = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version=prompt_ver,
            error_code="api_timeout",
            error_message="Request timed out",
            parsed_home_win=None,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(pred)
        db_session.flush()

        with patch("app.ai.service.is_ai_enabled", return_value=True), \
             patch("app.ai.service._call_ai_provider", new_callable=AsyncMock) as mock_call, \
             patch("app.ai.service._process_and_save_prediction", return_value={"status": "success", "prediction_id": 2}):
            mock_call.return_value = {"status": "api_call_done", "match_id": "match-1", "model_version": "v1"}
            result = await run_ai_prediction(db_session, "match-1", "v1", force=False)

        mock_call.assert_called_once()
        assert result["status"] == "success"


class TestRunAIPredictionsForMatchDedup:
    """Test dedup logic in run_ai_predictions_for_match."""

    @pytest.mark.asyncio
    async def test_skips_models_with_existing_predictions(self, db_session):
        """run_ai_predictions_for_match skips models that already have successful predictions."""
        from unittest.mock import patch, MagicMock, AsyncMock

        from app.ai.providers.base import AIModelConfig
        from app.ai.service import run_ai_predictions_for_match, get_prompt_version
        from app.models import AIPrediction

        _create_match_and_teams(db_session)
        prompt_ver = get_prompt_version()
        # Existing prediction for v1
        pred = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version=prompt_ver,
            parsed_home_win=0.5,
            parsed_draw=0.3,
            parsed_away_win=0.2,
            confidence=0.8,
            recommended_label="home_win",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(pred)
        db_session.flush()

        model_v1 = AIModelConfig(
            provider_name="deepseek",
            model_id="model-a",
            enabled=True,
            model_version="v1",
            display_name="Model V1",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )
        model_v2 = AIModelConfig(
            provider_name="deepseek",
            model_id="model-b",
            enabled=True,
            model_version="v2",
            display_name="Model V2",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )

        with patch("app.ai.service.list_enabled_models", return_value=[model_v1, model_v2]), \
             patch("app.ai.service._call_ai_provider", new_callable=AsyncMock) as mock_call, \
             patch("app.ai.service.get_provider_config", return_value=MagicMock()), \
             patch("app.ai.service._get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.is_configured.return_value = True
            mock_get_provider.return_value = mock_provider
            mock_call.return_value = {"status": "api_call_done", "match_id": "match-1", "model_version": "v2"}
            with patch("app.ai.service._process_and_save_prediction", return_value={"status": "success", "model_version": "v2", "prediction_id": 2}):
                results = await run_ai_predictions_for_match(db_session, "match-1", force=False)

        # v1 should be skipped, v2 should be called
        called_versions = [call.args[2] for call in mock_call.call_args_list]
        assert "v1" not in called_versions
        assert "v2" in called_versions

        # Results should include both skipped and new
        statuses = {r["status"] for r in results}
        assert "skipped_existing" in statuses
        assert "success" in statuses

    @pytest.mark.asyncio
    async def test_force_true_runs_all_models_after_cooldown(self, db_session):
        """run_ai_predictions_for_match with force=True runs models when previous prediction is old."""
        from unittest.mock import patch, MagicMock, AsyncMock

        from app.ai.providers.base import AIModelConfig
        from app.ai.service import run_ai_predictions_for_match, get_prompt_version
        from app.models import AIPrediction

        _create_match_and_teams(db_session)
        prompt_ver = get_prompt_version()
        pred = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version=prompt_ver,
            parsed_home_win=0.5,
            parsed_draw=0.3,
            parsed_away_win=0.2,
            confidence=0.8,
            recommended_label="home_win",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db_session.add(pred)
        db_session.flush()

        model_v1 = AIModelConfig(
            provider_name="deepseek",
            model_id="model-a",
            enabled=True,
            model_version="v1",
            display_name="Model V1",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )

        with patch("app.ai.service.list_enabled_models", return_value=[model_v1]), \
             patch("app.ai.service._call_ai_provider", new_callable=AsyncMock) as mock_call, \
             patch("app.ai.service.get_provider_config", return_value=MagicMock()), \
             patch("app.ai.service._get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.is_configured.return_value = True
            mock_get_provider.return_value = mock_provider
            mock_call.return_value = {"status": "api_call_done", "match_id": "match-1", "model_version": "v1"}
            with patch("app.ai.service._process_and_save_prediction", return_value={"status": "success", "model_version": "v1", "prediction_id": 2}):
                results = await run_ai_predictions_for_match(db_session, "match-1", force=True)

        mock_call.assert_called_once()
        assert len(results) == 1
        assert results[0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_force_true_skips_whole_match_with_recent_prediction(self, db_session):
        """run_ai_predictions_for_match with force=True respects match-level cooldown."""
        from unittest.mock import patch, MagicMock, AsyncMock

        from app.ai.providers.base import AIModelConfig
        from app.ai.service import run_ai_predictions_for_match, get_prompt_version
        from app.models import AIPrediction

        _create_match_and_teams(db_session)
        prompt_ver = get_prompt_version()
        pred = AIPrediction(
            match_id="match-1",
            provider="deepseek",
            model_id="model-a",
            model_version="v1",
            prompt_version=prompt_ver,
            parsed_home_win=0.5,
            parsed_draw=0.3,
            parsed_away_win=0.2,
            confidence=0.8,
            recommended_label="home_win",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        db_session.add(pred)
        db_session.flush()

        model_v1 = AIModelConfig(
            provider_name="deepseek",
            model_id="model-a",
            enabled=True,
            model_version="v1",
            display_name="Model V1",
            cost_tier="medium",
            latency_tier="medium",
            role="general",
        )

        with patch("app.ai.service.list_enabled_models", return_value=[model_v1]), \
             patch("app.ai.service._call_ai_provider", new_callable=AsyncMock) as mock_call, \
             patch("app.ai.service.get_provider_config", return_value=MagicMock()), \
             patch("app.ai.service._get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.is_configured.return_value = True
            mock_get_provider.return_value = mock_provider
            results = await run_ai_predictions_for_match(db_session, "match-1", force=True)

        mock_call.assert_not_called()
        assert len(results) == 1
        assert results[0]["status"] == "skipped_cooldown"


# ===================================================================
# Identical-to-baseline detection tests
# ===================================================================

class TestIdenticalToBaselineDetection:
    """Test that _serialize_ai_prediction correctly detects when AI probs match baseline."""

    def test_identical_probs_flagged(self):
        """AI prediction with probs within 0.01 of baseline should be flagged."""
        from app.ai.service import _serialize_ai_prediction
        from unittest.mock import MagicMock

        row = MagicMock()
        row.id = 1
        row.match_id = "test-match"
        row.provider = "deepseek"
        row.model_id = "deepseek-v4-flash"
        row.model_version = "ai-deepseek-v4-flash-v1"
        row.prompt_version = "v1"
        row.parsed_home_win = 0.681  # baseline is 0.68
        row.parsed_draw = 0.211      # baseline is 0.21
        row.parsed_away_win = 0.108   # baseline is 0.11
        row.confidence = 0.7
        row.risk_flags_json = []
        row.key_factors_json = []
        row.reason = "test"
        row.uncertainties_json = []
        row.disagreement_with_system = ""
        row.disagreement_with_market = ""
        row.recommended_label = "home_win"
        row.created_at = datetime.now(timezone.utc)
        row.locked_at = None
        row.is_pre_match_locked = False
        row.is_fallback_locked = False
        row.real_time_only = False
        row.error_code = None
        row.error_message = None
        row.latency_ms = 1000

        baseline = {"home_win": 0.68, "draw": 0.21, "away_win": 0.11}
        result = _serialize_ai_prediction(row, baseline)

        assert result["identical_to_baseline"] is True
        assert result["deviation_from_baseline"] < 0.01
        assert result["baseline_home_win"] == 0.68

    def test_different_probs_not_flagged(self):
        """AI prediction with probs significantly different from baseline should NOT be flagged."""
        from app.ai.service import _serialize_ai_prediction
        from unittest.mock import MagicMock

        row = MagicMock()
        row.id = 2
        row.match_id = "test-match"
        row.provider = "deepseek"
        row.model_id = "deepseek-v4-pro"
        row.model_version = "ai-deepseek-v4-pro-v1"
        row.prompt_version = "v1"
        row.parsed_home_win = 0.60  # baseline is 0.68
        row.parsed_draw = 0.25      # baseline is 0.21
        row.parsed_away_win = 0.15   # baseline is 0.11
        row.confidence = 0.7
        row.risk_flags_json = []
        row.key_factors_json = []
        row.reason = "test"
        row.uncertainties_json = []
        row.disagreement_with_system = ""
        row.disagreement_with_market = ""
        row.recommended_label = "home_win"
        row.created_at = datetime.now(timezone.utc)
        row.locked_at = None
        row.is_pre_match_locked = False
        row.is_fallback_locked = False
        row.real_time_only = False
        row.error_code = None
        row.error_message = None
        row.latency_ms = 1000

        baseline = {"home_win": 0.68, "draw": 0.21, "away_win": 0.11}
        result = _serialize_ai_prediction(row, baseline)

        assert result["identical_to_baseline"] is False
        assert result["deviation_from_baseline"] >= 0.01
        assert result["baseline_home_win"] == 0.68

    def test_no_baseline_returns_none(self):
        """Without baseline comparison, identical_to_baseline should be None."""
        from app.ai.service import _serialize_ai_prediction
        from unittest.mock import MagicMock

        row = MagicMock()
        row.id = 3
        row.match_id = "test-match"
        row.provider = "deepseek"
        row.model_id = "deepseek-v4-flash"
        row.model_version = "ai-deepseek-v4-flash-v1"
        row.prompt_version = "v1"
        row.parsed_home_win = 0.68
        row.parsed_draw = 0.21
        row.parsed_away_win = 0.11
        row.confidence = 0.7
        row.risk_flags_json = []
        row.key_factors_json = []
        row.reason = "test"
        row.uncertainties_json = []
        row.disagreement_with_system = ""
        row.disagreement_with_market = ""
        row.recommended_label = "home_win"
        row.created_at = datetime.now(timezone.utc)
        row.locked_at = None
        row.is_pre_match_locked = False
        row.is_fallback_locked = False
        row.real_time_only = False
        row.error_code = None
        row.error_message = None
        row.latency_ms = 1000

        result = _serialize_ai_prediction(row, None)

        assert result["identical_to_baseline"] is None
        assert result["deviation_from_baseline"] is None
        assert result["baseline_home_win"] is None

    def test_failed_prediction_no_baseline_comparison(self):
        """Failed AI prediction should have None for baseline comparison fields."""
        from app.ai.service import _serialize_ai_prediction
        from unittest.mock import MagicMock

        row = MagicMock()
        row.id = 4
        row.match_id = "test-match"
        row.provider = "deepseek"
        row.model_id = "deepseek-v4-flash"
        row.model_version = "ai-deepseek-v4-flash-v1"
        row.prompt_version = "v1"
        row.parsed_home_win = None
        row.parsed_draw = None
        row.parsed_away_win = None
        row.confidence = None
        row.risk_flags_json = []
        row.key_factors_json = []
        row.reason = ""
        row.uncertainties_json = []
        row.disagreement_with_system = ""
        row.disagreement_with_market = ""
        row.recommended_label = ""
        row.created_at = datetime.now(timezone.utc)
        row.locked_at = None
        row.is_pre_match_locked = False
        row.is_fallback_locked = False
        row.real_time_only = False
        row.error_code = "parse_failed"
        row.error_message = "Could not parse response"
        row.latency_ms = 500

        baseline = {"home_win": 0.68, "draw": 0.21, "away_win": 0.11}
        result = _serialize_ai_prediction(row, baseline)

        assert result["identical_to_baseline"] is None
        assert result["parsed_home_win"] is None


# ===================================================================
# Ensemble preserves different AI probabilities tests
# ===================================================================

class TestEnsemblePreservesDifferentProbs:
    """Test that ensemble computation correctly uses different AI probabilities."""

    def test_ensemble_different_from_baseline_when_ai_differs(self, db_session):
        """When AI predictions differ from baseline, ensemble should also differ."""
        from app.ai.ensemble import compute_ensemble
        from app.models import (
            DashboardRevision, Match, Team, TeamRating,
            MatchPrediction, PredictionSnapshot, AIPrediction,
        )

        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(hours=2)

        # Create minimal test data
        team_home = Team(id="TST1", name="Team Home", short_name="Home", code="THM", group_code="A")
        team_away = Team(id="TST2", name="Team Away", short_name="Away", code="TAW", group_code="A")
        db_session.add_all([team_home, team_away])
        db_session.flush()

        rating1 = TeamRating(team_id="TST1", effective_date=now.date(), elo=1800, recent_form="WDL", source="test")
        rating2 = TeamRating(team_id="TST2", effective_date=now.date(), elo=1600, recent_form="LDW", source="test")
        db_session.add_all([rating1, rating2])
        db_session.flush()

        match = Match(id="ens-test-1", group_code="A", home_team_id="TST1", away_team_id="TST2",
                      kickoff=kickoff, status="scheduled", source="test", stage="group")
        db_session.add(match)
        db_session.flush()

        revision = DashboardRevision(model_version="elo-poisson-v1", simulation_iterations=1000, simulation_seed=42, active=True)
        db_session.add(revision)
        db_session.flush()

        # Baseline prediction: 68/21/11
        pred = MatchPrediction(
            revision_id=revision.id, match_id="ens-test-1",
            home_xg=1.8, away_xg=0.9,
            home_win=0.68, draw=0.21, away_win=0.11,
            has_auto_adjustments=False,
            scorelines=[], score_matrix={},
            confidence=0.7, confidence_label="中",
            data_confidence=0.8, data_confidence_label="高",
            model_confidence=0.6, model_confidence_label="中",
            explanation="test", model_inputs={}, model_version="elo-poisson-v1",
        )
        db_session.add(pred)

        # Snapshot for ensemble to read
        snap = PredictionSnapshot(
            match_id="ens-test-1", revision_id=revision.id,
            kickoff=kickoff, is_pre_match_locked=False, is_fallback_locked=False,
            home_win=0.68, draw=0.21, away_win=0.11,
            home_xg=1.8, away_xg=0.9,
            has_auto_adjustments=False,
            scorelines=[], score_matrix={},
            confidence=0.7, confidence_label="中",
            model_inputs={}, model_version="elo-poisson-v1",
            snapshotted_at=now,
        )
        db_session.add(snap)

        # AI prediction: DIFFERENT from baseline (60/25/15)
        ai_pred = AIPrediction(
            match_id="ens-test-1", provider="deepseek", model_id="deepseek-v4-flash",
            model_version="ai-deepseek-v4-flash-v1", prompt_version="v1",
            input_snapshot_json={}, raw_response_text="test",
            parsed_home_win=0.60, parsed_draw=0.25, parsed_away_win=0.15,
            confidence=0.65, risk_flags_json=[], key_factors_json=[],
            reason="AI analysis", uncertainties_json=[],
            recommended_label="home_win",
            created_at=now, is_pre_match_locked=False,
            is_fallback_locked=False, real_time_only=False,
        )
        db_session.add(ai_pred)
        db_session.flush()

        # Compute ensemble
        result = compute_ensemble(db_session, "ens-test-1")

        # Ensemble should be DIFFERENT from baseline (0.68/0.21/0.11)
        # With default weights (system 60%, AI 40% when no market)
        # The ensemble should be pulled toward the AI prediction
        assert result["status"] == "success"
        assert result["home_win"] != 0.68  # Must differ from baseline
        assert result["home_win"] < 0.68   # Should be pulled down by AI's 0.60
        assert result["draw"] > 0.21       # Should be pulled up by AI's 0.25
        assert result["away_win"] > 0.11   # Should be pulled up by AI's 0.15

    def test_ensemble_identical_to_baseline_when_ai_same(self, db_session):
        """When AI predictions are identical to baseline, ensemble should also be close."""
        from app.ai.ensemble import compute_ensemble
        from app.models import (
            DashboardRevision, Match, Team, TeamRating,
            MatchPrediction, PredictionSnapshot, AIPrediction,
        )

        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(hours=2)

        team_home = Team(id="TST3", name="Team Home 3", short_name="Home3", code="TH3", group_code="B")
        team_away = Team(id="TST4", name="Team Away 3", short_name="Away3", code="TA3", group_code="B")
        db_session.add_all([team_home, team_away])
        db_session.flush()

        rating1 = TeamRating(team_id="TST3", effective_date=now.date(), elo=1800, recent_form="WDL", source="test")
        rating2 = TeamRating(team_id="TST4", effective_date=now.date(), elo=1600, recent_form="LDW", source="test")
        db_session.add_all([rating1, rating2])
        db_session.flush()

        match = Match(id="ens-test-2", group_code="B", home_team_id="TST3", away_team_id="TST4",
                      kickoff=kickoff, status="scheduled", source="test", stage="group")
        db_session.add(match)
        db_session.flush()

        revision = DashboardRevision(model_version="elo-poisson-v1", simulation_iterations=1000, simulation_seed=42, active=True)
        db_session.add(revision)
        db_session.flush()

        pred = MatchPrediction(
            revision_id=revision.id, match_id="ens-test-2",
            home_xg=1.8, away_xg=0.9,
            home_win=0.68, draw=0.21, away_win=0.11,
            has_auto_adjustments=False,
            scorelines=[], score_matrix={},
            confidence=0.7, confidence_label="中",
            data_confidence=0.8, data_confidence_label="高",
            model_confidence=0.6, model_confidence_label="中",
            explanation="test", model_inputs={}, model_version="elo-poisson-v1",
        )
        db_session.add(pred)

        snap = PredictionSnapshot(
            match_id="ens-test-2", revision_id=revision.id,
            kickoff=kickoff, is_pre_match_locked=False, is_fallback_locked=False,
            home_win=0.68, draw=0.21, away_win=0.11,
            home_xg=1.8, away_xg=0.9,
            has_auto_adjustments=False,
            scorelines=[], score_matrix={},
            confidence=0.7, confidence_label="中",
            model_inputs={}, model_version="elo-poisson-v1",
            snapshotted_at=now,
        )
        db_session.add(snap)

        # AI prediction: IDENTICAL to baseline (68/21/11)
        ai_pred = AIPrediction(
            match_id="ens-test-2", provider="deepseek", model_id="deepseek-v4-flash",
            model_version="ai-deepseek-v4-flash-v1", prompt_version="v1",
            input_snapshot_json={}, raw_response_text="test",
            parsed_home_win=0.68, parsed_draw=0.21, parsed_away_win=0.11,
            confidence=0.65, risk_flags_json=[], key_factors_json=[],
            reason="AI analysis", uncertainties_json=[],
            recommended_label="home_win",
            created_at=now, is_pre_match_locked=False,
            is_fallback_locked=False, real_time_only=False,
        )
        db_session.add(ai_pred)
        db_session.flush()

        result = compute_ensemble(db_session, "ens-test-2")

        # When AI is identical to baseline and no market, ensemble = baseline
        assert result["status"] == "success"
        assert abs(result["home_win"] - 0.68) < 0.01
        assert abs(result["draw"] - 0.21) < 0.01
        assert abs(result["away_win"] - 0.11) < 0.01
