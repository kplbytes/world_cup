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
    TeamProfilePrediction,
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

    # 2. Match kicks off in 20 mins (so it is within 24h lock window)
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

    # There should still be only 1 locked snapshot (updated in place)
    locked_count = db_session.scalar(
        select(func.count(PredictionSnapshot.revision_id))
        .where(PredictionSnapshot.match_id == match_id, PredictionSnapshot.is_pre_match_locked.is_(True))
    )
    assert locked_count == 1

    # The new write_snapshots updates the existing locked snapshot in place
    snapshots = list(db_session.scalars(select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)))
    assert len(snapshots) == 1

    locked_snap = [s for s in snapshots if s.is_pre_match_locked][0]
    assert locked_snap.revision_id == rev2.id  # Updated to latest revision

def test_fallback_locked_created_if_lock_window_missed(db_session: Session):
    # 1. Create a snapshot 30 hours before kickoff (outside 24h lock window)
    rev1 = DashboardRevision(active=True, model_version="test", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev1)
    db_session.flush()

    now = datetime.now(timezone.utc)
    match_id = "test_match_fallback"
    kickoff = now + timedelta(hours=30)

    _create_match_and_prediction(db_session, match_id, kickoff, rev1)
    write_snapshots(db_session, rev1, now=now)
    db_session.flush()

    # Snapshot should not be locked (outside 24h window)
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
    kickoff = now + timedelta(minutes=10) # within 24h lock window
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
    kickoff = datetime.now(timezone.utc) + timedelta(hours=25)
    first_time = kickoff - timedelta(hours=26)
    second_time = kickoff - timedelta(hours=25)

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

    # Now lock from within the 24h window
    counts = lock_due_predictions(db_session, now=kickoff - timedelta(hours=20))

    snapshots = list(db_session.scalars(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.match_id == "periodic_lock")
        .order_by(PredictionSnapshot.snapshotted_at)
    ))
    assert counts == {"matches": 1, "baseline": 1, "ai": 1, "ensemble": 1, "shadow": 0}
    assert snapshots[0].is_pre_match_locked is False
    assert snapshots[1].is_pre_match_locked is True
    assert older_ai.is_pre_match_locked is False
    assert latest_ai.is_pre_match_locked is True
    assert older_ensemble.is_pre_match_locked is False
    assert latest_ensemble.is_pre_match_locked is True


def test_periodic_lock_does_not_lock_legacy_team_profile_predictions(db_session: Session):
    kickoff = datetime.now(timezone.utc) + timedelta(hours=20)
    created_at = kickoff - timedelta(hours=2)
    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()
    _create_match_and_prediction(db_session, "profile_lock_isolated", kickoff, rev)
    write_snapshots(db_session, rev, now=created_at)
    legacy_profile = TeamProfilePrediction(
        revision_id=rev.id,
        match_id="profile_lock_isolated",
        model_version="legacy-profile-v1",
        profile_version="team-profile-v1",
        profile_as_of=created_at,
        base_home_win=0.5,
        base_draw=0.3,
        base_away_win=0.2,
        home_win=0.52,
        draw=0.29,
        away_win=0.19,
        home_xg=1.4,
        away_xg=0.9,
        probability_deltas_json={},
        xg_deltas_json={},
        explanation="legacy",
        created_at=created_at,
    )
    db_session.add(legacy_profile)
    db_session.flush()

    counts = lock_due_predictions(db_session, now=kickoff - timedelta(hours=1))

    assert "profile" not in counts
    assert legacy_profile.is_pre_match_locked is False
    assert legacy_profile.is_fallback_locked is False
    assert legacy_profile.locked_at is None


def test_periodic_lock_does_not_lock_outside_24h_window(db_session: Session):
    kickoff = datetime.now(timezone.utc) + timedelta(hours=30)
    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()
    _create_match_and_prediction(db_session, "outside_lock_window", kickoff, rev)
    write_snapshots(db_session, rev, now=kickoff - timedelta(hours=26))

    counts = lock_due_predictions(db_session, now=kickoff - timedelta(hours=25), window_hours=24)

    snapshot = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == "outside_lock_window")
    )
    assert counts == {"matches": 0, "baseline": 0, "ai": 0, "ensemble": 0, "shadow": 0}
    assert snapshot.is_pre_match_locked is False


def test_repair_removes_locks_created_outside_24h_window(db_session: Session):
    kickoff = datetime.now(timezone.utc) + timedelta(hours=4)
    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()
    _create_match_and_prediction(db_session, "bad_historical_lock", kickoff, rev)

    invalid_time = kickoff - timedelta(hours=25)  # Outside 24h lock window
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


