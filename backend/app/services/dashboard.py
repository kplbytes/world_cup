from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.prediction.shadow import SHADOW_MODEL_VERSIONS
from app.models import (
    DashboardRevision,
    DataSnapshot,
    MarketSnapshot,
    Match,
    MatchPrediction,
    PredictionSnapshot,
    QualificationPrediction,
    StandingSnapshot,
    SyncRun,
    Team,
    TeamRating,
    MatchIntelligence,
    AutoAdjustment,
    ProviderQuotaState,
    TeamProfilePrediction,
    AIPrediction,
    EnsemblePrediction,
)
from app.services.market import compute_divergence
from app.services.localization import localized_team_names
from app.services.manual_adjustments import adjustments_by_match, list_manual_adjustments, serialize_adjustment
from app.services.scoring import score_predictions

from typing import Any

import math



SHANGHAI = ZoneInfo("Asia/Shanghai")


def _china_time_fields(kickoff_dt: datetime) -> dict:
    """Add China-time helper fields for a match kickoff."""
    if kickoff_dt.tzinfo is None:
        kickoff_dt = kickoff_dt.replace(tzinfo=timezone.utc)
    local_kickoff = kickoff_dt.astimezone(SHANGHAI)
    china_date_key = local_kickoff.strftime("%Y-%m-%d")
    now_utc = datetime.now(timezone.utc)
    now_shanghai = now_utc.astimezone(SHANGHAI)
    today_key = now_shanghai.strftime("%Y-%m-%d")
    is_today_china = china_date_key == today_key
    is_next_48h = (kickoff_dt >= now_utc) and (kickoff_dt <= now_utc + timedelta(hours=48))
    return {
        "kickoff_china": local_kickoff.strftime("%Y-%m-%d %H:%M"),
        "china_date_key": china_date_key,
        "is_today_china": is_today_china,
        "is_next_48h": is_next_48h,
    }


def decision_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _display_snapshots(session: Session, matches: list[Match]) -> dict[str, PredictionSnapshot]:
    if not matches:
        return {}
    matches_by_id = {match.id: match for match in matches}
    grouped: dict[str, list[PredictionSnapshot]] = defaultdict(list)
    for snapshot in session.scalars(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.match_id.in_(matches_by_id))
        .order_by(PredictionSnapshot.snapshotted_at.desc())
    ):
        grouped[snapshot.match_id].append(snapshot)

    selected = {}
    for match_id, snapshots in grouped.items():
        kickoff = _as_utc(matches_by_id[match_id].kickoff)
        pre_kickoff = [
            snapshot
            for snapshot in snapshots
            if _as_utc(snapshot.snapshotted_at) < kickoff
        ]
        selected[match_id] = pre_kickoff[0] if pre_kickoff else snapshots[0]
    return selected


def _snapshot_status(snapshot: PredictionSnapshot | None, match: Match) -> dict:
    is_pre_kickoff = bool(
        snapshot and _as_utc(snapshot.snapshotted_at) < _as_utc(match.kickoff)
    )
    return {
        "locked": is_pre_kickoff,
        "locked_at": _as_utc(snapshot.snapshotted_at).isoformat() if snapshot else None,
        "is_fallback": snapshot.is_fallback_locked if snapshot else False,
        "participates_in_model_score": is_pre_kickoff,
        "real_time_only": bool(snapshot and not is_pre_kickoff),
    }


_CLIP = 1e-6


def _compute_brier(p_home: float, p_draw: float, p_away: float,
                   o_home: float, o_draw: float, o_away: float) -> float:
    """Three-class Brier score for a single match."""
    return (p_home - o_home) ** 2 + (p_draw - o_draw) ** 2 + (p_away - o_away) ** 2


def _compute_deviations(p_home: float, p_draw: float, p_away: float,
                        o_home: float, o_draw: float, o_away: float) -> dict[str, float]:
    """Probability deviation = predicted - actual for each class."""
    return {
        "home_win": round(p_home - o_home, 4),
        "draw": round(p_draw - o_draw, 4),
        "away_win": round(p_away - o_away, 4),
    }


