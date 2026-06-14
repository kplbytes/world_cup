"""Calibration analysis for model predictions.

Groups predictions into probability buckets and compares predicted
probability with actual win rate to assess calibration quality.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, MarketSnapshot, PredictionSnapshot


# Probability buckets for calibration
_BUCKETS = [
    (0.30, 0.40, "30-40%"),
    (0.40, 0.50, "40-50%"),
    (0.50, 0.60, "50-60%"),
    (0.60, 0.70, "60-70%"),
    (0.70, 1.01, "70%+"),
]


@dataclass(frozen=True)
class CalibrationBucket:
    label: str
    sample_count: int
    predicted_avg_prob: float
    actual_win_rate: float
    calibration_gap: float


def compute_calibration(session: Session) -> list[dict[str, Any]]:
    """Compute calibration analysis for all scored matches.

    Groups by max predicted probability bucket and compares
    predicted average probability vs actual win rate.
    """
    rows = session.execute(
        select(PredictionSnapshot, Match)
        .join(Match, PredictionSnapshot.match_id == Match.id)
        .where(Match.status == "final")
        .where(PredictionSnapshot.is_pre_match_locked.is_(True))
    ).all()

    if not rows:
        return []

    # For each match, get the max predicted probability and whether it won
    data_points: list[tuple[float, bool]] = []
    for snap, match in rows:
        max_prob = max(snap.home_win, snap.draw, snap.away_win)
        # Determine which outcome was predicted
        predicted_outcome = max(
            [("home", snap.home_win), ("draw", snap.draw), ("away", snap.away_win)],
            key=lambda x: x[1],
        )[0]

        actual_home = match.home_score or 0
        actual_away = match.away_score or 0
        if actual_home > actual_away:
            actual_outcome = "home"
        elif actual_home == actual_away:
            actual_outcome = "draw"
        else:
            actual_outcome = "away"

        outcome_correct = predicted_outcome == actual_outcome
        data_points.append((max_prob, outcome_correct))

    # Group into buckets
    results = []
    for low, high, label in _BUCKETS:
        bucket_points = [(p, w) for p, w in data_points if low <= p < high]
        n = len(bucket_points)
        if n == 0:
            results.append({
                "label": label,
                "sample_count": 0,
                "predicted_avg_prob": 0.0,
                "actual_win_rate": 0.0,
                "calibration_gap": 0.0,
                "note": "样本不足，仅作观察，不作为强结论",
            })
            continue

        avg_prob = sum(p for p, _ in bucket_points) / n
        win_rate = sum(1 for _, w in bucket_points if w) / n
        gap = avg_prob - win_rate

        results.append({
            "label": label,
            "sample_count": n,
            "predicted_avg_prob": round(avg_prob, 4),
            "actual_win_rate": round(win_rate, 4),
            "calibration_gap": round(gap, 4),
            "note": "样本不足，仅作观察，不作为强结论" if n < 5 else "",
        })

    return results


def calibration_report_markdown(session: Session) -> str:
    """Generate a markdown calibration report."""
    buckets = compute_calibration(session)

    lines = [
        "# 概率校准分析报告",
        "",
        "按模型最高预测概率分桶，比较预测概率与实际命中率。",
        "",
        "| 概率区间 | 样本数 | 预测平均概率 | 实际命中率 | 校准偏差 | 备注 |",
        "|----------|--------|-------------|-----------|---------|------|",
    ]

    for b in buckets:
        lines.append(
            f"| {b['label']} | {b['sample_count']} | {b['predicted_avg_prob']:.1%} | "
            f"{b['actual_win_rate']:.1%} | {b['calibration_gap']:+.1%} | {b.get('note', '')} |"
        )

    # Interpretation
    lines.append("")
    lines.append("## 解读")
    lines.append("")
    lines.append("- **校准偏差 > 0**: 模型过度自信，预测概率高于实际命中率")
    lines.append("- **校准偏差 < 0**: 模型不够自信，实际命中率高于预测概率")
    lines.append("- **校准偏差接近 0**: 模型校准良好")

    total = sum(b["sample_count"] for b in buckets)
    if total < 10:
        lines.append("")
        lines.append(f"⚠️ 当前仅 {total} 场比赛样本，统计结论不够可靠。")

    return "\n".join(lines)
