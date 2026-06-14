from __future__ import annotations


def _value(profile, name: str) -> float:
    return float(profile.get(name, 0.0) if isinstance(profile, dict) else getattr(profile, name, 0.0))


def apply_profile_adjustment(base: dict, home_profile, away_profile, home_elo: float, away_elo: float) -> dict:
    deltas = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
    flags: list[str] = []
    explanations: list[str] = []
    elo_gap = home_elo - away_elo
    favorite = "home_win" if elo_gap >= 0 else "away_win"
    underdog_profile = away_profile if elo_gap >= 0 else home_profile
    favorite_profile = home_profile if elo_gap >= 0 else away_profile

    if abs(elo_gap) >= 150 and _value(underdog_profile, "draw_resilience_score") >= 0.58:
        boost = min(0.04, 0.02 + (_value(underdog_profile, "draw_resilience_score") - 0.58) * 0.08)
        deltas["draw"] += boost
        deltas[favorite] -= boost
        flags.append("underdog_draw_resilience")
        explanations.append(f"弱势方遇强平局韧性提高平局概率 {boost:+.1%}")

    if abs(elo_gap) >= 180 and _value(favorite_profile, "favorite_win_rate") >= 0.7 and _value(favorite_profile, "favorite_overconfidence_risk") < 0.35:
        boost = min(0.03, (_value(favorite_profile, "favorite_win_rate") - 0.65) * 0.12)
        deltas[favorite] += boost
        deltas["draw"] -= boost
        flags.append("favorite_stability")
        explanations.append(f"热门方对弱队稳定性提高胜率 {boost:+.1%}")

    if _value(favorite_profile, "favorite_overconfidence_risk") >= 0.45 and _value(underdog_profile, "defensive_resilience_score") >= 0.6:
        shift = min(0.04, 0.02 + (_value(favorite_profile, "favorite_overconfidence_risk") - 0.45) * 0.08)
        deltas[favorite] -= shift
        deltas["draw"] += shift
        flags.append("favorite_overconfidence_risk")
        explanations.append(f"热门方失手风险与弱势方防守韧性提高平局概率 {shift:+.1%}")

    l1 = sum(abs(value) for value in deltas.values())
    if l1 > 0.08:
        scale = 0.08 / l1
        deltas = {key: value * scale for key, value in deltas.items()}
    deltas = {key: max(-0.05, min(0.05, value)) for key, value in deltas.items()}

    probs = {key: max(0.001, base[key] + deltas[key]) for key in deltas}
    total = sum(probs.values())
    probs = {key: value / total for key, value in probs.items()}
    actual_deltas = {key: probs[key] - base[key] for key in probs}

    low_score = (_value(home_profile, "low_score_tendency") + _value(away_profile, "low_score_tendency")) / 2
    xg_shift = min(0.15, max(0.0, low_score - 0.6) * 0.3)
    if xg_shift > 0:
        flags.append("low_score_tendency")
        explanations.append("双方低比分倾向下调预期进球")

    return {
        "model_version": "elo-poisson-v1-team-profile",
        "probabilities": probs,
        "probability_deltas": actual_deltas,
        "xg": {"home": max(0.2, base["home_xg"] - xg_shift), "away": max(0.2, base["away_xg"] - xg_shift)},
        "xg_deltas": {"home": -xg_shift, "away": -xg_shift},
        "risk_flags": list(dict.fromkeys(flags)),
        "explanation": "；".join(explanations) or "画像样本未触发概率修正，仅作为解释信号",
    }