def _compute_match_review(
    match: Match,
    snapshot: PredictionSnapshot | None,
    ai_preds: list[AIPrediction],
    ensemble_preds: list[EnsemblePrediction],
    market_snap: MarketSnapshot | None,
) -> dict | None:
    """Compute per-match review data for a finished match.

    Returns None if the match is not finished or lacks scores.
    """
    if match.status != "final" or match.home_score is None or match.away_score is None:
        return None

    # Determine actual outcome indicators
    actual_home = match.home_score
    actual_away = match.away_score
    if actual_home > actual_away:
        o_home, o_draw, o_away = 1.0, 0.0, 0.0
        actual_result = "home"
    elif actual_home == actual_away:
        o_home, o_draw, o_away = 0.0, 1.0, 0.0
        actual_result = "draw"
    else:
        o_home, o_draw, o_away = 0.0, 0.0, 1.0
        actual_result = "away"

    result: dict[str, Any] = {
        "actual_result": actual_result,
        "actual_score": {"home": actual_home, "away": actual_away},
    }

    # Map actual_result to the probability key for lookup
    actual_prob_key = {"home": "home_win", "draw": "draw", "away": "away_win"}[actual_result]

    # --- Baseline review (from snapshot) ---
    if snapshot:
        b_home = snapshot.base_home_win if snapshot.base_home_win is not None else snapshot.home_win
        b_draw = snapshot.base_draw if snapshot.base_draw is not None else snapshot.draw
        b_away = snapshot.base_away_win if snapshot.base_away_win is not None else snapshot.away_win
        baseline_predicted = max(
            [("home", b_home), ("draw", b_draw), ("away", b_away)],
            key=lambda x: x[1],
        )[0]
        baseline_brier = _compute_brier(b_home, b_draw, b_away, o_home, o_draw, o_away)
        baseline_actual_prob = {"home_win": b_home, "draw": b_draw, "away_win": b_away}[actual_prob_key]
        result["baseline"] = {
            "predicted_result": baseline_predicted,
            "outcome_hit": baseline_predicted == actual_result,
            "brier": round(baseline_brier, 4),
            "actual_probability": round(baseline_actual_prob, 4),
            "probabilities": {"home_win": b_home, "draw": b_draw, "away_win": b_away},
            "deviations": _compute_deviations(b_home, b_draw, b_away, o_home, o_draw, o_away),
        }
    else:
        result["baseline"] = None

    # --- AI review ---
    if ai_preds:
        # Use the first effective AI prediction (no error, has parsed probs)
        effective_ai = None
        for ai in ai_preds:
            if ai.error_code is None and ai.parsed_home_win is not None:
                effective_ai = ai
                break
        if effective_ai:
            a_home = effective_ai.parsed_home_win
            a_draw = effective_ai.parsed_draw
            a_away = effective_ai.parsed_away_win
            ai_predicted = max(
                [("home", a_home), ("draw", a_draw), ("away", a_away)],
                key=lambda x: x[1],
            )[0]
            ai_brier = _compute_brier(a_home, a_draw, a_away, o_home, o_draw, o_away)
            ai_actual_prob = {"home_win": a_home, "draw": a_draw, "away_win": a_away}[actual_prob_key]
            result["ai"] = {
                "predicted_result": ai_predicted,
                "outcome_hit": ai_predicted == actual_result,
                "brier": round(ai_brier, 4),
                "actual_probability": round(ai_actual_prob, 4),
                "probabilities": {"home_win": a_home, "draw": a_draw, "away_win": a_away},
                "deviations": _compute_deviations(a_home, a_draw, a_away, o_home, o_draw, o_away),
                "model_version": effective_ai.model_version,
            }
        else:
            result["ai"] = None
    else:
        result["ai"] = None

    # --- Ensemble review ---
    if ensemble_preds:
        ens = ensemble_preds[0]
        e_home = ens.ensemble_home_win
        e_draw = ens.ensemble_draw
        e_away = ens.ensemble_away_win
        ens_predicted = max(
            [("home", e_home), ("draw", e_draw), ("away", e_away)],
            key=lambda x: x[1],
        )[0]
        ens_brier = _compute_brier(e_home, e_draw, e_away, o_home, o_draw, o_away)
        ens_actual_prob = {"home_win": e_home, "draw": e_draw, "away_win": e_away}[actual_prob_key]
        result["ensemble"] = {
            "predicted_result": ens_predicted,
            "outcome_hit": ens_predicted == actual_result,
            "brier": round(ens_brier, 4),
            "actual_probability": round(ens_actual_prob, 4),
            "probabilities": {"home_win": e_home, "draw": e_draw, "away_win": e_away},
            "deviations": _compute_deviations(e_home, e_draw, e_away, o_home, o_draw, o_away),
            "model_version": ens.model_version,
        }
    else:
        result["ensemble"] = None

    # --- Market review (for reference) ---
    if market_snap and market_snap.home_probability is not None:
        m_home = market_snap.home_probability
        m_draw = market_snap.draw_probability
        m_away = market_snap.away_probability
        m_predicted = max(
            [("home", m_home), ("draw", m_draw), ("away", m_away)],
            key=lambda x: x[1],
        )[0]
        m_brier = _compute_brier(m_home, m_draw, m_away, o_home, o_draw, o_away)
        m_actual_prob = {"home_win": m_home, "draw": m_draw, "away_win": m_away}[actual_prob_key]
        result["market"] = {
            "predicted_result": m_predicted,
            "outcome_hit": m_predicted == actual_result,
            "brier": round(m_brier, 4),
            "actual_probability": round(m_actual_prob, 4),
            "probabilities": {"home_win": m_home, "draw": m_draw, "away_win": m_away},
            "deviations": _compute_deviations(m_home, m_draw, m_away, o_home, o_draw, o_away),
        }
    else:
        result["market"] = None

    # --- Top-level convenience fields ---
    # winner_hit: prefer Ensemble, fallback to Baseline
    if result.get("ensemble"):
        result["winner_hit"] = result["ensemble"]["outcome_hit"]
    elif result.get("baseline"):
        result["winner_hit"] = result["baseline"]["outcome_hit"]
    else:
        result["winner_hit"] = None

    # best_model: lowest Brier score among available sources
    brier_scores: list[tuple[str, float]] = []
    for source in ("baseline", "ai", "ensemble"):
        src = result.get(source)
        if src:
            brier_scores.append((source, src["brier"]))
    result["best_model"] = min(brier_scores, key=lambda x: x[1])[0] if brier_scores else None

    return result


