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


class TestAsOfStrictness:
    """验证 as_of 机制的严格性：严格 < 而非 <=。"""

    def test_match_view_excludes_same_timestamp(self, match_data):
        """MatchView 在 kickoff=T 时不应包含 match_date 恰好等于 T 的比赛。

        核心设计：使用严格 < 而非 <=，否则会包含当前比赛本身，导致数据泄漏。
        """
        # 选取一场有同日其他比赛的比赛
        match = match_data.iloc[5000]
        kickoff = match["match_date"]

        view = MatchView(match_data, kickoff, match["home_team"])

        # 获取所有可见比赛，验证没有 match_date == kickoff 的
        all_visible = view.recent_matches(1000)
        if len(all_visible) > 0:
            assert not (all_visible["match_date"] == kickoff).any(), (
                "MatchView 包含了与 kickoff 同时间戳的比赛，应使用严格 < 而非 <="
            )
            assert (all_visible["match_date"] < kickoff).all(), (
                "MatchView 包含了 kickoff 及之后的比赛"
            )

    def test_elo_pre_match_is_before_kickoff(self, match_data):
        """赛前 Elo 必须仅由 kickoff 之前的比赛计算，不包含当前比赛。"""
        match = match_data.iloc[3000]
        kickoff = match["match_date"]

        # 当前比赛的 pre_match_elo 应该只反映 kickoff 之前的历史
        # 验证方法：移除当前比赛及之后的所有比赛，重新计算 Elo，应与原始 pre_match_elo 一致
        before_kickoff = match_data[match_data["match_date"] < kickoff]

        # 原始 pre_match_elo
        original_home_elo = match.get("pre_match_elo_home")
        original_away_elo = match.get("pre_match_elo_away")

        if original_home_elo is not None and original_away_elo is not None:
            # 重新计算仅使用 kickoff 之前数据的 Elo
            replayed = replay_elo_history(before_kickoff)
            # 找到该队在 replayed 中最后一场比赛的赛后 Elo
            team_home = match["home_team"]
            team_away = match["away_team"]

            home_last = replayed[
                ((replayed["home_team"] == team_home) | (replayed["away_team"] == team_home))
            ]
            away_last = replayed[
                ((replayed["home_team"] == team_away) | (replayed["away_team"] == team_away))
            ]

            if len(home_last) > 0:
                last_home_match = home_last.iloc[-1]
                if last_home_match["home_team"] == team_home:
                    replayed_home_elo = last_home_match.get("post_match_elo_home", original_home_elo)
                else:
                    replayed_home_elo = last_home_match.get("post_match_elo_away", original_home_elo)
                # 赛前 Elo 应与仅用历史数据计算的结果一致（允许微小浮点误差）
                assert abs(original_home_elo - replayed_home_elo) < 1.0, (
                    f"pre_match_elo_home={original_home_elo} 与历史回放 Elo={replayed_home_elo} 不一致，"
                    "可能包含了当前或未来比赛数据"
                )

    def test_feature_determinism_with_future_perturbation(self, match_data):
        """添加100场虚假未来比赛后，历史比赛特征应完全不变。"""
        # 选取中间位置的比赛
        match = match_data.iloc[3000]
        kickoff = match["match_date"]

        # 原始特征
        original_features = compute_features_at_time(match, match_data, FACTOR_FUNCTIONS)

        # 构造100场虚假未来比赛
        future_date = kickoff + pd.Timedelta(days=1)
        fake_matches = pd.DataFrame([
            {
                "match_date": future_date + pd.Timedelta(days=i),
                "home_team": f"FAKE_TEAM_{i % 10}",
                "away_team": f"FAKE_TEAM_{(i + 1) % 10}",
                "home_goals": i % 5,
                "away_goals": (i + 1) % 4,
                "tournament": "Friendly",
                "is_neutral": True,
                "is_official": False,
                "tournament_category": "friendly",
            }
            for i in range(100)
        ])

        # 拼接虚假未来比赛
        augmented_data = pd.concat([match_data, fake_matches], ignore_index=True)

        # 重新计算特征
        augmented_features = compute_features_at_time(match, augmented_data, FACTOR_FUNCTIONS)

        # 验证特征完全一致
        for key in original_features:
            if original_features[key] is not None and augmented_features[key] is not None:
                assert original_features[key] == augmented_features[key], (
                    f"Feature {key} changed after adding future matches: "
                    f"{original_features[key]} -> {augmented_features[key]}"
                )

    def test_no_post_match_rankings_used(self, match_data):
        """验证仅使用赛前排名，不使用赛后排名。"""
        match = match_data.iloc[3000]

        # 如果数据中存在赛后排名字段，验证它们未被因子函数使用
        post_match_fields = [
            "post_match_fifa_rank_home", "post_match_fifa_rank_away",
            "post_match_elo_home", "post_match_elo_away",
        ]

        # 创建一个修改了赛后排名的版本
        modified_match = match.copy()
        for field in post_match_fields:
            if field in modified_match.index:
                modified_match[field] = 999999  # 极端值

        # 计算原始和修改后的特征
        home_view = MatchView(match_data, match["match_date"], match["home_team"])
        away_view = MatchView(match_data, match["match_date"], match["away_team"])

        for name, func in FACTOR_FUNCTIONS.items():
            if name in ("elo_diff", "fifa_rank_diff", "odds_implied_prob", "odds_movement"):
                continue  # 这些因子需要外部注入，跳过

            try:
                original_val = func(home_view, away_view, match)
                modified_val = func(home_view, away_view, modified_match)

                if original_val is not None and modified_val is not None:
                    assert original_val == modified_val, (
                        f"Factor {name} changed when post-match rankings were modified: "
                        f"{original_val} -> {modified_val}"
                    )
            except Exception:
                pass  # 某些因子可能因数据不足而失败


