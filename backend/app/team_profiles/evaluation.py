from __future__ import annotations

from collections import Counter
from math import log

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, PredictionSnapshot, TeamProfilePrediction
from app.services.scoring import _select_scorable_snapshot


def _brier(home: float, draw: float, away: float, actual: str) -> float:
    return sum((p - (1.0 if key == actual else 0.0)) ** 2 for key, p in (("home", home), ("draw", draw), ("away", away)))


def _logloss(home: float, draw: float, away: float, actual: str) -> float:
    clip = 1e-6
    probs = {"home": max(clip, min(1 - clip, home)), "draw": max(clip, min(1 - clip, draw)), "away": max(clip, min(1 - clip, away))}
    return -log(probs[actual])


def evaluate_profile_model(session: Session) -> dict:
    finals = list(session.scalars(select(Match).where(Match.status == "final").order_by(Match.kickoff)))
    details = []
    trait_help = Counter()
    trait_hurt = Counter()
    for match in finals:
        baseline = _select_scorable_snapshot(list(session.scalars(select(PredictionSnapshot).where(PredictionSnapshot.match_id == match.id))), match)
        candidates = list(session.scalars(select(TeamProfilePrediction).where(TeamProfilePrediction.match_id == match.id).order_by(TeamProfilePrediction.created_at.desc())))
        profile = next((p for p in candidates if p.is_pre_match_locked), None)
        profile = profile or next((p for p in candidates if p.is_fallback_locked), None)
        profile = profile or next((p for p in candidates if p.created_at < match.kickoff and not p.real_time_only), None)
        if baseline is None or profile is None:
            continue
        actual = "home" if match.home_score > match.away_score else "draw" if match.home_score == match.away_score else "away"
        baseline_brier = _brier(baseline.home_win, baseline.draw, baseline.away_win, actual)
        profile_brier = _brier(profile.home_win, profile.draw, profile.away_win, actual)
        profile_ll = _logloss(profile.home_win, profile.draw, profile.away_win, actual)
        delta = baseline_brier - profile_brier
        effect = "helped" if delta > 0.01 else "hurt" if delta < -0.01 else "neutral"
        if effect == "helped":
            trait_help.update(profile.triggered_traits_json or [])
        elif effect == "hurt":
            trait_hurt.update(profile.triggered_traits_json or [])
        details.append({
            "match_id": match.id, "baseline_brier": baseline_brier, "profile_brier": profile_brier,
            "profile_logloss": profile_ll,
            "brier_delta": delta, "effect": effect, "traits": profile.triggered_traits_json or [],
            "risk_flags": profile.risk_flags_json or [], "explanation": profile.explanation,
        })
    n = len(details)
    return {
        "model_version": "elo-poisson-v1-team-profile", "sample_count": n,
        "baseline_brier": sum(x["baseline_brier"] for x in details) / n if n else None,
        "profile_brier": sum(x["profile_brier"] for x in details) / n if n else None,
        "profile_logloss": sum(x["profile_logloss"] for x in details) / n if n else None,
        "helped": sum(x["effect"] == "helped" for x in details),
        "hurt": sum(x["effect"] == "hurt" for x in details),
        "neutral": sum(x["effect"] == "neutral" for x in details),
        "most_helpful_traits": [{"trait": key, "count": value} for key, value in trait_help.most_common(5)],
        "most_misleading_traits": [{"trait": key, "count": value} for key, value in trait_hurt.most_common(5)],
        "matches": details,
    }