def build_dashboard(session: Session) -> dict:
    revision = session.scalar(
        select(DashboardRevision)
        .where(DashboardRevision.active.is_(True))
        .order_by(DashboardRevision.id.desc())
        .limit(1)
    )
    if revision is None:
        raise LookupError("dashboard has not been computed")

    teams = list(session.scalars(select(Team).order_by(Team.group_code, Team.id)))
    matches = list(session.scalars(select(Match).order_by(Match.kickoff, Match.id)))
    standings = {
        row.team_id: row
        for row in session.scalars(
            select(StandingSnapshot).where(StandingSnapshot.revision_id == revision.id)
        )
    }
    qualifications = {
        row.team_id: row
        for row in session.scalars(
            select(QualificationPrediction).where(
                QualificationPrediction.revision_id == revision.id
            )
        )
    }
    predictions = {
        row.match_id: row
        for row in session.scalars(
            select(MatchPrediction).where(
                MatchPrediction.revision_id == revision.id,
                MatchPrediction.model_version.notin_(SHADOW_MODEL_VERSIONS),
            )
        )
    }
    market_snaps = {
        row.match_id: row
        for row in session.scalars(
            select(MarketSnapshot).where(MarketSnapshot.provider == "sporttery")
        )
    }

    intelligences_by_match = defaultdict(list)
    for row in session.scalars(select(MatchIntelligence)):
        intelligences_by_match[row.match_id].append(row)

    adjustments_by_match_auto = defaultdict(list)
    seen_auto_adjs = set()
    for row in session.scalars(select(AutoAdjustment).order_by(AutoAdjustment.id.desc())):
        sig = (row.match_id, row.adjustment_type, row.affected_team_id, row.reason)
        if sig not in seen_auto_adjs:
            seen_auto_adjs.add(sig)
            adjustments_by_match_auto[row.match_id].append(row)

    snapshots = _display_snapshots(session, matches)

    # Pre-fetch AI and Ensemble predictions for all matches (used in review + match card)
    all_match_ids = [m.id for m in matches]
    ai_preds_by_match: dict[str, list[AIPrediction]] = defaultdict(list)
    for row in session.scalars(
        select(AIPrediction)
        .where(AIPrediction.match_id.in_(all_match_ids))
        .order_by(AIPrediction.created_at.desc())
    ):
        ai_preds_by_match[row.match_id].append(row)

    ensemble_preds_by_match: dict[str, list[EnsemblePrediction]] = defaultdict(list)
    for row in session.scalars(
        select(EnsemblePrediction)
        .where(EnsemblePrediction.match_id.in_(all_match_ids))
        .order_by(EnsemblePrediction.created_at.desc())
    ):
        ensemble_preds_by_match[row.match_id].append(row)

    ratings = _latest_ratings(session)
    teams_by_id = {team.id: team for team in teams}
    display_names = localized_team_names(session, teams)
    manual_adjustments = {
        match_id: [serialize_adjustment(item, display_names) for item in items]
        for match_id, items in adjustments_by_match(session).items()
    }
    teams_by_group = defaultdict(list)
    matches_by_group = defaultdict(list)
    for team in teams:
        standing = standings[team.id]
        qualification = qualifications[team.id]
        rating = ratings[team.id]
        teams_by_group[team.group_code].append(
            {
                "id": team.id,
                "name": display_names[team.id],
                "short_name": display_names[team.id],
                "code": team.code,
                "flag": team.flag_url,
                "elo": round(rating.elo),
                "fifa_rank": rating.fifa_rank,
                "fifa_points": rating.fifa_points,
                "recent_form": rating.recent_form,
                "standing": _standing_dict(standing),
                "qualification": _qualification_dict(qualification),
            }
        )
    for match in matches:
        prediction = predictions.get(match.id)
        market_snap = market_snaps.get(match.id)
        market_data = None
        if market_snap and prediction:
            div = compute_divergence(
                {"home_win": prediction.home_win, "draw": prediction.draw, "away_win": prediction.away_win},
                market_snap,
            )
            market_data = {
                "home_probability": market_snap.home_probability,
                "draw_probability": market_snap.draw_probability,
                "away_probability": market_snap.away_probability,
                "raw_overround": market_snap.raw_overround,
                "divergence": {
                    "home_diff": div.home_diff,
                    "draw_diff": div.draw_diff,
                    "away_diff": div.away_diff,
                    "max_divergence": div.max_divergence,
                    "level": div.level,
                },
            }
        elif market_snap:
            market_data = {
                "home_probability": market_snap.home_probability,
                "draw_probability": market_snap.draw_probability,
                "away_probability": market_snap.away_probability,
                "raw_overround": market_snap.raw_overround,
                "divergence": None,
            }
        snap = snapshots.get(match.id)
        snapshot_status = _snapshot_status(snap, match)

        matches_by_group[match.group_code].append(
            {
                **_china_time_fields(match.kickoff),
                "id": match.id,
                "group_code": match.group_code,
                "kickoff": _as_utc(match.kickoff).isoformat(),
                "venue": match.venue,
                "status": match.status,
                "home_team": _team_ref(teams_by_id[match.home_team_id], display_names),
                "away_team": _team_ref(teams_by_id[match.away_team_id], display_names),
                "home_score": match.home_score,
                "away_score": match.away_score,
                "manual_adjustments": manual_adjustments.get(match.id, []),
                "intelligence": [
                    {
                        "type": intel.intelligence_type,
                        "provider": intel.provider,
                        "confidence": intel.source_confidence,
                        "fetched_at": _as_utc(intel.fetched_at).isoformat(),
                        "payload": intel.normalized_payload,
                    }
                    for intel in intelligences_by_match[match.id]
                ],
                "auto_adjustments": [
                    {
                        "type": adj.adjustment_type,
                        "affected_team_id": adj.affected_team_id,
                        "confidence_penalty": adj.confidence,
                        "reason": adj.reason,
                        "source_intelligence_ids": adj.source_intelligence_ids,
                    }
                    for adj in adjustments_by_match_auto[match.id]
                    if adj.adjustment_type != "numerical_roster_adjustment"
                ],
                "numerical_adjustments": [
                    {
                        "type": adj.adjustment_type,
                        "affected_team_id": adj.affected_team_id,
                        "attack_delta": adj.attack_delta,
                        "defense_delta": adj.defense_delta,
                        "reason": adj.reason,
                    }
                    for adj in adjustments_by_match_auto[match.id]
                    if adj.adjustment_type == "numerical_roster_adjustment"
                ],
                "numerical_delta_summary": {
                    "home_attack_delta": sum(adj.attack_delta for adj in adjustments_by_match_auto[match.id] if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == match.home_team_id),
                    "home_defense_delta": sum(adj.defense_delta for adj in adjustments_by_match_auto[match.id] if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == match.home_team_id),
                    "away_attack_delta": sum(adj.attack_delta for adj in adjustments_by_match_auto[match.id] if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == match.away_team_id),
                    "away_defense_delta": sum(adj.defense_delta for adj in adjustments_by_match_auto[match.id] if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == match.away_team_id),
                },
                "numerical_enabled": getattr(revision, 'model_version', '') == 'elo-poisson-v1-intel-numeric',
                "model_version": getattr(revision, 'model_version', ''),
                "risk_flags": list(set(adj.adjustment_type for adj in adjustments_by_match_auto[match.id] if adj.adjustment_type != "numerical_roster_adjustment")),
                "snapshot_status": snapshot_status,
                "prediction": _prediction_dict(prediction) if prediction else None,
                "market": market_data,
                "source": match.source,
                "source_updated_at": (
                    _as_utc(match.source_updated_at).isoformat() if match.source_updated_at else None
                ),
                # P0-4: Result sync metadata for finished matches
                "result_source": match.source if match.status == "final" else None,
                "result_synced_at": (
                    _as_utc(match.source_updated_at).isoformat() if match.source_updated_at and match.status == "final" else None
                ),
                "revision_id": revision.id,
                # AI prediction summary (for MatchCard / P0-3 fix)
                "ai_prediction": _ai_prediction_summary(ai_preds_by_match[match.id]),
                # Ensemble prediction summary (for MatchCard / P0-3 fix)
                "ensemble_prediction": _ensemble_prediction_summary(ensemble_preds_by_match[match.id]),
                # Per-match review data for finished matches (P0-2)
                "match_review": _compute_match_review(
                    match, snap, ai_preds_by_match[match.id],
                    ensemble_preds_by_match[match.id], market_snap,
                ),
            }
        )

    # Compute next_match: the earliest scheduled match with kickoff > now
    now_utc = datetime.now(timezone.utc)
    scheduled_matches = [
        m for m in matches
        if m.status == "scheduled" and m.kickoff and _as_utc(m.kickoff) > now_utc
    ]
    scheduled_matches.sort(key=lambda m: (_as_utc(m.kickoff), m.id))
    next_match = scheduled_matches[0] if scheduled_matches else None

    return {
        "revision": {
            "id": revision.id,
            "created_at": _as_utc(revision.created_at).isoformat(),
            "model_version": revision.model_version,
            "simulation_iterations": revision.simulation_iterations,
            "simulation_seed": revision.simulation_seed,
        },
        "groups": [
            {
                "code": group,
                "name": f"Group {group}",
                "teams": sorted(
                    teams_by_group[group], key=lambda item: item["standing"]["position"]
                ),
                "matches": matches_by_group[group],
            }
            for group in "ABCDEFGHIJKL"
        ],
        "data_sources": list_data_sources(session) + list_intelligence_providers(session),
        "next_match": {
            "id": next_match.id,
            "home_team": _team_ref(teams_by_id[next_match.home_team_id], display_names),
            "away_team": _team_ref(teams_by_id[next_match.away_team_id], display_names),
            "kickoff": _as_utc(next_match.kickoff).isoformat() if next_match.kickoff else None,
            "kickoff_china": _china_time_fields(next_match.kickoff)["kickoff_china"] if next_match and next_match.kickoff else None,
            "venue": next_match.venue,
        } if next_match else None,
        "last_updated": _as_utc(revision.created_at).isoformat() if revision and revision.created_at else None,
        "data_age_minutes": round((now_utc - _as_utc(revision.created_at)).total_seconds() / 60) if revision and revision.created_at else None,
        "current_time_utc": now_utc.isoformat(),
        "current_time_china": now_utc.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M"),
    }