# P0-5: Regression tests — post-match data must not pollute pre-match snapshot

def test_match_result_update_does_not_change_pre_match_snapshot(db_session: Session):
    """After a match result is synced, the pre-match snapshot probabilities must remain unchanged."""
    from app.services.dashboard import _compute_match_review, _display_snapshots

    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()

    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(minutes=20)  # within 24h lock window
    match_id = "p05_result_pollution_test"
    match, pred = _create_match_and_prediction(db_session, match_id, kickoff, rev)

    # Write and lock pre-match snapshot
    write_snapshots(db_session, rev, now=now)
    db_session.flush()

    snap = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    )
    assert snap is not None
    assert snap.is_pre_match_locked is True

    # Record pre-match snapshot values
    pre_match_home_win = snap.home_win
    pre_match_draw = snap.draw
    pre_match_away_win = snap.away_win

    # Now update match result (simulating post-match sync)
    match.status = "final"
    match.home_score = 2
    match.away_score = 1
    db_session.flush()

    # Re-read the snapshot
    db_session.expire(snap)
    snap_after = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    )

    # Snapshot probabilities must NOT have changed
    assert snap_after.home_win == pre_match_home_win
    assert snap_after.draw == pre_match_draw
    assert snap_after.away_win == pre_match_away_win
    assert snap_after.is_pre_match_locked is True


def test_match_review_uses_pre_match_prediction_not_post_match(db_session: Session):
    """match_review must compute Brier/deviations based on pre-match snapshot,
    not any post-match updated prediction."""
    from app.services.dashboard import _compute_match_review

    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()

    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(minutes=20)
    match_id = "p05_review_snapshot_test"
    match, pred = _create_match_and_prediction(db_session, match_id, kickoff, rev)

    # Pre-match snapshot: home_win=0.5, draw=0.3, away_win=0.2
    write_snapshots(db_session, rev, now=now)
    db_session.flush()

    snap = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    )
    assert snap.is_pre_match_locked is True

    # Finalize match: away team wins (upset)
    match.status = "final"
    match.home_score = 0
    match.away_score = 1
    db_session.flush()

    # Compute match_review using the pre-match snapshot
    review = _compute_match_review(match, snap, [], [], None)

    assert review is not None
    assert review["actual_result"] == "away"
    # Baseline predicted home (0.5 > 0.3 > 0.2), but actual was away
    assert review["baseline"]["predicted_result"] == "home"
    assert review["baseline"]["outcome_hit"] is False
    # Brier must be based on pre-match probabilities (0.5, 0.3, 0.2)
    # actual = (0, 0, 1), so Brier = (0.5-0)^2 + (0.3-0)^2 + (0.2-1)^2 = 0.25+0.09+0.64 = 0.98
    assert review["baseline"]["brier"] == round(0.98, 4)
    # actual_probability for away must be 0.2 (the pre-match away_win)
    assert review["baseline"]["actual_probability"] == 0.2


def test_match_review_does_not_write_back_to_snapshot(db_session: Session):
    """Computing match_review must not modify the PredictionSnapshot row."""
    from app.services.dashboard import _compute_match_review

    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()

    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(minutes=20)
    match_id = "p05_no_writeback_test"
    match, pred = _create_match_and_prediction(db_session, match_id, kickoff, rev)

    write_snapshots(db_session, rev, now=now)
    db_session.flush()

    snap = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    )
    original_home_win = snap.home_win
    original_draw = snap.draw
    original_away_win = snap.away_win

    match.status = "final"
    match.home_score = 1
    match.away_score = 0
    db_session.flush()

    # Compute review multiple times
    _compute_match_review(match, snap, [], [], None)
    _compute_match_review(match, snap, [], [], None)

    # Snapshot must be unchanged
    db_session.expire(snap)
    snap_check = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    )
    assert snap_check.home_win == original_home_win
    assert snap_check.draw == original_draw
    assert snap_check.away_win == original_away_win


# Regression tests for 24h lock window business rules

def test_no_lock_before_24h_window(db_session: Session):
    """Rule 4: Matches more than 24h away must NOT generate a locked snapshot."""
    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()

    now = datetime.now(timezone.utc)
    match_id = "test_no_lock_before_24h"
    kickoff = now + timedelta(hours=30)  # 30h away, outside 24h window
    _create_match_and_prediction(db_session, match_id, kickoff, rev)

    write_snapshots(db_session, rev, now=now)
    db_session.flush()

    snap = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    )
    assert snap is not None
    assert snap.is_pre_match_locked is False
    assert snap.is_fallback_locked is False


