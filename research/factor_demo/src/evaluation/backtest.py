"""历史回测模块

实现滚动时间验证和因子评估。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import numpy as np
import pandas as pd

from ..features.as_of import MatchView, compute_features_at_time
from ..features.calculator import FACTOR_FUNCTIONS
from ..models.baseline import (
    EloLogisticBaseline,
    EloPoissonBaseline,
    FrequencyBaseline,
    HomeFixedBaseline,
    MarketImpliedBaseline,
    Prediction,
)
from .metrics import EvaluationResult, evaluate_predictions


def time_based_split(
    df: pd.DataFrame,
    train_end: str,
    val_end: str | None = None,
    test_end: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame | None, pd.DataFrame | None]:
    """按时间划分数据集。"""
    train_end_dt = pd.Timestamp(train_end, tz="UTC")
    train = df[df["match_date"] <= train_end_dt]
    
    val = None
    if val_end:
        val_end_dt = pd.Timestamp(val_end, tz="UTC")
        val = df[(df["match_date"] > train_end_dt) & (df["match_date"] <= val_end_dt)]
    
    test = None
    if test_end:
        test_end_dt = pd.Timestamp(test_end, tz="UTC")
        start_dt = val_end_dt if val_end else train_end_dt
        test = df[(df["match_date"] > start_dt) & (df["match_date"] <= test_end_dt)]
    
    return train, val, test


def rolling_backtest(
    df: pd.DataFrame,
    model_factory: callable,
    train_start: str = "2010-01-01",
    initial_train_end: str = "2016-12-31",
    step_years: int = 1,
    end_date: str = "2025-12-31",
) -> list[dict]:
    """滚动时间回测。
    
    每步：
    1. 用过去数据训练模型
    2. 预测下一时间段
    3. 向前滚动
    """
    results = []
    current_train_end = pd.Timestamp(initial_train_end, tz="UTC")
    end_dt = pd.Timestamp(end_date, tz="UTC")
    start_dt = pd.Timestamp(train_start, tz="UTC")
    
    while current_train_end < end_dt:
        next_end = current_train_end + pd.DateOffset(years=step_years)
        next_end = min(next_end, end_dt)
        
        # 训练集
        train_mask = (df["match_date"] >= start_dt) & (df["match_date"] <= current_train_end)
        train_df = df[train_mask]
        
        # 测试集
        test_mask = (df["match_date"] > current_train_end) & (df["match_date"] <= next_end)
        test_df = df[test_mask]
        
        if len(train_df) == 0 or len(test_df) == 0:
            current_train_end = next_end
            continue
        
        # 训练模型
        model = model_factory(train_df)
        
        # 预测
        predictions = []
        labels = []
        for _, match in test_df.iterrows():
            pred = model.predict(match)
            predictions.append([pred.home_win, pred.draw, pred.away_win])
            labels.append(match["result"])
        
        if len(predictions) > 0:
            eval_result = evaluate_predictions(
                np.array(predictions),
                np.array(labels),
            )
            results.append({
                "train_end": str(current_train_end.date()),
                "test_end": str(next_end.date()),
                "n_train": len(train_df),
                "n_test": len(test_df),
                "evaluation": eval_result,
            })
        
        current_train_end = next_end
    
    return results


def evaluate_by_segment(
    df: pd.DataFrame,
    predictions: np.ndarray,
    segment_col: str = "tournament_category",
) -> dict[str, EvaluationResult]:
    """按维度分片评估。"""
    results = {}
    labels = df["result"].values
    
    for segment in df[segment_col].unique():
        mask = df[segment_col] == segment
        if mask.sum() < 10:
            continue
        seg_preds = predictions[mask]
        seg_labels = labels[mask]
        results[segment] = evaluate_predictions(seg_preds, seg_labels)
    
    return results


def evaluate_single_factor(
    df: pd.DataFrame,
    factor_name: str,
    factor_values: np.ndarray,
    n_bins: int = 10,
) -> dict:
    """单因子分析。
    
    Returns:
        包含覆盖率、分箱胜率、Brier等信息的字典
    """
    # Convert None to NaN for numpy operations
    factor_array = np.array([np.nan if v is None else v for v in factor_values], dtype=float)
    valid_mask = ~np.isnan(factor_array)
    coverage = valid_mask.sum() / len(factor_array)
    
    result = {
        "factor": factor_name,
        "coverage": coverage,
        "n_valid": int(valid_mask.sum()),
        "n_total": len(factor_array),
        "mean": float(np.nanmean(factor_array)),
        "std": float(np.nanstd(factor_array)),
        "bins": [],
    }
    
    # 分箱分析
    valid_values = factor_array[valid_mask]
    valid_mask_pd = pd.Series(valid_mask, index=df.index)
    valid_df = df[valid_mask_pd].copy()
    valid_df["factor_value"] = valid_values
    
    try:
        valid_df["bin"] = pd.qcut(valid_df["factor_value"], n_bins, duplicates="drop")
    except ValueError:
        return result
    
    for bin_val, group in valid_df.groupby("bin"):
        n = len(group)
        home_rate = (group["result"] == "H").mean()
        draw_rate = (group["result"] == "D").mean()
        away_rate = (group["result"] == "A").mean()
        
        result["bins"].append({
            "bin": str(bin_val),
            "n": n,
            "home_win_rate": home_rate,
            "draw_rate": draw_rate,
            "away_win_rate": away_rate,
            "mean_factor": float(group["factor_value"].mean()),
        })
    
    return result
