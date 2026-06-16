#!/usr/bin/env python3
"""第二轮验证脚本 - World Cup Factor Research Demo

严格遵循时间边界和 as_of 原则，对新候选因子进行全面验证。

使用方法:
    cd research/factor_demo
    python scripts/run_round2.py
"""

from __future__ import annotations

import copy
import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# 添加项目路径
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import load_international_results, filter_by_date
from src.features.as_of import compute_all_features
from src.features.calculator import FACTOR_FUNCTIONS
from src.models.baseline import EloLogisticBaseline, EloPoissonBaseline, Prediction
from src.evaluation.metrics import evaluate_predictions
from src.evaluation.calibration import reliability_diagram_data
from src.utils.elo_replay import replay_elo_history, EloConfig

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

# ============================================================
# 常量定义
# ============================================================

OUTPUT_DIR = PROJECT_DIR / "outputs" / "round2"

# 时间边界
DATA_START = "2000-01-01"
TRAIN_START = "2010-01-01"
TRAIN_END = "2018-12-31"
VAL_START = "2019-01-01"
VAL_END = "2025-12-31"
BLIND_START = "2026-01-01"

# 因子分类
BASELINE_FACTORS = ["elo_diff"]
NEW_CANDIDATE_FACTORS = [
    "recent_form_5", "recent_form_10", "recent_form_5_opp_adjusted",
    "recent_goals_scored_5", "recent_goals_conceded_5", "recent_goal_diff_5",
    "attack_strength", "defense_strength", "official_vs_friendly",
    "home_away_neutral_form", "rest_days", "match_density_30d",
    "match_density_90d", "tournament_experience", "knockout_experience",
    "inter_confederation_form", "host_advantage", "h2h_last_5",
]
SKIP_FACTORS = ["fifa_rank_diff", "odds_implied_prob", "odds_movement"]

ALL_MODEL_FACTORS = BASELINE_FACTORS + NEW_CANDIDATE_FACTORS

# Bootstrap 配置
N_BOOTSTRAP = 1000
RANDOM_SEED = 42


# ============================================================
# 工具函数
# ============================================================

def ensure_output_dir():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def save_json(data, filename):
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=_json_default)
    print(f"  已保存: {path}")


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


def eval_to_dict(ev) -> dict:
    return {
        "brier_score": ev.brier_score,
        "brier_home": ev.brier_home,
        "brier_draw": ev.brier_draw,
        "brier_away": ev.brier_away,
        "log_loss": ev.log_loss,
        "accuracy": ev.accuracy,
        "n_samples": ev.n_samples,
        "home_win_accuracy": ev.home_win_accuracy,
        "draw_accuracy": ev.draw_accuracy,
        "away_win_accuracy": ev.away_win_accuracy,
        "ece": ev.ece,
    }


def predict_elo_baseline(model, df_slice):
    """用 Elo 基线模型预测一批比赛。跳过无 Elo 数据的比赛。"""
    preds = []
    valid_indices = []
    for idx, m in df_slice.iterrows():
        elo_h = m.get("pre_match_elo_home", None)
        elo_a = m.get("pre_match_elo_away", None)
        if elo_h is None or elo_a is None or pd.isna(elo_h) or pd.isna(elo_a):
            continue
        p = model.predict(
            elo_home=elo_h,
            elo_away=elo_a,
            is_neutral=m.get("is_neutral", False),
        )
        preds.append([p.home_win, p.draw, p.away_win])
        valid_indices.append(idx)
    return np.array(preds), valid_indices


def merge_features(df_slice, feature_df):
    """将特征 DataFrame 合并回比赛数据。

    合并后，冲突列（如 elo_diff）会有 _match 和 _feat 后缀，
    非冲突列（如 recent_form_5）保持原名。
    统一返回 (merged_df, feature_col_names)，其中 feature_col_names
    指向合并后 DataFrame 中实际存在的特征列。
    """
    merged = df_slice.merge(feature_df, on="match_id", how="left", suffixes=("_match", "_feat"))

    # 对于注入因子（elo_diff等），_feat 版本可能全为 None，用 _match 版本替换
    for c in feature_df.columns:
        if c == "match_id":
            continue
        feat_col = f"{c}_feat"
        match_col = f"{c}_match"
        if feat_col in merged.columns and match_col in merged.columns:
            if merged[feat_col].isna().all() and not merged[match_col].isna().all():
                merged[feat_col] = merged[match_col]

    # 构建特征列名列表：优先使用 _feat 后缀版本，否则使用原名
    feature_cols = []
    for c in feature_df.columns:
        if c == "match_id":
            continue
        feat_col = f"{c}_feat"
        if feat_col in merged.columns:
            feature_cols.append(feat_col)
        elif c in merged.columns:
            feature_cols.append(c)
    return merged, feature_cols


def prepare_lr_data(merged, feature_cols, factor_names=None):
    """准备逻辑回归所需的 X, y。

    factor_names 是逻辑因子名（如 'elo_diff'），需要映射到合并后的实际列名。
    """
    if factor_names is not None:
        cols = []
        for fn in factor_names:
            feat_col = f"{fn}_feat"
            if feat_col in merged.columns:
                cols.append(feat_col)
            elif fn in merged.columns:
                cols.append(fn)
    else:
        cols = feature_cols

    X = merged[cols].fillna(0).values
    y = merged["result"].map({"H": 0, "D": 1, "A": 2}).values
    return X, y, cols


def train_lr_model(X_train, y_train, C=1.0):
    """训练逻辑回归模型。"""
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_train)
    lr = LogisticRegression(max_iter=2000, C=C, solver="lbfgs")
    lr.fit(X_scaled, y_train)
    return lr, scaler


def predict_lr(lr, scaler, X):
    """用逻辑回归模型预测。"""
    X_scaled = scaler.transform(X)
    return lr.predict_proba(X_scaled)


# ============================================================
# 步骤 1: 数据加载与 Elo 计算
# ============================================================

def step1_load_data():
    print("\n" + "=" * 70)
    print("步骤 1: 数据加载与 Elo 计算")
    print("=" * 70)

    print("  加载历史比赛数据...")
    df = load_international_results()
    print(f"  全量数据: {len(df)} 场比赛")

    # 过滤到数据起始点
    df = filter_by_date(df, DATA_START, "2026-12-31")
    print(f"  {DATA_START} ~ 2026-12-31: {len(df)} 场比赛")

    # 计算 Elo
    print("  计算 Elo 历史...")
    df = replay_elo_history(df, EloConfig())
    print(f"  Elo 计算完成, elo_diff 范围: [{df['elo_diff'].min():.0f}, {df['elo_diff'].max():.0f}]")

    # 时间划分
    train = filter_by_date(df, TRAIN_START, TRAIN_END)
    val = filter_by_date(df, VAL_START, VAL_END)
    blind = filter_by_date(df, BLIND_START, "2026-12-31")

    print(f"  训练集 ({TRAIN_START} ~ {TRAIN_END}): {len(train)} 场")
    print(f"  验证集 ({VAL_START} ~ {VAL_END}): {len(val)} 场")
    print(f"  盲测集 ({BLIND_START} ~ 2026-12-31): {len(blind)} 场")

    # 2026 数据概况
    if len(blind) > 0:
        tc = blind["tournament_category"].value_counts().to_dict()
        print(f"  2026 赛事分布: {tc}")

    return df, train, val, blind


