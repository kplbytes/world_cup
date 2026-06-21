"""分层评估模块

按年份、赛事类型、正式/友谊、中立场、强弱、均势等维度分层评估。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .metrics import evaluate_predictions, EvaluationResult


def stratify_by_year(
    df: pd.DataFrame,
    predictions: np.ndarray,
) -> dict[str, EvaluationResult]:
    """按年份分层评估。"""
    results = {}
    df = df.copy()
    df["year"] = df["match_date"].dt.year

    for year in sorted(df["year"].unique()):
        mask = df["year"] == year
        if mask.sum() < 10:
            continue
        results[str(year)] = evaluate_predictions(predictions[mask], df.loc[mask, "result"].values)

    return results


def stratify_by_tournament(
    df: pd.DataFrame,
    predictions: np.ndarray,
) -> dict[str, EvaluationResult]:
    """按赛事类型分层评估。"""
    results = {}

    for cat in sorted(df["tournament_category"].unique()):
        mask = df["tournament_category"] == cat
        if mask.sum() < 10:
            continue
        results[cat] = evaluate_predictions(predictions[mask], df.loc[mask, "result"].values)

    return results


def stratify_by_official_vs_friendly(
    df: pd.DataFrame,
    predictions: np.ndarray,
) -> dict[str, EvaluationResult]:
    """按正式比赛/友谊赛分层评估。"""
    results = {}

    for is_official in [True, False]:
        mask = df["is_official"] == is_official
        label = "official" if is_official else "friendly"
        if mask.sum() < 10:
            continue
        results[label] = evaluate_predictions(predictions[mask], df.loc[mask, "result"].values)

    return results


def stratify_by_neutral(
    df: pd.DataFrame,
    predictions: np.ndarray,
) -> dict[str, EvaluationResult]:
    """按中立场/非中立场分层评估。"""
    results = {}

    for is_neutral in [True, False]:
        mask = df["is_neutral"] == is_neutral
        label = "neutral" if is_neutral else "non_neutral"
        if mask.sum() < 10:
            continue
        results[label] = evaluate_predictions(predictions[mask], df.loc[mask, "result"].values)

    return results


def stratify_by_strength_gap(
    df: pd.DataFrame,
    predictions: np.ndarray,
    strong_weak_threshold: float = 200.0,
    close_match_threshold: float = 100.0,
) -> dict[str, EvaluationResult]:
    """按强弱差距分层评估。

    strong_weak: |elo_diff| > threshold (实力悬殊)
    close_match: |elo_diff| <= close_threshold (实力接近)
    moderate: 介于两者之间
    """
    results = {}
    elo_diff = df["elo_diff"].abs()

    # 强弱悬殊
    mask_sw = elo_diff > strong_weak_threshold
    if mask_sw.sum() >= 10:
        results["strong_weak"] = evaluate_predictions(predictions[mask_sw], df.loc[mask_sw, "result"].values)

    # 实力接近
    mask_close = elo_diff <= close_match_threshold
    if mask_close.sum() >= 10:
        results["close_match"] = evaluate_predictions(predictions[mask_close], df.loc[mask_close, "result"].values)

    # 中等差距
    mask_mod = (elo_diff > close_match_threshold) & (elo_diff <= strong_weak_threshold)
    if mask_mod.sum() >= 10:
        results["moderate_gap"] = evaluate_predictions(predictions[mask_mod], df.loc[mask_mod, "result"].values)

    return results


def stratify_world_cup(
    df: pd.DataFrame,
    predictions: np.ndarray,
) -> dict[str, EvaluationResult]:
    """单独评估世界杯和世界杯预选赛。"""
    results = {}

    # 世界杯
    mask_wc = df["tournament_category"] == "world_cup"
    if mask_wc.sum() >= 5:
        results["world_cup"] = evaluate_predictions(predictions[mask_wc], df.loc[mask_wc, "result"].values)

    # 世界杯预选赛
    mask_wcq = df["tournament_category"] == "qualification"
    if mask_wcq.sum() >= 10:
        results["wc_qualification"] = evaluate_predictions(predictions[mask_wcq], df.loc[mask_wcq, "result"].values)

    # 世界杯 + 预选赛
    mask_combined = mask_wc | mask_wcq
    if mask_combined.sum() >= 10:
        results["wc_and_qualification"] = evaluate_predictions(predictions[mask_combined], df.loc[mask_combined, "result"].values)

    return results


def stratify_by_venue_type(
    df: pd.DataFrame,
    predictions: np.ndarray,
) -> dict[str, EvaluationResult]:
    """按场地类型分层评估（用于host_advantage分析）。

    home: 非中立场，主队国家=比赛国家
    away: 非中立场，主队国家!=比赛国家（客队在对方国家比赛）
    neutral: 中立场
    host_nation: 中立场但比赛在主队国家（东道主效应）
    """
    results = {}
    df = df.copy()

    def classify_venue(row):
        is_neutral = row.get("is_neutral", False)
        country = str(row.get("country", ""))
        home_team = row.get("home_team", "")

        if is_neutral:
            if country and home_team and country == home_team:
                return "host_nation"
            return "neutral"
        else:
            return "home"

    df["venue_type"] = df.apply(classify_venue, axis=1)

    for vtype in ["home", "neutral", "host_nation"]:
        mask = df["venue_type"] == vtype
        if mask.sum() >= 5:
            results[vtype] = evaluate_predictions(predictions[mask], df.loc[mask, "result"].values)

    return results


def full_stratified_report(
    df: pd.DataFrame,
    predictions: np.ndarray,
    model_name: str = "Model",
) -> dict:
    """生成完整的分层评估报告。"""
    report = {
        "model": model_name,
        "overall": evaluate_predictions(predictions, df["result"].values),
        "by_year": stratify_by_year(df, predictions),
        "by_tournament": stratify_by_tournament(df, predictions),
        "by_official_friendly": stratify_by_official_vs_friendly(df, predictions),
        "by_neutral": stratify_by_neutral(df, predictions),
        "by_strength_gap": stratify_by_strength_gap(df, predictions),
        "world_cup_specific": stratify_world_cup(df, predictions),
        "by_venue_type": stratify_by_venue_type(df, predictions),
    }
    return report


def format_stratified_report(report: dict) -> str:
    """格式化分层评估报告为可读文本。"""
    lines = []
    lines.append(f"{'='*80}")
    lines.append(f"Stratified Report: {report['model']}")
    lines.append(f"{'='*80}")

    overall = report["overall"]
    lines.append(f"Overall: {overall}")

    for section_name, section_data in report.items():
        if section_name in ("model", "overall"):
            continue

        lines.append(f"\n--- {section_name} ---")
        if isinstance(section_data, dict):
            for key, val in section_data.items():
                if isinstance(val, EvaluationResult):
                    lines.append(f"  {key}: {val}")
                else:
                    lines.append(f"  {key}: {val}")

    return "\n".join(lines)


def cross_year_stability(
    results_by_year: dict[str, EvaluationResult],
    metric: str = "brier_score",
) -> dict:
    """计算模型跨年份的性能稳定性。

    通过变异系数（CV）衡量模型在不同年份的表现是否一致。
    CV < 0.1 视为稳定。

    Args:
        results_by_year: 年份到评估结果的映射，如 {"2018": EvaluationResult, ...}
        metric: 使用的指标名称，默认为 brier_score

    Returns:
        包含 is_stable, coefficient_of_variation, worst_year, best_year 等的字典
    """
    if not results_by_year:
        return {
            "is_stable": False,
            "coefficient_of_variation": float("nan"),
            "worst_year": "",
            "best_year": "",
            "worst_metric": float("nan"),
            "best_metric": float("nan"),
        }

    year_metrics = {}
    for year, result in results_by_year.items():
        year_metrics[year] = getattr(result, metric, None)

    # 过滤无效值
    valid = {y: v for y, v in year_metrics.items() if v is not None}
    if not valid:
        return {
            "is_stable": False,
            "coefficient_of_variation": float("nan"),
            "worst_year": "",
            "best_year": "",
            "worst_metric": float("nan"),
            "best_metric": float("nan"),
        }

    values = np.array(list(valid.values()))
    mean_val = float(np.mean(values))
    std_val = float(np.std(values))
    cv = std_val / mean_val if mean_val != 0 else float("inf")

    # 对于 brier_score，越小越好
    if metric in ("brier_score", "log_loss", "ece"):
        best_year = min(valid, key=valid.get)
        worst_year = max(valid, key=valid.get)
    else:
        best_year = max(valid, key=valid.get)
        worst_year = min(valid, key=valid.get)

    return {
        "is_stable": cv < 0.1,
        "coefficient_of_variation": cv,
        "worst_year": worst_year,
        "best_year": best_year,
        "worst_metric": float(valid[worst_year]),
        "best_metric": float(valid[best_year]),
    }


def cross_tournament_stability(
    results_by_tournament: dict[str, EvaluationResult],
    metric: str = "brier_score",
) -> dict:
    """计算模型跨赛事类型的性能稳定性。

    通过变异系数（CV）衡量模型在不同赛事类型的表现是否一致。
    CV < 0.1 视为稳定。

    Args:
        results_by_tournament: 赛事类型到评估结果的映射
        metric: 使用的指标名称，默认为 brier_score

    Returns:
        包含 is_stable, coefficient_of_variation, worst_year, best_year 等的字典
    """
    if not results_by_tournament:
        return {
            "is_stable": False,
            "coefficient_of_variation": float("nan"),
            "worst_year": "",
            "best_year": "",
            "worst_metric": float("nan"),
            "best_metric": float("nan"),
        }

    tournament_metrics = {}
    for tournament, result in results_by_tournament.items():
        tournament_metrics[tournament] = getattr(result, metric, None)

    valid = {t: v for t, v in tournament_metrics.items() if v is not None}
    if not valid:
        return {
            "is_stable": False,
            "coefficient_of_variation": float("nan"),
            "worst_year": "",
            "best_year": "",
            "worst_metric": float("nan"),
            "best_metric": float("nan"),
        }

    values = np.array(list(valid.values()))
    mean_val = float(np.mean(values))
    std_val = float(np.std(values))
    cv = std_val / mean_val if mean_val != 0 else float("inf")

    if metric in ("brier_score", "log_loss", "ece"):
        best_tournament = min(valid, key=valid.get)
        worst_tournament = max(valid, key=valid.get)
    else:
        best_tournament = max(valid, key=valid.get)
        worst_tournament = min(valid, key=valid.get)

    return {
        "is_stable": cv < 0.1,
        "coefficient_of_variation": cv,
        "worst_year": worst_tournament,
        "best_year": best_tournament,
        "worst_metric": float(valid[worst_tournament]),
        "best_metric": float(valid[best_tournament]),
    }


def scenario_analysis(
    df: pd.DataFrame,
    predictions: np.ndarray,
    scenarios: list[dict] | None = None,
) -> dict:
    """在特定场景下评估模型表现。

    每个场景由名称和过滤函数定义，过滤函数接收 DataFrame 返回布尔掩码。

    Args:
        df: 比赛数据 DataFrame
        predictions: 预测概率 (N, 3)
        scenarios: 场景列表，每个场景为 {"name": str, "filter_func": callable}。
            filter_func 接收 df 返回布尔 Series/数组。
            默认包含四个内置场景。

    Returns:
        场景名到评估结果的映射
    """
    if scenarios is None:
        scenarios = [
            {
                "name": "world_cup_knockout",
                "filter_func": lambda d: (
                    (d["tournament_category"] == "world_cup")
                    & (d.get("stage", "").str.contains("knockout|round|quarter|semi|final", case=False, na=False))
                ),
            },
            {
                "name": "cross_confederation",
                "filter_func": lambda d: d.get("is_cross_confederation", False) == True,  # noqa: E712
            },
            {
                "name": "strong_vs_weak",
                "filter_func": lambda d: d["elo_diff"].abs() > 200,
            },
            {
                "name": "close_match",
                "filter_func": lambda d: d["elo_diff"].abs() < 50,
            },
        ]

    results = {}
    for scenario in scenarios:
        name = scenario["name"]
        filter_func = scenario["filter_func"]

        try:
            mask = filter_func(df)
            if hasattr(mask, "values"):
                mask = mask.values
            mask = mask.astype(bool)
        except Exception:
            continue

        if mask.sum() < 5:
            continue

        results[name] = evaluate_predictions(
            predictions[mask],
            df.loc[mask, "result"].values,
        )

    return results
