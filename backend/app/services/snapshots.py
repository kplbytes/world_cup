from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.lock_status import compute_match_lock_status
from app.models import (
    AIPrediction,
    DashboardRevision,
    EnsemblePrediction,
    Match,
    MatchPrediction,
    PredictionSnapshot,
    TeamProfilePrediction,
)
from app.prediction.shadow import SHADOW_MODEL_VERSIONS


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def lock_due_predictions(
    session: Session,
    now: datetime | None = None,
    window_hours: int = 24,
) -> dict[str, int]:
    """Lock the latest valid pre-match prediction for matches within 24h of kickoff.

    Any match with a kickoff within the next `window_hours` hours that has
    a prediction will be locked immediately. This replaces the old T-30 logic.
    """
    now = _ensure_utc(now or datetime.now(timezone.utc))
    window_end = now + timedelta(hours=window_hours)
    counts = {"matches": 0, "baseline": 0, "shadow": 0, "ai": 0, "ensemble": 0, "profile": 0}

    matches = list(session.scalars(
        select(Match)
        .where(Match.status != "final")
        .where(Match.kickoff >= now)
        .where(Match.kickoff <= window_end)
        .order_by(Match.kickoff)
    ))

    for match in matches:
        lock = compute_match_lock_status(match, now)
        if not lock.is_pre_match_locked:
            continue

        counts["matches"] += 1
        kickoff = _ensure_utc(match.kickoff)

        existing_baseline = session.scalar(
            select(PredictionSnapshot.match_id)
            .where(
                PredictionSnapshot.match_id == match.id,
                (PredictionSnapshot.is_pre_match_locked.is_(True))
                | (PredictionSnapshot.is_fallback_locked.is_(True)),
            )
            .limit(1)
        )
        if existing_baseline is None:
            baseline = session.scalar(
                select(PredictionSnapshot)
                .where(
                    PredictionSnapshot.match_id == match.id,
                    PredictionSnapshot.snapshotted_at < kickoff,
                )
                .order_by(PredictionSnapshot.snapshotted_at.desc())
                .limit(1)
            )
            if baseline is not None:
                baseline.is_pre_match_locked = True
                counts["baseline"] += 1

        # Lock shadow model snapshots alongside baseline
        for shadow_version in SHADOW_MODEL_VERSIONS:
            existing_shadow_locked = session.scalar(
                select(PredictionSnapshot.match_id)
                .where(
                    PredictionSnapshot.match_id == match.id,
                    PredictionSnapshot.model_version == shadow_version,
                    (PredictionSnapshot.is_pre_match_locked.is_(True))
                    | (PredictionSnapshot.is_fallback_locked.is_(True)),
                )
                .limit(1)
            )
            if existing_shadow_locked is None:
                shadow_snap = session.scalar(
                    select(PredictionSnapshot)
                    .where(
                        PredictionSnapshot.match_id == match.id,
                        PredictionSnapshot.model_version == shadow_version,
                        PredictionSnapshot.snapshotted_at < kickoff,
                    )
                    .order_by(PredictionSnapshot.snapshotted_at.desc())
                    .limit(1)
                )
                if shadow_snap is not None:
                    shadow_snap.is_pre_match_locked = True
                    counts["shadow"] += 1

        ai_predictions = list(session.scalars(
            select(AIPrediction)
            .where(
                AIPrediction.match_id == match.id,
                AIPrediction.error_code.is_(None),
                AIPrediction.parsed_home_win.is_not(None),
                AIPrediction.parsed_draw.is_not(None),
                AIPrediction.parsed_away_win.is_not(None),
                AIPrediction.created_at < kickoff,
            )
            .order_by(AIPrediction.created_at.desc())
        ))
        locked_ai_models = set(session.scalars(
            select(AIPrediction.model_version).where(
                AIPrediction.match_id == match.id,
                (AIPrediction.is_pre_match_locked.is_(True))
                | (AIPrediction.is_fallback_locked.is_(True)),
            )
        ))
        seen_models: set[str] = set()
        for prediction in ai_predictions:
            if prediction.model_version in locked_ai_models or prediction.model_version in seen_models:
                continue
            seen_models.add(prediction.model_version)
            prediction.is_pre_match_locked = True
            prediction.locked_at = now
            prediction.real_time_only = False
            counts["ai"] += 1

        existing_ensemble = session.scalar(
            select(EnsemblePrediction.id)
            .where(
                EnsemblePrediction.match_id == match.id,
                EnsemblePrediction.is_pre_match_locked.is_(True),
            )
            .limit(1)
        )
        if existing_ensemble is None:
            ensemble = session.scalar(
                select(EnsemblePrediction)
                .where(
                    EnsemblePrediction.match_id == match.id,
                    EnsemblePrediction.created_at < kickoff,
                )
                .order_by(EnsemblePrediction.created_at.desc())
                .limit(1)
            )
            if ensemble is not None:
                ensemble.is_pre_match_locked = True
                ensemble.locked_at = now
                counts["ensemble"] += 1

        existing_profile = session.scalar(
            select(TeamProfilePrediction.id).where(
                TeamProfilePrediction.match_id == match.id,
                (TeamProfilePrediction.is_pre_match_locked.is_(True))
                | (TeamProfilePrediction.is_fallback_locked.is_(True)),
            ).limit(1)
        )
        if existing_profile is None:
            profile_prediction = session.scalar(
                select(TeamProfilePrediction)
                .where(
                    TeamProfilePrediction.match_id == match.id,
                    TeamProfilePrediction.created_at < kickoff,
                    TeamProfilePrediction.real_time_only.is_(False),
                )
                .order_by(TeamProfilePrediction.created_at.desc())
                .limit(1)
            )
            if profile_prediction is not None:
                profile_prediction.is_pre_match_locked = True
                profile_prediction.locked_at = now
                counts["profile"] += 1

    session.flush()
    return counts


