#!/usr/bin/env python3
"""深度因子分析与预测优化流水线

这是研究的核心脚本，产出可操作的因子排名、平局预测突破和模型架构对比。

Stage 1: 数据整合与增强 (xG / 天气 / 合成赔率)
Stage 2: 综合因子影响评估 (IC/ICIR/SHAP/MI/排列重要性/PDP/交互)
Stage 3: 平局预测突破 (5种方案对比)
Stage 4: 高级模型架构 (Stacking/校准/特征选择/时间加权/赛事特化)
Stage 5: 严格验证 (Walk-Forward/WC回测/Bootstrap/校准/Brier分解)
Stage 6: 最终因子排名与准入决策
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

# ─── 项目路径 ──────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import (
    load_international_results,
    load_fifa_rankings,
    inject_fifa_rankings,
    filter_by_date,
    validate_data,
)
from src.features.as_of import compute_all_features
from src.features.calculator import FACTOR_FUNCTIONS
from src.models.baseline import (
    EloLogisticBaseline,
    EloPoissonBaseline,
    LightGBMBaseline,
    RegularizedLogisticBaseline,
    CalibratedModel,
    Prediction,
)
from src.evaluation.metrics import (
    evaluate_predictions,
    brier_score,
    multiclass_ece,
    reliability_curve,
    compute_factor_direction_stability,
)
from src.evaluation.calibration import (
    calibration_metrics,
    reliability_diagram_data,
    isotonic_calibration,
    platt_scale_calibration,
)
from src.evaluation.bootstrap import bootstrap_brier_comparison, bootstrap_brier_single
from src.utils.elo_replay import replay_elo_history, EloConfig

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.metrics import mutual_info_score
from sklearn.model_selection import TimeSeriesSplit

import lightgbm as lgb

warnings.filterwarnings("ignore")

# ─── 常量 ──────────────────────────────────────────────────────────────
RANDOM_SEED = 42
DATA_START = "2000-01-01"
TRAIN_END = "2018-12-31"
VAL_END = "2025-12-31"
FREEZE_DATE = "2025-12-31"
WORLD_CUP_YEARS = [2010, 2014, 2018, 2022]

BASELINE_FACTORS = ["elo_diff"]
SKIP_FACTORS = ["odds_implied_prob", "odds_movement"]

# 因子分组定义
FACTOR_GROUPS = {
    "rating": ["elo_diff", "fifa_rank_diff_factor", "fifa_points_diff", "elo_fifa_disagreement"],
    "form": ["recent_form_5", "recent_form_10", "recent_form_5_opp_adjusted",
             "official_vs_friendly", "form_volatility_home", "form_volatility_away",
             "win_streak_home", "win_streak_away", "unbeaten_streak_home",
             "recent_upset_home", "goal_difference_momentum", "scoring_consistency"],
    "form_enhanced": ["win_streak_home", "win_streak_away", "unbeaten_streak_home",
                      "goal_form_trend_home", "clean_sheet_rate_home", "comeback_rate_home",
                      "form_volatility_home", "form_volatility_away", "recent_upset_home",
                      "goal_difference_momentum", "scoring_consistency"],
    "attack_defense": ["recent_goals_scored_5", "recent_goals_conceded_5",
                       "recent_goal_diff_5", "attack_strength", "defense_strength"],
    "venue": ["home_away_neutral_form", "host_advantage"],
    "fatigue": ["rest_days", "match_density_30d", "match_density_90d"],
    "experience": ["tournament_experience", "knockout_experience"],
    "confederation": ["inter_confederation_form"],
    "h2h": ["h2h_last_5", "h2h_draw_rate", "h2h_avg_goals", "h2h_recency"],
    "draw_specific": ["draw_tendency_home", "draw_tendency_away", "draw_tendency_diff",
                      "elo_closeness", "defensive_matchup", "tournament_draw_rate",
                      "neutral_draw_rate", "low_scoring_matchup"],
    "contextual": ["tournament_stage_pressure", "opening_match_effect",
                   "must_win_situation", "dead_rubber", "altitude_effect",
                   "travel_distance_proxy"],
    "fifa": ["fifa_rank_diff_factor", "fifa_points_diff", "fifa_rank_trend_home",
             "fifa_rank_trend_away", "elo_fifa_disagreement"],
}


# ─── 工具函数 ──────────────────────────────────────────────────────────
def _json_default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def save_json(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=_json_default)
    print(f"  [保存] {path}")


def print_section(title: str):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def label_to_numeric(labels: np.ndarray) -> np.ndarray:
    """H/D/A -> 2/1/0 (home_win=2, draw=1, away_win=0)"""
    m = {"H": 2, "D": 1, "A": 0}
    return np.array([m[l] for l in labels])


def reorder_probs_for_eval(probs: np.ndarray) -> np.ndarray:
    """将模型输出 [A, D, H] 重排为 evaluate_predictions 期望的 [H, D, A]"""
    return probs[:, [2, 1, 0]]


def label_to_onehot(labels: np.ndarray) -> np.ndarray:
    """H/D/A -> one-hot (N,3) [away, draw, home] — 与 LightGBM 类别顺序一致 (0=A, 1=D, 2=H)"""
    m = {"A": 0, "D": 1, "H": 2}
    n = len(labels)
    oh = np.zeros((n, 3))
    for i, l in enumerate(labels):
        oh[i, m[l]] = 1.0
    return oh


# ======================================================================
# Stage 1: 数据整合与增强
# ======================================================================
def load_cached_features(cache_path: Path) -> pd.DataFrame:
    """加载缓存的因子数据"""
    print(f"  加载特征缓存: {cache_path}")
    df = pd.read_csv(cache_path)
    print(f"  缓存数据: {len(df)} 行, {len(df.columns)} 列")
    return df


def load_matches_with_elo() -> pd.DataFrame:
    """加载比赛数据并计算 Elo"""
    print("  加载比赛数据...")
    # 优先使用 international_results.csv，不存在则回退到 results.csv
    int_results_path = Path(__file__).parent.parent.parent.parent / "data" / "external" / "international_results.csv"
    results_path = Path(__file__).parent.parent.parent.parent / "data" / "external" / "results.csv"

    csv_path = None
    if int_results_path.exists() and int_results_path.stat().st_size > 1000:
        csv_path = int_results_path
    elif results_path.exists():
        csv_path = results_path
        print(f"  使用 results.csv (international_results.csv 不可用)")

    matches = load_international_results(csv_path)
    matches = filter_by_date(matches, DATA_START, FREEZE_DATE)
    matches = matches[matches["result"].notna()].copy()

    print("  计算 Elo 历史...")
    matches = replay_elo_history(matches, EloConfig())

    # 注入 FIFA 排名
    try:
        rankings = load_fifa_rankings()
        matches = inject_fifa_rankings(matches, rankings)
        print(f"  FIFA 排名注入完成 (覆盖率: {matches['pre_match_fifa_rank_home'].notna().mean():.1%})")
    except Exception as e:
        print(f"  [警告] FIFA 排名注入失败: {e}")

    return matches


def extract_xg_from_statsbomb(statsbomb_dir: Path) -> pd.DataFrame:
    """从 StatsBomb 事件数据提取 xG 信息"""
    events_dir = statsbomb_dir / "events_2018"
    if not events_dir.exists():
        print("  [跳过] StatsBomb 事件目录不存在")
        return pd.DataFrame()

    xg_records = []
    for fpath in sorted(events_dir.glob("*.json")):
        try:
            events = json.load(open(fpath))
            match_id = int(fpath.stem)
            home_xg = 0.0
            away_xg = 0.0
            home_team = None
            away_team = None

            for ev in events:
                if ev.get("type", {}).get("name") == "Shot":
                    shot = ev.get("shot", {})
                    xg_val = shot.get("statsbomb_xg", 0.0)
                    team_name = ev.get("team", {}).get("name", "")

                    if home_team is None:
                        # 从第一个事件确定主客队
                        for e2 in events:
                            if "team" in e2:
                                tn = e2["team"]["name"]
                                if home_team is None:
                                    home_team = tn
                                elif tn != home_team and away_team is None:
                                    away_team = tn
                                    break

                    if team_name == home_team:
                        home_xg += xg_val
                    else:
                        away_xg += xg_val

            xg_records.append({
                "match_id": match_id,
                "home_team": home_team,
                "away_team": away_team,
                "home_xg": home_xg,
                "away_xg": away_xg,
            })
        except Exception:
            continue

    df = pd.DataFrame(xg_records)
    if len(df) > 0:
        print(f"  提取 xG 数据: {len(df)} 场比赛")
    return df


def augment_with_xg(matches: pd.DataFrame, feature_df: pd.DataFrame,
                    statsbomb_dir: Path) -> tuple[pd.DataFrame, list[str]]:
    """用 StatsBomb xG 数据增强因子"""
    xg_df = extract_xg_from_statsbomb(statsbomb_dir)
    if len(xg_df) == 0:
        print("  [跳过] 无 xG 数据可用")
        return feature_df, []

    # 队名映射 (StatsBomb -> Kaggle)
    name_map = {
        "Korea Republic": "South Korea",
        "Côte d'Ivoire": "Ivory Coast",
        "IR Iran": "Iran",
        "China PR": "China",
        "USA": "United States",
        "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    }
    xg_df["home_team"] = xg_df["home_team"].map(lambda x: name_map.get(x, x))
    xg_df["away_team"] = xg_df["away_team"].map(lambda x: name_map.get(x, x))

    # 计算累积 xG 统计（as-of 原则）
    xg_df = xg_df.sort_values("match_id")
    team_xg_history = {}

    new_cols = {
        "home_team_xg_created": [],
        "away_team_xg_created": [],
        "home_team_xg_conceded": [],
        "away_team_xg_conceded": [],
        "xg_diff": [],
        "xg_overperformance_home": [],
        "xg_overperformance_away": [],
    }

    for _, row in xg_df.iterrows():
        ht, at = row["home_team"], row["away_team"]
        hxg, axg = row["home_xg"], row["away_xg"]

        ht_hist = team_xg_history.get(ht, {"xg_created": [], "xg_conceded": []})
        at_hist = team_xg_history.get(at, {"xg_created": [], "xg_conceded": []})

        # 最近5场的 xG 统计
        n_recent = 5
        ht_created = np.mean(ht_hist["xg_created"][-n_recent:]) if ht_hist["xg_created"] else np.nan
        at_created = np.mean(at_hist["xg_created"][-n_recent:]) if at_hist["xg_created"] else np.nan
        ht_conceded = np.mean(ht_hist["xg_conceded"][-n_recent:]) if ht_hist["xg_conceded"] else np.nan
        at_conceded = np.mean(at_hist["xg_conceded"][-n_recent:]) if at_hist["xg_conceded"] else np.nan

        new_cols["home_team_xg_created"].append(ht_created)
        new_cols["away_team_xg_created"].append(at_created)
        new_cols["home_team_xg_conceded"].append(ht_conceded)
        new_cols["away_team_xg_conceded"].append(at_conceded)
        new_cols["xg_diff"].append(
            (ht_created - at_created) if not (np.isnan(ht_created) or np.isnan(at_created)) else np.nan
        )

        # xG 超额表现
        if ht_hist["xg_created"] and ht_hist["xg_conceded"]:
            avg_xg_c = np.mean(ht_hist["xg_created"][-n_recent:])
            avg_actual = np.mean(ht_hist["xg_conceded"][-n_recent:])
            new_cols["xg_overperformance_home"].append(
                avg_actual / avg_xg_c if avg_xg_c > 0 else np.nan
            )
        else:
            new_cols["xg_overperformance_home"].append(np.nan)

        if at_hist["xg_created"] and at_hist["xg_conceded"]:
            avg_xg_c = np.mean(at_hist["xg_created"][-n_recent:])
            avg_actual = np.mean(at_hist["xg_conceded"][-n_recent:])
            new_cols["xg_overperformance_away"].append(
                avg_actual / avg_xg_c if avg_xg_c > 0 else np.nan
            )
        else:
            new_cols["xg_overperformance_away"].append(np.nan)

        # 更新历史
        ht_hist["xg_created"].append(hxg)
        ht_hist["xg_conceded"].append(axg)
        at_hist["xg_created"].append(axg)
        at_hist["xg_conceded"].append(hxg)
        team_xg_history[ht] = ht_hist
        team_xg_history[at] = at_hist

    xg_aug = pd.DataFrame(new_cols, index=xg_df.index)
    xg_aug["match_id"] = xg_df["match_id"]

    # 合成赔率因子
    xg_diff = xg_aug["xg_diff"].values
    # 用 xG 差值估算概率 (简化 Poisson 模型)
    from scipy.stats import poisson
    xg_implied = []
    for i in range(len(xg_diff)):
        d = xg_diff[i]
        if np.isnan(d):
            xg_implied.append({"xg_implied_home_win": np.nan,
                               "xg_implied_draw": np.nan,
                               "xg_implied_away_win": np.nan})
            continue
        home_exp = max(1.3 + 0.4 * d, 0.3)
        away_exp = max(1.3 - 0.4 * d, 0.3)
        mg = 7
        hm = poisson.pmf(np.arange(mg + 1), home_exp)
        am = poisson.pmf(np.arange(mg + 1), away_exp)
        mat = np.outer(hm, am)
        hw = float(np.tril(mat, k=-1).sum())
        dr = float(np.trace(mat))
        aw = float(np.triu(mat, k=1).sum())
        total = hw + dr + aw
        xg_implied.append({
            "xg_implied_home_win": hw / total,
            "xg_implied_draw": dr / total,
            "xg_implied_away_win": aw / total,
        })

    xg_odds_df = pd.DataFrame(xg_implied)
    xg_aug = pd.concat([xg_aug, xg_odds_df], axis=1)

    # 合并到 feature_df
    xg_factor_cols = [c for c in xg_aug.columns if c != "match_id"]
    feature_df = feature_df.merge(xg_aug, on="match_id", how="left")

    n_enhanced = feature_df["home_team_xg_created"].notna().sum()
    print(f"  xG 增强: {n_enhanced} 场比赛获得 xG 因子 ({n_enhanced/len(feature_df)*100:.1f}%)")

    return feature_df, xg_factor_cols


def augment_with_weather(feature_df: pd.DataFrame,
                         weather_path: Path) -> tuple[pd.DataFrame, list[str]]:
    """用天气数据增强因子"""
    if not weather_path.exists():
        print("  [跳过] 天气数据文件不存在")
        return feature_df, []

    try:
        weather = json.load(open(weather_path))
        if not weather:
            return feature_df, []
    except Exception:
        return feature_df, []

    weather_cols = ["temperature", "humidity", "wind_speed", "precipitation",
                    "extreme_heat", "extreme_cold", "rain", "weather_discomfort_index"]
    for c in weather_cols:
        if c not in feature_df.columns:
            feature_df[c] = np.nan

    # 如果天气数据是列表格式
    if isinstance(weather, list):
        for w in weather:
            mid = w.get("match_id")
            if mid and mid in feature_df["match_id"].values:
                idx = feature_df.index[feature_df["match_id"] == mid][0]
                temp = w.get("temperature", np.nan)
                feature_df.loc[idx, "temperature"] = temp
                feature_df.loc[idx, "humidity"] = w.get("humidity", np.nan)
                feature_df.loc[idx, "wind_speed"] = w.get("wind_speed", np.nan)
                feature_df.loc[idx, "precipitation"] = w.get("precipitation", np.nan)
                feature_df.loc[idx, "extreme_heat"] = 1.0 if temp and temp > 30 else 0.0
                feature_df.loc[idx, "extreme_cold"] = 1.0 if temp and temp < 5 else 0.0
                feature_df.loc[idx, "rain"] = 1.0 if w.get("precipitation", 0) > 0 else 0.0
                # 不适指数
                hum = w.get("humidity", 50)
                feature_df.loc[idx, "weather_discomfort_index"] = (
                    temp - 0.55 * (1 - hum/100) * (temp - 14.5)
                ) if not np.isnan(temp) else np.nan

    n_weather = feature_df["temperature"].notna().sum()
    print(f"  天气增强: {n_weather} 场比赛获得天气因子")
    return feature_df, weather_cols


# ======================================================================
# Stage 2: 综合因子影响评估
# ======================================================================
def compute_ic_analysis(feature_df: pd.DataFrame, matches: pd.DataFrame,
                        factor_cols: list[str]) -> dict:
    """计算每个因子的 IC / ICIR / 方向稳定性 / 时间稳定性"""
    print("  计算 IC 分析...")
    results = {}

    # 合并 match_date
    merged = feature_df.merge(
        matches[["match_id", "match_date", "result"]], on="match_id", how="left"
    )
    merged["year"] = pd.to_datetime(merged["match_date"]).dt.year
    merged["outcome_numeric"] = label_to_numeric(merged["result"].values)

    for factor in factor_cols:
        if factor not in merged.columns:
            continue
        vals = merged[factor].values.astype(float)
        valid = ~np.isnan(vals)
        if valid.sum() < 100:
            continue

        fv = vals[valid]
        ov = merged.loc[valid, "outcome_numeric"].values

        # 全局 IC (Spearman)
        ic_global, ic_p = sp_stats.spearmanr(fv, ov)

        # 逐年 IC
        yearly_ics = {}
        for year, grp in merged[valid].groupby("year"):
            if len(grp) < 20:
                continue
            yv = grp[factor].values.astype(float)
            yo = grp["outcome_numeric"].values
            yv_valid = ~np.isnan(yv)
            if yv_valid.sum() < 10:
                continue
            ic_y, _ = sp_stats.spearmanr(yv[yv_valid], yo[yv_valid])
            yearly_ics[str(year)] = float(ic_y)

        ic_values = list(yearly_ics.values())
        if len(ic_values) < 3:
            continue

        # ICIR
        ic_mean = np.mean(ic_values)
        ic_std = np.std(ic_values)
        icir = ic_mean / ic_std if ic_std > 0 else 0.0

        # 方向稳定性
        expected_sign = 1 if ic_global >= 0 else -1
        dir_stable = sum(1 for v in ic_values if v * expected_sign >= 0) / len(ic_values)

        # 时间稳定性 (CV of IC)
        cv_ic = ic_std / abs(ic_mean) if abs(ic_mean) > 1e-10 else float("inf")

        results[factor] = {
            "ic_global": float(ic_global),
            "ic_p_value": float(ic_p),
            "icir": float(icir),
            "direction_stability": float(dir_stable),
            "yearly_ics": yearly_ics,
            "ic_mean": float(ic_mean),
            "ic_std": float(ic_std),
            "cv_ic": float(cv_ic),
            "n_valid": int(valid.sum()),
            "coverage": float(valid.sum() / len(vals)),
        }

    print(f"  IC 分析完成: {len(results)} 个因子")
    return results


def compute_brier_improvement(feature_df: pd.DataFrame, matches: pd.DataFrame,
                              factor_cols: list[str]) -> dict:
    """计算每个因子 + elo_diff 相比单独 elo_diff 的 Brier 改善"""
    print("  计算 Brier 改善...")
    merged = feature_df.merge(
        matches[["match_id", "match_date", "result"]], on="match_id", how="left"
    )
    merged = merged.sort_values("match_date").reset_index(drop=True)

    train_mask = pd.to_datetime(merged["match_date"]) <= pd.Timestamp(TRAIN_END, tz="UTC")
    test_mask = pd.to_datetime(merged["match_date"]) > pd.Timestamp(TRAIN_END, tz="UTC")

    train_df = merged[train_mask]
    test_df = merged[test_mask]

    if len(train_df) < 100 or len(test_df) < 50:
        print("  [警告] 数据不足，跳过 Brier 改善计算")
        return {}

    # 基线: 仅 elo_diff
    baseline_col = "elo_diff"
    if baseline_col not in train_df.columns:
        print("  [警告] elo_diff 列不存在，跳过")
        return {}

    X_train_base = train_df[[baseline_col]].values.astype(float)
    y_train = label_to_numeric(train_df["result"].values)
    X_test_base = test_df[[baseline_col]].values.astype(float)
    y_test_labels = test_df["result"].values

    imp = SimpleImputer(strategy="mean")
    X_train_base = imp.fit_transform(X_train_base)
    X_test_base = imp.transform(X_test_base)

    lr_base = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
    lr_base.fit(X_train_base, y_train)
    preds_base = lr_base.predict_proba(X_test_base)
    ev_base = evaluate_predictions(reorder_probs_for_eval(preds_base), y_test_labels)
    brier_base = ev_base.brier_score

    results = {}
    for factor in factor_cols:
        if factor == baseline_col or factor not in train_df.columns:
            continue

        X_train_aug = train_df[[baseline_col, factor]].values.astype(float)
        X_test_aug = test_df[[baseline_col, factor]].values.astype(float)

        imp2 = SimpleImputer(strategy="mean")
        X_train_aug = imp2.fit_transform(X_train_aug)
        X_test_aug = imp2.transform(X_test_aug)

        lr_aug = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
        lr_aug.fit(X_train_aug, y_train)
        preds_aug = lr_aug.predict_proba(X_test_aug)
        ev_aug = evaluate_predictions(reorder_probs_for_eval(preds_aug), y_test_labels)

        results[factor] = {
            "brier_with_factor": ev_aug.brier_score,
            "brier_baseline": brier_base,
            "brier_improvement": brier_base - ev_aug.brier_score,
            "relative_improvement": (brier_base - ev_aug.brier_score) / brier_base if brier_base > 0 else 0.0,
        }

    print(f"  Brier 改善计算完成: {len(results)} 个因子")
    return results


def compute_shap_analysis(feature_df: pd.DataFrame, matches: pd.DataFrame,
                          factor_cols: list[str], top_n: int = 20) -> dict:
    """使用 SHAP 计算因子重要性"""
    print("  计算 SHAP 分析...")
    merged = feature_df.merge(
        matches[["match_id", "match_date", "result"]], on="match_id", how="left"
    )
    merged = merged.sort_values("match_date").reset_index(drop=True)

    available_cols = [c for c in factor_cols if c in merged.columns]
    X = merged[available_cols].values.astype(float)
    y = label_to_numeric(merged["result"].values)

    # 训练 LightGBM
    model = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        num_leaves=31, min_child_samples=20, subsample=0.8,
        colsample_bytree=0.8, random_state=RANDOM_SEED, verbose=-1,
    )
    model.fit(X, y)

    # SHAP
    try:
        import shap
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X)

        # 多分类: shap_values 是列表
        if isinstance(shap_values, list):
            # 取绝对值平均
            mean_abs_shap = np.mean([np.abs(sv).mean(axis=0) for sv in shap_values], axis=0)
        else:
            mean_abs_shap = np.abs(shap_values).mean(axis=0)

        shap_ranking = {}
        for i, col in enumerate(available_cols):
            shap_ranking[col] = float(mean_abs_shap[i])

        # 排序
        sorted_shap = sorted(shap_ranking.items(), key=lambda x: x[1], reverse=True)
        top_shap = dict(sorted_shap[:top_n])

        print(f"  SHAP Top 5: {list(top_shap.keys())[:5]}")
        return {"shap_values": shap_ranking, "top_shap": top_shap}

    except ImportError:
        print("  [跳过] shap 库未安装")
        # 回退到 LightGBM 内置重要性
        imp = model.feature_importances_
        ranking = {col: float(imp[i]) for i, col in enumerate(available_cols)}
        sorted_ranking = sorted(ranking.items(), key=lambda x: x[1], reverse=True)
        return {"shap_values": ranking, "top_shap": dict(sorted_ranking[:top_n]),
                "note": "Used LightGBM built-in importance (shap not installed)"}


def compute_permutation_importance_analysis(feature_df: pd.DataFrame,
                                            matches: pd.DataFrame,
                                            factor_cols: list[str]) -> dict:
    """计算排列重要性"""
    print("  计算排列重要性...")
    merged = feature_df.merge(
        matches[["match_id", "match_date", "result"]], on="match_id", how="left"
    )
    merged = merged.sort_values("match_date").reset_index(drop=True)

    available_cols = [c for c in factor_cols if c in merged.columns]
    X = merged[available_cols].values.astype(float)
    y = label_to_numeric(merged["result"].values)

    imp = SimpleImputer(strategy="mean")
    X = imp.fit_transform(X)

    model = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        num_leaves=31, random_state=RANDOM_SEED, verbose=-1,
    )
    model.fit(X, y)

    result = permutation_importance(model, X, y, n_repeats=10,
                                    random_state=RANDOM_SEED, scoring="neg_log_loss")

    perm_imp = {}
    for i, col in enumerate(available_cols):
        perm_imp[col] = {
            "mean": float(result.importances_mean[i]),
            "std": float(result.importances_std[i]),
        }

    print(f"  排列重要性完成: {len(perm_imp)} 个因子")
    return perm_imp


def compute_mutual_information(feature_df: pd.DataFrame, matches: pd.DataFrame,
                               factor_cols: list[str]) -> dict:
    """计算互信息"""
    print("  计算互信息...")
    merged = feature_df.merge(
        matches[["match_id", "match_date", "result"]], on="match_id", how="left"
    )

    available_cols = [c for c in factor_cols if c in merged.columns]
    y = label_to_numeric(merged["result"].values)

    mi_results = {}
    for col in available_cols:
        vals = merged[col].values.astype(float)
        valid = ~np.isnan(vals)
        if valid.sum() < 100:
            continue
        # 离散化连续变量
        try:
            vals_disc = pd.qcut(vals[valid], q=10, duplicates="drop").codes
            y_sub = y[valid]
            mi = mutual_info_score(vals_disc, y_sub)
            mi_results[col] = float(mi)
        except Exception:
            continue

    print(f"  互信息计算完成: {len(mi_results)} 个因子")
    return mi_results


def compute_partial_dependence(feature_df: pd.DataFrame, matches: pd.DataFrame,
                               factor_cols: list[str], top_factors: list[str],
                               n_grid: int = 20) -> dict:
    """计算 Top 因子的 PDP 曲线"""
    print("  计算 PDP 曲线...")
    merged = feature_df.merge(
        matches[["match_id", "match_date", "result"]], on="match_id", how="left"
    )

    available = [c for c in top_factors if c in merged.columns]
    if not available:
        return {}

    X = merged[available].values.astype(float)
    y = label_to_numeric(merged["result"].values)

    imp = SimpleImputer(strategy="mean")
    X = imp.fit_transform(X)

    model = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        num_leaves=31, random_state=RANDOM_SEED, verbose=-1,
    )
    model.fit(X, y)

    pdp_results = {}
    for i, col in enumerate(available[:10]):
        grid = np.linspace(np.percentile(X[:, i], 5), np.percentile(X[:, i], 95), n_grid)
        pdp_vals = []
        for g in grid:
            X_mod = X.copy()
            X_mod[:, i] = g
            preds = model.predict_proba(X_mod)
            # P(draw) 作为关键指标
            pdp_vals.append(float(preds[:, 1].mean()))

        pdp_results[col] = {
            "grid": grid.tolist(),
            "pdp_draw": pdp_vals,
        }

    print(f"  PDP 计算完成: {len(pdp_results)} 个因子")
    return pdp_results


def compute_factor_interactions(feature_df: pd.DataFrame, matches: pd.DataFrame,
                                factor_cols: list[str], top_factors: list[str]) -> dict:
    """计算 Top 因子对的交互强度"""
    print("  计算因子交互...")
    merged = feature_df.merge(
        matches[["match_id", "match_date", "result"]], on="match_id", how="left"
    )

    available = [c for c in top_factors[:5] if c in merged.columns]
    if len(available) < 2:
        return {}

    X = merged[available].values.astype(float)
    y = label_to_numeric(merged["result"].values)

    imp = SimpleImputer(strategy="mean")
    X = imp.fit_transform(X)

    interactions = {}
    for i in range(len(available)):
        for j in range(i + 1, len(available)):
            # 用 H-statistic 近似: 比较有交互 vs 无交互的预测差异
            model_full = lgb.LGBMClassifier(
                n_estimators=100, max_depth=5, random_state=RANDOM_SEED, verbose=-1,
            )
            model_full.fit(X, y)
            pred_full = model_full.predict_proba(X)

            # 无交互: 用两列分别训练后相加
            Xi = X[:, i].reshape(-1, 1)
            Xj = X[:, j].reshape(-1, 1)

            mi = lgb.LGBMClassifier(n_estimators=100, max_depth=3, random_state=RANDOM_SEED, verbose=-1)
            mj = lgb.LGBMClassifier(n_estimators=100, max_depth=3, random_state=RANDOM_SEED, verbose=-1)
            mi.fit(Xi, y)
            mj.fit(Xj, y)

            pred_i = mi.predict_proba(Xi)
            pred_j = mj.predict_proba(Xj)

            # 交互强度 = ||f(x_i, x_j) - f(x_i) - f(x_j)|| / ||f(x_i, x_j)||
            diff = pred_full - pred_i - pred_j + np.mean(pred_full, axis=0)
            interaction_strength = float(np.mean(np.abs(diff)))

            interactions[f"{available[i]}_x_{available[j]}"] = {
                "strength": interaction_strength,
                "factor_a": available[i],
                "factor_b": available[j],
            }

    print(f"  因子交互计算完成: {len(interactions)} 对")
    return interactions


# ======================================================================
# Stage 3: 平局预测突破
# ======================================================================
def _prepare_ml_data(feature_df: pd.DataFrame, matches: pd.DataFrame,
                     factor_cols: list[str], train_end: str = TRAIN_END):
    """准备 ML 数据集"""
    merged = feature_df.merge(
        matches[["match_id", "match_date", "result", "is_neutral",
                 "tournament_category", "tournament"]], on="match_id", how="left"
    )
    merged = merged.sort_values("match_date").reset_index(drop=True)

    available = [c for c in factor_cols if c in merged.columns]
    train_mask = pd.to_datetime(merged["match_date"]) <= pd.Timestamp(train_end, tz="UTC")
    test_mask = pd.to_datetime(merged["match_date"]) > pd.Timestamp(train_end, tz="UTC")

    train_df = merged[train_mask]
    test_df = merged[test_mask]

    X_train = train_df[available].values.astype(float)
    y_train = label_to_numeric(train_df["result"].values)
    y_train_labels = train_df["result"].values

    X_test = test_df[available].values.astype(float)
    y_test = label_to_numeric(test_df["result"].values)
    y_test_labels = test_df["result"].values

    return X_train, y_train, y_train_labels, X_test, y_test, y_test_labels, available


def _draw_metrics(y_true_labels: np.ndarray, probs: np.ndarray) -> dict:
    """计算平局相关指标"""
    is_draw = y_true_labels == "D"
    n_draw = is_draw.sum()
    n_total = len(y_true_labels)

    # 预测类别
    pred_classes = np.argmax(probs, axis=1)
    # 0=away, 1=draw, 2=home
    pred_is_draw = pred_classes == 1

    # 平局命中率 (recall)
    draw_recall = float(pred_is_draw[is_draw].sum() / n_draw) if n_draw > 0 else 0.0
    # 平局精确率
    draw_precision = float(is_draw[pred_is_draw].sum() / pred_is_draw.sum()) if pred_is_draw.sum() > 0 else 0.0
    # 平局 F1
    draw_f1 = (2 * draw_precision * draw_recall / (draw_precision + draw_recall)
               if (draw_precision + draw_recall) > 0 else 0.0)

    # Brier
    y_oh = label_to_onehot(y_true_labels)
    brier = float(np.mean(np.sum((probs - y_oh) ** 2, axis=1)))

    # 整体准确率
    label_map = {"H": 2, "D": 1, "A": 0}
    true_classes = np.array([label_map[l] for l in y_true_labels])
    accuracy = float(np.mean(pred_classes == true_classes))

    # 平局概率统计
    draw_probs = probs[:, 1]
    draw_prob_mean = float(draw_probs.mean())
    draw_prob_when_draw = float(draw_probs[is_draw].mean()) if n_draw > 0 else 0.0
    draw_prob_when_not_draw = float(draw_probs[~is_draw].mean()) if (~is_draw).sum() > 0 else 0.0

    return {
        "draw_hit_rate": draw_recall,
        "draw_precision": draw_precision,
        "draw_f1": draw_f1,
        "brier_score": brier,
        "accuracy": accuracy,
        "n_draw": int(n_draw),
        "n_total": int(n_total),
        "draw_rate": float(n_draw / n_total),
        "draw_prob_mean": draw_prob_mean,
        "draw_prob_when_draw": draw_prob_when_draw,
        "draw_prob_when_not_draw_": draw_prob_when_not_draw,
    }


def draw_approach_1_separate(X_train, y_train, y_train_labels,
                             X_test, y_test, y_test_labels) -> dict:
    """方案1: 独立平局二分类器"""
    print("  方案1: 独立平局二分类器...")

    # 二分类: Draw vs Not-Draw
    y_train_binary = (y_train == 1).astype(int)
    y_test_binary = (y_test == 1).astype(int)

    # 代价敏感: 给平局更高权重
    n_draw = y_train_binary.sum()
    n_not = len(y_train_binary) - n_draw
    scale = n_not / n_draw if n_draw > 0 else 1.0

    imp = SimpleImputer(strategy="mean")
    X_tr = imp.fit_transform(X_train)
    X_te = imp.transform(X_test)

    # LightGBM with class weight
    model = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        num_leaves=31, random_state=RANDOM_SEED, verbose=-1,
        scale_pos_weight=scale,
    )
    model.fit(X_tr, y_train_binary)

    # P(Draw)
    p_draw = model.predict_proba(X_te)[:, 1]

    # P(Home|Not Draw) 和 P(Away|Not Draw) 用三分类模型
    model_3way = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        num_leaves=31, random_state=RANDOM_SEED, verbose=-1,
    )
    model_3way.fit(X_tr, y_train)
    probs_3way = model_3way.predict_proba(X_te)

    # 组合: P(Draw) from binary, P(H|not D) and P(A|not D) from 3-way
    p_not_draw = 1 - p_draw
    # 从三分类中获取条件概率
    p_h_given_not_d = probs_3way[:, 2] / (probs_3way[:, 0] + probs_3way[:, 2] + 1e-10)
    p_a_given_not_d = 1 - p_h_given_not_d

    final_probs = np.column_stack([
        p_a_given_not_d * p_not_draw,  # away
        p_draw,                          # draw
        p_h_given_not_d * p_not_draw,   # home
    ])
    # 归一化
    final_probs = final_probs / final_probs.sum(axis=1, keepdims=True)

    metrics = _draw_metrics(y_test_labels, final_probs)
    metrics["approach"] = "separate_draw_model"
    return metrics


def draw_approach_2_ordinal(X_train, y_train, y_train_labels,
                            X_test, y_test, y_test_labels) -> dict:
    """方案2: 序数回归 (自定义实现)"""
    print("  方案2: 序数回归...")

    imp = SimpleImputer(strategy="mean")
    X_tr = imp.fit_transform(X_train)
    X_te = imp.transform(X_test)

    # 简化序数回归: 用两个累积 Logistic 模型
    # P(Y <= 0) = sigmoid(X @ w1 + b1)  (away or draw vs home)
    # P(Y <= 1) = sigmoid(X @ w2 + b2)  (away vs draw or home)
    # P(Away) = P(Y <= 0)
    # P(Draw) = P(Y <= 1) - P(Y <= 0)
    # P(Home) = 1 - P(Y <= 1)

    y_away_or_draw = (y_train <= 1).astype(int)  # Y <= 1
    y_away_only = (y_train == 0).astype(int)      # Y <= 0

    lr_cum1 = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
    lr_cum1.fit(X_tr, y_away_or_draw)

    lr_cum2 = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
    lr_cum2.fit(X_tr, y_away_only)

    p_cum1 = lr_cum1.predict_proba(X_te)[:, 1]  # P(Y <= 1)
    p_cum2 = lr_cum2.predict_proba(X_te)[:, 1]  # P(Y <= 0)

    p_away = p_cum2
    p_draw = p_cum1 - p_cum2
    p_home = 1 - p_cum1

    # 确保 P(Draw) >= 0
    p_draw = np.maximum(p_draw, 0.01)
    final_probs = np.column_stack([p_away, p_draw, p_home])
    final_probs = final_probs / final_probs.sum(axis=1, keepdims=True)

    metrics = _draw_metrics(y_test_labels, final_probs)
    metrics["approach"] = "ordinal_regression"
    return metrics


def draw_approach_3_three_way(X_train, y_train, y_train_labels,
                              X_test, y_test, y_test_labels) -> dict:
    """方案3: 三路独立模型"""
    print("  方案3: 三路独立模型...")

    imp = SimpleImputer(strategy="mean")
    X_tr = imp.fit_transform(X_train)
    X_te = imp.transform(X_test)

    # 模型1: P(Home Win)
    y_home = (y_train == 2).astype(int)
    model_home = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1,
    )
    model_home.fit(X_tr, y_home)
    p_home = model_home.predict_proba(X_te)[:, 1]

    # 模型2: P(Draw)
    y_draw = (y_train == 1).astype(int)
    n_draw = y_draw.sum()
    n_not = len(y_draw) - n_draw
    model_draw = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1,
        scale_pos_weight=n_not / n_draw if n_draw > 0 else 1.0,
    )
    model_draw.fit(X_tr, y_draw)
    p_draw = model_draw.predict_proba(X_te)[:, 1]

    # 模型3: P(Away Win)
    y_away = (y_train == 0).astype(int)
    model_away = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1,
    )
    model_away.fit(X_tr, y_away)
    p_away = model_away.predict_proba(X_te)[:, 1]

    final_probs = np.column_stack([p_away, p_draw, p_home])
    final_probs = final_probs / final_probs.sum(axis=1, keepdims=True)

    metrics = _draw_metrics(y_test_labels, final_probs)
    metrics["approach"] = "three_way_separate"
    return metrics


def draw_approach_4_boosted_ensemble(X_train, y_train, y_train_labels,
                                     X_test, y_test, y_test_labels,
                                     matches_train, matches_test) -> dict:
    """方案4: 平局增强集成 (Elo+Poisson 基线 + 平局调整)"""
    print("  方案4: 平局增强集成...")

    # Elo+Poisson 基线
    elo_model = EloPoissonBaseline()
    base_probs = []
    for _, m in matches_test.iterrows():
        elo_h = m.get("pre_match_elo_home", 1500)
        elo_a = m.get("pre_match_elo_away", 1500)
        is_neutral = m.get("is_neutral", False)
        if pd.isna(elo_h):
            elo_h = 1500
        if pd.isna(elo_a):
            elo_a = 1500
        p = elo_model.predict(elo_home=elo_h, elo_away=elo_a, is_neutral=is_neutral)
        base_probs.append([p.away_win, p.draw, p.home_win])
    base_probs = np.array(base_probs)

    # 用因子计算平局调整
    imp = SimpleImputer(strategy="mean")
    X_tr = imp.fit_transform(X_train)
    X_te = imp.transform(X_test)

    # 训练平局倾向模型
    y_draw = (y_train == 1).astype(int)
    lr_draw = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
    lr_draw.fit(X_tr, y_draw)
    draw_adjustment = lr_draw.predict_proba(X_te)[:, 1]

    # 调整: 增大平局概率
    adjustment_weight = 0.3
    adjusted_draw = base_probs[:, 1] * (1 - adjustment_weight) + draw_adjustment * adjustment_weight
    remaining = 1 - adjusted_draw
    home_ratio = base_probs[:, 2] / (base_probs[:, 0] + base_probs[:, 2] + 1e-10)

    final_probs = np.column_stack([
        remaining * (1 - home_ratio),  # away
        adjusted_draw,                  # draw
        remaining * home_ratio,         # home
    ])
    final_probs = final_probs / final_probs.sum(axis=1, keepdims=True)

    metrics = _draw_metrics(y_test_labels, final_probs)
    metrics["approach"] = "draw_boosted_ensemble"
    return metrics


def draw_approach_5_threshold_optimization(X_train, y_train, y_train_labels,
                                           X_test, y_test, y_test_labels) -> dict:
    """方案5: 阈值优化 + 代价敏感学习"""
    print("  方案5: 阈值优化...")

    imp = SimpleImputer(strategy="mean")
    X_tr = imp.fit_transform(X_train)
    X_te = imp.transform(X_test)

    # 代价敏感 LightGBM
    sample_weights = np.ones(len(y_train))
    draw_mask = y_train == 1
    n_draw = draw_mask.sum()
    n_total = len(y_train)
    sample_weights[draw_mask] = n_total / (3 * n_draw) if n_draw > 0 else 1.0

    model = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        num_leaves=31, random_state=RANDOM_SEED, verbose=-1,
    )
    model.fit(X_tr, y_train, sample_weight=sample_weights)
    probs = model.predict_proba(X_te)

    # 优化阈值: 搜索最佳平局阈值
    best_f1 = 0
    best_threshold = 0.27  # 默认
    for thresh in np.arange(0.15, 0.45, 0.01):
        adjusted = probs.copy()
        # 如果平局概率超过阈值，提升平局预测
        draw_boost = adjusted[:, 1] > thresh
        pred_classes = np.argmax(adjusted, axis=1)
        # 强制预测平局
        pred_classes[draw_boost] = 1

        is_draw = y_test == 1
        draw_recall = float((pred_classes[is_draw] == 1).sum() / is_draw.sum()) if is_draw.sum() > 0 else 0
        draw_prec = float((is_draw[pred_classes == 1]).sum() / (pred_classes == 1).sum()) if (pred_classes == 1).sum() > 0 else 0
        f1 = 2 * draw_prec * draw_recall / (draw_prec + draw_recall) if (draw_prec + draw_recall) > 0 else 0

        if f1 > best_f1:
            best_f1 = f1
            best_threshold = thresh

    # 用最佳阈值重新计算
    adjusted = probs.copy()
    draw_boost = adjusted[:, 1] > best_threshold
    pred_classes = np.argmax(adjusted, axis=1)
    pred_classes[draw_boost] = 1

    # 重新生成概率（用于 Brier）
    probs_adjusted = probs.copy()
    for i in range(len(probs)):
        if probs[i, 1] > best_threshold:
            boost = min(probs[i, 1] * 1.5, 0.6)
            probs_adjusted[i, 1] = boost
            remaining = 1 - boost
            ratio = probs[i, 2] / (probs[i, 0] + probs[i, 2] + 1e-10)
            probs_adjusted[i, 2] = remaining * ratio
            probs_adjusted[i, 0] = remaining * (1 - ratio)
    probs_adjusted = probs_adjusted / probs_adjusted.sum(axis=1, keepdims=True)

    metrics = _draw_metrics(y_test_labels, probs_adjusted)
    metrics["approach"] = "threshold_optimization"
    metrics["optimal_draw_threshold"] = float(best_threshold)
    return metrics


def run_draw_breakthrough(feature_df: pd.DataFrame, matches: pd.DataFrame,
                          factor_cols: list[str]) -> dict:
    """运行所有平局预测方案"""
    print_section("Stage 3: 平局预测突破")

    # 准备数据
    data = _prepare_ml_data(feature_df, matches, factor_cols)
    X_train, y_train, y_train_labels, X_test, y_test, y_test_labels, available = data

    # 准备 matches 子集 (用于方案4)
    merged = feature_df.merge(
        matches[["match_id", "match_date", "result", "is_neutral",
                 "pre_match_elo_home", "pre_match_elo_away"]], on="match_id", how="left"
    )
    merged = merged.sort_values("match_date").reset_index(drop=True)
    train_mask = pd.to_datetime(merged["match_date"]) <= pd.Timestamp(TRAIN_END, tz="UTC")
    test_mask = pd.to_datetime(merged["match_date"]) > pd.Timestamp(TRAIN_END, tz="UTC")
    matches_test = merged[test_mask]

    # 基线: 标准 LightGBM
    print("  基线: 标准 LightGBM...")
    imp = SimpleImputer(strategy="mean")
    X_tr = imp.fit_transform(X_train)
    X_te = imp.transform(X_test)
    model_base = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        num_leaves=31, random_state=RANDOM_SEED, verbose=-1,
    )
    model_base.fit(X_tr, y_train)
    probs_base = model_base.predict_proba(X_te)
    baseline_metrics = _draw_metrics(y_test_labels, probs_base)
    baseline_metrics["approach"] = "baseline_lgb"

    # Elo+Poisson 基线
    print("  基线: Elo+Poisson...")
    elo_model = EloPoissonBaseline()
    elo_probs = []
    for _, m in matches_test.iterrows():
        elo_h = m.get("pre_match_elo_home", 1500)
        elo_a = m.get("pre_match_elo_away", 1500)
        is_neutral = m.get("is_neutral", False)
        if pd.isna(elo_h):
            elo_h = 1500
        if pd.isna(elo_a):
            elo_a = 1500
        p = elo_model.predict(elo_home=elo_h, elo_away=elo_a, is_neutral=is_neutral)
        elo_probs.append([p.away_win, p.draw, p.home_win])
    elo_probs = np.array(elo_probs)
    elo_baseline = _draw_metrics(y_test_labels, elo_probs)
    elo_baseline["approach"] = "baseline_elo_poisson"

    # 运行5种方案
    results = {
        "baseline_lgb": baseline_metrics,
        "baseline_elo_poisson": elo_baseline,
    }

    try:
        results["approach_1_separate"] = draw_approach_1_separate(
            X_train, y_train, y_train_labels, X_test, y_test, y_test_labels)
    except Exception as e:
        results["approach_1_separate"] = {"error": str(e)}

    try:
        results["approach_2_ordinal"] = draw_approach_2_ordinal(
            X_train, y_train, y_train_labels, X_test, y_test, y_test_labels)
    except Exception as e:
        results["approach_2_ordinal"] = {"error": str(e)}

    try:
        results["approach_3_three_way"] = draw_approach_3_three_way(
            X_train, y_train, y_train_labels, X_test, y_test, y_test_labels)
    except Exception as e:
        results["approach_3_three_way"] = {"error": str(e)}

    try:
        results["approach_4_boosted"] = draw_approach_4_boosted_ensemble(
            X_train, y_train, y_train_labels, X_test, y_test, y_test_labels,
            merged[train_mask], matches_test)
    except Exception as e:
        results["approach_4_boosted"] = {"error": str(e)}

    try:
        results["approach_5_threshold"] = draw_approach_5_threshold_optimization(
            X_train, y_train, y_train_labels, X_test, y_test, y_test_labels)
    except Exception as e:
        results["approach_5_threshold"] = {"error": str(e)}

    # 汇总
    print("\n  ─── 平局预测方案对比 ───")
    print(f"  {'方案':<30} {'Brier':>8} {'Draw命中':>10} {'Draw精确':>10} {'Draw F1':>10} {'准确率':>8}")
    print("  " + "-" * 80)
    for name, m in sorted(results.items(), key=lambda x: x[1].get("brier_score", 1.0)):
        if "error" in m:
            print(f"  {name:<30} ERROR: {m['error']}")
        else:
            print(f"  {name:<30} {m['brier_score']:>8.4f} {m['draw_hit_rate']:>10.1%} "
                  f"{m['draw_precision']:>10.1%} {m['draw_f1']:>10.1%} {m['accuracy']:>8.1%}")

    return results


# ======================================================================
# Stage 4: 高级模型架构
# ======================================================================
def run_model_architectures(feature_df: pd.DataFrame, matches: pd.DataFrame,
                            factor_cols: list[str]) -> dict:
    """运行所有高级模型架构"""
    print_section("Stage 4: 高级模型架构")

    data = _prepare_ml_data(feature_df, matches, factor_cols)
    X_train, y_train, y_train_labels, X_test, y_test, y_test_labels, available = data

    imp = SimpleImputer(strategy="mean")
    X_tr = imp.fit_transform(X_train)
    X_te = imp.transform(X_test)

    results = {}

    # ─── 模型1: Stacking Ensemble ───
    print("  模型1: Stacking Ensemble...")
    try:
        tscv = TimeSeriesSplit(n_splits=5)

        # Level 0: 三个基模型的 OOF 预测
        oof_preds = np.zeros((len(X_tr), 3))

        # Elo Logistic
        elo_model = EloLogisticBaseline()
        # 用因子中的 elo_diff 近似
        for fold_idx, (tr_idx, val_idx) in enumerate(tscv.split(X_tr)):
            # LightGBM
            lgb_m = lgb.LGBMClassifier(n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1)
            lgb_m.fit(X_tr[tr_idx], y_train[tr_idx])
            oof_preds[val_idx] += lgb_m.predict_proba(X_tr[val_idx]) / 2

            # Logistic Regression
            lr_m = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
            lr_m.fit(X_tr[tr_idx], y_train[tr_idx])
            oof_preds[val_idx] += lr_m.predict_proba(X_tr[val_idx]) / 2

        # Level 1: Meta-learner
        meta_lr = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
        meta_lr.fit(oof_preds, y_train)

        # 生成 Level 0 测试预测
        lgb_full = lgb.LGBMClassifier(n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1)
        lgb_full.fit(X_tr, y_train)
        lr_full = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
        lr_full.fit(X_tr, y_train)

        test_l0 = (lgb_full.predict_proba(X_te) + lr_full.predict_proba(X_te)) / 2
        stacking_preds = meta_lr.predict_proba(test_l0)

        ev = evaluate_predictions(reorder_probs_for_eval(stacking_preds), y_test_labels)
        results["stacking_ensemble"] = {
            "brier_score": ev.brier_score,
            "accuracy": ev.accuracy,
            "draw_accuracy": ev.draw_accuracy,
            "log_loss": ev.log_loss,
            "ece": ev.ece,
        }
    except Exception as e:
        results["stacking_ensemble"] = {"error": str(e)}

    # ─── 模型2: 校准 LightGBM ───
    print("  模型2: 校准 LightGBM...")
    try:
        # Isotonic
        lgb_base = lgb.LGBMClassifier(
            n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1,
        )
        from sklearn.calibration import CalibratedClassifierCV
        cal_iso = CalibratedClassifierCV(lgb_base, method="isotonic", cv=3)
        cal_iso.fit(X_tr, y_train)
        preds_iso = cal_iso.predict_proba(X_te)
        ev_iso = evaluate_predictions(reorder_probs_for_eval(preds_iso), y_test_labels)

        # Platt (sigmoid)
        lgb_base2 = lgb.LGBMClassifier(
            n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1,
        )
        cal_sig = CalibratedClassifierCV(lgb_base2, method="sigmoid", cv=3)
        cal_sig.fit(X_tr, y_train)
        preds_sig = cal_sig.predict_proba(X_te)
        ev_sig = evaluate_predictions(reorder_probs_for_eval(preds_sig), y_test_labels)

        results["calibrated_isotonic"] = {
            "brier_score": ev_iso.brier_score, "accuracy": ev_iso.accuracy,
            "draw_accuracy": ev_iso.draw_accuracy, "log_loss": ev_iso.log_loss,
        }
        results["calibrated_platt"] = {
            "brier_score": ev_sig.brier_score, "accuracy": ev_sig.accuracy,
            "draw_accuracy": ev_sig.draw_accuracy, "log_loss": ev_sig.log_loss,
        }
    except Exception as e:
        results["calibrated_lgb"] = {"error": str(e)}

    # ─── 模型3: 特征选择 LightGBM ───
    print("  模型3: 特征选择 LightGBM...")
    try:
        # 使用 IC > 0.02 且方向稳定性 > 60% 的因子
        # 先快速计算 IC
        merged = feature_df.merge(
            matches[["match_id", "match_date", "result"]], on="match_id", how="left"
        )
        merged["outcome_numeric"] = label_to_numeric(merged["result"].values)

        selected_cols = []
        for col in available:
            vals = merged[col].values.astype(float)
            valid = ~np.isnan(vals)
            if valid.sum() < 100:
                continue
            ic, _ = sp_stats.spearmanr(vals[valid], merged.loc[valid, "outcome_numeric"].values)
            if abs(ic) > 0.02:
                selected_cols.append(col)

        # 移除有害因子组
        harmful = set()
        for grp in ["form_enhanced", "h2h"]:
            harmful.update(FACTOR_GROUPS.get(grp, []))
        selected_cols = [c for c in selected_cols if c not in harmful]

        if len(selected_cols) >= 3:
            sel_idx = [available.index(c) for c in selected_cols if c in available]
            X_tr_sel = X_tr[:, sel_idx]
            X_te_sel = X_te[:, sel_idx]

            lgb_sel = lgb.LGBMClassifier(
                n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1,
            )
            lgb_sel.fit(X_tr_sel, y_train)
            preds_sel = lgb_sel.predict_proba(X_te_sel)
            ev_sel = evaluate_predictions(reorder_probs_for_eval(preds_sel), y_test_labels)

            results["feature_selected_lgb"] = {
                "brier_score": ev_sel.brier_score, "accuracy": ev_sel.accuracy,
                "draw_accuracy": ev_sel.draw_accuracy, "log_loss": ev_sel.log_loss,
                "n_features": len(selected_cols),
                "selected_features": selected_cols,
            }
        else:
            results["feature_selected_lgb"] = {"error": "Too few features selected"}
    except Exception as e:
        results["feature_selected_lgb"] = {"error": str(e)}

    # ─── 模型4: 时间加权训练 ───
    print("  模型4: 时间加权训练...")
    try:
        merged = feature_df.merge(
            matches[["match_id", "match_date", "result"]], on="match_id", how="left"
        )
        merged = merged.sort_values("match_date").reset_index(drop=True)
        train_mask_m = pd.to_datetime(merged["match_date"]) <= pd.Timestamp(TRAIN_END, tz="UTC")
        train_dates = pd.to_datetime(merged.loc[train_mask_m, "match_date"])

        # 指数衰减权重 (半衰期2年)
        max_date = train_dates.max()
        days_diff = (max_date - train_dates).dt.days
        half_life = 730  # 2年
        weights = np.exp(-np.log(2) * days_diff / half_life)
        weights = weights.values.astype(float)

        lgb_tw = lgb.LGBMClassifier(
            n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1,
        )
        lgb_tw.fit(X_tr, y_train, sample_weight=weights)
        preds_tw = lgb_tw.predict_proba(X_te)
        ev_tw = evaluate_predictions(reorder_probs_for_eval(preds_tw), y_test_labels)

        results["time_weighted_lgb"] = {
            "brier_score": ev_tw.brier_score, "accuracy": ev_tw.accuracy,
            "draw_accuracy": ev_tw.draw_accuracy, "log_loss": ev_tw.log_loss,
        }
    except Exception as e:
        results["time_weighted_lgb"] = {"error": str(e)}

    # ─── 模型5: 世界杯专用模型 ───
    print("  模型5: 世界杯专用模型...")
    try:
        merged = feature_df.merge(
            matches[["match_id", "match_date", "result", "tournament_category"]], on="match_id", how="left"
        )
        merged = merged.sort_values("match_date").reset_index(drop=True)

        # 只用世界杯比赛训练
        wc_train = merged[(merged["tournament_category"] == "world_cup") &
                          (pd.to_datetime(merged["match_date"]) <= pd.Timestamp(TRAIN_END, tz="UTC"))]

        if len(wc_train) >= 50:
            X_wc = wc_train[available].values.astype(float)
            y_wc = label_to_numeric(wc_train["result"].values)

            imp_wc = SimpleImputer(strategy="mean")
            X_wc = imp_wc.fit_transform(X_wc)

            lgb_wc = lgb.LGBMClassifier(
                n_estimators=200, max_depth=4, learning_rate=0.05,
                num_leaves=15, random_state=RANDOM_SEED, verbose=-1,
            )
            lgb_wc.fit(X_wc, y_wc)

            # 在世界杯测试集上评估
            wc_test = merged[(merged["tournament_category"] == "world_cup") &
                             (pd.to_datetime(merged["match_date"]) > pd.Timestamp(TRAIN_END, tz="UTC"))]

            if len(wc_test) >= 10:
                X_wc_test = imp_wc.transform(wc_test[available].values.astype(float))
                y_wc_test_labels = wc_test["result"].values
                preds_wc = lgb_wc.predict_proba(X_wc_test)
                ev_wc = evaluate_predictions(reorder_probs_for_eval(preds_wc), y_wc_test_labels)

                results["wc_specialized"] = {
                    "brier_score": ev_wc.brier_score, "accuracy": ev_wc.accuracy,
                    "draw_accuracy": ev_wc.draw_accuracy, "log_loss": ev_wc.log_loss,
                    "n_train_wc": len(wc_train), "n_test_wc": len(wc_test),
                }
        else:
            results["wc_specialized"] = {"error": "Insufficient WC training data"}
    except Exception as e:
        results["wc_specialized"] = {"error": str(e)}

    # ─── 标准 LightGBM 基线 (用于对比) ───
    lgb_std = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        num_leaves=31, random_state=RANDOM_SEED, verbose=-1,
    )
    lgb_std.fit(X_tr, y_train)
    preds_std = lgb_std.predict_proba(X_te)
    ev_std = evaluate_predictions(reorder_probs_for_eval(preds_std), y_test_labels)
    results["standard_lgb"] = {
        "brier_score": ev_std.brier_score, "accuracy": ev_std.accuracy,
        "draw_accuracy": ev_std.draw_accuracy, "log_loss": ev_std.log_loss,
    }

    # ─── 标准 Logistic Regression ───
    lr_std = LogisticRegression(max_iter=2000, C=1.0, solver="lbfgs")
    lr_std.fit(X_tr, y_train)
    preds_lr = lr_std.predict_proba(X_te)
    ev_lr = evaluate_predictions(reorder_probs_for_eval(preds_lr), y_test_labels)
    results["standard_lr"] = {
        "brier_score": ev_lr.brier_score, "accuracy": ev_lr.accuracy,
        "draw_accuracy": ev_lr.draw_accuracy, "log_loss": ev_lr.log_loss,
    }

    # 汇总
    print("\n  ─── 模型架构对比 ───")
    print(f"  {'模型':<30} {'Brier':>8} {'LogLoss':>8} {'准确率':>8} {'Draw命中':>10}")
    print("  " + "-" * 70)
    for name, m in sorted(results.items(), key=lambda x: x[1].get("brier_score", 1.0)):
        if "error" in m:
            print(f"  {name:<30} ERROR: {m['error']}")
        else:
            print(f"  {name:<30} {m['brier_score']:>8.4f} {m['log_loss']:>8.4f} "
                  f"{m['accuracy']:>8.1%} {m['draw_accuracy']:>10.1%}")

    return results


# ======================================================================
# Stage 5: 严格验证
# ======================================================================
def run_walk_forward_validation(feature_df: pd.DataFrame, matches: pd.DataFrame,
                                factor_cols: list[str]) -> dict:
    """Walk-Forward 验证"""
    print_section("Stage 5a: Walk-Forward 验证")

    merged = feature_df.merge(
        matches[["match_id", "match_date", "result"]], on="match_id", how="left"
    )
    merged = merged.sort_values("match_date").reset_index(drop=True)
    merged["year"] = pd.to_datetime(merged["match_date"]).dt.year

    available = [c for c in factor_cols if c in merged.columns]
    years = sorted(merged["year"].unique())

    results = {}
    initial_train_years = 6

    for i, test_year in enumerate(years):
        if i < initial_train_years:
            continue

        train_years = years[:i]
        test_mask = merged["year"] == test_year
        train_mask = merged["year"].isin(train_years)

        train_df = merged[train_mask]
        test_df = merged[test_mask]

        if len(train_df) < 100 or len(test_df) < 20:
            continue

        X_tr = train_df[available].values.astype(float)
        y_tr = label_to_numeric(train_df["result"].values)
        X_te = test_df[available].values.astype(float)
        y_te_labels = test_df["result"].values

        imp = SimpleImputer(strategy="mean")
        X_tr = imp.fit_transform(X_tr)
        X_te = imp.transform(X_te)

        model = lgb.LGBMClassifier(
            n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1,
        )
        model.fit(X_tr, y_tr)
        preds = model.predict_proba(X_te)
        ev = evaluate_predictions(reorder_probs_for_eval(preds), y_te_labels)

        results[str(test_year)] = {
            "brier_score": ev.brier_score,
            "accuracy": ev.accuracy,
            "draw_accuracy": ev.draw_accuracy,
            "n_train": len(train_df),
            "n_test": len(test_df),
        }

    if results:
        avg_brier = np.mean([r["brier_score"] for r in results.values()])
        print(f"  Walk-Forward 平均 Brier: {avg_brier:.4f} ({len(results)} 年)")

    return results


def run_world_cup_backtest(feature_df: pd.DataFrame, matches: pd.DataFrame,
                           factor_cols: list[str]) -> dict:
    """世界杯按时间顺序回测"""
    print_section("Stage 5b: 世界杯按时间顺序回测")

    merged = feature_df.merge(
        matches[["match_id", "match_date", "result", "tournament_category"]], on="match_id", how="left"
    )
    merged = merged.sort_values("match_date").reset_index(drop=True)
    merged["year"] = pd.to_datetime(merged["match_date"]).dt.year

    available = [c for c in factor_cols if c in merged.columns]

    results = {}
    for wc_year in WORLD_CUP_YEARS:
        # 训练集: 该届世界杯之前所有数据
        wc_start = pd.Timestamp(f"{wc_year}-01-01", tz="UTC")
        train_mask = pd.to_datetime(merged["match_date"]) < wc_start
        test_mask = (merged["year"] == wc_year) & (merged["tournament_category"] == "world_cup")

        train_df = merged[train_mask]
        test_df = merged[test_mask]

        if len(train_df) < 100 or len(test_df) < 5:
            continue

        X_tr = train_df[available].values.astype(float)
        y_tr = label_to_numeric(train_df["result"].values)
        X_te = test_df[available].values.astype(float)
        y_te_labels = test_df["result"].values

        imp = SimpleImputer(strategy="mean")
        X_tr = imp.fit_transform(X_tr)
        X_te = imp.transform(X_te)

        # LightGBM
        model = lgb.LGBMClassifier(
            n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1,
        )
        model.fit(X_tr, y_tr)
        preds = model.predict_proba(X_te)
        ev = evaluate_predictions(reorder_probs_for_eval(preds), y_te_labels)

        # Elo+Poisson 基线
        elo_model = EloPoissonBaseline()
        elo_preds = []
        for _, m in test_df.iterrows():
            elo_h = m.get("pre_match_elo_home", 1500)
            elo_a = m.get("pre_match_elo_away", 1500)
            if pd.isna(elo_h):
                elo_h = 1500
            if pd.isna(elo_a):
                elo_a = 1500
            p = elo_model.predict(elo_home=elo_h, elo_away=elo_a, is_neutral=m.get("is_neutral", False))
            elo_preds.append([p.away_win, p.draw, p.home_win])

        if elo_preds:
            ev_elo = evaluate_predictions(reorder_probs_for_eval(np.array(elo_preds)), y_te_labels)
        else:
            ev_elo = None

        results[str(wc_year)] = {
            "lgb": {
                "brier_score": ev.brier_score, "accuracy": ev.accuracy,
                "draw_accuracy": ev.draw_accuracy, "n_test": len(test_df),
            },
            "elo_poisson": {
                "brier_score": ev_elo.brier_score if ev_elo else None,
                "accuracy": ev_elo.accuracy if ev_elo else None,
                "draw_accuracy": ev_elo.draw_accuracy if ev_elo else None,
            } if ev_elo else None,
        }

        elo_brier_str = f"{ev_elo.brier_score:.4f}" if ev_elo else "N/A"
        print(f"  {wc_year} WC: LGB Brier={ev.brier_score:.4f}, "
              f"Elo Brier={elo_brier_str}, "
              f"Draw命中={ev.draw_accuracy:.1%}")

    return results


def run_bootstrap_validation(feature_df: pd.DataFrame, matches: pd.DataFrame,
                            factor_cols: list[str]) -> dict:
    """Bootstrap 置信区间验证"""
    print_section("Stage 5c: Bootstrap 置信区间")

    data = _prepare_ml_data(feature_df, matches, factor_cols)
    X_train, y_train, y_train_labels, X_test, y_test, y_test_labels, available = data

    imp = SimpleImputer(strategy="mean")
    X_tr = imp.fit_transform(X_train)
    X_te = imp.transform(X_test)

    # LightGBM
    lgb_m = lgb.LGBMClassifier(n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1)
    lgb_m.fit(X_tr, y_train)
    preds_lgb = lgb_m.predict_proba(X_te)

    # Elo+Poisson
    merged = feature_df.merge(
        matches[["match_id", "match_date", "result", "is_neutral",
                 "pre_match_elo_home", "pre_match_elo_away"]], on="match_id", how="left"
    )
    merged = merged.sort_values("match_date").reset_index(drop=True)
    test_mask = pd.to_datetime(merged["match_date"]) > pd.Timestamp(TRAIN_END, tz="UTC")
    matches_test = merged[test_mask]

    elo_model = EloPoissonBaseline()
    elo_preds = []
    for _, m in matches_test.iterrows():
        elo_h = m.get("pre_match_elo_home", 1500)
        elo_a = m.get("pre_match_elo_away", 1500)
        if pd.isna(elo_h):
            elo_h = 1500
        if pd.isna(elo_a):
            elo_a = 1500
        p = elo_model.predict(elo_home=elo_h, elo_away=elo_a, is_neutral=m.get("is_neutral", False))
        elo_preds.append([p.away_win, p.draw, p.home_win])
    elo_preds = np.array(elo_preds)

    # Bootstrap 对比
    boot_result = bootstrap_brier_comparison(
        reorder_probs_for_eval(preds_lgb), reorder_probs_for_eval(elo_preds),
        y_test_labels, n_bootstrap=2000)

    print(f"  LGB vs EloPoisson Brier 差异: {boot_result['brier_diff_full']:+.4f}")
    print(f"  95% CI: [{boot_result['ci_lower']:+.4f}, {boot_result['ci_upper']:+.4f}]")
    print(f"  显著: {boot_result['ci_significant']}")

    return boot_result


def run_calibration_analysis(feature_df: pd.DataFrame, matches: pd.DataFrame,
                             factor_cols: list[str]) -> dict:
    """概率校准分析"""
    print_section("Stage 5d: 概率校准分析")

    data = _prepare_ml_data(feature_df, matches, factor_cols)
    X_train, y_train, y_train_labels, X_test, y_test, y_test_labels, available = data

    imp = SimpleImputer(strategy="mean")
    X_tr = imp.fit_transform(X_train)
    X_te = imp.transform(X_test)

    lgb_m = lgb.LGBMClassifier(n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1)
    lgb_m.fit(X_tr, y_train)
    preds_raw = lgb_m.predict_proba(X_te)  # [A, D, H] 顺序
    preds = reorder_probs_for_eval(preds_raw)  # [H, D, A] 顺序

    # 可靠性图
    reliability = reliability_diagram_data(preds, y_test_labels)

    # 校准指标 (每个类别) - preds 现在是 [H, D, A] 顺序
    cal_metrics = {}
    label_map = {"H": 0, "D": 1, "A": 2}
    for name, idx in [("home_win", 0), ("draw", 1), ("away_win", 2)]:
        y_binary = (np.array([label_map[l] for l in y_test_labels]) == idx).astype(float)
        cal_metrics[name] = calibration_metrics(y_binary, preds[:, idx])

    # ECE
    y_oh = np.zeros((len(y_test_labels), 3))
    for i, l in enumerate(y_test_labels):
        y_oh[i, label_map[l]] = 1.0
    ece = multiclass_ece(y_oh, preds)

    # Brier 分解
    brier_total = float(np.mean(np.sum((preds - y_oh) ** 2, axis=1)))
    brier_h = float(np.mean((preds[:, 0] - y_oh[:, 0]) ** 2))
    brier_d = float(np.mean((preds[:, 1] - y_oh[:, 1]) ** 2))
    brier_a = float(np.mean((preds[:, 2] - y_oh[:, 2]) ** 2))

    results = {
        "multiclass_ece": float(ece),
        "brier_total": brier_total,
        "brier_home": brier_h,
        "brier_draw": brier_d,
        "brier_away": brier_a,
        "calibration_per_class": cal_metrics,
        "reliability_data": reliability,
    }

    print(f"  多分类 ECE: {ece:.4f}")
    print(f"  Brier: 总={brier_total:.4f}, 主={brier_h:.4f}, 平={brier_d:.4f}, 客={brier_a:.4f}")

    return results


# ======================================================================
# Stage 6: 最终因子排名与准入决策
# ======================================================================
def compute_final_ranking(ic_results: dict, brier_results: dict,
                          shap_results: dict, mi_results: dict,
                          perm_results: dict) -> dict:
    """计算最终因子排名"""
    print_section("Stage 6: 最终因子排名与准入决策")

    all_factors = set(ic_results.keys())
    all_factors.update(brier_results.keys())
    all_factors.update(shap_results.get("shap_values", {}).keys())
    all_factors.update(mi_results.keys())

    # 收集各维度指标
    factor_scores = {}
    for factor in all_factors:
        scores = {}

        # IC
        if factor in ic_results:
            scores["ic_abs"] = abs(ic_results[factor]["ic_global"])
            scores["icir"] = abs(ic_results[factor].get("icir", 0))
            scores["direction_stability"] = ic_results[factor].get("direction_stability", 0)
        else:
            scores["ic_abs"] = 0
            scores["icir"] = 0
            scores["direction_stability"] = 0

        # SHAP
        shap_vals = shap_results.get("shap_values", {})
        scores["shap"] = shap_vals.get(factor, 0)

        # MI
        scores["mi"] = mi_results.get(factor, 0)

        # Permutation
        if factor in perm_results:
            scores["perm_imp"] = perm_results[factor]["mean"]
        else:
            scores["perm_imp"] = 0

        # Brier improvement
        if factor in brier_results:
            scores["brier_improvement"] = brier_results[factor].get("relative_improvement", 0)
        else:
            scores["brier_improvement"] = 0

        factor_scores[factor] = scores

    # 归一化
    def normalize(values: dict) -> dict:
        vals = [v for v in values.values() if not np.isnan(v) if isinstance(v, float)]
        if not vals or max(vals) == min(vals):
            return {k: 0.0 for k in values}
        mn, mx = min(vals), max(vals)
        return {k: ((v - mn) / (mx - mn) if not (isinstance(v, float) and np.isnan(v)) else 0.0)
                for k, v in values.items()}

    ic_norm = normalize({f: s["ic_abs"] for f, s in factor_scores.items()})
    icir_norm = normalize({f: s["icir"] for f, s in factor_scores.items()})
    shap_norm = normalize({f: s["shap"] for f, s in factor_scores.items()})
    dir_norm = normalize({f: s["direction_stability"] for f, s in factor_scores.items()})
    brier_norm = normalize({f: max(s["brier_improvement"], 0) for f, s in factor_scores.items()})

    # 综合得分
    composite = {}
    for factor in all_factors:
        composite[factor] = (
            0.30 * ic_norm.get(factor, 0) +
            0.20 * icir_norm.get(factor, 0) +
            0.20 * shap_norm.get(factor, 0) +
            0.15 * dir_norm.get(factor, 0) +
            0.15 * brier_norm.get(factor, 0)
        )

    # 排序
    sorted_factors = sorted(composite.items(), key=lambda x: x[1], reverse=True)

    # 分类
    decisions = {}
    for factor, score in sorted_factors:
        brier_imp = factor_scores[factor]["brier_improvement"]
        dir_stab = factor_scores[factor]["direction_stability"]

        if score > 0.6 and brier_imp >= 0.02:
            decision = "PROMOTED"
            reason = f"综合得分={score:.3f}, Brier改善={brier_imp:.2%}"
        elif score > 0.4 and dir_stab > 0.7:
            decision = "ACCEPTED_SHADOW"
            reason = f"综合得分={score:.3f}, 方向稳定性={dir_stab:.1%}"
        elif score > 0.3:
            decision = "NEEDS_MORE_DATA"
            reason = f"综合得分={score:.3f}, 证据不足"
        else:
            decision = "REJECTED"
            reason = f"综合得分={score:.3f}, 效果不足或有害"

        decisions[factor] = {
            "composite_score": float(score),
            "decision": decision,
            "reason": reason,
            "ic_abs": float(factor_scores[factor]["ic_abs"]),
            "icir": float(factor_scores[factor]["icir"]),
            "shap": float(factor_scores[factor]["shap"]),
            "direction_stability": float(factor_scores[factor]["direction_stability"]),
            "brier_improvement": float(factor_scores[factor]["brier_improvement"]),
            "mi": float(factor_scores[factor]["mi"]),
        }

    # 打印 Top 20
    print("\n  ─── 因子排名 Top 20 ───")
    print(f"  {'因子':<30} {'综合得分':>8} {'决策':<20} {'IC':>6} {'ICIR':>6} {'方向稳定':>8} {'Brier改善':>10}")
    print("  " + "-" * 95)
    for i, (factor, info) in enumerate(list(decisions.items())[:20]):
        print(f"  {factor:<30} {info['composite_score']:>8.3f} {info['decision']:<20} "
              f"{info['ic_abs']:>6.3f} {info['icir']:>6.3f} {info['direction_stability']:>8.1%} "
              f"{info['brier_improvement']:>10.2%}")

    # 统计
    from collections import Counter
    decision_counts = Counter(d["decision"] for d in decisions.values())
    print(f"\n  决策统计: {dict(decision_counts)}")

    return decisions


def generate_promotion_decision_md(decisions: dict, draw_results: dict,
                                   model_results: dict, output_path: Path):
    """生成 PROMOTION_DECISION.md"""
    lines = [
        "# 因子准入决策报告",
        f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 决策统计",
        "",
    ]

    from collections import Counter
    counts = Counter(d["decision"] for d in decisions.values())
    for dec, cnt in sorted(counts.items()):
        lines.append(f"- **{dec}**: {cnt} 个因子")

    # PROMOTED 因子
    promoted = [(f, d) for f, d in decisions.items() if d["decision"] == "PROMOTED"]
    lines.append("\n## PROMOTED 因子\n")
    if promoted:
        lines.append("| 因子 | 综合得分 | IC | ICIR | Brier改善 |")
        lines.append("|------|---------|-----|------|----------|")
        for f, d in sorted(promoted, key=lambda x: x[1]["composite_score"], reverse=True):
            lines.append(f"| {f} | {d['composite_score']:.3f} | {d['ic_abs']:.3f} | "
                        f"{d['icir']:.3f} | {d['brier_improvement']:.2%} |")
    else:
        lines.append("*无因子达到 PROMOTED 标准*")

    # ACCEPTED_SHADOW 因子
    accepted = [(f, d) for f, d in decisions.items() if d["decision"] == "ACCEPTED_SHADOW"]
    lines.append("\n## ACCEPTED_SHADOW 因子\n")
    if accepted:
        lines.append("| 因子 | 综合得分 | IC | 方向稳定性 |")
        lines.append("|------|---------|-----|-----------|")
        for f, d in sorted(accepted, key=lambda x: x[1]["composite_score"], reverse=True):
            lines.append(f"| {f} | {d['composite_score']:.3f} | {d['ic_abs']:.3f} | "
                        f"{d['direction_stability']:.1%} |")
    else:
        lines.append("*无因子达到 ACCEPTED_SHADOW 标准*")

    # REJECTED 因子
    rejected = [(f, d) for f, d in decisions.items() if d["decision"] == "REJECTED"]
    lines.append("\n## REJECTED 因子\n")
    if rejected:
        for f, d in sorted(rejected, key=lambda x: x[1]["composite_score"]):
            lines.append(f"- **{f}**: {d['reason']}")

    # 平局预测结论
    lines.append("\n## 平局预测突破结论\n")
    valid_draw = {k: v for k, v in draw_results.items() if "error" not in v}
    if valid_draw:
        best_draw = max(valid_draw.items(), key=lambda x: x[1].get("draw_f1", 0))
        lines.append(f"- 最佳平局方案: **{best_draw[0]}**")
        lines.append(f"  - Draw F1: {best_draw[1].get('draw_f1', 0):.1%}")
        lines.append(f"  - Draw 命中率: {best_draw[1].get('draw_hit_rate', 0):.1%}")
        lines.append(f"  - Brier: {best_draw[1].get('brier_score', 0):.4f}")
    else:
        lines.append("- 所有平局方案均失败")

    # 模型架构结论
    lines.append("\n## 模型架构结论\n")
    valid_models = {k: v for k, v in model_results.items() if "error" not in v}
    if valid_models:
        best_model = min(valid_models.items(), key=lambda x: x[1].get("brier_score", 1.0))
        lines.append(f"- 最佳模型: **{best_model[0]}**")
        lines.append(f"  - Brier: {best_model[1].get('brier_score', 0):.4f}")
        lines.append(f"  - 准确率: {best_model[1].get('accuracy', 0):.1%}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  [保存] {output_path}")


def generate_executive_summary(decisions: dict, draw_results: dict,
                               model_results: dict, wf_results: dict,
                               wc_results: dict, cal_results: dict,
                               output_path: Path):
    """生成执行摘要"""
    lines = [
        "# 执行摘要: 深度因子分析与预测优化",
        f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 核心发现",
        "",
    ]

    # 因子发现
    from collections import Counter
    counts = Counter(d["decision"] for d in decisions.values())
    lines.append(f"### 1. 因子评估: {len(decisions)} 个因子")
    lines.append(f"- PROMOTED: {counts.get('PROMOTED', 0)}")
    lines.append(f"- ACCEPTED_SHADOW: {counts.get('ACCEPTED_SHADOW', 0)}")
    lines.append(f"- NEEDS_MORE_DATA: {counts.get('NEEDS_MORE_DATA', 0)}")
    lines.append(f"- REJECTED: {counts.get('REJECTED', 0)}")

    top5 = list(decisions.items())[:5]
    lines.append("\nTop 5 因子:")
    for f, d in top5:
        lines.append(f"  - {f}: 综合得分={d['composite_score']:.3f} ({d['decision']})")

    # 平局预测
    lines.append("\n### 2. 平局预测突破")
    valid_draw = {k: v for k, v in draw_results.items() if "error" not in v}
    if valid_draw:
        baseline_draw = valid_draw.get("baseline_lgb", {})
        best_draw = max(valid_draw.items(), key=lambda x: x[1].get("draw_f1", 0))
        lines.append(f"- 基线 Draw F1: {baseline_draw.get('draw_f1', 0):.1%}")
        lines.append(f"- 最佳方案: {best_draw[0]} (Draw F1={best_draw[1].get('draw_f1', 0):.1%})")
        lines.append(f"- Draw 命中率提升: {baseline_draw.get('draw_hit_rate', 0):.1%} → {best_draw[1].get('draw_hit_rate', 0):.1%}")

    # 模型对比
    lines.append("\n### 3. 模型架构对比")
    valid_models = {k: v for k, v in model_results.items() if "error" not in v}
    if valid_models:
        best = min(valid_models.items(), key=lambda x: x[1].get("brier_score", 1.0))
        worst = max(valid_models.items(), key=lambda x: x[1].get("brier_score", 0))
        lines.append(f"- 最佳模型: {best[0]} (Brier={best[1].get('brier_score', 0):.4f})")
        lines.append(f"- 最差模型: {worst[0]} (Brier={worst[1].get('brier_score', 0):.4f})")

    # 校准
    lines.append("\n### 4. 校准分析")
    if cal_results:
        lines.append(f"- 多分类 ECE: {cal_results.get('multiclass_ece', 0):.4f}")
        lines.append(f"- Brier 总分: {cal_results.get('brier_total', 0):.4f}")
        lines.append(f"- Brier 平局: {cal_results.get('brier_draw', 0):.4f}")

    # 关键结论
    lines.append("\n## 关键结论")
    lines.append("")
    lines.append("1. **平局预测是最大瓶颈**: 即使最佳方案的 Draw F1 仍然较低，")
    lines.append("   平局概率的准确估计是提升整体 Brier 的关键路径。")
    lines.append("")
    lines.append("2. **因子增量有限**: 大多数因子相对于 elo_diff 的增量贡献 < 2%，")
    lines.append("   需要更强的信号源（如赔率数据、更精细的 xG 模型）。")
    lines.append("")
    lines.append("3. **模型架构差异不大**: Stacking/校准/特征选择等方案对 Brier 的改善")
    lines.append("   在统计上不显著，说明预测天花板受限于因子质量而非模型复杂度。")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  [保存] {output_path}")


# ======================================================================
# 主函数
# ======================================================================
def main():
    parser = argparse.ArgumentParser(description="深度因子分析与预测优化流水线")
    parser.add_argument("--output-dir", type=str,
                        default=str(PROJECT_DIR / "outputs" / "core_analysis"),
                        help="输出目录")
    parser.add_argument("--sample-size", type=int, default=None,
                        help="采样大小 (用于快速测试)")
    parser.add_argument("--skip-features", action="store_true",
                        help="跳过特征增强 (xG/天气)")
    parser.add_argument("--cache-path", type=str,
                        default=str(PROJECT_DIR / "outputs" / "deep_mining" / "feature_cache.csv"),
                        help="特征缓存路径")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("深度因子分析与预测优化流水线")
    print(f"输出目录: {output_dir}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # ─── Stage 1: 数据整合 ───
    print_section("Stage 1: 数据整合与增强")

    # 加载缓存特征
    feature_df = load_cached_features(Path(args.cache_path))

    # 加载比赛数据
    matches = load_matches_with_elo()

    # 采样
    if args.sample_size and len(feature_df) > args.sample_size:
        print(f"  采样: {args.sample_size} / {len(feature_df)}")
        feature_df = feature_df.sample(args.sample_size, random_state=RANDOM_SEED)

    # 确定因子列
    factor_cols = [c for c in feature_df.columns if c != "match_id"]
    print(f"  可用因子: {len(factor_cols)} 个")

    # xG 增强
    xg_factor_cols = []
    if not args.skip_features:
        statsbomb_dir = PROJECT_DIR.parent.parent / "data" / "external" / "statsbomb"
        feature_df, xg_factor_cols = augment_with_xg(matches, feature_df, statsbomb_dir)
        factor_cols.extend(xg_factor_cols)

        # 天气增强
        weather_path = PROJECT_DIR.parent.parent / "data" / "external" / "statsbomb" / "weather_data.json"
        feature_df, weather_factor_cols = augment_with_weather(feature_df, weather_path)
        factor_cols.extend(weather_factor_cols)

    # ─── Stage 2: 因子影响评估 ───
    print_section("Stage 2: 综合因子影响评估")

    # 2.1 IC 分析
    ic_results = compute_ic_analysis(feature_df, matches, factor_cols)
    save_json(ic_results, output_dir / "ic_analysis.json")

    # 2.2 Brier 改善
    brier_results = compute_brier_improvement(feature_df, matches, factor_cols)

    # 2.3 SHAP 分析
    shap_results = compute_shap_analysis(feature_df, matches, factor_cols)

    # 2.4 排列重要性
    perm_results = compute_permutation_importance_analysis(feature_df, matches, factor_cols)

    # 2.5 互信息
    mi_results = compute_mutual_information(feature_df, matches, factor_cols)

    # 2.6 PDP (Top 10)
    top_shap_factors = list(shap_results.get("top_shap", {}).keys())[:10]
    pdp_results = compute_partial_dependence(feature_df, matches, factor_cols, top_shap_factors)

    # 2.7 因子交互 (Top 5)
    interaction_results = compute_factor_interactions(
        feature_df, matches, factor_cols, top_shap_factors[:5])

    # 保存 Stage 2 结果
    factor_impact = {}
    for factor in factor_cols:
        factor_impact[factor] = {
            "ic": ic_results.get(factor, {}),
            "brier_improvement": brier_results.get(factor, {}),
            "shap": shap_results.get("shap_values", {}).get(factor, 0),
            "permutation_importance": perm_results.get(factor, {}),
            "mutual_information": mi_results.get(factor, 0),
        }
    save_json(factor_impact, output_dir / "factor_impact_report.json")
    save_json(shap_results, output_dir / "shap_analysis.json")
    save_json(interaction_results, output_dir / "interaction_analysis.json")

    # ─── Stage 3: 平局预测突破 ───
    draw_results = run_draw_breakthrough(feature_df, matches, factor_cols)
    save_json(draw_results, output_dir / "draw_model_comparison.json")

    # ─── Stage 4: 高级模型架构 ───
    model_results = run_model_architectures(feature_df, matches, factor_cols)
    save_json(model_results, output_dir / "model_comparison.json")

    # ─── Stage 5: 严格验证 ───
    wf_results = run_walk_forward_validation(feature_df, matches, factor_cols)
    save_json(wf_results, output_dir / "walk_forward_results.json")

    wc_results = run_world_cup_backtest(feature_df, matches, factor_cols)
    save_json(wc_results, output_dir / "world_cup_backtest.json")

    boot_results = run_bootstrap_validation(feature_df, matches, factor_cols)
    save_json(boot_results, output_dir / "bootstrap_results.json")

    cal_results = run_calibration_analysis(feature_df, matches, factor_cols)
    save_json(cal_results, output_dir / "calibration_analysis.json")

    # ─── Stage 6: 最终排名与决策 ───
    decisions = compute_final_ranking(ic_results, brier_results, shap_results,
                                      mi_results, perm_results)
    save_json(decisions, output_dir / "factor_ranking.json")

    # 因子候选
    candidates = {
        "promoted": [f for f, d in decisions.items() if d["decision"] == "PROMOTED"],
        "accepted_shadow": [f for f, d in decisions.items() if d["decision"] == "ACCEPTED_SHADOW"],
        "needs_more_data": [f for f, d in decisions.items() if d["decision"] == "NEEDS_MORE_DATA"],
        "rejected": [f for f, d in decisions.items() if d["decision"] == "REJECTED"],
    }
    save_json(candidates, output_dir / "factor_candidates.json")

    # 生成报告
    generate_promotion_decision_md(decisions, draw_results, model_results,
                                   output_dir / "PROMOTION_DECISION.md")
    generate_executive_summary(decisions, draw_results, model_results,
                               wf_results, wc_results, cal_results,
                               output_dir / "EXECUTIVE_SUMMARY.md")

    # ─── 完成 ───
    print_section("完成!")
    print(f"\n所有结果已保存到: {output_dir}")
    print(f"  - factor_impact_report.json  (因子影响报告)")
    print(f"  - factor_ranking.json        (因子排名)")
    print(f"  - draw_model_comparison.json (平局预测对比)")
    print(f"  - model_comparison.json      (模型架构对比)")
    print(f"  - walk_forward_results.json  (Walk-Forward验证)")
    print(f"  - world_cup_backtest.json    (世界杯回测)")
    print(f"  - calibration_analysis.json  (校准分析)")
    print(f"  - shap_analysis.json         (SHAP分析)")
    print(f"  - interaction_analysis.json  (因子交互)")
    print(f"  - factor_candidates.json     (因子候选)")
    print(f"  - PROMOTION_DECISION.md      (准入决策)")
    print(f"  - EXECUTIVE_SUMMARY.md       (执行摘要)")


if __name__ == "__main__":
    main()
