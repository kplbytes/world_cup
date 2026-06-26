from __future__ import annotations

from collections import Counter
from math import log

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, load_only

from app.models import Match, PredictionSnapshot, TeamProfilePrediction
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
    if not finals:
        return {
            "model_version": "elo-poisson-v1-team-profile", "sample_count": 0,
            "baseline_brier": None,
            "profile_brier": None,
            "profile_logloss": None,
            "helped": 0,
            "hurt": 0,
            "neutral": 0,
            "most_helpful_traits": [],
            "most_misleading_traits": [],
            "matches": [],
        }

    match_ids = [match.id for match in finals]
    baseline_ranked = (
        select(
            PredictionSnapshot.id.label("snapshot_id"),
            func.row_number().over(
                partition_by=PredictionSnapshot.match_id,
                order_by=PredictionSnapshot.snapshotted_at.desc(),
            ).label("rn"),
        )
        .join(Match, PredictionSnapshot.match_id == Match.id)
        .where(
            Match.status == "final",
            PredictionSnapshot.match_id.in_(match_ids),
            PredictionSnapshot.snapshotted_at < Match.kickoff,
        )
        .subquery()
    )
    baselines_by_match = {
        snap.match_id: snap
        for snap in session.scalars(
            select(PredictionSnapshot)
            .options(
                load_only(
                    PredictionSnapshot.match_id,
                    PredictionSnapshot.home_win,
                    PredictionSnapshot.draw,
                    PredictionSnapshot.away_win,
                )
            )
            .join(baseline_ranked, PredictionSnapshot.id == baseline_ranked.c.snapshot_id)
            .where(baseline_ranked.c.rn == 1)
        )
    }

    profile_priority = case(
        (TeamProfilePrediction.is_pre_match_locked.is_(True), 0),
        (TeamProfilePrediction.is_fallback_locked.is_(True), 1),
        (
            (TeamProfilePrediction.created_at < Match.kickoff)
            & TeamProfilePrediction.real_time_only.is_(False),
            2,
        ),
        else_=3,
    )
    profile_ranked = (
        select(
            TeamProfilePrediction.id.label("prediction_id"),
            TeamProfilePrediction.match_id.label("match_id"),
            profile_priority.label("priority"),
            func.row_number().over(
                partition_by=TeamProfilePrediction.match_id,
                order_by=(profile_priority.asc(), TeamProfilePrediction.created_at.desc()),
            ).label("rn"),
        )
        .join(Match, TeamProfilePrediction.match_id == Match.id)
        .where(
            Match.status == "final",
            TeamProfilePrediction.match_id.in_(match_ids),
        )
        .subquery()
    )
    profiles_by_match = {
        profile.match_id: profile
        for profile in session.scalars(
            select(TeamProfilePrediction)
            .options(
                load_only(
                    TeamProfilePrediction.match_id,
                    TeamProfilePrediction.home_win,
                    TeamProfilePrediction.draw,
                    TeamProfilePrediction.away_win,
                    TeamProfilePrediction.triggered_traits_json,
                    TeamProfilePrediction.risk_flags_json,
                    TeamProfilePrediction.explanation,
                )
            )
            .join(profile_ranked, TeamProfilePrediction.id == profile_ranked.c.prediction_id)
            .where(
                profile_ranked.c.rn == 1,
                profile_ranked.c.priority < 3,
            )
        )
    }

    for match in finals:
        baseline = baselines_by_match.get(match.id)
        profile = profiles_by_match.get(match.id)
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