def repair_invalid_prediction_locks(session: Session) -> dict[str, int]:
    """Remove historical AI/ensemble locks created outside the 24h lock window.

    This is for data cleanup only, not a core scoring function.
    Under the new scoring rule, lock status does not affect scoring eligibility.
    """
    counts = {"ai": 0, "ensemble": 0}
    matches = {match.id: match for match in session.scalars(select(Match))}

    for prediction in session.scalars(
        select(AIPrediction).where(AIPrediction.is_pre_match_locked.is_(True))
    ):
        match = matches.get(prediction.match_id)
        if match is None or prediction.locked_at is None:
            continue
        kickoff = _ensure_utc(match.kickoff)
        locked_at = _ensure_utc(prediction.locked_at)
        if not kickoff - timedelta(hours=24) <= locked_at < kickoff:
            prediction.is_pre_match_locked = False
            prediction.locked_at = None
            prediction.real_time_only = _ensure_utc(prediction.created_at) >= kickoff
            counts["ai"] += 1

    for prediction in session.scalars(
        select(EnsemblePrediction).where(EnsemblePrediction.is_pre_match_locked.is_(True))
    ):
        match = matches.get(prediction.match_id)
        if match is None or prediction.locked_at is None:
            continue
        kickoff = _ensure_utc(match.kickoff)
        locked_at = _ensure_utc(prediction.locked_at)
        if not kickoff - timedelta(hours=24) <= locked_at < kickoff:
            prediction.is_pre_match_locked = False
            prediction.locked_at = None
            counts["ensemble"] += 1

    session.flush()
    return counts

