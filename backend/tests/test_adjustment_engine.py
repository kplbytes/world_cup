import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from app.models import Team, Match, MatchIntelligence, AutoAdjustment, MatchPrediction
from app.services.recompute import recompute_all
from app.intelligence.engine import AdjustmentEngine
from app.prediction.poisson import MatchPredictionResult

def test_engine_evaluates_market_divergence(db_session):
    db_session.add(Team(id="TeamA", name="Team A", short_name="TA", code="TMA", group_code="A"))
    db_session.add(Team(id="TeamB", name="Team B", short_name="TB", code="TMB", group_code="A"))
    db_session.flush()

    kickoff = datetime.now(timezone.utc)
    match = Match(
        id="test_match_1", group_code="A", source="test", home_team_id="TeamA", away_team_id="TeamB",
        kickoff=kickoff, venue="V", status="scheduled"
    )
    db_session.add(match)
    db_session.flush()

    # We will just test the `evaluate_match` directly
    base_pred = MatchPredictionResult(
        home_xg=1.5, away_xg=1.0, home_win=0.5, draw=0.3, away_win=0.2,
        scorelines=[], score_matrix=[], confidence=0.8, confidence_label="高",
        data_confidence=0.8, data_confidence_label="高",
        model_confidence=0.8, model_confidence_label="高",
        explanation="Base", model_version="test"
    )

    # High divergence intelligence
    intel = MatchIntelligence(
        match_id="test_match_1",
        provider="sporttery",
        source_url="http",
        intelligence_type="odds",
        raw_payload={},
        normalized_payload={"home": 0.2, "draw": 0.3, "away": 0.5, "raw_overround": 1.05}, # huge diff from 0.5/0.3/0.2
        source_confidence=0.9
    )

    engine = AdjustmentEngine(db_session, "test")
    adjs = engine.evaluate_match(match, base_pred, [intel])

    assert len(adjs) == 1
    assert adjs[0].adjustment_type == "market_divergence"
    assert adjs[0].confidence == -0.25 # "高" divergence
    assert "模型预测" in adjs[0].reason
    assert "风险" in adjs[0].reason

def test_recompute_all_applies_engine_adjustments(db_session):
    from pathlib import Path
    from app.services.seed import seed_ratings, seed_tournament
    from app.providers.openfootball import OpenFootballProvider

    ROOT = Path(__file__).resolve().parents[2]
    FIXTURES = Path(__file__).parent / "fixtures"
    payload = OpenFootballProvider.from_files(
        FIXTURES / "openfootball-worldcup-2026.json",
        FIXTURES / "openfootball-worldcup-teams-2026.json",
    ).load()
    seed_tournament(db_session, payload)
    seed_ratings(db_session, ROOT / "data/seed/elo-ratings-2026.json")
    db_session.commit()

    # We will just pick the first unplayed match
    match = db_session.scalars(select(Match).where(Match.status != "final").limit(1)).first()

    intel = MatchIntelligence(
        match_id=match.id,
        provider="sporttery",
        source_url="http",
        intelligence_type="odds",
        raw_payload={},
        normalized_payload={"home": 0.99, "draw": 0.005, "away": 0.005, "raw_overround": 1.05}, # huge diff to trigger auto adjustment
        source_confidence=0.9
    )
    db_session.add(intel)
    db_session.flush()

    # Recompute
    recompute_all(db_session, iterations=10, seed=1)

    # Check that AutoAdjustment was created
    adjs = list(db_session.scalars(select(AutoAdjustment).where(AutoAdjustment.match_id == match.id)))
    assert len(adjs) == 1

    # Check that prediction confidence was lowered and explanation updated
    pred = db_session.scalar(
        select(MatchPrediction)
        .where(MatchPrediction.match_id == match.id)
        .where(MatchPrediction.model_version == "elo-poisson-v1")
        .order_by(MatchPrediction.id.desc())
    )

    assert pred.has_auto_adjustments is True
    assert pred.confidence <= 0.75
    assert "自动修正提示" in pred.explanation
    assert "风险" in pred.explanation

def test_engine_evaluates_roster_warnings(db_session):
    db_session.add_all([
        Team(id="TeamA", name="Team A", short_name="TA", code="TMA", group_code="A"),
        Team(id="TeamB", name="Team B", short_name="TB", code="TMB", group_code="A")
    ])
    db_session.flush()

    match = Match(id="test_roster", group_code="A", home_team_id="TeamA", away_team_id="TeamB", kickoff=datetime.now(timezone.utc), source="test", status="scheduled")
    db_session.add(match)
    db_session.flush()

    intel1 = MatchIntelligence(
        match_id="test_roster",
        provider="sportmonks",
        source_url="test",
        intelligence_type="injuries",
        raw_payload={},
        normalized_payload={"affected_team_id": "TeamA", "player_name": "Player 1", "reason": "Hamstring"},
        source_confidence=0.8,
        fetched_at=datetime.now(timezone.utc)
    )
    intel2 = MatchIntelligence(
        match_id="test_roster",
        provider="sportmonks",
        source_url="test",
        intelligence_type="suspensions",
        raw_payload={},
        normalized_payload={"affected_team_id": "TeamA", "player_name": "Player 2", "reason": "Red Card"},
        source_confidence=0.8,
        fetched_at=datetime.now(timezone.utc)
    )
    db_session.add_all([intel1, intel2])
    db_session.flush()

    engine = AdjustmentEngine(db_session, "test")
    base_pred = MatchPredictionResult(
        home_xg=1.5, away_xg=1.0, home_win=0.5, draw=0.3, away_win=0.2,
        scorelines=[], score_matrix=[], confidence=0.8, confidence_label="高",
        data_confidence=0.8, data_confidence_label="高",
        model_confidence=0.8, model_confidence_label="高",
        explanation="Base", model_version="test"
    )

    adjs = engine.evaluate_match(match, base_pred, [intel1, intel2])

    assert len(adjs) == 1
    adj = adjs[0]
    assert adj.adjustment_type == "roster_warning"
    assert adj.affected_team_id == "TeamA"
    assert adj.attack_delta == 0.0
    assert adj.defense_delta == 0.0
    assert adj.draw_delta == 0.0
    assert adj.confidence == 0.8
    assert "Player 1(Hamstring)" in adj.reason
    assert "Player 2(Red Card)" in adj.reason
    assert len(adj.source_intelligence_ids) == 2
