"""Accuracy Command Center - unified accuracy assessment across all model versions."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.model_registry import list_enabled_models
from app.models import AIPrediction, Match, Team

logger = logging.getLogger(__name__)


def get_accuracy_command_center(session: Session) -> dict[str, Any]:
    """Build the unified accuracy command center payload."""
    from app.ai.service import is_ai_enabled
    from app.services.scoring import (
        _scorable_snapshot_rows,
        get_scoring_exclusions,
        model_score_by_version,
    )
    from app.services.model_recommendation import get_model_recommendation

    scored_rows = _scorable_snapshot_rows(session)
    version_scores = model_score_by_version(session)
    total_scored = len(scored_rows)

    # 1. Model recommendation
    recommendation = get_model_recommendation(session, version_scores=version_scores)

    # 2. Version scores (system baseline)
    # 3. AI/ensemble evaluation
    ai_eval = _evaluate_all_sources(session)

    # 4. Error pattern analysis
    error_analysis = _analyze_error_patterns(session, scored_rows=scored_rows)

    # 5. Sample size assessment
    sample_sufficient = total_scored >= 20

    # 6. Build per-model scores
    baseline_score = _find_version(version_scores, "elo-poisson-v1")
    market_score = _find_version(version_scores, "elo-poisson-v1-market-lite")
    flash_score = ai_eval.get("ai_by_version", {}).get("ai-deepseek-v4-flash-v1", {})
    pro_score = ai_eval.get("ai_by_version", {}).get("ai-deepseek-v4-pro-v1", {})
    ensemble_score = ai_eval.get("ensemble", {})

    # Build ai_model_scores dict for all AI model versions
    ai_model_scores = {}
    for version, results in ai_eval.get("ai_by_version", {}).items():
        ai_model_scores[version] = _format_ai_score(results)

    # 7. AI enabled status
    ai_enabled = is_ai_enabled()
    ai_models = list_enabled_models()

    # 8. Max error type
    top_errors = error_analysis.get("top_error_types", [])
    max_error_type = top_errors[0]["type"] if top_errors else None

    # 9. Recent match scores (last 5)
    recent_match_scores = _get_recent_match_scores(session, scored_rows=scored_rows)

    # 10. Upcoming matches needing predictions
    upcoming_matches = _get_upcoming_matches(session)

    # 11. Scoring exclusions - explain why finished matches are not scored
    scoring_exclusions = get_scoring_exclusions(session, scored_rows=scored_rows)

    return {
        "recommended_model": recommendation.get("recommended_model_version", "elo-poisson-v1"),
        "recommendation_reason": recommendation.get("reason", ""),
        "sample_sufficient": sample_sufficient,
        "sample_count": total_scored,
        "baseline_score": _format_version_score(baseline_score),
        "market_score": _format_version_score(market_score),
        "ai_model_scores": ai_model_scores,
        "ai_flash_score": _format_ai_score(flash_score),
        "ai_pro_score": _format_ai_score(pro_score),
        "ensemble_score": _format_ai_score(ensemble_score),
        "top_error_types": top_errors,
        "max_error_type": max_error_type,
        "draw_underestimated": error_analysis.get("draw_underestimated", False),
        "favorite_overestimated": error_analysis.get("favorite_overestimated", False),
        "strong_team_overestimated": error_analysis.get("favorite_overestimated", False),
        "upset_underestimated": error_analysis.get("upset_underestimated", False),
        "ai_helped": ai_eval.get("ai_effect", {}).get("overall_ai", {}).get("effect") == "helped",
        "ai_helpful": ai_eval.get("ai_effect", {}).get("overall_ai", {}).get("effect") == "helped",
        "ensemble_helped": ai_eval.get("ai_effect", {}).get("ensemble", {}).get("effect") == "helped",
        "ensemble_helpful": ai_eval.get("ai_effect", {}).get("ensemble", {}).get("effect") == "helped",
        "next_round_recommendation": recommendation.get("recommended_model_version", "elo-poisson-v1"),
        "next_recommended_version": recommendation.get("recommended_model_version", "elo-poisson-v1"),
        "insufficient_reason": _get_insufficient_reason(total_scored, ai_enabled, ai_models, flash_score, pro_score),
        "cannot_conclude_reason": _get_insufficient_reason(total_scored, ai_enabled, ai_models, flash_score, pro_score),
        "recent_match_scores": recent_match_scores,
        "upcoming_matches": upcoming_matches,
        "ai_enabled": ai_enabled,
        "ai_models_configured": len(ai_models),
        "scoring_exclusions": scoring_exclusions,
        # Structured model comparison for Baseline vs AI v1 vs AI v2 vs Ensemble
        "model_comparison": _build_model_comparison(
            baseline_score, version_scores, ai_eval, ensemble_score
        ),
        # Frontend compatibility fields
        "version_scores": version_scores,
        "model_recommendation": {
            "recommended_model_version": recommendation.get("recommended_model_version", "elo-poisson-v1"),
            "confidence": recommendation.get("confidence", "low"),
            "reason": recommendation.get("reason", ""),
            "fallback_model_version": recommendation.get("fallback_model_version", "elo-poisson-v1"),
            "sample_warning": recommendation.get("sample_warning"),
            "brier_improvement": recommendation.get("brier_improvement"),
            "relative_improvement": recommendation.get("relative_improvement"),
        },
    }


def _evaluate_all_sources(session: Session) -> dict[str, Any]:
    """Evaluate AI and ensemble predictions."""
    from app.ai.evaluation import evaluate_ai_predictions
    try:
        return evaluate_ai_predictions(session)
    except Exception as e:
        logger.warning(f"AI evaluation failed: {e}")
        return {}


def _analyze_error_patterns(
    session: Session,
    scored_rows: list[tuple[Any, Match]] | None = None,
) -> dict[str, Any]:
    """Analyze common error patterns across all predictions."""
    from app.services.scoring import _scorable_snapshot_rows
    from app.services.error_attribution import classify_error

    rows = scored_rows if scored_rows is not None else _scorable_snapshot_rows(session)

    error_counts: dict[str, int] = {}
    draw_miss = 0
    favorite_over = 0
    upset_miss = 0
    total = 0

    for snap, match in rows:
        total += 1
        actual_home = match.home_score or 0
        actual_away = match.away_score or 0
        if actual_home > actual_away:
            actual_result = "home"
        elif actual_home == actual_away:
            actual_result = "draw"
        else:
            actual_result = "away"

        p_home, p_draw, p_away = snap.home_win, snap.draw, snap.away_win
        predicted = max([("home", p_home), ("draw", p_draw), ("away", p_away)], key=lambda x: x[1])[0]

        if actual_result == "draw" and predicted != "draw":
            draw_miss += 1
        if actual_result != "draw" and predicted != actual_result:
            max_prob = max(p_home, p_away)
            if max_prob >= 0.5:
                favorite_over += 1
            else:
                upset_miss += 1

        try:
            error_attrs = classify_error(
                home_win_prob=p_home, draw_prob=p_draw, away_win_prob=p_away,
                actual_result=actual_result,
                home_xg=snap.home_xg, away_xg=snap.away_xg,
                actual_home_score=actual_home, actual_away_score=actual_away,
                top_scorelines=snap.scorelines,
            )
            for e in error_attrs:
                error_counts[e.error_type] = error_counts.get(e.error_type, 0) + 1
        except Exception:
            pass

    sorted_errors = sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "top_error_types": [{"type": t, "count": c} for t, c in sorted_errors],
        "draw_underestimated": draw_miss > total * 0.3 if total > 0 else False,
        "favorite_overestimated": favorite_over > total * 0.2 if total > 0 else False,
        "upset_underestimated": upset_miss > total * 0.15 if total > 0 else False,
    }


def _find_version(version_scores: list[dict], version_name: str) -> dict:
    """Find a specific version in the score list."""
    for v in version_scores:
        if v.get("model_version") == version_name:
            return v
    return {}


def _format_version_score(v: dict) -> dict[str, Any]:
    """Format a version score for the command center."""
    if not v:
        return {"available": False}
    return {
        "available": True,
        "sample_count": v.get("sample_count", 0),
        "brier": v.get("brier"),
        "logloss": v.get("logloss"),
        "hit_rate": v.get("hit_rate"),
    }


def _format_ai_score(v: dict) -> dict[str, Any]:
    """Format an AI/ensemble score for the command center."""
    if not v or v.get("sample_count", 0) == 0:
        return {"available": False}
    return {
        "available": True,
        "sample_count": v.get("sample_count", 0),
        "brier": v.get("brier"),
        "logloss": v.get("logloss"),
        "hit_rate": v.get("hit_rate"),
        "helped": v.get("helped", 0),
        "hurt": v.get("hurt", 0),
    }


def _get_insufficient_reason(
    total: int,
    ai_enabled: bool,
    ai_models: list,
    flash_score: dict,
    pro_score: dict,
) -> str:
    """Explain why we can't draw conclusions yet."""
    reasons = []
    if total < 20:
        reasons.append(f"样本量不足（{total}场比赛，需≥20场）")
    if not ai_enabled:
        reasons.append("AI预测未启用")
    if not flash_score.get("sample_count"):
        reasons.append("DeepSeek Flash 尚无评分数据")
    if not pro_score.get("sample_count"):
        reasons.append("DeepSeek Pro 尚无评分数据")
    if not reasons:
        return ""
    return "；".join(reasons)


