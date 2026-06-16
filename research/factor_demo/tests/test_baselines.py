"""Baseline 模型测试"""

import pandas as pd
import numpy as np
import pytest

from src.data.loader import load_international_results, filter_by_date
from src.models.baseline import (
    HomeFixedBaseline,
    FrequencyBaseline,
    EloLogisticBaseline,
    EloPoissonBaseline,
    MarketImpliedBaseline,
)
from src.evaluation.metrics import evaluate_predictions
from src.utils.elo_replay import replay_elo_history


@pytest.fixture(scope="module")
def match_data():
    """加载测试数据。"""
    df = load_international_results()
    df = filter_by_date(df, "2010-01-01", "2023-12-31")
    df = replay_elo_history(df)
    return df


class TestBaselines:
    """测试基线模型。"""

    def test_home_fixed_baseline(self, match_data):
        """主场固定概率基线应输出合法概率。"""
        model = HomeFixedBaseline()
        pred = model.predict()
        assert abs(pred.home_win + pred.draw + pred.away_win - 1.0) < 1e-6
        assert pred.home_win > 0
        assert pred.draw > 0
        assert pred.away_win > 0

    def test_home_fixed_from_data(self, match_data):
        """从数据学习的固定概率应接近历史平均。"""
        model = HomeFixedBaseline.from_data(match_data)
        pred = model.predict()
        assert 0.3 < pred.home_win < 0.6
        assert 0.15 < pred.draw < 0.35
        assert 0.15 < pred.away_win < 0.4

    def test_frequency_baseline(self, match_data):
        """类别频率基线应输出合法概率。"""
        train = match_data[match_data["match_date"] < "2020-01-01"]
        model = FrequencyBaseline().fit(train)

        match = match_data.iloc[0]
        pred = model.predict(match)
        assert abs(pred.home_win + pred.draw + pred.away_win - 1.0) < 1e-6

    def test_elo_logistic_baseline(self, match_data):
        """Elo Logistic 基线应输出合法概率。"""
        model = EloLogisticBaseline()
        pred = model.predict(elo_home=1600, elo_away=1400, is_neutral=False)
        assert abs(pred.home_win + pred.draw + pred.away_win - 1.0) < 1e-6
        assert pred.home_win > pred.away_win  # 主队更强

    def test_elo_poisson_baseline(self, match_data):
        """Elo + Poisson 基线应输出合法概率。"""
        model = EloPoissonBaseline()
        pred = model.predict(elo_home=1600, elo_away=1400, is_neutral=False)
        assert abs(pred.home_win + pred.draw + pred.away_win - 1.0) < 1e-6
        assert pred.home_win > pred.away_win

    def test_market_implied_baseline(self, match_data):
        """市场隐含概率基线应输出合法概率。"""
        model = MarketImpliedBaseline()
        pred = model.predict(odds_home=1.5, odds_draw=4.0, odds_away=6.0)
        assert abs(pred.home_win + pred.draw + pred.away_win - 1.0) < 1e-6
        assert pred.home_win > pred.away_win

    def test_market_implied_no_odds(self, match_data):
        """无赔率时应退化为均匀分布。"""
        model = MarketImpliedBaseline()
        pred = model.predict()
        assert abs(pred.home_win - 1/3) < 1e-6


class TestBaselineComparison:
    """基线模型对比测试。"""

    def test_all_baselines_on_test_set(self, match_data):
        """在测试集上评估所有基线模型。"""
        test = match_data[match_data["match_date"] >= "2022-01-01"]

        if len(test) == 0:
            pytest.skip("No test data available")

        # Home Fixed
        train = match_data[match_data["match_date"] < "2022-01-01"]
        home_fixed = HomeFixedBaseline.from_data(train)

        results = {}

        # Home Fixed
        preds = []
        for _, m in test.iterrows():
            p = home_fixed.predict()
            preds.append([p.home_win, p.draw, p.away_win])
        results["HomeFixed"] = evaluate_predictions(np.array(preds), test["result"].values)

        # Elo Logistic
        elo_logistic = EloLogisticBaseline()
        preds = []
        for _, m in test.iterrows():
            p = elo_logistic.predict(
                elo_home=m["pre_match_elo_home"],
                elo_away=m["pre_match_elo_away"],
                is_neutral=m["is_neutral"],
            )
            preds.append([p.home_win, p.draw, p.away_win])
        results["EloLogistic"] = evaluate_predictions(np.array(preds), test["result"].values)

        # Elo Poisson
        elo_poisson = EloPoissonBaseline()
        preds = []
        for _, m in test.iterrows():
            p = elo_poisson.predict(
                elo_home=m["pre_match_elo_home"],
                elo_away=m["pre_match_elo_away"],
                is_neutral=m["is_neutral"],
            )
            preds.append([p.home_win, p.draw, p.away_win])
        results["EloPoisson"] = evaluate_predictions(np.array(preds), test["result"].values)

        # 打印结果
        from src.evaluation.metrics import compare_models
        print("\n" + compare_models(results))

        # Elo 模型应该优于固定概率基线
        assert results["EloLogistic"].brier_score <= results["HomeFixed"].brier_score + 0.02
