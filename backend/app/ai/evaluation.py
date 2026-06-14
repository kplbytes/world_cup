"""AI and Ensemble evaluation - score AI predictions against actual results."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIPrediction, EnsemblePrediction, MarketSnapshot, Match, PredictionSnapshot
from app.services.scoring import _select_scorable_snapshot

_CLIP = 1e-6


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _select_system_prediction(session: Session, match: Match) -> PredictionSnapshot | None:
    snapshots = list(session.scalars(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.match_id == match.id)
        .order_by(PredictionSnapshot.snapshotted_at.desc())
    ))
    return _select_scorable_snapshot(snapshots, match)


def _select_ai_prediction(
    session: Session,
    match: Match,
    model_version: str,
) -> AIPrediction | None:
    predictions = list(session.scalars(
        select(AIPrediction)
        .where(
            AIPrediction.match_id == match.id,
            AIPrediction.model_version == model_version,
            AIPrediction.error_code.is_(None),
            AIPrediction.parsed_home_win.is_not(None),
            AIPrediction.parsed_draw.is_not(None),
            AIPrediction.parsed_away_win.is_not(None),
            AIPrediction.real_time_only.is_(False),
        )
        .order_by(AIPrediction.created_at.desc())
    ))
    locked = [prediction for prediction in predictions if prediction.is_pre_match_locked]
    if locked:
        return locked[0]
    fallback = [prediction for prediction in predictions if prediction.is_fallback_locked]
    if fallback:
        return fallback[0]
    return next(
        (
            prediction
            for prediction in predictions
            if _ensure_utc(prediction.created_at) < _ensure_utc(match.kickoff)
        ),
        None,
    )


def _select_ensemble_prediction(session: Session, match: Match) -> EnsemblePrediction | None:
    predictions = list(session.scalars(
        select(EnsemblePrediction)
        .where(EnsemblePrediction.match_id == match.id)
        .order_by(EnsemblePrediction.created_at.desc())
    ))
    locked = [prediction for prediction in predictions if prediction.is_pre_match_locked]
    if locked:
        return locked[0]
    return next(
        (
            prediction
            for prediction in predictions
            if _ensure_utc(prediction.created_at) < _ensure_utc(match.kickoff)
        ),
        None,
    )


def evaluate_ai_predictions(session: Session) -> dict[str, Any]:
    """Evaluate all AI and ensemble predictions against actual match results."""

    # Get all final matches
    final_matches = list(session.scalars(
        select(Match).where(Match.status == "final")
    ))

    market_snaps = {
        row.match_id: row
        for row in session.scalars(select(MarketSnapshot).where(MarketSnapshot.provider == "sporttery"))
    }

    # Evaluate system predictions
    system_results = _evaluate_source(session, final_matches, "system")

    # Evaluate AI predictions by version
    ai_versions = set()
    for match in final_matches:
        ai_preds = list(session.scalars(
            select(AIPrediction)
            .where(AIPrediction.match_id == match.id)
            .where(AIPrediction.error_code.is_(None))
            .where(AIPrediction.parsed_home_win.isnot(None))
        ))
        for pred in ai_preds:
            ai_versions.add(pred.model_version)

    ai_results = {}
    for version in ai_versions:
        ai_results[version] = _evaluate_ai_version(session, final_matches, version)

    # Evaluate ensemble predictions
    ensemble_results = _evaluate_ensemble(session, final_matches)

    # Determine overall AI helped/hurt
    ai_effect = _compute_ai_effect(system_results, ai_results, ensemble_results)

    return {
        "system": system_results,
        "ai_by_version": ai_results,
        "ensemble": ensemble_results,
        "ai_effect": ai_effect,
    }


def _evaluate_source(
    session: Session,
    matches: list[Match],
    source_type: str,
) -> dict[str, Any]:
    """Evaluate system model predictions."""
    briers = []
    loglosses = []
    hits = 0

    for match in matches:
        snap = _select_system_prediction(session, match)
        if not snap:
            continue

        actual_result = _get_actual_result(match)
        p = {"home_win": snap.home_win, "draw": snap.draw, "away_win": snap.away_win}

        brier = _compute_brier(p, actual_result)
        logloss = _compute_logloss(p, actual_result)

        briers.append(brier)
        loglosses.append(logloss)
        if _get_predicted_direction(p) == actual_result:
            hits += 1

    n = len(briers)
    if n == 0:
        return {"sample_count": 0, "brier": None, "logloss": None, "hit_rate": None}

    return {
        "sample_count": n,
        "brier": sum(briers) / n,
        "logloss": sum(loglosses) / n,
        "hit_rate": hits / n,
    }


def _evaluate_ai_version(
    session: Session,
    matches: list[Match],
    model_version: str,
) -> dict[str, Any]:
    """Evaluate a specific AI model version."""
    briers = []
    loglosses = []
    hits = 0
    helped_count = 0
    hurt_count = 0

    for match in matches:
        actual_result = _get_actual_result(match)

        ai_pred = _select_ai_prediction(session, match, model_version)
        if not ai_pred:
            continue

        p = {"home_win": ai_pred.parsed_home_win, "draw": ai_pred.parsed_draw, "away_win": ai_pred.parsed_away_win}
        brier = _compute_brier(p, actual_result)
        logloss = _compute_logloss(p, actual_result)

        briers.append(brier)
        loglosses.append(logloss)
        if _get_predicted_direction(p) == actual_result:
            hits += 1

        # Compare with system
        sys_snap = _select_system_prediction(session, match)
        if sys_snap:
            sys_p = {"home_win": sys_snap.home_win, "draw": sys_snap.draw, "away_win": sys_snap.away_win}
            sys_brier = _compute_brier(sys_p, actual_result)
            if brier < sys_brier - 0.01:
                helped_count += 1
            elif brier > sys_brier + 0.01:
                hurt_count += 1

    n = len(briers)
    if n == 0:
        return {"sample_count": 0, "brier": None, "logloss": None, "hit_rate": None, "helped": 0, "hurt": 0}

    return {
        "sample_count": n,
        "brier": sum(briers) / n,
        "logloss": sum(loglosses) / n,
        "hit_rate": hits / n,
        "helped": helped_count,
        "hurt": hurt_count,
    }


def _evaluate_ensemble(
    session: Session,
    matches: list[Match],
) -> dict[str, Any]:
    """Evaluate ensemble predictions."""
    briers = []
    loglosses = []
    hits = 0
    helped_count = 0
    hurt_count = 0

    for match in matches:
        actual_result = _get_actual_result(match)

        ens_pred = _select_ensemble_prediction(session, match)
        if not ens_pred:
            continue

        p = {"home_win": ens_pred.ensemble_home_win, "draw": ens_pred.ensemble_draw, "away_win": ens_pred.ensemble_away_win}
        brier = _compute_brier(p, actual_result)
        logloss = _compute_logloss(p, actual_result)

        briers.append(brier)
        loglosses.append(logloss)
        if _get_predicted_direction(p) == actual_result:
            hits += 1

        # Compare with system
        sys_snap = _select_system_prediction(session, match)
        if sys_snap:
            sys_p = {"home_win": sys_snap.home_win, "draw": sys_snap.draw, "away_win": sys_snap.away_win}
            sys_brier = _compute_brier(sys_p, actual_result)
            if brier < sys_brier - 0.01:
                helped_count += 1
            elif brier > sys_brier + 0.01:
                hurt_count += 1

    n = len(briers)
    if n == 0:
        return {"sample_count": 0, "brier": None, "logloss": None, "hit_rate": None, "helped": 0, "hurt": 0}

    return {
        "sample_count": n,
        "brier": sum(briers) / n,
        "logloss": sum(loglosses) / n,
        "hit_rate": hits / n,
        "helped": helped_count,
        "hurt": hurt_count,
    }


def _compute_ai_effect(
    system_results: dict[str, Any],
    ai_results: dict[str, dict[str, Any]],
    ensemble_results: dict[str, Any],
) -> dict[str, Any]:
    """Determine whether AI/ensemble helped or hurt vs system alone."""
    sys_brier = system_results.get("brier")

    effects = {}
    for version, results in ai_results.items():
        ai_brier = results.get("brier")
        if sys_brier is not None and ai_brier is not None:
            diff = sys_brier - ai_brier  # positive = AI better
            if diff > 0.01:
                effects[version] = {"effect": "helped", "brier_diff": diff}
            elif diff < -0.01:
                effects[version] = {"effect": "hurt", "brier_diff": diff}
            else:
                effects[version] = {"effect": "neutral", "brier_diff": diff}

    ens_brier = ensemble_results.get("brier")
    if sys_brier is not None and ens_brier is not None:
        diff = sys_brier - ens_brier
        if diff > 0.01:
            effects["ensemble"] = {"effect": "helped", "brier_diff": diff}
        elif diff < -0.01:
            effects["ensemble"] = {"effect": "hurt", "brier_diff": diff}
        else:
            effects["ensemble"] = {"effect": "neutral", "brier_diff": diff}

    return effects


def _get_actual_result(match: Match) -> str:
    """Get the actual match result as 'home', 'draw', or 'away'."""
    h = match.home_score or 0
    a = match.away_score or 0
    if h > a:
        return "home"
    elif h == a:
        return "draw"
    return "away"


def _compute_brier(probs: dict[str, float], actual: str) -> float:
    """Compute Brier score."""
    o = {"home": 0, "draw": 0, "away": 0}
    o[actual] = 1.0
    return sum((probs.get(k, 0) - o.get(k, 0)) ** 2 for k in ("home_win", "draw", "away_win"))


def _compute_logloss(probs: dict[str, float], actual: str) -> float:
    """Compute log loss."""
    o = {"home": 0, "draw": 0, "away": 0}
    o[actual] = 1.0
    mapping = {"home_win": "home", "draw": "draw", "away_win": "away"}
    ll = 0.0
    for pk, ok in mapping.items():
        p = max(_CLIP, min(1 - _CLIP, probs.get(pk, 1/3)))
        if o.get(ok, 0) == 1:
            ll -= math.log(p)
    return ll


def _get_predicted_direction(probs: dict[str, float]) -> str:
    """Get the predicted direction."""
    keys = {"home_win": "home", "draw": "draw", "away_win": "away"}
    best = max(probs, key=probs.get)
    return keys.get(best, "home")
