"""Shadow model promotion — auto-promote shadow models that consistently
outperform the baseline.

Evaluates shadow model Brier scores against the baseline after each
recompute cycle. If a shadow model is significantly better (lower Brier
by a meaningful margin) with enough samples, its parameters are promoted
into the calibration feedback file so the next recompute adopts them.

Safety:
  - Requires ``MIN_SAMPLES`` scored matches before evaluating.
  - Requires ``MIN_BRIER_IMPROVEMENT`` improvement to promote.
  - Only promotes one shadow per cycle (the best candidate).
  - Promoted parameters are written to ``data/shadow_promotion.json``
    and logged for auditability.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, MatchPrediction

logger = logging.getLogger(__name__)

_PROMOTION_PATH = Path("data/shadow_promotion.json")
_MIN_SAMPLES = 10
_MIN_BRIER_IMPROVEMENT = 0.005  # 0.5% Brier improvement to justify promotion

# Map shadow model_version → parameter overrides to apply if promoted.
# These are the parameter changes that would make the baseline behave
# like the shadow model.
_SHADOW_PARAMS: dict[str, dict[str, float]] = {
    "elo-poisson-v1-drawboost-105-shadow": {"dynamic_draw_cap_group": 0.01},
    "elo-poisson-v1-drawboost-110-shadow": {"dynamic_draw_cap_group": 0.02},
    "elo-poisson-v1-favorite-dampened-shadow": {"favorite_dampening": 0.08},
    "elo-poisson-v1-low-score-shadow": {},  # no direct param mapping
    "elo-poisson-v2-research-draw-shadow": {"dynamic_draw_cap_group": 0.05},
    "elo-poisson-v2-market-heavy-shadow": {"market_blend_weight": 0.25},
}

BASELINE_VERSION_PREFIX = "elo-poisson-v1"


def _brier(pred: dict[str, float], actual: str) -> float:
    """Compute Brier score for a single match prediction."""
    actual_home = 1.0 if actual == "home" else 0.0
    actual_draw = 1.0 if actual == "draw" else 0.0
    actual_away = 1.0 if actual == "away" else 0.0
    return (
        (pred["home_win"] - actual_home) ** 2
        + (pred["draw"] - actual_draw) ** 2
        + (pred["away_win"] - actual_away) ** 2
    ) / 3.0


def _resolve_actual(match: Match) -> str:
    """Resolve actual match outcome (home/draw/away)."""
    h = match.home_score or 0
    a = match.away_score or 0
    if h > a:
        return "home"
    if h < a:
        return "away"
    # Level scores — knockout matches resolve via advance flags
    if match.stage and match.stage != "group":
        if getattr(match, "home_advance", None) is True:
            return "home"
        if getattr(match, "away_advance", None) is True:
            return "away"
    return "draw"


def evaluate_shadow_promotion(session: Session) -> dict[str, Any]:
    """Evaluate all shadow models against the baseline.

    Returns a report dict with per-shadow Brier comparison and any
    promoted parameter overrides. Persists promotions to disk.
    """
    # Load all final matches with their predictions
    final_matches = list(session.scalars(
        select(Match).where(Match.status == "final").where(Match.home_score.is_not(None))
    ))
    if len(final_matches) < _MIN_SAMPLES:
        return {"status": "insufficient_samples", "matches": len(final_matches)}

    match_ids = {m.id for m in final_matches}
    preds = list(session.scalars(
        select(MatchPrediction).where(MatchPrediction.match_id.in_(match_ids))
    ))

    # Group predictions by model_version
    by_version: dict[str, list[tuple[MatchPrediction, Match]]] = {}
    match_by_id = {m.id: m for m in final_matches}
    for p in preds:
        match = match_by_id.get(p.match_id)
        if match and p.home_win is not None and p.draw is not None and p.away_win is not None:
            by_version.setdefault(p.model_version, []).append((p, match))

    # Identify baseline (non-shadow) versions
    baseline_versions = [
        v for v in by_version
        if "shadow" not in v and v.startswith(BASELINE_VERSION_PREFIX)
    ]
    if not baseline_versions:
        return {"status": "no_baseline"}

    # Use the most common baseline version
    baseline_version = max(baseline_versions, key=lambda v: len(by_version[v]))
    baseline_preds = by_version[baseline_version]

    # Compute baseline Brier
    baseline_brier = sum(
        _brier({"home_win": p.home_win, "draw": p.draw, "away_win": p.away_win}, _resolve_actual(m))
        for p, m in baseline_preds
    ) / len(baseline_preds)

    # Evaluate each shadow model
    shadow_results = []
    best_candidate = None
    best_improvement = 0.0

    for version, pred_pairs in by_version.items():
        if "shadow" not in version:
            continue
        if len(pred_pairs) < _MIN_SAMPLES:
            continue

        shadow_brier = sum(
            _brier({"home_win": p.home_win, "draw": p.draw, "away_win": p.away_win}, _resolve_actual(m))
            for p, m in pred_pairs
        ) / len(pred_pairs)

        improvement = baseline_brier - shadow_brier
        result = {
            "model_version": version,
            "sample_count": len(pred_pairs),
            "brier": round(shadow_brier, 6),
            "baseline_brier": round(baseline_brier, 6),
            "improvement": round(improvement, 6),
            "promoted": False,
        }
        shadow_results.append(result)

        if improvement > _MIN_BRIER_IMPROVEMENT and improvement > best_improvement:
            best_improvement = improvement
            best_candidate = version

    # Promote the best candidate
    if best_candidate and best_candidate in _SHADOW_PARAMS:
        params = _SHADOW_PARAMS[best_candidate]
        if params:
            _save_promotion(best_candidate, params)
            for r in shadow_results:
                if r["model_version"] == best_candidate:
                    r["promoted"] = True
            logger.info(
                "Shadow model promoted: %s (improvement=%.4f, params=%s)",
                best_candidate, best_improvement, params,
            )

    return {
        "status": "evaluated",
        "baseline_version": baseline_version,
        "baseline_brier": round(baseline_brier, 6),
        "baseline_samples": len(baseline_preds),
        "shadows": shadow_results,
        "promoted": best_candidate if best_candidate and best_candidate in _SHADOW_PARAMS else None,
    }


def _save_promotion(model_version: str, params: dict[str, float]) -> None:
    """Persist promoted shadow parameters to disk."""
    try:
        _PROMOTION_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PROMOTION_PATH.write_text(json.dumps({
            "promoted_model": model_version,
            "params": params,
        }, indent=2))
    except Exception as exc:
        logger.warning("Failed to persist shadow promotion: %s", exc)


def get_promoted_params() -> dict[str, float]:
    """Read persisted shadow promotion parameters."""
    if not _PROMOTION_PATH.exists():
        return {}
    try:
        data = json.loads(_PROMOTION_PATH.read_text())
        return {k: float(v) for k, v in data.get("params", {}).items()}
    except Exception:
        return {}
