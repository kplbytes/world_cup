"""因子计算测试"""

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


class TestFactorComputation:
    """测试因子计算的正确性。"""

    def test_recent_form_range(self, match_data):
        """recent_form_5 应在 [-1, 1] 范围内。"""
        match = match_data.iloc[5000]
        home_view = MatchView(match_data, match["match_date"], match["home_team"])
        away_view = MatchView(match_data, match["match_date"], match["away_team"])

        val = FACTOR_FUNCTIONS["recent_form_5"](home_view, away_view, match)
        if val is not None:
            assert -1.0 <= val <= 1.0, f"recent_form_5 out of range: {val}"

    def test_host_advantage_range(self, match_data):
        """host_advantage 应在 [0, 1] 范围内。"""
        match = match_data.iloc[5000]
        home_view = MatchView(match_data, match["match_date"], match["home_team"])
        away_view = MatchView(match_data, match["match_date"], match["away_team"])

        val = FACTOR_FUNCTIONS["host_advantage"](home_view, away_view, match)
        assert 0.0 <= val <= 1.0, f"host_advantage out of range: {val}"

    def test_elo_diff_computed(self, match_data):
        """Elo 差值应该被正确注入。"""
        match = match_data.iloc[5000]
        home_view = MatchView(match_data, match["match_date"], match["home_team"])
        away_view = MatchView(match_data, match["match_date"], match["away_team"])

        val = FACTOR_FUNCTIONS["elo_diff"](home_view, away_view, match)
        assert val is not None, "elo_diff should be injected"
        assert "elo_diff" in match.index, "match should have elo_diff column"

    def test_all_factors_run_without_error(self, match_data):
        """所有因子函数应能正常运行（允许返回 None）。"""
        match = match_data.iloc[5000]

        for name, func in FACTOR_FUNCTIONS.items():
            try:
                home_view = MatchView(match_data, match["match_date"], match["home_team"])
                away_view = MatchView(match_data, match["match_date"], match["away_team"])
                val = func(home_view, away_view, match)
                # 允许返回 None（数据不足）
            except Exception as e:
                pytest.fail(f"Factor {name} raised exception: {e}")

    def test_coverage_report(self, match_data):
        """生成因子覆盖率报告。"""
        # 抽样计算
        sample = match_data.sample(min(500, len(match_data)), random_state=42)

        coverage = {}
        for name, func in FACTOR_FUNCTIONS.items():
            non_null = 0
            total = 0
            for _, match in sample.iterrows():
                try:
                    home_view = MatchView(match_data, match["match_date"], match["home_team"])
                    away_view = MatchView(match_data, match["match_date"], match["away_team"])
                    val = func(home_view, away_view, match)
                    total += 1
                    if val is not None:
                        non_null += 1
                except Exception:
                    total += 1

            coverage[name] = non_null / total if total > 0 else 0.0

        # 打印覆盖率报告
        print("\nFactor Coverage Report:")
        for name, cov in sorted(coverage.items(), key=lambda x: x[1], reverse=True):
            print(f"  {name}: {cov:.1%}")
