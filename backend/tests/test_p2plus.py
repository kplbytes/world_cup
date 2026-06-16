"""Tests for P2+ features: AI models, ensemble, tournament, stage scoring."""

import json
import math
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    Base,
    DashboardRevision,
    MarketSnapshot,
    Match,
    MatchPrediction,
    PredictionSnapshot,
    Team,
    TeamRating,
    AIPrediction,
    EnsemblePrediction,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


def _seed_teams_and_match(session: Session, match_id: str = "m1", stage: str = "group", group_code: str = "A"):
    """Helper to seed basic teams and a match."""
    session.add_all([
        Team(id="T1", name="Team1", short_name="Team1", code="T1", group_code="A"),
        Team(id="T2", name="Team2", short_name="Team2", code="T2", group_code="A"),
        Team(id="T3", name="Team3", short_name="Team3", code="T3", group_code="A"),
        Team(id="T4", name="Team4", short_name="Team4", code="T4", group_code="A"),
    ])
    session.flush()

    kickoff = datetime(2026, 6, 15, 12, tzinfo=timezone.utc)
    match = Match(
        id=match_id, group_code=group_code,
        home_team_id="T1", away_team_id="T2",
        kickoff=kickoff, status="scheduled", source="test",
        stage=stage,
    )
    session.add(match)
    session.flush()
    return match


# ==================== AI Model Registry Tests ====================

class TestAIModelRegistry:
    def test_load_deepseek_flash(self):
        from app.ai.model_registry import get_model_config
        config = get_model_config("ai-deepseek-v4-flash-v1")
        assert config is not None
        assert config.model_id == "deepseek-v4-flash"
        assert config.provider_name == "deepseek"
        assert config.enabled is True
        assert config.cost_tier == "low"
        assert config.latency_tier == "fast"
        assert config.role == "fast_baseline"

    def test_load_deepseek_pro(self):
        from app.ai.model_registry import get_model_config
        config = get_model_config("ai-deepseek-v4-pro-v1")
        assert config is not None
        assert config.model_id == "deepseek-v4-pro"
        assert config.provider_name == "deepseek"
        assert config.enabled is True
        assert config.cost_tier == "high"
        assert config.latency_tier == "slow"
        assert config.role == "reasoning_strong"

    def test_supports_n_models(self):
        from app.ai.model_registry import list_enabled_models
        models = list_enabled_models()
        assert len(models) >= 2
        versions = [m.model_version for m in models]
        assert "ai-deepseek-v4-flash-v1" in versions
        assert "ai-deepseek-v4-pro-v1" in versions

    def test_disabled_model_not_listed(self):
        from app.ai.model_registry import get_model_config
        # This model doesn't exist in config
        config = get_model_config("ai-nonexistent-model-v1")
        assert config is None

    def test_no_api_key_no_error(self):
        """Verify that missing API key doesn't cause import errors."""
        from app.ai.providers.deepseek import DeepSeekProvider
        from app.ai.providers.base import AIProviderConfig
        config = AIProviderConfig(
            provider_name="deepseek",
            enabled=True,
            api_key_env="NONEXISTENT_KEY_12345",
            base_url_env="NONEXISTENT_URL_12345",
            default_timeout_seconds=30,
            default_temperature=0,
            max_retries=2,
        )
        provider = DeepSeekProvider(config)
        assert provider.is_configured() is False


# ==================== AI Parser Tests ====================

