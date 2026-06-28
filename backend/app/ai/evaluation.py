"""AI and Ensemble evaluation - score AI predictions against actual results."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import AIPrediction, EnsemblePrediction, Match, PredictionSnapshot
from app.services.scoring import _select_scorable_snapshot

_CLIP = 1e-6
_OUTCOME_KEY = {
    "home": "home_win",
    "draw": "draw",
    "away": "away_win",
}


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _is_visible_ai_version(model_version: str | None) -> bool:
    version = (model_version or "").lower()
    return "xiaomi" not in version and "mimo" not in version


@dataclass(frozen=True)
class _SystemPredictionLite:
    match_id: str
    snapshotted_at: datetime
    home_win: float
    draw: float
    away_win: float


@dataclass(frozen=True)
class _AIPredictionLite:
    match_id: str
    model_version: str
    created_at: datetime
    is_pre_match_locked: bool
    is_fallback_locked: bool
    parsed_home_win: float
    parsed_draw: float
    parsed_away_win: float


@dataclass(frozen=True)
class _EnsemblePredictionLite:
    match_id: str
    created_at: datetime
    is_pre_match_locked: bool
    ensemble_home_win: float
    ensemble_draw: float
    ensemble_away_win: float


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
    if not _is_visible_ai_version(model_version):
        return None
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
    return _pick_ai_prediction(predictions, match.kickoff)


def _select_ensemble_prediction(session: Session, match: Match) -> EnsemblePrediction | None:
    predictions = list(session.scalars(
        select(EnsemblePrediction)
        .where(EnsemblePrediction.match_id == match.id)
        .order_by(EnsemblePrediction.created_at.desc())
    ))
    return _pick_ensemble_prediction(predictions, match.kickoff)


def _select_system_predictions(
    session: Session,
    matches: list[Match],
) -> dict[str, PredictionSnapshot]:
    if not matches:
        return {}
    match_ids = [match.id for match in matches]
    ranked = (
        select(
            PredictionSnapshot.match_id.label("match_id"),
            PredictionSnapshot.snapshotted_at.label("snapshotted_at"),
            PredictionSnapshot.home_win.label("home_win"),
            PredictionSnapshot.draw.label("draw"),
            PredictionSnapshot.away_win.label("away_win"),
            func.row_number().over(
                partition_by=PredictionSnapshot.match_id,
                order_by=PredictionSnapshot.snapshotted_at.desc(),
            ).label("rn"),
        )
        .join(Match, PredictionSnapshot.match_id == Match.id)
        .where(Match.status == "final")
        .where(PredictionSnapshot.match_id.in_(match_ids))
        .where(PredictionSnapshot.snapshotted_at < Match.kickoff)
        .subquery()
    )
    rows = session.execute(
        select(
            ranked.c.match_id,
            ranked.c.snapshotted_at,
            ranked.c.home_win,
            ranked.c.draw,
            ranked.c.away_win,
        )
        .where(ranked.c.rn == 1)
        .order_by(ranked.c.match_id)
    ).all()

    selected: dict[str, _SystemPredictionLite] = {}
    for row in rows:
        selected[row.match_id] = _SystemPredictionLite(
            match_id=row.match_id,
            snapshotted_at=row.snapshotted_at,
            home_win=row.home_win,
            draw=row.draw,
            away_win=row.away_win,
        )
    return selected


def _pick_ai_prediction(predictions: list[AIPrediction], kickoff: datetime) -> AIPrediction | None:
    locked = [prediction for prediction in predictions if prediction.is_pre_match_locked]
    if locked:
        return locked[0]
    fallback = [prediction for prediction in predictions if prediction.is_fallback_locked]
    if fallback:
        return fallback[0]
    kickoff_utc = _ensure_utc(kickoff)
    return next(
        (
            prediction
            for prediction in predictions
            if _ensure_utc(prediction.created_at) < kickoff_utc
        ),
        None,
    )


def _select_ai_predictions(
    session: Session,
    matches: list[Match],
) -> dict[str, dict[str, AIPrediction]]:
    if not matches:
        return {}
    match_ids = [match.id for match in matches]
    ranked = (
        select(
            AIPrediction.match_id.label("match_id"),
            AIPrediction.model_version.label("model_version"),
            AIPrediction.created_at.label("created_at"),
            AIPrediction.is_pre_match_locked.label("is_pre_match_locked"),
            AIPrediction.is_fallback_locked.label("is_fallback_locked"),
            AIPrediction.parsed_home_win.label("parsed_home_win"),
            AIPrediction.parsed_draw.label("parsed_draw"),
            AIPrediction.parsed_away_win.label("parsed_away_win"),
            func.row_number().over(
                partition_by=(AIPrediction.match_id, AIPrediction.model_version),
                order_by=(
                    case(
                        (AIPrediction.is_pre_match_locked.is_(True), 0),
                        (AIPrediction.is_fallback_locked.is_(True), 1),
                        else_=2,
                    ),
                    AIPrediction.created_at.desc(),
                ),
            ).label("rn"),
        )
        .join(Match, AIPrediction.match_id == Match.id)
        .where(Match.status == "final")
        .where(AIPrediction.match_id.in_(match_ids))
        .where(AIPrediction.error_code.is_(None))
        .where(AIPrediction.parsed_home_win.is_not(None))
        .where(AIPrediction.parsed_draw.is_not(None))
        .where(AIPrediction.parsed_away_win.is_not(None))
        .where(AIPrediction.real_time_only.is_(False))
        .where(
            or_(
                AIPrediction.is_pre_match_locked.is_(True),
                AIPrediction.is_fallback_locked.is_(True),
                AIPrediction.created_at < Match.kickoff,
            )
        )
        .subquery()
    )
    rows = session.execute(
        select(
            ranked.c.match_id,
            ranked.c.model_version,
            ranked.c.created_at,
            ranked.c.is_pre_match_locked,
            ranked.c.is_fallback_locked,
            ranked.c.parsed_home_win,
            ranked.c.parsed_draw,
            ranked.c.parsed_away_win,
        )
        .where(ranked.c.rn == 1)
        .order_by(ranked.c.match_id, ranked.c.model_version)
    ).all()

    selected: dict[str, dict[str, _AIPredictionLite]] = defaultdict(dict)
    for row in rows:
        if not _is_visible_ai_version(row.model_version):
            continue
        selected[row.match_id][row.model_version] = _AIPredictionLite(
            match_id=row.match_id,
            model_version=row.model_version,
            created_at=row.created_at,
            is_pre_match_locked=bool(row.is_pre_match_locked),
            is_fallback_locked=bool(row.is_fallback_locked),
            parsed_home_win=row.parsed_home_win,
            parsed_draw=row.parsed_draw,
            parsed_away_win=row.parsed_away_win,
        )
    return dict(selected)


def _pick_ensemble_prediction(
    predictions: list[EnsemblePrediction],
    kickoff: datetime,
) -> EnsemblePrediction | None:
    locked = [prediction for prediction in predictions if prediction.is_pre_match_locked]
    if locked:
        return locked[0]
    kickoff_utc = _ensure_utc(kickoff)
    return next(
        (
            prediction
            for prediction in predictions
            if _ensure_utc(prediction.created_at) < kickoff_utc
        ),
        None,
    )


def _select_ensemble_predictions(
    session: Session,
    matches: list[Match],
) -> dict[str, EnsemblePrediction]:
    if not matches:
        return {}
    match_ids = [match.id for match in matches]
    ranked = (
        select(
            EnsemblePrediction.match_id.label("match_id"),
            EnsemblePrediction.created_at.label("created_at"),
            EnsemblePrediction.is_pre_match_locked.label("is_pre_match_locked"),
            EnsemblePrediction.ensemble_home_win.label("ensemble_home_win"),
            EnsemblePrediction.ensemble_draw.label("ensemble_draw"),
            EnsemblePrediction.ensemble_away_win.label("ensemble_away_win"),
            func.row_number().over(
                partition_by=EnsemblePrediction.match_id,
                order_by=(
                    case(
                        (EnsemblePrediction.is_pre_match_locked.is_(True), 0),
                        else_=1,
                    ),
                    EnsemblePrediction.created_at.desc(),
                ),
            ).label("rn"),
        )
        .join(Match, EnsemblePrediction.match_id == Match.id)
        .where(Match.status == "final")
        .where(EnsemblePrediction.match_id.in_(match_ids))
        .where(
            or_(
                EnsemblePrediction.is_pre_match_locked.is_(True),
                EnsemblePrediction.created_at < Match.kickoff,
            )
        )
        .subquery()
    )
    rows = session.execute(
        select(
            ranked.c.match_id,
            ranked.c.created_at,
            ranked.c.is_pre_match_locked,
            ranked.c.ensemble_home_win,
            ranked.c.ensemble_draw,
            ranked.c.ensemble_away_win,
        )
        .where(ranked.c.rn == 1)
        .order_by(ranked.c.match_id)
    ).all()

    selected: dict[str, _EnsemblePredictionLite] = {}
    for row in rows:
        selected[row.match_id] = _EnsemblePredictionLite(
            match_id=row.match_id,
            created_at=row.created_at,
            is_pre_match_locked=bool(row.is_pre_match_locked),
            ensemble_home_win=row.ensemble_home_win,
            ensemble_draw=row.ensemble_draw,
            ensemble_away_win=row.ensemble_away_win,
        )
    return selected


def evaluate_ai_predictions(session: Session) -> dict[str, Any]:
    """Evaluate all AI and ensemble predictions against actual match results."""

    # Get all final matches
    final_matches = list(session.scalars(
        select(Match).where(Match.status == "final")
    ))
    system_predictions = _select_system_predictions(session, final_matches)
    ai_predictions = _select_ai_predictions(session, final_matches)
    ensemble_predictions = _select_ensemble_predictions(session, final_matches)

    # Evaluate system predictions
    system_results = _evaluate_source(
        session,
        final_matches,
        "system",
        system_predictions=system_predictions,
    )

    # Evaluate AI predictions by version
    ai_versions = sorted({
        version
        for predictions_by_version in ai_predictions.values()
        for version in predictions_by_version
    })

    ai_results = {}
    for version in ai_versions:
        ai_results[version] = _evaluate_ai_version(
            session,
            final_matches,
            version,
            system_predictions=system_predictions,
            ai_predictions=ai_predictions,
        )

    # Evaluate ensemble predictions
    ensemble_results = _evaluate_ensemble(
        session,
        final_matches,
        system_predictions=system_predictions,
        ensemble_predictions=ensemble_predictions,
    )

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
    system_predictions: dict[str, PredictionSnapshot] | None = None,
) -> dict[str, Any]:
    """Evaluate system model predictions."""
    briers = []
    loglosses = []
    hits = 0
    selected_system_predictions = (
        system_predictions
        if system_predictions is not None
        else _select_system_predictions(session, matches)
    )

    for match in matches:
        snap = selected_system_predictions.get(match.id)
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
    system_predictions: dict[str, PredictionSnapshot] | None = None,
    ai_predictions: dict[str, dict[str, AIPrediction]] | None = None,
) -> dict[str, Any]:
    """Evaluate a specific AI model version."""
    briers = []
    loglosses = []
    hits = 0
    helped_count = 0
    hurt_count = 0
    selected_system_predictions = (
        system_predictions
        if system_predictions is not None
        else _select_system_predictions(session, matches)
    )
    selected_ai_predictions = (
        ai_predictions
        if ai_predictions is not None
        else _select_ai_predictions(session, matches)
    )

    for match in matches:
        actual_result = _get_actual_result(match)

        ai_pred = selected_ai_predictions.get(match.id, {}).get(model_version)
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
        sys_snap = selected_system_predictions.get(match.id)
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
    system_predictions: dict[str, PredictionSnapshot] | None = None,
    ensemble_predictions: dict[str, EnsemblePrediction] | None = None,
) -> dict[str, Any]:
    """Evaluate ensemble predictions."""
    briers = []
    loglosses = []
    hits = 0
    helped_count = 0
    hurt_count = 0
    selected_system_predictions = (
        system_predictions
        if system_predictions is not None
        else _select_system_predictions(session, matches)
    )
    selected_ensemble_predictions = (
        ensemble_predictions
        if ensemble_predictions is not None
        else _select_ensemble_predictions(session, matches)
    )

    for match in matches:
        actual_result = _get_actual_result(match)

        ens_pred = selected_ensemble_predictions.get(match.id)
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
        sys_snap = selected_system_predictions.get(match.id)
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
    """Get the actual match result as 'home', 'draw', or 'away'.

    For knockout matches that ended level after extra time and were decided
    by penalties (or by the `home_advance`/`away_advance` flag), the actual
    outcome is the advancing team — a "draw" never survives in knockout
    play. Falling back to "draw" here would penalise models that correctly
    picked the eventual winner.
    """
    h = match.home_score or 0
    a = match.away_score or 0
    if h > a:
        return "home"
    if h < a:
        return "away"
    # Level scores — only valid as a "draw" for group stage.
    is_knockout = bool(getattr(match, "stage", None) and match.stage != "group")
    if is_knockout:
        if getattr(match, "home_advance", None) is True:
            return "home"
        if getattr(match, "away_advance", None) is True:
            return "away"
    return "draw"


def _compute_brier(probs: dict[str, float], actual: str) -> float:
    """Compute Brier score."""
    o = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
    o[_OUTCOME_KEY[actual]] = 1.0
    return sum((probs.get(k, 0) - o[k]) ** 2 for k in ("home_win", "draw", "away_win"))


def _compute_logloss(probs: dict[str, float], actual: str) -> float:
    """Compute log loss."""
    o = {"home": 0, "draw": 0, "away": 0}
    o[actual] = 1.0
    ll = 0.0
    for pk, ok in (("home_win", "home"), ("draw", "draw"), ("away_win", "away")):
        p = max(_CLIP, min(1 - _CLIP, probs.get(pk, 1/3)))
        if o.get(ok, 0) == 1:
            ll -= math.log(p)
    return ll


def _get_predicted_direction(probs: dict[str, float]) -> str:
    """Get the predicted direction."""
    keys = {"home_win": "home", "draw": "draw", "away_win": "away"}
    best = max(probs, key=probs.get)
    return keys.get(best, "home")