# ============================================================
# 步骤 2: 特征计算
# ============================================================

def step2_compute_features(df, train, val, blind):
    print("\n" + "=" * 70)
    print("步骤 2: 特征计算")
    print("=" * 70)

    # 构建因子函数字典（排除 skip 因子）
    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}

    print(f"  活跃因子数: {len(active_factors)} (排除 {len(SKIP_FACTORS)} 个零覆盖率因子)")

    # 计算训练集特征
    print("  计算训练集特征...")
    train_features = compute_all_features(train, df, active_factors, show_progress=True)
    print(f"  训练集特征计算完成: {train_features.shape}")

    # 计算验证集特征
    print("  计算验证集特征...")
    val_features = compute_all_features(val, df, active_factors, show_progress=True)
    print(f"  验证集特征计算完成: {val_features.shape}")

    # 计算盲测集特征
    print("  计算盲测集特征...")
    blind_features = compute_all_features(blind, df, active_factors, show_progress=True)
    print(f"  盲测集特征计算完成: {blind_features.shape}")

    # 覆盖率报告
    print("\n  因子覆盖率:")
    coverage_report = {}
    for col in train_features.columns:
        if col == "match_id":
            continue
        train_cov = train_features[col].notna().mean()
        val_cov = val_features[col].notna().mean()
        blind_cov = blind_features[col].notna().mean() if len(blind) > 0 else 0.0
        coverage_report[col] = {
            "train": float(train_cov),
            "val": float(val_cov),
            "blind": float(blind_cov),
        }
        print(f"    {col}: train={train_cov:.1%}, val={val_cov:.1%}, blind={blind_cov:.1%}")

    return train_features, val_features, blind_features, coverage_report


# ============================================================
# 步骤 3: 统一模型对比
# ============================================================

def step3_unified_comparison(df, train, val, blind, train_features, val_features, blind_features):
    print("\n" + "=" * 70)
    print("步骤 3: 统一模型对比")
    print("=" * 70)

    # 合并特征
    train_merged, all_feature_cols = merge_features(train, train_features)
    val_merged, _ = merge_features(val, val_features)
    blind_merged, _ = merge_features(blind, blind_features) if len(blind) > 0 else (blind, [])

    # 构建不同因子组合的列名
    baseline_cols = []
    for fn in BASELINE_FACTORS:
        feat_col = f"{fn}_feat"
        if feat_col in train_merged.columns:
            baseline_cols.append(feat_col)
        elif fn in train_merged.columns:
            baseline_cols.append(fn)

    new_cols = []
    for fn in NEW_CANDIDATE_FACTORS:
        feat_col = f"{fn}_feat"
        if feat_col in train_merged.columns:
            new_cols.append(feat_col)
        elif fn in train_merged.columns:
            new_cols.append(fn)

    full_cols = baseline_cols + new_cols

    # 准备训练数据
    y_train = train_merged["result"].map({"H": 0, "D": 1, "A": 2}).values
    y_val = val_merged["result"].map({"H": 0, "D": 1, "A": 2}).values
    y_blind = blind_merged["result"].map({"H": 0, "D": 1, "A": 2}).values if len(blind_merged) > 0 else np.array([])

    val_labels = val_merged["result"].values
    blind_labels = blind_merged["result"].values if len(blind_merged) > 0 else np.array([])

    # 过滤掉没有 Elo 数据的比赛，确保所有模型在相同样本上比较
    val_has_elo = val_merged["pre_match_elo_home"].notna() & val_merged["pre_match_elo_away"].notna()
    val_eval = val_merged[val_has_elo]
    val_labels = val_eval["result"].values
    
    blind_has_elo = blind_merged["pre_match_elo_home"].notna() & blind_merged["pre_match_elo_away"].notna() if len(blind_merged) > 0 else pd.Series(dtype=bool)
    blind_eval = blind_merged[blind_has_elo] if len(blind_merged) > 0 else blind_merged
    blind_labels = blind_eval["result"].values if len(blind_eval) > 0 else np.array([])

    # ---- 模型 1: EloPoisson (baseline) ----
    print("  训练/评估 EloPoisson (baseline)...")
    elo_poisson = EloPoissonBaseline()
    val_preds_ep, _ = predict_elo_baseline(elo_poisson, val_eval)
    blind_preds_ep, _ = predict_elo_baseline(elo_poisson, blind_eval) if len(blind_eval) > 0 else (np.array([]), [])

    # ---- 模型 2: EloLogistic (baseline) ----
    print("  训练/评估 EloLogistic (baseline)...")
    elo_logistic = EloLogisticBaseline()
    val_preds_el, _ = predict_elo_baseline(elo_logistic, val_eval)
    blind_preds_el, _ = predict_elo_baseline(elo_logistic, blind_eval) if len(blind_eval) > 0 else (np.array([]), [])

    # ---- 模型 3: LR(elo_diff only) ----
    print("  训练/评估 LR(elo_diff only)...")
    X_train_base = train_merged[baseline_cols].fillna(0).values
    X_val_base = val_eval[baseline_cols].fillna(0).values
    lr_base, scaler_base = train_lr_model(X_train_base, y_train)
    val_preds_lr_base = predict_lr(lr_base, scaler_base, X_val_base)
    blind_preds_lr_base = predict_lr(lr_base, scaler_base,
                                      blind_eval[baseline_cols].fillna(0).values) if len(blind_eval) > 0 else np.array([])

    # ---- 模型 4: LR(new factors only) ----
    print("  训练/评估 LR(new factors only)...")
    X_train_new = train_merged[new_cols].fillna(0).values
    X_val_new = val_eval[new_cols].fillna(0).values
    lr_new, scaler_new = train_lr_model(X_train_new, y_train)
    val_preds_lr_new = predict_lr(lr_new, scaler_new, X_val_new)
    blind_preds_lr_new = predict_lr(lr_new, scaler_new,
                                     blind_eval[new_cols].fillna(0).values) if len(blind_eval) > 0 else np.array([])

    # ---- 模型 5: LR(elo_diff + new factors) ----
    print("  训练/评估 LR(elo_diff + new factors)...")
    X_train_full = train_merged[full_cols].fillna(0).values
    X_val_full = val_eval[full_cols].fillna(0).values
    lr_full, scaler_full = train_lr_model(X_train_full, y_train)
    val_preds_lr_full = predict_lr(lr_full, scaler_full, X_val_full)
    blind_preds_lr_full = predict_lr(lr_full, scaler_full,
                                      blind_eval[full_cols].fillna(0).values) if len(blind_eval) > 0 else np.array([])

    # 评估
    model_results = {}
    model_preds = {
        "EloPoisson": (val_preds_ep, blind_preds_ep),
        "EloLogistic": (val_preds_el, blind_preds_el),
        "LR_elo_only": (val_preds_lr_base, blind_preds_lr_base),
        "LR_new_only": (val_preds_lr_new, blind_preds_lr_new),
        "LR_full": (val_preds_lr_full, blind_preds_lr_full),
    }

    for name, (vp, bp) in model_preds.items():
        val_ev = evaluate_predictions(vp, val_labels)
        result = {"validation": eval_to_dict(val_ev)}
        if len(blind_eval) > 0 and len(bp) > 0:
            blind_ev = evaluate_predictions(bp, blind_labels)
            result["blind_test"] = eval_to_dict(blind_ev)
        model_results[name] = result
        val_brier = val_ev.brier_score
        blind_brier = result.get("blind_test", {}).get("brier_score", "N/A")
        print(f"    {name}: Val Brier={val_brier:.4f}, Blind Brier={blind_brier}")

    # 保存
    save_json(model_results, "unified_comparison_table.json")

    # 返回模型和数据供后续步骤使用
    return {
        "model_results": model_results,
        "model_preds": model_preds,
        "train_merged": train_merged,
        "val_merged": val_eval,  # 使用过滤后的版本
        "blind_merged": blind_eval,  # 使用过滤后的版本
        "all_feature_cols": all_feature_cols,
        "baseline_cols": baseline_cols,
        "new_cols": new_cols,
        "full_cols": full_cols,
        "y_train": y_train,
        "y_val": y_val,
        "val_labels": val_labels,
        "blind_labels": blind_labels,
        "lr_full": lr_full,
        "scaler_full": scaler_full,
    }