def _get_recent_match_scores(
    session: Session,
    limit: int = 5,
    scored_rows: list[tuple[Any, Match]] | None = None,
) -> list[dict[str, Any]]:
    """Get the last N scored match details."""
    from app.services.scoring import _scorable_snapshot_rows

    team_names = {row.id: row.short_name for row in session.scalars(select(Team))}
    rows = sorted(
        scored_rows if scored_rows is not None else _scorable_snapshot_rows(session),
        key=lambda row: row[1].kickoff,
        reverse=True,
    )[:limit]

    results = []
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
        o_home = 1.0 if actual_result == "home" else 0.0
        o_draw = 1.0 if actual_result == "draw" else 0.0
        o_away = 1.0 if actual_result == "away" else 0.0
        brier = (p_home - o_home) ** 2 + (p_draw - o_draw) ** 2 + (p_away - o_away) ** 2

        predicted = max([("home", p_home), ("draw", p_draw), ("away", p_away)], key=lambda x: x[1])[0]
        outcome_correct = predicted == actual_result

        results.append({
            "match_id": match.id,
            "home_team": team_names.get(match.home_team_id, match.home_team_id),
            "away_team": team_names.get(match.away_team_id, match.away_team_id),
            "kickoff": match.kickoff.isoformat() if match.kickoff else "",
            "predicted": predicted,
            "actual": actual_result,
            "outcome_correct": outcome_correct,
            "brier": round(brier, 4),
            "home_win_prob": round(p_home, 3),
            "draw_prob": round(p_draw, 3),
            "away_win_prob": round(p_away, 3),
            "home_score": actual_home,
            "away_score": actual_away,
        })
    return results


