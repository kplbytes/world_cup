"""Data quality check service.

Validates the integrity of match data, predictions, snapshots,
market odds, and intelligence to prevent dirty data from
affecting model scoring.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AutoAdjustment,
    MarketSnapshot,
    Match,
    MatchIntelligence,
    MatchPrediction,
    PredictionSnapshot,
    Team,
    TeamRating,
)


def check_data_quality(session: Session) -> dict[str, Any]:
    """Run all data quality checks and return a report."""
    checks: list[dict[str, Any]] = []

    # 1. Duplicate match_id
    dup_matches = session.execute(
        select(Match.id, func.count(Match.id))
        .group_by(Match.id)
        .having(func.count(Match.id) > 1)
    ).all()
    checks.append({
        "check": "duplicate_match_id",
        "status": "pass" if not dup_matches else "fail",
        "count": len(dup_matches),
        "details": [row[0] for row in dup_matches[:10]] if dup_matches else [],
    })

    # 2. Missing Elo ratings
    teams = list(session.scalars(select(Team)))
    rated_teams = set(session.scalars(
        select(TeamRating.team_id.distinct())
    ))
    missing_elo = [t.id for t in teams if t.id not in rated_teams]
    checks.append({
        "check": "missing_elo_ratings",
        "status": "pass" if not missing_elo else "fail",
        "count": len(missing_elo),
        "details": missing_elo[:10],
    })

    # 3. Final matches without score
    final_no_score = session.execute(
        select(Match.id)
        .where(Match.status == "final")
        .where((Match.home_score.is_(None)) | (Match.away_score.is_(None)))
    ).scalars().all()
    checks.append({
        "check": "final_match_without_score",
        "status": "pass" if not final_no_score else "fail",
        "count": len(final_no_score),
        "details": list(final_no_score[:10]),
    })

    # 4. Scheduled matches with scores
    scheduled_with_score = session.execute(
        select(Match.id)
        .where(Match.status == "scheduled")
        .where((Match.home_score.isnot(None)) | (Match.away_score.isnot(None)))
    ).scalars().all()
    checks.append({
        "check": "scheduled_match_with_score",
        "status": "pass" if not scheduled_with_score else "fail",
        "count": len(scheduled_with_score),
        "details": list(scheduled_with_score[:10]),
    })

    # 5. Missing locked snapshots for final matches
    final_matches = list(session.scalars(
        select(Match).where(Match.status == "final")
    ))
    locked_match_ids = set(session.scalars(
        select(PredictionSnapshot.match_id)
        .where(PredictionSnapshot.is_pre_match_locked.is_(True))
    ))
    missing_locked = [m.id for m in final_matches if m.id not in locked_match_ids]
    checks.append({
        "check": "missing_locked_snapshot",
        "status": "pass" if not missing_locked else "warn",
        "count": len(missing_locked),
        "details": missing_locked[:10],
        "note": "已结束但缺少锁定快照的比赛将不参与评分",
    })

    # 6. High fallback snapshot ratio
    total_locked = session.scalar(
        select(func.count())
        .select_from(PredictionSnapshot)
        .where((PredictionSnapshot.is_pre_match_locked.is_(True)) | (PredictionSnapshot.is_fallback_locked.is_(True)))
    ) or 0
    fallback_count = session.scalar(
        select(func.count())
        .select_from(PredictionSnapshot)
        .where(PredictionSnapshot.is_fallback_locked.is_(True))
    ) or 0
    fallback_ratio = fallback_count / max(total_locked, 1)
    checks.append({
        "check": "fallback_snapshot_ratio",
        "status": "pass" if fallback_ratio < 0.3 else "warn",
        "count": fallback_count,
        "ratio": round(fallback_ratio, 2),
        "note": f"降级快照占比 {fallback_ratio:.0%}，过高可能影响评分质量",
    })

    # 7. Intelligence after 24h lock window
    now = datetime.now(timezone.utc)
    late_intel = []
    for snap in session.scalars(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.is_pre_match_locked.is_(True))
    ):
        intel_rows = session.execute(
            select(MatchIntelligence)
            .where(MatchIntelligence.match_id == snap.match_id)
            .where(MatchIntelligence.fetched_at > snap.kickoff - timedelta(hours=24))
        ).scalars().all()
        if intel_rows:
            late_intel.append(snap.match_id)
    checks.append({
        "check": "intelligence_after_lock_window",
        "status": "pass" if not late_intel else "warn",
        "count": len(late_intel),
        "details": late_intel[:10],
        "note": "24h锁定窗口后情报不应进入赛后评分",
    })

    # 8. Unmatched market odds
    unmatched_market = session.execute(
        select(MarketSnapshot.match_id)
        .where(MarketSnapshot.provider == "sporttery")
        .where(~MarketSnapshot.match_id.in_(select(Match.id)))
    ).scalars().all()
    checks.append({
        "check": "unmatched_market_odds",
        "status": "pass" if not unmatched_market else "warn",
        "count": len(unmatched_market),
        "details": list(unmatched_market[:10]),
    })

    # 9. Missing player_importance for teams with roster intel
    roster_intel = list(session.scalars(
        select(MatchIntelligence)
        .where(MatchIntelligence.intelligence_type.in_(("injuries", "suspensions")))
    ))
    missing_importance = []
    for intel in roster_intel:
        player_name = intel.normalized_payload.get("player_name", "")
        team_id = intel.normalized_payload.get("affected_team_id", "")
        if player_name and team_id:
            from app.intelligence.player_mock import get_player_importance
            imp = get_player_importance(player_name, team_id)
            if not imp:
                missing_importance.append(f"{player_name}({team_id})")
    checks.append({
        "check": "missing_player_importance",
        "status": "pass" if not missing_importance else "warn",
        "count": len(missing_importance),
        "details": missing_importance[:10],
        "note": "缺少球员重要性数据，数值修正可能不准确",
    })

    # 10. Abnormal xG values
    abnormal_xg = session.execute(
        select(MatchPrediction.match_id, MatchPrediction.home_xg, MatchPrediction.away_xg)
        .where((MatchPrediction.home_xg < 0.15) | (MatchPrediction.home_xg > 4.0) |
               (MatchPrediction.away_xg < 0.15) | (MatchPrediction.away_xg > 4.0))
    ).all()
    checks.append({
        "check": "abnormal_xg_values",
        "status": "pass" if not abnormal_xg else "warn",
        "count": len(abnormal_xg),
        "details": [{"match_id": row[0], "home_xg": row[1], "away_xg": row[2]} for row in abnormal_xg[:10]],
    })

    # Summary
    fail_count = sum(1 for c in checks if c["status"] == "fail")
    warn_count = sum(1 for c in checks if c["status"] == "warn")
    pass_count = sum(1 for c in checks if c["status"] == "pass")

    return {
        "timestamp": now.isoformat(),
        "summary": {
            "total_checks": len(checks),
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "overall_status": "fail" if fail_count > 0 else ("warn" if warn_count > 0 else "pass"),
        },
        "checks": checks,
    }


def data_quality_report_markdown(session: Session) -> str:
    """Generate a markdown data quality report."""
    result = check_data_quality(session)

    lines = [
        "# 数据质量检查报告",
        "",
        f"检查时间: {result['timestamp']}",
        "",
        f"## 概览",
        "",
        f"- 总检查项: {result['summary']['total_checks']}",
        f"- ✅ 通过: {result['summary']['pass']}",
        f"- ⚠️ 警告: {result['summary']['warn']}",
        f"- ❌ 失败: {result['summary']['fail']}",
        f"- 整体状态: {result['summary']['overall_status']}",
        "",
        "## 详细结果",
        "",
    ]

    for check in result["checks"]:
        status_icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(check["status"], "?")
        lines.append(f"### {status_icon} {check['check']}")
        lines.append(f"- 状态: {check['status']}")
        lines.append(f"- 数量: {check['count']}")
        if check.get("details"):
            details_str = ", ".join(str(d) for d in check["details"][:5])
            lines.append(f"- 详情: {details_str}")
        if check.get("note"):
            lines.append(f"- 备注: {check['note']}")
        lines.append("")

    return "\n".join(lines)
