from dataclasses import dataclass, replace
from math import isfinite
from typing import Any

import numpy as np
from scipy.stats import poisson

from app.prediction.confidence import (
    ConfidenceInputs,
    data_confidence,
    model_confidence,
)
from app.prediction.explanation import explain_prediction


MODEL_VERSION = "elo-poisson-v1"
_MAX_EXACT_GOALS = 7


@dataclass(frozen=True)
class MatchContext:
    data_freshness: float
    ranking_coverage: float
    history_coverage: float
    provider_agreement: float
    recent_form_delta: float = 0.0
    host_advantage: float = 0.0
    home_attack_adjustment: float = 0.0
    home_defense_adjustment: float = 0.0
    away_attack_adjustment: float = 0.0
    away_defense_adjustment: float = 0.0
    home_name: str = "主队"
    away_name: str = "客队"
    market_probs: dict[str, float] | None = None
    # --- Research-enhanced fields ---
    fifa_rank_delta: float = 0.0        # FIFA ranking difference (home - away), negative = home ranked higher
    is_group_stage: bool = True          # Group stage has higher draw rate
    elo_closeness: float = 0.0           # 1 - |home_strength - away_strength|, higher = closer match


@dataclass(frozen=True)
class ScorelineProbability:
    home_goals: int
    away_goals: int
    probability: float


@dataclass(frozen=True)
class MatchPredictionResult:
    home_xg: float
    away_xg: float
    home_win: float
    draw: float
    away_win: float
    scorelines: list[ScorelineProbability]
    score_matrix: list[list[float]]
    confidence: float
    confidence_label: str
    data_confidence: float
    data_confidence_label: str
    model_confidence: float
    model_confidence_label: str
    explanation: str
    model_version: str


