#!/usr/bin/env python3
"""Scoring consistency audit script.

Read-only audit that outputs JSON and Markdown with 13 fields covering
the full scoring pipeline consistency. All statistics are computed within
a single database session to ensure a consistent snapshot.

Usage:
    python backend/scripts/audit_scoring_consistency.py [--db PATH] [--output-dir DIR]

Output files:
    - audit_scoring_YYYYMMDD_HHMMSS.json
    - audit_scoring_YYYYMMDD_HHMMSS.md
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure backend is on sys.path
_backend_dir = Path(__file__).resolve().parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import create_database
from app.models import (
    DashboardRevision,
    Match,
    MatchPrediction,
    ModelScore,
    PredictionSnapshot,
    Team,
)


def _ensure_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def run_audit(session: Session, db_path: str) -> dict:
    """Run the full scoring consistency audit within a single session."""
    audit_time_utc = datetime.now(timezone.utc)

    # ── 1. audit_time_utc ──
    result = {"audit_time_utc": audit_time_utc.isoformat()}

    # ── 2. db_path ──
    result["db_path"] = db_path

    # ── 3. latest model_scores.id ──
    latest_model_score = session.scalar(
        select(ModelScore).order_by(ModelScore.id.desc()).limit(1)
    )
    result["latest_model_score_id"] = latest_model_score.id if latest_model_score else None

    # ── 4. latest revision_id ──
    latest_revision = session.scalar(
        select(DashboardRevision).order_by(DashboardRevision.id.desc()).limit(1)
    )
    result["latest_revision_id"] = latest_revision.id if latest_revision else None

    # ── 5. total_finished ──
    finished_matches = list(session.scalars(
        select(Match).where(Match.status == "final")
    ))
    result["total_finished"] = len(finished_matches)

    # ── Per-match analysis ──
    team_names = {row.id: row.short_name for row in session.scalars(select(Team))}

    match_details = []
    has_match_prediction_count = 0
    has_pre_kickoff_snapshot_count = 0
    actually_scored_count = 0
    missing_snapshot_count = 0
    post_kickoff_snapshot_total = 0

    for match in finished_matches:
        kickoff = _ensure_utc(match.kickoff)

        # Count match_predictions
        mp_count = session.scalar(
            select(func.count()).select_from(MatchPrediction)
            .where(MatchPrediction.match_id == match.id)
        ) or 0

        # Get all snapshots for this match
        snapshots = list(session.scalars(
            select(PredictionSnapshot)
            .where(PredictionSnapshot.match_id == match.id)
            .order_by(PredictionSnapshot.snapshotted_at.desc())
        ))

        pre_kickoff_snaps = [
            s for s in snapshots
            if _ensure_utc(s.snapshotted_at) < kickoff
        ] if kickoff else []

        post_kickoff_snaps = [
            s for s in snapshots
            if _ensure_utc(s.snapshotted_at) >= kickoff
        ] if kickoff else []

        pre_kickoff_count = len(pre_kickoff_snaps)
        post_kickoff_count = len(post_kickoff_snaps)
        post_kickoff_snapshot_total += post_kickoff_count

        # Latest legal snapshot time
        latest_legal_at = None
        if pre_kickoff_snaps:
            latest_legal_at = max(
                _ensure_utc(s.snapshotted_at) for s in pre_kickoff_snaps
            ).isoformat()

        # Determine scorable status
        scorable = False
        unscorable_reason = None

        if match.home_score is None or match.away_score is None:
            unscorable_reason = "no_final_score"
        elif not snapshots:
            unscorable_reason = "no_snapshot_at_all"
        elif pre_kickoff_count == 0:
            unscorable_reason = "no_pre_kickoff_snapshot"
        else:
            scorable = True

        # Counters
        if mp_count > 0:
            has_match_prediction_count += 1
        if pre_kickoff_count > 0:
            has_pre_kickoff_snapshot_count += 1
        if scorable:
            actually_scored_count += 1
        if not scorable:
            missing_snapshot_count += 1

        match_details.append({
            "match_id": match.id,
            "home_team": team_names.get(match.home_team_id, match.home_team_id),
            "away_team": team_names.get(match.away_team_id, match.away_team_id),
            "kickoff": kickoff.isoformat() if kickoff else None,
            "status": match.status,
            "home_score": match.home_score,
            "away_score": match.away_score,
            "match_predictions_count": mp_count,
            "pre_kickoff_snapshots_count": pre_kickoff_count,
            "post_kickoff_snapshots_count": post_kickoff_count,
            "latest_legal_snapshot_at": latest_legal_at,
            "scorable": scorable,
            "unscorable_reason": unscorable_reason,
        })

    # ── 6-10. Summary counts ──
    result["has_match_prediction"] = has_match_prediction_count
    result["has_pre_kickoff_snapshot"] = has_pre_kickoff_snapshot_count
    result["actually_scored"] = actually_scored_count
    result["missing_snapshot"] = missing_snapshot_count
    result["post_kickoff_snapshot_count"] = post_kickoff_snapshot_total

    # ── 11. Per-match details ──
    match_details.sort(key=lambda d: d["kickoff"] or "")
    result["match_details"] = match_details

    # ── 12. model_scores distribution ──
    model_scores_rows = session.execute(
        select(
            ModelScore.matches_scored,
            func.count().label("cnt"),
            func.min(ModelScore.id).label("earliest_id"),
            func.max(ModelScore.id).label("latest_id"),
            func.min(ModelScore.created_at).label("earliest_at"),
            func.max(ModelScore.created_at).label("latest_at"),
        )
        .group_by(ModelScore.matches_scored)
        .order_by(ModelScore.matches_scored)
    ).all()

    result["model_scores_distribution"] = [
        {
            "matches_scored": row.matches_scored,
            "count": row.cnt,
            "earliest_id": row.earliest_id,
            "latest_id": row.latest_id,
            "earliest_at": row.earliest_at.isoformat() if row.earliest_at else None,
            "latest_at": row.latest_at.isoformat() if row.latest_at else None,
        }
        for row in model_scores_rows
    ]

    # ── 13. API vs DB consistency check ──
    # Compare latest model_scores.matches_scored with actually_scored
    api_matches_scored = latest_model_score.matches_scored if latest_model_score else 0
    db_actually_scored = actually_scored_count

    # Also compute via _scorable_snapshot_rows for cross-check
    from app.services.scoring import _scorable_snapshot_rows
    scorable_rows = _scorable_snapshot_rows(session)
    scorable_snapshot_count = len(scorable_rows)
    scorable_match_ids = sorted(set(snap.match_id for snap, _match in scorable_rows))

    consistency_check = {
        "api_model_score_matches_scored": api_matches_scored,
        "db_actually_scored": db_actually_scored,
        "scorable_snapshot_rows_count": scorable_snapshot_count,
        "scorable_match_ids": scorable_match_ids,
        "api_vs_db_consistent": api_matches_scored == db_actually_scored,
        "scorable_rows_vs_db_consistent": scorable_snapshot_count == db_actually_scored,
    }

    # Also check get_match_count_breakdown
    from app.services.scoring import get_match_count_breakdown
    breakdown = get_match_count_breakdown(session)
    consistency_check["breakdown_actually_scored"] = breakdown.actually_scored
    consistency_check["breakdown_total_finished"] = breakdown.total_finished
    consistency_check["breakdown_missing_snapshot"] = breakdown.missing_snapshot
    consistency_check["breakdown_vs_db_consistent"] = breakdown.actually_scored == db_actually_scored

    # Final triple-check
    consistency_check["triple_consistent"] = (
        api_matches_scored == db_actually_scored == breakdown.actually_scored
        and scorable_snapshot_count == db_actually_scored
    )

    result["consistency_check"] = consistency_check

    # ── Disclaimer ──
    result["disclaimer"] = (
        f"这是截至 {audit_time_utc.isoformat()} 的快照，不代表永久固定值。"
        "系统持续运行时数据可能变化。"
    )

    return result


def format_markdown(result: dict) -> str:
    """Format audit result as Markdown."""
    lines = []
    lines.append("# 评分一致性审计报告")
    lines.append("")
    lines.append(f"> **审计时间**: {result['audit_time_utc']}")
    lines.append(f"> **数据库路径**: `{result['db_path']}`")
    lines.append(f"> **免责声明**: {result['disclaimer']}")
    lines.append("")

    # Summary
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| 最新 model_scores.id | {result['latest_model_score_id']} |")
    lines.append(f"| 最新 revision_id | {result['latest_revision_id']} |")
    lines.append(f"| 已完赛比赛数 | {result['total_finished']} |")
    lines.append(f"| 有预测记录的完赛比赛数 | {result['has_match_prediction']} |")
    lines.append(f"| 有合法赛前快照的完赛比赛数 | {result['has_pre_kickoff_snapshot']} |")
    lines.append(f"| 实际进入评分的比赛数 | {result['actually_scored']} |")
    lines.append(f"| 缺少合法赛前快照的完赛比赛数 | {result['missing_snapshot']} |")
    lines.append(f"| 非法赛后快照数量 | {result['post_kickoff_snapshot_count']} |")
    lines.append("")

    # Consistency check
    cc = result["consistency_check"]
    lines.append("## 一致性检查")
    lines.append("")
    lines.append(f"| 检查项 | 值 |")
    lines.append(f"|--------|-----|")
    lines.append(f"| /api/model-score.matches_scored | {cc['api_model_score_matches_scored']} |")
    lines.append(f"| DB actually_scored | {cc['db_actually_scored']} |")
    lines.append(f"| _scorable_snapshot_rows() count | {cc['scorable_snapshot_rows_count']} |")
    lines.append(f"| get_match_count_breakdown().actually_scored | {cc['breakdown_actually_scored']} |")
    lines.append(f"| API vs DB 一致 | {'✅' if cc['api_vs_db_consistent'] else '❌'} |")
    lines.append(f"| scorable_rows vs DB 一致 | {'✅' if cc['scorable_rows_vs_db_consistent'] else '❌'} |")
    lines.append(f"| breakdown vs DB 一致 | {'✅' if cc['breakdown_vs_db_consistent'] else '❌'} |")
    lines.append(f"| **三方一致** | {'✅' if cc['triple_consistent'] else '❌'} |")
    lines.append("")

    # model_scores distribution
    lines.append("## model_scores 分布")
    lines.append("")
    lines.append("| matches_scored | count | earliest_id | latest_id | earliest_at | latest_at |")
    lines.append("|----------------|-------|-------------|-----------|-------------|-----------|")
    for row in result["model_scores_distribution"]:
        lines.append(
            f"| {row['matches_scored']} | {row['count']} | {row['earliest_id']} | "
            f"{row['latest_id']} | {row['earliest_at'] or 'N/A'} | {row['latest_at'] or 'N/A'} |"
        )
    lines.append("")

    # Per-match details
    lines.append("## 每场完赛比赛明细")
    lines.append("")
    lines.append("| match_id | 主队 | 客队 | kickoff | 比分 | 预测数 | 赛前快照 | 赛后快照 | 可评分 | 原因 |")
    lines.append("|----------|------|------|---------|------|--------|----------|----------|--------|------|")
    for d in result["match_details"]:
        score = f"{d['home_score']}-{d['away_score']}" if d['home_score'] is not None else "N/A"
        scorable_label = "✅" if d["scorable"] else "❌"
        reason = d["unscorable_reason"] or ""
        lines.append(
            f"| {d['match_id']} | {d['home_team']} | {d['away_team']} | "
            f"{d['kickoff'][:16] if d['kickoff'] else 'N/A'} | {score} | "
            f"{d['match_predictions_count']} | {d['pre_kickoff_snapshots_count']} | "
            f"{d['post_kickoff_snapshots_count']} | {scorable_label} | {reason} |"
        )
    lines.append("")

    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Scoring consistency audit")
    parser.add_argument(
        "--db",
        default=None,
        help="Database path (default: from .env DATABASE_PATH or backend/world_cup.db)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory for output files (default: current directory)",
    )
    args = parser.parse_args()

    # Resolve db path
    if args.db:
        db_path = args.db
    else:
        # Try .env
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DATABASE_PATH="):
                    db_path = line.split("=", 1)[1].strip()
                    break
            else:
                db_path = "backend/world_cup.db"
        else:
            db_path = "backend/world_cup.db"

    # Resolve relative to project root
    project_root = Path(__file__).resolve().parent.parent.parent
    db_file = (project_root / db_path).resolve()
    if not db_file.exists():
        print(f"ERROR: Database not found at {db_file}", file=sys.stderr)
        sys.exit(1)

    print(f"Database: {db_file}")

    # Create engine and session
    engine = create_database(str(db_file))
    from sqlalchemy.orm import Session as SA_Session
    session = SA_Session(engine)

    try:
        result = run_audit(session, str(db_file))
    finally:
        session.close()

    # Generate timestamp for filenames
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write JSON
    json_path = output_dir / f"audit_scoring_{ts}.json"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    print(f"JSON output: {json_path}")

    # Write Markdown
    md_path = output_dir / f"audit_scoring_{ts}.md"
    md_path.write_text(format_markdown(result), encoding="utf-8")
    print(f"Markdown output: {md_path}")

    # Print summary to stdout
    cc = result["consistency_check"]
    print()
    print("=" * 60)
    print("审计摘要")
    print("=" * 60)
    print(f"  完赛比赛: {result['total_finished']}")
    print(f"  可评分:   {result['actually_scored']}")
    print(f"  缺快照:   {result['missing_snapshot']}")
    print(f"  赛后快照: {result['post_kickoff_snapshot_count']}")
    print()
    print(f"  API matches_scored:              {cc['api_model_score_matches_scored']}")
    print(f"  DB actually_scored:              {cc['db_actually_scored']}")
    print(f"  _scorable_snapshot_rows() count: {cc['scorable_snapshot_rows_count']}")
    print(f"  breakdown.actually_scored:       {cc['breakdown_actually_scored']}")
    print()
    if cc["triple_consistent"]:
        print("  ✅ 三方口径一致")
    else:
        print("  ❌ 三方口径不一致，需要修复！")
        if not cc["api_vs_db_consistent"]:
            print(f"     - API({cc['api_model_score_matches_scored']}) != DB({cc['db_actually_scored']})")
        if not cc["scorable_rows_vs_db_consistent"]:
            print(f"     - scorable_rows({cc['scorable_snapshot_rows_count']}) != DB({cc['db_actually_scored']})")
        if not cc["breakdown_vs_db_consistent"]:
            print(f"     - breakdown({cc['breakdown_actually_scored']}) != DB({cc['db_actually_scored']})")


if __name__ == "__main__":
    main()
