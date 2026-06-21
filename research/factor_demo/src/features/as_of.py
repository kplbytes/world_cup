"""时间点特征计算机制 (as_of)

核心原则：计算某场比赛的特征时，只能使用该比赛开赛前的数据。
修改未来比赛结果不会改变历史比赛特征。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import numpy as np
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
        #
        # 【设计决策】此处使用严格 < 而非 <= 进行时间戳比较：
        # - 严格 < 确保不包含 kickoff 时刻本身的比赛（即当前比赛）
        # - 若使用 <=，当同一天有多场比赛时会包含与当前比赛同时开赛的其他比赛，
        #   导致数据泄漏（同时间戳的比赛结果在赛前不可知）
        # - 即使 match_date 精确到天，同日比赛之间也可能存在信息泄漏
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
        """获取该球队在指定比赛中的结果序列（向量化版本）。"""
        if len(matches) == 0:
            return pd.Series(dtype=str)

        home_goals = matches["home_goals"].values
        away_goals = matches["away_goals"].values
        is_home = matches["home_team"].values == self._team

        # 主队视角：home_goals > away_goals → W, < → L, = → D
        home_outcome = np.where(
            home_goals > away_goals, "W",
            np.where(home_goals < away_goals, "L", "D")
        )
        # 客队视角：away_goals > home_goals → W, < → L, = → D
        away_outcome = np.where(
            away_goals > home_goals, "W",
            np.where(away_goals < home_goals, "L", "D")
        )

        result = np.where(is_home, home_outcome, away_outcome)
        return pd.Series(result, index=matches.index)
    
    def get_opponent_elo(self, matches: pd.DataFrame) -> pd.Series:
        """获取指定比赛中对手的赛前 Elo 评分。

        对于每场比赛，判断该队是主队还是客队，
        然后返回对手的 pre_match_elo。

        Args:
            matches: 包含 pre_match_elo_home 和 pre_match_elo_away 列的 DataFrame

        Returns:
            对手 Elo 评分的 Series
        """
        def _opp_elo(row):
            if row["home_team"] == self._team:
                return row.get("pre_match_elo_away", 1500.0)
            else:
                return row.get("pre_match_elo_home", 1500.0)
        return matches.apply(_opp_elo, axis=1)

    def get_team_goals(self, matches: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """获取该球队在指定比赛中的进球和失球序列。"""
        if len(matches) == 0:
            return pd.Series(dtype=float), pd.Series(dtype=float)

        home_goals = matches["home_goals"].values
        away_goals = matches["away_goals"].values
        is_home = matches["home_team"].values == self._team

        scored = np.where(is_home, home_goals, away_goals)
        conceded = np.where(is_home, away_goals, home_goals)

        return pd.Series(scored, index=matches.index), pd.Series(conceded, index=matches.index)


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
    
    Args:
        matches: 需要计算特征的比赛数据
        all_matches: 全量比赛数据
        feature_funcs: 特征名到计算函数的映射
        show_progress: 是否显示进度条（tqdm）和每500行进度输出
    """
    results = []
    matches_sorted = matches.sort_values("match_date")
    total = len(matches_sorted)

    use_tqdm = False
    iterator = matches_sorted.iterrows()
    if show_progress:
        try:
            from tqdm import tqdm
            iterator = tqdm(iterator, total=total, desc="Computing features", unit="match")
            use_tqdm = True
        except ImportError:
            pass  # tqdm 不可用时回退到手动进度输出

    for i, (idx, match) in enumerate(iterator):
        features = compute_features_at_time(match, all_matches, feature_funcs)
        features["match_id"] = match.get("match_id", idx)
        results.append(features)

        # tqdm 不可用时，每 500 行打印进度
        if show_progress and not use_tqdm and (i + 1) % 500 == 0:
            print(f"  [progress] {i + 1}/{total} matches processed ({(i + 1) / total * 100:.1f}%)")

    feature_df = pd.DataFrame(results)
    return feature_df


def verify_no_leakage(all_matches: pd.DataFrame, feature_df: pd.DataFrame) -> list[str]:
    """运行时泄漏检测：验证特征计算未使用未来数据。

    对每场比赛，通过添加虚假未来比赛并重新计算特征来检测泄漏。
    如果添加未来数据后特征值发生变化，则判定为泄漏。

    Args:
        all_matches: 原始全量比赛数据（必须包含 match_date 列）
        feature_df: 已计算的特征 DataFrame（必须包含 match_id 列）

    Returns:
        违规描述列表，空列表表示无泄漏
    """
    from .calculator import FACTOR_FUNCTIONS

    violations: list[str] = []

    # 采样检测：不检查所有比赛，取前 50 场有足够历史的比赛
    matches_with_history = all_matches[all_matches.index > 100].head(50)

    for idx, match in matches_with_history.iterrows():
        kickoff = match["match_date"]
        match_id = match.get("match_id", idx)

        # 构造虚假未来比赛
        fake_matches = pd.DataFrame([
            {
                "match_date": kickoff + pd.Timedelta(days=i + 1),
                "home_team": f"_LEAK_TEST_{i % 5}",
                "away_team": f"_LEAK_TEST_{(i + 2) % 5}",
                "home_goals": 3,
                "away_goals": 0,
                "tournament": "Friendly",
                "is_neutral": True,
                "is_official": False,
                "tournament_category": "friendly",
            }
            for i in range(20)
        ])

        augmented = pd.concat([all_matches, fake_matches], ignore_index=True)

        # 用原始数据和增强数据分别计算特征
        original_features = compute_features_at_time(match, all_matches, FACTOR_FUNCTIONS)
        augmented_features = compute_features_at_time(match, augmented, FACTOR_FUNCTIONS)

        # 比较特征值
        for key in original_features:
            orig_val = original_features[key]
            aug_val = augmented_features[key]

            if orig_val is None or aug_val is None:
                continue

            if orig_val != aug_val:
                violations.append(
                    f"match_id={match_id}, factor={key}: "
                    f"original={orig_val}, after_future_perturbation={aug_val}"
                )

    return violations
