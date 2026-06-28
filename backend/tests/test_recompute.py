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
from app.services.recompute import _compute_data_context, _prune_old_revisions, recompute_all, recompute_knockout_stage
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


def test_recompute_all_uses_profile_adjustments_without_market_data(db_session, monkeypatch):
    seed_database(db_session)
    match_id = db_session.scalar(
        select(Match.id).where(Match.status == "scheduled", Match.stage == "group").order_by(Match.kickoff.asc())
    )
    assert match_id is not None

    monkeypatch.setattr("app.team_profiles.service.get_team_profile", lambda session, team_id, as_of_date=None: {"team_id": team_id})

    monkeypatch.setattr(
        "app.prediction.profile_adapter.compute_profile_adjustments",
        lambda home, away: {
            "profile_home_attack": 0.20,
            "profile_home_defense": 0.00,
            "profile_away_attack": -0.10,
            "profile_away_defense": 0.00,
            "profile_home_form": 0.04,
            "profile_away_form": 0.00,
            "profile_draw_adjustment": 0.02,
            "profile_available": True,
            "profile_risk_flags": ["group_profile_test"],
        },
    )
    with_profile_revision = recompute_all(db_session, iterations=50, seed=9)
    with_profile = db_session.scalar(
        select(MatchPrediction)
        .where(MatchPrediction.revision_id == with_profile_revision.id, MatchPrediction.match_id == match_id)
        .order_by(MatchPrediction.id.asc())
    )

    monkeypatch.setattr(
        "app.prediction.profile_adapter.compute_profile_adjustments",
        lambda home, away: {
            "profile_home_attack": 0.0,
            "profile_home_defense": 0.0,
            "profile_away_attack": 0.0,
            "profile_away_defense": 0.0,
            "profile_home_form": 0.0,
            "profile_away_form": 0.0,
            "profile_draw_adjustment": 0.0,
            "profile_available": False,
            "profile_risk_flags": [],
        },
    )
    without_profile_revision = recompute_all(db_session, iterations=50, seed=9)
    without_profile = db_session.scalar(
        select(MatchPrediction)
        .where(MatchPrediction.revision_id == without_profile_revision.id, MatchPrediction.match_id == match_id)
        .order_by(MatchPrediction.id.asc())
    )

    assert with_profile is not None
    assert without_profile is not None
    assert with_profile.model_inputs["profile_adjustments"]["profile_available"] is True
    assert with_profile.model_inputs["profile_adjustments"]["home_attack"] == 0.20
    assert (
        with_profile.home_xg != without_profile.home_xg
        or with_profile.home_win != without_profile.home_win
        or with_profile.draw != without_profile.draw
    )


def test_recompute_knockout_stage_uses_profile_adjustments(db_session, monkeypatch):
    seed_database(db_session)
    db_session.add(
        Match(
            id="ko-profile-test",
            group_code=None,
            home_team_id="MEX",
            away_team_id="KOR",
            kickoff=db_session.scalar(select(Match.kickoff).where(Match.id == "2026-A-MEX-KOR-2026-06-18")) + timedelta(days=10),
            status="scheduled",
            source="test",
            stage="round_of_32",
            round_name="32强",
            bracket_position=73,
        )
    )
    db_session.commit()

    monkeypatch.setattr("app.team_profiles.service.get_team_profile", lambda session, team_id, as_of_date=None: {"team_id": team_id})

    monkeypatch.setattr(
        "app.prediction.profile_adapter.compute_profile_adjustments",
        lambda home, away: {
            "profile_home_attack": 0.20,
            "profile_home_defense": 0.00,
            "profile_away_attack": -0.10,
            "profile_away_defense": 0.00,
            "profile_home_form": 0.04,
            "profile_away_form": 0.00,
            "profile_draw_adjustment": 0.02,
            "profile_available": True,
            "profile_risk_flags": ["knockout_profile_test"],
        },
    )
    with_profile_revision = recompute_knockout_stage(db_session, iterations=50, seed=9)
    with_profile = db_session.scalar(
        select(MatchPrediction)
        .where(MatchPrediction.revision_id == with_profile_revision.id, MatchPrediction.match_id == "ko-profile-test")
        .order_by(MatchPrediction.id.asc())
    )

    monkeypatch.setattr(
        "app.prediction.profile_adapter.compute_profile_adjustments",
        lambda home, away: {
            "profile_home_attack": 0.0,
            "profile_home_defense": 0.0,
            "profile_away_attack": 0.0,
            "profile_away_defense": 0.0,
            "profile_home_form": 0.0,
            "profile_away_form": 0.0,
            "profile_draw_adjustment": 0.0,
            "profile_available": False,
            "profile_risk_flags": [],
        },
    )
    without_profile_revision = recompute_knockout_stage(db_session, iterations=50, seed=9)
    without_profile = db_session.scalar(
        select(MatchPrediction)
        .where(MatchPrediction.revision_id == without_profile_revision.id, MatchPrediction.match_id == "ko-profile-test")
        .order_by(MatchPrediction.id.asc())
    )

    assert with_profile is not None
    assert without_profile is not None
    assert with_profile.model_inputs["profile_available"] is True
    assert with_profile.model_inputs["profile_adjustments"]["home_attack"] == 0.20
    assert (
        with_profile.home_xg != without_profile.home_xg
        or with_profile.home_win != without_profile.home_win
        or with_profile.draw != without_profile.draw
    )
