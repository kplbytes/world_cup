import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.models import (
    AIPrediction,
    DashboardRevision,
    EnsemblePrediction,
    Match,
    MatchPrediction,
    PredictionSnapshot,
    Team,
)
from app.services.snapshots import lock_due_predictions, repair_invalid_prediction_locks, write_snapshots

def _create_match_and_prediction(session: Session, match_id: str, kickoff: datetime, active_rev: DashboardRevision):
    # Setup teams if they don't exist
    if not session.get(Team, "TeamA"):
        session.add(Team(id="TeamA", name="Team A", short_name="TA", code="TMA", group_code="A"))
    if not session.get(Team, "TeamB"):
        session.add(Team(id="TeamB", name="Team B", short_name="TB", code="TMB", group_code="A"))
    session.flush()

    # Setup test match
    match = session.get(Match, match_id)
    if not match:
        match = Match(
            id=match_id,
            group_code="A",
            source="test",
            home_team_id="TeamA",
            away_team_id="TeamB",
            kickoff=kickoff,
            venue="Venue 1",
            status="scheduled"
        )
        session.add(match)
        session.flush()

    # Setup prediction
    pred = MatchPrediction(
        revision_id=active_rev.id,
        match_id=match_id,
        home_win=0.5,
        draw=0.3,
        away_win=0.2,
        home_xg=1.5,
        away_xg=1.0,
        scorelines=[{"home_goals": 1, "away_goals": 0, "probability": 0.1}],
        score_matrix=[[0.1]],
        confidence=0.8,
        confidence_label="High",
        data_confidence=0.9,
        data_confidence_label="High",
        model_confidence=0.85,
        model_confidence_label="High",
        explanation="Test",
        model_inputs={},
        model_version="v1"
    )
    session.add(pred)
    session.flush()
    return match, pred

def test_t30_snapshot_is_idempotent(db_session: Session):
    # 1. Create a revision
    rev = DashboardRevision(active=True, model_version="test", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()

    # 2. Match kicks off in 20 mins (so it is within T-30)
    now = datetime.now(timezone.utc)
    match_id = "test_match_1"
    kickoff = now + timedelta(minutes=20)
    _create_match_and_prediction(db_session, match_id, kickoff, rev)

    # 3. Write snapshots
    write_snapshots(db_session, rev, now=now)
    db_session.flush()

    # Should have a locked snapshot
    snap1 = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    )
    assert snap1 is not None
    assert snap1.is_pre_match_locked is True
    assert snap1.is_fallback_locked is False

    # 4. Now pretend another recompute happens 5 mins later with a new revision
    rev2 = DashboardRevision(active=True, model_version="test", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev2)
    db_session.flush()

    _create_match_and_prediction(db_session, match_id, kickoff, rev2) # Adds prediction for new rev
    write_snapshots(db_session, rev2, now=now + timedelta(minutes=5))
    db_session.flush()

    # There should NOT be a second locked snapshot for the same match
    locked_count = db_session.scalar(
        select(func.count(PredictionSnapshot.revision_id))
        .where(PredictionSnapshot.match_id == match_id, PredictionSnapshot.is_pre_match_locked.is_(True))
    )
    assert locked_count == 1

    # The new write_snapshots creates a new row for the latest prediction, but it's not locked.
    snapshots = list(db_session.scalars(select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)))
    assert len(snapshots) == 2

    locked_snap = [s for s in snapshots if s.is_pre_match_locked][0]
    assert locked_snap.revision_id == rev.id

def test_fallback_locked_created_if_t30_missed(db_session: Session):
    # 1. Create a snapshot 10 hours before kickoff
    rev1 = DashboardRevision(active=True, model_version="test", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev1)
    db_session.flush()

    now = datetime.now(timezone.utc)
    match_id = "test_match_fallback"
    kickoff = now + timedelta(hours=10)

    _create_match_and_prediction(db_session, match_id, kickoff, rev1)
    write_snapshots(db_session, rev1, now=now)
    db_session.flush()

    # Snapshot should not be locked
    snap1 = db_session.scalar(select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id))
    assert snap1.is_pre_match_locked is False
    assert snap1.is_fallback_locked is False

    # 2. Time travels to after kickoff, and another prediction is made
    rev2 = DashboardRevision(active=True, model_version="test2", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev2)
    db_session.flush()

    _create_match_and_prediction(db_session, match_id, kickoff, rev2)
    write_snapshots(db_session, rev2, now=kickoff + timedelta(minutes=5))
    db_session.flush()

    # The first snapshot should now be upgraded to fallback_locked
    # The new snapshot should NOT be locked
    snapshots = list(db_session.scalars(select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id).order_by(PredictionSnapshot.snapshotted_at)))
    assert len(snapshots) == 2

    assert snapshots[0].revision_id == rev1.id
    assert snapshots[0].is_fallback_locked is True
    assert snapshots[0].is_pre_match_locked is False

    assert snapshots[1].revision_id == rev2.id
    assert snapshots[1].is_fallback_locked is False
    assert snapshots[1].is_pre_match_locked is False

def test_no_pre_match_snapshot_does_not_create_fallback(db_session: Session):
    rev = DashboardRevision(active=True, model_version="test", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()

    now = datetime.now(timezone.utc)
    match_id = "test_match_no_snap"
    # Kickoff was 5 mins ago, but NO snapshot exists yet
    kickoff = now - timedelta(minutes=5)
    _create_match_and_prediction(db_session, match_id, kickoff, rev)

    write_snapshots(db_session, rev, now=now)
    db_session.flush()

    # The post-kickoff snapshot is created, but since there was no pre-match snapshot, NO lock is made
    snap = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    )
    assert snap is not None
    assert snap.is_pre_match_locked is False
    assert snap.is_fallback_locked is False

