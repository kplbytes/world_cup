"""Shared lock status computation for match predictions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class LockStatus:
    """Lock status for a match prediction."""
    is_pre_match_locked: bool = False
    is_fallback_locked: bool = False
    real_time_only: bool = False
    locked_at: datetime | None = None
    participates_in_model_score: bool = False


def compute_match_lock_status(
    match,
    now: datetime | None = None,
    lock_hours: int = 24,
) -> LockStatus:
    """Compute lock status for a match prediction.

    Args:
        match: Match object with kickoff attribute
        now: Current time (defaults to UTC now)
        lock_hours: Hours before kickoff to lock predictions

    Returns:
        LockStatus with appropriate flags set
    """
    if now is None:
        now = datetime.now(timezone.utc)

    kickoff = match.kickoff
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)

    status = LockStatus()

    lock_start = kickoff - timedelta(hours=lock_hours)

    if lock_start <= now < kickoff:
        status.is_pre_match_locked = True
        status.locked_at = now
        status.participates_in_model_score = True

    status.real_time_only = now >= kickoff

    # After match is final, don't participate in model score
    if hasattr(match, 'status') and match.status == 'final':
        status.participates_in_model_score = False

    return status


def _ensure_utc(dt):
    """Ensure a datetime is timezone-aware in UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class DecisionSnapshotStatus:
    """Status of the user decision snapshot for a match."""
    has_decision_snapshot: bool = False
    snapshot_at: datetime | None = None
    hours_before_kickoff: float | None = None
    is_real_time_only: bool = False
    participates_in_scoring: bool = False
    rule: str = "latest_pre_match_snapshot_before_kickoff"


def compute_decision_snapshot_status(
    match,
    snapshots: list,
    now: datetime | None = None,
) -> DecisionSnapshotStatus:
    """Compute the decision snapshot status for a match.

    Under the new rule, any pre-kickoff snapshot is a valid decision snapshot.
    The latest one before kickoff is used for scoring.
    24h locking is no longer the core scoring mechanism.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    kickoff = match.kickoff
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)

    status = DecisionSnapshotStatus()

    if not snapshots:
        return status

    # Find latest pre-kickoff snapshot
    pre_kickoff = [s for s in snapshots if _ensure_utc(s.snapshotted_at) < kickoff]
    if pre_kickoff:
        latest = max(pre_kickoff, key=lambda s: _ensure_utc(s.snapshotted_at))
        status.has_decision_snapshot = True
        status.snapshot_at = latest.snapshotted_at
        status.hours_before_kickoff = (kickoff - _ensure_utc(latest.snapshotted_at)).total_seconds() / 3600
        status.participates_in_scoring = True

    # Check if current time is past kickoff (real-time only)
    status.is_real_time_only = now >= kickoff

    return status
