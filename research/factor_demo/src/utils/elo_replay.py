"""Elo 回放模块

按时间顺序回放比赛，计算每场比赛前的 Elo 评分。
严格遵守 as_of 原则。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class EloConfig:
    """Elo 计算配置。"""
    initial_rating: float = 1500.0
    k_factor: float = 30.0
    elo_scale: float = 400.0
    home_advantage: float = 60.0
    friendly_k: float = 15.0  # 友谊赛降权
    friendly_weight: float = 0.5


def replay_elo_history(
    matches: pd.DataFrame,
    config: EloConfig | None = None,
) -> pd.DataFrame:
    """回放所有比赛，计算每场比赛前的 Elo 评分。
    
    返回的 DataFrame 添加以下列：
    - pre_match_elo_home: 主队赛前 Elo
    - pre_match_elo_away: 客队赛前 Elo
    - elo_diff: 主队 Elo - 客队 Elo
    
    注意：必须按时间顺序处理！
    """
    if config is None:
        config = EloConfig()
    
    ratings: dict[str, float] = {}
    matches = matches.sort_values("match_date").copy()
    n = len(matches)
    
    # 预分配结果数组
    pre_elo_home = np.empty(n, dtype=np.float64)
    pre_elo_away = np.empty(n, dtype=np.float64)
    elo_diffs = np.empty(n, dtype=np.float64)
    
    # 提取为 numpy 数组，避免 pandas 逐行开销
    home_teams = matches["home_team"].values
    away_teams = matches["away_team"].values
    home_goals_arr = matches["home_goals"].values.astype(np.float64)
    away_goals_arr = matches["away_goals"].values.astype(np.float64)
    
    # 处理可选列
    if "is_neutral" in matches.columns:
        is_neutral_arr = matches["is_neutral"].values.astype(bool)
    else:
        is_neutral_arr = np.zeros(n, dtype=bool)
    
    if "tournament_category" in matches.columns:
        is_friendly_arr = matches["tournament_category"].values == "friendly"
    else:
        is_friendly_arr = np.zeros(n, dtype=bool)
    
    initial = config.initial_rating
    ha_val = config.home_advantage
    k_val = config.k_factor
    friendly_k_val = config.friendly_k
    scale = config.elo_scale
    
    for i in range(n):
        home = home_teams[i]
        away = away_teams[i]
        
        # 赛前 Elo
        home_rating = ratings.get(home, initial)
        away_rating = ratings.get(away, initial)
        
        pre_elo_home[i] = home_rating
        pre_elo_away[i] = away_rating
        elo_diffs[i] = home_rating - away_rating
        
        # 更新 Elo
        ha = 0.0 if is_neutral_arr[i] else ha_val
        k = friendly_k_val if is_friendly_arr[i] else k_val
        
        expected_home = 1.0 / (1.0 + 10.0 ** ((away_rating - (home_rating + ha)) / scale))
        
        hg = home_goals_arr[i]
        ag = away_goals_arr[i]
        
        if hg > ag:
            actual_home = 1.0
        elif hg < ag:
            actual_home = 0.0
        else:
            actual_home = 0.5
        
        # 进球差修正
        goal_margin = abs(hg - ag)
        margin_mult = 1.0 if goal_margin <= 1 else 1.0 + 0.5 * np.log1p(goal_margin - 1)
        
        change = k * margin_mult * (actual_home - expected_home)
        
        ratings[home] = home_rating + change
        ratings[away] = away_rating - change
    
    matches["pre_match_elo_home"] = pre_elo_home
    matches["pre_match_elo_away"] = pre_elo_away
    matches["elo_diff"] = elo_diffs
    
    return matches


# 模块级缓存：避免 get_elo_at_time 每次调用都从头回放
# 缓存结构: {(matches_id, config_key): (sorted_dates, ratings_snapshots)}
# ratings_snapshots[i] 是第 i 场比赛后的 ratings dict
_elo_cache: dict[tuple[int, tuple], tuple] = {}


def _replay_full_and_cache(matches: pd.DataFrame, config: EloConfig):
    """对完整 matches 做一次回放，缓存每个时间点的评分快照。"""
    cache_key = (id(matches), (config.initial_rating, config.k_factor, config.elo_scale,
                                config.home_advantage, config.friendly_k, config.friendly_weight))
    if cache_key in _elo_cache:
        return _elo_cache[cache_key]

    sorted_matches = matches.sort_values("match_date")
    n = len(sorted_matches)
    initial = config.initial_rating
    ha_val = config.home_advantage
    k_val = config.k_factor
    friendly_k_val = config.friendly_k
    scale = config.elo_scale

    # 提取为 numpy 数组加速
    match_dates = sorted_matches["match_date"].values
    home_teams = sorted_matches["home_team"].values
    away_teams = sorted_matches["away_team"].values
    home_goals_arr = sorted_matches["home_goals"].values.astype(np.float64)
    away_goals_arr = sorted_matches["away_goals"].values.astype(np.float64)

    if "is_neutral" in sorted_matches.columns:
        is_neutral_arr = sorted_matches["is_neutral"].values.astype(bool)
    else:
        is_neutral_arr = np.zeros(n, dtype=bool)

    if "tournament_category" in sorted_matches.columns:
        is_friendly_arr = sorted_matches["tournament_category"].values == "friendly"
    else:
        is_friendly_arr = np.zeros(n, dtype=bool)

    # 存储每场比赛后的 ratings 快照（只存增量：变更的队伍评分）
    # 改用更高效的方式：存储每场比赛后的完整 ratings dict
    ratings: dict[str, float] = {}
    snapshots: list[dict[str, float]] = []

    for i in range(n):
        home = home_teams[i]
        away = away_teams[i]

        home_rating = ratings.get(home, initial)
        away_rating = ratings.get(away, initial)

        ha = 0.0 if is_neutral_arr[i] else ha_val
        k = friendly_k_val if is_friendly_arr[i] else k_val

        expected_home = 1.0 / (1.0 + 10.0 ** ((away_rating - (home_rating + ha)) / scale))

        hg = home_goals_arr[i]
        ag = away_goals_arr[i]

        if hg > ag:
            actual_home = 1.0
        elif hg < ag:
            actual_home = 0.0
        else:
            actual_home = 0.5

        goal_margin = abs(hg - ag)
        margin_mult = 1.0 if goal_margin <= 1 else 1.0 + 0.5 * np.log1p(goal_margin - 1)
        change = k * margin_mult * (actual_home - expected_home)

        ratings[home] = home_rating + change
        ratings[away] = away_rating - change

        # 只存储本轮变更的队伍评分（增量快照）
        snapshots.append({home: ratings[home], away: ratings[away]})

    result = (match_dates, snapshots, initial)
    _elo_cache[cache_key] = result
    return result


def _get_ratings_at_cutoff(match_dates: np.ndarray, snapshots: list[dict[str, float]],
                            initial: float, cutoff: pd.Timestamp) -> dict[str, float]:
    """从缓存快照中重建 cutoff 时间点的 ratings dict。"""
    # 二分查找：找到 cutoff 之前最后一场比赛的索引
    # match_dates 已排序
    idx = np.searchsorted(match_dates, cutoff, side="left") - 1

    if idx < 0:
        return {}  # cutoff 之前没有比赛

    # 重建 ratings：从快照增量合并
    ratings: dict[str, float] = {}
    for i in range(idx + 1):
        ratings.update(snapshots[i])

    # 将未出现的队伍设为 initial（与原逻辑一致，但只在查询时补充）
    return ratings


def get_elo_at_time(
    matches: pd.DataFrame,
    team: str,
    cutoff: pd.Timestamp,
    config: EloConfig | None = None,
) -> float:
    """获取某支球队在某个时间点的 Elo 评分。
    
    使用缓存机制：对同一 matches DataFrame + config 只做一次完整回放，
    后续调用通过快照查找，避免重复计算。
    """
    if config is None:
        config = EloConfig()

    match_dates, snapshots, initial = _replay_full_and_cache(matches, config)
    ratings = _get_ratings_at_cutoff(match_dates, snapshots, initial, cutoff)
    return ratings.get(team, config.initial_rating)
