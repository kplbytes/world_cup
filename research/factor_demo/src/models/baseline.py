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

import numpy as np
from scipy.special import softmax


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
