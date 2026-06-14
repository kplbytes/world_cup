from __future__ import annotations

from collections import defaultdict
from typing import Iterable


def classify_opponent_tier(elo: float | None) -> str:
    if elo is None:
        return "mid"
    if elo >= 1850:
        return "elite"
    if elo >= 1700:
        return "strong"
    if elo >= 1450:
        return "mid"
    return "weak"


def rate(rows: list, predicate) -> float:
    return sum(1 for row in rows if predicate(row)) / len(rows) if rows else 0.0


def tier_statistics(rows: Iterable) -> dict:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.opponent_tier].append(row)
    result = {}
    for tier in ("elite", "strong", "mid", "weak"):
        items = grouped[tier]
        count = len(items)
        result[tier] = {
            "sample_count": count,
            "win_rate": rate(items, lambda x: x.result == "win"),
            "draw_rate": rate(items, lambda x: x.result == "draw"),
            "loss_rate": rate(items, lambda x: x.result == "loss"),
            "goal_for_avg": sum(x.goals_for for x in items) / count if count else 0.0,
            "goal_against_avg": sum(x.goals_against for x in items) / count if count else 0.0,
        }
    return result


def generate_traits(metrics: dict, sample_count: int, tier_stats: dict) -> list[str]:
    if sample_count < 6:
        return []
    traits = []
    if metrics["draw_resilience_score"] >= 0.6 and tier_stats["elite"]["sample_count"] + tier_stats["strong"]["sample_count"] >= 4:
        traits.append("遇强韧性高")
    if metrics["favorite_win_rate"] >= 0.72 and tier_stats["weak"]["sample_count"] >= 3:
        traits.append("弱旅虐菜稳定")
    if metrics["draw_rate_overall"] >= 0.34:
        traits.append("平局倾向高")
    if metrics["low_score_tendency"] >= 0.65:
        traits.append("低比分倾向")
    if metrics["defensive_resilience_score"] >= 0.68:
        traits.append("防守优先")
    if metrics["favorite_overconfidence_risk"] >= 0.45 and tier_stats["weak"]["sample_count"] >= 3:
        traits.append("强队翻车风险")
    if metrics["world_cup_experience_score"] >= 0.65:
        traits.append("大赛经验丰富")
    if metrics["opening_match_slow_start_score"] >= 0.55:
        traits.append("首战慢热")
    if metrics["high_score_tendency"] >= 0.62:
        traits.append("高比分倾向")
    return traits
