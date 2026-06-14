from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Team, TeamProfile, TeamProfileMatchHistory
from app.team_profiles import PROFILE_VERSION
from app.team_profiles.data_loader import seed_mock_history
from app.team_profiles.feature_engineering import generate_traits, rate, tier_statistics


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def compute_team_profile(session: Session, team_id: str, as_of_date: datetime) -> TeamProfile:
    team = session.get(Team, team_id)
    if team is None:
        raise LookupError(f"unknown team: {team_id}")
    cutoff = _as_utc(as_of_date)
    rows = list(session.scalars(
        select(TeamProfileMatchHistory)
        .where(TeamProfileMatchHistory.team_id == team_id)
        .where(TeamProfileMatchHistory.match_date <= cutoff.date())
        .order_by(TeamProfileMatchHistory.match_date)
    ))
    competitive = [row for row in rows if not row.is_friendly]
    n = len(competitive)
    tier_stats = tier_statistics(competitive)
    strong_rows = [row for row in competitive if row.opponent_tier in ("elite", "strong")]
    weak_rows = [row for row in competitive if row.opponent_tier == "weak"]
    recent = competitive[-8:]
    world_cup = [row for row in competitive if row.is_world_cup]
    knockout = [row for row in world_cup if row.stage == "knockout"]
    opening = [row for row in world_cup if row.stage in ("opening", "group")][:max(1, len(world_cup) // 3)]
    goal_for_avg = sum(row.goals_for for row in competitive) / n if n else 0.0
    goal_against_avg = sum(row.goals_against for row in competitive) / n if n else 0.0
    under_25 = rate(competitive, lambda row: row.goals_for + row.goals_against <= 2)
    draw_strong = rate(strong_rows, lambda row: row.result == "draw")
    strong_not_loss = rate(strong_rows, lambda row: row.result != "loss")
    favorite_win = rate(weak_rows, lambda row: row.result == "win")
    metrics = {
        "attack_strength_recent": min(1.0, sum(row.goals_for for row in recent) / max(1, len(recent)) / 2.5),
        "defense_strength_recent": max(0.0, 1.0 - sum(row.goals_against for row in recent) / max(1, len(recent)) / 2.5),
        "goal_for_avg": goal_for_avg,
        "goal_against_avg": goal_against_avg,
        "clean_sheet_rate": rate(competitive, lambda row: row.goals_against == 0),
        "failed_to_score_rate": rate(competitive, lambda row: row.goals_for == 0),
        "over_2_5_rate": rate(competitive, lambda row: row.goals_for + row.goals_against >= 3),
        "under_2_5_rate": under_25,
        "both_teams_score_rate": rate(competitive, lambda row: row.goals_for > 0 and row.goals_against > 0),
        "low_score_tendency": under_25,
        "high_score_tendency": rate(competitive, lambda row: row.goals_for + row.goals_against >= 4),
        "draw_rate_overall": rate(competitive, lambda row: row.result == "draw"),
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
    profile = TeamProfile(
        team_id=team.id, team_code=team.code, profile_version=PROFILE_VERSION,
        profile_as_of=cutoff, data_cutoff=cutoff,
        data_start_year=min((row.match_date.year for row in rows), default=None),
        data_end_year=max((row.match_date.year for row in rows), default=None),
        sample_count=len(rows), world_cup_sample_count=len(world_cup),
        qualifier_sample_count=sum(row.is_qualifier for row in rows), competitive_sample_count=n,
        tier_stats_json=tier_stats, traits_json=generate_traits(metrics, n, tier_stats),
        source_summary_json={"mode": "seed_mock_v1" if rows and all(row.source == "seed_mock_v1" for row in rows) else "mixed", "sources": sorted({row.source for row in rows}), "friendly_weight": 0.0},
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
        seed_mock_history(session)
    session.execute(delete(TeamProfile))
    profiles = [compute_team_profile(session, team.id, cutoff) for team in session.scalars(select(Team).order_by(Team.id))]
    return {"profiles": len(profiles), "profile_version": PROFILE_VERSION, "profile_as_of": cutoff.isoformat(), "data_mode": "seed_mock_v1" if use_seed else "existing_history"}


def explain_team_profile(profile: TeamProfile) -> str:
    if profile.sample_count < 6:
        return f"仅 {profile.sample_count} 场样本，画像只作弱提示。"
    traits = "、".join(profile.traits_json) or "未触发强标签"
    return f"基于 {profile.sample_count} 场正式比赛；标签：{traits}；场均进球 {profile.goal_for_avg:.2f}，场均失球 {profile.goal_against_avg:.2f}。"
