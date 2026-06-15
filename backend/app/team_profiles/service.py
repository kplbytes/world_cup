from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import HistoricalMatch, HistoricalTeam, Team, TeamProfile, TeamProfileMatchHistory, TeamRating
from app.team_profiles import PROFILE_VERSION
from app.team_profiles.data_loader import seed_mock_history
from app.team_profiles.feature_engineering import classify_opponent_tier, generate_traits, rate, tier_statistics

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _historical_to_profile_row(match: HistoricalMatch, team_id: str, session: Session) -> dict:
    """Convert a HistoricalMatch into a dict compatible with profile computation.

    This creates the same shape as TeamProfileMatchHistory but from real data.
    Handles both WC team opponents and HistoricalTeam opponents.
    """
    is_home = match.home_team_id == team_id
    opponent_team_id = match.away_team_id if is_home else match.home_team_id
    opponent_source = match.away_team_source if is_home else match.home_team_source
    opponent_raw = match.away_team_raw if is_home else match.home_team_raw

    goals_for = match.home_score if is_home else match.away_score
    goals_against = match.away_score if is_home else match.home_score

    if goals_for > goals_against:
        result = "win"
    elif goals_for < goals_against:
        result = "loss"
    else:
        result = "draw"

    # Get opponent ELO for tier classification
    opponent_elo = None
    if opponent_team_id:
        if opponent_source == "world_cup":
            # WC team - look up in TeamRating
            opp_rating = session.scalar(
                select(TeamRating).where(TeamRating.team_id == opponent_team_id).order_by(TeamRating.effective_date.desc()).limit(1)
            )
            opponent_elo = opp_rating.elo if opp_rating else None
        elif opponent_source == "historical":
            # HistoricalTeam - use default Elo based on category
            ht = session.get(HistoricalTeam, opponent_team_id)
            if ht:
                opponent_elo = _default_elo_for_category(ht.team_category)
                # If linked to a WC team, try to use that team's Elo
                if ht.current_team_id:
                    opp_rating = session.scalar(
                        select(TeamRating).where(TeamRating.team_id == ht.current_team_id).order_by(TeamRating.effective_date.desc()).limit(1)
                    )
                    if opp_rating:
                        opponent_elo = opp_rating.elo

    is_world_cup = match.competition_type == "world_cup"
    is_qualifier = match.competition_type == "qualifier"
    is_friendly = match.competition_type == "friendly"

    # Determine stage
    stage = "group"
    if is_world_cup:
        # Heuristic: knockout if went to extra time or penalties
        if match.went_to_extra_time or match.went_to_penalties:
            stage = "knockout"

    return {
        "team_id": team_id,
        "match_date": match.kickoff.date(),
        "competition": match.competition,
        "stage": stage,
        "opponent_team_id": opponent_team_id,
        "opponent_name": opponent_raw,
        "opponent_elo": opponent_elo,
        "opponent_tier": classify_opponent_tier(opponent_elo),
        "is_neutral": match.neutral_venue,
        "is_home": is_home,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "result": result,
        "points": 3 if result == "win" else 1 if result == "draw" else 0,
        "is_world_cup": is_world_cup,
        "is_qualifier": is_qualifier,
        "is_friendly": is_friendly,
        "source": "historical_real",
        "score_scope": getattr(match, "score_scope", "full_90min"),
    }


def _default_elo_for_category(category: str) -> float:
    """Return a default Elo rating based on team category.

    These are rough estimates used when no TeamRating is available.
    """
    _CATEGORY_ELO = {
        "fifa_member": 1500.0,
        "non_fifa_representative": 1350.0,
        "historical_renamed": 1500.0,
        "regional": 1200.0,
        "unknown": 1300.0,
    }
    return _CATEGORY_ELO.get(category, 1300.0)


class _ProfileRow:
    """Lightweight wrapper that mimics TeamProfileMatchHistory attributes."""

    __slots__ = (
        "match_date", "competition", "stage", "opponent_team_id", "opponent_name",
        "opponent_elo", "opponent_tier", "is_neutral", "is_home", "goals_for",
        "goals_against", "result", "points", "is_world_cup", "is_qualifier",
        "is_friendly", "source", "score_scope",
    )

    def __init__(self, data: dict):
        for key in self.__slots__:
            setattr(self, key, data[key])


