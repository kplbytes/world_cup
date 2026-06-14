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

    profile_info = {
        "home_team_profile": request.home_team_profile or {"status": "insufficient_sample"},
        "away_team_profile": request.away_team_profile or {"status": "insufficient_sample"},
    }
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
        f"- Team profile claims MUST come from Team Profiles above; do not add reputation-based traits\n"
        f"- If a profile has insufficient samples, state that uncertainty and give it little weight\n"
        f"- prompt_version: {prompt_version}"
    )

    return "\n\n".join(sections)
