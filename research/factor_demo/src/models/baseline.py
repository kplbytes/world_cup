"""Baseline 模型

实现多个基线模型用于对比：
1. 主场固定概率基线
2. 类别频率基线
3. Elo Logistic 基线
4. Elo + Poisson 基线
5. 市场隐含概率基线
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
from scipy.special import softmax
from sklearn.calibration import CalibratedClassifierCV
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression


@dataclass
class Prediction:
    """三分类预测结果。"""
    home_win: float
    draw: float
    away_win: float
    
    def to_array(self) -> np.ndarray:
        return np.array([self.home_win, self.draw, self.away_win])
    
    def predicted_class(self) -> str:
        probs = {"H": self.home_win, "D": self.draw, "A": self.away_win}
        return max(probs, key=probs.get)


class HomeFixedBaseline:
    """主场固定概率基线。
    
    假设主队胜率固定为历史平均值。
    """
    
    def __init__(self, home_win_rate: float = 0.46, draw_rate: float = 0.26, away_win_rate: float = 0.28):
        self.home_win_rate = home_win_rate
        self.draw_rate = draw_rate
        self.away_win_rate = away_win_rate
    
    def predict(self, **kwargs) -> Prediction:
        return Prediction(
            home_win=self.home_win_rate,
            draw=self.draw_rate,
            away_win=self.away_win_rate,
        )
    
    @classmethod
    def from_data(cls, matches) -> "HomeFixedBaseline":
        """从历史数据计算固定概率。"""
        total = len(matches)
        home_wins = (matches["result"] == "H").sum()
        draws = (matches["result"] == "D").sum()
        away_wins = (matches["result"] == "A").sum()
        return cls(
            home_win_rate=home_wins / total,
            draw_rate=draws / total,
            away_win_rate=away_wins / total,
        )


class FrequencyBaseline:
    """类别频率基线。
    
    根据赛事类型和场地使用不同的频率。
    """
    
    def __init__(self):
        self._freqs: dict[str, tuple[float, float, float]] = {}
    
    def fit(self, matches) -> "FrequencyBaseline":
        """从训练数据学习频率。"""
        # 全局频率
        total = len(matches)
        h = (matches["result"] == "H").sum() / total
        d = (matches["result"] == "D").sum() / total
        a = (matches["result"] == "A").sum() / total
        self._freqs["global"] = (h, d, a)
        
        # 按中立场/非中立场
        for neutral in [True, False]:
            subset = matches[matches["is_neutral"] == neutral]
            if len(subset) > 0:
                t = len(subset)
                self._freqs[f"neutral_{neutral}"] = (
                    (subset["result"] == "H").sum() / t,
                    (subset["result"] == "D").sum() / t,
                    (subset["result"] == "A").sum() / t,
                )
        
        # 按赛事类别
        for cat in matches["tournament_category"].unique():
            subset = matches[matches["tournament_category"] == cat]
            if len(subset) > 0:
                t = len(subset)
                self._freqs[f"cat_{cat}"] = (
                    (subset["result"] == "H").sum() / t,
                    (subset["result"] == "D").sum() / t,
                    (subset["result"] == "A").sum() / t,
                )
        
        return self
    
    def predict(self, match, **kwargs) -> Prediction:
        """预测单场比赛。"""
        # 优先使用细粒度频率
        is_neutral = match.get("is_neutral", False)
        cat = match.get("tournament_category", "other")
        
        key = f"neutral_{is_neutral}"
        probs = self._freqs.get(key, self._freqs.get("global", (0.46, 0.26, 0.28)))
        
        return Prediction(home_win=probs[0], draw=probs[1], away_win=probs[2])


class EloLogisticBaseline:
    """Elo Logistic 基线。
    
    使用 Elo 差值通过 Logistic 函数预测胜率。
    """
    
    def __init__(self, elo_scale: float = 400.0, home_advantage: float = 60.0, draw_base: float = 0.26):
        self.elo_scale = elo_scale
        self.home_advantage = home_advantage
        self.draw_base = draw_base
    
    def predict(self, elo_home: float, elo_away: float, is_neutral: bool = False, **kwargs) -> Prediction:
        """根据 Elo 预测比赛结果。"""
        ha = 0.0 if is_neutral else self.home_advantage
        expected_home = 1.0 / (1.0 + 10 ** ((elo_away - (elo_home + ha)) / self.elo_scale))
        
        # 简化：用 expected_home 分配胜平负概率
        # draw_base 作为基础平局概率，根据实力差距调整
        draw_adj = self.draw_base * (1.0 - abs(expected_home - 0.5))
        draw = draw_adj
        
        remaining = 1.0 - draw
        home_win = remaining * expected_home
        away_win = remaining * (1.0 - expected_home)
        
        # 归一化
        total = home_win + draw + away_win
        return Prediction(
            home_win=home_win / total,
            draw=draw / total,
            away_win=away_win / total,
        )


class EloPoissonBaseline:
    """Elo + Poisson 基线。
    
    模拟主程序中的 Elo + Poisson 模型。
    """
    
    def __init__(
        self,
        elo_scale: float = 400.0,
        home_advantage: float = 60.0,
        base_goal_home: float = 1.25,
        base_goal_away: float = 1.10,
        strength_coeff_home: float = 0.90,
        strength_coeff_away: float = 0.75,
        min_xg: float = 0.20,
        max_xg: float = 3.50,
    ):
        self.elo_scale = elo_scale
        self.home_advantage = home_advantage
        self.base_goal_home = base_goal_home
        self.base_goal_away = base_goal_away
        self.strength_coeff_home = strength_coeff_home
        self.strength_coeff_away = strength_coeff_away
        self.min_xg = min_xg
        self.max_xg = max_xg
    
    def predict(self, elo_home: float, elo_away: float, is_neutral: bool = False, **kwargs) -> Prediction:
        """使用 Elo + Poisson 模型预测。"""
        from scipy.stats import poisson
        
        ha = 0.0 if is_neutral else self.home_advantage
        strength_delta = elo_home + ha - elo_away
        
        home_xg = np.clip(
            self.base_goal_home + self.strength_coeff_home * strength_delta / 400.0,
            self.min_xg,
            self.max_xg,
        )
        away_xg = np.clip(
            self.base_goal_away - self.strength_coeff_away * strength_delta / 400.0,
            self.min_xg,
            self.max_xg,
        )
        
        # Poisson 矩阵
        max_goals = 7
        home_goals = poisson.pmf(np.arange(max_goals + 1), home_xg)
        away_goals = poisson.pmf(np.arange(max_goals + 1), away_xg)
        matrix = np.outer(home_goals, away_goals)
        
        home_win = float(np.tril(matrix, k=-1).sum())
        draw = float(np.trace(matrix))
        away_win = float(np.triu(matrix, k=1).sum())
        
        total = home_win + draw + away_win
        return Prediction(
            home_win=home_win / total,
            draw=draw / total,
            away_win=away_win / total,
        )


class MarketImpliedBaseline:
    """市场隐含概率基线。
    
    从赔率反推胜平负概率。
    """
    
    def predict(self, odds_home: float | None = None, odds_draw: float | None = None, 
                odds_away: float | None = None, **kwargs) -> Prediction:
        """从赔率计算隐含概率。"""
        if odds_home is None or odds_draw is None or odds_away is None:
            # 无赔率数据时退化为均匀分布
            return Prediction(home_win=1/3, draw=1/3, away_win=1/3)
        
        # 去除利润率
        implied_home = 1.0 / odds_home
        implied_draw = 1.0 / odds_draw
        implied_away = 1.0 / odds_away
        
        total = implied_home + implied_draw + implied_away
        return Prediction(
            home_win=implied_home / total,
            draw=implied_draw / total,
            away_win=implied_away / total,
        )


class RegularizedLogisticBaseline:
    """L2 正则化 Logistic 回归基线。

    使用 sklearn 的 LogisticRegression 对因子进行组合预测。
    """

    def __init__(self, C: float = 1.0, max_iter: int = 1000):
        self.C = C
        self.max_iter = max_iter
        self._model: LogisticRegression | None = None
        self._imputer: SimpleImputer | None = None

    def fit(self, X_train: np.ndarray, y_train: np.ndarray) -> "RegularizedLogisticBaseline":
        """训练模型，自动用列均值填充 NaN。"""
        self._imputer = SimpleImputer(strategy="mean")
        X_clean = self._imputer.fit_transform(X_train)

        self._model = LogisticRegression(
            C=self.C,
            max_iter=self.max_iter,
            solver="lbfgs",
        )
        self._model.fit(X_clean, y_train)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """返回 (N, 3) 概率数组。"""
        X_clean = self._imputer.transform(X)
        return self._model.predict_proba(X_clean)


class LightGBMBaseline:
    """LightGBM 基线模型。

    使用 LightGBM 对因子进行组合预测，原生支持 NaN。
    """

    def __init__(
        self,
        n_estimators: int = 200,
        max_depth: int = 5,
        learning_rate: float = 0.05,
        num_leaves: int = 31,
        min_child_samples: int = 20,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        random_state: int = 42,
    ):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.min_child_samples = min_child_samples
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.random_state = random_state
        self._model: lgb.LGBMClassifier | None = None

    def fit(self, X_train: np.ndarray, y_train: np.ndarray) -> "LightGBMBaseline":
        """训练模型，LightGBM 原生处理 NaN。"""
        self._model = lgb.LGBMClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            num_leaves=self.num_leaves,
            min_child_samples=self.min_child_samples,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            random_state=self.random_state,
            verbose=-1,
        )
        self._model.fit(X_train, y_train)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """返回 (N, 3) 概率数组。"""
        return self._model.predict_proba(X)


class AblationModel:
    """消融实验模型包装器。

    仅使用指定特征子集训练基础模型，用于消融研究。
    """

    def __init__(self, base_model_class: type, feature_names: list[str], **model_kwargs: Any):
        self.base_model_class = base_model_class
        self.feature_names = feature_names
        self.model_kwargs = model_kwargs
        self._model = None

    def fit(self, X_train_df, y_train: np.ndarray) -> "AblationModel":
        """选择指定特征列后训练模型。"""
        X_subset = X_train_df[self.feature_names]
        if hasattr(X_subset, "values"):
            X_subset = X_subset.values
        self._model = self.base_model_class(**self.model_kwargs)
        self._model.fit(X_subset, y_train)
        return self

    def predict(self, X_df) -> np.ndarray:
        """选择指定特征列后预测，返回 (N, 3) 概率数组。"""
        X_subset = X_df[self.feature_names]
        if hasattr(X_subset, "values"):
            X_subset = X_subset.values
        return self._model.predict(X_subset)


class CalibratedModel:
    """校准模型包装器。

    对任意基础模型应用 Platt 缩放或等距校准。
    """

    def __init__(self, base_model, method: str = "isotonic", cv: int = 5):
        self.base_model = base_model
        self.method = method
        self.cv = cv
        self._calibrator: CalibratedClassifierCV | None = None

    def fit(self, X_train: np.ndarray, y_train: np.ndarray) -> "CalibratedModel":
        """先拟合基础模型，再拟合校准器。"""
        self._calibrator = CalibratedClassifierCV(
            estimator=self.base_model,
            method=self.method,
            cv=self.cv,
        )
        self._calibrator.fit(X_train, y_train)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """返回校准后的 (N, 3) 概率数组。"""
        return self._calibrator.predict_proba(X)