# ============================================================
# 步骤 4: Bootstrap 置信区间
# ============================================================

def step4_bootstrap(model_data):
    print("\n" + "=" * 70)
    print("步骤 4: Bootstrap 置信区间")
    print("=" * 70)

    val_labels = model_data["val_labels"]
    model_preds = model_data["model_preds"]
    baseline_preds = model_preds["EloPoisson"][0]  # validation predictions

    rng = np.random.RandomState(RANDOM_SEED)
    n = len(val_labels)

    # 计算 baseline Brier
    label_map = {"H": 0, "D": 1, "A": 2}
    y_true_onehot = np.zeros((n, 3))
    for i, label in enumerate(val_labels):
        y_true_onehot[i, label_map[label]] = 1.0

    baseline_briers = np.sum((baseline_preds - y_true_onehot) ** 2, axis=1)

    bootstrap_results = {}

    for name, (vp, _) in model_preds.items():
        if name == "EloPoisson":
            continue
        model_briers = np.sum((vp - y_true_onehot) ** 2, axis=1)
        diffs = baseline_briers - model_briers  # positive = model better

        boot_diffs = []
        for _ in range(N_BOOTSTRAP):
            idx = rng.randint(0, n, size=n)
            boot_diffs.append(np.mean(diffs[idx]))

        boot_diffs = np.array(boot_diffs)
        mean_diff = float(np.mean(boot_diffs))
        ci_low = float(np.percentile(boot_diffs, 2.5))
        ci_high = float(np.percentile(boot_diffs, 97.5))
        significant = not (ci_low <= 0 <= ci_high)

        interpretation = "显著改善" if (significant and mean_diff > 0) else ("显著退化" if (significant and mean_diff < 0) else "不显著")
        bootstrap_results[name] = {
            "mean_brier_diff": mean_diff,
            "ci_95_low": ci_low,
            "ci_95_high": ci_high,
            "significant": significant,
            "n_bootstrap": N_BOOTSTRAP,
            "direction": "better" if mean_diff > 0 else "worse",
            "interpretation": f"{interpretation} (ΔBrier={mean_diff:+.5f}, CI: [{ci_low:.5f}, {ci_high:.5f}])",
        }
        direction_str = "优于" if mean_diff > 0 else "劣于"
        print(f"    {name}: ΔBrier={mean_diff:+.5f}, 95%CI=[{ci_low:.5f}, {ci_high:.5f}], {direction_str}EP, {interpretation}")

    save_json(bootstrap_results, "bootstrap_results.json")
    return bootstrap_results


# ============================================================
# 步骤 5: 嵌套 Walk-Forward 验证
# ============================================================

def step5_walk_forward(df):
    """嵌套 Walk-Forward 验证（简化版，对比 EloPoisson vs LR_full）。"""
    print("\n" + "=" * 70)
    print("步骤 5: 嵌套 Walk-Forward 验证")
    print("=" * 70)

    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}
    wf_results = []

    for outer_train_end_year in range(2018, 2026):
        outer_train_end = f"{outer_train_end_year}-12-31"
        outer_test_start = f"{outer_train_end_year + 1}-01-01"
        outer_test_end = f"{outer_train_end_year + 1}-12-31"

        test = filter_by_date(df, outer_test_start, outer_test_end)
        test_elo = test[test["pre_match_elo_home"].notna() & test["pre_match_elo_away"].notna()]
        if len(test_elo) < 10:
            continue

        # EloPoisson baseline
        elo_poisson = EloPoissonBaseline()
        ep_preds, _ = predict_elo_baseline(elo_poisson, test_elo)
        ep_ev = evaluate_predictions(ep_preds, test_elo["result"].values)

        # LR_full（抽样加速）
        sample_size = min(1500, len(test_elo))
        test_sample = test_elo.sample(sample_size, random_state=42) if len(test_elo) > sample_size else test_elo

        try:
            train_all = filter_by_date(df, TRAIN_START, outer_train_end)
            train_feat = compute_all_features(train_all, df, active_factors, show_progress=False)
            train_m, fcols = merge_features(train_all, train_feat)

            test_feat = compute_all_features(test_sample, df, active_factors, show_progress=False)
            test_m, _ = merge_features(test_sample, test_feat)

            valid_cols = [c for c in fcols if c in train_m.columns and c in test_m.columns]
            y_train = train_m["result"].map({"H": 0, "D": 1, "A": 2}).values

            lr, sc = train_lr_model(train_m[valid_cols].fillna(0).values, y_train, C=1.0)
            lr_preds = predict_lr(lr, sc, test_m[valid_cols].fillna(0).values)
            lr_ev = evaluate_predictions(lr_preds, test_m["result"].values)

            delta = ep_ev.brier_score - lr_ev.brier_score
            wf_results.append({
                "test_period": f"{outer_test_start}~{outer_test_end}",
                "n_test": len(test_elo),
                "n_sample": len(test_sample),
                "EloPoisson_brier": ep_ev.brier_score,
                "LR_full_brier": lr_ev.brier_score,
                "delta_brier": delta,
                "EloPoisson_logloss": ep_ev.log_loss,
                "LR_full_logloss": lr_ev.log_loss,
                "EloPoisson_acc": ep_ev.accuracy,
                "LR_full_acc": lr_ev.accuracy,
                "EloPoisson_ece": ep_ev.ece,
                "LR_full_ece": lr_ev.ece,
            })
            print(f"    {outer_test_start}~{outer_test_end}: N={len(test_elo)}, "
                  f"EP={ep_ev.brier_score:.4f}, LR={lr_ev.brier_score:.4f}, "
                  f"Δ={delta:+.4f} ({'✓' if delta > 0 else '✗'})")
        except Exception as e:
            print(f"    {outer_test_start}~{outer_test_end}: 失败 - {e}")

    valid_results = [r for r in wf_results if "error" not in r]
    if valid_results:
        avg_delta = np.mean([r["delta_brier"] for r in valid_results])
        n_positive = sum(1 for r in valid_results if r["delta_brier"] > 0)
        print(f"\n  Walk-Forward 汇总: {len(valid_results)} 个窗口, "
              f"平均 ΔBrier={avg_delta:+.4f}, "
              f"LR优于EP的窗口: {n_positive}/{len(valid_results)}")

    save_json(wf_results, "walk_forward_results.json")
    return wf_results