def build_match_detail(session: Session, match_id: str) -> dict | None:
    """Build detail for a single match using direct DB queries instead of full dashboard."""
    match = session.get(Match, match_id)
    if match is None:
        return None

    revision = session.scalar(
        select(DashboardRevision)
        .where(DashboardRevision.active.is_(True))
        .order_by(DashboardRevision.id.desc())
        .limit(1)
    )
    if revision is None:
        return None

    # Only fetch data related to this match
    prediction = session.scalar(
        select(MatchPrediction)
        .where(MatchPrediction.revision_id == revision.id)
        .where(MatchPrediction.match_id == match_id)
        .where(MatchPrediction.model_version.notin_(SHADOW_MODEL_VERSIONS))
    ) if revision else None

    market_snap = session.scalar(
        select(MarketSnapshot)
        .where(MarketSnapshot.provider == "sporttery")
        .where(MarketSnapshot.match_id == match_id)
    )

    snap = _display_snapshots(session, [match]).get(match_id)

    intelligences = list(session.scalars(
        select(MatchIntelligence).where(MatchIntelligence.match_id == match_id)
    ))

    # Auto adjustments for this match (deduplicated)
    auto_adjs_raw = list(session.scalars(
        select(AutoAdjustment)
        .where(AutoAdjustment.match_id == match_id)
        .order_by(AutoAdjustment.id.desc())
    ))
    seen_auto = set()
    auto_adjs = []
    for adj in auto_adjs_raw:
        sig = (adj.match_id, adj.adjustment_type, adj.affected_team_id, adj.reason)
        if sig not in seen_auto:
            seen_auto.add(sig)
            auto_adjs.append(adj)

    # Manual adjustments for this match
    home_team = session.get(Team, match.home_team_id)
    away_team = session.get(Team, match.away_team_id)
    teams_list = [t for t in (home_team, away_team) if t]
    display_names = localized_team_names(session, teams_list)
    manual_adjs = list_manual_adjustments(session, match_id=match_id)
    manual_adjustments = [serialize_adjustment(a, display_names) for a in manual_adjs]
    from app.team_profiles.service import explain_team_profile, get_team_profile, profile_payload
    home_profile = get_team_profile(session, match.home_team_id, match.kickoff)
    away_profile = get_team_profile(session, match.away_team_id, match.kickoff)
    profile_prediction = session.scalar(
        select(TeamProfilePrediction)
        .where(TeamProfilePrediction.match_id == match_id)
        .order_by(TeamProfilePrediction.created_at.desc())
        .limit(1)
    )

    # Build market data
    market_data = None
    if market_snap and prediction:
        div = compute_divergence(
            {"home_win": prediction.home_win, "draw": prediction.draw, "away_win": prediction.away_win},
            market_snap,
        )
        market_data = {
            "home_probability": market_snap.home_probability,
            "draw_probability": market_snap.draw_probability,
            "away_probability": market_snap.away_probability,
            "raw_overround": market_snap.raw_overround,
            "divergence": {
                "home_diff": div.home_diff,
                "draw_diff": div.draw_diff,
                "away_diff": div.away_diff,
                "max_divergence": div.max_divergence,
                "level": div.level,
            },
        }
    elif market_snap:
        market_data = {
            "home_probability": market_snap.home_probability,
            "draw_probability": market_snap.draw_probability,
            "away_probability": market_snap.away_probability,
            "raw_overround": market_snap.raw_overround,
            "divergence": None,
        }

    snapshot_status = _snapshot_status(snap, match)

    teams_by_id = {t.id: t for t in teams_list}

    return {
        **_china_time_fields(match.kickoff),
        "id": match.id,
        "group_code": match.group_code,
        "kickoff": _as_utc(match.kickoff).isoformat(),
        "venue": match.venue,
        "status": match.status,
        "home_team": _team_ref(teams_by_id[match.home_team_id], display_names) if match.home_team_id in teams_by_id else None,
        "away_team": _team_ref(teams_by_id[match.away_team_id], display_names) if match.away_team_id in teams_by_id else None,
        "home_score": match.home_score,
        "away_score": match.away_score,
        "manual_adjustments": manual_adjustments,
        "intelligence": [
            {
                "type": intel.intelligence_type,
                "provider": intel.provider,
                "confidence": intel.source_confidence,
                "fetched_at": _as_utc(intel.fetched_at).isoformat(),
                "payload": intel.normalized_payload,
            }
            for intel in intelligences
        ],
        "auto_adjustments": [
            {
                "type": adj.adjustment_type,
                "affected_team_id": adj.affected_team_id,
                "confidence_penalty": adj.confidence,
                "reason": adj.reason,
                "source_intelligence_ids": adj.source_intelligence_ids,
            }
            for adj in auto_adjs
            if adj.adjustment_type != "numerical_roster_adjustment"
        ],
        "numerical_adjustments": [
            {
                "type": adj.adjustment_type,
                "affected_team_id": adj.affected_team_id,
                "attack_delta": adj.attack_delta,
                "defense_delta": adj.defense_delta,
                "reason": adj.reason,
            }
            for adj in auto_adjs
            if adj.adjustment_type == "numerical_roster_adjustment"
        ],
        "numerical_delta_summary": {
            "home_attack_delta": sum(adj.attack_delta for adj in auto_adjs if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == match.home_team_id),
            "home_defense_delta": sum(adj.defense_delta for adj in auto_adjs if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == match.home_team_id),
            "away_attack_delta": sum(adj.attack_delta for adj in auto_adjs if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == match.away_team_id),
            "away_defense_delta": sum(adj.defense_delta for adj in auto_adjs if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == match.away_team_id),
        },
        "numerical_enabled": getattr(revision, 'model_version', '') == 'elo-poisson-v1-intel-numeric',
        "model_version": getattr(revision, 'model_version', ''),
        "risk_flags": list(set(adj.adjustment_type for adj in auto_adjs if adj.adjustment_type != "numerical_roster_adjustment")),
        "snapshot_status": snapshot_status,
        "prediction": _prediction_dict(prediction) if prediction else None,
        "market": market_data,
        "source": match.source,
        "source_updated_at": (
            _as_utc(match.source_updated_at).isoformat() if match.source_updated_at else None
        ),
        # P0-4: Result sync metadata for finished matches
        "result_source": match.source if match.status == "final" else None,
        "result_synced_at": (
            _as_utc(match.source_updated_at).isoformat() if match.source_updated_at and match.status == "final" else None
        ),
        "revision_id": revision.id,
        "team_profiles": {
            "home": {"profile": profile_payload(home_profile), "summary": explain_team_profile(home_profile)} if home_profile else None,
            "away": {"profile": profile_payload(away_profile), "summary": explain_team_profile(away_profile)} if away_profile else None,
        },
        "profile_prediction": ({
            "model_version": profile_prediction.model_version,
            "profile_version": profile_prediction.profile_version,
            "profile_as_of": _as_utc(profile_prediction.profile_as_of).isoformat(),
            "home_win": profile_prediction.home_win,
            "draw": profile_prediction.draw,
            "away_win": profile_prediction.away_win,
            "home_xg": profile_prediction.home_xg,
            "away_xg": profile_prediction.away_xg,
            "probability_deltas": profile_prediction.probability_deltas_json,
            "xg_deltas": profile_prediction.xg_deltas_json,
            "risk_flags": profile_prediction.risk_flags_json,
            "triggered_traits": profile_prediction.triggered_traits_json,
            "explanation": profile_prediction.explanation,
            "is_pre_match_locked": profile_prediction.is_pre_match_locked,
        } if profile_prediction else None),
        # AI/Ensemble prediction summaries (P0-3 fix)
        "ai_prediction": _ai_prediction_summary(list(session.scalars(
            select(AIPrediction)
            .where(AIPrediction.match_id == match_id)
            .order_by(AIPrediction.created_at.desc())
        ))),
        "ensemble_prediction": _ensemble_prediction_summary(list(session.scalars(
            select(EnsemblePrediction)
            .where(EnsemblePrediction.match_id == match_id)
            .order_by(EnsemblePrediction.created_at.desc())
        ))),
        # Per-match review data (P0-2)
        "match_review": _compute_match_review(
            match, snap,
            list(session.scalars(
                select(AIPrediction)
                .where(AIPrediction.match_id == match_id)
                .order_by(AIPrediction.created_at.desc())
            )),
            list(session.scalars(
                select(EnsemblePrediction)
                .where(EnsemblePrediction.match_id == match_id)
                .order_by(EnsemblePrediction.created_at.desc())
            )),
            market_snap,
        ),
    }