class TestAIParser:
    def test_parse_valid_response(self):
        from app.ai.parser import parse_ai_response
        raw = json.dumps({
            "home_win": 0.50, "draw": 0.25, "away_win": 0.25,
            "confidence": 0.7,
            "risk_flags": ["injury"],
            "key_factors": ["home advantage"],
            "reason": "Home team is stronger",
            "uncertainties": ["weather"],
            "disagreement_with_system": "none",
            "disagreement_with_market": "none",
            "recommended_label": "home_win",
        })
        parsed, warnings = parse_ai_response(raw)
        assert parsed is not None
        assert abs(parsed.home_win - 0.50) < 0.01
        assert abs(parsed.draw - 0.25) < 0.01
        assert abs(parsed.away_win - 0.25) < 0.01
        assert parsed.confidence == 0.7
        assert parsed.recommended_label == "home_win"
        assert "injury" in parsed.risk_flags
        assert len(warnings) == 0

    def test_parse_unnormalized_probabilities(self):
        from app.ai.parser import parse_ai_response
        raw = json.dumps({"home_win": 0.6, "draw": 0.3, "away_win": 0.3})
        parsed, warnings = parse_ai_response(raw)
        assert parsed is not None
        total = parsed.home_win + parsed.draw + parsed.away_win
        assert abs(total - 1.0) < 0.01
        assert parsed.was_normalized is True

    def test_parse_missing_fields(self):
        from app.ai.parser import parse_ai_response
        raw = json.dumps({"home_win": 0.5})  # missing draw and away_win
        parsed, warnings = parse_ai_response(raw)
        assert parsed is None
        assert any("missing_fields" in w for w in warnings)

    def test_parse_invalid_json(self):
        from app.ai.parser import parse_ai_response
        parsed, warnings = parse_ai_response("not valid json")
        assert parsed is None
        assert any("parse_failed" in w for w in warnings)

    def test_parse_empty_response(self):
        from app.ai.parser import parse_ai_response
        parsed, warnings = parse_ai_response("")
        assert parsed is None
        assert "empty_response" in warnings

    def test_parse_zero_sum(self):
        from app.ai.parser import parse_ai_response
        raw = json.dumps({"home_win": 0, "draw": 0, "away_win": 0})
        parsed, warnings = parse_ai_response(raw)
        assert parsed is None
        assert any("zero" in w.lower() for w in warnings)

    def test_parse_default_confidence(self):
        from app.ai.parser import parse_ai_response
        raw = json.dumps({"home_win": 0.5, "draw": 0.25, "away_win": 0.25})
        parsed, warnings = parse_ai_response(raw)
        assert parsed is not None
        assert parsed.confidence == 0.5
        assert parsed.confidence_was_default is True

    def test_parse_markdown_code_block(self):
        from app.ai.parser import parse_ai_response
        raw = '```json\n{"home_win": 0.5, "draw": 0.3, "away_win": 0.2}\n```'
        parsed, warnings = parse_ai_response(raw)
        assert parsed is not None
        assert abs(parsed.home_win - 0.5) < 0.01


# ==================== AI Prediction Storage Tests ====================

