"""Post-match model scoring engine.

Compares prediction snapshots against actual match results to produce
Brier scores, log loss, hit rates, expected goals error, and error attribution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DashboardRevision, MarketSnapshot, Match, ModelScore, PredictionSnapshot
from app.services.error_attribution import ErrorAttribution, classify_error

_CLIP = 1e-6


def _ensure_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _select_scorable_snapshot(
    snapshots: list[PredictionSnapshot],
    match: Match,
) -> PredictionSnapshot | None:
    """Choose the best scorable snapshot for a finished match.

    New rule: Use the latest user-visible prediction snapshot created before kickoff.
    T-30 locking is no longer the core scoring mechanism.

    Note: This function does NOT filter by is_pre_match_locked or is_fallback_locked.
    A fallback-locked snapshot was still created before kickoff, so it IS eligible
    for scoring. Only the snapshotted_at < kickoff boundary matters.
    """
    if not snapshots:
        return None

    kickoff = _ensure_utc(match.kickoff)
    if not kickoff:
        return None

    # Only consider snapshots created before kickoff
    pre_kickoff = [
        snap for snap in snapshots
        if _ensure_utc(snap.snapshotted_at) < kickoff
    ]

    if pre_kickoff:
        # Return the latest one - this is the "user decision snapshot"
        return max(pre_kickoff, key=lambda snap: _ensure_utc(snap.snapshotted_at))

    return None


def _scorable_snapshot_rows(session: Session) -> list[tuple[PredictionSnapshot, Match]]:
    """Return one scoring snapshot per final match."""
    rows = session.execute(
        select(PredictionSnapshot, Match)
        .join(Match, PredictionSnapshot.match_id == Match.id)
        .where(Match.status == "final")
        .order_by(
            PredictionSnapshot.match_id,
            PredictionSnapshot.snapshotted_at.desc(),
        )
    ).all()

    grouped: dict[str, tuple[Match, list[PredictionSnapshot]]] = {}
    for snap, match in rows:
        state = grouped.setdefault(match.id, (match, []))
        state[1].append(snap)

    deduped: list[tuple[PredictionSnapshot, Match]] = []
    for match, snapshots in grouped.values():
        chosen = _select_scorable_snapshot(snapshots, match)
        if chosen is not None:
            deduped.append((chosen, match))
    return deduped


def _scorable_snapshot_rows_by_version(session: Session) -> list[tuple[PredictionSnapshot, Match]]:
    """Return one scoring snapshot per (match, model_version) pair."""
    rows = session.execute(
        select(PredictionSnapshot, Match)
        .join(Match, PredictionSnapshot.match_id == Match.id)
        .where(Match.status == "final")
        .order_by(
            PredictionSnapshot.match_id,
            PredictionSnapshot.model_version,
            PredictionSnapshot.snapshotted_at.desc(),
        )
    ).all()

    grouped: dict[tuple[str, str], tuple[Match, list[PredictionSnapshot]]] = {}
    for snap, match in rows:
        key = (match.id, snap.model_version or "")
        state = grouped.setdefault(key, (match, []))
        state[1].append(snap)

    deduped: list[tuple[PredictionSnapshot, Match]] = []
    for match, snapshots in grouped.values():
        chosen = _select_scorable_snapshot(snapshots, match)
        if chosen is not None:
            deduped.append((chosen, match))
    return deduped


@dataclass(frozen=True)
class MatchScoreDetail:
    match_id: str
    home_team: str
    away_team: str
    predicted: dict[str, float]
    actual: dict[str, int]
    brier: float
    log_loss: float
    outcome_correct: bool
    top_score_correct: bool
    xg_error: float
    probability_effect: float = 0.0
    warning_effect: str = "neutral"
    numerical_effect: str = "neutral"
    error_types: tuple[str, ...] = ()
    error_reasons: tuple[str, ...] = ()
    suggested_fixes: tuple[str, ...] = ()
    model_version: str = ""
    kickoff: str = ""
    locked_at: str = ""
    max_prob: float = 0.0
    actual_result: str = ""


@dataclass(frozen=True)
class ModelScoreReport:
    matches_scored: int
    brier_score: float
    log_loss: float
    outcome_hit_rate: float
    top_score_hit_rate: float
    xg_mae: float
    per_match: list[MatchScoreDetail] = field(default_factory=list)


def _serialize_model_score_row(row: ModelScore, model_version: str) -> dict[str, Any]:
    return {
        "id": row.id,
        "revision_id": row.revision_id,
        "model_version": model_version,
        "matches_scored": row.matches_scored,
        "brier_score": row.brier_score,
        "log_loss": row.log_loss,
        "outcome_hit_rate": row.outcome_hit_rate,
        "top_score_hit_rate": row.top_score_hit_rate,
        "xg_mae": row.xg_mae,
        "per_match": row.per_match,
        "created_at": row.created_at.isoformat(),
    }


def model_score_payload(session: Session, history_limit: int = 12) -> dict[str, Any]:
    rows = session.execute(
        select(ModelScore, DashboardRevision.model_version)
        .join(DashboardRevision, ModelScore.revision_id == DashboardRevision.id)
        .order_by(ModelScore.id.desc())
    ).all()
    if not rows:
        return {
            "matches_scored": 0,
            "per_match": [],
            "history": [],
            "model_versions": [],
            "comparison": None,
        }

    history = [
        _serialize_model_score_row(score, model_version)
        for score, model_version in rows[:history_limit]
    ]

    aggregates: dict[str, dict[str, Any]] = {}
    for score, model_version in rows:
        state = aggregates.setdefault(
            model_version,
            {
                "model_version": model_version,
                "runs": 0,
                "total_matches_scored": 0,
                "weighted_brier": 0.0,
                "weighted_log_loss": 0.0,
                "weighted_outcome_hit_rate": 0.0,
                "weighted_top_score_hit_rate": 0.0,
                "weighted_xg_mae": 0.0,
                "warning_effects": {"helped": 0, "hurt": 0, "neutral": 0},
                "numerical_effects": {"helped": 0, "hurt": 0, "neutral": 0},
                "error_type_counts": {},
                "latest": None,
            },
        )
        state["runs"] += 1
        state["total_matches_scored"] += score.matches_scored
        state["weighted_brier"] += score.brier_score * score.matches_scored
        state["weighted_log_loss"] += score.log_loss * score.matches_scored
        state["weighted_outcome_hit_rate"] += score.outcome_hit_rate * score.matches_scored
        state["weighted_top_score_hit_rate"] += score.top_score_hit_rate * score.matches_scored
        state["weighted_xg_mae"] += score.xg_mae * score.matches_scored

        for detail in score.per_match:
            if "warning_effect" in detail:
                w_eff = detail["warning_effect"]
                if w_eff in state["warning_effects"]:
                    state["warning_effects"][w_eff] += 1
            if "numerical_effect" in detail:
                n_eff = detail["numerical_effect"]
                if n_eff in state["numerical_effects"]:
                    state["numerical_effects"][n_eff] += 1
            # Count error types
            for et in detail.get("error_types", []):
                state["error_type_counts"][et] = state["error_type_counts"].get(et, 0) + 1

        if state["latest"] is None:
            state["latest"] = _serialize_model_score_row(score, model_version)

    model_versions = []
    for state in aggregates.values():
        total_matches = max(state["total_matches_scored"], 1)
        latest = state["latest"]
        model_versions.append(
            {
                "model_version": state["model_version"],
                "runs": state["runs"],
                "total_matches_scored": state["total_matches_scored"],
                "average_brier_score": state["weighted_brier"] / total_matches,
                "average_log_loss": state["weighted_log_loss"] / total_matches,
                "average_outcome_hit_rate": state["weighted_outcome_hit_rate"] / total_matches,
                "average_top_score_hit_rate": state["weighted_top_score_hit_rate"] / total_matches,
                "average_xg_mae": state["weighted_xg_mae"] / total_matches,
                "warning_effects": state["warning_effects"],
                "numerical_effects": state["numerical_effects"],
                "error_type_counts": state["error_type_counts"],
                "latest": latest,
            }
        )
    model_versions.sort(
        key=lambda item: item["latest"]["created_at"],
        reverse=True,
    )

    comparison = None
    if len(model_versions) >= 2:
        current = model_versions[0]
        previous = model_versions[1]
        comparison = {
            "current_version": current,
            "previous_version": previous,
            "deltas": {
                "brier_score": current["latest"]["brier_score"] - previous["latest"]["brier_score"],
                "log_loss": current["latest"]["log_loss"] - previous["latest"]["log_loss"],
                "outcome_hit_rate": current["latest"]["outcome_hit_rate"] - previous["latest"]["outcome_hit_rate"],
                "top_score_hit_rate": current["latest"]["top_score_hit_rate"] - previous["latest"]["top_score_hit_rate"],
                "xg_mae": current["latest"]["xg_mae"] - previous["latest"]["xg_mae"],
            },
        }

    latest_score, latest_version = rows[0]
    payload = _serialize_model_score_row(latest_score, latest_version)
    payload["history"] = history
    payload["model_versions"] = model_versions
    payload["comparison"] = comparison
    return payload


def model_score_details(session: Session) -> list[dict[str, Any]]:
    """Return per-match scoring details for all scored matches."""
    from app.models import Team

    team_names = {row.id: row.short_name for row in session.scalars(select(Team))}

    # Get locked snapshots for final matches
    rows = _scorable_snapshot_rows_by_version(session)

    # Get market data for market comparison
    market_snaps = {
        row.match_id: row
        for row in session.scalars(select(MarketSnapshot).where(MarketSnapshot.provider == "sporttery"))
    }

    details = []
    for snap, match in rows:
        actual_home = match.home_score or 0
        actual_away = match.away_score or 0

        if actual_home > actual_away:
            actual_result = "home"
        elif actual_home == actual_away:
            actual_result = "draw"
        else:
            actual_result = "away"

        p_home, p_draw, p_away = snap.home_win, snap.draw, snap.away_win
        max_prob = max(p_home, p_draw, p_away)

        # Brier
        o_home = 1.0 if actual_result == "home" else 0.0
        o_draw = 1.0 if actual_result == "draw" else 0.0
        o_away = 1.0 if actual_result == "away" else 0.0
        brier = (p_home - o_home) ** 2 + (p_draw - o_draw) ** 2 + (p_away - o_away) ** 2

        # LogLoss
        cp_home = max(_CLIP, min(1 - _CLIP, p_home))
        cp_draw = max(_CLIP, min(1 - _CLIP, p_draw))
        cp_away = max(_CLIP, min(1 - _CLIP, p_away))
        ll = -(o_home * math.log(cp_home) + o_draw * math.log(cp_draw) + o_away * math.log(cp_away))

        predicted_outcome = max([("home", p_home), ("draw", p_draw), ("away", p_away)], key=lambda x: x[1])[0]
        outcome_hit = predicted_outcome == actual_result

        # Base probabilities
        b_home = snap.base_home_win if snap.base_home_win is not None else p_home
        b_draw = snap.base_draw if snap.base_draw is not None else p_draw
        b_away = snap.base_away_win if snap.base_away_win is not None else p_away
        base_brier = (b_home - o_home) ** 2 + (b_draw - o_draw) ** 2 + (b_away - o_away) ** 2
        probability_effect = base_brier - brier

        warning_effect = "neutral"
        if snap.confidence_label == "低" or getattr(snap, 'has_auto_adjustments', False):
            if not outcome_hit:
                warning_effect = "helped"
            else:
                warning_effect = "hurt"

        numerical_effect = "neutral"
        if "intel-numeric" in (snap.model_version or ""):
            if brier < base_brier - 0.01:
                numerical_effect = "helped"
            elif brier > base_brier + 0.01:
                numerical_effect = "hurt"

        # Market data
        market_snap = market_snaps.get(match.id)
        market_home = market_snap.home_probability if market_snap else None
        market_draw = market_snap.draw_probability if market_snap else None
        market_away = market_snap.away_probability if market_snap else None

        # Error attribution
        has_numerical = "intel-numeric" in (snap.model_version or "") and snap.has_auto_adjustments
        error_attrs = classify_error(
            home_win_prob=p_home,
            draw_prob=p_draw,
            away_win_prob=p_away,
            actual_result=actual_result,
            home_xg=snap.home_xg,
            away_xg=snap.away_xg,
            actual_home_score=actual_home,
            actual_away_score=actual_away,
            top_scorelines=snap.scorelines,
            market_home_prob=market_home,
            market_draw_prob=market_draw,
            market_away_prob=market_away,
            base_home_win=snap.base_home_win if snap.has_auto_adjustments else None,
            base_draw=snap.base_draw if snap.has_auto_adjustments else None,
            base_away_win=snap.base_away_win if snap.has_auto_adjustments else None,
            has_auto_adjustments=snap.has_auto_adjustments,
            has_numerical_adjustments=has_numerical,
        )

        # Compute hours_before_kickoff for the new scoring rule fields
        snap_at = _ensure_utc(snap.snapshotted_at)
        kickoff_utc = _ensure_utc(match.kickoff)
        hours_before_kickoff = (kickoff_utc - snap_at).total_seconds() / 3600 if snap_at and kickoff_utc else None

        details.append({
            "match_id": match.id,
            "kickoff": match.kickoff.isoformat() if match.kickoff else "",
            "home_team": team_names.get(match.home_team_id, match.home_team_id),
            "away_team": team_names.get(match.away_team_id, match.away_team_id),
            "model_version": snap.model_version,
            "locked_at": snap.snapshotted_at.isoformat() if snap.snapshotted_at else "",
            "scoring_snapshot_rule": "latest_pre_match_snapshot_before_kickoff",
            "snapshot_at": snap.snapshotted_at.isoformat() if snap.snapshotted_at else "",
            "hours_before_kickoff": hours_before_kickoff,
            "is_real_time_only": False,
            "home_win_prob": p_home,
            "draw_prob": p_draw,
            "away_win_prob": p_away,
            "max_prob": max_prob,
            "actual_result": actual_result,
            "outcome_hit": outcome_hit,
            "brier": brier,
            "logloss": ll,
            "xg_error": (abs(snap.home_xg - actual_home) + abs(snap.away_xg - actual_away)) / 2.0,
            "error_types": [e.error_type for e in error_attrs],
            "error_reasons": [e.error_reason for e in error_attrs],
            "suggested_fixes": [e.suggested_fix for e in error_attrs if e.suggested_fix],
            "warning_effect": warning_effect,
            "numerical_effect": numerical_effect,
            "probability_effect": probability_effect,
            "market_home_prob": market_home,
            "market_draw_prob": market_draw,
            "market_away_prob": market_away,
        })

    details.sort(key=lambda d: d["kickoff"])
    return details


def model_score_by_version(session: Session) -> list[dict[str, Any]]:
    """Aggregate model score by version with detailed error stats."""
    from app.models import Team

    team_names = {row.id: row.short_name for row in session.scalars(select(Team))}

    rows = _scorable_snapshot_rows_by_version(session)

    market_snaps = {
        row.match_id: row
        for row in session.scalars(select(MarketSnapshot).where(MarketSnapshot.provider == "sporttery"))
    }

    # Group by model_version
    by_version: dict[str, dict[str, Any]] = {}
    for snap, match in rows:
        version = snap.model_version or "unknown"
        if version not in by_version:
            by_version[version] = {
                "model_version": version,
                "sample_count": 0,
                "brier_sum": 0.0,
                "logloss_sum": 0.0,
                "hit_count": 0,
                "avg_confidence": 0.0,
                "upset_miss_count": 0,
                "draw_miss_count": 0,
                "favorite_overestimated_count": 0,
                "underdog_underestimated_count": 0,
                "overconfident_wrong_count": 0,
                "warning_helped_count": 0,
                "warning_hurt_count": 0,
                "numerical_helped_count": 0,
                "numerical_hurt_count": 0,
            }
        v = by_version[version]

        actual_home = match.home_score or 0
        actual_away = match.away_score or 0
        if actual_home > actual_away:
            actual_result = "home"
        elif actual_home == actual_away:
            actual_result = "draw"
        else:
            actual_result = "away"

        p_home, p_draw, p_away = snap.home_win, snap.draw, snap.away_win
        o_home = 1.0 if actual_result == "home" else 0.0
        o_draw = 1.0 if actual_result == "draw" else 0.0
        o_away = 1.0 if actual_result == "away" else 0.0

        brier = (p_home - o_home) ** 2 + (p_draw - o_draw) ** 2 + (p_away - o_away) ** 2
        cp_home = max(_CLIP, min(1 - _CLIP, p_home))
        cp_draw = max(_CLIP, min(1 - _CLIP, p_draw))
        cp_away = max(_CLIP, min(1 - _CLIP, p_away))
        ll = -(o_home * math.log(cp_home) + o_draw * math.log(cp_draw) + o_away * math.log(cp_away))

        predicted_outcome = max([("home", p_home), ("draw", p_draw), ("away", p_away)], key=lambda x: x[1])[0]
        outcome_correct = predicted_outcome == actual_result

        b_home = snap.base_home_win if snap.base_home_win is not None else p_home
        b_draw = snap.base_draw if snap.base_draw is not None else p_draw
        b_away = snap.base_away_win if snap.base_away_win is not None else p_away
        base_brier = (b_home - o_home) ** 2 + (b_draw - o_draw) ** 2 + (b_away - o_away) ** 2

        # Error attribution
        market_snap = market_snaps.get(match.id)
        has_numerical = "intel-numeric" in (snap.model_version or "") and snap.has_auto_adjustments
        error_attrs = classify_error(
            home_win_prob=p_home, draw_prob=p_draw, away_win_prob=p_away,
            actual_result=actual_result,
            home_xg=snap.home_xg, away_xg=snap.away_xg,
            actual_home_score=actual_home, actual_away_score=actual_away,
            top_scorelines=snap.scorelines,
            market_home_prob=market_snap.home_probability if market_snap else None,
            market_draw_prob=market_snap.draw_probability if market_snap else None,
            market_away_prob=market_snap.away_probability if market_snap else None,
            base_home_win=snap.base_home_win if snap.has_auto_adjustments else None,
            base_draw=snap.base_draw if snap.has_auto_adjustments else None,
            base_away_win=snap.base_away_win if snap.has_auto_adjustments else None,
            has_auto_adjustments=snap.has_auto_adjustments,
            has_numerical_adjustments=has_numerical,
        )
        error_types_set = {e.error_type for e in error_attrs}

        v["sample_count"] += 1
        v["brier_sum"] += brier
        v["logloss_sum"] += ll
        v["hit_count"] += int(outcome_correct)
        v["avg_confidence"] += max(p_home, p_draw, p_away)
        if "favorite_overestimated" in error_types_set:
            v["favorite_overestimated_count"] += 1
        if "underdog_underestimated" in error_types_set:
            v["underdog_underestimated_count"] += 1
        if "overconfident_wrong" in error_types_set:
            v["overconfident_wrong_count"] += 1
        # upset_miss: favorite won but model didn't predict it
        if not outcome_correct and actual_result != "draw" and max(p_home, p_away) < 0.50:
            v["upset_miss_count"] += 1
        # draw_miss: actual draw but model predicted something else
        if actual_result == "draw" and predicted_outcome != "draw":
            v["draw_miss_count"] += 1
        # warning/numerical effects
        if snap.has_auto_adjustments:
            if not outcome_correct:
                v["warning_helped_count"] += 1
            else:
                v["warning_hurt_count"] += 1
        if "intel-numeric" in (snap.model_version or ""):
            if brier < base_brier - 0.01:
                v["numerical_helped_count"] += 1
            elif brier > base_brier + 0.01:
                v["numerical_hurt_count"] += 1

    result = []
    for version, v in by_version.items():
        n = max(v["sample_count"], 1)
        result.append({
            "model_version": version,
            "sample_count": v["sample_count"],
            "hit_rate": v["hit_count"] / n,
            "brier": v["brier_sum"] / n,
            "logloss": v["logloss_sum"] / n,
            "avg_confidence": v["avg_confidence"] / n,
            "upset_miss_count": v["upset_miss_count"],
            "draw_miss_count": v["draw_miss_count"],
            "favorite_overestimated_count": v["favorite_overestimated_count"],
            "underdog_underestimated_count": v["underdog_underestimated_count"],
            "overconfident_wrong_count": v["overconfident_wrong_count"],
            "warning_helped_count": v["warning_helped_count"],
            "warning_hurt_count": v["warning_hurt_count"],
            "numerical_helped_count": v["numerical_helped_count"],
            "numerical_hurt_count": v["numerical_hurt_count"],
        })

    return sorted(result, key=lambda x: x["brier"])


def score_predictions(
    snapshots: list[tuple[PredictionSnapshot, Match]],
    team_names: dict[str, str] | None = None,
    market_snaps: dict[str, MarketSnapshot] | None = None,
) -> ModelScoreReport:
    """Score a list of (snapshot, finalised-match) pairs.

    Pure function — does not touch the database.
    """
    if not snapshots:
        return ModelScoreReport(
            matches_scored=0,
            brier_score=0.0,
            log_loss=0.0,
            outcome_hit_rate=0.0,
            top_score_hit_rate=0.0,
            xg_mae=0.0,
        )

    names = team_names or {}
    markets = market_snaps or {}
    brier_sum = 0.0
    log_loss_sum = 0.0
    outcome_hits = 0
    top_score_hits = 0
    xg_error_sum = 0.0
    details: list[MatchScoreDetail] = []

    for snap, match in snapshots:
        actual_home = match.home_score or 0
        actual_away = match.away_score or 0

        # Outcome indicators
        if actual_home > actual_away:
            o_home, o_draw, o_away = 1.0, 0.0, 0.0
            actual_result = "home"
        elif actual_home == actual_away:
            o_home, o_draw, o_away = 0.0, 1.0, 0.0
            actual_result = "draw"
        else:
            o_home, o_draw, o_away = 0.0, 0.0, 1.0
            actual_result = "away"

        p_home = snap.home_win
        p_draw = snap.draw
        p_away = snap.away_win
        max_prob = max(p_home, p_draw, p_away)

        # Brier score for this match
        brier = (p_home - o_home) ** 2 + (p_draw - o_draw) ** 2 + (p_away - o_away) ** 2
        brier_sum += brier

        # Log loss (clip probabilities to avoid log(0))
        cp_home = max(_CLIP, min(1 - _CLIP, p_home))
        cp_draw = max(_CLIP, min(1 - _CLIP, p_draw))
        cp_away = max(_CLIP, min(1 - _CLIP, p_away))
        ll = -(o_home * math.log(cp_home) + o_draw * math.log(cp_draw) + o_away * math.log(cp_away))
        log_loss_sum += ll

        # Outcome hit
        predicted_outcome = max(
            [("home", p_home), ("draw", p_draw), ("away", p_away)],
            key=lambda x: x[1],
        )[0]
        outcome_correct = predicted_outcome == actual_result
        if outcome_correct:
            outcome_hits += 1

        # Base probabilities
        b_home = snap.base_home_win if snap.base_home_win is not None else p_home
        b_draw = snap.base_draw if snap.base_draw is not None else p_draw
        b_away = snap.base_away_win if snap.base_away_win is not None else p_away

        base_brier = (b_home - o_home) ** 2 + (b_draw - o_draw) ** 2 + (b_away - o_away) ** 2
        probability_effect = base_brier - brier

        warning_effect = "neutral"
        if snap.confidence_label == "低" or getattr(snap, 'has_auto_adjustments', False):
            if not outcome_correct:
                warning_effect = "helped"
            else:
                warning_effect = "hurt"

        numerical_effect = "neutral"
        if "intel-numeric" in (snap.model_version or ""):
            if brier < base_brier - 0.01:
                numerical_effect = "helped"
            elif brier > base_brier + 0.01:
                numerical_effect = "hurt"

        # Top score hit
        actual_scoreline = (actual_home, actual_away)
        top_scores = [(s.get("home_goals", s[0] if isinstance(s, (list, tuple)) else 0),
                       s.get("away_goals", s[1] if isinstance(s, (list, tuple)) else 0))
                      for s in snap.scorelines]
        top_score_correct = actual_scoreline in top_scores
        if top_score_correct:
            top_score_hits += 1

        # xG error
        xg_error = (abs(snap.home_xg - actual_home) + abs(snap.away_xg - actual_away)) / 2.0
        xg_error_sum += xg_error

        # Error attribution
        market_snap = markets.get(match.id)
        has_numerical = "intel-numeric" in (snap.model_version or "") and snap.has_auto_adjustments
        error_attrs = classify_error(
            home_win_prob=p_home, draw_prob=p_draw, away_win_prob=p_away,
            actual_result=actual_result,
            home_xg=snap.home_xg, away_xg=snap.away_xg,
            actual_home_score=actual_home, actual_away_score=actual_away,
            top_scorelines=snap.scorelines,
            market_home_prob=market_snap.home_probability if market_snap else None,
            market_draw_prob=market_snap.draw_probability if market_snap else None,
            market_away_prob=market_snap.away_probability if market_snap else None,
            base_home_win=snap.base_home_win if snap.has_auto_adjustments else None,
            base_draw=snap.base_draw if snap.has_auto_adjustments else None,
            base_away_win=snap.base_away_win if snap.has_auto_adjustments else None,
            has_auto_adjustments=snap.has_auto_adjustments,
            has_numerical_adjustments=has_numerical,
        )

        details.append(
            MatchScoreDetail(
                match_id=match.id,
                home_team=names.get(match.home_team_id, match.home_team_id),
                away_team=names.get(match.away_team_id, match.away_team_id),
                predicted={
                    "home_win": p_home,
                    "draw": p_draw,
                    "away_win": p_away,
                    "home_xg": snap.home_xg,
                    "away_xg": snap.away_xg,
                },
                actual={"home_score": actual_home, "away_score": actual_away},
                brier=brier,
                log_loss=ll,
                outcome_correct=outcome_correct,
                probability_effect=probability_effect,
                warning_effect=warning_effect,
                numerical_effect=numerical_effect,
                top_score_correct=top_score_correct,
                xg_error=xg_error,
                error_types=tuple(e.error_type for e in error_attrs),
                error_reasons=tuple(e.error_reason for e in error_attrs),
                suggested_fixes=tuple(e.suggested_fix for e in error_attrs if e.suggested_fix),
                model_version=snap.model_version,
                kickoff=match.kickoff.isoformat() if match.kickoff else "",
                locked_at=snap.snapshotted_at.isoformat() if snap.snapshotted_at else "",
                max_prob=max_prob,
                actual_result=actual_result,
            )
        )

    n = len(snapshots)
    return ModelScoreReport(
        matches_scored=n,
        brier_score=brier_sum / n,
        log_loss=log_loss_sum / n,
        outcome_hit_rate=outcome_hits / n,
        top_score_hit_rate=top_score_hits / n,
        xg_mae=xg_error_sum / n,
        per_match=details,
    )


def score_model(session: Session) -> ModelScoreReport:
    """Score all finalised matches that have prediction snapshots.

    Reads from the database and returns a report.
    """
    from app.models import Team

    # Build team name lookup
    team_names = {
        row.id: row.short_name
        for row in session.scalars(select(Team))
    }

    # Get market snapshots
    market_snaps = {
        row.match_id: row
        for row in session.scalars(select(MarketSnapshot).where(MarketSnapshot.provider == "sporttery"))
    }

    # Find all snapshots whose match is final
    rows = _scorable_snapshot_rows(session)

    pairs = [(snap, match) for snap, match in rows]
    return score_predictions(pairs, team_names, market_snaps)


def save_model_score(session: Session, report: ModelScoreReport, revision_id: int) -> ModelScore | None:
    """Persist a ModelScore row from a report. Returns the saved row or None if empty."""
    if report.matches_scored == 0:
        return None
    row = ModelScore(
        revision_id=revision_id,
        matches_scored=report.matches_scored,
        brier_score=report.brier_score,
        log_loss=report.log_loss,
        outcome_hit_rate=report.outcome_hit_rate,
        top_score_hit_rate=report.top_score_hit_rate,
        xg_mae=report.xg_mae,
        per_match=[
            {
                "match_id": d.match_id,
                "home_team": d.home_team,
                "away_team": d.away_team,
                "predicted": d.predicted,
                "actual": d.actual,
                "brier": d.brier,
                "log_loss": d.log_loss,
                "outcome_correct": d.outcome_correct,
                "top_score_correct": d.top_score_correct,
                "xg_error": d.xg_error,
                "error_types": list(d.error_types),
                "error_reasons": list(d.error_reasons),
                "suggested_fixes": list(d.suggested_fixes),
                "warning_effect": d.warning_effect,
                "numerical_effect": d.numerical_effect,
            }
            for d in report.per_match
        ],
    )
    session.add(row)
    session.flush()
    return row


def snapshot_prediction(session: Session, match_id: str) -> PredictionSnapshot | None:
    """Find the latest pre-match snapshot for this match.

    Called when a match transitions to 'final'.
    Uses the new scoring rule: latest pre-match user-visible snapshot.
    T-30 locking is no longer the core scoring mechanism.
    """
    import logging

    logger = logging.getLogger(__name__)

    match = session.get(Match, match_id)
    if not match:
        return None

    kickoff = _ensure_utc(match.kickoff)
    if not kickoff:
        return None

    # Find the latest snapshot created before kickoff
    latest_pre_match = session.scalar(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.match_id == match_id)
        .where(PredictionSnapshot.snapshotted_at < kickoff)
        .order_by(PredictionSnapshot.snapshotted_at.desc())
        .limit(1)
    )

    if latest_pre_match is None:
        logger.warning(f"No pre-match snapshot found for {match_id}")
        return None

    # Mark as fallback_locked for backward compatibility (old field, not core mechanism)
    if not latest_pre_match.is_pre_match_locked and not latest_pre_match.is_fallback_locked:
        latest_pre_match.is_fallback_locked = True
        session.add(latest_pre_match)
        session.flush()

    return latest_pre_match


def model_score_by_stage(session: Session) -> dict[str, list[dict[str, Any]]]:
    """Aggregate model scores by tournament stage and version."""
    from app.models import Team

    team_names = {row.id: row.short_name for row in session.scalars(select(Team))}

    rows = _scorable_snapshot_rows(session)

    market_snaps = {
        row.match_id: row
        for row in session.scalars(select(MarketSnapshot).where(MarketSnapshot.provider == "sporttery"))
    }

    # Group by (stage, model_version)
    by_stage_version: dict[str, dict[str, dict[str, Any]]] = {}
    for snap, match in rows:
        stage = getattr(match, 'stage', 'group') or 'group'
        version = snap.model_version or "unknown"
        key = stage
        if key not in by_stage_version:
            by_stage_version[key] = {}
        if version not in by_stage_version[key]:
            by_stage_version[key][version] = {
                "model_version": version,
                "sample_count": 0,
                "brier_sum": 0.0,
                "logloss_sum": 0.0,
                "hit_count": 0,
            }
        v = by_stage_version[key][version]

        actual_home = match.home_score or 0
        actual_away = match.away_score or 0
        if actual_home > actual_away:
            actual_result = "home"
        elif actual_home == actual_away:
            actual_result = "draw"
        else:
            actual_result = "away"

        p_home, p_draw, p_away = snap.home_win, snap.draw, snap.away_win
        o_home = 1.0 if actual_result == "home" else 0.0
        o_draw = 1.0 if actual_result == "draw" else 0.0
        o_away = 1.0 if actual_result == "away" else 0.0

        brier = (p_home - o_home) ** 2 + (p_draw - o_draw) ** 2 + (p_away - o_away) ** 2
        cp_home = max(_CLIP, min(1 - _CLIP, p_home))
        cp_draw = max(_CLIP, min(1 - _CLIP, p_draw))
        cp_away = max(_CLIP, min(1 - _CLIP, p_away))
        ll = -(o_home * math.log(cp_home) + o_draw * math.log(cp_draw) + o_away * math.log(cp_away))

        predicted_outcome = max([("home", p_home), ("draw", p_draw), ("away", p_away)], key=lambda x: x[1])[0]
        outcome_correct = predicted_outcome == actual_result

        v["sample_count"] += 1
        v["brier_sum"] += brier
        v["logloss_sum"] += ll
        v["hit_count"] += int(outcome_correct)

    # Build output
    result = {}
    for stage, versions in sorted(by_stage_version.items()):
        stage_versions = []
        for version, v in versions.items():
            n = max(v["sample_count"], 1)
            stage_versions.append({
                "model_version": version,
                "sample_count": v["sample_count"],
                "brier": v["brier_sum"] / n,
                "logloss": v["logloss_sum"] / n,
                "hit_rate": v["hit_count"] / n,
            })
        stage_versions.sort(key=lambda x: x["brier"])
        result[stage] = stage_versions

    return result


def get_scoring_exclusions(session: Session) -> list[dict[str, Any]]:
    """List finished matches that were NOT scored and explain why.

    A match is excluded from scoring if:
    1. It has no pre-match snapshot at all (no_pre_match_snapshot)
    2. It has snapshots but all are after kickoff (excluded_after_kickoff)
    3. It has no AI prediction generated (for AI model scoring)
    4. It has no prediction at all
    5. It has no final score (data issue)

    Note: no_locked_snapshot is no longer an exclusion reason under the new
    scoring rule (latest pre-match snapshot before kickoff).
    """
    from app.models import Team, AIPrediction, MatchPrediction, EnsemblePrediction

    team_names = {row.id: row.short_name for row in session.scalars(select(Team))}

    # All finished matches
    finished_matches = list(session.scalars(
        select(Match).where(Match.status == "final")
    ))

    # Matches that have scorable snapshots (these ARE scored)
    scored_match_ids = {snap.match_id for snap, _match in _scorable_snapshot_rows(session)}

    # Matches that have AI predictions (no error)
    ai_match_ids = set(
        row[0] for row in session.execute(
            select(AIPrediction.match_id)
            .where(AIPrediction.error_code.is_(None))
            .where(AIPrediction.parsed_home_win.isnot(None))
        )
    )

    # Matches that have ensemble predictions
    ensemble_match_ids = set(
        row[0] for row in session.execute(
            select(EnsemblePrediction.match_id)
        )
    )

    # Matches that have any MatchPrediction
    prediction_match_ids = set(
        row[0] for row in session.execute(
            select(MatchPrediction.match_id)
        )
    )

    exclusions = []
    for match in finished_matches:
        reasons = []
        reason_codes = []
        has_scorable_snap = match.id in scored_match_ids
        has_ai_pred = match.id in ai_match_ids
        has_ensemble_pred = match.id in ensemble_match_ids

        # Check for no final score
        if match.home_score is None or match.away_score is None:
            reasons.append("比赛已完赛但无最终比分（数据异常）")
            reason_codes.append("no_final_score")

        # Check for no prediction at all
        if match.id not in prediction_match_ids:
            reasons.append("无任何模型预测")
            reason_codes.append("no_prediction")

        if not has_scorable_snap:
            # Check if it has any snapshot at all
            has_any_snap = session.scalar(
                select(PredictionSnapshot.match_id)
                .where(PredictionSnapshot.match_id == match.id)
                .limit(1)
            )
            if has_any_snap:
                # Check if any snapshot was created before kickoff
                kickoff = _ensure_utc(match.kickoff)
                has_pre_kickoff_snap = session.scalar(
                    select(PredictionSnapshot.match_id)
                    .where(
                        PredictionSnapshot.match_id == match.id,
                        PredictionSnapshot.snapshotted_at < kickoff,
                    )
                    .limit(1)
                )
                if has_pre_kickoff_snap:
                    # This shouldn't happen under new rule since any pre-kickoff snap is scorable
                    # But keep as safety net
                    pass
                else:
                    reasons.append("预测在开赛后创建，无法用于评分")
                    reason_codes.append("excluded_after_kickoff")
            else:
                reasons.append("缺少赛前预测快照（无任何预测快照）")
                reason_codes.append("no_pre_match_snapshot")

        if not has_ai_pred:
            reasons.append("AI 未生成有效预测")
            reason_codes.append("ai_missing")

        if not has_ensemble_pred:
            reasons.append("无集成预测")
            reason_codes.append("ensemble_missing")

        if reasons:
            exclusions.append({
                "match_id": match.id,
                "home_team": team_names.get(match.home_team_id, match.home_team_id),
                "away_team": team_names.get(match.away_team_id, match.away_team_id),
                "reason": "；".join(reasons),
                "reason_codes": reason_codes,
            })

    return exclusions


@dataclass
class MatchCountBreakdown:
    total_finished: int          # 已完赛比赛数
    has_pre_match_prediction: int  # 有赛前预测的比赛数
    has_pre_kickoff_snapshot: int  # 有开赛前快照的比赛数（新核心指标）
    has_locked_snapshot: int     # 有 locked/fallback 快照的比赛数（向后兼容）
    has_fallback_snapshot: int   # 有 fallback_locked 快照的比赛数
    actually_scored: int         # 实际进入 model-score 的比赛数
    missing_snapshot: int        # 缺少赛前快照而未评分的比赛数
    details: list[dict]          # 每场比赛的详细状态


def get_match_count_breakdown(session: Session) -> MatchCountBreakdown:
    """Return a detailed breakdown of match count categories for scoring.

    Under the new rule, a match with any pre-kickoff snapshot is scorable,
    regardless of lock status. T-30 locking is for backward compatibility only.
    """
    from app.models import Team, MatchPrediction, AIPrediction, EnsemblePrediction

    team_names = {row.id: row.short_name for row in session.scalars(select(Team))}

    # All finished matches
    finished_matches = list(session.scalars(
        select(Match).where(Match.status == "final")
    ))

    # Scored match IDs (baseline only)
    scored_rows = _scorable_snapshot_rows(session)
    scored_match_ids = {snap.match_id for snap, _match in scored_rows}

    details = []
    has_pre_match_prediction = 0
    has_pre_kickoff_snapshot = 0
    has_locked_snapshot = 0
    has_fallback_snapshot = 0
    actually_scored = 0
    missing_snapshot = 0

    for match in finished_matches:
        kickoff = _ensure_utc(match.kickoff)

        # Check for MatchPrediction
        has_prediction = session.scalar(
            select(MatchPrediction.match_id)
            .where(MatchPrediction.match_id == match.id)
            .limit(1)
        ) is not None

        # Check for pre-match prediction (created before kickoff)
        has_pre_match_pred = session.scalar(
            select(MatchPrediction.match_id)
            .where(
                MatchPrediction.match_id == match.id,
            )
            .limit(1)
        ) is not None

        # Check for snapshots
        snapshots = list(session.scalars(
            select(PredictionSnapshot)
            .where(PredictionSnapshot.match_id == match.id)
            .order_by(PredictionSnapshot.snapshotted_at.desc())
        ))

        has_any_snap = len(snapshots) > 0
        has_pre_kickoff_snap = any(
            _ensure_utc(s.snapshotted_at) < kickoff for s in snapshots
        )
        has_locked_snap = any(
            s.is_pre_match_locked for s in snapshots
        )
        has_fallback_snap = any(
            s.is_fallback_locked for s in snapshots
        )
        is_scored = match.id in scored_match_ids

        # Determine status - new rule: any pre-kickoff snapshot is scorable
        if match.home_score is None or match.away_score is None:
            status = "no_final_score"
            status_label = "无最终比分"
        elif is_scored:
            status = "scored"
            status_label = "已评分"
        elif not has_any_snap:
            status = "no_pre_match_snapshot"
            status_label = "缺少赛前快照"
        elif not has_pre_kickoff_snap:
            status = "excluded_after_kickoff"
            status_label = "开赛后创建预测"
        elif not has_prediction:
            status = "no_prediction"
            status_label = "无预测"
        else:
            status = "other"
            status_label = "其他原因"

        # Check AI and ensemble
        has_ai = session.scalar(
            select(AIPrediction.match_id)
            .where(
                AIPrediction.match_id == match.id,
                AIPrediction.error_code.is_(None),
                AIPrediction.parsed_home_win.is_not(None),
            )
            .limit(1)
        ) is not None

        has_ensemble = session.scalar(
            select(EnsemblePrediction.match_id)
            .where(EnsemblePrediction.match_id == match.id)
            .limit(1)
        ) is not None

        if has_pre_match_pred:
            has_pre_match_prediction += 1
        if has_pre_kickoff_snap:
            has_pre_kickoff_snapshot += 1
        if has_locked_snap or has_fallback_snap:
            has_locked_snapshot += 1
        if has_fallback_snap:
            has_fallback_snapshot += 1
        if is_scored:
            actually_scored += 1
        if not has_any_snap or not has_pre_kickoff_snap:
            missing_snapshot += 1

        details.append({
            "match_id": match.id,
            "home_team": team_names.get(match.home_team_id, match.home_team_id),
            "away_team": team_names.get(match.away_team_id, match.away_team_id),
            "status": status,
            "status_label": status_label,
            "has_prediction": has_prediction,
            "has_snapshot": has_any_snap,
            "has_pre_kickoff_snapshot": has_pre_kickoff_snap,
            "has_locked_snapshot": has_locked_snap,
            "has_fallback_snapshot": has_fallback_snap,
            "is_scored": is_scored,
            "has_ai": has_ai,
            "has_ensemble": has_ensemble,
        })

    return MatchCountBreakdown(
        total_finished=len(finished_matches),
        has_pre_match_prediction=has_pre_match_prediction,
        has_pre_kickoff_snapshot=has_pre_kickoff_snapshot,
        has_locked_snapshot=has_locked_snapshot,
        has_fallback_snapshot=has_fallback_snapshot,
        actually_scored=actually_scored,
        missing_snapshot=missing_snapshot,
        details=details,
    )


def aggregate_error_attributions(session: Session) -> dict[str, Any]:
    """Aggregate error attributions across all scored matches."""
    from app.services.error_attribution import classify_error

    # Get all scored matches
    details = model_score_details(session)

    counts: dict[str, int] = {
        "draw_underestimated": 0,
        "favorite_overestimated": 0,
        "underdog_underestimated": 0,
        "overconfident_wrong": 0,
        "low_score_draw_missed": 0,
        "market_missing": 0,
        "ai_missing": 0,
        "ensemble_helped": 0,
        "ensemble_hurt": 0,
    }

    for d in details:
        for error_type in d.get("error_types", []):
            if error_type in counts:
                counts[error_type] += 1
        # Check for market_missing
        if d.get("market_home_prob") is None:
            counts["market_missing"] += 1
        # Check for ensemble effects
        if d.get("warning_effect") == "helped":
            counts["ensemble_helped"] += 1
        elif d.get("warning_effect") == "hurt":
            counts["ensemble_hurt"] += 1

        # Detect low_score_draw_missed:
        # actual result is draw with total goals <= 2 AND draw_prob < 28%
        actual_result = d.get("actual_result", "")
        if actual_result == "draw":
            draw_prob = d.get("draw_prob", 0)
            if draw_prob < 0.28:
                match_id = d.get("match_id", "")
                match = session.get(Match, match_id) if match_id else None
                if match and match.home_score is not None and match.away_score is not None:
                    total_goals = match.home_score + match.away_score
                    if total_goals <= 2:
                        counts["low_score_draw_missed"] += 1

    total_scored = len(details)
    return {
        "total_scored": total_scored,
        "counts": counts,
        "rates": {k: v / max(total_scored, 1) for k, v in counts.items()},
    }