def build_team_detail(session: Session, team_id: str) -> dict | None:
    """Build detail for a single team using direct DB queries instead of full dashboard."""
    team = session.get(Team, team_id)
    if team is None:
        return None

    revision = session.scalar(
        select(DashboardRevision)
        .where(DashboardRevision.active.is_(True))
        .order_by(DashboardRevision.id.desc())
        .limit(1)
    )
    if revision is None:
        return None

    # Only fetch data related to this team
    standing = session.scalar(
        select(StandingSnapshot)
        .where(StandingSnapshot.revision_id == revision.id)
        .where(StandingSnapshot.team_id == team_id)
    )
    qualification = session.scalar(
        select(QualificationPrediction)
        .where(QualificationPrediction.revision_id == revision.id)
        .where(QualificationPrediction.team_id == team_id)
    )
    rating = session.scalar(
        select(TeamRating)
        .where(TeamRating.team_id == team_id)
        .order_by(TeamRating.effective_date.desc(), TeamRating.id.desc())
        .limit(1)
    )

    # Get matches for this team's group
    group_matches = list(session.scalars(
        select(Match)
        .where(Match.group_code == team.group_code)
        .order_by(Match.kickoff, Match.id)
    ))

    # Build display names for teams in this group
    team_ids = {team_id}
    for m in group_matches:
        if m.home_team_id:
            team_ids.add(m.home_team_id)
        if m.away_team_id:
            team_ids.add(m.away_team_id)
    group_teams = list(session.scalars(
        select(Team).where(Team.id.in_(team_ids))
    ))
    display_names = localized_team_names(session, group_teams)
    teams_by_id = {t.id: t for t in group_teams}

    # Predictions and market snaps for group matches
    predictions = {
        row.match_id: row
        for row in session.scalars(
            select(MatchPrediction)
            .where(MatchPrediction.revision_id == revision.id)
            .where(MatchPrediction.match_id.in_([m.id for m in group_matches]))
            .where(MatchPrediction.model_version.notin_(SHADOW_MODEL_VERSIONS))
        )
    } if group_matches else {}

    market_snaps = {
        row.match_id: row
        for row in session.scalars(
            select(MarketSnapshot)
            .where(MarketSnapshot.provider == "sporttery")
            .where(MarketSnapshot.match_id.in_([m.id for m in group_matches]))
        )
    } if group_matches else {}

    snapshots = _display_snapshots(session, group_matches)

    intelligences_by_match = defaultdict(list)
    for row in session.scalars(
        select(MatchIntelligence)
        .where(MatchIntelligence.match_id.in_([m.id for m in group_matches]))
    ):
        intelligences_by_match[row.match_id].append(row)

    # Auto adjustments for group matches
    adjustments_by_match_auto = defaultdict(list)
    if group_matches:
        match_ids = [m.id for m in group_matches]
        auto_adjs_raw = list(session.scalars(
            select(AutoAdjustment)
            .where(AutoAdjustment.match_id.in_(match_ids))
            .order_by(AutoAdjustment.id.desc())
        ))
        seen_auto = set()
        for adj in auto_adjs_raw:
            sig = (adj.match_id, adj.adjustment_type, adj.affected_team_id, adj.reason)
            if sig not in seen_auto:
                seen_auto.add(sig)
                adjustments_by_match_auto[adj.match_id].append(adj)

    # Manual adjustments for group matches
    manual_adjustments = {
        match_id: [serialize_adjustment(item, display_names) for item in items]
        for match_id, items in adjustments_by_match(session).items()
        if match_id in {m.id for m in group_matches}
    }

    # Build team dict
    if not rating or not standing:
        return None

    team_dict = {
        "id": team.id,
        "name": display_names[team.id],
        "short_name": display_names[team.id],
        "code": team.code,
        "flag": team.flag_url,
        "elo": round(rating.elo),
        "fifa_rank": rating.fifa_rank,
        "fifa_points": rating.fifa_points,
        "recent_form": rating.recent_form,
        "standing": _standing_dict(standing),
        "qualification": _qualification_dict(qualification) if qualification else None,
    }

    # Build match dicts for this team
    team_matches = []
    for m in group_matches:
        if team_id not in (m.home_team_id, m.away_team_id):
            continue
        pred = predictions.get(m.id)
        market_snap = market_snaps.get(m.id)
        market_data = None
        if market_snap and pred:
            div = compute_divergence(
                {"home_win": pred.home_win, "draw": pred.draw, "away_win": pred.away_win},
                market_snap,
            )
            market_data = {
                "home_probability": market_snap.home_probability,
                "draw_probability": market_snap.draw_probability,
                "away_probability": market_snap.away_probability,
                "raw_overround": market_snap.raw_overround,
                "divergence": {
                    "home_diff": div.home_diff,
                    "draw_diff": div.draw_diff,
                    "away_diff": div.away_diff,
                    "max_divergence": div.max_divergence,
                    "level": div.level,
                },
            }
        elif market_snap:
            market_data = {
                "home_probability": market_snap.home_probability,
                "draw_probability": market_snap.draw_probability,
                "away_probability": market_snap.away_probability,
                "raw_overround": market_snap.raw_overround,
                "divergence": None,
            }
        snap = snapshots.get(m.id)
        snapshot_status = _snapshot_status(snap, m)
        team_matches.append({
            **_china_time_fields(m.kickoff),
            "id": m.id,
            "group_code": m.group_code,
            "kickoff": _as_utc(m.kickoff).isoformat(),
            "venue": m.venue,
            "status": m.status,
            "home_team": _team_ref(teams_by_id[m.home_team_id], display_names) if m.home_team_id in teams_by_id else None,
            "away_team": _team_ref(teams_by_id[m.away_team_id], display_names) if m.away_team_id in teams_by_id else None,
            "home_score": m.home_score,
            "away_score": m.away_score,
            "manual_adjustments": manual_adjustments.get(m.id, []),
            "intelligence": [
                {
                    "type": intel.intelligence_type,
                    "provider": intel.provider,
                    "confidence": intel.source_confidence,
                    "fetched_at": _as_utc(intel.fetched_at).isoformat(),
                    "payload": intel.normalized_payload,
                }
                for intel in intelligences_by_match[m.id]
            ],
            "auto_adjustments": [
                {
                    "type": adj.adjustment_type,
                    "affected_team_id": adj.affected_team_id,
                    "confidence_penalty": adj.confidence,
                    "reason": adj.reason,
                    "source_intelligence_ids": adj.source_intelligence_ids,
                }
                for adj in adjustments_by_match_auto[m.id]
                if adj.adjustment_type != "numerical_roster_adjustment"
            ],
            "numerical_adjustments": [
                {
                    "type": adj.adjustment_type,
                    "affected_team_id": adj.affected_team_id,
                    "attack_delta": adj.attack_delta,
                    "defense_delta": adj.defense_delta,
                    "reason": adj.reason,
                }
                for adj in adjustments_by_match_auto[m.id]
                if adj.adjustment_type == "numerical_roster_adjustment"
            ],
            "numerical_delta_summary": {
                "home_attack_delta": sum(adj.attack_delta for adj in adjustments_by_match_auto[m.id] if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == m.home_team_id),
                "home_defense_delta": sum(adj.defense_delta for adj in adjustments_by_match_auto[m.id] if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == m.home_team_id),
                "away_attack_delta": sum(adj.attack_delta for adj in adjustments_by_match_auto[m.id] if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == m.away_team_id),
                "away_defense_delta": sum(adj.defense_delta for adj in adjustments_by_match_auto[m.id] if adj.adjustment_type == "numerical_roster_adjustment" and adj.affected_team_id == m.away_team_id),
            },
            "numerical_enabled": getattr(revision, 'model_version', '') == 'elo-poisson-v1-intel-numeric',
            "model_version": getattr(revision, 'model_version', ''),
            "risk_flags": list(set(adj.adjustment_type for adj in adjustments_by_match_auto[m.id] if adj.adjustment_type != "numerical_roster_adjustment")),
            "snapshot_status": snapshot_status,
            "prediction": _prediction_dict(pred) if pred else None,
            "market": market_data,
            "source": m.source,
            "source_updated_at": (
                _as_utc(m.source_updated_at).isoformat() if m.source_updated_at else None
            ),
        })

    from app.team_profiles.service import explain_team_profile, get_team_profile, profile_payload
    team_profile = get_team_profile(session, team_id)
    return {
        **team_dict,
        "group_code": team.group_code,
        "matches": team_matches,
        "team_profile": ({"profile": profile_payload(team_profile), "summary": explain_team_profile(team_profile)} if team_profile else None),
    }


