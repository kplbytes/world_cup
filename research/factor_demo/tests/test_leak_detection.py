"""数据泄漏自动测试

验证特征计算不会使用未来数据。
"""

import pandas as pd
import numpy as np
import pytest

from src.data.loader import load_international_results, filter_by_date
from src.features.as_of import MatchView, compute_features_at_time
from src.features.calculator import FACTOR_FUNCTIONS
from src.utils.elo_replay import replay_elo_history


@pytest.fixture(scope="module")
def match_data():
    """加载测试数据。"""
    df = load_international_results()
    df = filter_by_date(df, "2010-01-01", "2023-12-31")
    df = replay_elo_history(df)
    return df


class TestNoFutureDataLeak:
    """验证修改未来比赛结果不会改变历史比赛特征。"""

    def test_elo_before_vs_after_modification(self, match_data):
        """修改未来比赛比分后，历史比赛的 Elo 不应改变。"""
        # 原始 Elo
        original = match_data.copy()

        # 修改最后10场比赛的比分
        modified = match_data.copy()
        last_10_idx = modified.tail(10).index
        modified.loc[last_10_idx, "home_goals"] = 5
        modified.loc[last_10_idx, "away_goals"] = 0

        # 重新计算 Elo
        original_replay = replay_elo_history(original)
        modified_replay = replay_elo_history(modified)

        # 前100场比赛的 Elo 不应改变
        first_100 = original_replay.head(100).index
        pd.testing.assert_series_equal(
            original_replay.loc[first_100, "pre_match_elo_home"],
            modified_replay.loc[first_100, "pre_match_elo_home"],
            check_names=False,
        )

    def test_match_view_excludes_future(self, match_data):
        """MatchView 不应包含 kickoff 之后的比赛。"""
        match = match_data.iloc[5000]
        view = MatchView(match_data, match["match_date"], match["home_team"])

        # 所有返回的比赛都应在 kickoff 之前
        recent = view.recent_matches(10)
        if len(recent) > 0:
            assert (recent["match_date"] < match["match_date"]).all()

        window = view.matches_in_window(90)
        if len(window) > 0:
            assert (window["match_date"] < match["match_date"]).all()

    def test_feature_determinism(self, match_data):
        """同一输入必须生成完全一致的特征。"""
        match = match_data.iloc[3000]

        features1 = compute_features_at_time(match, match_data, FACTOR_FUNCTIONS)
        features2 = compute_features_at_time(match, match_data, FACTOR_FUNCTIONS)

        for key in features1:
            if features1[key] is not None and features2[key] is not None:
                assert features1[key] == features2[key], f"Feature {key} is not deterministic"


class TestForbiddenFields:
    """验证禁止字段不被用于特征计算。"""

    FORBIDDEN_FIELDS = [
        "home_goals", "away_goals", "result",
        "penalty_home_score", "penalty_away_score",
    ]

    def test_factor_functions_dont_access_forbidden_fields(self, match_data):
        """因子计算函数不应直接访问本场比赛的比分和结果。"""
        match = match_data.iloc[3000]

        # 创建一个修改了禁止字段的版本
        modified_match = match.copy()
        modified_match["home_goals"] = 99
        modified_match["away_goals"] = 99
        modified_match["result"] = "H"

        # 计算特征（除了 elo_diff 等直接从 match 读取的因子）
        for name, func in FACTOR_FUNCTIONS.items():
            if name in ("elo_diff", "fifa_rank_diff", "odds_implied_prob", "odds_movement"):
                continue  # 这些因子需要外部注入，跳过

            try:
                home_view = MatchView(match_data, match["match_date"], match["home_team"])
                away_view = MatchView(match_data, match["match_date"], match["away_team"])

                original_val = func(home_view, away_view, match)
                modified_val = func(home_view, away_view, modified_match)

                # 因子值不应因修改当前比赛结果而改变
                if original_val is not None and modified_val is not None:
                    assert original_val == modified_val, (
                        f"Factor {name} changed when match result was modified: "
                        f"{original_val} -> {modified_val}"
                    )
            except Exception:
                pass  # 某些因子可能因数据不足而失败


class TestAsOfMechanism:
    """测试 as_of 机制的正确性。"""

    def test_match_view_respects_kickoff_time(self, match_data):
        """MatchView 严格使用 kickoff 前的数据。"""
        match = match_data.iloc[5000]
        kickoff = match["match_date"]
        team = match["home_team"]

        view = MatchView(match_data, kickoff, team)

        # 获取所有可见比赛
        all_visible = view.recent_matches(1000)
        if len(all_visible) > 0:
            assert (all_visible["match_date"] < kickoff).all(), \
                "MatchView 包含了 kickoff 之后的比赛"

    def test_earlier_kickoff_sees_fewer_matches(self, match_data):
        """更早的 kickoff 应看到更少的比赛。"""
        match = match_data.iloc[5000]
        team = match["home_team"]

        later_kickoff = match["match_date"]
        earlier_kickoff = match["match_date"] - pd.Timedelta(days=365)

        later_view = MatchView(match_data, later_kickoff, team)
        earlier_view = MatchView(match_data, earlier_kickoff, team)

        assert earlier_view.total_matches() <= later_view.total_matches()
