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

    def add(label: str, condition: bool) -> None:
        if condition and label not in traits:
            traits.append(label)

    strong_sample_count = tier_stats["elite"]["sample_count"] + tier_stats["strong"]["sample_count"]
    weak_sample_count = tier_stats["weak"]["sample_count"]
    knockout_sample_count = metrics.get("knockout_sample_count", 0)
    qualifier_sample_count = metrics.get("qualifier_sample_count", 0)
    world_cup_group_sample_count = metrics.get("world_cup_group_sample_count", 0)
    team_elo = metrics.get("team_elo", 1500.0)

    # 进攻产出
    add("进攻火力顶级", metrics["goal_for_avg"] >= 2.35 and team_elo >= 1800)
    add("进攻产量高", metrics["goal_for_avg"] >= 2.35 and team_elo < 1800)
    add("进攻火力强", 2.05 <= metrics["goal_for_avg"] < 2.35 and team_elo >= 1700)
    add("进攻输出偏强", 2.05 <= metrics["goal_for_avg"] < 2.35 and team_elo < 1700)
    add("稳定破门", metrics["goal_for_avg"] >= 1.7 and metrics["failed_to_score_rate"] <= 0.18)
    add("进攻输出偏弱", metrics["goal_for_avg"] <= 1.25)
    add("进攻哑火风险", metrics["failed_to_score_rate"] >= 0.3)
    add("进攻输出中等", 1.25 < metrics["goal_for_avg"] < 2.05 and metrics["failed_to_score_rate"] < 0.3)

    # 防守质量
    add("防线稳固", metrics["goal_against_avg"] <= 0.8 and metrics["clean_sheet_rate"] >= 0.45)
    add("零封能力强", metrics["clean_sheet_rate"] >= 0.55)
    add("防守波动偏大", metrics["goal_against_avg"] >= 1.2)
    add("失球压力高", metrics["goal_against_avg"] >= 1.35)
    add("防守中等稳健", 0.8 < metrics["goal_against_avg"] < 1.2)

    # 比赛节奏和比分形态
    add("低比分倾向", metrics["low_score_tendency"] >= 0.65)
    add("开放对攻倾向", metrics["over_2_5_rate"] >= 0.58 or metrics["high_score_tendency"] >= 0.38)
    add("双方进球概率高", metrics["both_teams_score_rate"] >= 0.5)
    add("比赛节奏保守", metrics["under_2_5_rate"] >= 0.7)
    add("节奏均衡", 0.35 < metrics["under_2_5_rate"] < 0.65 and metrics["high_score_tendency"] < 0.38)

    # 结果稳定性
    add("近期结果稳定", metrics["recent_tournament_consistency"] >= 0.85)
    add("近期波动明显", metrics["recent_tournament_consistency"] <= 0.62)
    add("平局倾向高", metrics["draw_rate_overall"] >= 0.34)
    add("胜负分明", metrics["draw_rate_overall"] <= 0.16)

    # 按对手强弱拆解
    if metrics["draw_resilience_score"] >= 0.6 and metrics["underdog_win_or_draw_rate"] >= 0.65 and strong_sample_count >= 4:
        traits.append("遇强韧性高")
    add("遇强抗压不足", metrics["underdog_win_or_draw_rate"] <= 0.3 and strong_sample_count >= 4)
    add("弱旅虐菜稳定", metrics["favorite_win_rate"] >= 0.72 and weak_sample_count >= 3)
    add("强队翻车风险", metrics["favorite_overconfidence_risk"] >= 0.45 and weak_sample_count >= 3)
    add("防守优先", metrics["defensive_resilience_score"] >= 0.68)

    # 杯赛和预选赛画像
    add("大赛经验丰富", metrics["world_cup_experience_score"] >= 0.65)
    add("淘汰赛履历强", metrics["knockout_experience_score"] >= 0.4)
    add("杯赛压力表现好", metrics["pressure_match_score"] >= 0.75 and knockout_sample_count >= 2)
    add("首战慢热", metrics["opening_match_slow_start_score"] >= 0.65 and metrics.get("opening_sample_count", 0) >= 2)
    add("小组赛稳定", metrics["group_stage_consistency"] >= 0.8 and world_cup_group_sample_count >= 3)
    add("预选赛抢分强", metrics.get("qualifier_win_rate", 0.0) >= 0.6 and qualifier_sample_count >= 6)
    add("预选赛稳定", metrics.get("qualifier_not_loss_rate", 0.0) >= 0.75 and qualifier_sample_count >= 6)
    return traits