# ============================================================
# 步骤 6: 分层评估
# ============================================================

def step6_stratified(model_data):
    print("\n" + "=" * 70)
    print("步骤 6: 分层评估")
    print("=" * 70)

    val_merged = model_data["val_merged"]
    blind_merged = model_data["blind_merged"]
    val_labels = model_data["val_labels"]
    blind_labels = model_data["blind_labels"]

    # 使用 EloPoisson 和 LR_full 的预测
    ep_val = model_data["model_preds"]["EloPoisson"][0]
    lr_val = model_data["model_preds"]["LR_full"][0]
    ep_blind = model_data["model_preds"]["EloPoisson"][1] if len(blind_merged) > 0 else np.array([])
    lr_blind = model_data["model_preds"]["LR_full"][1] if len(blind_merged) > 0 else np.array([])

    stratified_results = {}

    def stratify(df_slice, labels, preds_ep, preds_lr, prefix):
        if len(df_slice) < 5 or len(preds_ep) == 0:
            return
        try:
            ep_ev = evaluate_predictions(preds_ep, labels)
            lr_ev = evaluate_predictions(preds_lr, labels)
            stratified_results[prefix] = {
                "n_samples": len(df_slice),
                "EloPoisson": eval_to_dict(ep_ev),
                "LR_full": eval_to_dict(lr_ev),
            }
        except Exception:
            pass

    # --- 验证集分层 ---
    print("  验证集分层评估...")

    # 按年份
    val_merged["_year"] = val_merged["match_date"].dt.year
    for year, group in val_merged.groupby("_year"):
        mask = val_merged["_year"] == year
        stratify(group, val_labels[mask], ep_val[mask], lr_val[mask], f"val/year/{year}")

    # 按赛事类型
    for cat, group in val_merged.groupby("tournament_category"):
        mask = val_merged["tournament_category"] == cat
        stratify(group, val_labels[mask], ep_val[mask], lr_val[mask], f"val/tournament/{cat}")

    # 正式 vs 友谊赛
    for is_off, group in val_merged.groupby("is_official"):
        mask = val_merged["is_official"] == is_off
        label = "official" if is_off else "friendly"
        stratify(group, val_labels[mask], ep_val[mask], lr_val[mask], f"val/official_vs_friendly/{label}")

    # 中立 vs 非中立
    for is_neut, group in val_merged.groupby("is_neutral"):
        mask = val_merged["is_neutral"] == is_neut
        label = "neutral" if is_neut else "non_neutral"
        stratify(group, val_labels[mask], ep_val[mask], lr_val[mask], f"val/venue/{label}")

    # 强弱差距 - 合并后 elo_diff 变为 elo_diff_match 或 elo_diff_feat
    elo_diff_col = None
    for candidate in ["elo_diff_match", "elo_diff_feat", "elo_diff"]:
        if candidate in val_merged.columns:
            elo_diff_col = candidate
            break
    if elo_diff_col is not None:
        elo_diff_abs = val_merged[elo_diff_col].abs()
    else:
        elo_diff_abs = pd.Series([0] * len(val_merged), index=val_merged.index)

    strong_weak_mask = elo_diff_abs > 200
    close_match_mask = elo_diff_abs <= 100
    stratify(val_merged[strong_weak_mask], val_labels[strong_weak_mask],
             ep_val[strong_weak_mask], lr_val[strong_weak_mask], "val/gap/strong_weak")
    stratify(val_merged[close_match_mask], val_labels[close_match_mask],
             ep_val[close_match_mask], lr_val[close_match_mask], "val/gap/close_match")

    # 世界杯 + 世界杯预选赛
    wc_mask = val_merged["tournament_category"].isin(["world_cup", "qualification"])
    # 进一步筛选：仅世界杯预选赛（包含 "World Cup" 的 qualification）
    wc_only_mask = val_merged["tournament_category"] == "world_cup"
    stratify(val_merged[wc_mask], val_labels[wc_mask],
             ep_val[wc_mask], lr_val[wc_mask], "val/world_cup_plus_qualification")
    stratify(val_merged[wc_only_mask], val_labels[wc_only_mask],
             ep_val[wc_only_mask], lr_val[wc_only_mask], "val/world_cup_only")

    # --- 盲测集分层 ---
    if len(blind_merged) > 0 and len(ep_blind) > 0:
        print("  盲测集分层评估...")

        blind_merged["_year"] = blind_merged["match_date"].dt.year
        for year, group in blind_merged.groupby("_year"):
            mask = blind_merged["_year"] == year
            stratify(group, blind_labels[mask], ep_blind[mask], lr_blind[mask], f"blind/year/{year}")

        for cat, group in blind_merged.groupby("tournament_category"):
            mask = blind_merged["tournament_category"] == cat
            stratify(group, blind_labels[mask], ep_blind[mask], lr_blind[mask], f"blind/tournament/{cat}")

        for is_off, group in blind_merged.groupby("is_official"):
            mask = blind_merged["is_official"] == is_off
            label = "official" if is_off else "friendly"
            stratify(group, blind_labels[mask], ep_blind[mask], lr_blind[mask], f"blind/official_vs_friendly/{label}")

        for is_neut, group in blind_merged.groupby("is_neutral"):
            mask = blind_merged["is_neutral"] == is_neut
            label = "neutral" if is_neut else "non_neutral"
            stratify(group, blind_labels[mask], ep_blind[mask], lr_blind[mask], f"blind/venue/{label}")

        blind_elo_col = None
        for candidate in ["elo_diff_match", "elo_diff_feat", "elo_diff"]:
            if candidate in blind_merged.columns:
                blind_elo_col = candidate
                break
        if blind_elo_col is not None:
            blind_elo_abs = blind_merged[blind_elo_col].abs()
        else:
            blind_elo_abs = pd.Series([0] * len(blind_merged), index=blind_merged.index)

        sw_mask = blind_elo_abs > 200
        cm_mask = blind_elo_abs <= 100
        stratify(blind_merged[sw_mask], blind_labels[sw_mask],
                 ep_blind[sw_mask], lr_blind[sw_mask], "blind/gap/strong_weak")
        stratify(blind_merged[cm_mask], blind_labels[cm_mask],
                 ep_blind[cm_mask], lr_blind[cm_mask], "blind/gap/close_match")

        wc_mask_b = blind_merged["tournament_category"].isin(["world_cup", "qualification"])
        wc_only_mask_b = blind_merged["tournament_category"] == "world_cup"
        stratify(blind_merged[wc_mask_b], blind_labels[wc_mask_b],
                 ep_blind[wc_mask_b], lr_blind[wc_mask_b], "blind/world_cup_plus_qualification")
        stratify(blind_merged[wc_only_mask_b], blind_labels[wc_only_mask_b],
                 ep_blind[wc_only_mask_b], lr_blind[wc_only_mask_b], "blind/world_cup_only")

    # 打印关键结果
    print("\n  关键分层结果:")
    for key in sorted(stratified_results.keys()):
        r = stratified_results[key]
        ep_b = r["EloPoisson"]["brier_score"]
        lr_b = r["LR_full"]["brier_score"]
        print(f"    {key}: N={r['n_samples']}, EP={ep_b:.4f}, LR={lr_b:.4f}, Δ={ep_b - lr_b:+.4f}")

    save_json(stratified_results, "stratified_results.json")
    return stratified_results


