import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from app.models import Match, MatchIntelligence, AutoAdjustment, MatchPrediction, PredictionSnapshot
from app.services.recompute import recompute_all
from app.services.snapshots import write_snapshots
from app.services.scoring import score_model

def test_e2e_sporttery_odds_pipeline(db_session):
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

    match = db_session.scalars(select(Match).where(Match.status != "final").limit(1)).first()

    # We will pretend the kickoff is exactly 30 minutes away so recompute_all locks the snapshot
    match.kickoff = datetime.now(timezone.utc) + timedelta(minutes=30)
    db_session.flush()

    # 2. Insert high divergence Sporttery Intelligence
    intel = MatchIntelligence(
        match_id=match.id,
        provider="sporttery",
        source_url="http",
        intelligence_type="odds",
        raw_payload={},
        normalized_payload={"home": 0.99, "draw": 0.005, "away": 0.005, "raw_overround": 1.05},
        source_confidence=0.9
    )
    db_session.add(intel)
    db_session.flush()

    # 3. Execute recompute
    revision = recompute_all(db_session, iterations=10, seed=1)

    # 4. Verify AutoAdjustment was generated
    adjs = list(db_session.scalars(select(AutoAdjustment).where(AutoAdjustment.match_id == match.id)))
    assert len(adjs) == 2

    odds_adj = next(a for a in adjs if a.adjustment_type == "market_divergence")
    assert odds_adj.attack_delta == 0.0
    assert odds_adj.defense_delta == 0.0
    assert "市场" in odds_adj.reason
    assert "风险" in odds_adj.reason

    comp_adj = next(a for a in adjs if a.adjustment_type == "data_completeness")
    assert comp_adj.attack_delta == 0.0
    assert "首发" in comp_adj.reason

    # 5. Verify MatchPrediction has adjusted confidence and exact same xG
    pred = db_session.scalar(
        select(MatchPrediction)
        .where(MatchPrediction.match_id == match.id, MatchPrediction.revision_id == revision.id)
    )
    assert pred.has_auto_adjustments is True
    assert pred.home_win == pred.base_home_win
    assert pred.draw == pred.base_draw
    assert pred.away_win == pred.base_away_win
    assert "自动修正提示" in pred.explanation

    # 6. Verify Snapshots lock the warning
    snap = db_session.scalar(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.match_id == match.id, PredictionSnapshot.revision_id == revision.id)
    )
    assert snap is not None
    assert snap.is_pre_match_locked is True
    assert snap.has_auto_adjustments is True

    # 7. Fake match result to check warning_effect
    match.status = "final"
    # If the model strongly favored home team, but home_score is 0 and away_score is 1, it's an upset
    # The sporttery odds were 99% for home, so model and sporttery diverged if model favored away?
    # Actually the test just checks the code path, we can fake the outcome.
    match.home_score = 0
    match.away_score = 1
    db_session.flush()

    # Check warning_effect in score_model
    report = score_model(db_session)

    # We can inspect the match score detail manually
    detail = next((d for d in report.per_match if d.match_id == match.id), None)

    assert detail is not None
    # Since confidence penalty happened without probability change, it should have a warning_effect
    assert detail.warning_effect in ["helped", "hurt"]
    assert detail.probability_effect == 0.0
