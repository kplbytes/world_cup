"""Build AI prediction prompts from structured system data."""

from __future__ import annotations

import json
from typing import Any

from app.ai.providers.base import AIPredictionRequest


def build_prediction_prompt(request: AIPredictionRequest, prompt_version: str = "worldcup-ai-v1") -> str:
    """Build a structured prompt for AI match prediction."""

    sections = []

    # 1. Match basic info
    match_info = {
        "match_id": request.match_id,
        "stage": request.stage,
        "group": request.group,
        "knockout_round": request.knockout_round,
        "home_team": request.home_team,
        "away_team": request.away_team,
        "kickoff": request.kickoff,
        "venue": request.venue,
        "neutral_ground": request.neutral_ground,
    }
    sections.append(f"## Match Information\n{json.dumps(match_info, indent=2, ensure_ascii=False)}")

    # 2. System model prediction
    system_info = {
        "model": "elo-poisson-v1",
        "home_win_probability": round(request.system_home_win, 4),
        "draw_probability": round(request.system_draw, 4),
        "away_win_probability": round(request.system_away_win, 4),
        "home_xg": round(request.system_home_xg, 3),
        "away_xg": round(request.system_away_xg, 3),
        "most_likely_score": request.most_likely_score,
        "model_confidence": round(request.system_model_confidence, 3),
        "data_confidence": round(request.system_data_confidence, 3) if request.system_data_confidence else None,
    }
    sections.append(f"## System Model Prediction\n{json.dumps(system_info, indent=2, ensure_ascii=False)}")

    # 3. Market odds
    if request.market_home_prob is not None:
        market_info = {
            "provider": request.market_provider,
            "home_implied_probability": round(request.market_home_prob, 4),
            "draw_implied_probability": round(request.market_draw_prob, 4),
            "away_implied_probability": round(request.market_away_prob, 4),
            "market_divergence": round(request.market_divergence, 4) if request.market_divergence else None,
            "fetched_at": request.market_fetched_at,
        }
    else:
        market_info = {"status": "unknown", "note": "No market odds available"}
    sections.append(f"## Market Odds\n{json.dumps(market_info, indent=2, ensure_ascii=False)}")

    # 4. Intelligence info
    intel_info: dict[str, Any] = {}
    if request.injuries:
        intel_info["injuries"] = request.injuries
    if request.suspensions:
        intel_info["suspensions"] = request.suspensions
    if request.lineup_status:
        intel_info["lineup_status"] = request.lineup_status
    if request.roster_warning:
        intel_info["roster_warning"] = request.roster_warning
    if request.data_completeness is not None:
        intel_info["data_completeness"] = round(request.data_completeness, 3)
    if request.numerical_adjustment:
        intel_info["numerical_adjustment"] = request.numerical_adjustment
    if request.risk_flags:
        intel_info["risk_flags"] = request.risk_flags
    if intel_info:
        sections.append(f"## Intelligence Information\n{json.dumps(intel_info, indent=2, ensure_ascii=False)}")
    else:
        sections.append("## Intelligence Information\nNo intelligence data available.")

    # 5. Tournament context
    if request.group_standing_context:
        sections.append(f"## Group Standing Context\n{request.group_standing_context}")
    if request.knockout_context:
        sections.append(f"## Knockout Context\n{request.knockout_context}")

    # 6. Historical score summary
    if request.historical_score_summary:
        sections.append(f"## Historical Model Performance\n{request.historical_score_summary}")
    else:
        sections.append("## Historical Model Performance\nInsufficient sample - no reliable history.")

    profile_info = {"status": "disabled_display_only", "note": "Team profiles are not prediction inputs in the current phase."}
    sections.append(f"## Team Profiles\n{json.dumps(profile_info, indent=2, ensure_ascii=False)}")

    # 7. Output format instructions
    output_format = {
        "home_win": "float 0-1",
        "draw": "float 0-1",
        "away_win": "float 0-1",
        "confidence": "float 0-1, your confidence in this prediction",
        "risk_flags": ["list of risk factors"],
        "key_factors": ["list of key decision factors"],
        "reason": "brief explanation of your prediction",
        "uncertainties": ["list of uncertainties"],
        "disagreement_with_system": "describe if you disagree with system model and why, or 'none'",
        "disagreement_with_market": "describe if you disagree with market and why, or 'none'",
        "recommended_label": "one of: home_win, draw, away_win, uncertain",
        "profile_factors": ["profile-supported factors used in the decision"],
        "profile_risk_flags": ["profile-derived risk flags"],
    }
    sections.append(
        f"## Required Output Format\n"
        f"Return ONLY a JSON object with these fields:\n"
        f"{json.dumps(output_format, indent=2, ensure_ascii=False)}\n\n"
        f"CRITICAL RULES:\n"
        f"- home_win + draw + away_win MUST equal 1.0\n"
        f"- Use ONLY the data provided above\n"
        f"- Do NOT fabricate injuries, odds, or news\n"
        f"- Mark missing data as 'unknown'\n"
        f"- Do NOT output betting advice\n"
        f"- Explicitly state uncertainties\n"
        f"- Team profiles are disabled for prediction in the current phase; do not infer profile-based probability adjustments\n"
        f"- prompt_version: {prompt_version}"
    )

    return "\n\n".join(sections)


