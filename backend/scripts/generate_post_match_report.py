"""Post-match review report generator.

Generates a markdown report after each match day summarizing
model performance, error patterns, and recommendations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MarketSnapshot, Match, PredictionSnapshot, Team
from app.services.error_attribution import classify_error
from app.services.scoring import score_predictions

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def generate_post_match_report(
    session: Session,
    output_path: Path | None = None,
    report_date: datetime | None = None,
) -> str:
    """Generate a post-match review report for the previous day's matches.

    Args:
        session: Database session
        output_path: Where to write the report (defaults to artifacts/)
        report_date: The date to generate the report for (defaults to yesterday)
    """
    from app.services.calibration import compute_calibration
    from app.services.market_comparison import compute_market_comparison
    from app.services.model_recommendation import get_model_recommendation
    from app.services.scoring import model_score_by_version

    now = report_date or datetime.now(timezone.utc)
    local_now = now.astimezone(_SHANGHAI)
    local_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = (local_today - timedelta(days=1)).astimezone(timezone.utc)
    today_start = local_today.astimezone(timezone.utc)

    # Get team names
    team_names = {row.id: row.short_name for row in session.scalars(select(Team))}

    # Get final matches from yesterday
    all_matches = list(session.scalars(select(Match).where(Match.status == "final")))
    yesterday_matches = []
    for m in all_matches:
        kickoff = m.kickoff
        if kickoff and kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        if kickoff and yesterday_start <= kickoff < today_start:
            yesterday_matches.append(m)

    # Get snapshots
    market_snaps = {
        row.match_id: row
        for row in session.scalars(select(MarketSnapshot).where(MarketSnapshot.provider == "sporttery"))
    }

    snap_pairs = []
    for m in yesterday_matches:
        snap = session.scalar(
            select(PredictionSnapshot)
            .where(PredictionSnapshot.match_id == m.id)
            .where(PredictionSnapshot.is_pre_match_locked.is_(True))
        )
        if snap:
            snap_pairs.append((snap, m))

    # Score yesterday's matches
    report = score_predictions(snap_pairs, team_names, market_snaps)

    # Build report
    lines = [
        f"# 赛后复盘报告 - {local_now.strftime('%Y-%m-%d')}",
        "",
        f"报告生成时间: {local_now.strftime('%Y-%m-%d %H:%M')} (北京时间)",
        "",
    ]

    # 1. Yesterday's matches
    lines.append("## 昨日比赛结果")
    lines.append("")
    if not yesterday_matches:
        lines.append("昨日无已结束比赛。")
    else:
        for m in yesterday_matches:
            home_name = team_names.get(m.home_team_id, m.home_team_id)
            away_name = team_names.get(m.away_team_id, m.away_team_id)
            lines.append(f"- **{home_name} {m.home_score} : {m.away_score} {away_name}**")

    lines.append("")

    # 2. Model prediction results
    lines.append("## 模型预测命中情况")
    lines.append("")
    if report.matches_scored == 0:
        lines.append("昨日无可评分的赛前锁定快照。")
    else:
        lines.append(f"- 评分比赛数: {report.matches_scored}")
        lines.append(f"- 命中率: {report.outcome_hit_rate:.1%}")
        lines.append(f"- Top 3 比分命中: {report.top_score_hit_rate:.1%}")
        lines.append(f"- xG 平均误差: {report.xg_mae:.2f}")
    lines.append("")

    # 3. Brier / LogLoss
    lines.append("## Brier / LogLoss")
    lines.append("")
    if report.matches_scored > 0:
        lines.append(f"- 平均 Brier: {report.brier_score:.4f}")
        lines.append(f"- 平均 LogLoss: {report.log_loss:.4f}")
    lines.append("")

    # 4. Error types
    lines.append("## 错误类型分析")
    lines.append("")
    error_counts: dict[str, int] = {}
    for detail in report.per_match:
        for et in detail.error_types:
            error_counts[et] = error_counts.get(et, 0) + 1

    if error_counts:
        for et, count in sorted(error_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {et}: {count} 场")
    else:
        lines.append("无错误类型记录。")
    lines.append("")

    # 5. Per-match details
    lines.append("## 逐场详情")
    lines.append("")
    for detail in report.per_match:
        hit_mark = "✅" if detail.outcome_correct else "❌"
        lines.append(f"### {hit_mark} {detail.home_team} vs {detail.away_team}")
        lines.append(f"- 预测: 主胜 {detail.predicted['home_win']:.1%} / 平 {detail.predicted['draw']:.1%} / 客胜 {detail.predicted['away_win']:.1%}")
        lines.append(f"- 实际: {detail.actual['home_score']} : {detail.actual['away_score']} ({detail.actual_result})")
        lines.append(f"- Brier: {detail.brier:.4f}, LogLoss: {detail.logloss:.4f}")
        if detail.error_types:
            lines.append(f"- 错误类型: {', '.join(detail.error_types)}")
        if detail.suggested_fixes:
            for fix in detail.suggested_fixes:
                if fix:
                    lines.append(f"- 建议: {fix}")
        lines.append("")

    # 6. Market comparison
    market_comp = compute_market_comparison(session)
    lines.append("## 市场赔率对比")
    lines.append("")
    if market_comp["market_sample_count"] > 0:
        lines.append(f"- 有市场数据比赛: {market_comp['market_sample_count']} 场")
        lines.append(f"- 模型 Brier: {market_comp['model_brier']:.4f}")
        lines.append(f"- 市场 Brier: {market_comp['market_brier']:.4f}")
        lines.append(f"- Blend Brier: {market_comp['blended_brier']:.4f}")
        if market_comp["model_brier"] <= market_comp["market_brier"]:
            lines.append("- **模型更准**")
        else:
            lines.append("- **市场更准**")
        lines.append(f"- 建议市场权重: {market_comp['suggested_market_blend_weight']}")
    else:
        lines.append("暂无市场赔率数据。")
    lines.append("")

    # 7. Warning value
    warning_helped = sum(1 for d in report.per_match if d.warning_effect == "helped")
    warning_hurt = sum(1 for d in report.per_match if d.warning_effect == "hurt")
    lines.append("## 情报警告价值")
    lines.append("")
    lines.append(f"- 警告有帮助(预测错误且存在警告): {warning_helped} 场")
    lines.append(f"- 警告有损害(预测正确但存在警告): {warning_hurt} 场")
    lines.append("")

    # 8. Numerical value
    num_helped = sum(1 for d in report.per_match if d.numerical_effect == "helped")
    num_hurt = sum(1 for d in report.per_match if d.numerical_effect == "hurt")
    lines.append("## 数值修正价值")
    lines.append("")
    lines.append(f"- 数值修正有帮助: {num_helped} 场")
    lines.append(f"- 数值修正有损害: {num_hurt} 场")
    lines.append("")

    # 9. Model recommendation
    recommendation = get_model_recommendation(session)
    lines.append("## 下一轮参数建议")
    lines.append("")
    lines.append(f"- 推荐模型版本: {recommendation['recommended_model_version']}")
    lines.append(f"- 推荐理由: {recommendation['reason']}")
    lines.append(f"- 置信度: {recommendation['confidence']}")
    if recommendation.get("sample_warning"):
        lines.append(f"- ⚠️ {recommendation['sample_warning']}")
    lines.append(f"- 备用版本: {recommendation['fallback_model_version']}")
    lines.append("")

    # Write to file
    md_content = "\n".join(lines)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md_content)

    return md_content
