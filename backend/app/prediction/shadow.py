"""Shadow model variants for parallel evaluation.

These models run alongside the baseline but do not affect default predictions.
They are only visible in the model review page for comparison.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ShadowPrediction:
    model_version: str
    home_win: float
    draw: float
    away_win: float
    home_xg: float
    away_xg: float
    label: str  # human-readable label for frontend


# --- draw_boost-105 shadow ---

def draw_boost_105(
    home_win: float, draw: float, away_win: float,
    home_xg: float, away_xg: float,
) -> ShadowPrediction:
    """Apply draw_boost=1.05 to baseline.

    Rules:
    - Multiply draw probability by 1.05
    - Reduce favorite win probability proportionally
    - Re-normalize to sum=1
    - Single match draw_prob increase capped at 5 percentage points
    - If draw is already >= 35%, don't boost further
    """
    original_draw = draw
    new_draw = draw * 1.05

    # Cap: draw increase cannot exceed 5 percentage points
    if new_draw - original_draw > 0.05:
        new_draw = original_draw + 0.05

    # Don't boost if draw is already high
    if original_draw >= 0.35:
        new_draw = original_draw

    # Reduce favorite proportionally
    if home_win >= away_win:
        reduction = new_draw - original_draw
        new_home = home_win - reduction * (home_win / (home_win + away_win))
        new_away = away_win - reduction * (away_win / (home_win + away_win))
    else:
        reduction = new_draw - original_draw
        new_away = away_win - reduction * (away_win / (home_win + away_win))
        new_home = home_win - reduction * (home_win / (home_win + away_win))

    # Re-normalize
    total = new_home + new_draw + new_away
    new_home /= total
    new_draw /= total
    new_away /= total

    return ShadowPrediction(
        model_version="elo-poisson-v1-drawboost-105-shadow",
        home_win=new_home,
        draw=new_draw,
        away_win=new_away,
        home_xg=home_xg,
        away_xg=away_xg,
        label="Draw Boost 1.05",
    )


# --- draw_boost-110 shadow ---

def draw_boost_110(
    home_win: float, draw: float, away_win: float,
    home_xg: float, away_xg: float,
) -> ShadowPrediction:
    """Apply draw_boost=1.10 to baseline.

    Same rules as draw_boost_105 but with 1.10 multiplier.
    """
    original_draw = draw
    new_draw = draw * 1.10

    # Cap: draw increase cannot exceed 5 percentage points
    if new_draw - original_draw > 0.05:
        new_draw = original_draw + 0.05

    # Don't boost if draw is already high
    if original_draw >= 0.35:
        new_draw = original_draw

    # Reduce favorite proportionally
    if home_win >= away_win:
        reduction = new_draw - original_draw
        new_home = home_win - reduction * (home_win / (home_win + away_win))
        new_away = away_win - reduction * (away_win / (home_win + away_win))
    else:
        reduction = new_draw - original_draw
        new_away = away_win - reduction * (away_win / (home_win + away_win))
        new_home = home_win - reduction * (home_win / (home_win + away_win))

    # Re-normalize
    total = new_home + new_draw + new_away
    new_home /= total
    new_draw /= total
    new_away /= total

    return ShadowPrediction(
        model_version="elo-poisson-v1-drawboost-110-shadow",
        home_win=new_home,
        draw=new_draw,
        away_win=new_away,
        home_xg=home_xg,
        away_xg=away_xg,
        label="Draw Boost 1.10",
    )


# --- favorite-dampened shadow ---

def favorite_dampened(
    home_win: float, draw: float, away_win: float,
    home_xg: float, away_xg: float,
) -> ShadowPrediction:
    """Dampen favorite when max_prob is 45-60% and draw >= 25%.

    Rules:
    - When max(home_win, away_win) is between 45% and 60% AND draw >= 25%:
      - Reduce favorite by 2%-4% (proportional to how close to 60%)
      - Increase draw by 1%-3%
      - Increase underdog by 0%-1%
    - Re-normalize
    - Otherwise: return baseline unchanged
    """
    max_prob = max(home_win, away_win)

    if not (0.45 <= max_prob <= 0.60 and draw >= 0.25):
        return ShadowPrediction(
            model_version="elo-poisson-v1-favorite-dampened-shadow",
            home_win=home_win,
            draw=draw,
            away_win=away_win,
            home_xg=home_xg,
            away_xg=away_xg,
            label="Favorite Dampened",
        )

    # Proportional dampening: closer to 60% -> more dampening
    ratio = (max_prob - 0.45) / (0.60 - 0.45)  # 0 to 1
    favorite_reduction = 0.02 + 0.02 * ratio  # 2% to 4%
    draw_increase = 0.01 + 0.02 * ratio  # 1% to 3%
    underdog_increase = favorite_reduction - draw_increase  # 0% to 1%

    if home_win >= away_win:
        new_home = home_win - favorite_reduction
        new_draw = draw + draw_increase
        new_away = away_win + underdog_increase
    else:
        new_away = away_win - favorite_reduction
        new_draw = draw + draw_increase
        new_home = home_win + underdog_increase

    # Re-normalize
    total = new_home + new_draw + new_away
    new_home /= total
    new_draw /= total
    new_away /= total

    return ShadowPrediction(
        model_version="elo-poisson-v1-favorite-dampened-shadow",
        home_win=new_home,
        draw=new_draw,
        away_win=new_away,
        home_xg=home_xg,
        away_xg=away_xg,
        label="Favorite Dampened",
    )


# --- low-score shadow ---

def low_score(
    home_win: float, draw: float, away_win: float,
    home_xg: float, away_xg: float,
) -> ShadowPrediction:
    """Boost low-score outcomes when total xG < 2.4.

    Rules:
    - When home_xg + away_xg < 2.4:
      - Slightly increase draw probability (multiply by 1.03)
      - Draw increase capped at 3 percentage points
      - Reduce both win probabilities proportionally
    - Otherwise: return baseline unchanged
    """
    total_xg = home_xg + away_xg

    if total_xg >= 2.4:
        return ShadowPrediction(
            model_version="elo-poisson-v1-low-score-shadow",
            home_win=home_win,
            draw=draw,
            away_win=away_win,
            home_xg=home_xg,
            away_xg=away_xg,
            label="Low Score",
        )

    original_draw = draw
    new_draw = draw * 1.03

    # Cap at 3 percentage points
    if new_draw - original_draw > 0.03:
        new_draw = original_draw + 0.03

    # Reduce both win probabilities proportionally
    reduction = new_draw - original_draw
    win_total = home_win + away_win
    if win_total > 0:
        new_home = home_win - reduction * (home_win / win_total)
        new_away = away_win - reduction * (away_win / win_total)
    else:
        new_home = home_win
        new_away = away_win

    # Re-normalize
    total = new_home + new_draw + new_away
    new_home /= total
    new_draw /= total
    new_away /= total

    return ShadowPrediction(
        model_version="elo-poisson-v1-low-score-shadow",
        home_win=new_home,
        draw=new_draw,
        away_win=new_away,
        home_xg=home_xg,
        away_xg=away_xg,
        label="Low Score",
    )


# --- research-draw-boost shadow ---
# Based on factor research: WC group stage draw rate ~37.5% vs model ~20%

def research_draw_boost(
    home_win: float, draw: float, away_win: float,
    home_xg: float, away_xg: float,
) -> ShadowPrediction:
    """Research-calibrated draw boost for group stage.

    Based on analysis of 24 WC2026 matches showing 37.5% draw rate
    vs model's ~20% prediction. Applies a stronger draw boost (1.15)
    with a 8pp cap, targeting the observed gap.
    """
    original_draw = draw
    new_draw = draw * 1.15

    # Cap: draw increase cannot exceed 8 percentage points
    if new_draw - original_draw > 0.08:
        new_draw = original_draw + 0.08

    # Don't boost if draw is already very high
    if original_draw >= 0.40:
        new_draw = original_draw

    # Reduce favorite proportionally
    if home_win >= away_win:
        reduction = new_draw - original_draw
        new_home = home_win - reduction * (home_win / (home_win + away_win)) if (home_win + away_win) > 0 else home_win
        new_away = away_win - reduction * (away_win / (home_win + away_win)) if (home_win + away_win) > 0 else away_win
    else:
        reduction = new_draw - original_draw
        new_away = away_win - reduction * (away_win / (home_win + away_win)) if (home_win + away_win) > 0 else away_win
        new_home = home_win - reduction * (home_win / (home_win + away_win)) if (home_win + away_win) > 0 else home_win

    # Re-normalize
    total = new_home + new_draw + new_away
    new_home /= total
    new_draw /= total
    new_away /= total

    return ShadowPrediction(
        model_version="elo-poisson-v2-research-draw-shadow",
        home_win=new_home,
        draw=new_draw,
        away_win=new_away,
        home_xg=home_xg,
        away_xg=away_xg,
        label="Research Draw+15%",
    )


# --- market-heavy shadow ---
# Based on odds research: market odds 8.7% Brier improvement over Elo+Poisson

def market_heavy(
    home_win: float, draw: float, away_win: float,
    home_xg: float, away_xg: float,
    market_probs: dict[str, float] | None = None,
) -> ShadowPrediction:
    """Market-heavy blend: 40% model + 60% market.

    Based on research showing odds-implied probabilities significantly
    outperform pure Elo+Poisson. Only active when market data is available.
    """
    if market_probs is None:
        return ShadowPrediction(
            model_version="elo-poisson-v2-market-heavy-shadow",
            home_win=home_win,
            draw=draw,
            away_win=away_win,
            home_xg=home_xg,
            away_xg=away_xg,
            label="Market Heavy (no data)",
        )

    model_probs = {"home_win": home_win, "draw": draw, "away_win": away_win}
    weight = 0.60  # 60% market
    blended = {}
    for key in ("home_win", "draw", "away_win"):
        m = model_probs.get(key, 1.0 / 3)
        k = market_probs.get(key, 1.0 / 3)
        blended[key] = (1 - weight) * m + weight * k

    total = sum(blended.values())
    if total > 0:
        for key in blended:
            blended[key] /= total

    return ShadowPrediction(
        model_version="elo-poisson-v2-market-heavy-shadow",
        home_win=blended["home_win"],
        draw=blended["draw"],
        away_win=blended["away_win"],
        home_xg=home_xg,
        away_xg=away_xg,
        label="Market Heavy 60%",
    )


# --- Registry ---

SHADOW_MODELS = {
    "elo-poisson-v1-drawboost-105-shadow": draw_boost_105,
    "elo-poisson-v1-drawboost-110-shadow": draw_boost_110,
    "elo-poisson-v1-favorite-dampened-shadow": favorite_dampened,
    "elo-poisson-v1-low-score-shadow": low_score,
    "elo-poisson-v2-research-draw-shadow": research_draw_boost,
    "elo-poisson-v2-market-heavy-shadow": market_heavy,
}

SHADOW_MODEL_VERSIONS = list(SHADOW_MODELS.keys())


def compute_shadow_predictions(
    home_win: float, draw: float, away_win: float,
    home_xg: float, away_xg: float,
    market_probs: dict[str, float] | None = None,
) -> list[ShadowPrediction]:
    """Compute all shadow model predictions from baseline."""
    results = []
    for name, func in SHADOW_MODELS.items():
        if name == "elo-poisson-v2-market-heavy-shadow":
            results.append(func(home_win, draw, away_win, home_xg, away_xg, market_probs=market_probs))
        else:
            results.append(func(home_win, draw, away_win, home_xg, away_xg))
    return results