def build_prediction_prompt_v2(request: AIPredictionRequest, prompt_version: str = "worldcup-ai-v2") -> str:
    """Build an independence-focused prompt for AI match prediction.

    Unlike v1, this prompt does NOT expose the system model's three-way probabilities
    or xG values directly. Instead, it provides qualitative strength indicators
    to encourage independent judgment from the AI model.
    """

    sections = []

    # 1. Match basic info (same as v1)
    match_info = {
        "match_id": request.match_id,
        "stage": request.stage,
        "group": request.group,
        "knockout_round": request.knockout_round,
        "home_team": request.home_team,
        "away_team": request.away_team,
        "kickoff": request.kickoff,
        "venue": request.venue,
        "neutral_ground": request.neutral_ground,
    }
    sections.append(f"## Match Information\n{json.dumps(match_info, indent=2, ensure_ascii=False)}")

    # 2. Team Strength Assessment (replaces System Model Prediction)
    # Instead of raw probabilities, provide qualitative indicators
    strength_diff = request.system_home_win - request.system_away_win
    if strength_diff > 0.30:
        strength_tier = "strong_favorite"
        strength_desc = f"{request.home_team} is a strong favorite"
    elif strength_diff > 0.15:
        strength_tier = "moderate_favorite"
        strength_desc = f"{request.home_team} is a moderate favorite"
    elif strength_diff > 0.05:
        strength_tier = "slight_favorite"
        strength_desc = f"{request.home_team} has a slight edge"
    elif strength_diff > -0.05:
        strength_tier = "evenly_matched"
        strength_desc = "Teams are evenly matched"
    elif strength_diff > -0.15:
        strength_tier = "slight_underdog"
        strength_desc = f"{request.away_team} has a slight edge"
    elif strength_diff > -0.30:
        strength_tier = "moderate_underdog"
        strength_desc = f"{request.away_team} is a moderate favorite"
    else:
        strength_tier = "strong_underdog"
        strength_desc = f"{request.away_team} is a strong favorite"

    # Draw likelihood indicator based on system draw probability
    if request.system_draw > 0.30:
        draw_likelihood = "high"
    elif request.system_draw > 0.25:
        draw_likelihood = "moderate"
    else:
        draw_likelihood = "low"

    # Expected goal range (qualitative, not exact)
    total_xg = request.system_home_xg + request.system_away_xg
    if total_xg > 3.0:
        goal_expectation = "high_scoring"
    elif total_xg > 2.2:
        goal_expectation = "moderate_scoring"
    else:
        goal_expectation = "low_scoring"

    strength_info = {
        "overall_assessment": strength_desc,
        "strength_tier": strength_tier,
        "draw_likelihood": draw_likelihood,
        "goal_expectation": goal_expectation,
        "model_confidence": round(request.system_model_confidence, 3),
        "data_confidence": round(request.system_data_confidence, 3) if request.system_data_confidence else None,
    }
    sections.append(f"## Team Strength Assessment\n{json.dumps(strength_info, indent=2, ensure_ascii=False)}")

    # 3. Market odds (same as v1 - market is independent signal)
    if request.market_home_prob is not None:
        market_info = {
            "provider": request.market_provider,
            "home_implied_probability": round(request.market_home_prob, 4),
            "draw_implied_probability": round(request.market_draw_prob, 4),
            "away_implied_probability": round(request.market_away_prob, 4),
            "market_divergence": round(request.market_divergence, 4) if request.market_divergence else None,
            "fetched_at": request.market_fetched_at,
        }
    else:
        market_info = {"status": "unknown", "note": "No market odds available"}
    sections.append(f"## Market Odds\n{json.dumps(market_info, indent=2, ensure_ascii=False)}")

    # 4. Intelligence info (same as v1)
    intel_info: dict[str, Any] = {}
    if request.injuries:
        intel_info["injuries"] = request.injuries
    if request.suspensions:
        intel_info["suspensions"] = request.suspensions
    if request.lineup_status:
        intel_info["lineup_status"] = request.lineup_status
    if request.roster_warning:
        intel_info["roster_warning"] = request.roster_warning
    if request.data_completeness is not None:
        intel_info["data_completeness"] = round(request.data_completeness, 3)
    if request.numerical_adjustment:
        intel_info["numerical_adjustment"] = request.numerical_adjustment
    if request.risk_flags:
        intel_info["risk_flags"] = request.risk_flags
    if intel_info:
        sections.append(f"## Intelligence Information\n{json.dumps(intel_info, indent=2, ensure_ascii=False)}")
    else:
        sections.append("## Intelligence Information\nNo intelligence data available.")

    # 5. Tournament context (same as v1)
    if request.group_standing_context:
        sections.append(f"## Group Standing Context\n{request.group_standing_context}")
    if request.knockout_context:
        sections.append(f"## Knockout Context\n{request.knockout_context}")

    # 6. Historical score summary (same as v1)
    if request.historical_score_summary:
        sections.append(f"## Historical Model Performance\n{request.historical_score_summary}")
    else:
        sections.append("## Historical Model Performance\nInsufficient sample - no reliable history.")

    # 7. Team profiles (same as v1)
    profile_info = {"status": "disabled_display_only", "note": "Team profiles are not prediction inputs in the current phase."}
    sections.append(f"## Team Profiles\n{json.dumps(profile_info, indent=2, ensure_ascii=False)}")

    # 8. Output format instructions (v2 specific)
    output_format = {
        "home_win": "float 0-1",
        "draw": "float 0-1",
        "away_win": "float 0-1",
        "predicted_score": "predicted scoreline like '2-1'",
        "confidence": "float 0-1, your confidence in this prediction",
        "risk_flags": ["list of risk factors"],
        "key_factors": ["list of key decision factors"],
        "reason": "brief explanation of your prediction",
        "uncertainties": ["list of uncertainties"],
        "disagreement_with_system": "describe if your prediction differs from the strength assessment and why, or 'none'",
        "disagreement_with_market": "describe if you disagree with market and why, or 'none'",
        "recommended_label": "one of: home_win, draw, away_win, uncertain",
        "profile_factors": ["profile-supported factors used in the decision"],
        "profile_risk_flags": ["profile-derived risk flags"],
        "independence_note": "explain whether your prediction is independent of the strength assessment, or if you relied on it heavily",
    }
    sections.append(
        f"## Required Output Format\n"
        f"Return ONLY a JSON object with these fields:\n"
        f"{json.dumps(output_format, indent=2, ensure_ascii=False)}\n\n"
        f"CRITICAL RULES:\n"
        f"- home_win + draw + away_win MUST equal 1.0\n"
        f"- You MUST form your OWN independent probability assessment based on ALL the data provided\n"
        f"- Do NOT simply copy or anchor on any single input signal\n"
        f"- The Team Strength Assessment above is a REFERENCE ONLY, not a prescription\n"
        f"- If the intelligence data or market odds suggest a different outcome, you MUST reflect that in your probabilities\n"
        f"- If evidence is weak or conflicting, move probabilities toward EVEN (e.g., 0.33/0.33/0.33), do NOT mechanically follow the strength tier\n"
        f"- Use ONLY the data provided above\n"
        f"- Do NOT fabricate injuries, odds, or news\n"
        f"- Mark missing data as 'unknown'\n"
        f"- Do NOT output betting advice\n"
        f"- Explicitly state uncertainties\n"
        f"- Team profiles are disabled for prediction in the current phase; do not infer profile-based probability adjustments\n"
        f"- independence_note is REQUIRED: explain whether and how you diverged from the strength assessment\n"
        f"- prompt_version: {prompt_version}"
    )

    return "\n\n".join(sections)