def list_data_sources(session: Session) -> list[dict]:
    snapshots = list(
        session.scalars(select(DataSnapshot).order_by(DataSnapshot.fetched_at.desc()))
    )
    latest = {}
    for snapshot in snapshots:
        latest.setdefault(snapshot.provider, snapshot)
    return [
        {
            "provider": snapshot.provider,
            "source_url": snapshot.source_url,
            "fetched_at": _as_utc(snapshot.fetched_at).isoformat(),
            "status": snapshot.status,
            "coverage": snapshot.coverage,
            "error": snapshot.error,
        }
        for snapshot in latest.values()
    ]


def list_intelligence_providers(session: Session) -> list[dict]:
    states = {row.provider: row for row in session.scalars(select(ProviderQuotaState))}
    providers = []

    # sporttery
    sporttery_state = states.get("sporttery")
    providers.append({
        "provider": "sporttery",
        "status": "enabled",
        "daily_limit": sporttery_state.daily_limit if sporttery_state else 5000,
        "used_today": sporttery_state.used_today if sporttery_state else 0,
        "last_success_at": _as_utc(sporttery_state.updated_at).isoformat() if sporttery_state else None,
        "error": None
    })

    # api-football
    api_football_state = states.get("api-football")
    token = settings.api_football_token
    status = "enabled"
    error = None
    if not token:
        status = "disabled_no_token"
    elif api_football_state and api_football_state.used_today >= api_football_state.daily_limit:
        status = "quota_limited"
        error = "Quota exceeded"
    providers.append({
        "provider": "api-football",
        "status": status,
        "daily_limit": api_football_state.daily_limit if api_football_state else 100,
        "used_today": api_football_state.used_today if api_football_state else 0,
        "last_success_at": _as_utc(api_football_state.updated_at).isoformat() if api_football_state else None,
        "error": error
    })

    # sportmonks
    sportmonks_state = states.get("sportmonks")
    token = settings.sportmonks_token
    status = "enabled"
    error = None
    if not token:
        status = "disabled_no_token"
    elif sportmonks_state and sportmonks_state.used_today >= sportmonks_state.daily_limit:
        status = "quota_limited"
        error = "Quota exceeded"
    providers.append({
        "provider": "sportmonks",
        "status": status,
        "daily_limit": sportmonks_state.daily_limit if sportmonks_state else 500,
        "used_today": sportmonks_state.used_today if sportmonks_state else 0,
        "last_success_at": _as_utc(sportmonks_state.updated_at).isoformat() if sportmonks_state else None,
        "error": error
    })

    return providers


def list_sync_runs(session: Session, limit: int = 20) -> list[dict]:
    rows = list(session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)))
    return [
        {
            "id": row.id,
            "started_at": _as_utc(row.started_at).isoformat(),
            "finished_at": _as_utc(row.finished_at).isoformat() if row.finished_at else None,
            "status": row.status,
            "updated_count": row.updated_count,
            "finalized_matches": row.finalized_matches,
            "warnings": row.warnings,
            "errors": row.errors,
        }
        for row in rows
    ]


def _latest_ratings(session: Session) -> dict[str, TeamRating]:
    result = {}
    for row in session.scalars(
        select(TeamRating).order_by(
            TeamRating.team_id, TeamRating.effective_date.desc(), TeamRating.id.desc()
        )
    ):
        result.setdefault(row.team_id, row)
    return result


def _standing_dict(row: StandingSnapshot) -> dict:
    return {
        "position": row.position,
        "played": row.played,
        "won": row.won,
        "drawn": row.drawn,
        "lost": row.lost,
        "goals_for": row.goals_for,
        "goals_against": row.goals_against,
        "goal_difference": row.goals_for - row.goals_against,
        "points": row.points,
        "tiebreak_uncertain": row.tiebreak_uncertain,
    }


def _qualification_dict(row: QualificationPrediction) -> dict:
    return {
        "first": row.first_probability,
        "second": row.second_probability,
        "third": row.third_probability,
        "fourth": row.fourth_probability,
        "qualify": row.qualify_probability,
        "standard_error": row.standard_error,
    }


def _prediction_dict(row: MatchPrediction) -> dict:
    d = {
        "home_xg": row.home_xg,
        "away_xg": row.away_xg,
        "home_win": row.home_win,
        "draw": row.draw,
        "away_win": row.away_win,
        "scorelines": row.scorelines,
        "confidence": row.confidence,
        "confidence_label": row.confidence_label,
        "data_confidence": row.data_confidence,
        "data_confidence_label": row.data_confidence_label,
        "model_confidence": row.model_confidence,
        "model_confidence_label": row.model_confidence_label,
        "explanation": row.explanation,
        "model_inputs": row.model_inputs,
        "model_version": row.model_version,
    }
    if getattr(row, "has_auto_adjustments", False) and getattr(row, "base_home_win", None) is not None:
        d["base_home_win"] = row.base_home_win
        d["base_draw"] = row.base_draw
        d["base_away_win"] = row.base_away_win
    return d


