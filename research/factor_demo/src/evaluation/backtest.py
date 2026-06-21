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


def walk_forward_validation(
    df: pd.DataFrame,
    model_factory: callable,
    feature_cols: list[str],
    target_col: str = "result",
    initial_train_years: int = 6,
    step_years: int = 1,
    end_date: str = "2025-12-31",
) -> list[dict]:
    """前向滚动验证。

    严格按时间顺序进行：
    1. 使用初始 initial_train_years 年数据训练
    2. 预测下一个 step_years 时间段
    3. 向前滚动，训练集不断扩大
    4. 返回每步的评估结果

    Args:
        df: 比赛数据 DataFrame，需包含 match_date 列
        model_factory: 模型工厂函数，接收训练 DataFrame 返回可预测模型
        feature_cols: 特征列名列表
        target_col: 目标列名，默认 'result'
        initial_train_years: 初始训练年数，默认 6
        step_years: 每步前进年数，默认 1
        end_date: 回测结束日期，默认 '2025-12-31'

    Returns:
        每步验证结果的列表，每项包含 train_end, test_end, n_train, n_test,
        evaluation, predictions, labels
    """
    df = df.sort_values("match_date").reset_index(drop=True)
    start_year = df["match_date"].dt.year.min()
    initial_train_end = pd.Timestamp(
        f"{start_year + initial_train_years - 1}-12-31", tz="UTC"
    )
    end_dt = pd.Timestamp(end_date, tz="UTC")
    start_dt = df["match_date"].min()

    results = []
    current_train_end = initial_train_end

    while current_train_end < end_dt:
        next_end = current_train_end + pd.DateOffset(years=step_years)
        next_end = min(next_end, end_dt)

        # 训练集：从最早数据到 current_train_end
        train_mask = (df["match_date"] >= start_dt) & (df["match_date"] <= current_train_end)
        train_df = df[train_mask]

        # 测试集：current_train_end 之后到 next_end
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
            labels.append(match[target_col])

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
                "predictions": np.array(predictions),
                "labels": np.array(labels),
            })

        current_train_end = next_end

    return results


def ablation_study(
    df: pd.DataFrame,
    model_factory: callable,
    all_features: dict[str, list[str]],
    baseline_features: list[str],
    target_col: str = "result",
    train_end: str = "2021-12-31",
    val_end: str = "2025-12-31",
) -> dict:
    """消融实验。

    1. 用全部特征训练完整模型
    2. 逐个移除特征组，训练消融模型
    3. 比较消融模型与完整模型的性能差异

    Args:
        df: 比赛数据 DataFrame
        model_factory: 模型工厂函数，接收 (train_df, feature_cols) 返回可预测模型
        all_features: 特征组映射，如 {"elo": ["elo_diff"], "form": ["recent_form_5", ...]}
        baseline_features: 基线特征列表（始终保留）
        target_col: 目标列名，默认 'result'
        train_end: 训练集截止日期
        val_end: 验证集截止日期

    Returns:
        特征组名到消融结果的映射，每项包含 brier_delta, log_loss_delta, is_critical
    """
    train_end_dt = pd.Timestamp(train_end, tz="UTC")
    val_end_dt = pd.Timestamp(val_end, tz="UTC")

    train_df = df[df["match_date"] <= train_end_dt]
    val_df = df[(df["match_date"] > train_end_dt) & (df["match_date"] <= val_end_dt)]

    if len(train_df) == 0 or len(val_df) == 0:
        return {}

    # 全部特征列表
    all_feature_list = []
    for feat_list in all_features.values():
        all_feature_list.extend(feat_list)

    # 训练完整模型
    full_model = model_factory(train_df, all_feature_list)

    # 完整模型在验证集上的预测
    full_predictions = []
    val_labels = []
    for _, match in val_df.iterrows():
        pred = full_model.predict(match)
        full_predictions.append([pred.home_win, pred.draw, pred.away_win])
        val_labels.append(match[target_col])

    full_eval = evaluate_predictions(np.array(full_predictions), np.array(val_labels))

    # 消融实验
    ablation_results = {}
    for group_name, group_features in all_features.items():
        # 移除该特征组后的特征列表
        ablated_features = [f for f in all_feature_list if f not in group_features]
        if not ablated_features:
            continue

        # 训练消融模型
        ablated_model = model_factory(train_df, ablated_features)

        ablated_predictions = []
        for _, match in val_df.iterrows():
            pred = ablated_model.predict(match)
            ablated_predictions.append([pred.home_win, pred.draw, pred.away_win])

        ablated_eval = evaluate_predictions(np.array(ablated_predictions), np.array(val_labels))

        brier_delta = ablated_eval.brier_score - full_eval.brier_score
        log_loss_delta = ablated_eval.log_loss - full_eval.log_loss

        ablation_results[group_name] = {
            "brier_delta": brier_delta,
            "log_loss_delta": log_loss_delta,
            "is_critical": brier_delta > 0.005,  # 移除后 Brier 增加 > 0.005 视为关键
        }

    return ablation_results


def chronological_tournament_backtest(
    df: pd.DataFrame,
    model_factory: callable,
    feature_cols: list[str],
    tournament_name: str = "FIFA World Cup",
) -> dict:
    """按时间顺序对特定赛事进行回测。

    对该赛事的每一届：
    1. 使用该届之前所有数据训练模型
    2. 预测该届所有比赛
    3. 与基线对比

    Args:
        df: 比赛数据 DataFrame，需包含 match_date, tournament, tournament_category 列
        model_factory: 模型工厂函数，接收训练 DataFrame 返回可预测模型
        feature_cols: 特征列名列表
        tournament_name: 赛事名称，默认 'FIFA World Cup'

    Returns:
        赛事年份到评估结果的映射
    """
    df = df.sort_values("match_date").reset_index(drop=True)

    # 筛选目标赛事的比赛
    tournament_mask = (
        df["tournament"].str.contains(tournament_name, case=False, na=False)
        | (df["tournament_category"] == "world_cup")
    )
    tournament_df = df[tournament_mask].copy()

    if len(tournament_df) == 0:
        return {}

    # 按年份分组（每届赛事）
    tournament_df["year"] = tournament_df["match_date"].dt.year
    editions = sorted(tournament_df["year"].unique())

    results = {}
    for year in editions:
        # 该届赛事的比赛
        edition_mask = tournament_df["year"] == year
        edition_df = tournament_df[edition_mask]

        # 训练集：该届之前的所有数据
        edition_start = edition_df["match_date"].min()
        train_mask = df["match_date"] < edition_start
        train_df = df[train_mask]

        if len(train_df) < 50 or len(edition_df) < 3:
            continue

        # 训练模型
        model = model_factory(train_df)

        # 预测
        predictions = []
        labels = []
        for _, match in edition_df.iterrows():
            pred = model.predict(match)
            predictions.append([pred.home_win, pred.draw, pred.away_win])
            labels.append(match["result"])

        if len(predictions) > 0:
            eval_result = evaluate_predictions(
                np.array(predictions),
                np.array(labels),
            )
            results[str(year)] = eval_result

    return results
