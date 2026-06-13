from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import DashboardRevision, Match, TeamRating
from app.providers.openfootball import OpenFootballProvider
from app.services.recompute import recompute_all
from app.services.refresh import refresh_tournament
from app.services.seed import seed_ratings, seed_tournament


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"


class StaticProvider:
    def __init__(self, payload):
        self.payload = payload

    def load(self):
        return self.payload


def seeded_session(session):
    payload = OpenFootballProvider.from_files(
        FIXTURES / "openfootball-worldcup-2026.json",
        FIXTURES / "openfootball-worldcup-teams-2026.json",
    ).load()
    seed_tournament(session, payload)
    seed_ratings(session, ROOT / "data/seed/elo-ratings-2026.json")
    session.commit()
    recompute_all(session, iterations=100, seed=7)
    session.commit()
    return payload


def test_new_final_score_updates_result_ratings_and_revision(db_session):
    payload = seeded_session(db_session)
    updated = payload.model_copy(deep=True)
    target = next(match for match in updated.matches if match.status == "scheduled")
    target.status = "final"
    target.home_score = 2
    target.away_score = 1
    before_revision = db_session.scalar(
        select(DashboardRevision.id).where(DashboardRevision.active.is_(True))
    )
    before_rating = db_session.scalar(
        select(TeamRating.elo)
        .where(TeamRating.team_id == target.home_team_id)
        .order_by(TeamRating.effective_date.desc(), TeamRating.id.desc())
    )

    outcome = refresh_tournament(
        db_session,
        providers=[StaticProvider(updated)],
        iterations=100,
        seed=8,
    )
    db_session.commit()

    refreshed = db_session.get(Match, target.id)
    after_revision = db_session.scalar(
        select(DashboardRevision.id).where(DashboardRevision.active.is_(True))
    )
    after_rating = db_session.scalar(
        select(TeamRating.elo)
        .where(TeamRating.team_id == target.home_team_id)
        .order_by(TeamRating.effective_date.desc(), TeamRating.id.desc())
    )
    assert outcome.finalized_matches == 1
    assert (refreshed.status, refreshed.home_score, refreshed.away_score) == ("final", 2, 1)
    assert after_revision != before_revision
    assert after_rating != before_rating


def test_conflicting_final_score_is_rejected(db_session):
    payload = seeded_session(db_session)
    updated = payload.model_copy(deep=True)
    target = next(match for match in updated.matches if match.status == "final")
    target.home_score += 1

    outcome = refresh_tournament(
        db_session,
        providers=[StaticProvider(updated)],
        iterations=50,
        seed=8,
    )

    assert outcome.finalized_matches == 0
    assert outcome.warnings
    stored = db_session.get(Match, target.id)
    assert stored.home_score != target.home_score


def test_provider_failure_keeps_active_revision(db_session):
    seeded_session(db_session)
    before_revision = db_session.scalar(
        select(DashboardRevision.id).where(DashboardRevision.active.is_(True))
    )

    class FailedProvider:
        def load(self):
            raise RuntimeError("provider unavailable")

    outcome = refresh_tournament(db_session, providers=[FailedProvider()], iterations=50)

    after_revision = db_session.scalar(
        select(DashboardRevision.id).where(DashboardRevision.active.is_(True))
    )
    assert outcome.status == "failed"
    assert after_revision == before_revision


def test_unchanged_payload_does_not_publish_a_new_revision(db_session):
    payload = seeded_session(db_session)
    before_revision = db_session.scalar(
        select(DashboardRevision.id).where(DashboardRevision.active.is_(True))
    )

    outcome = refresh_tournament(
        db_session,
        providers=[StaticProvider(payload)],
        iterations=50,
        seed=8,
    )

    after_revision = db_session.scalar(
        select(DashboardRevision.id).where(DashboardRevision.active.is_(True))
    )
    assert outcome.updated_matches == 0
    assert outcome.revision_id is None
    assert after_revision == before_revision