def write_snapshots(session: Session, revision: DashboardRevision, now: datetime | None = None) -> None:
    """Write PredictionSnapshot records for all unstarted or live matches based on the new revision.

    Matches within 24h of kickoff are locked immediately upon snapshot creation.
    """
    now = now or datetime.now(timezone.utc)
    lock_threshold = timedelta(hours=24)

    # Get all non-final matches
    matches = list(session.scalars(select(Match).where(Match.status != "final")))
    if not matches:
        return

    # Baseline predictions (non-shadow)
    predictions = {
        p.match_id: p
        for p in session.scalars(
            select(MatchPrediction).where(
                MatchPrediction.revision_id == revision.id,
                MatchPrediction.model_version.notin_(SHADOW_MODEL_VERSIONS),
            )
        )
    }

    # Group shadow model predictions by match_id
    shadow_predictions: dict[str, list[MatchPrediction]] = {}
    for p in session.scalars(
        select(MatchPrediction).where(
            MatchPrediction.revision_id == revision.id,
            MatchPrediction.model_version.in_(SHADOW_MODEL_VERSIONS),
        )
    ):
        shadow_predictions.setdefault(p.match_id, []).append(p)

    for match in matches:
        pred = predictions.get(match.id)
        if not pred:
            continue

        # Check if already locked
        existing_locked = session.scalar(
            select(PredictionSnapshot)
            .where(
                PredictionSnapshot.match_id == match.id,
                (PredictionSnapshot.is_pre_match_locked.is_(True)) | (PredictionSnapshot.is_fallback_locked.is_(True))
            )
            .limit(1)
        )

        # Check if an unlocked snapshot already exists for this revision + model_version
        existing_snap = session.scalar(
            select(PredictionSnapshot)
            .where(
                PredictionSnapshot.match_id == match.id,
                PredictionSnapshot.revision_id == revision.id,
                PredictionSnapshot.model_version == pred.model_version,
            )
            .order_by(PredictionSnapshot.snapshotted_at.desc())
            .limit(1)
        )

        # If the match has already started and is locked, skip it completely.
        # If it has started but not locked, we must create a fallback.
        # If it hasn't started, we write a snapshot. If within 24h lock window, we lock it.

        kickoff = match.kickoff
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)

        is_past_kickoff = now >= kickoff
        is_within_lock_window = now >= kickoff - lock_threshold

        if existing_locked and is_past_kickoff:
            # Fully locked and started, no more snapshots allowed
            continue

        is_locked = False
        is_fallback = False

        if not existing_locked:
            if is_past_kickoff:
                # We missed the 24h lock window and the match has kicked off.
                # We CANNOT use the current prediction (generated post-kickoff) as fallback.
                # Instead, try to upgrade the latest pre-match snapshot.
                latest_pre_match = session.scalar(
                    select(PredictionSnapshot)
                    .where(
                        PredictionSnapshot.match_id == match.id,
                        PredictionSnapshot.snapshotted_at < kickoff
                    )
                    .order_by(desc(PredictionSnapshot.snapshotted_at))
                    .limit(1)
                )
                if latest_pre_match and not latest_pre_match.is_fallback_locked:
                    latest_pre_match.is_fallback_locked = True
                    session.add(latest_pre_match)
                # Current post-kickoff snapshot remains unlocked
                is_fallback = False
            elif is_within_lock_window:
                # Within 24h lock window
                is_locked = True

        # If an existing_locked exists but the match hasn't kicked off yet,
        # update the locked snapshot with the latest prediction values.
        if existing_locked and not is_past_kickoff:
            # Update the existing locked snapshot with latest prediction
            existing_locked.home_win = pred.home_win
            existing_locked.draw = pred.draw
            existing_locked.away_win = pred.away_win
            existing_locked.home_xg = pred.home_xg
            existing_locked.away_xg = pred.away_xg
            existing_locked.has_auto_adjustments = pred.has_auto_adjustments
            existing_locked.base_home_win = pred.base_home_win
            existing_locked.base_draw = pred.base_draw
            existing_locked.base_away_win = pred.base_away_win
            existing_locked.scorelines = pred.scorelines
            existing_locked.score_matrix = pred.score_matrix
            existing_locked.confidence = pred.confidence
            existing_locked.confidence_label = pred.confidence_label
            existing_locked.model_inputs = pred.model_inputs
            existing_locked.model_version = pred.model_version
            existing_locked.snapshotted_at = now
            existing_locked.revision_id = revision.id
            session.add(existing_locked)

            # Also update shadow snapshots
            for shadow_pred in shadow_predictions.get(match.id, []):
                existing_shadow_locked = session.scalar(
                    select(PredictionSnapshot)
                    .where(
                        PredictionSnapshot.match_id == match.id,
                        PredictionSnapshot.model_version == shadow_pred.model_version,
                        (PredictionSnapshot.is_pre_match_locked.is_(True))
                        | (PredictionSnapshot.is_fallback_locked.is_(True)),
                    )
                    .limit(1)
                )
                if existing_shadow_locked:
                    existing_shadow_locked.home_win = shadow_pred.home_win
                    existing_shadow_locked.draw = shadow_pred.draw
                    existing_shadow_locked.away_win = shadow_pred.away_win
                    existing_shadow_locked.home_xg = shadow_pred.home_xg
                    existing_shadow_locked.away_xg = shadow_pred.away_xg
                    existing_shadow_locked.has_auto_adjustments = shadow_pred.has_auto_adjustments
                    existing_shadow_locked.base_home_win = shadow_pred.base_home_win
                    existing_shadow_locked.base_draw = shadow_pred.base_draw
                    existing_shadow_locked.base_away_win = shadow_pred.base_away_win
                    existing_shadow_locked.scorelines = shadow_pred.scorelines
                    existing_shadow_locked.score_matrix = shadow_pred.score_matrix
                    existing_shadow_locked.confidence = shadow_pred.confidence
                    existing_shadow_locked.confidence_label = shadow_pred.confidence_label
                    existing_shadow_locked.model_inputs = shadow_pred.model_inputs
                    existing_shadow_locked.model_version = shadow_pred.model_version
                    existing_shadow_locked.snapshotted_at = now
                    existing_shadow_locked.revision_id = revision.id
                    session.add(existing_shadow_locked)
            continue

        # Update or create snapshot
        if existing_snap and not existing_snap.is_pre_match_locked and not existing_snap.is_fallback_locked:
            # Update existing unlocked snapshot
            existing_snap.home_win = pred.home_win
            existing_snap.draw = pred.draw
            existing_snap.away_win = pred.away_win
            existing_snap.home_xg = pred.home_xg
            existing_snap.away_xg = pred.away_xg
            existing_snap.has_auto_adjustments = pred.has_auto_adjustments
            existing_snap.base_home_win = pred.base_home_win
            existing_snap.base_draw = pred.base_draw
            existing_snap.base_away_win = pred.base_away_win
            existing_snap.scorelines = pred.scorelines
            existing_snap.score_matrix = pred.score_matrix
            existing_snap.confidence = pred.confidence
            existing_snap.confidence_label = pred.confidence_label
            existing_snap.model_inputs = pred.model_inputs
            existing_snap.model_version = pred.model_version
            existing_snap.snapshotted_at = now
            existing_snap.is_pre_match_locked = is_locked
            existing_snap.is_fallback_locked = is_fallback
            existing_snap.kickoff = kickoff
            session.add(existing_snap)
        elif not existing_snap:
            snap = PredictionSnapshot(
                match_id=match.id,
                revision_id=revision.id,
                kickoff=kickoff,
                is_pre_match_locked=is_locked,
                is_fallback_locked=is_fallback,
                home_win=pred.home_win,
                draw=pred.draw,
                away_win=pred.away_win,
                home_xg=pred.home_xg,
                away_xg=pred.away_xg,
                has_auto_adjustments=pred.has_auto_adjustments,
                base_home_win=pred.base_home_win,
                base_draw=pred.base_draw,
                base_away_win=pred.base_away_win,
                scorelines=pred.scorelines,
                score_matrix=pred.score_matrix,
                confidence=pred.confidence,
                confidence_label=pred.confidence_label,
                model_inputs=pred.model_inputs,
                model_version=pred.model_version,
                snapshotted_at=now
            )
            session.add(snap)

        # Shadow model snapshots
        for shadow_pred in shadow_predictions.get(match.id, []):
            # Check if this shadow version already has a locked snapshot
            existing_shadow_locked = session.scalar(
                select(PredictionSnapshot)
                .where(
                    PredictionSnapshot.match_id == match.id,
                    PredictionSnapshot.model_version == shadow_pred.model_version,
                    (PredictionSnapshot.is_pre_match_locked.is_(True))
                    | (PredictionSnapshot.is_fallback_locked.is_(True)),
                )
                .limit(1)
            )
            shadow_is_locked = False
            shadow_is_fallback = False
            if existing_shadow_locked and is_past_kickoff:
                continue
            if not existing_shadow_locked:
                if is_past_kickoff:
                    # Try to upgrade the latest pre-match shadow snapshot
                    latest_shadow_pre = session.scalar(
                        select(PredictionSnapshot)
                        .where(
                            PredictionSnapshot.match_id == match.id,
                            PredictionSnapshot.model_version == shadow_pred.model_version,
                            PredictionSnapshot.snapshotted_at < kickoff,
                        )
                        .order_by(desc(PredictionSnapshot.snapshotted_at))
                        .limit(1)
                    )
                    if latest_shadow_pre and not latest_shadow_pre.is_fallback_locked:
                        latest_shadow_pre.is_fallback_locked = True
                        session.add(latest_shadow_pre)
                elif is_within_lock_window:
                    shadow_is_locked = True

            # Check if existing unlocked snapshot for this shadow model
            existing_shadow_snap = session.scalar(
                select(PredictionSnapshot)
                .where(
                    PredictionSnapshot.match_id == match.id,
                    PredictionSnapshot.revision_id == revision.id,
                    PredictionSnapshot.model_version == shadow_pred.model_version,
                )
                .order_by(PredictionSnapshot.snapshotted_at.desc())
                .limit(1)
            )

            if existing_shadow_snap and not existing_shadow_snap.is_pre_match_locked and not existing_shadow_snap.is_fallback_locked:
                # Update existing unlocked shadow snapshot
                existing_shadow_snap.home_win = shadow_pred.home_win
                existing_shadow_snap.draw = shadow_pred.draw
                existing_shadow_snap.away_win = shadow_pred.away_win
                existing_shadow_snap.home_xg = shadow_pred.home_xg
                existing_shadow_snap.away_xg = shadow_pred.away_xg
                existing_shadow_snap.has_auto_adjustments = shadow_pred.has_auto_adjustments
                existing_shadow_snap.base_home_win = shadow_pred.base_home_win
                existing_shadow_snap.base_draw = shadow_pred.base_draw
                existing_shadow_snap.base_away_win = shadow_pred.base_away_win
                existing_shadow_snap.scorelines = shadow_pred.scorelines
                existing_shadow_snap.score_matrix = shadow_pred.score_matrix
                existing_shadow_snap.confidence = shadow_pred.confidence
                existing_shadow_snap.confidence_label = shadow_pred.confidence_label
                existing_shadow_snap.model_inputs = shadow_pred.model_inputs
                existing_shadow_snap.snapshotted_at = now
                existing_shadow_snap.is_pre_match_locked = shadow_is_locked
                existing_shadow_snap.is_fallback_locked = shadow_is_fallback
                existing_shadow_snap.kickoff = kickoff
                session.add(existing_shadow_snap)
            elif not existing_shadow_snap:
                shadow_snap = PredictionSnapshot(
                    match_id=match.id,
                    revision_id=revision.id,
                    kickoff=kickoff,
                    is_pre_match_locked=shadow_is_locked,
                    is_fallback_locked=shadow_is_fallback,
                    home_win=shadow_pred.home_win,
                    draw=shadow_pred.draw,
                    away_win=shadow_pred.away_win,
                    home_xg=shadow_pred.home_xg,
                    away_xg=shadow_pred.away_xg,
                    has_auto_adjustments=shadow_pred.has_auto_adjustments,
                    base_home_win=shadow_pred.base_home_win,
                    base_draw=shadow_pred.base_draw,
                    base_away_win=shadow_pred.base_away_win,
                    scorelines=shadow_pred.scorelines,
                    score_matrix=shadow_pred.score_matrix,
                    confidence=shadow_pred.confidence,
                    confidence_label=shadow_pred.confidence_label,
                    model_inputs=shadow_pred.model_inputs,
                    model_version=shadow_pred.model_version,
                    snapshotted_at=now,
                )
                session.add(shadow_snap)