def _ai_prediction_summary(ai_preds: list[AIPrediction]) -> dict | None:
    """Extract a summary of the best AI prediction for a match (for MatchCard)."""
    if not ai_preds:
        return None
    # Pick the first effective prediction (no error, has parsed probs)
    for ai in ai_preds:
        if ai.error_code is None and ai.parsed_home_win is not None:
            return {
                "home_win": ai.parsed_home_win,
                "draw": ai.parsed_draw,
                "away_win": ai.parsed_away_win,
                "model_version": ai.model_version,
                "recommended_label": ai.recommended_label,
            }
    return None


def _ensemble_prediction_summary(ensemble_preds: list[EnsemblePrediction]) -> dict | None:
    """Extract a summary of the ensemble prediction for a match (for MatchCard)."""
    if not ensemble_preds:
        return None
    ens = ensemble_preds[0]
    return {
        "home_win": ens.ensemble_home_win,
        "draw": ens.ensemble_draw,
        "away_win": ens.ensemble_away_win,
        "model_version": ens.model_version,
        "system_weight": ens.system_weight,
        "market_weight": ens.market_weight,
    }


def _team_ref(team: Team, display_names: dict[str, str]) -> dict:
    name = display_names[team.id]
    return {"id": team.id, "name": name, "short_name": name, "flag": team.flag_url}


def _empty_review_summary() -> dict:
    return {
        "matches_scored": 0,
        "brier_score": 0.0,
        "log_loss": 0.0,
        "outcome_hit_rate": 0.0,
        "top_score_hit_rate": 0.0,
        "xg_mae": 0.0,
    }


def _outcome_label(outcome: str) -> str:
    return {"home": "主胜", "draw": "平局", "away": "客胜"}[outcome]


def _bias_explanation(snapshot: PredictionSnapshot, match: Match, outcome_correct: bool) -> str:
    predicted_outcome = max(
        [("home", snapshot.home_win), ("draw", snapshot.draw), ("away", snapshot.away_win)],
        key=lambda item: item[1],
    )[0]
    if match.home_score is None or match.away_score is None:
        return "比赛尚未形成可复盘结果。"

    if match.home_score > match.away_score:
        actual_outcome = "home"
    elif match.home_score == match.away_score:
        actual_outcome = "draw"
    else:
        actual_outcome = "away"

    if outcome_correct:
        if actual_outcome == "draw":
            return "模型较准确地识别了平局方向，比赛胶着程度与预期接近。"
        predicted_margin = snapshot.home_xg - snapshot.away_xg
        actual_margin = match.home_score - match.away_score
        margin_gap = actual_margin - predicted_margin
        if margin_gap > 0.75:
            return f"模型较准确地识别了{_outcome_label(actual_outcome)}方向，但低估了净胜优势。"
        if margin_gap < -0.75:
            return f"模型较准确地识别了{_outcome_label(actual_outcome)}方向，但高估了净胜优势。"
        return f"模型较准确地识别了{_outcome_label(actual_outcome)}方向，比赛强弱差基本符合预期。"

    if actual_outcome == "draw":
        return f"模型更看好{_outcome_label(predicted_outcome)}，但实际打成平局，说明对胶着程度判断不足。"
    if predicted_outcome == "draw":
        return f"模型过度强调了平局可能，低估了{_outcome_label(actual_outcome)}兑现的概率。"
    return f"模型更看好{_outcome_label(predicted_outcome)}，但比赛最终走向{_outcome_label(actual_outcome)}，方向判断出现偏差。"