def test_post_kickoff_recompute_does_not_overwrite_locked(db_session: Session):
    rev1 = DashboardRevision(active=True, model_version="test", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev1)
    db_session.flush()

    now = datetime.now(timezone.utc)
    match_id = "test_match_started"
    kickoff = now + timedelta(minutes=10) # within T-30
    _create_match_and_prediction(db_session, match_id, kickoff, rev1)

    write_snapshots(db_session, rev1, now=now)
    db_session.flush()

    # Now kickoff happens
    rev2 = DashboardRevision(active=True, model_version="test", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev2)
    db_session.flush()
    _create_match_and_prediction(db_session, match_id, kickoff, rev2)

    write_snapshots(db_session, rev2, now=now + timedelta(minutes=20)) # 10 mins post kickoff
    db_session.flush()

    snapshots = list(db_session.scalars(select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)))
    # Only the pre-match locked snapshot from rev1 should exist
    assert len(snapshots) == 1
    assert snapshots[0].revision_id == rev1.id
    assert snapshots[0].is_pre_match_locked is True


def test_periodic_lock_uses_only_latest_pre_match_predictions(db_session: Session):
    kickoff = datetime.now(timezone.utc) + timedelta(hours=3)
    first_time = kickoff - timedelta(hours=2)
    second_time = kickoff - timedelta(hours=1)

    rev1 = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev1)
    db_session.flush()
    _create_match_and_prediction(db_session, "periodic_lock", kickoff, rev1)
    write_snapshots(db_session, rev1, now=first_time)

    rev2 = DashboardRevision(active=True, model_version="v2", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev2)
    db_session.flush()
    _create_match_and_prediction(db_session, "periodic_lock", kickoff, rev2)
    write_snapshots(db_session, rev2, now=second_time)

    older_ai = AIPrediction(
        match_id="periodic_lock", provider="test", model_id="flash", model_version="flash-v1",
        prompt_version="v1", parsed_home_win=0.4, parsed_draw=0.3, parsed_away_win=0.3,
        created_at=first_time,
    )
    latest_ai = AIPrediction(
        match_id="periodic_lock", provider="test", model_id="flash", model_version="flash-v1",
        prompt_version="v1", parsed_home_win=0.5, parsed_draw=0.3, parsed_away_win=0.2,
        created_at=second_time,
    )
    older_ensemble = EnsemblePrediction(
        match_id="periodic_lock", model_version="ensemble-v1", system_weight=1.0,
        market_weight=0.0, ensemble_home_win=0.4, ensemble_draw=0.3,
        ensemble_away_win=0.3, confidence=0.5, created_at=first_time,
    )
    latest_ensemble = EnsemblePrediction(
        match_id="periodic_lock", model_version="ensemble-v1", system_weight=1.0,
        market_weight=0.0, ensemble_home_win=0.5, ensemble_draw=0.3,
        ensemble_away_win=0.2, confidence=0.6, created_at=second_time,
    )
    db_session.add_all([older_ai, latest_ai, older_ensemble, latest_ensemble])
    db_session.flush()

    counts = lock_due_predictions(db_session, now=kickoff - timedelta(minutes=15))

    snapshots = list(db_session.scalars(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.match_id == "periodic_lock")
        .order_by(PredictionSnapshot.snapshotted_at)
    ))
    assert counts == {"matches": 1, "baseline": 1, "ai": 1, "ensemble": 1, "profile": 0, "shadow": 0}
    assert snapshots[0].is_pre_match_locked is False
    assert snapshots[1].is_pre_match_locked is True
    assert older_ai.is_pre_match_locked is False
    assert latest_ai.is_pre_match_locked is True
    assert older_ensemble.is_pre_match_locked is False
    assert latest_ensemble.is_pre_match_locked is True


def test_periodic_lock_does_not_lock_before_t30(db_session: Session):
    kickoff = datetime.now(timezone.utc) + timedelta(hours=2)
    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()
    _create_match_and_prediction(db_session, "outside_lock_window", kickoff, rev)
    write_snapshots(db_session, rev, now=kickoff - timedelta(hours=3))

    counts = lock_due_predictions(db_session, now=kickoff - timedelta(minutes=31), window_minutes=45)

    snapshot = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == "outside_lock_window")
    )
    assert counts == {"matches": 0, "baseline": 0, "ai": 0, "ensemble": 0, "profile": 0, "shadow": 0}
    assert snapshot.is_pre_match_locked is False


def test_repair_removes_locks_created_outside_t30(db_session: Session):
    kickoff = datetime.now(timezone.utc) + timedelta(hours=4)
    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()
    _create_match_and_prediction(db_session, "bad_historical_lock", kickoff, rev)

    invalid_time = kickoff - timedelta(hours=3)
    ai = AIPrediction(
        match_id="bad_historical_lock", provider="test", model_id="flash", model_version="flash-v1",
        prompt_version="v1", parsed_home_win=0.5, parsed_draw=0.3, parsed_away_win=0.2,
        created_at=invalid_time, locked_at=invalid_time, is_pre_match_locked=True,
    )
    ensemble = EnsemblePrediction(
        match_id="bad_historical_lock", model_version="ensemble-v1", system_weight=1.0,
        market_weight=0.0, ensemble_home_win=0.5, ensemble_draw=0.3,
        ensemble_away_win=0.2, confidence=0.6, created_at=invalid_time,
        locked_at=invalid_time, is_pre_match_locked=True,
    )
    db_session.add_all([ai, ensemble])
    db_session.flush()

    counts = repair_invalid_prediction_locks(db_session)

    assert counts == {"ai": 1, "ensemble": 1}
    assert ai.is_pre_match_locked is False
    assert ai.locked_at is None
    assert ensemble.is_pre_match_locked is False
    assert ensemble.locked_at is None