def compute_team_profile(session: Session, team_id: str, as_of_date: datetime) -> TeamProfile:
    team = session.get(Team, team_id)
    if team is None:
        raise LookupError(f"unknown team: {team_id}")
    cutoff = _as_utc(as_of_date)

    # ── Try real historical data first ──────────────────────────────────
    real_matches = list(session.scalars(
        select(HistoricalMatch)
        .where(HistoricalMatch.available_at < cutoff)
        .where(HistoricalMatch.is_unmapped.is_(False))
        .where(
            (HistoricalMatch.home_team_id == team_id)
            | (HistoricalMatch.away_team_id == team_id)
        )
        .order_by(HistoricalMatch.kickoff)
    ))

    real_rows: list[_ProfileRow] = []
    mock_rows: list[TeamProfileMatchHistory] = []

    # Collect source_match_ids for provenance
    source_match_ids: list[str] = []

    if real_matches:
        real_rows = [_ProfileRow(_historical_to_profile_row(m, team_id, session)) for m in real_matches]
        source_match_ids = [m.source_match_id for m in real_matches if m.source_match_id]

    # ── Fall back to mock data if no real data ──────────────────────────
    if not real_rows:
        mock_rows = list(session.scalars(
            select(TeamProfileMatchHistory)
            .where(TeamProfileMatchHistory.team_id == team_id)
            .where(TeamProfileMatchHistory.match_date <= cutoff.date())
            .order_by(TeamProfileMatchHistory.match_date)
        ))

    # Combine: use real data if available, otherwise mock
    if real_rows:
        all_rows = real_rows
        sources = {"historical_real"}
    elif mock_rows:
        all_rows = mock_rows  # type: ignore[assignment]
        sources = {row.source for row in mock_rows}
    else:
        all_rows = []
        sources = set()

    # Determine data_source label
    if real_rows:
        data_source = "real"
    elif mock_rows:
        # Preserve backward compat: if all mock data is seed_mock_v1, label it as such
        if all(row.source == "seed_mock_v1" for row in mock_rows):
            data_source = "seed_mock_v1"
        else:
            data_source = "mock"
    else:
        data_source = "none"

    competitive = [row for row in all_rows if not row.is_friendly]
    n = len(competitive)

    # Separate matches by score_scope for goal statistics
    # "full_90min" matches are included in 90-minute model features
    # "after_extra_time_or_unknown" and "unknown_score_scope" are excluded
    competitive_90min = [row for row in competitive if getattr(row, "score_scope", "full_90min") == "full_90min"]
    excluded_extra_time_count = n - len(competitive_90min)

    tier_stats = tier_statistics(competitive_90min)
    strong_rows = [row for row in competitive_90min if row.opponent_tier in ("elite", "strong")]
    weak_rows = [row for row in competitive_90min if row.opponent_tier == "weak"]
    recent = competitive_90min[-8:]
    world_cup = [row for row in competitive_90min if row.is_world_cup]
    knockout = [row for row in world_cup if row.stage == "knockout"]
    opening = [row for row in world_cup if row.stage in ("opening", "group")][:max(1, len(world_cup) // 3)]

    # Goal-based statistics: only use full_90min matches
    n_90min = len(competitive_90min)
    goal_for_avg = sum(row.goals_for for row in competitive_90min) / n_90min if n_90min else 0.0
    goal_against_avg = sum(row.goals_against for row in competitive_90min) / n_90min if n_90min else 0.0
    under_25 = rate(competitive_90min, lambda row: row.goals_for + row.goals_against <= 2)
    draw_strong = rate(strong_rows, lambda row: row.result == "draw")
    strong_not_loss = rate(strong_rows, lambda row: row.result != "loss")
    favorite_win = rate(weak_rows, lambda row: row.result == "win")
    metrics = {
        "attack_strength_recent": min(1.0, sum(row.goals_for for row in recent) / max(1, len(recent)) / 2.5),
        "defense_strength_recent": max(0.0, 1.0 - sum(row.goals_against for row in recent) / max(1, len(recent)) / 2.5),
        "goal_for_avg": goal_for_avg,
        "goal_against_avg": goal_against_avg,
        "clean_sheet_rate": rate(competitive_90min, lambda row: row.goals_against == 0),
        "failed_to_score_rate": rate(competitive_90min, lambda row: row.goals_for == 0),
        "over_2_5_rate": rate(competitive_90min, lambda row: row.goals_for + row.goals_against >= 3),
        "under_2_5_rate": under_25,
        "both_teams_score_rate": rate(competitive_90min, lambda row: row.goals_for > 0 and row.goals_against > 0),
        "low_score_tendency": under_25,
        "high_score_tendency": rate(competitive_90min, lambda row: row.goals_for + row.goals_against >= 4),
        "draw_rate_overall": rate(competitive_90min, lambda row: row.result == "draw"),
        "draw_rate_vs_elite": tier_stats["elite"]["draw_rate"],
        "draw_rate_vs_strong": tier_stats["strong"]["draw_rate"],
        "draw_rate_as_underdog": draw_strong,
        "draw_resilience_score": min(1.0, draw_strong * 0.65 + strong_not_loss * 0.35),
        "favorite_win_rate": favorite_win,
        "favorite_fail_to_win_rate": 1.0 - favorite_win if weak_rows else 0.0,
        "favorite_overconfidence_risk": (1.0 - favorite_win) * min(1.0, len(weak_rows) / 4) if weak_rows else 0.0,
        "weak_opponent_upset_risk": rate(weak_rows, lambda row: row.result == "loss"),
        "underdog_draw_rate": draw_strong,
        "underdog_win_or_draw_rate": strong_not_loss,
        "upset_potential_score": rate(strong_rows, lambda row: row.result == "win") * 0.6 + strong_not_loss * 0.4,
        "defensive_resilience_score": max(0.0, min(1.0, strong_not_loss * 0.55 + (1.0 - tier_stats["elite"]["goal_against_avg"] / 3.0) * 0.45)),
        "world_cup_experience_score": min(1.0, len(world_cup) / 12),
        "knockout_experience_score": min(1.0, len(knockout) / 5),
        "recent_tournament_consistency": rate(recent, lambda row: row.result != "loss"),
        "pressure_match_score": rate(knockout, lambda row: row.result != "loss"),
        "opening_match_slow_start_score": rate(opening, lambda row: row.result != "win"),
        "group_stage_consistency": rate([r for r in world_cup if r.stage != "knockout"], lambda row: row.result != "loss"),
        "third_match_rotation_risk": 0.0,
        "must_win_match_performance": rate(knockout, lambda row: row.result == "win"),
    }

    # Compute confidence based on sample size and data source
    sample_confidence = min(1.0, n / 20) if n > 0 else 0.0
    source_confidence = 1.0 if data_source == "real" else 0.5

    profile = TeamProfile(
        team_id=team.id, team_code=team.code, profile_version=PROFILE_VERSION,
        profile_as_of=cutoff, data_cutoff=cutoff,
        data_start_year=min((row.match_date.year for row in all_rows), default=None),
        data_end_year=max((row.match_date.year for row in all_rows), default=None),
        sample_count=len(all_rows), world_cup_sample_count=len(world_cup),
        qualifier_sample_count=sum(row.is_qualifier for row in all_rows), competitive_sample_count=n,
        tier_stats_json=tier_stats, traits_json=generate_traits(metrics, n, tier_stats),
        source_summary_json={
            "data_source": data_source,
            "mode": data_source,
            "sources": sorted(sources),
            "friendly_weight": 0.0,
            "sample_count": len(all_rows),
            "as_of": cutoff.isoformat(),
            "source": data_source,
            "freshness": cutoff.isoformat(),
            "confidence": sample_confidence * source_confidence,
            "real_match_count": len(real_rows),
            "mock_match_count": len(mock_rows),
            "profile_as_of": cutoff.isoformat(),
            "profile_source_version": PROFILE_VERSION,
            "profile_sample_count": len(all_rows),
            "historical_match_ids": source_match_ids,
            "excluded_extra_time_count": excluded_extra_time_count,
            "excluded_extra_time_result_count": excluded_extra_time_count,
        },
        **metrics,
    )
    session.add(profile)
    session.flush()
    return profile


def get_team_profile(session: Session, team_id: str, as_of_date: datetime | None = None) -> TeamProfile | None:
    query = select(TeamProfile).where(TeamProfile.team_id == team_id)
    if as_of_date is not None:
        query = query.where(TeamProfile.profile_as_of <= _as_utc(as_of_date))
    return session.scalar(query.order_by(TeamProfile.profile_as_of.desc(), TeamProfile.id.desc()).limit(1))


def profile_payload(profile: TeamProfile | None) -> dict | None:
    if profile is None:
        return None
    return {column.name: getattr(profile, column.name) for column in TeamProfile.__table__.columns if column.name not in {"created_at", "updated_at"}}


def rebuild_team_profiles(session: Session, as_of_date: datetime | None = None, use_seed: bool = True) -> dict:
    cutoff = _as_utc(as_of_date or datetime.now(timezone.utc))
    if use_seed:
        seed_mock_history(session, use_seed=True)
    session.execute(delete(TeamProfile))
    profiles = [compute_team_profile(session, team.id, cutoff) for team in session.scalars(select(Team).order_by(Team.id))]

    # Determine overall data mode
    real_count = sum(1 for p in profiles if p.source_summary_json.get("data_source") == "real")
    mock_count = sum(1 for p in profiles if p.source_summary_json.get("data_source") in ("mock", "seed_mock_v1"))
    if real_count > 0 and mock_count > 0:
        data_mode = "mixed"
    elif real_count > 0:
        data_mode = "real"
    else:
        data_mode = "seed_mock_v1" if use_seed else "mock"

    return {"profiles": len(profiles), "profile_version": PROFILE_VERSION, "profile_as_of": cutoff.isoformat(), "data_mode": data_mode}


def explain_team_profile(profile: TeamProfile) -> str:
    if profile.sample_count < 6:
        return f"仅 {profile.sample_count} 场样本，画像只作弱提示。"
    traits = "、".join(profile.traits_json) or "未触发强标签"
    data_source = profile.source_summary_json.get("data_source", "unknown")
    source_label = "真实历史数据" if data_source == "real" else "模拟数据" if data_source in ("mock", "seed_mock_v1") else "混合数据"
    return f"基于 {profile.sample_count} 场正式比赛（{source_label}）；标签：{traits}；场均进球 {profile.goal_for_avg:.2f}，场均失球 {profile.goal_against_avg:.2f}。"