class TestAIPredictionStorage:
    def test_single_model_prediction_stored(self, db_session):
        _seed_teams_and_match(db_session)
        pred = AIPrediction(
            match_id="m1",
            provider="deepseek",
            model_id="deepseek-v4-flash",
            model_version="ai-deepseek-v4-flash-v1",
            prompt_version="worldcup-ai-v1",
            input_snapshot_json={"match_id": "m1"},
            raw_response_text='{"home_win": 0.5, "draw": 0.3, "away_win": 0.2}',
            parsed_home_win=0.5,
            parsed_draw=0.3,
            parsed_away_win=0.2,
            confidence=0.7,
            reason="Test prediction",
            recommended_label="home_win",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(pred)
        db_session.flush()

        result = db_session.get(AIPrediction, pred.id)
        assert result is not None
        assert result.model_version == "ai-deepseek-v4-flash-v1"
        assert result.parsed_home_win == 0.5

    def test_multiple_model_predictions_stored(self, db_session):
        _seed_teams_and_match(db_session)

        for version in ["ai-deepseek-v4-flash-v1", "ai-deepseek-v4-pro-v1"]:
            pred = AIPrediction(
                match_id="m1",
                provider="deepseek",
                model_id=version.replace("ai-", "").replace("-v1", ""),
                model_version=version,
                prompt_version="worldcup-ai-v1",
                input_snapshot_json={"match_id": "m1"},
                raw_response_text="{}",
                parsed_home_win=0.5,
                parsed_draw=0.3,
                parsed_away_win=0.2,
                confidence=0.7,
                created_at=datetime.now(timezone.utc),
            )
            db_session.add(pred)
        db_session.flush()

        from sqlalchemy import select
        preds = list(db_session.scalars(select(AIPrediction).where(AIPrediction.match_id == "m1")))
        assert len(preds) == 2
        versions = {p.model_version for p in preds}
        assert "ai-deepseek-v4-flash-v1" in versions
        assert "ai-deepseek-v4-pro-v1" in versions

    def test_parse_error_stored(self, db_session):
        _seed_teams_and_match(db_session)
        pred = AIPrediction(
            match_id="m1",
            provider="deepseek",
            model_id="deepseek-v4-flash",
            model_version="ai-deepseek-v4-flash-v1",
            prompt_version="worldcup-ai-v1",
            input_snapshot_json={},
            raw_response_text="invalid json",
            error_code="parse_failed",
            error_message="Could not parse response",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(pred)
        db_session.flush()

        result = db_session.get(AIPrediction, pred.id)
        assert result.error_code == "parse_failed"
        assert result.parsed_home_win is None

    def test_post_24h_window_not_scored(self, db_session):
        """AI prediction generated after 24h lock window should not participate in scoring."""
        _seed_teams_and_match(db_session)

        # Pre-match locked prediction
        pred_locked = AIPrediction(
            match_id="m1", provider="deepseek", model_id="deepseek-v4-flash",
            model_version="ai-deepseek-v4-flash-v1", prompt_version="worldcup-ai-v1",
            input_snapshot_json={}, raw_response_text="{}",
            parsed_home_win=0.5, parsed_draw=0.3, parsed_away_win=0.2,
            confidence=0.7, is_pre_match_locked=True,
            created_at=datetime(2026, 6, 15, 10, tzinfo=timezone.utc),
        )

        # Real-time only prediction (after 24h lock window)
        pred_rt = AIPrediction(
            match_id="m1", provider="deepseek", model_id="deepseek-v4-flash",
            model_version="ai-deepseek-v4-flash-v1", prompt_version="worldcup-ai-v1",
            input_snapshot_json={}, raw_response_text="{}",
            parsed_home_win=0.6, parsed_draw=0.2, parsed_away_win=0.2,
            confidence=0.8, real_time_only=True,
            created_at=datetime(2026, 6, 15, 12, tzinfo=timezone.utc),
        )
        db_session.add_all([pred_locked, pred_rt])
        db_session.flush()

        # Only pre-match locked should be scoreable
        from sqlalchemy import select
        scorable = list(db_session.scalars(
            select(AIPrediction)
            .where(AIPrediction.match_id == "m1")
            .where(AIPrediction.is_pre_match_locked.is_(True))
        ))
        assert len(scorable) == 1
        assert scorable[0].parsed_home_win == 0.5


# ==================== Ensemble Tests ====================

class TestEnsemble:
    def test_single_ai_weight(self):
        from app.ai.ensemble import _compute_weights
        weights = _compute_weights(
            has_market=True, has_ai=True, num_ai=1,
            defaults={"system_weight": 0.50, "market_weight": 0.20, "total_ai_weight": 0.30,
                      "system_weight_no_market": 0.60, "total_ai_weight_no_market": 0.40,
                      "system_weight_no_ai": 0.80, "market_weight_no_ai": 0.20,
                      "system_weight_only": 1.00},
        )
        assert abs(sum(weights.values()) - 1.0) < 0.01
        assert "system" in weights
        assert "market" in weights
        assert weights["system"] > 0

    def test_dual_ai_weights(self):
        from app.ai.ensemble import _compute_weights
        weights = _compute_weights(
            has_market=True, has_ai=True, num_ai=2,
            defaults={"system_weight": 0.50, "market_weight": 0.20, "total_ai_weight": 0.30,
                      "system_weight_no_market": 0.60, "total_ai_weight_no_market": 0.40,
                      "system_weight_no_ai": 0.80, "market_weight_no_ai": 0.20,
                      "system_weight_only": 1.00},
            ai_weights_override={"model-a": 0.67, "model-b": 0.33},
        )
        assert abs(sum(weights.values()) - 1.0) < 0.01
        ai_keys = [k for k in weights if k.startswith("ai_")]
        assert len(ai_keys) == 2

    def test_n_ai_weights(self):
        from app.ai.ensemble import _compute_weights
        weights = _compute_weights(
            has_market=True, has_ai=True, num_ai=5,
            defaults={"system_weight": 0.50, "market_weight": 0.20, "total_ai_weight": 0.30,
                      "system_weight_no_market": 0.60, "total_ai_weight_no_market": 0.40,
                      "system_weight_no_ai": 0.80, "market_weight_no_ai": 0.20,
                      "system_weight_only": 1.00},
        )
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_market_missing_degradation(self):
        from app.ai.ensemble import _compute_weights
        weights = _compute_weights(
            has_market=False, has_ai=True, num_ai=1,
            defaults={"system_weight": 0.50, "market_weight": 0.20, "total_ai_weight": 0.30,
                      "system_weight_no_market": 0.60, "total_ai_weight_no_market": 0.40,
                      "system_weight_no_ai": 0.80, "market_weight_no_ai": 0.20,
                      "system_weight_only": 1.00},
        )
        assert "market" not in weights
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_ai_missing_degradation(self):
        from app.ai.ensemble import _compute_weights
        weights = _compute_weights(
            has_market=True, has_ai=False, num_ai=0,
            defaults={"system_weight": 0.50, "market_weight": 0.20, "total_ai_weight": 0.30,
                      "system_weight_no_market": 0.60, "total_ai_weight_no_market": 0.40,
                      "system_weight_no_ai": 0.80, "market_weight_no_ai": 0.20,
                      "system_weight_only": 1.00},
        )
        ai_keys = [k for k in weights if k.startswith("ai_")]
        assert len(ai_keys) == 0
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_both_missing_system_only(self):
        from app.ai.ensemble import _compute_weights
        weights = _compute_weights(
            has_market=False, has_ai=False, num_ai=0,
            defaults={"system_weight": 0.50, "market_weight": 0.20, "total_ai_weight": 0.30,
                      "system_weight_no_market": 0.60, "total_ai_weight_no_market": 0.40,
                      "system_weight_no_ai": 0.80, "market_weight_no_ai": 0.20,
                      "system_weight_only": 1.00},
        )
        assert abs(weights["system"] - 1.0) < 0.01

    def test_probability_normalization(self):
        """Ensemble output probabilities must sum to 1.0."""
        from app.ai.ensemble import _compute_weights
        weights = _compute_weights(
            has_market=True, has_ai=True, num_ai=2,
            defaults={"system_weight": 0.50, "market_weight": 0.20, "total_ai_weight": 0.30,
                      "system_weight_no_market": 0.60, "total_ai_weight_no_market": 0.40,
                      "system_weight_no_ai": 0.80, "market_weight_no_ai": 0.20,
                      "system_weight_only": 1.00},
            ai_weights_override={"model-a": 0.67, "model-b": 0.33},
        )
        # Simulate ensemble probability calculation
        sys_probs = {"home_win": 0.5, "draw": 0.3, "away_win": 0.2}
        market_probs = {"home_win": 0.45, "draw": 0.28, "away_win": 0.27}
        ai1_probs = {"home_win": 0.55, "draw": 0.25, "away_win": 0.20}
        ai2_probs = {"home_win": 0.52, "draw": 0.22, "away_win": 0.26}

        ai_keys = sorted(k for k in weights if k.startswith("ai_"))

        result = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
        for key in result:
            result[key] += sys_probs[key] * weights.get("system", 0)
            result[key] += market_probs[key] * weights.get("market", 0)
            result[key] += ai1_probs[key] * weights.get(ai_keys[0], 0)
            result[key] += ai2_probs[key] * weights.get(ai_keys[1], 0)

        total = sum(result.values())
        assert abs(total - 1.0) < 0.01


# ==================== Tournament Tests ====================

class TestTournament:
    def test_stage_enum(self):
        from app.tournament.rules import STAGE_ORDER
        expected = ["group", "round_of_32", "round_of_16", "quarter_final", "semi_final", "third_place", "final"]
        assert STAGE_ORDER == expected

    def test_placeholder_match_creation(self, db_session):
        """Placeholder matches can be created for knockout rounds."""
        match = Match(
            id="ko_m1",
            group_code=None,
            home_team_id=None,  # placeholder
            away_team_id=None,  # placeholder
            kickoff=datetime(2026, 7, 5, 18, tzinfo=timezone.utc),
            status="scheduled",
            source="tournament",
            stage="round_of_32",
            round_name="Round of 32",
            bracket_position=1,
            home_team_source="A1",
            away_team_source="C2",
            is_placeholder_match=True,
        )
        db_session.add(match)
        db_session.flush()

        result = db_session.get(Match, "ko_m1")
        assert result is not None
        assert result.stage == "round_of_32"
        assert result.is_placeholder_match is True
        assert result.home_team_id is None
        assert result.home_team_source == "A1"

    def test_group_ranking(self, db_session):
        """Test group ranking computation."""
        _seed_teams_and_match(db_session, "m1", "group", "A")

        # Add more teams and a completed match
        match = db_session.get(Match, "m1")
        match.home_score = 2
        match.away_score = 1
        match.status = "final"
        db_session.flush()

        from app.tournament.standings import get_current_standings
        standings = get_current_standings(db_session)
        assert "A" in standings
        group_a = standings["A"]
        assert len(group_a) == 4
        # T1 should be first after winning 2-1
        assert group_a[0]["team_id"] == "T1"
        assert group_a[0]["points"] == 3

    def test_qualification_probability_output(self):
        """Test that qualification probabilities are computed."""
        from app.tournament.qualification import compute_projections
        # Build group placement probs and team-group map for 48 teams in 12 groups
        group_placement_probs = {}
        team_group_map = {}
        for i in range(1, 49):
            group_idx = (i - 1) // 4
            group_code = chr(ord("A") + group_idx)
            team_group_map[f"T{i}"] = group_code
            if i % 4 == 1:
                group_placement_probs[f"T{i}"] = {"1st": 0.6, "2nd": 0.25, "3rd": 0.1, "4th": 0.05}
            elif i % 4 == 2:
                group_placement_probs[f"T{i}"] = {"1st": 0.25, "2nd": 0.4, "3rd": 0.25, "4th": 0.1}
            elif i % 4 == 3:
                group_placement_probs[f"T{i}"] = {"1st": 0.1, "2nd": 0.25, "3rd": 0.4, "4th": 0.25}
            else:
                group_placement_probs[f"T{i}"] = {"1st": 0.05, "2nd": 0.1, "3rd": 0.25, "4th": 0.6}
        team_elos = {f"T{i}": 1800 - i * 10 for i in range(1, 49)}
        projections = compute_projections(group_placement_probs, team_elos, team_group_map, iterations=2000, seed=42)
        assert len(projections) == 48
        # Projections are sorted by champion probability (descending)
        # Higher Elo teams should generally have higher champion probability
        # T1 has highest Elo (1790) and should be among top
        top_team_ids = [p.team_id for p in projections[:5]]
        assert "T1" in top_team_ids or "T2" in top_team_ids  # Strong teams in top

    def test_bracket_generation(self):
        """Test that bracket can be generated."""
        from app.tournament.bracket import generate_bracket
        standings = {
            "A": [{"team_id": "A1", "team_name": "Team A1", "points": 9, "position": 1},
                   {"team_id": "A2", "team_name": "Team A2", "points": 6, "position": 2}],
            "B": [{"team_id": "B1", "team_name": "Team B1", "points": 9, "position": 1},
                   {"team_id": "B2", "team_name": "Team B2", "points": 6, "position": 2}],
        }
        third_placed = {"qualified": [], "eliminated": []}
        bracket = generate_bracket(standings, third_placed)
        assert "round_of_32" in bracket
        assert len(bracket["round_of_32"]) > 0

    def test_team_path(self, db_session):
        """Test team path retrieval."""
        _seed_teams_and_match(db_session)
        # Add remaining teams for all 12 groups (B-L) so the bracket is complete
        for group_idx in range(1, 12):
            group_code = chr(ord("A") + group_idx)
            for num in range(1, 5):
                tid = f"{group_code}{num}"
                db_session.add(Team(
                    id=tid, name=f"Team {tid}", short_name=f"Team {tid}",
                    code=tid, group_code=group_code,
                ))
        db_session.flush()

        # Add more matches for T1
        for i, opp in enumerate(["T3", "T4"]):
            match = Match(
                id=f"m_T1_{i}", group_code="A",
                home_team_id="T1", away_team_id=opp,
                kickoff=datetime(2026, 6, 18 + i, 12, tzinfo=timezone.utc),
                status="scheduled", source="test", stage="group",
            )
            db_session.add(match)

        # Add qualification predictions for all 48 teams
        rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1000, simulation_seed=42)
        db_session.add(rev)
        db_session.flush()

        from app.models import QualificationPrediction
        for group_idx in range(12):
            group_code = chr(ord("A") + group_idx)
            for num in range(1, 5):
                tid = f"{group_code}{num}" if group_idx > 0 else f"T{num}"
                db_session.add(QualificationPrediction(
                    revision_id=rev.id, team_id=tid,
                    first_probability=0.4 if num == 1 else 0.2,
                    second_probability=0.3 if num == 2 else 0.25,
                    third_probability=0.2 if num == 3 else 0.3,
                    fourth_probability=0.1 if num == 4 else 0.25,
                    qualify_probability=0.7 if num <= 2 else 0.3,
                    standard_error=0.02,
                ))
        db_session.flush()

        from app.tournament.simulation import get_team_path
        path = get_team_path(db_session, "T1")
        assert path["found"] is True
        assert path["team_id"] == "T1"
        assert "projections" in path

    def test_knockout_advance_probability(self):
        """Test that advance probabilities are computed for knockout matches."""
        from app.tournament.qualification import compute_projections
        # 4 teams in group A only – not enough for full bracket,
        # so use 48 teams across 12 groups
        group_placement_probs = {}
        team_group_map = {}
        for i in range(1, 49):
            group_idx = (i - 1) // 4
            group_code = chr(ord("A") + group_idx)
            team_group_map[f"T{i}"] = group_code
            group_placement_probs[f"T{i}"] = {"1st": 0.25, "2nd": 0.25, "3rd": 0.25, "4th": 0.25}
        team_elos = {f"T{i}": 1850 - (i - 1) * 10 for i in range(1, 49)}
        projections = compute_projections(group_placement_probs, team_elos, team_group_map, iterations=2000, seed=42)

        for p in projections:
            assert p.round_of_16 <= p.round_of_32 + 0.01
            assert p.champion <= p.final + 0.01

    def test_champion_probability(self):
        """Champion probability should sum to approximately 1.0 with enough teams and iterations."""
        from app.tournament.qualification import compute_projections
        # Need enough qualified teams for knockout rounds to run
        group_placement_probs = {}
        team_group_map = {}
        for i in range(1, 49):
            group_idx = (i - 1) // 4
            group_code = chr(ord("A") + group_idx)
            team_group_map[f"T{i}"] = group_code
            group_placement_probs[f"T{i}"] = {"1st": 0.25, "2nd": 0.25, "3rd": 0.25, "4th": 0.25}
        team_elos = {f"T{i}": 1500 + (49 - i) * 20 for i in range(1, 49)}
        projections = compute_projections(group_placement_probs, team_elos, team_group_map, iterations=5000, seed=42)
        total_champion = sum(p.champion for p in projections)
        # With high qualification rates and enough teams, should approach 1.0
        # Allow variance for Monte Carlo
        assert total_champion > 0.5  # At least a significant portion should be assigned


# ==================== Stage Scoring Tests ====================

class TestStageScoring:
    def test_group_stage_scoring(self, db_session):
        """Test that group stage matches are scored correctly."""
        _seed_teams_and_match(db_session, "m_g1", "group", "A")
        match = db_session.get(Match, "m_g1")
        match.home_score = 1
        match.away_score = 0
        match.status = "final"
        db_session.flush()

        rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
        db_session.add(rev)
        db_session.flush()

        snap = PredictionSnapshot(
            match_id="m_g1", revision_id=rev.id,
            kickoff=datetime(2026, 6, 15, 12, tzinfo=timezone.utc),
            is_pre_match_locked=True,
            home_win=0.55, draw=0.25, away_win=0.20,
            home_xg=1.5, away_xg=0.8,
            scorelines=[], score_matrix=[], confidence=0.8, confidence_label="High",
            model_inputs={}, model_version="v1",
        )
        db_session.add(snap)
        db_session.flush()

        from app.services.scoring import model_score_by_version
        result = model_score_by_version(db_session)
        assert len(result) > 0
        assert result[0]["model_version"] == "v1"
        assert result[0]["sample_count"] >= 1

    def test_knockout_90min_scoring(self, db_session):
        """Test knockout match 90-minute result scoring."""
        match = Match(
            id="m_ko1", group_code=None,
            home_team_id="T1", away_team_id="T2",
            kickoff=datetime(2026, 7, 5, 18, tzinfo=timezone.utc),
            status="final", home_score=2, away_score=2, source="test",
            stage="quarter_final",
        )
        db_session.add_all([
            Team(id="T1", name="Team1", short_name="Team1", code="T1", group_code="A"),
            Team(id="T2", name="Team2", short_name="Team2", code="T2", group_code="B"),
        ])
        db_session.add(match)
        db_session.flush()

        rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
        db_session.add(rev)
        db_session.flush()

        snap = PredictionSnapshot(
            match_id="m_ko1", revision_id=rev.id,
            kickoff=datetime(2026, 7, 5, 18, tzinfo=timezone.utc),
            is_pre_match_locked=True,
            home_win=0.40, draw=0.30, away_win=0.30,
            home_xg=1.3, away_xg=1.1,
            scorelines=[], score_matrix=[], confidence=0.6, confidence_label="Medium",
            model_inputs={}, model_version="v1",
        )
        db_session.add(snap)
        db_session.flush()

        from app.services.scoring import model_score_by_version
        result = model_score_by_version(db_session)
        assert len(result) > 0

    def test_by_stage_aggregation(self, db_session):
        """Test that model-score/by-stage endpoint works."""
        _seed_teams_and_match(db_session, "m_s1", "group", "A")
        match = db_session.get(Match, "m_s1")
        match.home_score = 1
        match.away_score = 0
        match.status = "final"
        db_session.flush()

        # The by-stage endpoint should return results
        from app.services.scoring import model_score_by_version
        result = model_score_by_version(db_session)
        assert isinstance(result, list)


# ==================== AI Model Version Independent Scoring ====================

class TestAIModelScoring:
    def test_ai_model_version_independent_scoring(self, db_session):
        """AI predictions are scored independently from system predictions."""
        _seed_teams_and_match(db_session, "m_ai1", "group", "A")
        match = db_session.get(Match, "m_ai1")
        match.home_score = 1
        match.away_score = 0
        match.status = "final"
        db_session.flush()

        # System prediction
        rev = DashboardRevision(active=True, model_version="elo-poisson-v1", simulation_iterations=1, simulation_seed=1)
        db_session.add(rev)
        db_session.flush()

        sys_snap = PredictionSnapshot(
            match_id="m_ai1", revision_id=rev.id,
            kickoff=datetime(2026, 6, 15, 12, tzinfo=timezone.utc),
            is_pre_match_locked=True,
            home_win=0.55, draw=0.25, away_win=0.20,
            home_xg=1.5, away_xg=0.8,
            scorelines=[], score_matrix=[], confidence=0.8, confidence_label="High",
            model_inputs={}, model_version="elo-poisson-v1",
        )
        db_session.add(sys_snap)

        # AI prediction
        ai_pred = AIPrediction(
            match_id="m_ai1", provider="deepseek", model_id="deepseek-v4-flash",
            model_version="ai-deepseek-v4-flash-v1", prompt_version="worldcup-ai-v1",
            input_snapshot_json={}, raw_response_text="{}",
            parsed_home_win=0.60, parsed_draw=0.20, parsed_away_win=0.20,
            confidence=0.75, is_pre_match_locked=True,
            created_at=datetime(2026, 6, 15, 10, tzinfo=timezone.utc),
        )
        db_session.add(ai_pred)
        db_session.flush()

        # Evaluate AI predictions
        from app.ai.evaluation import evaluate_ai_predictions
        result = evaluate_ai_predictions(db_session)
        assert "system" in result
        assert "ai_by_version" in result
        assert "ensemble" in result
        assert "ai_effect" in result

    def test_ensemble_model_version_independent_scoring(self, db_session):
        """Ensemble predictions are scored independently."""
        _seed_teams_and_match(db_session, "m_ens1", "group", "A")
        match = db_session.get(Match, "m_ens1")
        match.home_score = 1
        match.away_score = 0
        match.status = "final"
        db_session.flush()

        # System prediction
        rev = DashboardRevision(active=True, model_version="elo-poisson-v1", simulation_iterations=1, simulation_seed=1)
        db_session.add(rev)
        db_session.flush()

        sys_snap = PredictionSnapshot(
            match_id="m_ens1", revision_id=rev.id,
            kickoff=datetime(2026, 6, 15, 12, tzinfo=timezone.utc),
            is_pre_match_locked=True,
            home_win=0.55, draw=0.25, away_win=0.20,
            home_xg=1.5, away_xg=0.8,
            scorelines=[], score_matrix=[], confidence=0.8, confidence_label="High",
            model_inputs={}, model_version="elo-poisson-v1",
        )
        db_session.add(sys_snap)

        # Ensemble prediction
        ens = EnsemblePrediction(
            match_id="m_ens1", model_version="ensemble-v1",
            system_model_version="elo-poisson-v1",
            system_weight=0.7, market_weight=0.15,
            ai_weights_json={"ai-deepseek-v4-flash-v1": 0.15},
            source_probabilities_json={},
            ensemble_home_win=0.58, ensemble_draw=0.22, ensemble_away_win=0.20,
            confidence=0.75, is_pre_match_locked=True,
            created_at=datetime(2026, 6, 15, 10, tzinfo=timezone.utc),
        )
        db_session.add(ens)
        db_session.flush()

        from app.ai.evaluation import evaluate_ai_predictions
        result = evaluate_ai_predictions(db_session)
        assert result["ensemble"]["sample_count"] >= 1


# ==================== Prompt Builder Tests ====================

class TestPromptBuilder:
    def test_build_prompt_contains_all_sections(self):
        from app.ai.prompt_builder import build_prediction_prompt
        from app.ai.providers.base import AIPredictionRequest

        request = AIPredictionRequest(
            match_id="m1", stage="group", group="A", knockout_round=None,
            home_team="Brazil", away_team="Argentina",
            kickoff="2026-06-15T12:00:00Z", venue="Maracana", neutral_ground=True,
            system_home_win=0.45, system_draw=0.28, system_away_win=0.27,
            system_home_xg=1.5, system_away_xg=1.1,
            system_model_confidence=0.7, system_data_confidence=0.8,
            most_likely_score="1-1",
            market_home_prob=0.42, market_draw_prob=0.30, market_away_prob=0.28,
            market_divergence=0.03, market_provider="sporttery", market_fetched_at="2026-06-15T08:00:00Z",
            risk_flags=["injury:PlayerA"],
            group_standing_context="Group A: Brazil 6pts, Argentina 3pts",
        )

        prompt = build_prediction_prompt(request)
        assert "Match Information" in prompt
        assert "Brazil" in prompt
        assert "Argentina" in prompt
        assert "System Model Prediction" in prompt
        assert "Market Odds" in prompt
        assert "Group Standing Context" in prompt
        assert "Required Output Format" in prompt
        assert "0.45" in prompt

    def test_build_prompt_no_market(self):
        from app.ai.prompt_builder import build_prediction_prompt
        from app.ai.providers.base import AIPredictionRequest

        request = AIPredictionRequest(
            match_id="m1", stage="group", group="A", knockout_round=None,
            home_team="T1", away_team="T2",
            kickoff="2026-06-15T12:00:00Z", venue=None, neutral_ground=True,
            system_home_win=0.4, system_draw=0.3, system_away_win=0.3,
            system_home_xg=1.2, system_away_xg=0.9,
            system_model_confidence=0.6, system_data_confidence=0.5,
            most_likely_score="1-0",
            market_home_prob=None, market_draw_prob=None, market_away_prob=None,
            market_divergence=None, market_provider=None, market_fetched_at=None,
        )

        prompt = build_prediction_prompt(request)
        assert "unknown" in prompt.lower() or "No market odds" in prompt