def build_decision(session: Session) -> dict:
    """Build decision view data for the frontend."""
    revision = session.scalar(
        select(DashboardRevision)
        .where(DashboardRevision.active.is_(True))
        .order_by(DashboardRevision.id.desc())
        .limit(1)
    )
    if revision is None:
        return {
            "today_matches": [], "most_confident": [], "most_uncertain": [],
            "biggest_divergence": [], "upset_risk": [], "recent_review": [],
            "review_summary": _empty_review_summary(),
        }

    teams = list(session.scalars(select(Team).order_by(Team.group_code, Team.id)))
    matches = list(session.scalars(select(Match).order_by(Match.kickoff, Match.id)))
    predictions = {
        row.match_id: row
        for row in session.scalars(
            select(MatchPrediction).where(
                MatchPrediction.revision_id == revision.id,
                MatchPrediction.model_version.notin_(SHADOW_MODEL_VERSIONS),
            )
        )
    }
    market_snaps = {
        row.match_id: row
        for row in session.scalars(
            select(MarketSnapshot).where(MarketSnapshot.provider == "sporttery")
        )
    }
    teams_by_id = {team.id: team for team in teams}
    display_names = localized_team_names(session, teams)
    manual_adjustments = {
        match_id: [serialize_adjustment(item, display_names) for item in items]
        for match_id, items in adjustments_by_match(session).items()
    }

    adjustments_by_match_auto = defaultdict(list)
    seen_auto_adjs_decision = set()
    for row in session.scalars(select(AutoAdjustment).order_by(AutoAdjustment.id.desc())):
        sig = (row.match_id, row.adjustment_type, row.affected_team_id, row.reason)
        if sig not in seen_auto_adjs_decision:
            seen_auto_adjs_decision.add(sig)
            adjustments_by_match_auto[row.match_id].append(row)

    intelligences = {row.id: row for row in session.scalars(select(MatchIntelligence))}

    local_now = decision_now().astimezone(SHANGHAI)
    local_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = local_today.astimezone(timezone.utc)
    tomorrow_end = (local_today + timedelta(days=2)).astimezone(timezone.utc)
    yesterday_start = (local_today - timedelta(days=1)).astimezone(timezone.utc)

    def _ensure_aware(dt):
        """Ensure datetime is timezone-aware (default to UTC)."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _snapshot_prediction(snap):
        return {
            "home_win": snap.home_win,
            "draw": snap.draw,
            "away_win": snap.away_win,
            "confidence_label": snap.confidence_label,
            "model_confidence_label": None,
            "home_xg": snap.home_xg,
            "away_xg": snap.away_xg,
        }

    def _match_card(match, pred=None, market_snap=None):
        """Build a compact match card for decision view."""
        card = {
            **_china_time_fields(match.kickoff),
            "id": match.id,
            "group_code": match.group_code,
            "kickoff": _as_utc(match.kickoff).isoformat(),
            "home_team": _team_ref(teams_by_id[match.home_team_id], display_names),
            "away_team": _team_ref(teams_by_id[match.away_team_id], display_names),
            "status": match.status,
            "home_score": match.home_score,
            "away_score": match.away_score,
            "manual_adjustments": manual_adjustments.get(match.id, []),
            "intelligence_risks": [
                {
                    "type": adj.adjustment_type,
                    "affected_team_id": adj.affected_team_id,
                    "reason": adj.reason,
                }
                for adj in adjustments_by_match_auto[match.id]
                if adj.adjustment_type != "numerical_roster_adjustment"
            ],
            "numerical_adjustments": [
                {
                    "type": adj.adjustment_type,
                    "affected_team_id": adj.affected_team_id,
                    "attack_delta": adj.attack_delta,
                    "defense_delta": adj.defense_delta,
                    "reason": adj.reason,
                }
                for adj in adjustments_by_match_auto[match.id]
                if adj.adjustment_type == "numerical_roster_adjustment"
            ],
            "numerical_enabled": getattr(revision, 'model_version', '') == 'elo-poisson-v1-intel-numeric',
        }
        if pred:
            card["prediction"] = {
                "home_win": pred.home_win, "draw": pred.draw, "away_win": pred.away_win,
                "confidence_label": pred.confidence_label,
                "model_confidence_label": pred.model_confidence_label,
                "home_xg": pred.home_xg, "away_xg": pred.away_xg,
            }
            if getattr(pred, "has_auto_adjustments", False) and getattr(pred, "base_home_win", None) is not None:
                card["prediction"]["base_home_win"] = pred.base_home_win
                card["prediction"]["base_draw"] = pred.base_draw
                card["prediction"]["base_away_win"] = pred.base_away_win
        if market_snap and pred:
            div = compute_divergence(
                {"home_win": pred.home_win, "draw": pred.draw, "away_win": pred.away_win},
                market_snap,
            )
            card["market"] = {
                "home_probability": market_snap.home_probability,
                "draw_probability": market_snap.draw_probability,
                "away_probability": market_snap.away_probability,
                "divergence": {
                    "max_divergence": div.max_divergence, "level": div.level,
                },
            }
        return card

    # Today/tomorrow matches
    today_matches = []
    for m in matches:
        if m.status == "final":
            continue
        kickoff_aware = _ensure_aware(m.kickoff)
        if today_start <= kickoff_aware < tomorrow_end:
            pred = predictions.get(m.id)
            today_matches.append(_match_card(m, pred, market_snaps.get(m.id)))

    # Most confident (highest max probability with high model confidence)
    predicted_unfinal = [(m, predictions[m.id]) for m in matches if m.status != "final" and m.id in predictions]
    confident = sorted(
        predicted_unfinal,
        key=lambda pair: max(pair[1].home_win, pair[1].draw, pair[1].away_win),
        reverse=True,
    )[:6]
    most_confident = [_match_card(m, p, market_snaps.get(m.id)) for m, p in confident]

    # Most uncertain (three probabilities closest together)
    uncertain = sorted(
        predicted_unfinal,
        key=lambda pair: max(pair[1].home_win, pair[1].draw, pair[1].away_win)
        - min(pair[1].home_win, pair[1].draw, pair[1].away_win),
    )[:6]
    most_uncertain = [_match_card(m, p, market_snaps.get(m.id)) for m, p in uncertain]

    # Biggest divergence with market
    divergent = []
    for m, p in predicted_unfinal:
        ms = market_snaps.get(m.id)
        if ms:
            div = compute_divergence(
                {"home_win": p.home_win, "draw": p.draw, "away_win": p.away_win}, ms
            )
            divergent.append((m, p, ms, div.max_divergence))
    divergent.sort(key=lambda x: x[3], reverse=True)
    biggest_divergence = [_match_card(m, p, ms) for m, p, ms, _ in divergent[:6]]

    # Upset risk: strong team with unstable win probability (low-ish)
    upset = sorted(
        predicted_unfinal,
        key=lambda pair: (
            1.0 - max(pair[1].home_win, pair[1].away_win)
            if pair[1].home_win > 0.45 or pair[1].away_win > 0.45
            else 0.0
        ),
        reverse=True,
    )[:6]
    upset_risk = [_match_card(m, p, market_snaps.get(m.id)) for m, p in upset if p.home_win > 0.45 or p.away_win > 0.45]

    # Recent review: yesterday's finalized matches
    snapshots = _display_snapshots(session, matches)
    review_pairs = []
    for m in matches:
        snap = snapshots.get(m.id)
        if not snap or m.status != "final" or m.home_score is None:
            continue
        kickoff_aware = _ensure_aware(m.kickoff)
        if yesterday_start <= kickoff_aware < today_start:
            review_pairs.append((snap, m))

    review_report = score_predictions(review_pairs, display_names)
    review_details = {detail.match_id: detail for detail in review_report.per_match}
    recent = []
    for m in matches:
        if m.status != "final" or m.home_score is None:
            continue
        kickoff_aware = _ensure_aware(m.kickoff)
        if yesterday_start <= kickoff_aware < today_start:
            snap = snapshots.get(m.id)
            card = _match_card(m)
            detail = review_details.get(m.id)
            if snap and detail:
                card["snapshot"] = {
                    "home_win": snap.home_win,
                    "draw": snap.draw,
                    "away_win": snap.away_win,
                    "outcome_correct": detail.outcome_correct,
                }
                card["prediction"] = _snapshot_prediction(snap)
                card["review"] = {
                    "brier": detail.brier,
                    "log_loss": detail.log_loss,
                    "xg_error": detail.xg_error,
                    "bias_explanation": _bias_explanation(
                        snap,
                        m,
                        detail.outcome_correct,
                    ),
                }
            recent.append(card)

    intelligence_risks = []
    for m in matches:
        if m.status == "final":
            continue
        for adj in adjustments_by_match_auto.get(m.id, []):
            if adj.adjustment_type in ("market_divergence", "data_completeness", "roster_warning"):
                # find provider from source intelligences
                provider = "unknown"
                if adj.source_intelligence_ids:
                    first_intel = intelligences.get(adj.source_intelligence_ids[0])
                    if first_intel:
                        provider = first_intel.provider
                elif adj.adjustment_type == "market_divergence":
                    provider = "sporttery"

                level = "低"
                if adj.confidence < -0.1:
                    level = "高"
                elif adj.confidence < -0.02:
                    level = "中"

                intelligence_risks.append({
                    "match_id": m.id,
                    "home_team": _team_ref(teams_by_id[m.home_team_id], display_names),
                    "away_team": _team_ref(teams_by_id[m.away_team_id], display_names),
                    "kickoff": _as_utc(m.kickoff).isoformat(),
                    "risk_type": adj.adjustment_type,
                    "level": level,
                    "provider": provider,
                    "reason": adj.reason
                })

    return {
        "today_matches": today_matches,
        "most_confident": most_confident,
        "most_uncertain": most_uncertain,
        "biggest_divergence": biggest_divergence,
        "upset_risk": upset_risk,
        "recent_review": recent,
        "intelligence_risks": intelligence_risks,
        "review_summary": {
            "matches_scored": review_report.matches_scored,
            "brier_score": review_report.brier_score,
            "log_loss": review_report.log_loss,
            "outcome_hit_rate": review_report.outcome_hit_rate,
            "top_score_hit_rate": review_report.top_score_hit_rate,
            "xg_mae": review_report.xg_mae,
        },
    }
