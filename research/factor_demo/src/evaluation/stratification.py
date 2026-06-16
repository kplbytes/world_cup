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