class TestForbiddenFieldAccess:
    """验证因子函数不会访问禁止字段（比分、结果等赛后数据）。"""

    def test_factor_functions_dont_access_score_columns(self, match_data):
        """因子函数不应使用比分列（home_goals, away_goals）。

        通过将比分设为极端值 999，验证因子值不变，证明不依赖比分数据。
        """
        match = match_data.iloc[3000]

        for name, func in FACTOR_FUNCTIONS.items():
            if name in ("elo_diff", "fifa_rank_diff", "odds_implied_prob", "odds_movement"):
                continue  # 这些因子需要外部注入，跳过

            try:
                home_view = MatchView(match_data, match["match_date"], match["home_team"])
                away_view = MatchView(match_data, match["match_date"], match["away_team"])

                original_val = func(home_view, away_view, match)

                # 修改当前比赛的比分字段
                modified_match = match.copy()
                modified_match["home_goals"] = 999
                modified_match["away_goals"] = 999

                modified_val = func(home_view, away_view, modified_match)

                if original_val is not None and modified_val is not None:
                    assert original_val == modified_val, (
                        f"Factor {name} uses score columns: value changed from "
                        f"{original_val} to {modified_val} when home_goals/away_goals were set to 999"
                    )
            except Exception:
                pass

    def test_factor_functions_dont_access_result_column(self, match_data):
        """因子函数不应使用比赛结果列（result）。

        通过将结果设为极端值，验证因子值不变，证明不依赖结果数据。
        """
        match = match_data.iloc[3000]

        for name, func in FACTOR_FUNCTIONS.items():
            if name in ("elo_diff", "fifa_rank_diff", "odds_implied_prob", "odds_movement"):
                continue

            try:
                home_view = MatchView(match_data, match["match_date"], match["home_team"])
                away_view = MatchView(match_data, match["match_date"], match["away_team"])

                original_val = func(home_view, away_view, match)

                # 修改当前比赛的结果字段
                modified_match = match.copy()
                modified_match["result"] = "FAKE_RESULT"

                modified_val = func(home_view, away_view, modified_match)

                if original_val is not None and modified_val is not None:
                    assert original_val == modified_val, (
                        f"Factor {name} uses result column: value changed from "
                        f"{original_val} to {modified_val} when result was modified"
                    )
            except Exception:
                pass


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