# ============================================================
# 步骤 7: 主场优势深度分析
# ============================================================

def step7_host_advantage(model_data):
    print("\n" + "=" * 70)
    print("步骤 7: 主场优势深度分析")
    print("=" * 70)

    val_merged = model_data["val_merged"]
    blind_merged = model_data["blind_merged"]
    ep_val = model_data["model_preds"]["EloPoisson"][0]
    lr_val = model_data["model_preds"]["LR_full"][0]
    val_labels = model_data["val_labels"]

    def classify_venue(row):
        """分类场地类型：真实主场、中立场、东道主、半主场。"""
        is_neutral = row.get("is_neutral", False)
        if not is_neutral:
            return "home"  # 非中立，主队主场

        # 中立场但比赛在主队国家 → 东道主
        country = str(row.get("country", ""))
        home_team = str(row.get("home_team", ""))
        away_team = str(row.get("away_team", ""))
        if country and home_team and country == home_team:
            return "host_nation"  # 世界杯东道主

        # 中立场但比赛在主队同一大洲 → 半主场
        # 通过 confederation 映射判断
        home_conf = str(row.get("home_confederation", ""))
        country_conf = str(row.get("country_confederation", ""))
        if home_conf and country_conf and home_conf == country_conf and home_team != country:
            return "semi_home"  # 同大洲中立场

        return "neutral"

    # 对验证集分类
    val_merged["_venue_type"] = val_merged.apply(classify_venue, axis=1)

    host_analysis = {"venue_types": {}}

    for vtype in ["home", "neutral", "host_nation", "semi_home"]:
        mask = val_merged["_venue_type"] == vtype
        subset = val_merged[mask]
        n = len(subset)

        if n == 0:
            host_analysis["venue_types"][vtype] = {
                "n_samples": 0,
                "home_win_rate": None,
                "elo_poisson_brier": None,
                "lr_full_brier": None,
                "host_advantage_helps": None,
            }
            continue

        home_win_rate = float((subset["result"] == "H").mean())

        try:
            ep_ev = evaluate_predictions(ep_val[mask], val_labels[mask])
            lr_ev = evaluate_predictions(lr_val[mask], val_labels[mask])
            ep_brier = ep_ev.brier_score
            lr_brier = lr_ev.brier_score
        except Exception:
            ep_brier = None
            lr_brier = None

        # host_advantage 是否在中立/世界杯比赛中有增量价值
        helps = None
        if vtype in ["neutral", "host_nation"] and ep_brier is not None and lr_brier is not None:
            helps = lr_brier < ep_brier  # LR_full 包含 host_advantage，如果 Brier 更低则有帮助

        host_analysis["venue_types"][vtype] = {
            "n_samples": n,
            "home_win_rate": home_win_rate,
            "elo_poisson_brier": ep_brier,
            "lr_full_brier": lr_brier,
            "host_advantage_helps": helps,
        }
        print(f"    {vtype}: N={n}, HomeWinRate={home_win_rate:.1%}, "
              f"EP_Brier={ep_brier}, LR_Brier={lr_brier}, helps={helps}")

    # 判定 host_advantage 是否应被拒绝
    neutral_helps = host_analysis["venue_types"].get("neutral", {}).get("host_advantage_helps")
    host_nation_helps = host_analysis["venue_types"].get("host_nation", {}).get("host_advantage_helps")

    # 如果在中立和东道主比赛中都没有增量价值，则拒绝
    if neutral_helps is False and host_nation_helps is False:
        verdict = "REJECTED"
        reason = "host_advantage 在中立/东道主比赛中均无增量价值"
    elif neutral_helps is True or host_nation_helps is True:
        verdict = "KEEP"
        reason = "host_advantage 在中立/东道主比赛中有增量价值"
    else:
        verdict = "INCONCLUSIVE"
        reason = "数据不足或评估失败，无法确定 host_advantage 增量价值"

    host_analysis["verdict"] = verdict
    host_analysis["reason"] = reason
    print(f"\n  主场优势判定: {verdict} - {reason}")

    # 盲测集分析（如果有数据）
    if len(blind_merged) > 0:
        blind_merged["_venue_type"] = blind_merged.apply(classify_venue, axis=1)
        ep_blind = model_data["model_preds"]["EloPoisson"][1]
        lr_blind = model_data["model_preds"]["LR_full"][1]
        blind_labels = model_data["blind_labels"]

        host_analysis["blind_venue_types"] = {}
        for vtype in ["home", "neutral", "host_nation", "semi_home"]:
            mask = blind_merged["_venue_type"] == vtype
            subset = blind_merged[mask]
            n = len(subset)
            if n == 0:
                host_analysis["blind_venue_types"][vtype] = {"n_samples": 0}
                continue
            home_win_rate = float((subset["result"] == "H").mean())
            try:
                ep_ev = evaluate_predictions(ep_blind[mask], blind_labels[mask])
                lr_ev = evaluate_predictions(lr_blind[mask], blind_labels[mask])
                host_analysis["blind_venue_types"][vtype] = {
                    "n_samples": n,
                    "home_win_rate": home_win_rate,
                    "elo_poisson_brier": ep_ev.brier_score,
                    "lr_full_brier": lr_ev.brier_score,
                }
            except Exception:
                host_analysis["blind_venue_types"][vtype] = {
                    "n_samples": n,
                    "home_win_rate": home_win_rate,
                }

    save_json(host_analysis, "host_advantage_analysis.json")
    return host_analysis


# ============================================================
# 步骤 8: 时间泄漏审计
# ============================================================

