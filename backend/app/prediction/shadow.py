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


# --- Registry ---

SHADOW_MODELS = {
    "elo-poisson-v1-drawboost-105-shadow": draw_boost_105,
    "elo-poisson-v1-drawboost-110-shadow": draw_boost_110,
    "elo-poisson-v1-favorite-dampened-shadow": favorite_dampened,
    "elo-poisson-v1-low-score-shadow": low_score,
}

SHADOW_MODEL_VERSIONS = list(SHADOW_MODELS.keys())


def compute_shadow_predictions(
    home_win: float, draw: float, away_win: float,
    home_xg: float, away_xg: float,
) -> list[ShadowPrediction]:
    """Compute all shadow model predictions from baseline."""
    return [
        func(home_win, draw, away_win, home_xg, away_xg)
        for func in SHADOW_MODELS.values()
    ]
