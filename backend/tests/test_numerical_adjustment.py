import pytest
from unittest.mock import patch
from app.config import settings
from app.intelligence.engine import AdjustmentEngine
from app.intelligence.player_mock import PlayerMock
from app.models import Match, MatchIntelligence, Team
from sqlalchemy.orm import Session
from app.prediction.poisson import MatchPredictionResult

def test_numerical_adjustment_disabled(db_session: Session):
    settings.enable_numerical_adjustments = False

    home = Team(id="FRA", name="France", short_name="FRA", code="FRA", group_code="A")
    away = Team(id="ARG", name="Argentina", short_name="ARG", code="ARG", group_code="A")
    db_session.add_all([home, away])
    db_session.flush()

    from datetime import datetime, timezone
    match = Match(id="TEST-FRA-ARG", home_team_id="FRA", away_team_id="ARG", group_code="A", status="scheduled", kickoff=datetime.now(timezone.utc), source="test")
    db_session.add(match)
    db_session.flush()

    intel = MatchIntelligence(
        match_id=match.id,
        provider="sportmonks",
        source_url="http://mock",
        intelligence_type="injuries",
        affected_team_id="FRA",
        raw_payload={},
        normalized_payload={"affected_team_id": "FRA", "player_name": "Kylian Mbappé", "reason": "Injured"},
        source_confidence=0.9
    )
    db_session.add(intel)
    db_session.flush()

    engine = AdjustmentEngine(db_session, "v1")
    adjustments = engine.evaluate_match(match, MatchPredictionResult(
        home_win=0.5, draw=0.3, away_win=0.2, home_xg=1.5, away_xg=1.0,
        confidence=0.8, confidence_label="高", data_confidence=0.8,
        data_confidence_label="高", model_confidence=0.8, model_confidence_label="高",
        explanation="", model_version="v1", scorelines=[], score_matrix=[]
    ), [intel])

    assert len(adjustments) == 1
    assert adjustments[0].adjustment_type == "roster_warning"
    assert adjustments[0].attack_delta == 0.0
    assert adjustments[0].defense_delta == 0.0

def test_numerical_adjustment_enabled(db_session: Session):
    settings.enable_numerical_adjustments = True

    home = Team(id="FRA", name="France", short_name="FRA", code="FRA", group_code="A")
    away = Team(id="ARG", name="Argentina", short_name="ARG", code="ARG", group_code="A")
    db_session.add_all([home, away])
    db_session.flush()

    from datetime import datetime, timezone
    match = Match(id="TEST-FRA-ARG", home_team_id="FRA", away_team_id="ARG", group_code="A", status="scheduled", kickoff=datetime.now(timezone.utc), source="test")
    db_session.add(match)
    db_session.flush()

    intel = MatchIntelligence(
        match_id=match.id,
        provider="sportmonks",
        source_url="http://mock",
        intelligence_type="injuries",
        affected_team_id="FRA",
        raw_payload={},
        normalized_payload={"affected_team_id": "FRA", "player_name": "Kylian Mbappé", "reason": "Injured"},
        source_confidence=0.9
    )
    db_session.add(intel)
    db_session.flush()

    engine = AdjustmentEngine(db_session, "v1")
    mock_player = PlayerMock(
        team_id="FRA", player_name="Kylian Mbappé", position="FWD",
        importance_score=0.95, is_key_player=True, source="test"
    )
    with patch("app.intelligence.player_mock.get_player_importance", return_value=mock_player):
        adjustments = engine.evaluate_match(match, MatchPredictionResult(
            home_win=0.5, draw=0.3, away_win=0.2, home_xg=1.5, away_xg=1.0,
            confidence=0.8, confidence_label="高", data_confidence=0.8,
            data_confidence_label="高", model_confidence=0.8, model_confidence_label="高",
            explanation="", model_version="v1", scorelines=[], score_matrix=[]
        ), [intel])

    assert len(adjustments) == 2
    warning_adj = next(a for a in adjustments if a.adjustment_type == "roster_warning")
    num_adj = next(a for a in adjustments if a.adjustment_type == "numerical_roster_adjustment")
    assert num_adj.attack_delta < 0.0 # Mbappe is FWD, so negative attack_delta
    assert num_adj.defense_delta == 0.0
    assert "实验性数值修正开启" in num_adj.reason

    # Clean up state
    settings.enable_numerical_adjustments = False
