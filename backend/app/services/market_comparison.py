"""Market comparison analysis.

Compares model predictions against market (Sporttery) implied probabilities
to determine which is more accurate and whether blending helps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MarketSnapshot, Match, PredictionSnapshot


_CLIP = 1e-6


@dataclass(frozen=True)
class MarketComparisonResult:
    match_id: str
    model_brier: float
    market_brier: float
    blended_brier: float
    model_logloss: float
    market_logloss: float
    blended_logloss: float
    market_effect: str  # helped | hurt | neutral
    model_home: float
    model_draw: float
    model_away: float
    market_home: float
    market_draw: float
    market_away: float
    blended_home: float
    blended_draw: float
    blended_away: float
    actual_result: str


def compute_market_comparison(
    session: Session,
    blend_weight: float = 0.15,
) -> dict[str, Any]:
    """Compare model, market, and blended predictions for all scored matches with market data."""
    # Get locked snapshots for final matches
    rows = session.execute(
        select(PredictionSnapshot, Match, MarketSnapshot)
        .join(Match, PredictionSnapshot.match_id == Match.id)
        .join(MarketSnapshot, MarketSnapshot.match_id == Match.id)
        .where(Match.status == "final")
        .where(PredictionSnapshot.is_pre_match_locked.is_(True))
        .where(MarketSnapshot.provider == "sporttery")
    ).all()

    if not rows:
        return {
            "market_sample_count": 0,
            "model_brier": 0.0,
            "market_brier": 0.0,
            "blended_brier": 0.0,
            "model_logloss": 0.0,
            "market_logloss": 0.0,
            "blended_logloss": 0.0,
            "suggested_market_blend_weight": 0.0,
            "per_match": [],
        }

    comparisons: list[MarketComparisonResult] = []
    for snap, match, market in rows:
        actual_home = match.home_score or 0
        actual_away = match.away_score or 0

        if actual_home > actual_away:
            o_home, o_draw, o_away = 1.0, 0.0, 0.0
            actual_result = "home"
        elif actual_home == actual_away:
            o_home, o_draw, o_away = 0.0, 1.0, 0.0
            actual_result = "draw"
        else:
            o_home, o_draw, o_away = 0.0, 0.0, 1.0
            actual_result = "away"

        # Model probs
        m_home, m_draw, m_away = snap.home_win, snap.draw, snap.away_win
        # Market probs
        k_home, k_draw, k_away = market.home_probability, market.draw_probability, market.away_probability
        # Blended probs
        b_home = (1 - blend_weight) * m_home + blend_weight * k_home
        b_draw = (1 - blend_weight) * m_draw + blend_weight * k_draw
        b_away = (1 - blend_weight) * m_away + blend_weight * k_away
        b_total = b_home + b_draw + b_away
        b_home, b_draw, b_away = b_home / b_total, b_draw / b_total, b_away / b_total

        # Brier scores
        model_brier = (m_home - o_home) ** 2 + (m_draw - o_draw) ** 2 + (m_away - o_away) ** 2
        market_brier = (k_home - o_home) ** 2 + (k_draw - o_draw) ** 2 + (k_away - o_away) ** 2
        blended_brier = (b_home - o_home) ** 2 + (b_draw - o_draw) ** 2 + (b_away - o_away) ** 2

        # LogLoss
        def _logloss(p_h, p_d, p_a):
            ch = max(_CLIP, min(1 - _CLIP, p_h))
            cd = max(_CLIP, min(1 - _CLIP, p_d))
            ca = max(_CLIP, min(1 - _CLIP, p_a))
            return -(o_home * math.log(ch) + o_draw * math.log(cd) + o_away * math.log(ca))

        model_ll = _logloss(m_home, m_draw, m_away)
        market_ll = _logloss(k_home, k_draw, k_away)
        blended_ll = _logloss(b_home, b_draw, b_away)

        # Market effect
        if blended_brier < model_brier - 0.005:
            market_effect = "helped"
        elif blended_brier > model_brier + 0.005:
            market_effect = "hurt"
        else:
            market_effect = "neutral"

        comparisons.append(MarketComparisonResult(
            match_id=match.id,
            model_brier=model_brier,
            market_brier=market_brier,
            blended_brier=blended_brier,
            model_logloss=model_ll,
            market_logloss=market_ll,
            blended_logloss=blended_ll,
            market_effect=market_effect,
            model_home=m_home, model_draw=m_draw, model_away=m_away,
            market_home=k_home, market_draw=k_draw, market_away=k_away,
            blended_home=b_home, blended_draw=b_draw, blended_away=b_away,
            actual_result=actual_result,
        ))

    n = len(comparisons)
    avg_model_brier = sum(c.model_brier for c in comparisons) / n
    avg_market_brier = sum(c.market_brier for c in comparisons) / n
    avg_blended_brier = sum(c.blended_brier for c in comparisons) / n
    avg_model_ll = sum(c.model_logloss for c in comparisons) / n
    avg_market_ll = sum(c.market_logloss for c in comparisons) / n
    avg_blended_ll = sum(c.blended_logloss for c in comparisons) / n

    # Find best blend weight via grid search
    best_weight = 0.0
    best_brier = avg_model_brier
    for w in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]:
        total_brier = 0.0
        for c in comparisons:
            bh = (1 - w) * c.model_home + w * c.market_home
            bd = (1 - w) * c.model_draw + w * c.market_draw
            ba = (1 - w) * c.model_away + w * c.market_away
            bt = bh + bd + ba
            bh, bd, ba = bh / bt, bd / bt, ba / bt
            actual_home = 1.0 if c.actual_result == "home" else 0.0
            actual_draw = 1.0 if c.actual_result == "draw" else 0.0
            actual_away = 1.0 if c.actual_result == "away" else 0.0
            brier = (bh - actual_home) ** 2 + (bd - actual_draw) ** 2 + (ba - actual_away) ** 2
            total_brier += brier
        avg_brier = total_brier / n
        if avg_brier < best_brier:
            best_brier = avg_brier
            best_weight = w

    return {
        "market_sample_count": n,
        "model_brier": round(avg_model_brier, 4),
        "market_brier": round(avg_market_brier, 4),
        "blended_brier": round(avg_blended_brier, 4),
        "model_logloss": round(avg_model_ll, 4),
        "market_logloss": round(avg_market_ll, 4),
        "blended_logloss": round(avg_blended_ll, 4),
        "suggested_market_blend_weight": round(best_weight, 2),
        "market_helped_count": sum(1 for c in comparisons if c.market_effect == "helped"),
        "market_hurt_count": sum(1 for c in comparisons if c.market_effect == "hurt"),
        "market_neutral_count": sum(1 for c in comparisons if c.market_effect == "neutral"),
        "per_match": [
            {
                "match_id": c.match_id,
                "model_brier": round(c.model_brier, 4),
                "market_brier": round(c.market_brier, 4),
                "blended_brier": round(c.blended_brier, 4),
                "market_effect": c.market_effect,
                "actual_result": c.actual_result,
            }
            for c in comparisons
        ],
    }
