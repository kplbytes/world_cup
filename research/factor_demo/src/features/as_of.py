"""时间点特征计算机制 (as_of)

核心原则：计算某场比赛的特征时，只能使用该比赛开赛前的数据。
修改未来比赛结果不会改变历史比赛特征。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import pandas as pd


class MatchView:
    """截至某场比赛开赛前的数据视图。
    
    所有通过此视图获取的数据，都严格限制在 kickoff 时间之前。
    """
    
    def __init__(self, all_matches: pd.DataFrame, kickoff: pd.Timestamp, team: str):
        self._all_matches = all_matches
        self._kickoff = kickoff
        self._team = team
        
        # 预计算：该球队在 kickoff 之前的所有比赛
        before = all_matches[
            (all_matches["match_date"] < kickoff)
            & ((all_matches["home_team"] == team) | (all_matches["away_team"] == team))
        ].sort_values("match_date", ascending=False)
        self._team_matches_before = before
        
    @property
    def kickoff(self) -> pd.Timestamp:
        return self._kickoff
    
    @property
    def team(self) -> str:
        return self._team
    
    def recent_matches(self, n: int = 5, official_only: bool = False) -> pd.DataFrame:
        """获取最近 n 场比赛。"""
        matches = self._team_matches_before
        if official_only:
            matches = matches[matches["is_official"] == True]
        return matches.head(n)
    
    def matches_in_window(self, days: int = 30) -> pd.DataFrame:
        """获取过去指定天数内的比赛。"""
        cutoff = self._kickoff - pd.Timedelta(days=days)
        return self._team_matches_before[self._team_matches_before["match_date"] >= cutoff]
    
    def matches_by_venue(self, venue_type: str = "home", n: int = 10) -> pd.DataFrame:
        """按场地类型获取比赛。
        
        venue_type: 'home', 'away', 'neutral'
        """
        matches = self._team_matches_before
        if venue_type == "home":
            matches = matches[matches["home_team"] == self._team]
            matches = matches[~matches["is_neutral"]]
        elif venue_type == "away":
            matches = matches[matches["away_team"] == self._team]
            matches = matches[~matches["is_neutral"]]
        elif venue_type == "neutral":
            matches = matches[matches["is_neutral"] == True]
        return matches.head(n)
    
    def h2h_matches(self, opponent: str, n: int = 5) -> pd.DataFrame:
        """获取与对手的历史交锋。"""
        matches = self._team_matches_before
        h2h = matches[
            ((matches["home_team"] == opponent) | (matches["away_team"] == opponent))
        ]
        return h2h.head(n)
    
    def matches_by_tournament_category(self, category: str, n: int = 10) -> pd.DataFrame:
        """按赛事类别获取比赛。"""
        matches = self._team_matches_before
        return matches[matches["tournament_category"] == category].head(n)
    
    def days_since_last_match(self) -> int | None:
        """距上一场比赛的天数。"""
        if len(self._team_matches_before) == 0:
            return None
        last_match_date = self._team_matches_before.iloc[0]["match_date"]
        delta = self._kickoff - last_match_date
        return delta.days
    
    def total_matches(self) -> int:
        """该球队在 kickoff 前的总比赛数。"""
        return len(self._team_matches_before)
    
    def get_team_outcomes(self, matches: pd.DataFrame) -> pd.Series:
        """获取该球队在指定比赛中的结果序列。"""
        def outcome(row):
            if row["home_team"] == self._team:
                return "W" if row["home_goals"] > row["away_goals"] else ("L" if row["home_goals"] < row["away_goals"] else "D")
            else:
                return "W" if row["away_goals"] > row["home_goals"] else ("L" if row["away_goals"] < row["home_goals"] else "D")
        return matches.apply(outcome, axis=1)
    
    def get_team_goals(self, matches: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """获取该球队在指定比赛中的进球和失球序列。"""
        def goals(row):
            if row["home_team"] == self._team:
                return row["home_goals"], row["away_goals"]
            else:
                return row["away_goals"], row["home_goals"]
        results = matches.apply(lambda r: pd.Series(goals(r), index=["scored", "conceded"]), axis=1)
        return results["scored"], results["conceded"]


def create_match_views(
    match: pd.Series,
    all_matches: pd.DataFrame,
) -> tuple[MatchView, MatchView]:
    """为一场比赛创建主客队的 MatchView。"""
    home_view = MatchView(all_matches, match["match_date"], match["home_team"])
    away_view = MatchView(all_matches, match["match_date"], match["away_team"])
    return home_view, away_view


def compute_features_at_time(
    match: pd.Series,
    all_matches: pd.DataFrame,
    feature_funcs: dict[str, callable],
) -> dict:
    """在某个时间点计算一场比赛的所有特征。
    
    Args:
        match: 当前比赛行
        all_matches: 全量比赛数据
        feature_funcs: 特征名到计算函数的映射
    
    Returns:
        特征字典
    """
    home_view, away_view = create_match_views(match, all_matches)
    
    features = {}
    for name, func in feature_funcs.items():
        try:
            features[name] = func(home_view, away_view, match)
        except Exception as e:
            features[name] = None
    
    return features


def compute_all_features(
    matches: pd.DataFrame,
    all_matches: pd.DataFrame,
    feature_funcs: dict[str, callable],
    show_progress: bool = True,
) -> pd.DataFrame:
    """为所有比赛计算特征。
    
    严格按时间顺序处理，确保不会使用未来数据。
    """
    results = []
    matches_sorted = matches.sort_values("match_date")
    
    for idx, match in matches_sorted.iterrows():
        features = compute_features_at_time(match, all_matches, feature_funcs)
        features["match_id"] = match.get("match_id", idx)
        results.append(features)
    
    feature_df = pd.DataFrame(results)
    return feature_df