def build_prompt(request: AIPredictionRequest, prompt_version: str = "worldcup-ai-v1") -> str:
    """Build prediction prompt using the specified version."""
    if prompt_version.startswith("worldcup-ai-v2") or prompt_version == "ai-independent-v2":
        return build_prediction_prompt_v2(request, prompt_version)
    return build_prediction_prompt(request, prompt_version)


def analyze_prompt_independence(prompt_text: str) -> dict[str, Any]:
    """Analyze whether a prompt contains baseline probability anchors."""
    import re

    # Check for direct probability exposure patterns
    prob_patterns = [
        r'home_win_probability["\s:]+0?\.\d+',
        r'draw_probability["\s:]+0?\.\d+',
        r'away_win_probability["\s:]+0?\.\d+',
        r'home_xg["\s:]+\d+\.?\d*',
        r'away_xg["\s:]+\d+\.?\d*',
    ]

    found_anchors = []
    for pattern in prob_patterns:
        matches = re.findall(pattern, prompt_text)
        if matches:
            found_anchors.extend(matches)

    return {
        "contains_baseline_probabilities": len(found_anchors) > 0,
        "anchor_count": len(found_anchors),
        "anchors_found": found_anchors[:10],  # Limit to first 10
        "prompt_length": len(prompt_text),
    }
