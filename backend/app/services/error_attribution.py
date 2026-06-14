"""Error attribution system for match predictions.

Classifies prediction errors into structured error types with
reasoning and suggested fixes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorAttribution:
    error_type: str
    error_reason: str
    suggested_fix: str


def classify_error(
    home_win_prob: float,
    draw_prob: float,
    away_win_prob: float,
    actual_result: str,  # "home" | "draw" | "away"
    home_xg: float,
    away_xg: float,
    actual_home_score: int,
    actual_away_score: int,
    top_scorelines: list[dict[str, Any]] | None = None,
    market_home_prob: float | None = None,
    market_draw_prob: float | None = None,
    market_away_prob: float | None = None,
    base_home_win: float | None = None,
    base_draw: float | None = None,
    base_away_win: float | None = None,
    has_auto_adjustments: bool = False,
    has_numerical_adjustments: bool = False,
) -> list[ErrorAttribution]:
    """Classify a match prediction into one or more error types.

    Returns a list of ErrorAttribution objects; a single match can have
    multiple error types (e.g. favorite_overestimated AND market_disagreed_model_wrong).
    """
    errors: list[ErrorAttribution] = []
    max_prob = max(home_win_prob, draw_prob, away_win_prob)
    predicted_outcome = _argmax(home_win_prob, draw_prob, away_win_prob)
    outcome_correct = predicted_outcome == actual_result

    # Determine which team is the "favorite" and which is the "underdog"
    if home_win_prob >= away_win_prob:
        favorite_prob = home_win_prob
        underdog_prob = away_win_prob
        favorite_side = "home"
    else:
        favorite_prob = away_win_prob
        underdog_prob = home_win_prob
        favorite_side = "away"

    # 1. favorite_overestimated: model's highest-prob side lost AND max prob >= 50%
    if not outcome_correct and favorite_prob >= 0.50 and actual_result != predicted_outcome:
        if (favorite_side == "home" and actual_result != "home") or \
           (favorite_side == "away" and actual_result != "away"):
            errors.append(ErrorAttribution(
                error_type="favorite_overestimated",
                error_reason=f"模型高估强队({favorite_side})，预测概率 {favorite_prob:.1%}，但实际该方未获胜",
                suggested_fix="建议降低 Elo 差距权重(favorite_dampening)或提高平局基准概率(draw_boost)",
            ))

    # 2. draw_underestimated: actual draw, but model draw_prob < 28%
    if actual_result == "draw" and draw_prob < 0.28:
        errors.append(ErrorAttribution(
            error_type="draw_underestimated",
            error_reason=f"实际平局，但模型平局概率仅 {draw_prob:.1%}，显著低估",
            suggested_fix="建议提高 draw_boost 参数，或降低 base_goal_mean 减少进球预期从而提升平局概率",
        ))

    # 3. underdog_underestimated: actual underdog won, but underdog prob < 30%
    if actual_result != "draw" and actual_result != predicted_outcome:
        if (actual_result == "home" and away_win_prob > home_win_prob and home_win_prob < 0.30) or \
           (actual_result == "away" and home_win_prob > away_win_prob and away_win_prob < 0.30):
            errors.append(ErrorAttribution(
                error_type="underdog_underestimated",
                error_reason=f"弱队爆冷获胜，但模型弱队概率仅 {underdog_prob:.1%}",
                suggested_fix="建议增加 underdog_boost 或 upset_factor 参数，提升尾部冷门概率",
            ))

    # 4. overconfident_wrong: max prob >= 60% but prediction wrong
    if max_prob >= 0.60 and not outcome_correct:
        errors.append(ErrorAttribution(
            error_type="overconfident_wrong",
            error_reason=f"模型过度自信(最高概率 {max_prob:.1%})但预测错误",
            suggested_fix="建议增加 favorite_dampening 或降低 elo_scale，抑制过度自信",
        ))

    # 5. low_confidence_match: max_prob < 45%
    if max_prob < 0.45:
        errors.append(ErrorAttribution(
            error_type="low_confidence_match",
            error_reason=f"三项概率接近，模型最高概率仅 {max_prob:.1%}，属于低置信度比赛",
            suggested_fix="低置信度比赛属于正常不确定性，无需调整。可关注市场赔率是否有更明确信号",
        ))

    # 6. goal_total_missed: most likely score total vs actual total diff >= 2
    if top_scorelines:
        most_likely_total = top_scorelines[0].get("home_goals", 0) + top_scorelines[0].get("away_goals", 0)
        actual_total = actual_home_score + actual_away_score
        if abs(most_likely_total - actual_total) >= 2:
            errors.append(ErrorAttribution(
                error_type="goal_total_missed",
                error_reason=f"最可能比分总进球={most_likely_total}，实际={actual_total}，差距≥2",
                suggested_fix="建议调整 base_goal_mean 或 poisson_dispersion 参数",
            ))

    # 6b. low_score_draw_missed: actual draw with total goals <= 2 AND draw_prob < 28%
    if actual_result == "draw" and (actual_home_score + actual_away_score) <= 2 and draw_prob < 0.28:
        errors.append(ErrorAttribution(
            error_type="low_score_draw_missed",
            error_reason=f"低比分平局(总进球≤2)但模型平局概率仅 {draw_prob:.1%}，低估低分平局可能性",
            suggested_fix="建议在低 xG 场景下提升 draw_boost，或增加 low_score_draw_adjustment 参数",
        ))

    # Market comparison (7-9)
    if market_home_prob is not None and market_draw_prob is not None and market_away_prob is not None:
        market_predicted = _argmax(market_home_prob, market_draw_prob, market_away_prob)
        model_and_market_agree = predicted_outcome == market_predicted

        if model_and_market_agree and not outcome_correct:
            # 7. market_agreed_but_wrong
            errors.append(ErrorAttribution(
                error_type="market_agreed_but_wrong",
                error_reason="模型和市场方向一致，但都预测错误。属于难以预见的比赛",
                suggested_fix="此类比赛属于尾部事件，无需参数调整。可关注情报质量是否不足",
            ))
        elif not model_and_market_agree and not outcome_correct:
            # 8. market_disagreed_model_wrong
            errors.append(ErrorAttribution(
                error_type="market_disagreed_model_wrong",
                error_reason="市场与模型分歧，模型方向错误。市场信号有价值",
                suggested_fix="建议增加 market_blend_weight，让市场赔率参与概率校准",
            ))
        elif not model_and_market_agree and outcome_correct:
            # 9. market_disagreed_model_right
            errors.append(ErrorAttribution(
                error_type="market_disagreed_model_right",
                error_reason="市场与模型分歧，模型方向正确。模型在分歧场景更优",
                suggested_fix="模型在分歧场景表现好，当前参数无需调整",
            ))

    # 10-11. numerical adjustment effect
    if has_numerical_adjustments and base_home_win is not None:
        import math
        # Compute base brier
        o_home = 1.0 if actual_result == "home" else 0.0
        o_draw = 1.0 if actual_result == "draw" else 0.0
        o_away = 1.0 if actual_result == "away" else 0.0
        base_brier = (base_home_win - o_home) ** 2 + (base_draw - o_draw) ** 2 + (base_away_win - o_away) ** 2
        adj_brier = (home_win_prob - o_home) ** 2 + (draw_prob - o_draw) ** 2 + (away_win_prob - o_away) ** 2
        if adj_brier < base_brier - 0.01:
            errors.append(ErrorAttribution(
                error_type="numerical_helped",
                error_reason=f"数值修正改善了预测(Brier {base_brier:.4f}→{adj_brier:.4f})",
                suggested_fix="数值修正有效，建议继续使用或适当提升 numerical_adjustment_weight",
            ))
        elif adj_brier > base_brier + 0.01:
            errors.append(ErrorAttribution(
                error_type="numerical_hurt",
                error_reason=f"数值修正恶化了预测(Brier {base_brier:.4f}→{adj_brier:.4f})",
                suggested_fix="数值修正效果不佳，建议降低 numerical_adjustment_weight 或检查伤停数据质量",
            ))

    # 12-13. warning effect
    if has_auto_adjustments and not outcome_correct:
        errors.append(ErrorAttribution(
            error_type="warning_helped",
            error_reason="存在情报警告且预测错误，警告有效提示了不确定性",
            suggested_fix="警告系统有效，继续维持情报管线",
        ))
    elif has_auto_adjustments and outcome_correct:
        errors.append(ErrorAttribution(
            error_type="warning_hurt",
            error_reason="存在情报警告但预测正确，警告可能过度降权",
            suggested_fix="警告可能过于保守，可适当降低警告的 confidence_penalty",
        ))

    # If no errors found, it was a correct prediction with no issues
    if not errors and outcome_correct:
        errors.append(ErrorAttribution(
            error_type="correct",
            error_reason="预测方向正确",
            suggested_fix="",
        ))

    return errors


def _argmax(a: float, b: float, c: float) -> str:
    vals = [("home", a), ("draw", b), ("away", c)]
    return max(vals, key=lambda x: x[1])[0]
