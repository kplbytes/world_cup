from pathlib import Path

from sqlalchemy import func, select

from app.models import (
    DashboardRevision,
    MatchPrediction,
    QualificationPrediction,
    StandingSnapshot,
)
from app.providers.openfootball import OpenFootballProvider
from app.services.recompute import recompute_all
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
    ) == 70
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

