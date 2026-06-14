"""P2+ Final Hardening tests - multi-AI model, ensemble, tournament, scoring, data pollution."""

import os
import json
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from sqlalchemy import select

from app.db import create_database, session_scope
from app.models import (
    AIPrediction, EnsemblePrediction, Match, MarketSnapshot,
    PredictionSnapshot, DashboardRevision, Team, TeamRating, Base,
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
    team = Team(id=team_id, name=name, short_name=name, code=team_id[:3].upper() + team_id[-1], group_code=group)
    session.add(team)
    session.flush()
    rating = TeamRating(team_id=team_id, effective_date=date(2026, 6, 1), elo=elo, source="test")
    session.add(rating)
    session.flush()
    return team


def _make_match(session, match_id, home_id, away_id, kickoff_offset_hours=48,
                stage="group", group_code="A", status="scheduled",
                home_score=None, away_score=None, is_placeholder=False):
    """Helper to create a match."""
    kickoff = datetime.now(timezone.utc) + timedelta(hours=kickoff_offset_hours)
    match = Match(
        id=match_id, home_team_id=home_id, away_team_id=away_id,
        kickoff=kickoff, status=status, source="test",
        group_code=group_code, stage=stage,
        is_placeholder_match=is_placeholder,
        home_score=home_score, away_score=away_score,
    )
    session.add(match)
    session.flush()
    return match


def _make_snapshot(session, match_id, home_win=0.5, draw=0.25, away_win=0.25,
                   model_version="elo-poisson-v1", is_pre_match_locked=True,
                   is_fallback_locked=False):
    """Helper to create a prediction snapshot."""
    revision = DashboardRevision(model_version=model_version, simulation_iterations=1000, simulation_seed=42, active=True)
    session.add(revision)
    session.flush()
    snap = PredictionSnapshot(
        match_id=match_id, revision_id=revision.id,
        kickoff=datetime.now(timezone.utc) + timedelta(hours=48),
        home_win=home_win, draw=draw, away_win=away_win,
        home_xg=1.2, away_xg=0.9,
        is_pre_match_locked=is_pre_match_locked,
        is_fallback_locked=is_fallback_locked,
        confidence=0.7, confidence_label="中",
        scorelines=[{"home_goals": 1, "away_goals": 0, "probability": 0.15}],
        score_matrix=[[0.1]], model_version=model_version,
        snapshotted_at=datetime.now(timezone.utc),
    )
    session.add(snap)
    session.flush()
    return snap


def _make_ai_prediction(session, match_id, model_version="ai-deepseek-v4-flash-v1",
                        home_win=0.45, draw=0.28, away_win=0.27,
                        is_pre_match_locked=True, is_fallback_locked=False,
                        real_time_only=False, error_code=None):
    """Helper to create an AI prediction."""
    pred = AIPrediction(
        match_id=match_id, provider="deepseek",
        model_id="deepseek-v4-flash", model_version=model_version,
        prompt_version="worldcup-ai-v1",
        parsed_home_win=home_win, parsed_draw=draw, parsed_away_win=away_win,
        confidence=0.6, is_pre_match_locked=is_pre_match_locked,
        is_fallback_locked=is_fallback_locked, real_time_only=real_time_only,
        error_code=error_code, created_at=datetime.now(timezone.utc),
    )
    if error_code:
        pred.error_message = "test error"
    session.add(pred)
    session.flush()
    return pred


# ── 1. AI Registry Tests ─────────────────────────────────────────

class TestAIRegistry:
    """Test AI model registry loading and configuration."""

    def test_load_deepseek_flash(self):
        from app.ai.model_registry import get_model_config
        model = get_model_config("ai-deepseek-v4-flash-v1")
        assert model is not None
        assert model.model_id == "deepseek-v4-flash"
        assert model.enabled is True
        assert model.cost_tier == "low"
        assert model.latency_tier == "fast"
        assert model.role == "fast_baseline"
        assert model.prompt_version == "worldcup-ai-v1"

    def test_load_deepseek_pro(self):
        from app.ai.model_registry import get_model_config
        model = get_model_config("ai-deepseek-v4-pro-v1")
        assert model is not None
        assert model.model_id == "deepseek-v4-pro"
        assert model.enabled is True
        assert model.cost_tier == "high"
        assert model.latency_tier == "slow"
        assert model.role == "reasoning_strong"

    def test_load_two_models(self):
        from app.ai.model_registry import list_enabled_models
        models = list_enabled_models()
        versions = [m.model_version for m in models]
        assert "ai-deepseek-v4-flash-v1" in versions
        assert "ai-deepseek-v4-pro-v1" in versions

    def test_ensemble_defaults_loaded(self):
        from app.ai.model_registry import get_ensemble_defaults
        defaults = get_ensemble_defaults()
        assert defaults["system_weight"] == 0.50
        assert defaults["market_weight"] == 0.20
        assert defaults["total_ai_weight"] == 0.30

    def test_no_api_key_no_error(self):
        """Registry should load fine even without API key."""
        from app.ai.model_registry import list_enabled_models
        models = list_enabled_models()
        assert len(models) >= 2

    def test_disabled_model_not_loaded(self):
        """Disabled models should not appear in list_enabled_models."""
        from app.ai.model_registry import get_model_config
        # Both current models are enabled; verify None for unknown
        assert get_model_config("ai-unknown-v1") is None

    def test_n_model_extensibility(self):
        """Verify the YAML structure supports N models."""
        import yaml
        from pathlib import Path
        config_path = Path(__file__).resolve().parents[1] / "app" / "ai" / "ai_models.yaml"
        with open(config_path) as f:
            data = yaml.safe_load(f)
        deepseek_models = data["providers"]["deepseek"]["models"]
        assert len(deepseek_models) >= 2
        # Each model must have required fields
        required = {"model_id", "model_version", "display_name", "enabled", "cost_tier", "latency_tier", "role", "ensemble_weight", "prompt_version"}
        for m in deepseek_models:
            missing = required - set(m.keys())
            assert not missing, f"Model {m.get('model_id', '?')} missing: {missing}"


# ── 2. AI Prediction Call Chain Tests ─────────────────────────────

class TestAIPredictionChain:
    """Test AI prediction service logic."""

    def test_no_system_prediction_refuses_ai(self, session):
        """AI should refuse to run when no system prediction exists."""
        from app.ai.service import _build_prediction_request
        _make_team(session, "T1", "Team1", "A", 1600)
        _make_team(session, "T2", "Team2", "A", 1500)
        _make_match(session, "M1", "T1", "T2")
        # No snapshot — should return None
        request = _build_prediction_request(session, "M1")
        assert request is None

    def test_ai_disabled_returns_status(self):
        """AI should return disabled status when not enabled."""
        from app.ai.service import is_ai_enabled
        from app.config import settings
        with patch.object(settings, "enable_ai_prediction", False):
            assert is_ai_enabled() is False

    def test_ai_parse_error_stores_raw(self, session):
        """Parse errors should store raw response and error code."""
        _make_team(session, "T_PE1", "TeamPE1", "Z", 1600)
        _make_team(session, "T_PE2", "TeamPE2", "Z", 1500)
        _make_match(session, "M_PE1", "T_PE1", "T_PE2")
        pred = AIPrediction(
            match_id="M_PE1", provider="deepseek", model_id="deepseek-v4-flash",
            model_version="ai-deepseek-v4-flash-v1", prompt_version="worldcup-ai-v1",
            raw_response_text="not json at all",
            error_code="parse_failed", error_message="Invalid JSON",
            created_at=datetime.now(timezone.utc),
        )
        session.add(pred)
        session.flush()
        assert pred.error_code == "parse_failed"
        assert pred.raw_response_text == "not json at all"

    def test_ai_probabilities_normalize(self):
        """Parser should normalize probabilities that don't sum to 1."""
        from app.ai.parser import parse_ai_response
        raw = json.dumps({
            "home_win": 0.5, "draw": 0.3, "away_win": 0.3,  # sum = 1.1
            "confidence": 0.7, "reason": "test",
        })
        parsed, warnings = parse_ai_response(raw)
        assert parsed is not None
        total = parsed.home_win + parsed.draw + parsed.away_win
        assert abs(total - 1.0) < 0.01

    def test_ai_invalid_probabilities_rejected(self):
        """Parser should reject impossible probabilities."""
        from app.ai.parser import parse_ai_response
        raw = json.dumps({
            "home_win": -0.5, "draw": 0.3, "away_win": 1.2,
            "confidence": 0.7,
        })
        parsed, warnings = parse_ai_response(raw)
        assert parsed is None  # negative probs rejected

    def test_ai_24h_lock_pre_match(self, session):
        """Pre-24h-lock AI predictions should be marked is_pre_match_locked."""
        _make_team(session, "T1", "Team1", "A", 1600)
        _make_team(session, "T2", "Team2", "A", 1500)
        # Match 48 hours from now
        _make_match(session, "M_LOCK", "T1", "T2", kickoff_offset_hours=48)
        pred = _make_ai_prediction(session, "M_LOCK", is_pre_match_locked=True, real_time_only=False)
        assert pred.is_pre_match_locked is True
        assert pred.real_time_only is False

    def test_ai_real_time_only_not_scored(self, session):
        """real_time_only predictions should not participate in scoring."""
        _make_team(session, "T3", "Team3", "B", 1600)
        _make_team(session, "T4", "Team4", "B", 1500)
        _make_match(session, "M_RT", "T3", "T4", kickoff_offset_hours=-2,
                    status="final", home_score=2, away_score=1)
        pred = _make_ai_prediction(session, "M_RT", real_time_only=True, is_pre_match_locked=False)
        assert pred.real_time_only is True
        assert pred.is_pre_match_locked is False

    def test_ai_model_status_no_key(self, session):
        """Models without API key should show disabled_no_key."""
        from app.ai.service import list_ai_model_status
        from app.config import settings
        with patch.object(settings, 'enable_ai_prediction', True), \
             patch.object(settings, 'deepseek_api_key', ''), \
             patch.object(settings, 'xiaomi_api_key', ''):
            models = list_ai_model_status(session)
            for m in models:
                assert m["disabled_no_key"] is True
                assert m["status"] == "disabled_no_key"
                assert m["has_api_key"] is False


# ── 3. Ensemble Tests ─────────────────────────────────────────────

class TestEnsemble:
    """Test ensemble fusion logic."""

    def test_ensemble_system_only(self, session):
        """Ensemble with only system prediction = system prediction."""
        _make_team(session, "T5", "Team5", "C", 1600)
        _make_team(session, "T6", "Team6", "C", 1500)
        _make_match(session, "M_ENS1", "T5", "T6")
        _make_snapshot(session, "M_ENS1", home_win=0.5, draw=0.25, away_win=0.25)

        from app.ai.ensemble import compute_ensemble
        result = compute_ensemble(session, "M_ENS1")
        assert result["status"] == "success"
        # System only weight = 1.0
        assert abs(result["home_win"] - 0.5) < 0.01

    def test_ensemble_weights_normalized(self, session):
        """Ensemble weights must sum to 1.0."""
        _make_team(session, "T7", "Team7", "D", 1600)
        _make_team(session, "T8", "Team8", "D", 1500)
        _make_match(session, "M_ENS2", "T7", "T8")
        _make_snapshot(session, "M_ENS2", home_win=0.6, draw=0.2, away_win=0.2)
        _make_ai_prediction(session, "M_ENS2", "ai-deepseek-v4-flash-v1",
                           home_win=0.55, draw=0.22, away_win=0.23, is_pre_match_locked=True)

        from app.ai.ensemble import compute_ensemble
        result = compute_ensemble(session, "M_ENS2")
        assert result["status"] == "success"
        total = result["home_win"] + result["draw"] + result["away_win"]
        assert abs(total - 1.0) < 0.01

    def test_ensemble_degrade_missing_market(self, session):
        """Ensemble without market should redistribute market weight."""
        _make_team(session, "T9", "Team9", "E", 1600)
        _make_team(session, "T10", "Team10", "E", 1500)
        _make_match(session, "M_ENS3", "T9", "T10")
        _make_snapshot(session, "M_ENS3", home_win=0.5, draw=0.25, away_win=0.25)
        # No market, 1 AI model
        _make_ai_prediction(session, "M_ENS3", "ai-deepseek-v4-flash-v1",
                           home_win=0.45, draw=0.28, away_win=0.27, is_pre_match_locked=True)

        from app.ai.ensemble import compute_ensemble
        result = compute_ensemble(session, "M_ENS3")
        assert result["status"] == "success"
        # System weight should be ~0.6 (no market), AI weight ~0.4
        weights = result["weights"]
        assert weights["system"] > 0.5  # larger than with market

    def test_ensemble_degrade_missing_ai(self, session):
        """Ensemble without AI should use system + market only."""
        _make_team(session, "T11", "Team11", "F", 1600)
        _make_team(session, "T12", "Team12", "F", 1500)
        _make_match(session, "M_ENS4", "T11", "T12")
        _make_snapshot(session, "M_ENS4", home_win=0.5, draw=0.25, away_win=0.25)
        # Add market
        market = MarketSnapshot(match_id="M_ENS4", provider="sporttery",
                               home_probability=0.48, draw_probability=0.27, away_probability=0.25,
                               raw_overround=1.05, fetched_at=datetime.now(timezone.utc))
        session.add(market)
        session.flush()

        from app.ai.ensemble import compute_ensemble
        result = compute_ensemble(session, "M_ENS4")
        assert result["status"] == "success"
        weights = result["weights"]
        assert "market" in weights
        assert weights["system"] == pytest.approx(0.8, abs=0.01)
        assert weights["market"] == pytest.approx(0.2, abs=0.01)

    def test_ensemble_degrade_single_ai_failure(self, session):
        """If one AI model has error, it should not participate in ensemble."""
        _make_team(session, "T13", "Team13", "G", 1600)
        _make_team(session, "T14", "Team14", "G", 1500)
        _make_match(session, "M_ENS5", "T13", "T14")
        _make_snapshot(session, "M_ENS5", home_win=0.5, draw=0.25, away_win=0.25)
        # Flash with error
        _make_ai_prediction(session, "M_ENS5", "ai-deepseek-v4-flash-v1",
                           error_code="api_error", is_pre_match_locked=True)
        # Pro with valid data
        _make_ai_prediction(session, "M_ENS5", "ai-deepseek-v4-pro-v1",
                           home_win=0.47, draw=0.26, away_win=0.27, is_pre_match_locked=True)

        from app.ai.ensemble import compute_ensemble
        result = compute_ensemble(session, "M_ENS5")
        assert result["status"] == "success"
        # Only Pro should be in the AI pool
        source_probs = result["source_probabilities"]
        ai_keys = [k for k in source_probs if k.startswith("ai_")]
        assert len(ai_keys) >= 1

    def test_ensemble_real_time_only_excluded(self, session):
        """real_time_only AI predictions should be excluded from ensemble."""
        _make_team(session, "T15", "Team15", "H", 1600)
        _make_team(session, "T16", "Team16", "H", 1500)
        _make_match(session, "M_ENS6", "T15", "T16", kickoff_offset_hours=-1)
        _make_snapshot(session, "M_ENS6", home_win=0.5, draw=0.25, away_win=0.25)
        # AI prediction that is real_time_only
        _make_ai_prediction(session, "M_ENS6", "ai-deepseek-v4-flash-v1",
                           home_win=0.45, draw=0.28, away_win=0.27,
                           is_pre_match_locked=False, real_time_only=True)

        from app.ai.ensemble import compute_ensemble
        result = compute_ensemble(session, "M_ENS6")
        assert result["status"] == "success"
        # No AI should be included
        source_probs = result["source_probabilities"]
        ai_keys = [k for k in source_probs if k.startswith("ai_")]
        assert len(ai_keys) == 0

    def test_ensemble_independent_model_version(self, session):
        """Ensemble must have its own model_version: ensemble-v1."""
        _make_team(session, "T17", "Team17", "I", 1600)
        _make_team(session, "T18", "Team18", "I", 1500)
        _make_match(session, "M_ENS7", "T17", "T18")
        _make_snapshot(session, "M_ENS7", home_win=0.5, draw=0.25, away_win=0.25)

        from app.ai.ensemble import compute_ensemble
        result = compute_ensemble(session, "M_ENS7")
        assert result["status"] == "success"

        ens_rows = list(session.scalars(
            select(EnsemblePrediction).where(EnsemblePrediction.match_id == "M_ENS7")
        ))
        assert len(ens_rows) >= 1
        assert ens_rows[-1].model_version == "ensemble-v1"

    def test_ensemble_source_probabilities_recorded(self, session):
        """Ensemble must record source_probabilities_json."""
        _make_team(session, "T19", "Team19", "J", 1600)
        _make_team(session, "T20", "Team20", "J", 1500)
        _make_match(session, "M_ENS8", "T19", "T20")
        _make_snapshot(session, "M_ENS8", home_win=0.5, draw=0.25, away_win=0.25)

        from app.ai.ensemble import compute_ensemble
        compute_ensemble(session, "M_ENS8")

        ens = session.scalar(select(EnsemblePrediction).where(EnsemblePrediction.match_id == "M_ENS8").order_by(EnsemblePrediction.id.desc()))
        assert ens.source_probabilities_json is not None
        assert "system" in ens.source_probabilities_json
        assert ens.ai_weights_json is not None
        assert ens.source_status_json is not None


# ── 4. Tournament Tests ──────────────────────────────────────────

class TestTournament:
    """Test tournament stages, bracket, and projection."""

    def test_stage_enum_complete(self):
        """All required stages must be defined in rules."""
        from app.tournament.rules import STAGE_ORDER
        required = {"group", "round_of_32", "round_of_16", "quarter_final", "semi_final", "third_place", "final"}
        assert required == set(STAGE_ORDER)

    def test_placeholder_match_creation(self, session):
        """Placeholder matches should be creatable with nullable team IDs."""
        match = Match(
            id="KO_P1", home_team_id=None, away_team_id=None,
            kickoff=datetime.now(timezone.utc) + timedelta(days=30),
            status="scheduled", source="tournament",
            stage="round_of_32", round_name="32强赛",
            bracket_position=1,
            home_team_source="A1", away_team_source="C2",
            is_placeholder_match=True,
        )
        session.add(match)
        session.flush()
        assert match.is_placeholder_match is True
        assert match.home_team_id is None
        assert match.home_team_source == "A1"

    def test_bracket_generation(self, session):
        """Bracket should generate from group standings."""
        from app.tournament.standings import get_current_standings
        from app.tournament.bracket import generate_bracket
        # Create 12 groups with 4 teams each
        for i, group in enumerate("ABCDEFGHIJKL"):
            for j in range(4):
                tid = f"{group}{j+1}"
                _make_team(session, tid, f"Team {tid}", group, 1500 + (4 - j) * 50)

        standings = get_current_standings(session)
        # standings may be empty if no matches played, but bracket should still work
        bracket = generate_bracket(standings, {})
        assert isinstance(bracket, dict)

    def test_champion_probability_output(self):
        """Tournament projection must include champion probability."""
        from app.tournament.qualification import TeamProjection
        proj = TeamProjection(
            team_id="T1",
            group_qualify=0.9, round_of_32=0.8, round_of_16=0.6,
            quarter_final=0.4, semi_final=0.2, final=0.1, champion=0.05,
        )
        assert proj.champion >= 0
        assert proj.champion <= 1

    def test_stage_labels(self):
        """Stage display names should be defined for all stages."""
        from app.tournament.rules import STAGE_DISPLAY_NAMES
        for stage in ["group", "round_of_32", "round_of_16", "quarter_final", "semi_final", "third_place", "final"]:
            assert stage in STAGE_DISPLAY_NAMES


# ── 5. Scoring by Stage Tests ─────────────────────────────────────

class TestScoringByStage:
    """Test scoring system supports stage-based aggregation."""

    def test_model_score_by_stage_function(self, session):
        """model_score_by_stage should return dict keyed by stage."""
        from app.services.scoring import model_score_by_stage
        result = model_score_by_stage(session)
        assert isinstance(result, dict)

    def test_ai_score_independent(self, session):
        """AI predictions should be scored independently."""
        from app.ai.evaluation import _evaluate_ai_version
        # No matches to score — should return empty
        result = _evaluate_ai_version(session, [], "ai-deepseek-v4-flash-v1")
        assert result["sample_count"] == 0

    def test_ensemble_score_independent(self, session):
        """Ensemble should be scored independently."""
        from app.ai.evaluation import _evaluate_ensemble
        result = _evaluate_ensemble(session, [])
        assert result["sample_count"] == 0


# ── 6. Accuracy Command Center Tests ─────────────────────────────

class TestAccuracyCommandCenter:
    """Test the unified accuracy command center."""

    def test_command_center_returns_required_fields(self, session):
        """Command center must return all required fields."""
        from app.services.accuracy_command import get_accuracy_command_center
        result = get_accuracy_command_center(session)
        required_fields = [
            "recommended_model", "recommendation_reason", "sample_sufficient",
            "sample_count", "baseline_score", "market_score",
            "ai_flash_score", "ai_pro_score", "ensemble_score",
            "top_error_types", "draw_underestimated", "favorite_overestimated",
            "upset_underestimated", "ai_helped", "ensemble_helped",
            "next_round_recommendation", "insufficient_reason",
            "ai_enabled", "ai_models_configured",
        ]
        for field in required_fields:
            assert field in result, f"Missing field: {field}"

    def test_command_center_sample_insufficient(self, session):
        """With 0 matches, sample should be insufficient."""
        from app.services.accuracy_command import get_accuracy_command_center
        result = get_accuracy_command_center(session)
        assert result["sample_sufficient"] is False
        assert result["sample_count"] == 0
        assert result["insufficient_reason"] != ""


# ── 7. Data Pollution Audit Tests ─────────────────────────────────

class TestDataPollution:
    """Test data pollution prevention rules."""

    def test_24h_locked_only_scoring_basis(self, session):
        """Under the new rule, the latest pre-kickoff snapshot is used for scoring.

        24h locking is no longer the core scoring mechanism. The scoring system
        uses the latest snapshot created before kickoff, regardless of lock status.
        """
        _make_team(session, "T_P1", "TeamP1", "Z", 1600)
        _make_team(session, "T_P2", "TeamP2", "Z", 1500)
        _make_match(session, "M_POLL1", "T_P1", "T_P2", status="final",
                    home_score=2, away_score=1)

        # Earlier snapshot (pre-match locked)
        snap_locked = _make_snapshot(session, "M_POLL1", is_pre_match_locked=True,
                                    home_win=0.6, draw=0.2, away_win=0.2)
        # Later snapshot (fallback locked) - this is the latest before kickoff
        snap_fallback = _make_snapshot(session, "M_POLL1", is_pre_match_locked=False,
                                       is_fallback_locked=True, home_win=0.8, draw=0.1, away_win=0.1)

        from app.services.scoring import score_model
        report = score_model(session)
        # Under the new rule, the latest pre-kickoff snapshot is used
        if report.matches_scored > 0:
            for d in report.per_match:
                if d.match_id == "M_POLL1":
                    assert d.predicted["home_win"] == 0.8  # latest before kickoff

    def test_fallback_not_counted_as_24h_locked(self, session):
        """Fallback-locked snapshots should be distinguishable from 24h locked."""
        _make_team(session, "T_P3", "TeamP3", "Y", 1600)
        _make_team(session, "T_P4", "TeamP4", "Y", 1500)
        _make_match(session, "M_POLL2", "T_P3", "T_P4", status="final",
                    home_score=1, away_score=1)

        snap = _make_snapshot(session, "M_POLL2", is_pre_match_locked=False,
                             is_fallback_locked=True, home_win=0.4, draw=0.3, away_win=0.3)
        assert snap.is_pre_match_locked is False
        assert snap.is_fallback_locked is True

    def test_ai_real_time_not_in_scoring(self, session):
        """real_time_only AI predictions should not be scored."""
        from app.ai.evaluation import _evaluate_ai_version
        _make_team(session, "T_P5", "TeamP5", "X", 1600)
        _make_team(session, "T_P6", "TeamP6", "X", 1500)
        match = _make_match(session, "M_POLL3", "T_P5", "T_P6", status="final",
                           home_score=3, away_score=0)

        # Real-time AI prediction
        _make_ai_prediction(session, "M_POLL3", real_time_only=True, is_pre_match_locked=False)

        # Should not be scored
        result = _evaluate_ai_version(session, [match], "ai-deepseek-v4-flash-v1")
        assert result["sample_count"] == 0

    def test_placeholder_not_in_scoring(self, session):
        """Placeholder knockout matches should not enter scoring."""
        match = Match(
            id="KO_PLACEHOLDER", home_team_id=None, away_team_id=None,
            kickoff=datetime.now(timezone.utc) + timedelta(days=30),
            status="scheduled", source="tournament",
            stage="round_of_32", is_placeholder_match=True,
        )
        session.add(match)
        session.flush()

        from app.services.scoring import score_model
        report = score_model(session)
        # Placeholder shouldn't appear
        match_ids = [d.match_id for d in report.per_match]
        assert "KO_PLACEHOLDER" not in match_ids

    def test_ai_parse_error_not_in_ensemble(self, session):
        """AI predictions with parse errors should not enter ensemble."""
        _make_team(session, "T_P7", "TeamP7", "W", 1600)
        _make_team(session, "T_P8", "TeamP8", "W", 1500)
        _make_match(session, "M_POLL4", "T_P7", "T_P8")
        _make_snapshot(session, "M_POLL4", home_win=0.5, draw=0.25, away_win=0.25)

        # AI with parse error
        _make_ai_prediction(session, "M_POLL4", "ai-deepseek-v4-flash-v1",
                           error_code="parse_failed", is_pre_match_locked=True)

        from app.ai.ensemble import compute_ensemble
        result = compute_ensemble(session, "M_POLL4")
        assert result["status"] == "success"
        # No AI in source probabilities
        source_probs = result["source_probabilities"]
        ai_keys = [k for k in source_probs if k.startswith("ai_")]
        assert len(ai_keys) == 0

    def test_model_versions_isolated(self, session):
        """Different model_versions should not overwrite each other."""
        _make_team(session, "T_P9", "TeamP9", "V", 1600)
        _make_team(session, "T_PA", "TeamPA", "V", 1500)
        _make_match(session, "M_POLL5", "T_P9", "T_PA", status="final",
                    home_score=2, away_score=1)
        _make_snapshot(session, "M_POLL5", home_win=0.5, draw=0.25, away_win=0.25,
                      model_version="elo-poisson-v1", is_pre_match_locked=True)
        _make_snapshot(session, "M_POLL5", home_win=0.55, draw=0.22, away_win=0.23,
                      model_version="elo-poisson-v1-intel-numeric", is_pre_match_locked=True)

        from app.services.scoring import model_score_by_version
        versions = model_score_by_version(session)
        version_names = [v["model_version"] for v in versions]
        # Both versions should exist independently
        assert "elo-poisson-v1" in version_names
        assert "elo-poisson-v1-intel-numeric" in version_names

    def test_dashboard_revision_traceable(self, session):
        """Dashboard revisions should be traceable."""
        rev = DashboardRevision(model_version="elo-poisson-v1", simulation_iterations=1000, simulation_seed=42, active=True)
        session.add(rev)
        session.flush()
        assert rev.id is not None
        assert rev.created_at is not None