def _build_model_comparison(
    baseline_score: dict,
    version_scores: list[dict],
    ai_eval: dict[str, Any],
    ensemble_score: dict,
) -> list[dict[str, Any]]:
    """Build structured comparison: Baseline vs AI v1 vs AI v2 vs Ensemble."""
    comparison = []

    # Baseline — available is based on sample_count, not the formatted dict
    baseline_sample_count = baseline_score.get("sample_count", 0)
    comparison.append({
        "source": "Baseline",
        "model_version": "elo-poisson-v1",
        "prompt_version": None,
        "role": "production",
        "sample_count": baseline_sample_count,
        "brier": baseline_score.get("brier"),
        "logloss": baseline_score.get("logloss"),
        "hit_rate": baseline_score.get("hit_rate"),
        "available": baseline_sample_count > 0,
    })

    # AI models by version
    for version, results in ai_eval.get("ai_by_version", {}).items():
        from app.ai.model_registry import get_model_config
        config = get_model_config(version)
        role = config.role if config else "unknown"
        prompt_version = config.prompt_version if config else "unknown"
        include_in_ensemble = config.ensemble_weight > 0 if config else True

        comparison.append({
            "source": f"AI ({version})",
            "model_version": version,
            "prompt_version": prompt_version,
            "role": "shadow" if not include_in_ensemble else "production",
            "sample_count": results.get("sample_count", 0),
            "brier": results.get("brier"),
            "logloss": results.get("logloss"),
            "hit_rate": results.get("hit_rate"),
            "available": results.get("sample_count", 0) > 0,
        })

    # Ensemble — available is based on sample_count, not the formatted dict
    ensemble_sample_count = ensemble_score.get("sample_count", 0)
    comparison.append({
        "source": "Ensemble",
        "model_version": "ensemble-v1",
        "prompt_version": None,
        "role": "production",
        "sample_count": ensemble_sample_count,
        "brier": ensemble_score.get("brier"),
        "logloss": ensemble_score.get("logloss"),
        "hit_rate": ensemble_score.get("hit_rate"),
        "available": ensemble_sample_count > 0,
    })

    return comparison


def _get_upcoming_matches(session: Session, limit: int = 10) -> list[dict[str, Any]]:
    """Get upcoming matches that need predictions."""
    now = datetime.now(timezone.utc)
    team_names = {row.id: row.short_name for row in session.scalars(select(Team))}

    rows = session.scalars(
        select(Match)
        .where(Match.status != "final")
        .where(Match.kickoff > now)
        .order_by(Match.kickoff)
        .limit(limit)
    ).all()
    match_ids = [match.id for match in rows]
    ai_match_ids = set(
        row[0]
        for row in session.execute(
            select(AIPrediction.match_id)
            .where(AIPrediction.match_id.in_(match_ids))
            .where(AIPrediction.error_code.is_(None))
            .where(AIPrediction.parsed_home_win.isnot(None))
        )
    ) if match_ids else set()

    results = []
    for match in rows:
        results.append({
            "match_id": match.id,
            "home_team": team_names.get(match.home_team_id, match.home_team_id),
            "away_team": team_names.get(match.away_team_id, match.away_team_id),
            "kickoff": match.kickoff.isoformat() if match.kickoff else "",
            "stage": match.stage,
            "has_ai_prediction": match.id in ai_match_ids,
        })
    return results
