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

    Knockout matches that ended level after extra time / penalties are
    scored by the advancing team (using ``home_advance`` / ``away_advance``)
    so a 90-minute draw that the model correctly identified as a tight game
    is not double-penalised. The aggregate row keeps the historical shape;
    per-stage breakdown is exposed via the ``stage_breakdown`` field so
    callers can see group vs knockout calibration independently.
    """
    rows = session.execute(
        select(PredictionSnapshot, Match)
        .join(Match, PredictionSnapshot.match_id == Match.id)
        .where(Match.status == "final")
        .where(PredictionSnapshot.is_pre_match_locked.is_(True))
    ).all()

    if not rows:
        return []

    def _resolve_actual_outcome(match: Match) -> str:
        actual_home = match.home_score or 0
        actual_away = match.away_score or 0
        if actual_home > actual_away:
            return "home"
        if actual_home < actual_away:
            return "away"
        # Level scores — only a "draw" for group stage. Knockout matches
        # that went to ET/penalties have home_advance/away_advance set.
        is_knockout = bool(match.stage and match.stage != "group")
        if is_knockout:
            if getattr(match, "home_advance", None) is True:
                return "home"
            if getattr(match, "away_advance", None) is True:
                return "away"
        return "draw"

    # For each match, get the max predicted probability and whether it won
    data_points: list[tuple[float, bool, str]] = []
    for snap, match in rows:
        max_prob = max(snap.home_win, snap.draw, snap.away_win)
        # Determine which outcome was predicted
        predicted_outcome = max(
            [("home", snap.home_win), ("draw", snap.draw), ("away", snap.away_win)],
            key=lambda x: x[1],
        )[0]

        actual_outcome = _resolve_actual_outcome(match)
        outcome_correct = predicted_outcome == actual_outcome
        stage = "knockout" if (match.stage and match.stage != "group") else "group"
        data_points.append((max_prob, outcome_correct, stage))

    def _bucket(points: list[tuple[float, bool, str]]) -> list[dict[str, Any]]:
        results = []
        for low, high, label in _BUCKETS:
            bucket_points = [(p, w) for p, w, _ in points if low <= p < high]
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

    aggregate = _bucket(data_points)
    # Attach a per-stage breakdown so callers can inspect knockout calibration
    # independently of group stage (knockout samples are scarce but important).
    for row, label in zip(aggregate, [b[2] for b in _BUCKETS]):
        row["stage_breakdown"] = {
            stage: _bucket([p for p in data_points if p[2] == stage])
            for stage in ("group", "knockout")
        }
    return aggregate


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
