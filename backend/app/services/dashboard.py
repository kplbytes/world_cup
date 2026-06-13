from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

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
)
from app.services.market import compute_divergence
from app.services.localization import localized_team_names
from app.services.manual_adjustments import adjustments_by_match, serialize_adjustment
from app.services.scoring import score_predictions


SHANGHAI = ZoneInfo("Asia/Shanghai")


def decision_now() -> datetime:
    return datetime.now(timezone.utc)


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
            select(MatchPrediction).where(MatchPrediction.revision_id == revision.id)
        )
    }
    market_snaps = {
        row.match_id: row
        for row in session.scalars(
            select(MarketSnapshot).where(MarketSnapshot.provider == "sporttery")
        )
    }
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
        matches_by_group[match.group_code].append(
            {
                "id": match.id,
                "group_code": match.group_code,
                "kickoff": match.kickoff.isoformat(),
                "venue": match.venue,
                "status": match.status,
                "home_team": _team_ref(teams_by_id[match.home_team_id], display_names),
                "away_team": _team_ref(teams_by_id[match.away_team_id], display_names),
                "home_score": match.home_score,
                "away_score": match.away_score,
                "manual_adjustments": manual_adjustments.get(match.id, []),
                "prediction": _prediction_dict(prediction) if prediction else None,
                "market": market_data,
                "source": match.source,
                "source_updated_at": (
                    match.source_updated_at.isoformat() if match.source_updated_at else None
                ),
            }
        )

    return {
        "revision": {
            "id": revision.id,
            "created_at": revision.created_at.isoformat(),
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
        "data_sources": list_data_sources(session),
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
            "fetched_at": snapshot.fetched_at.isoformat(),
            "status": snapshot.status,
            "coverage": snapshot.coverage,
            "error": snapshot.error,
        }
        for snapshot in latest.values()
    ]


def list_sync_runs(session: Session, limit: int = 20) -> list[dict]:
    rows = list(session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)))
    return [
        {
            "id": row.id,
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
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
    return {
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
            select(MatchPrediction).where(MatchPrediction.revision_id == revision.id)
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
            "id": match.id,
            "group_code": match.group_code,
            "kickoff": match.kickoff.isoformat(),
            "home_team": _team_ref(teams_by_id[match.home_team_id], display_names),
            "away_team": _team_ref(teams_by_id[match.away_team_id], display_names),
            "status": match.status,
            "home_score": match.home_score,
            "away_score": match.away_score,
            "manual_adjustments": manual_adjustments.get(match.id, []),
        }
        if pred:
            card["prediction"] = {
                "home_win": pred.home_win, "draw": pred.draw, "away_win": pred.away_win,
                "confidence_label": pred.confidence_label,
                "model_confidence_label": pred.model_confidence_label,
                "home_xg": pred.home_xg, "away_xg": pred.away_xg,
            }
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
    snapshots = {
        row.match_id: row for row in session.scalars(select(PredictionSnapshot))
    }
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

    return {
        "today_matches": today_matches,
        "most_confident": most_confident,
        "most_uncertain": most_uncertain,
        "biggest_divergence": biggest_divergence,
        "upset_risk": upset_risk,
        "recent_review": recent,
        "review_summary": {
            "matches_scored": review_report.matches_scored,
            "brier_score": review_report.brier_score,
            "log_loss": review_report.log_loss,
            "outcome_hit_rate": review_report.outcome_hit_rate,
            "top_score_hit_rate": review_report.top_score_hit_rate,
            "xg_mae": review_report.xg_mae,
        },
    }