def step8_time_leak_audit(df):
    print("\n" + "=" * 70)
    print("步骤 8: 时间泄漏审计")
    print("=" * 70)

    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}

    rng = np.random.RandomState(RANDOM_SEED)
    audit_results = {"checks": [], "overall_pass": True}

    # 选择 100 个历史比赛（2015-2020）
    historical = filter_by_date(df, "2015-01-01", "2020-12-31")
    if len(historical) < 100:
        historical = filter_by_date(df, "2010-01-01", "2020-12-31")
    sample_hist = historical.sample(min(100, len(historical)), random_state=rng)

    # 计算原始特征
    print("  计算原始特征（100 个历史比赛）...")
    orig_features = compute_all_features(sample_hist, df, active_factors, show_progress=False)

    # 随机修改 50 个未来比赛结果
    future = filter_by_date(df, "2021-01-01", "2025-12-31")
    if len(future) < 50:
        future = filter_by_date(df, "2020-01-01", "2025-12-31")
    sample_future = future.sample(min(50, len(future)), random_state=rng)

    # 创建篡改数据副本
    df_tampered = df.copy()
    tampered_indices = sample_future.index
    for idx in tampered_indices:
        df_tampered.loc[idx, "home_goals"] = 9
        df_tampered.loc[idx, "away_goals"] = 0
        # 重新计算 result
        df_tampered.loc[idx, "result"] = "H"

    # 重新计算特征
    print("  计算篡改后特征...")
    tampered_features = compute_all_features(sample_hist, df_tampered, active_factors, show_progress=False)

    # 逐因子检查
    print("\n  时间泄漏检查:")
    for col in orig_features.columns:
        if col == "match_id":
            continue
        orig_vals = orig_features[col].values
        tamp_vals = tampered_features[col].values

        # 比较（处理 None/NaN）
        changed = 0
        for i in range(len(orig_vals)):
            o = orig_vals[i]
            t = tamp_vals[i]
            if o is None and t is None:
                continue
            if o is None or t is None:
                changed += 1
                continue
            try:
                if isinstance(o, float) and isinstance(t, float):
                    if not np.isclose(o, t, equal_nan=True):
                        changed += 1
                elif o != t:
                    changed += 1
            except (TypeError, ValueError):
                if o != t:
                    changed += 1

        passed = changed == 0
        status = "PASS" if passed else "FAIL"
        if not passed:
            audit_results["overall_pass"] = False

        check = {
            "factor": col,
            "n_changed": changed,
            "n_total": len(orig_vals),
            "status": status,
        }
        audit_results["checks"].append(check)
        print(f"    {col}: {status} (changed={changed}/{len(orig_vals)})")

    overall = "PASS" if audit_results["overall_pass"] else "FAIL"
    print(f"\n  整体审计结果: {overall}")
    audit_results["overall_status"] = overall

    save_json(audit_results, "time_leak_audit.json")
    return audit_results


# ============================================================
# 步骤 9: 逐因子分析
# ============================================================

def step9_per_factor_analysis(model_data, coverage_report):
    print("\n" + "=" * 70)
    print("步骤 9: 逐因子分析")
    print("=" * 70)

    val_merged = model_data["val_merged"]
    train_merged = model_data["train_merged"]
    val_labels = model_data["val_labels"]
    full_cols = model_data["full_cols"]
    y_train = model_data["y_train"]

    # 全模型基线
    lr_full = model_data["lr_full"]
    scaler_full = model_data["scaler_full"]

    per_factor = {}

    for factor_name in NEW_CANDIDATE_FACTORS:
        print(f"  分析因子: {factor_name}")
        feat_col = f"{factor_name}_feat"
        if feat_col not in val_merged.columns:
            if factor_name in val_merged.columns:
                feat_col = factor_name
            else:
                print(f"    跳过 {factor_name}: 列不存在")
                continue

        factor_vals = val_merged[feat_col]
        valid_mask = factor_vals.notna()
        coverage = float(valid_mask.mean())

        # 覆盖率
        factor_info = {"coverage_rate": coverage}

        # 与 elo_diff 的相关性
        elo_col = "elo_diff_feat" if "elo_diff_feat" in val_merged.columns else "elo_diff"
        if elo_col in val_merged.columns:
            both_valid = valid_mask & val_merged[elo_col].notna()
            if both_valid.sum() > 10:
                fv = factor_vals[both_valid].astype(float).values
                ev = val_merged[elo_col][both_valid].astype(float).values
                pearson_r, pearson_p = stats.pearsonr(fv, ev)
                spearman_r, spearman_p = stats.spearmanr(fv, ev)
                factor_info["pearson_with_elo"] = {"r": float(pearson_r), "p": float(pearson_p)}
                factor_info["spearman_with_elo"] = {"r": float(spearman_r), "p": float(spearman_p)}
            else:
                factor_info["pearson_with_elo"] = None
                factor_info["spearman_with_elo"] = None
        else:
            factor_info["pearson_with_elo"] = None
            factor_info["spearman_with_elo"] = None

        # 与比赛结果的相关性 (point-biserial for home win)
        home_win = (val_labels == "H").astype(float)
        both_valid_hw = valid_mask.values
        if both_valid_hw.sum() > 10:
            fv = factor_vals[both_valid_hw].astype(float).values
            hw = home_win[both_valid_hw]
            try:
                pb_r, pb_p = stats.pointbiserialr(hw, fv)
                factor_info["point_biserial_home_win"] = {"r": float(pb_r), "p": float(pb_p)}
            except Exception:
                factor_info["point_biserial_home_win"] = None
        else:
            factor_info["point_biserial_home_win"] = None

        # 年度稳定性 (变异系数)
        val_with_year = val_merged.copy()
        val_with_year["_year"] = val_with_year["match_date"].dt.year
        val_with_year["_fv"] = factor_vals

        yearly_means = val_with_year[val_with_year["_fv"].notna()].groupby("_year")["_fv"].mean()
        if len(yearly_means) > 1 and yearly_means.mean() != 0:
            cv = float(yearly_means.std() / abs(yearly_means.mean()))
        else:
            cv = None
        factor_info["stability_cv"] = cv

        # 消融: 移除此因子后的 Brier 变化
        remaining_cols = [c for c in full_cols if c != feat_col]
        if len(remaining_cols) > 0:
            try:
                X_train_abl = train_merged[remaining_cols].fillna(0).values
                X_val_abl = val_merged[remaining_cols].fillna(0).values
                lr_abl, sc_abl = train_lr_model(X_train_abl, y_train)
                abl_preds = predict_lr(lr_abl, sc_abl, X_val_abl)
                abl_ev = evaluate_predictions(abl_preds, val_labels)

                full_preds = predict_lr(lr_full, scaler_full, val_merged[full_cols].fillna(0).values)
                full_ev = evaluate_predictions(full_preds, val_labels)

                brier_delta = abl_ev.brier_score - full_ev.brier_score
                factor_info["ablation_brier_delta"] = float(brier_delta)
                factor_info["ablation_direction"] = "worse_without" if brier_delta > 0 else "better_without"
            except Exception as e:
                factor_info["ablation_brier_delta"] = None
                factor_info["ablation_error"] = str(e)
        else:
            factor_info["ablation_brier_delta"] = None

        # 冗余判定
        pearson_r = None
        if factor_info.get("pearson_with_elo"):
            pearson_r = abs(factor_info["pearson_with_elo"]["r"])
        factor_info["is_redundant"] = pearson_r is not None and pearson_r > 0.7
        factor_info["redundancy_threshold"] = 0.7

        per_factor[factor_name] = factor_info

        # 打印摘要
        cov_str = f"{coverage:.1%}"
        pearson_str = f"r={pearson_r:.3f}" if pearson_r is not None else "N/A"
        abl_str = f"Δ={factor_info.get('ablation_brier_delta', 'N/A')}"
        red_str = "REDUNDANT" if factor_info["is_redundant"] else "OK"
        print(f"    coverage={cov_str}, pearson_elo={pearson_str}, ablation={abl_str}, {red_str}")

    save_json(per_factor, "per_factor_analysis.json")
    return per_factor


# ============================================================
# 步骤 10: 校准曲线
# ============================================================

