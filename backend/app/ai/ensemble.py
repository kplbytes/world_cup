"""Ensemble prediction system - combine system, market, and AI predictions."""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.lock_status import compute_match_lock_status
from app.ai.model_registry import get_ensemble_defaults, list_enabled_models
from app.models import AIPrediction, EnsembleLockTracker, EnsemblePrediction, MarketSnapshot, Match, PredictionSnapshot

logger = logging.getLogger(__name__)


def _sync_tracker(session: Session, match_id: str, model_version: str, lock_type: str, ensemble_id: int) -> None:
    """Insert into ensemble_lock_tracker, handling IntegrityError.

    Uses a check-then-insert pattern instead of try-insert-catch-rollback
    to avoid rolling back ALL pending changes in the session (which would
    cause data loss for other flushed objects).
    """
    from sqlalchemy.exc import IntegrityError

    # Check if tracker already exists
    existing = session.scalar(
        select(EnsembleLockTracker).where(
            EnsembleLockTracker.match_id == match_id,
            EnsembleLockTracker.model_version == model_version,
            EnsembleLockTracker.lock_type == lock_type,
        )
    )
    if existing:
        if existing.ensemble_id != ensemble_id:
            logger.warning(
                "Tracker inconsistency: match=%s ver=%s type=%s tracker_ens=%d actual_ens=%d",
                match_id, model_version, lock_type, existing.ensemble_id, ensemble_id,
            )
        return

    try:
        tracker = EnsembleLockTracker(
            match_id=match_id,
            model_version=model_version,
            lock_type=lock_type,
            ensemble_id=ensemble_id,
        )
        session.add(tracker)
        session.flush()
    except IntegrityError:
        # Another session inserted first - just log and continue
        logger.info("Tracker row already exists for %s/%s/%s", match_id, model_version, lock_type)


def _verify_tracker(session: Session, match_id: str, model_version: str) -> None:
    """Verify tracker rows match actual locked ensembles. Rebuild if missing."""
    locked = list(session.scalars(
        select(EnsemblePrediction).where(
            EnsemblePrediction.match_id == match_id,
            EnsemblePrediction.model_version == model_version,
        )
    ))

    official = [e for e in locked if e.is_pre_match_locked]
    fallback = [e for e in locked if e.is_fallback_locked]

    # Check/rebuild official tracker
    if official:
        ens = official[0]
        tracker = session.scalar(
            select(EnsembleLockTracker).where(
                EnsembleLockTracker.match_id == match_id,
                EnsembleLockTracker.model_version == model_version,
                EnsembleLockTracker.lock_type == "official",
            )
        )
        if not tracker:
            _sync_tracker(session, match_id, model_version, "official", ens.id)
        elif tracker.ensemble_id != ens.id:
            logger.warning(
                "Tracker points to non-existent ensemble %d for %s/%s/official",
                tracker.ensemble_id, match_id, model_version,
            )

    # Check/rebuild fallback tracker
    if fallback:
        ens = fallback[0]
        tracker = session.scalar(
            select(EnsembleLockTracker).where(
                EnsembleLockTracker.match_id == match_id,
                EnsembleLockTracker.model_version == model_version,
                EnsembleLockTracker.lock_type == "fallback",
            )
        )
        if not tracker:
            _sync_tracker(session, match_id, model_version, "fallback", ens.id)


