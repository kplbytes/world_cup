"""Profile-to-Prediction Adapter.

Translates TeamProfile scores (0-100 scale) into MatchContext-compatible
adjustments (0.0-0.15 scale) for the Poisson prediction pipeline.
"""

from __future__ import annotations

from typing import Any


def compute_profile_adjustments(
    home_profile: Any | None,
    away_profile: Any | None,
) -> dict[str, Any]:
    """Compute profile-derived adjustments for MatchContext.

    Returns dict with keys matching MatchContext profile fields.
    All returned values default to safe zero/empty when profiles are None.

    Scale: profile scores [0,100] are mapped to small deltas.
      Attack/defense adjustments: max ±0.12
      Form adjustments: max ±0.05
      Draw adjustment: max ±0.03
    """
    if home_profile is None or away_profile is None:
        return {
            "profile_home_attack": 0.0,
            "profile_home_defense": 0.0,
            "profile_away_attack": 0.0,
            "profile_away_defense": 0.0,
            "profile_home_form": 0.0,
            "profile_away_form": 0.0,
            "profile_draw_adjustment": 0.0,
            "profile_available": False,
            "profile_risk_flags": [],
        }

    # Normalize core scores from [0,100] to [0, 1.0]
    h_attack = _score(home_profile, "attack_score") / 100.0
    a_attack = _score(away_profile, "attack_score") / 100.0
    h_defense = _score(home_profile, "defense_score") / 100.0
    a_defense = _score(away_profile, "defense_score") / 100.0
    h_form = _score(home_profile, "recent_form_score") / 100.0
    a_form = _score(away_profile, "recent_form_score") / 100.0
    h_stability = _score(home_profile, "stability_score") / 100.0
    a_stability = _score(away_profile, "stability_score") / 100.0

    # Attack adjustment: home attack vs away defense (defense weighted 0.7)
    ATTACK_SCALE = 0.12
    home_attack_adj = (h_attack - a_defense * 0.7) * ATTACK_SCALE
    away_attack_adj = (a_attack - h_defense * 0.7) * ATTACK_SCALE

    # Defense adjustment: own defense reduces opponent xG
    DEFENSE_SCALE = 0.10
    home_defense_adj = (h_defense - a_attack * 0.7) * DEFENSE_SCALE
    away_defense_adj = (a_defense - h_attack * 0.7) * DEFENSE_SCALE

    # Form adjustment: recent form delta (home form helps home, away form helps away)
    FORM_SCALE = 0.05
    home_form_adj = h_form * FORM_SCALE
    away_form_adj = a_form * FORM_SCALE

    # Draw adjustment: both teams highly stable → higher draw probability
    draw_adj = 0.0
    if h_stability > 0.6 and a_stability > 0.6:
        combined = h_stability + a_stability
        draw_adj = min(0.03, (combined - 1.2) * 0.05)

    # Collect risk flags from both profiles
    risk_flags: list[str] = []
    home_flags = _risk_flags(home_profile)
    away_flags = _risk_flags(away_profile)
    if home_flags:
        risk_flags.extend(home_flags)
    if away_flags:
        risk_flags.extend(away_flags)

    return {
        "profile_home_attack": round(home_attack_adj, 4),
        "profile_home_defense": round(home_defense_adj, 4),
        "profile_away_attack": round(away_attack_adj, 4),
        "profile_away_defense": round(away_defense_adj, 4),
        "profile_home_form": round(home_form_adj, 4),
        "profile_away_form": round(away_form_adj, 4),
        "profile_draw_adjustment": round(draw_adj, 4),
        "profile_available": True,
        "profile_risk_flags": risk_flags,
    }


def _score(profile: Any, name: str) -> float:
    """Extract a core score from profile, defaulting to 50.0 (neutral)."""
    try:
        val = float(getattr(profile, name, 0.0))
    except (TypeError, ValueError):
        return 50.0
    return val if val > 0 else 50.0


def _risk_flags(profile: Any) -> list[str]:
    """Extract risk flags relevant to prediction."""
    try:
        flags = getattr(profile, "risk_flags_json", None) or []
        if isinstance(flags, list):
            return flags
    except Exception:
        pass
    return []