def step10_calibration(model_data):
    print("\n" + "=" * 70)
    print("步骤 10: 校准曲线")
    print("=" * 70)

    val_labels = model_data["val_labels"]
    ep_val = model_data["model_preds"]["EloPoisson"][0]
    lr_val = model_data["model_preds"]["LR_full"][0]

    # EloPoisson 校准数据
    print("  计算 EloPoisson 校准曲线...")
    ep_cal = reliability_diagram_data(ep_val, val_labels, n_bins=10)

    # LR_full 校准数据
    print("  计算 LR_full 校准曲线...")
    lr_cal = reliability_diagram_data(lr_val, val_labels, n_bins=10)

    calibration_data = {
        "EloPoisson": ep_cal,
        "LR_full": lr_cal,
    }

    # 打印校准摘要
    for model_name, cal in calibration_data.items():
        print(f"\n  {model_name} 校准:")
        for outcome, data in cal.items():
            centers = data["bin_centers"]
            freqs = data["actual_frequencies"]
            counts = data["counts"]
            print(f"    {outcome}:")
            for c, f, n in zip(centers, freqs, counts):
                print(f"      pred={c:.2f} -> actual={f:.3f} (N={n})")

    save_json(calibration_data, "calibration_data.json")
    return calibration_data


# ============================================================
# 步骤 11: 最终决策
# ============================================================

def step11_final_decision(model_results, bootstrap_results, wf_results,
                          stratified_results, host_analysis, time_leak_audit,
                          per_factor, calibration_data):
    print("\n" + "=" * 70)
    print("步骤 11: 最终决策")
    print("=" * 70)

    # ---- 决策逻辑 ----

    # 1. 是否有任何新因子显示显著改善
    any_significant = any(r["significant"] for r in bootstrap_results.values())

    # 2. 改善是否在 walk-forward 中稳定
    if len(wf_results) >= 3:
        wf_deltas = [w["delta_brier"] for w in wf_results]
        wf_mean = np.mean(wf_deltas)
        wf_std = np.std(wf_deltas)
        n_wf_positive = sum(1 for d in wf_deltas if d > 0)
        # 稳定性判定：方向一致性（≥75%窗口同向）且均值改善
        stable_across_windows = n_wf_positive / len(wf_results) >= 0.75 and wf_mean > 0
    else:
        stable_across_windows = False
        n_wf_positive = 0

    # 3. 世界杯比赛是否无退化
    wc_keys = [k for k in stratified_results if "world_cup_only" in k]
    wc_no_degradation = True
    for key in wc_keys:
        r = stratified_results[key]
        ep_brier = r["EloPoisson"]["brier_score"]
        lr_brier = r["LR_full"]["brier_score"]
        if lr_brier > ep_brier + 0.005:  # 允许微小退化
            wc_no_degradation = False
            break

    # 4. Bootstrap CI 不跨零（LR_full 相对 EP）
    lr_full_significant = bootstrap_results.get("LR_full", {}).get("significant", False)
    lr_full_positive = bootstrap_results.get("LR_full", {}).get("mean_brier_diff", 0) > 0

    # 5. 新因子单独是否有价值
    lr_new_only_worse = bootstrap_results.get("LR_new_only", {}).get("mean_brier_diff", 0) < 0

    # 6. 盲测集是否有退化
    blind_degradation = False
    if "LR_full" in model_results and "blind_test" in model_results["LR_full"]:
        lr_blind_brier = model_results["LR_full"]["blind_test"]["brier_score"]
        ep_blind_brier = model_results.get("EloPoisson", {}).get("blind_test", {}).get("brier_score", 0)
        if ep_blind_brier > 0 and lr_blind_brier > ep_blind_brier:
            blind_degradation = True

    # 综合决策
    # PASS_SHADOW: 所有条件满足 - 显著改善、WF稳定、世界杯无退化、新因子有独立价值
    # NEEDS_MORE_DATA: 有部分正面证据但存在关键问题
    # REJECTED: 无稳定优于EloPoisson
    if (lr_full_significant and lr_full_positive and stable_across_windows
            and wc_no_degradation and not lr_new_only_worse and not blind_degradation):
        decision = "PASS_SHADOW"
    elif (lr_full_significant and lr_full_positive) or (stable_across_windows and wc_no_degradation):
        decision = "NEEDS_MORE_DATA"
    else:
        decision = "REJECTED"

    print(f"\n  决策条件:")
    print(f"    LR_full 显著优于EP: {lr_full_significant and lr_full_positive}")
    print(f"    Walk-Forward 稳定: {stable_across_windows}")
    print(f"    世界杯无退化: {wc_no_degradation}")
    print(f"    新因子单独有价值: {not lr_new_only_worse}")
    print(f"    盲测集无退化: {not blind_degradation}")
    print(f"\n  最终决策: {decision}")

    # ---- 生成 PROMOTION_DECISION.md ----
    lines = []
    lines.append("# 第二轮因子准入评审报告\n")
    lines.append(f"评审时间: {datetime.now().isoformat()}\n")

    # 执行摘要
    lines.append("## 执行摘要\n")
    lines.append(f"**最终决策: {decision}**\n")
    if decision == "PASS_SHADOW":
        lines.append("新候选因子通过了第二轮验证，建议进入 Shadow 模式运行。\n")
    elif decision == "NEEDS_MORE_DATA":
        lines.append("部分证据支持新因子价值，但不足以做出确定性结论，需要更多数据。\n")
    else:
        lines.append("新候选因子未能通过第二轮验证，不建议进入生产环境。\n")

    # Baseline 性能表
    lines.append("## Baseline 性能表\n")
    lines.append("| 模型 | 验证集 Brier | 验证集 LogLoss | 验证集 Accuracy | 验证集 ECE |")
    lines.append("|------|-------------|---------------|----------------|-----------|")
    for name in ["EloPoisson", "EloLogistic", "LR_elo_only"]:
        if name in model_results:
            r = model_results[name]["validation"]
            lines.append(f"| {name} | {r['brier_score']:.4f} | {r['log_loss']:.4f} | {r['accuracy']:.1%} | {r['ece']:.4f} |")

    # 新因子性能表
    lines.append("\n## 新因子性能表\n")
    lines.append("| 模型 | 验证集 Brier | 验证集 LogLoss | 验证集 Accuracy | 验证集 ECE |")
    lines.append("|------|-------------|---------------|----------------|-----------|")
    for name in ["LR_new_only", "LR_full"]:
        if name in model_results:
            r = model_results[name]["validation"]
            lines.append(f"| {name} | {r['brier_score']:.4f} | {r['log_loss']:.4f} | {r['accuracy']:.1%} | {r['ece']:.4f} |")

    # Bootstrap CI 结果
    lines.append("\n## Bootstrap 置信区间结果\n")
    lines.append("| 模型 | ΔBrier 均值 | 95% CI 下界 | 95% CI 上界 | 显著? |")
    lines.append("|------|------------|------------|------------|-------|")
    for name, r in bootstrap_results.items():
        lines.append(f"| {name} | {r['mean_brier_diff']:.5f} | {r['ci_95_low']:.5f} | {r['ci_95_high']:.5f} | {'是' if r['significant'] else '否'} |")

    # Walk-Forward 稳定性
    lines.append("\n## Walk-Forward 稳定性评估\n")
    lines.append(f"窗口数: {len(wf_results)}\n")
    if wf_results:
        lines.append("| 窗口 | 测试期 | 样本数 | EP Brier | LR Brier | ΔBrier | EP LogLoss | LR LogLoss | EP Acc | LR Acc | EP ECE | LR ECE |")
        lines.append("|------|--------|--------|----------|----------|--------|-----------|-----------|--------|--------|--------|--------|")
        for w in wf_results:
            lines.append(f"| {wf_results.index(w)+1} | {w['test_period']} | {w['n_test']} | "
                        f"{w['EloPoisson_brier']:.4f} | {w['LR_full_brier']:.4f} | {w['delta_brier']:+.4f} | "
                        f"{w['EloPoisson_logloss']:.4f} | {w['LR_full_logloss']:.4f} | "
                        f"{w['EloPoisson_acc']:.1%} | {w['LR_full_acc']:.1%} | "
                        f"{w['EloPoisson_ece']:.4f} | {w['LR_full_ece']:.4f} |")
        wf_deltas = [w["delta_brier"] for w in wf_results]
        lines.append(f"\nΔBrier 均值: {np.mean(wf_deltas):+.4f}")
        lines.append(f"ΔBrier 标准差: {np.std(wf_deltas):.4f}")
        lines.append(f"LR优于EP的窗口: {n_wf_positive}/{len(wf_results)}")
        lines.append(f"稳定性评估: {'稳定' if stable_across_windows else '不稳定'}")

    # 世界杯专项结果
    lines.append("\n## 世界杯专项结果\n")
    for key in sorted(stratified_results.keys()):
        if "world_cup" in key:
            r = stratified_results[key]
            ep_b = r["EloPoisson"]["brier_score"]
            lr_b = r["LR_full"]["brier_score"]
            lines.append(f"- **{key}**: N={r['n_samples']}, EloPoisson Brier={ep_b:.4f}, LR_full Brier={lr_b:.4f}, Δ={ep_b - lr_b:+.4f}")

    # 主场优势判定
    lines.append("\n## 主场优势判定\n")
    lines.append(f"**判定: {host_analysis['verdict']}**\n")
    lines.append(f"原因: {host_analysis['reason']}\n")
    for vtype, info in host_analysis.get("venue_types", {}).items():
        lines.append(f"- {vtype}: N={info['n_samples']}, HomeWinRate={info.get('home_win_rate', 'N/A')}")

    # 逐因子判定表
    lines.append("\n## 逐因子判定表\n")
    lines.append("| 因子 | 覆盖率 | Pearson(elo) | 冗余? | 消融ΔBrier | 判定 |")
    lines.append("|------|--------|-------------|-------|-----------|------|")
    for fn, info in per_factor.items():
        cov = f"{info['coverage_rate']:.1%}"
        pearson = f"{abs(info['pearson_with_elo']['r']):.3f}" if info.get("pearson_with_elo") else "N/A"
        redundant = "是" if info.get("is_redundant") else "否"
        abl = f"{info.get('ablation_brier_delta', 'N/A')}"
        if info.get("ablation_brier_delta") is not None:
            abl = f"{info['ablation_brier_delta']:+.4f}"

        if info.get("is_redundant"):
            verdict = "冗余"
        elif info.get("coverage_rate", 0) < 0.3:
            verdict = "覆盖率不足"
        elif info.get("ablation_brier_delta") is not None and info["ablation_brier_delta"] > 0.001:
            verdict = "有贡献"
        elif info.get("ablation_brier_delta") is not None and info["ablation_brier_delta"] < -0.001:
            verdict = "负贡献"
        else:
            verdict = "贡献不显著"

        lines.append(f"| {fn} | {cov} | {pearson} | {redundant} | {abl} | {verdict} |")

    # 时间泄漏审计
    lines.append("\n## 时间泄漏审计结果\n")
    lines.append(f"**整体结果: {time_leak_audit['overall_status']}**\n")
    for check in time_leak_audit["checks"]:
        lines.append(f"- {check['factor']}: {check['status']} (changed={check['n_changed']}/{check['n_total']})")

    # 决策推理
    lines.append("\n## 决策推理\n")
    lines.append(f"1. LR_full 显著优于EP: **{'是' if lr_full_significant and lr_full_positive else '否'}**")
    lines.append(f"2. Walk-Forward 稳定: **{'是' if stable_across_windows else '否'}** (LR优于EP窗口: {n_wf_positive}/{len(wf_results)})")
    lines.append(f"3. 世界杯无退化: **{'是' if wc_no_degradation else '否'}**")
    lines.append(f"4. 新因子单独有价值: **{'是' if not lr_new_only_worse else '否'}**")
    lines.append(f"5. 盲测集无退化: **{'是' if not blind_degradation else '否'}**")
    lines.append(f"\n综合决策: **{decision}**")

    # 禁止事项重申
    lines.append("\n## 禁止事项重申\n")
    lines.append("- 本 Demo 不修改主程序")
    lines.append("- 不接入 Ensemble")
    lines.append("- 不调整 Shadow 权重")
    lines.append("- 所有结论仅基于当前数据和验证方法")
    lines.append("- 世界杯退化问题解决前，任何因子不得进入 Shadow 模式")

    md_content = "\n".join(lines)
    md_path = OUTPUT_DIR / "PROMOTION_DECISION.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"  已保存: {md_path}")

    return decision


