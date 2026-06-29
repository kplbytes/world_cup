from dataclasses import dataclass, replace
from math import isfinite
from typing import Any

import numpy as np
from scipy.stats import poisson

from app.prediction.confidence import (
    ConfidenceInputs,
    data_confidence,
    model_confidence,
    overall_confidence,
)
from app.prediction.explanation import explain_prediction


MODEL_VERSION = "elo-poisson-v1"
_MAX_EXACT_GOALS = 10


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
    # Knockout stage flag. When True the model knows the match MUST produce
    # a winner (extra time + penalties if level after 90 min). The 90-minute
    # probabilities are still emitted on home_win/draw/away_win (draw is a
    # valid 90-minute outcome), but advance probabilities are also computed
    # on MatchPredictionResult.home_advance / away_advance.
    is_knockout: bool = False
    # --- Profile-enhanced fields ---
    profile_home_attack: float = 0.0     # match: profile-derived home attack adjustment
    profile_home_defense: float = 0.0    # match: profile-derived home defense adjustment
    profile_away_attack: float = 0.0     # match: profile-derived away attack adjustment
    profile_away_defense: float = 0.0    # match: profile-derived away defense adjustment
    profile_home_form: float = 0.0       # match: profile-derived home form delta
    profile_away_form: float = 0.0       # match: profile-derived away form delta
    profile_draw_adjustment: float = 0.0 # match: profile-derived draw boost
    profile_available: bool = False
    profile_risk_flags: list[str] | None = None


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
    # Advance probabilities (who progresses) — only set for knockout matches.
    # For group-stage matches these stay None.
    home_advance: float | None = None
    away_advance: float | None = None


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

    # Extract config parameters with defaults (calibrated to WC 2026 avg 3.03 goals/match)
    base_goal_home = getattr(config, 'base_goal_mean_home', 1.55)
    base_goal_away = getattr(config, 'base_goal_mean_away', 1.35)
    str_coeff_home = getattr(config, 'strength_coeff_home', 1.20)
    str_coeff_away = getattr(config, 'strength_coeff_away', 1.00)
    min_xg = getattr(config, 'min_xg', 0.20)
    max_xg = getattr(config, 'max_xg', 4.50)
    draw_boost = getattr(config, 'draw_boost', 1.00)
    favorite_dampening = getattr(config, 'favorite_dampening', 0.00)
    underdog_boost = getattr(config, 'underdog_boost', 0.00)
    market_blend_weight = getattr(config, 'market_blend_weight', 0.00)
    upset_factor = getattr(config, 'upset_factor', 0.00)
    smart_blend = getattr(config, 'smart_market_blend', True)
    dynamic_draw = getattr(config, 'dynamic_draw_boost', True)
    profile_weight = getattr(config, 'profile_weight', 0.0)
    fifa_rank_weight = getattr(config, 'fifa_rank_weight', 0.15)
    poisson_dispersion = getattr(config, 'poisson_dispersion', 1.0)
    # Dixon-Coles low-score correction parameter (default -0.02).
    # Negative rho boosts 0-0 and 1-1 cells, mitigating Poisson's tendency
    # to under-predict low-scoring draws.
    dixon_coles_rho = getattr(config, 'dixon_coles_rho', -0.02)

    # FIFA rank delta adjustment:
    # fifa_rank_delta = home_fifa_rank - away_fifa_rank (negative = home ranked higher)
    # Research shows IC=0.472, equivalent to elo_diff. Blend as supplementary signal.
    # Normalize: rank difference of ~40 ≈ Elo strength_delta of ~0.2 (moderate gap)
    fifa_rank_adjustment = 0.0
    if context.fifa_rank_delta != 0.0:
        # Negative delta means home is ranked higher → positive adjustment for home
        fifa_rank_adjustment = -context.fifa_rank_delta / 40.0 * 0.2 * fifa_rank_weight

    # Profile-aware adjustments: blend profile-derived adjustments with manual/auto adjustments
    if profile_weight > 0 and context.profile_available:
        home_attack_total = context.home_attack_adjustment + context.profile_home_attack * profile_weight
        home_defense_total = context.home_defense_adjustment + context.profile_home_defense * profile_weight
        away_attack_total = context.away_attack_adjustment + context.profile_away_attack * profile_weight
        away_defense_total = context.away_defense_adjustment + context.profile_away_defense * profile_weight
        form_contribution = (context.profile_home_form + context.profile_away_form) * profile_weight
        profile_draw = context.profile_draw_adjustment * profile_weight
    else:
        home_attack_total = context.home_attack_adjustment
        home_defense_total = context.home_defense_adjustment
        away_attack_total = context.away_attack_adjustment
        away_defense_total = context.away_defense_adjustment
        form_contribution = 0.0
        profile_draw = 0.0

    strength_delta = (
        home_strength
        - away_strength
        + context.recent_form_delta
        + context.host_advantage
        + fifa_rank_adjustment
        + form_contribution
    )
    home_xg = float(
        np.clip(
            base_goal_home
            + str_coeff_home * strength_delta
            + home_attack_total
            - away_defense_total,
            min_xg,
            max_xg,
        )
    )
    away_xg = float(
        np.clip(
            base_goal_away
            - str_coeff_away * strength_delta
            + away_attack_total
            - home_defense_total,
            min_xg,
            max_xg,
        )
    )
    home_goals = _goal_probabilities(home_xg, poisson_dispersion)
    away_goals = _goal_probabilities(away_xg, poisson_dispersion)
    matrix = np.outer(home_goals, away_goals)

    # --- Dixon-Coles low-score correction ---
    # Adjusts the four low-score cells (0-0, 1-0, 0-1, 1-1) to better reflect
    # the negative scoring correlation observed in real football. Pure Poisson
    # assumes independence between teams' scoring, which over-predicts 1-0/0-1
    # and under-predicts 0-0/1-1 (the "low-scoring draw" pattern).
    # τ(0,0) = 1 - ρ*λ*μ, τ(1,0) = 1 + ρ*μ, τ(0,1) = 1 + ρ*λ, τ(1,1) = 1 - ρ.
    # Negative ρ boosts 0-0 and 1-1 (and depresses 1-0/0-1), matching the
    # observed WC group-stage draw rate (~37.5% vs model's ~20%).
    if dixon_coles_rho != 0.0 and matrix.shape >= (2, 2):
        lam, mu = home_xg, away_xg
        rho = dixon_coles_rho
        # Compute multipliers safely.
        m00 = 1.0 - rho * lam * mu
        m10 = 1.0 + rho * mu
        m01 = 1.0 + rho * lam
        m11 = 1.0 - rho
        # Apply multipliers to in-range cells.
        matrix[0, 0] *= m00
        matrix[1, 0] *= m10
        matrix[0, 1] *= m01
        matrix[1, 1] *= m11
        # Negative values are nonsensical for probabilities — clip to zero.
        matrix = np.clip(matrix, 0.0, None)
        # Re-normalize the matrix total to 1.0 (the four cells are a small
        # fraction of total mass, so renormalization preserves marginals closely).
        total_mass = float(matrix.sum())
        if total_mass > 1e-12:
            matrix = matrix / total_mass

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
    if dynamic_draw and draw > 0:
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

        # Cap total extra draw boost. Group stage caps at 15pp (WC group stage
        # draw rate ~37.5% vs model baseline ~20%); knockout caps at 5pp
        # because knockout 90-min draw rate is materially lower (~25%).
        draw_cap = 0.05 if context.is_knockout else 0.15
        extra_draw = min(extra_draw, draw_cap)

        if extra_draw > 0:
            draw += extra_draw
            # Reduce proportionally from win probabilities
            win_total = home_win + away_win
            if win_total > 0:
                home_win -= extra_draw * (home_win / win_total)
                away_win -= extra_draw * (away_win / win_total)
            total = home_win + draw + away_win
            home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Profile-derived draw adjustment (from team stability matchups)
    if profile_draw > 0 and draw > 0:
        draw += profile_draw
        win_total = home_win + away_win
        if win_total > 0:
            home_win -= profile_draw * (home_win / win_total)
            away_win -= profile_draw * (away_win / win_total)
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

    # Apply combined underdog adjustment (merges underdog_boost + upset_factor)
    combined_underdog = underdog_boost + upset_factor
    if combined_underdog > 0:
        if home_win <= away_win:
            home_win += combined_underdog
        else:
            away_win += combined_underdog
        total = home_win + draw + away_win
        home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Market blend: if market_blend_weight > 0 and market data available in context
    if market_blend_weight > 0 and context.market_probs is not None:
        # Research-enhanced: adaptive market blend weight
        # When market and model disagree significantly, increase market weight
        # because odds data has proven 8.7% Brier improvement over pure Elo+Poisson
        adaptive_weight = market_blend_weight
        if smart_blend:
            market_home = context.market_probs.get("home_win", 0.0)
            market_draw = context.market_probs.get("draw", 0.0)
            market_away = context.market_probs.get("away_win", 0.0)
            market_max = max(market_home, market_draw, market_away)
            model_max = max(home_win, draw, away_win)

            # If market and model disagree on direction, increase market weight by 50%
            model_dir = int(np.argmax([home_win, draw, away_win]))
            market_dir = int(np.argmax([market_home, market_draw, market_away]))
            if model_dir != market_dir:
                adaptive_weight = min(market_blend_weight * 1.5, 0.40)

            # Confidence amplification: when market is highly confident
            # (max >= 0.55) the implied probability has been historically more
            # accurate — increase blend weight by up to 25%.
            if market_max >= 0.55:
                conf_boost = 1.0 + 0.25 * min(1.0, (market_max - 0.55) / 0.20)
                adaptive_weight = min(adaptive_weight * conf_boost, 0.40)

            # Draw signal: if market sees a draw strongly (>= 30%) but the
            # model's draw is materially lower (< 25%), trust the market a
            # bit more — draw odds are a strong exogenous signal.
            if market_draw >= 0.30 and draw < 0.25:
                adaptive_weight = min(adaptive_weight * 1.20, 0.40)

            # Magnitude gap: even when direction agrees, if market and model
            # differ by >= 15pp on the top outcome, lean slightly more on
            # market — calibration has shown odds are better calibrated on
            # heavy favorites / heavy underdogs.
            if model_dir == market_dir and abs(market_max - model_max) >= 0.15:
                adaptive_weight = min(adaptive_weight * 1.15, 0.40)

        blended = blend_with_market(
            {"home_win": home_win, "draw": draw, "away_win": away_win},
            context.market_probs,
            adaptive_weight,
        )
        home_win = blended["home_win"]
        draw = blended["draw"]
        away_win = blended["away_win"]

    matrix = _rebalance_matrix_to_outcomes(matrix, home_win, draw, away_win)

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
    # Overall confidence: blend data + model using unified semantics so
    # the confidence/confidence_label field reflects both input quality
    # and model certainty, not just one of them.
    o_conf, o_label = overall_confidence(d_conf, m_conf)

    # Determine model version
    model_ver = MODEL_VERSION
    if config is not None:
        model_ver = getattr(config, 'name', MODEL_VERSION)

    # Knockout advance probabilities: combine 90-minute result with a
    # two-stage extra-time + penalties model.
    #   - 90-min winner advances directly.
    #   - 90-min draw → 30 min extra time. Empirically ~50% of drawn knockout
    #     matches are decided in ET; the stronger side (by Elo strength_delta)
    #     wins ET more often. Modelled as
    #     P(home wins ET | draw) = 0.5 + 0.50 * tanh(strength_delta).
    #   - If still level after ET → penalties, modelled as a coin flip with a
    #     small Elo bias (penalty shootouts are ~50/50 long-run).
    # The result sums to 1.0 and is only attached to knockout predictions.
    home_advance: float | None = None
    away_advance: float | None = None
    if context.is_knockout:
        # ET win probability: stronger team wins ET more often.
        # Coefficient 0.50 (was 0.10) ensures strength_delta in [0,1]
        # produces meaningful ET win swing (up to tanh(0.5)≈0.23 → 73%/27%).
        et_home_win = 0.5 + 0.50 * float(np.tanh(strength_delta))
        et_away_win = 1.0 - et_home_win
        p_decided_in_et = 0.50  # 50% of drawn games end in ET
        pen_home_win = 0.5 + 0.04 * float(np.tanh(strength_delta))  # small elo bias
        pen_away_win = 1.0 - pen_home_win
        # P(home advances | 90-min draw)
        p_home_advance_given_draw = (
            p_decided_in_et * et_home_win
            + (1.0 - p_decided_in_et) * pen_home_win
        )
        home_advance = home_win + draw * p_home_advance_given_draw
        away_advance = 1.0 - home_advance

    return MatchPredictionResult(
        home_xg=home_xg,
        away_xg=away_xg,
        home_win=home_win,
        draw=draw,
        away_win=away_win,
        scorelines=scorelines,
        score_matrix=matrix.tolist(),
        confidence=o_conf,
        confidence_label=o_label,
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
            risk_flags=context.profile_risk_flags,
        ),
        model_version=model_ver,
        home_advance=home_advance,
        away_advance=away_advance,
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


