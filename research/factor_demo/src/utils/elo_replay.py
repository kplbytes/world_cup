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
    
    pre_elo_home = []
    pre_elo_away = []
    elo_diffs = []
    
    for _, match in matches.iterrows():
        home = match["home_team"]
        away = match["away_team"]
        
        # 赛前 Elo
        home_rating = ratings.get(home, config.initial_rating)
        away_rating = ratings.get(away, config.initial_rating)
        
        pre_elo_home.append(home_rating)
        pre_elo_away.append(away_rating)
        elo_diffs.append(home_rating - away_rating)
        
        # 更新 Elo
        is_neutral = match.get("is_neutral", False)
        is_friendly = match.get("tournament_category") == "friendly"
        
        ha = 0.0 if is_neutral else config.home_advantage
        k = config.friendly_k if is_friendly else config.k_factor
        
        expected_home = 1.0 / (1.0 + 10 ** ((away_rating - (home_rating + ha)) / config.elo_scale))
        
        home_goals = match["home_goals"]
        away_goals = match["away_goals"]
        
        if home_goals > away_goals:
            actual_home = 1.0
        elif home_goals < away_goals:
            actual_home = 0.0
        else:
            actual_home = 0.5
        
        # 进球差修正
        goal_margin = abs(home_goals - away_goals)
        margin_mult = 1.0 if goal_margin <= 1 else 1.0 + 0.5 * np.log1p(goal_margin - 1)
        
        change = k * margin_mult * (actual_home - expected_home)
        
        ratings[home] = home_rating + change
        ratings[away] = away_rating - change
    
    matches["pre_match_elo_home"] = pre_elo_home
    matches["pre_match_elo_away"] = pre_elo_away
    matches["elo_diff"] = elo_diffs
    
    return matches


def get_elo_at_time(
    matches: pd.DataFrame,
    team: str,
    cutoff: pd.Timestamp,
    config: EloConfig | None = None,
) -> float:
    """获取某支球队在某个时间点的 Elo 评分。"""
    if config is None:
        config = EloConfig()
    
    ratings: dict[str, float] = {}
    before = matches[matches["match_date"] < cutoff].sort_values("match_date")
    
    for _, match in before.iterrows():
        home = match["home_team"]
        away = match["away_team"]
        
        home_rating = ratings.get(home, config.initial_rating)
        away_rating = ratings.get(away, config.initial_rating)
        
        is_neutral = match.get("is_neutral", False)
        is_friendly = match.get("tournament_category") == "friendly"
        ha = 0.0 if is_neutral else config.home_advantage
        k = config.friendly_k if is_friendly else config.k_factor
        
        expected_home = 1.0 / (1.0 + 10 ** ((away_rating - (home_rating + ha)) / config.elo_scale))
        
        home_goals = match["home_goals"]
        away_goals = match["away_goals"]
        
        if home_goals > away_goals:
            actual_home = 1.0
        elif home_goals < away_goals:
            actual_home = 0.0
        else:
            actual_home = 0.5
        
        goal_margin = abs(home_goals - away_goals)
        margin_mult = 1.0 if goal_margin <= 1 else 1.0 + 0.5 * np.log1p(goal_margin - 1)
        change = k * margin_mult * (actual_home - expected_home)
        
        ratings[home] = home_rating + change
        ratings[away] = away_rating - change
    
    return ratings.get(team, config.initial_rating)