# ============================================================
# 主函数
# ============================================================

def main():
    print("=" * 70)
    print("World Cup Factor Research - 第二轮验证")
    print(f"运行时间: {datetime.now().isoformat()}")
    print("=" * 70)

    ensure_output_dir()

    # 步骤 1: 数据加载
    df, train, val, blind = step1_load_data()

    # 步骤 2: 特征计算
    train_features, val_features, blind_features, coverage_report = step2_compute_features(df, train, val, blind)

    # 步骤 3: 统一模型对比
    model_data = step3_unified_comparison(df, train, val, blind, train_features, val_features, blind_features)

    # 步骤 4: Bootstrap 置信区间
    bootstrap_results = step4_bootstrap(model_data)

    # 步骤 5: 嵌套 Walk-Forward 验证
    wf_results = step5_walk_forward(df)

    # 步骤 6: 分层评估
    stratified_results = step6_stratified(model_data)

    # 步骤 7: 主场优势深度分析
    host_analysis = step7_host_advantage(model_data)

    # 步骤 8: 时间泄漏审计
    time_leak_audit = step8_time_leak_audit(df)

    # 步骤 9: 逐因子分析
    per_factor = step9_per_factor_analysis(model_data, coverage_report)

    # 步骤 10: 校准曲线
    calibration_data = step10_calibration(model_data)

    # 步骤 11: 最终决策
    decision = step11_final_decision(
        model_data["model_results"], bootstrap_results, wf_results,
        stratified_results, host_analysis, time_leak_audit,
        per_factor, calibration_data,
    )

    print("\n" + "=" * 70)
    print(f"第二轮验证完成! 最终决策: {decision}")
    print(f"输出目录: {OUTPUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