def _goal_probabilities(expected_goals: float, dispersion: float = 1.0) -> np.ndarray:
    """Compute goal probabilities with optional dispersion adjustment.

    dispersion = 1.0: standard Poisson (no adjustment)
    dispersion > 1.0: flatter distribution (more extreme scores)
    dispersion < 1.0: sharper distribution (fewer extreme scores)
    """
    exact = poisson.pmf(np.arange(_MAX_EXACT_GOALS + 1), expected_goals)
    tail = max(0.0, 1.0 - float(exact.sum()))
    values = np.append(exact, tail)

    # Apply dispersion: power transform to flatten/sharpen the distribution
    if dispersion != 1.0:
        values = values ** (1.0 / dispersion)
        values = values / values.sum()

    return values


def _rebalance_matrix_to_outcomes(
    matrix: np.ndarray,
    home_win: float,
    draw: float,
    away_win: float,
) -> np.ndarray:
    """Scale score cells so the matrix matches final outcome probabilities.

    Uses Iterative Proportional Fitting (IPF): alternates between
    (1) scaling each outcome bucket (home_win/draw/away_win) to its target
    and (2) preserving the row (home goals) and column (away goals)
    marginal distributions from the original matrix. This preserves the
    joint distribution structure far better than a single bucket-only
    rescale, so the resulting scoreline probabilities remain realistic.
    """
    row_idx, col_idx = np.indices(matrix.shape)
    masks = {
        "home_win": row_idx > col_idx,
        "draw": row_idx == col_idx,
        "away_win": row_idx < col_idx,
    }
    targets = {
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
    }

    rebalanced = matrix.astype(float, copy=True)
    # Store original row/column sums for IPF marginal preservation.
    orig_row_sums = rebalanced.sum(axis=1, keepdims=True)
    orig_col_sums = rebalanced.sum(axis=0, keepdims=True)

    # IPF: iterate bucket-scaling + marginal restoration.
    for _ in range(5):
        # Step 1: scale each bucket to match target outcome probability.
        for key, mask in masks.items():
            current = float(rebalanced[mask].sum())
            if current > 1e-12:
                rebalanced[mask] *= targets[key] / current

        # Step 2: restore row marginals (home goals distribution).
        row_sums = rebalanced.sum(axis=1, keepdims=True)
        np.divide(rebalanced, row_sums, out=rebalanced, where=row_sums > 1e-12)
        rebalanced *= orig_row_sums

        # Step 3: restore column marginals (away goals distribution).
        col_sums = rebalanced.sum(axis=0, keepdims=True)
        np.divide(rebalanced, col_sums, out=rebalanced, where=col_sums > 1e-12)
        rebalanced *= orig_col_sums

    # Final pass: ensure buckets exactly match targets (IPF converges
    # close but not exact due to the marginal restoration steps).
    for key, mask in masks.items():
        current = float(rebalanced[mask].sum())
        if current > 1e-12:
            rebalanced[mask] *= targets[key] / current

    # Final normalization to ensure sum == 1.0
    total = float(rebalanced.sum())
    if total > 0:
        rebalanced /= total
    return rebalanced