def predict_match(
    home_strength: float,
    away_strength: float,
    context: MatchContext,
    config: Any | None = None,
) -> MatchPredictionResult:
    """Predict a match outcome using Elo + Poisson model.

    If a ModelConfig is provided, uses its parameters instead of hardcoded values.
    The config object is expected to have attributes like:
      elo_scale, base_goal_mean_home, base_goal_mean_away, strength_coeff_home,
      strength_coeff_away, draw_boost, favorite_dampening, underdog_boost,
      min_xg, max_xg, market_blend_weight, upset_factor
    """
    if not isfinite(home_strength) or not isfinite(away_strength):
        raise ValueError("team strengths must be finite")

    # Extract config parameters with defaults
    base_goal_home = getattr(config, 'base_goal_mean_home', 1.25)
    base_goal_away = getattr(config, 'base_goal_mean_away', 1.10)
    str_coeff_home = getattr(config, 'strength_coeff_home', 0.90)
    str_coeff_away = getattr(config, 'strength_coeff_away', 0.75)
    min_xg = getattr(config, 'min_xg', 0.20)
    max_xg = getattr(config, 'max_xg', 3.50)
    draw_boost = getattr(config, 'draw_boost', 1.00)
    favorite_dampening = getattr(config, 'favorite_dampening', 0.00)
    underdog_boost = getattr(config, 'underdog_boost', 0.00)
    market_blend_weight = getattr(config, 'market_blend_weight', 0.00)
    upset_factor = getattr(config, 'upset_factor', 0.00)

    # FIFA rank delta adjustment:
    # fifa_rank_delta = home_fifa_rank - away_fifa_rank (negative = home ranked higher)
    # Research shows IC=0.472, equivalent to elo_diff. Blend as supplementary signal.
    # Normalize: rank difference of ~40 ≈ Elo strength_delta of ~0.2 (moderate gap)
    # Weight: 15% of Elo signal (Elo is still primary, FIFA rank is supplementary)
    fifa_rank_adjustment = 0.0
    if context.fifa_rank_delta != 0.0:
        # Negative delta means home is ranked higher → positive adjustment for home
        fifa_rank_adjustment = -context.fifa_rank_delta / 40.0 * 0.2 * 0.15

    strength_delta = (
        home_strength
        - away_strength
        + context.recent_form_delta
        + context.host_advantage
        + fifa_rank_adjustment
    )
    home_xg = float(
        np.clip(
            base_goal_home
            + str_coeff_home * strength_delta
            + context.home_attack_adjustment
            - context.away_defense_adjustment,
            min_xg,
            max_xg,
        )
    )
    away_xg = float(
        np.clip(
            base_goal_away
            - str_coeff_away * strength_delta
            + context.away_attack_adjustment
            - context.home_defense_adjustment,
            min_xg,
            max_xg,
        )
    )
    home_goals = _goal_probabilities(home_xg)
    away_goals = _goal_probabilities(away_xg)
    matrix = np.outer(home_goals, away_goals)

    home_win = float(np.tril(matrix, k=-1).sum())
    draw = float(np.trace(matrix))
    away_win = float(np.triu(matrix, k=1).sum())
    total = home_win + draw + away_win
    home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Apply draw_boost
    if draw_boost != 1.0 and draw > 0:
        draw_boosted = draw * draw_boost
        excess = draw_boosted - draw
        # Take proportionally from home and away
        home_win -= excess * (home_win / (home_win + away_win)) if (home_win + away_win) > 0 else 0
        away_win -= excess * (away_win / (home_win + away_win)) if (home_win + away_win) > 0 else 0
        draw = draw_boosted
        # Renormalize
        total = home_win + draw + away_win
        home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # --- Research-enhanced: dynamic draw calibration ---
    # When teams are closely matched (elo_closeness > 0.85) or market signals high draw,
    # apply additional draw boost beyond the static draw_boost parameter.
    # This is based on findings that draw rate in WC group stage is ~37.5% vs model's ~20%.
    dynamic_draw_boost = getattr(config, 'dynamic_draw_boost', True)
    if dynamic_draw_boost and draw > 0:
        extra_draw = 0.0

        # Factor 1: Elo closeness — when teams are close, draws are more likely
        if context.elo_closeness > 0.85:
            extra_draw += 0.02 * (context.elo_closeness - 0.85) / 0.15  # 0-2% extra

        # Factor 2: Market signals — if market draw prob > model draw prob, trust market
        if context.market_probs and "draw" in context.market_probs:
            market_draw = context.market_probs["draw"]
            if market_draw > draw and market_draw > 0.25:
                # Market sees more draw than model — add half the gap
                extra_draw += (market_draw - draw) * 0.3  # 30% of gap

        # Factor 3: Group stage bonus — group stage has higher draw rate
        if context.is_group_stage:
            extra_draw += 0.015  # 1.5% extra for group stage

        # Cap total extra draw boost at 8 percentage points
        extra_draw = min(extra_draw, 0.08)

        if extra_draw > 0:
            draw += extra_draw
            # Reduce proportionally from win probabilities
            win_total = home_win + away_win
            if win_total > 0:
                home_win -= extra_draw * (home_win / win_total)
                away_win -= extra_draw * (away_win / win_total)
            total = home_win + draw + away_win
            home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Apply favorite_dampening: reduce the gap between max prob and 1/3
    if favorite_dampening > 0:
        probs = [home_win, draw, away_win]
        max_idx = probs.index(max(probs))
        uniform = 1.0 / 3.0
        excess = probs[max_idx] - uniform
        if excess > 0:
            reduction = excess * favorite_dampening
            probs[max_idx] -= reduction
            # Distribute reduction to other outcomes proportionally
            others = [i for i in range(3) if i != max_idx]
            other_sum = sum(probs[i] for i in others)
            if other_sum > 0:
                for i in others:
                    probs[i] += reduction * (probs[i] / other_sum)
            else:
                for i in others:
                    probs[i] += reduction / len(others)
            home_win, draw, away_win = probs
            total = home_win + draw + away_win
            home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Apply underdog_boost: add probability to the weaker side
    if underdog_boost > 0:
        if home_win <= away_win:
            home_win += underdog_boost
        else:
            away_win += underdog_boost
        total = home_win + draw + away_win
        home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Apply upset_factor: add to underdog tail probability
    if upset_factor > 0:
        if home_win <= away_win:
            home_win += upset_factor
        else:
            away_win += upset_factor
        total = home_win + draw + away_win
        home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Market blend: if market_blend_weight > 0 and market data available in context
    if market_blend_weight > 0 and context.market_probs is not None:
        # Research-enhanced: adaptive market blend weight
        # When market and model disagree significantly, increase market weight
        # because odds data has proven 8.7% Brier improvement over pure Elo+Poisson
        adaptive_weight = market_blend_weight
        smart_blend = getattr(config, 'smart_market_blend', True)
        if smart_blend:
            model_pred = max(home_win, draw, away_win)
            market_pred = max(
                context.market_probs.get("home_win", 0),
                context.market_probs.get("draw", 0),
                context.market_probs.get("away_win", 0),
            )
            # If market and model disagree on direction, increase market weight by 50%
            model_dir = np.argmax([home_win, draw, away_win])
            market_dir = np.argmax([
                context.market_probs.get("home_win", 0),
                context.market_probs.get("draw", 0),
                context.market_probs.get("away_win", 0),
            ])
            if model_dir != market_dir:
                adaptive_weight = min(market_blend_weight * 1.5, 0.50)

        blended = blend_with_market(
            {"home_win": home_win, "draw": draw, "away_win": away_win},
            context.market_probs,
            adaptive_weight,
        )
        home_win = blended["home_win"]
        draw = blended["draw"]
        away_win = blended["away_win"]

    exact_scores = [
        ScorelineProbability(home, away, float(matrix[home, away]))
        for home in range(_MAX_EXACT_GOALS + 1)
        for away in range(_MAX_EXACT_GOALS + 1)
    ]
    scorelines = sorted(exact_scores, key=lambda item: item.probability, reverse=True)[:3]
    d_conf, d_label = data_confidence(
        ConfidenceInputs(
            data_freshness=context.data_freshness,
            ranking_coverage=context.ranking_coverage,
            history_coverage=context.history_coverage,
            provider_agreement=context.provider_agreement,
        )
    )
    m_conf, m_label = model_confidence(home_win, draw, away_win)

    # Determine model version
    model_ver = MODEL_VERSION
    if config is not None:
        model_ver = getattr(config, 'name', MODEL_VERSION)

    return MatchPredictionResult(
        home_xg=home_xg,
        away_xg=away_xg,
        home_win=home_win,
        draw=draw,
        away_win=away_win,
        scorelines=scorelines,
        score_matrix=matrix.tolist(),
        confidence=d_conf,
        confidence_label=d_label,
        data_confidence=d_conf,
        data_confidence_label=d_label,
        model_confidence=m_conf,
        model_confidence_label=m_label,
        explanation=explain_prediction(
            context.home_name,
            context.away_name,
            home_win,
            draw,
            away_win,
            strength_delta,
        ),
        model_version=model_ver,
    )


def blend_with_market(
    model_probs: dict[str, float],
    market_probs: dict[str, float],
    weight: float,
) -> dict[str, float]:
    """Blend model probabilities with market probabilities.

    weight: 0 = pure model, 1 = pure market, 0.15 = 85% model + 15% market
    """
    blended = {}
    for key in ("home_win", "draw", "away_win"):
        m = model_probs.get(key, 1.0 / 3)
        k = market_probs.get(key, 1.0 / 3)
        blended[key] = (1 - weight) * m + weight * k
    # Renormalize
    total = sum(blended.values())
    if total > 0:
        for key in blended:
            blended[key] /= total
    return blended


def _goal_probabilities(expected_goals: float) -> np.ndarray:
    exact = poisson.pmf(np.arange(_MAX_EXACT_GOALS + 1), expected_goals)
    tail = max(0.0, 1.0 - float(exact.sum()))
    values = np.append(exact, tail)
    return values / values.sum()