def compute_ensemble(
    session: Session,
    match_id: str,
    system_weight: float | None = None,
    market_weight: float | None = None,
    ai_weights_override: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute ensemble prediction for a match.

    The ensemble combines:
    1. System model (Elo + Poisson)
    2. Market odds (if available)
    3. AI predictions (if available and enabled)

    All weights are normalized. Missing sources cause automatic weight redistribution.
    """
    defaults = get_ensemble_defaults()

    # 1. Get system prediction
    sys_snap = session.scalar(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.match_id == match_id)
        .order_by(PredictionSnapshot.snapshotted_at.desc())
        .limit(1)
    )

    sys_probs = None
    sys_version = ""
    if sys_snap:
        sys_probs = {"home_win": sys_snap.home_win, "draw": sys_snap.draw, "away_win": sys_snap.away_win}
        sys_version = sys_snap.model_version

    # 2. Get market odds
    market_snap = session.scalar(
        select(MarketSnapshot)
        .where(MarketSnapshot.match_id == match_id)
        .where(MarketSnapshot.provider == "sporttery")
        .order_by(MarketSnapshot.fetched_at.desc())
        .limit(1)
    )

    market_probs = None
    if market_snap:
        market_probs = {
            "home_win": market_snap.home_probability,
            "draw": market_snap.draw_probability,
            "away_win": market_snap.away_probability,
        }

    # 3. Get AI predictions (only pre-match locked or fallback locked, exclude real_time_only)
    ai_preds = list(session.scalars(
        select(AIPrediction)
        .where(AIPrediction.match_id == match_id)
        .where(AIPrediction.error_code.is_(None))
        .where(AIPrediction.parsed_home_win.isnot(None))
        .where(AIPrediction.real_time_only.is_(False))
        .order_by(AIPrediction.created_at.desc())
    ))

    # Deduplicate by model_version (keep latest)
    ai_by_version: dict[str, AIPrediction] = {}
    for pred in ai_preds:
        if pred.model_version not in ai_by_version:
            ai_by_version[pred.model_version] = pred

    # Filter out shadow models (include_in_ensemble=False)
    ai_by_version = {
        version: pred
        for version, pred in ai_by_version.items()
        if _should_include_in_ensemble(version)
    }

    ai_probs_list: list[tuple[str, dict[str, float], float]] = []
    for version, pred in ai_by_version.items():
        ai_probs_list.append((
            version,
            {"home_win": pred.parsed_home_win, "draw": pred.parsed_draw, "away_win": pred.parsed_away_win},
            _get_ai_weight(version, ai_weights_override),
        ))

    # 4. Compute weights
    has_system = sys_probs is not None
    has_market = market_probs is not None
    has_ai = len(ai_probs_list) > 0

    if not has_system:
        return {
            "status": "error",
            "error": "No system prediction available for ensemble",
            "match_id": match_id,
        }

    weights = _compute_weights(has_market, has_ai, len(ai_probs_list), defaults, system_weight, market_weight, ai_weights_override)

    # 5. Compute ensemble probabilities
    ensemble_probs = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}

    # System contribution
    if has_system:
        for key in ensemble_probs:
            ensemble_probs[key] += sys_probs[key] * weights["system"]

    # Market contribution
    if has_market and "market" in weights:
        for key in ensemble_probs:
            ensemble_probs[key] += market_probs[key] * weights["market"]

    # AI contributions
    for version, probs, _raw_weight in ai_probs_list:
        ai_w = weights.get(f"ai_{version}", 0.0)
        for key in ensemble_probs:
            ensemble_probs[key] += probs[key] * ai_w

    # Normalize
    total = sum(ensemble_probs.values())
    if total > 0:
        for key in ensemble_probs:
            ensemble_probs[key] /= total

    # 6. Build source probabilities record
    source_probs = {}
    if has_system:
        source_probs["system"] = {"version": sys_version, "probs": sys_probs, "weight": weights.get("system", 0)}
    if has_market:
        source_probs["market"] = {"probs": market_probs, "weight": weights.get("market", 0)}
    for version, probs, _ in ai_probs_list:
        source_probs[f"ai_{version}"] = {"probs": probs, "weight": weights.get(f"ai_{version}", 0)}

    # 7. Compute confidence (considering source agreement)
    confidence, confidence_label, disagreement_score = _compute_ensemble_confidence(
        ensemble_probs, source_probs
    )

    # 8. Build reason
    reasons = []
    if has_system:
        reasons.append(f"System({sys_version})")
    if has_market:
        reasons.append("Market(Sporttery)")
    for version, _, _ in ai_probs_list:
        reasons.append(f"AI({version})")
    reason = " + ".join(reasons) if reasons else "System only"

    # 9. Check if match is within 24h lock window
    match = session.get(Match, match_id)
    if not match:
        return {
            "status": "error",
            "error": f"Match {match_id} not found",
            "match_id": match_id,
        }

    # Check if a locked ensemble already exists for this match + model_version
    existing_locked = session.scalar(
        select(EnsemblePrediction)
        .where(
            EnsemblePrediction.match_id == match_id,
            EnsemblePrediction.model_version == "ensemble-v1",
            (EnsemblePrediction.is_pre_match_locked.is_(True))
            | (EnsemblePrediction.is_fallback_locked.is_(True)),
        )
        .limit(1)
    )
    if existing_locked:
        # Locked ensemble is immutable - return existing
        # Verify tracker consistency before returning
        _verify_tracker(session, match_id, "ensemble-v1")
        selection_type = "official_locked" if existing_locked.is_pre_match_locked else "fallback_pre_match"
        return {
            "status": "skipped_locked",
            "ensemble_id": existing_locked.id,
            "match_id": match_id,
            "home_win": existing_locked.ensemble_home_win,
            "draw": existing_locked.ensemble_draw,
            "away_win": existing_locked.ensemble_away_win,
            "confidence": existing_locked.confidence,
            "reason": existing_locked.reason,
            "is_locked": True,
            "real_time_only": False,
            "selection_type": selection_type,
        }

    is_locked = False
    locked_at = None
    real_time_only = False
    now = datetime.now(timezone.utc)

    lock = compute_match_lock_status(match, now)
    is_locked = lock.is_pre_match_locked
    locked_at = lock.locked_at
    real_time_only = lock.real_time_only

    # After kickoff, do NOT save as a pre-match locked record
    if real_time_only:
        is_locked = False
        locked_at = None

        # Fallback: mark the latest pre-match ensemble as is_fallback_locked=True
        # This ensures the pre-match ensemble is properly tagged for scoring
        kickoff = match.kickoff
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        latest_pre_match = session.scalar(
            select(EnsemblePrediction)
            .where(
                EnsemblePrediction.match_id == match_id,
                EnsemblePrediction.created_at < kickoff,
                EnsemblePrediction.is_pre_match_locked.is_(False),
                EnsemblePrediction.is_fallback_locked.is_(False),
                EnsemblePrediction.real_time_only.is_(False),
            )
            .order_by(EnsemblePrediction.created_at.desc())
            .limit(1)
        )
        if latest_pre_match:
            # Guard: official-to-fallback transition must be atomic
            # An officially locked ensemble should never be demoted to fallback
            if latest_pre_match.is_pre_match_locked:
                logger.warning(
                    "Attempted official-to-fallback transition for match=%s ens=%d - skipped",
                    match_id, latest_pre_match.id,
                )
            else:
                latest_pre_match.is_fallback_locked = True
                session.add(latest_pre_match)
                session.flush()
                _sync_tracker(session, match_id, "ensemble-v1", "fallback", latest_pre_match.id)

    # Idempotency: for non-locked, non-realtime predictions,
    # check if the latest unlocked ensemble has identical probabilities
    # If inputs haven't changed, skip creating a duplicate
    if not is_locked and not real_time_only:
        latest_ens = session.scalar(
            select(EnsemblePrediction)
            .where(
                EnsemblePrediction.match_id == match_id,
                EnsemblePrediction.model_version == "ensemble-v1",
                EnsemblePrediction.is_pre_match_locked.is_(False),
                EnsemblePrediction.is_fallback_locked.is_(False),
                EnsemblePrediction.real_time_only.is_(False),
            )
            .order_by(EnsemblePrediction.created_at.desc())
            .limit(1)
        )
        if latest_ens:
            hw_diff = abs(latest_ens.ensemble_home_win - ensemble_probs["home_win"])
            d_diff = abs(latest_ens.ensemble_draw - ensemble_probs["draw"])
            aw_diff = abs(latest_ens.ensemble_away_win - ensemble_probs["away_win"])
            if hw_diff < 0.001 and d_diff < 0.001 and aw_diff < 0.001:
                selection_type = "unlocked_pre_match"
                return {
                    "status": "skipped_unchanged",
                    "ensemble_id": latest_ens.id,
                    "match_id": match_id,
                    "home_win": latest_ens.ensemble_home_win,
                    "draw": latest_ens.ensemble_draw,
                    "away_win": latest_ens.ensemble_away_win,
                    "confidence": latest_ens.confidence,
                    "reason": latest_ens.reason,
                    "is_locked": False,
                    "real_time_only": False,
                    "selection_type": selection_type,
                }

    # 10. Build source IDs for traceability
    source_ids = {}
    if sys_snap:
        source_ids["baseline_snapshot_id"] = sys_snap.id
        source_ids["baseline_model_version"] = sys_version
    if market_snap:
        source_ids["market_snapshot_id"] = market_snap.id
        source_ids["market_provider"] = market_snap.provider
    ai_ids = {version: pred.id for version, pred in ai_by_version.items()}
    if ai_ids:
        source_ids["ai_prediction_ids"] = ai_ids

    # 11. Save ensemble prediction
    ensemble = EnsemblePrediction(
        match_id=match_id,
        model_version="ensemble-v1",
        system_model_version=sys_version,
        system_weight=weights.get("system", 0),
        market_weight=weights.get("market", 0),
        ai_weights_json={k: v for k, v in weights.items() if k.startswith("ai_")},
        source_probabilities_json=source_probs,
        source_ids_json=source_ids,
        ensemble_home_win=ensemble_probs["home_win"],
        ensemble_draw=ensemble_probs["draw"],
        ensemble_away_win=ensemble_probs["away_win"],
        confidence=confidence,
        reason=reason,
        created_at=now,
        locked_at=locked_at,
        is_pre_match_locked=is_locked,
        is_fallback_locked=False,
        real_time_only=real_time_only,
        source_status_json={
            "system": has_system,
            "market": has_market,
            "ai_versions": [v for v, _, _ in ai_probs_list],
            "disagreement_score": round(disagreement_score, 4),
            "confidence_label": confidence_label,
        },
    )

    session.add(ensemble)
    session.flush()

    # Sync tracker for locked ensembles
    if is_locked:
        _sync_tracker(session, match_id, "ensemble-v1", "official", ensemble.id)

    selection_type = "official_locked" if is_locked else ("real_time_only" if real_time_only else "unlocked_pre_match")

    return {
        "status": "success",
        "ensemble_id": ensemble.id,
        "match_id": match_id,
        "home_win": ensemble_probs["home_win"],
        "draw": ensemble_probs["draw"],
        "away_win": ensemble_probs["away_win"],
        "confidence": confidence,
        "disagreement_score": round(disagreement_score, 4),
        "confidence_label": confidence_label,
        "reason": reason,
        "weights": weights,
        "source_probabilities": source_probs,
        "source_ids": source_ids,
        "is_locked": is_locked,
        "real_time_only": real_time_only,
        "selection_type": selection_type,
    }


def get_ensemble_predictions(session: Session, match_id: str) -> list[dict[str, Any]]:
    """Get ensemble prediction history for a match."""
    rows = session.scalars(
        select(EnsemblePrediction)
        .where(EnsemblePrediction.match_id == match_id)
        .order_by(EnsemblePrediction.created_at.desc())
    ).all()

    return [_serialize_ensemble(row) for row in rows]


def _compute_ensemble_confidence(
    ensemble_probs: dict[str, float],
    source_probs: dict[str, dict],
) -> tuple[float, str, float]:
    """Compute ensemble confidence considering source agreement.

    Returns (confidence, label, disagreement_score).
    """
    # Base confidence from max probability
    max_prob = max(ensemble_probs.values())

    # Direction agreement: what does each source predict?
    directions = []
    for source_data in source_probs.values():
        probs = source_data.get("probs", {})
        if probs:
            direction = max(probs, key=probs.get)
            directions.append(direction)

    # Direction agreement factor
    if directions:
        most_common = max(set(directions), key=directions.count)
        direction_agreement = directions.count(most_common) / len(directions)
    else:
        direction_agreement = 1.0

    # Magnitude agreement: variance of home_win probs across sources
    home_probs = []
    for source_data in source_probs.values():
        probs = source_data.get("probs", {})
        if "home_win" in probs:
            home_probs.append(probs["home_win"])

    if len(home_probs) >= 2:
        variance = statistics.variance(home_probs)
        # Normalize: variance of 0 = perfect agreement, variance > 0.05 = high disagreement
        magnitude_agreement = max(0.0, 1.0 - variance / 0.05)
    else:
        magnitude_agreement = 1.0

    # Disagreement score
    disagreement_score = 1.0 - (direction_agreement * 0.6 + magnitude_agreement * 0.4)

    # Adjusted confidence
    agreement_factor = direction_agreement * 0.6 + magnitude_agreement * 0.4
    confidence = max_prob * (0.5 + 0.5 * agreement_factor)

    # Label
    if disagreement_score < 0.2:
        label = "高"
    elif disagreement_score < 0.5:
        label = "中"
    else:
        label = "低"

    return confidence, label, disagreement_score


def _compute_weights(
    has_market: bool,
    has_ai: bool,
    num_ai: int,
    defaults: dict[str, float],
    system_weight_override: float | None = None,
    market_weight_override: float | None = None,
    ai_weights_override: dict[str, float] | None = None,
) -> dict[str, float]:
    """Compute normalized weights for all sources."""
    if has_market and has_ai:
        sys_w = system_weight_override or defaults.get("system_weight", 0.50)
        mkt_w = market_weight_override or defaults.get("market_weight", 0.20)
        total_ai_w = defaults.get("total_ai_weight", 0.30)
    elif has_market and not has_ai:
        sys_w = defaults.get("system_weight_no_ai", 0.80)
        mkt_w = defaults.get("market_weight_no_ai", 0.20)
        total_ai_w = 0.0
    elif not has_market and has_ai:
        sys_w = defaults.get("system_weight_no_market", 0.60)
        mkt_w = 0.0
        total_ai_w = defaults.get("total_ai_weight_no_market", 0.40)
    else:
        sys_w = defaults.get("system_weight_only", 1.00)
        mkt_w = 0.0
        total_ai_w = 0.0

    weights = {"system": sys_w}
    if mkt_w > 0:
        weights["market"] = mkt_w

    # Distribute AI weight across models
    if has_ai and total_ai_w > 0:
        enabled_models = list_enabled_models()
        if ai_weights_override:
            # Use explicit overrides
            total_explicit = sum(ai_weights_override.values())
            if total_explicit > 0:
                for version, w in ai_weights_override.items():
                    weights[f"ai_{version}"] = total_ai_w * (w / total_explicit)
            else:
                # Equal distribution
                for version in ai_weights_override:
                    weights[f"ai_{version}"] = total_ai_w / len(ai_weights_override)
        else:
            # Use ensemble_weight from model configs
            total_config_weight = sum(m.ensemble_weight for m in enabled_models)
            if total_config_weight > 0:
                for model in enabled_models:
                    w = total_ai_w * (model.ensemble_weight / total_config_weight)
                    weights[f"ai_{model.model_version}"] = w
            else:
                # Equal distribution
                for model in enabled_models:
                    weights[f"ai_{model.model_version}"] = total_ai_w / len(enabled_models)

    # Normalize all weights
    total = sum(weights.values())
    if total > 0:
        for key in weights:
            weights[key] /= total

    return weights


def _should_include_in_ensemble(version: str) -> bool:
    """Check if an AI model version should be included in ensemble calculations."""
    from app.ai.model_registry import get_model_config
    config = get_model_config(version)
    if config is None:
        return True  # Unknown models included by default (backward compat)
    return config.include_in_ensemble


def _get_ai_weight(version: str, overrides: dict[str, float] | None) -> float:
    """Get the relative weight for an AI model version."""
    if overrides and version in overrides:
        return overrides[version]
    from app.ai.model_registry import get_model_config
    config = get_model_config(version)
    if config:
        return config.ensemble_weight
    return 1.0


def _serialize_ensemble(row: EnsemblePrediction) -> dict[str, Any]:
    """Serialize an EnsemblePrediction row for API output."""
    source_status = row.source_status_json or {}

    # Determine selection_type from lock status
    if row.is_pre_match_locked:
        selection_type = "official_locked"
    elif row.is_fallback_locked:
        selection_type = "fallback_pre_match"
    elif row.real_time_only:
        selection_type = "real_time_only"
    else:
        selection_type = "unlocked_pre_match"

    # Determine traceability status
    source_ids = row.source_ids_json or {}
    has_source_ids = bool(source_ids.get("baseline_snapshot_id") or source_ids.get("ai_prediction_ids"))
    traceability_status = "complete" if has_source_ids else "legacy_unavailable"

    return {
        "id": row.id,
        "match_id": row.match_id,
        "model_version": row.model_version,
        "system_model_version": row.system_model_version,
        "system_weight": row.system_weight,
        "market_weight": row.market_weight,
        "ai_weights": row.ai_weights_json or {},
        "source_probabilities": row.source_probabilities_json or {},
        "source_ids": source_ids,
        "home_win": row.ensemble_home_win,
        "draw": row.ensemble_draw,
        "away_win": row.ensemble_away_win,
        "confidence": row.confidence,
        "disagreement_score": source_status.get("disagreement_score"),
        "confidence_label": source_status.get("confidence_label"),
        "reason": row.reason,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "locked_at": row.locked_at.isoformat() if row.locked_at else None,
        "is_pre_match_locked": row.is_pre_match_locked,
        "is_fallback_locked": row.is_fallback_locked,
        "real_time_only": row.real_time_only,
        "selection_type": selection_type,
        "traceability_status": traceability_status,
        "source_status": source_status,
    }