def test_create_lock_inside_24h_window(db_session: Session):
    """Rule 1: Matches within 24h of kickoff must generate a locked snapshot."""
    rev = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev)
    db_session.flush()

    now = datetime.now(timezone.utc)
    match_id = "test_create_lock_inside_24h"
    kickoff = now + timedelta(hours=12)  # 12h away, inside 24h window
    _create_match_and_prediction(db_session, match_id, kickoff, rev)

    write_snapshots(db_session, rev, now=now)
    db_session.flush()

    snap = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    )
    assert snap is not None
    assert snap.is_pre_match_locked is True
    assert snap.is_fallback_locked is False


def test_update_existing_locked_snapshot_before_kickoff(db_session: Session):
    """Rule 2: Before kickoff, a new prediction must update the existing locked snapshot in-place."""
    rev1 = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev1)
    db_session.flush()

    now = datetime.now(timezone.utc)
    match_id = "test_update_locked_before_kickoff"
    kickoff = now + timedelta(hours=12)  # Inside 24h window
    match, pred1 = _create_match_and_prediction(db_session, match_id, kickoff, rev1)
    pred1.home_win = 0.6
    pred1.draw = 0.25
    pred1.away_win = 0.15
    db_session.flush()

    write_snapshots(db_session, rev1, now=now)
    db_session.flush()

    # Verify initial locked snapshot
    snap1 = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    )
    assert snap1 is not None
    assert snap1.is_pre_match_locked is True
    assert snap1.home_win == 0.6
    original_snap_id = snap1.id

    # New prediction arrives (different revision, still before kickoff)
    rev2 = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev2)
    db_session.flush()

    _, pred2 = _create_match_and_prediction(db_session, match_id, kickoff, rev2)
    pred2.home_win = 0.45
    pred2.draw = 0.30
    pred2.away_win = 0.25
    db_session.flush()

    write_snapshots(db_session, rev2, now=now + timedelta(hours=1))
    db_session.flush()

    # Verify the locked snapshot was updated in-place (same row, new values)
    all_snaps = list(db_session.scalars(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    ))
    locked_snaps = [s for s in all_snaps if s.is_pre_match_locked]
    assert len(locked_snaps) == 1, f"Expected 1 locked snapshot, got {len(locked_snaps)}"
    assert locked_snaps[0].id == original_snap_id, "Should be the same row (in-place update)"
    assert locked_snaps[0].home_win == 0.45, "Should have updated home_win to new value"
    assert locked_snaps[0].draw == 0.30
    assert locked_snaps[0].away_win == 0.25
    assert locked_snaps[0].revision_id == rev2.id


def test_do_not_update_locked_snapshot_after_kickoff(db_session: Session):
    """Rule 3: After kickoff, the locked snapshot must NOT be updated even if new predictions arrive."""
    rev1 = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev1)
    db_session.flush()

    now = datetime.now(timezone.utc)
    match_id = "test_no_update_after_kickoff"
    kickoff = now + timedelta(hours=12)
    match, pred1 = _create_match_and_prediction(db_session, match_id, kickoff, rev1)
    pred1.home_win = 0.7
    pred1.draw = 0.2
    pred1.away_win = 0.1
    db_session.flush()

    write_snapshots(db_session, rev1, now=now)
    db_session.flush()

    # Verify initial locked snapshot
    snap1 = db_session.scalar(
        select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)
    )
    assert snap1.is_pre_match_locked is True
    assert snap1.home_win == 0.7
    original_home_win = snap1.home_win

    # Now time has passed and match has kicked off
    rev2 = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(rev2)
    db_session.flush()

    _, pred2 = _create_match_and_prediction(db_session, match_id, kickoff, rev2)
    pred2.home_win = 0.3  # Very different prediction
    pred2.draw = 0.3
    pred2.away_win = 0.4
    db_session.flush()

    # Write snapshot after kickoff
    write_snapshots(db_session, rev2, now=kickoff + timedelta(minutes=10))
    db_session.flush()

    # The original locked snapshot must NOT have been updated
    db_session.expire(snap1)
    locked_snaps = list(db_session.scalars(
        select(PredictionSnapshot).where(
            PredictionSnapshot.match_id == match_id,
            PredictionSnapshot.is_pre_match_locked.is_(True),
        )
    ))
    assert len(locked_snaps) == 1
    assert locked_snaps[0].home_win == original_home_win, \
        f"Locked snapshot should not be updated after kickoff, got {locked_snaps[0].home_win}"
