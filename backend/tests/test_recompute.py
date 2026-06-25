from pathlib import Path
from datetime import timedelta

from sqlalchemy import func, select

from app.models import (
    DashboardRevision,
    Match,
    MatchPrediction,
    ModelScore,
    PredictionSnapshot,
    QualificationPrediction,
    StandingSnapshot,
    Team,
    TeamProfilePrediction,
)
from app.providers.openfootball import OpenFootballProvider
from app.services.recompute import _compute_data_context, _prune_old_revisions, recompute_all
from app.services.seed import seed_ratings, seed_tournament


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"


def seed_database(session):
    payload = OpenFootballProvider.from_files(
        FIXTURES / "openfootball-worldcup-2026.json",
        FIXTURES / "openfootball-worldcup-teams-2026.json",
    ).load()
    seed_tournament(session, payload)
    seed_ratings(session, ROOT / "data/seed/elo-ratings-2026.json")
    session.commit()


def test_recompute_publishes_one_complete_revision(db_session):
    seed_database(db_session)

    revision = recompute_all(db_session, iterations=300, seed=7)

    assert db_session.scalar(
        select(func.count(MatchPrediction.id)).where(MatchPrediction.revision_id == revision.id)
    ) == 490
    assert db_session.scalar(
        select(func.count(QualificationPrediction.id)).where(
            QualificationPrediction.revision_id == revision.id
        )
    ) == 48
    assert db_session.scalar(
        select(func.count(StandingSnapshot.id)).where(StandingSnapshot.revision_id == revision.id)
    ) == 48
    assert db_session.scalar(
        select(DashboardRevision.id).where(DashboardRevision.active.is_(True))
    ) == revision.id
    assert db_session.scalar(select(func.count(TeamProfilePrediction.id))) == 0


def test_failed_recompute_keeps_previous_revision_active(db_session, monkeypatch):
    seed_database(db_session)
    first = recompute_all(db_session, iterations=100, seed=7)

    monkeypatch.setattr(
        "app.services.recompute.simulate_qualification",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulation failed")),
    )

    try:
        recompute_all(db_session, iterations=100, seed=8)
    except RuntimeError:
        pass

    assert db_session.scalar(
        select(DashboardRevision.id).where(DashboardRevision.active.is_(True))
    ) == first.id


def test_data_context_uses_available_snapshots(db_session):
    seed_database(db_session)
    teams = list(db_session.scalars(select(Team)))

    freshness, ranking_coverage, provider_agreement = _compute_data_context(
        db_session, teams
    )

    assert freshness >= 0.0
    assert ranking_coverage == 1.0
    assert provider_agreement > 0.5


def test_prune_old_revisions_preserves_scoring_history(db_session):
    seed_database(db_session)
    match = db_session.scalar(select(Match).order_by(Match.kickoff))
    team = db_session.scalar(select(Team).order_by(Team.id))

    revisions = []
    for idx in range(6):
        revision = DashboardRevision(
            model_version=f"test-v{idx}",
            simulation_iterations=1,
            simulation_seed=idx,
            active=idx == 5,
        )
        db_session.add(revision)
        db_session.flush()
        revisions.append(revision)

    old_revision = revisions[0]
    old_revision_id = old_revision.id
    db_session.add(
        MatchPrediction(
            revision_id=old_revision_id,
            match_id=match.id,
            home_xg=1.2,
            away_xg=0.8,
            home_win=0.5,
            draw=0.3,
            away_win=0.2,
            scorelines=[],
            score_matrix=[],
            confidence=0.8,
            confidence_label="High",
            data_confidence=0.8,
            data_confidence_label="High",
            model_confidence=0.8,
            model_confidence_label="High",
            explanation="historical prediction",
            model_inputs={},
            model_version="baseline",
        )
    )
    db_session.add(
        PredictionSnapshot(
            match_id=match.id,
            revision_id=old_revision_id,
            kickoff=match.kickoff,
            snapshotted_at=match.kickoff - timedelta(hours=1),
            home_win=0.5,
            draw=0.3,
            away_win=0.2,
            home_xg=1.2,
            away_xg=0.8,
            scorelines=[],
            score_matrix=[],
            confidence=0.8,
            confidence_label="High",
            model_inputs={},
            model_version="baseline",
        )
    )
    db_session.add(
        ModelScore(
            revision_id=old_revision_id,
            matches_scored=1,
            brier_score=0.1,
            log_loss=0.2,
            outcome_hit_rate=1.0,
            top_score_hit_rate=0.0,
            xg_mae=0.4,
            per_match=[],
        )
    )
    db_session.add(
        QualificationPrediction(
            revision_id=old_revision_id,
            team_id=team.id,
            first_probability=0.25,
            second_probability=0.25,
            third_probability=0.25,
            fourth_probability=0.25,
            qualify_probability=0.5,
            standard_error=0.0,
        )
    )
    db_session.commit()

    _prune_old_revisions(db_session, keep=5)
    db_session.commit()

    assert db_session.get(DashboardRevision, old_revision_id) is not None
    assert db_session.scalar(
        select(func.count(MatchPrediction.id)).where(MatchPrediction.revision_id == old_revision_id)
    ) == 1
    assert db_session.scalar(
        select(func.count(PredictionSnapshot.id)).where(PredictionSnapshot.revision_id == old_revision_id)
    ) == 1
    assert db_session.scalar(
        select(func.count(ModelScore.id)).where(ModelScore.revision_id == old_revision_id)
    ) == 1
    assert db_session.scalar(
        select(func.count(QualificationPrediction.id)).where(
            QualificationPrediction.revision_id == old_revision_id
        )
    ) == 0
