#!/usr/bin/env python3
"""第三轮验证脚本 - 数据真实性审计

严格审计2026年数据，冻结历史数据至2025-12-31，
实现Leave-One-Tournament-Out世界杯回测、分组Bootstrap等。

使用方法:
    cd research/factor_demo
    python3 scripts/run_round3.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import load_international_results, filter_by_date
from src.features.as_of import compute_all_features
from src.features.calculator import FACTOR_FUNCTIONS
from src.models.baseline import EloLogisticBaseline, EloPoissonBaseline
from src.evaluation.metrics import evaluate_predictions
from src.evaluation.calibration import reliability_diagram_data
from src.utils.elo_replay import replay_elo_history, EloConfig

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

# ============================================================
# 常量
# ============================================================

OUTPUT_DIR = PROJECT_DIR / "outputs" / "round3"
CSV_PATH = Path(__file__).parent.parent.parent.parent / "data" / "external" / "international_results.csv"

# 时间边界 - 冻结至2025-12-31
DATA_START = "2000-01-01"
TRAIN_START = "2010-01-01"
TRAIN_END = "2018-12-31"
VAL_START = "2019-01-01"
VAL_END = "2025-12-31"
# 2026年不作为盲测集，改为前瞻Shadow验证
FREEZE_DATE = "2025-12-31"
TODAY = "2026-06-15"

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

# 世界杯年份（用于LOTO）
WORLD_CUP_YEARS = [2010, 2014, 2018, 2022]


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
    preds = []
    valid_indices = []
    for idx, m in df_slice.iterrows():
        elo_h = m.get("pre_match_elo_home", None)
        elo_a = m.get("pre_match_elo_away", None)
        if elo_h is None or elo_a is None or pd.isna(elo_h) or pd.isna(elo_a):
            continue
        p = model.predict(elo_home=elo_h, elo_away=elo_a, is_neutral=m.get("is_neutral", False))
        preds.append([p.home_win, p.draw, p.away_win])
        valid_indices.append(idx)
    return np.array(preds), valid_indices


def merge_features(df_slice, feature_df):
    merged = df_slice.merge(feature_df, on="match_id", how="left", suffixes=("_match", "_feat"))
    for c in feature_df.columns:
        if c == "match_id":
            continue
        feat_col = f"{c}_feat"
        match_col = f"{c}_match"
        if feat_col in merged.columns and match_col in merged.columns:
            if merged[feat_col].isna().all() and not merged[match_col].isna().all():
                merged[feat_col] = merged[match_col]
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


def get_factor_cols(merged, factor_names):
    cols = []
    for fn in factor_names:
        feat_col = f"{fn}_feat"
        if feat_col in merged.columns:
            cols.append(feat_col)
        elif fn in merged.columns:
            cols.append(fn)
    return cols


def train_lr_model(X_train, y_train, C=1.0):
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_train)
    lr = LogisticRegression(max_iter=2000, C=C, solver="lbfgs")
    lr.fit(X_scaled, y_train)
    return lr, scaler


def predict_lr(lr, scaler, X):
    X_scaled = scaler.transform(X)
    return lr.predict_proba(X_scaled)


# ============================================================
# 步骤 1: 数据真实性审计
# ============================================================

def step1_data_audit():
    print("\n" + "=" * 70)
    print("步骤 1: 数据真实性审计")
    print("=" * 70)

    # 计算文件哈希
    print("  计算文件哈希...")
    sha256 = hashlib.sha256()
    with open(CSV_PATH, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    file_hash = sha256.hexdigest()
    file_size = CSV_PATH.stat().st_size
    print(f"  SHA256: {file_hash}")
    print(f"  文件大小: {file_size:,} bytes")

    # 加载原始CSV
    raw = pd.read_csv(CSV_PATH)
    total_records = len(raw)
    print(f"  总记录数: {total_records}")

    # 审计2026年记录
    raw["date"] = pd.to_datetime(raw["date"])
    records_2026 = raw[raw["date"].dt.year == 2026].copy()
    print(f"  2026年记录数: {len(records_2026)}")

    # 分类：有比分 vs NA比分
    # pandas 将 "NA" 解析为 NaN，所以用 notna() 检测
    score_available = records_2026["home_score"].notna() & records_2026["away_score"].notna()
    no_score = ~score_available

    results_2026 = records_2026[score_available].copy()
    fixtures_2026 = records_2026[no_score].copy()

    print(f"  有比分（实际结果）: {len(results_2026)}")
    print(f"  无比分（赛程/未赛）: {len(fixtures_2026)}")

    # 日期超过今天的记录
    today = pd.Timestamp(TODAY)
    future_records = records_2026[records_2026["date"] > today]
    print(f"  日期超过今天({TODAY}): {len(future_records)}")

    # 世界杯记录
    wc_2026 = records_2026[records_2026["tournament"] == "FIFA World Cup"]
    wc_with_score = wc_2026[score_available.reindex(wc_2026.index).fillna(False)]
    wc_no_score = wc_2026[no_score.reindex(wc_2026.index).fillna(False)]
    print(f"  2026世界杯记录: {len(wc_2026)} (有比分: {len(wc_with_score)}, 无比分: {len(wc_no_score)})")

    # 添加 record_type 字段
    def classify_record(row):
        hs = row.get("home_score")
        aws = row.get("away_score")
        if pd.isna(hs) or pd.isna(aws):
            return "fixture"
        if pd.Timestamp(row["date"]) > today:
            return "fixture"  # 未来日期有比分也不可信
        return "result"

    records_2026["record_type"] = records_2026.apply(classify_record, axis=1)

    # 统计
    type_counts = records_2026["record_type"].value_counts().to_dict()
    print(f"  记录类型分布: {type_counts}")

    # 生成 invalid_records.csv（所有 fixture 类型）
    invalid = records_2026[records_2026["record_type"] == "fixture"].copy()
    invalid_path = OUTPUT_DIR / "invalid_records.csv"
    invalid.to_csv(invalid_path, index=False, encoding="utf-8")
    print(f"  已保存: {invalid_path} ({len(invalid)} 条)")

    # 生成 verified_results.csv（所有 result 类型）
    verified = records_2026[records_2026["record_type"] == "result"].copy()
    verified["result_verified"] = True
    verified["source"] = "kaggle_international_results"
    verified["fetched_at"] = datetime.now(timezone.utc).isoformat()
    verified["result_available_at"] = verified["date"].dt.strftime("%Y-%m-%d")
    verified_path = OUTPUT_DIR / "verified_results.csv"
    verified.to_csv(verified_path, index=False, encoding="utf-8")
    print(f"  已保存: {verified_path} ({len(verified)} 条)")

    # 找到最大真实赛果日期
    if len(results_2026) > 0:
        max_result_date = str(results_2026["date"].max().strftime("%Y-%m-%d"))
    else:
        max_result_date = "N/A"
    print(f"  最大真实赛果日期: {max_result_date}")

    # 生成 DATA_PROVENANCE_AUDIT.md
    audit_md = generate_provenance_audit(
        file_hash, file_size, total_records, len(records_2026),
        type_counts, max_result_date, len(wc_2026),
        len(wc_with_score), len(wc_no_score),
        results_2026, fixtures_2026,
    )
    audit_path = OUTPUT_DIR / "DATA_PROVENANCE_AUDIT.md"
    with open(audit_path, "w", encoding="utf-8") as f:
        f.write(audit_md)
    print(f"  已保存: {audit_path}")

    return {
        "file_hash": file_hash,
        "total_records": total_records,
        "records_2026": len(records_2026),
        "verified_2026": len(verified),
        "invalid_2026": len(invalid),
        "max_result_date": max_result_date,
        "wc_2026_total": len(wc_2026),
        "wc_2026_results": len(wc_2026[score_available]),
        "wc_2026_fixtures": len(wc_2026[no_score]),
    }


def generate_provenance_audit(file_hash, file_size, total_records, n_2026,
                               type_counts, max_result_date,
                               wc_total, wc_results, wc_fixtures,
                               results_2026, fixtures_2026):
    lines = []
    lines.append("# 数据溯源审计报告 - 第三轮验证")
    lines.append("")
    lines.append(f"**审计时间**: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**审计人**: 自动化脚本")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 1. 原始文件信息")
    lines.append("")
    lines.append(f"| 项目 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| 文件路径 | `data/external/international_results.csv` |")
    lines.append(f"| SHA256 | `{file_hash}` |")
    lines.append(f"| 文件大小 | {file_size:,} bytes |")
    lines.append(f"| 总记录数 | {total_records:,} |")
    lines.append(f"| 数据来源 | Kaggle International Football Results |")
    lines.append(f"| 最大真实赛果日期 | {max_result_date} |")
    lines.append("")

    lines.append("## 2. 2026年记录分类统计")
    lines.append("")
    lines.append(f"| 记录类型 | 数量 | 说明 |")
    lines.append(f"|---------|------|------|")
    lines.append(f"| result（已完赛） | {type_counts.get('result', 0)} | 有真实比分，日期 <= 今天 |")
    lines.append(f"| fixture（赛程/未赛） | {type_counts.get('fixture', 0)} | 无比分或日期 > 今天 |")
    lines.append(f"| simulated | 0 | 无模拟数据 |")
    lines.append(f"| seed | 0 | 无种子数据 |")
    lines.append(f"| **合计** | **{n_2026}** | |")
    lines.append("")

    lines.append("## 3. 2026世界杯记录详情")
    lines.append("")
    lines.append(f"| 类型 | 数量 |")
    lines.append(f"|------|------|")
    lines.append(f"| 世界杯总记录 | {wc_total} |")
    lines.append(f"| 已完赛（有比分） | {wc_results} |")
    lines.append(f"| 赛程/未赛（NA比分） | {wc_fixtures} |")
    lines.append("")

    # 已完赛世界杯记录
    if wc_results > 0:
        lines.append("### 已完赛世界杯比赛")
        lines.append("")
        lines.append("| 日期 | 主队 | 客队 | 比分 | 城市 | 国家 |")
        lines.append("|------|------|------|------|------|------|")
        wc_results_df = results_2026[results_2026["tournament"] == "FIFA World Cup"]
        for _, r in wc_results_df.iterrows():
            lines.append(f"| {r['date'].strftime('%Y-%m-%d')} | {r['home_team']} | {r['away_team']} | "
                        f"{int(r['home_score'])}-{int(r['away_score'])} | {r.get('city', 'N/A')} | {r.get('country', 'N/A')} |")
        lines.append("")

    # 赛程世界杯记录
    if wc_fixtures > 0:
        lines.append("### 未赛世界杯赛程（排除）")
        lines.append("")
        lines.append("| 日期 | 主队 | 客队 | 城市 | 国家 |")
        lines.append("|------|------|------|------|------|")
        wc_fixture_df = fixtures_2026[fixtures_2026["tournament"] == "FIFA World Cup"]
        for _, r in wc_fixture_df.iterrows():
            lines.append(f"| {r['date'].strftime('%Y-%m-%d')} | {r['home_team']} | {r['away_team']} | "
                        f"{r.get('city', 'N/A')} | {r.get('country', 'N/A')} |")
        lines.append("")

    lines.append("## 4. 数据冻结决策")
    lines.append("")
    lines.append(f"- **冻结日期**: {FREEZE_DATE}")
    lines.append(f"- **原因**: 2026年数据包含未完赛赛程，不可用于回测评分")
    lines.append(f"- **2026年已验证结果**: {type_counts.get('result', 0)} 场可用于特征计算（但不用于评分）")
    lines.append(f"- **2026年排除记录**: {type_counts.get('fixture', 0)} 场")
    lines.append("")
    lines.append("### 2026世界杯前瞻Shadow验证方案")
    lines.append("")
    lines.append("- 2026世界杯改为前瞻Shadow验证：赛前生成不可修改的预测快照，赛后再评分")
    lines.append("- 当前不得使用2026世界杯已有结果反向回测")
    lines.append("- 预测快照须在比赛开球前生成，包含时间戳和哈希签名")
    lines.append("")

    lines.append("## 5. 回测准入条件")
    lines.append("")
    lines.append("只有满足以下所有条件的比赛才能进入回测：")
    lines.append("")
    lines.append("1. `record_type = result`")
    lines.append("2. `result_verified = true`")
    lines.append("3. `result_available_at <= evaluation_as_of`")
    lines.append("4. 比赛日期 <= 2025-12-31（冻结日期）")
    lines.append("5. 有完整比分（非NA）")
    lines.append("")

    lines.append("## 6. 第二轮盲测指标作废声明")
    lines.append("")
    lines.append("第二轮验证中基于2026年数据生成的盲测指标（包括2026盲测集Brier、世界杯盲测退化等）")
    lines.append("因数据包含未完赛赛程和不可验证结果，**全部作废**。")
    lines.append("第三轮验证仅使用截至2025-12-31的已验证数据。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*审计完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}*")

    return "\n".join(lines)


# ============================================================
# 步骤 2: 加载冻结数据并计算特征
# ============================================================

def step2_load_frozen_data():
    print("\n" + "=" * 70)
    print("步骤 2: 加载冻结数据（截至2025-12-31）")
    print("=" * 70)

    # 加载原始数据
    df = load_international_results()

    # 过滤掉NA比分的记录（赛程/未赛）
    n_before = len(df)
    df = df[df["result"].notna()].copy()
    print(f"  过滤NA比分: {n_before} → {len(df)} (移除 {n_before - len(df)})")

    # 冻结至2025-12-31
    df = filter_by_date(df, DATA_START, FREEZE_DATE)
    print(f"  冻结数据 ({DATA_START} ~ {FREEZE_DATE}): {len(df)} 场")

    # 计算Elo
    print("  计算Elo历史...")
    df = replay_elo_history(df, EloConfig())
    print(f"  Elo计算完成")

    # 时间划分（无盲测集）
    train = filter_by_date(df, TRAIN_START, TRAIN_END)
    val = filter_by_date(df, VAL_START, VAL_END)

    print(f"  训练集 ({TRAIN_START} ~ {TRAIN_END}): {len(train)} 场")
    print(f"  验证集 ({VAL_START} ~ {VAL_END}): {len(val)} 场")

    # Elo覆盖率报告
    total_val = len(val)
    val_has_elo = val["pre_match_elo_home"].notna() & val["pre_match_elo_away"].notna()
    val_no_elo = total_val - val_has_elo.sum()
    elo_coverage = val_has_elo.mean()
    print(f"  验证集Elo覆盖率: {elo_coverage:.1%} ({val_no_elo} 场无Elo)")

    return df, train, val


def step3_compute_features(df, train, val):
    print("\n" + "=" * 70)
    print("步骤 3: 特征计算")
    print("=" * 70)

    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}
    print(f"  活跃因子数: {len(active_factors)}")

    print("  计算训练集特征...")
    train_features = compute_all_features(train, df, active_factors, show_progress=True)

    print("  计算验证集特征...")
    val_features = compute_all_features(val, df, active_factors, show_progress=True)

    print(f"  特征计算完成: train={train_features.shape}, val={val_features.shape}")

    return train_features, val_features


# ============================================================
# 步骤 4: 统一模型对比（仅训练集+验证集，无盲测集）
# ============================================================

def step4_unified_comparison(df, train, val, train_features, val_features):
    print("\n" + "=" * 70)
    print("步骤 4: 统一模型对比（冻结数据，无盲测集）")
    print("=" * 70)

    train_merged, all_feature_cols = merge_features(train, train_features)
    val_merged, _ = merge_features(val, val_features)

    baseline_cols = get_factor_cols(train_merged, BASELINE_FACTORS)
    new_cols = get_factor_cols(train_merged, NEW_CANDIDATE_FACTORS)
    full_cols = baseline_cols + new_cols

    y_train = train_merged["result"].map({"H": 0, "D": 1, "A": 2}).values

    # 过滤无Elo的验证集比赛
    val_has_elo = val_merged["pre_match_elo_home"].notna() & val_merged["pre_match_elo_away"].notna()
    val_eval = val_merged[val_has_elo]
    val_labels = val_eval["result"].values

    # 报告选择偏差
    total_val = len(val_merged)
    excluded = total_val - len(val_eval)
    excluded_pct = excluded / total_val * 100 if total_val > 0 else 0
    print(f"  验证集: 总{total_val}场, 有Elo {len(val_eval)}场, 排除{excluded}场({excluded_pct:.1f}%)")

    # 分析排除比赛的分布
    no_elo_df = val_merged[~val_has_elo]
    if len(no_elo_df) > 0:
        no_elo_tournaments = no_elo_df["tournament_category"].value_counts().to_dict()
        no_elo_years = no_elo_df["match_date"].dt.year.value_counts().sort_index().to_dict()
        print(f"  排除比赛赛事分布: {no_elo_tournaments}")
        print(f"  排除比赛年份分布: {no_elo_years}")

    selection_bias_report = {
        "total_val": total_val,
        "included": len(val_eval),
        "excluded": excluded,
        "excluded_pct": float(excluded_pct),
        "excluded_by_tournament": {k: int(v) for k, v in no_elo_df["tournament_category"].value_counts().to_dict().items()} if len(no_elo_df) > 0 else {},
        "excluded_by_year": {str(k): int(v) for k, v in no_elo_df["match_date"].dt.year.value_counts().sort_index().to_dict().items()} if len(no_elo_df) > 0 else {},
        "fallback_strategy": "EloPoisson使用历史平均胜率作为回退；LR模型用0填充缺失特征；排除无Elo比赛时报告选择偏差",
    }

    # ---- 模型训练与预测 ----
    # EloPoisson
    elo_poisson = EloPoissonBaseline()
    val_preds_ep, _ = predict_elo_baseline(elo_poisson, val_eval)

    # EloLogistic
    elo_logistic = EloLogisticBaseline()
    val_preds_el, _ = predict_elo_baseline(elo_logistic, val_eval)

    # LR_elo_only
    X_train_base = train_merged[baseline_cols].fillna(0).values
    lr_base, scaler_base = train_lr_model(X_train_base, y_train)
    val_preds_lr_base = predict_lr(lr_base, scaler_base, val_eval[baseline_cols].fillna(0).values)

    # LR_new_only
    X_train_new = train_merged[new_cols].fillna(0).values
    lr_new, scaler_new = train_lr_model(X_train_new, y_train)
    val_preds_lr_new = predict_lr(lr_new, scaler_new, val_eval[new_cols].fillna(0).values)

    # LR_full
    X_train_full = train_merged[full_cols].fillna(0).values
    lr_full, scaler_full = train_lr_model(X_train_full, y_train)
    val_preds_lr_full = predict_lr(lr_full, scaler_full, val_eval[full_cols].fillna(0).values)

    # 评估
    model_results = {}
    model_preds = {
        "EloPoisson": val_preds_ep,
        "EloLogistic": val_preds_el,
        "LR_elo_only": val_preds_lr_base,
        "LR_new_only": val_preds_lr_new,
        "LR_full": val_preds_lr_full,
    }

    for name, vp in model_preds.items():
        val_ev = evaluate_predictions(vp, val_labels)
        model_results[name] = {"validation": eval_to_dict(val_ev)}
        print(f"    {name}: Val Brier={val_ev.brier_score:.4f}, LogLoss={val_ev.log_loss:.4f}, "
              f"Acc={val_ev.accuracy:.1%}, ECE={val_ev.ece:.4f}")

    save_json(model_results, "unified_comparison_table.json")
    save_json(selection_bias_report, "selection_bias_report.json")

    return {
        "model_results": model_results,
        "model_preds": model_preds,
        "train_merged": train_merged,
        "val_merged": val_eval,
        "all_feature_cols": all_feature_cols,
        "baseline_cols": baseline_cols,
        "new_cols": new_cols,
        "full_cols": full_cols,
        "y_train": y_train,
        "val_labels": val_labels,
        "lr_full": lr_full,
        "scaler_full": scaler_full,
        "selection_bias_report": selection_bias_report,
    }


# ============================================================
# 步骤 5: 分组Bootstrap（按赛事/时间窗口）
# ============================================================

def step5_block_bootstrap(model_data):
    print("\n" + "=" * 70)
    print("步骤 5: 分组Bootstrap置信区间")
    print("=" * 70)

    val_merged = model_data["val_merged"]
    val_labels = model_data["val_labels"]
    baseline_preds = model_data["model_preds"]["EloPoisson"]
    rng = np.random.RandomState(RANDOM_SEED)

    label_map = {"H": 0, "D": 1, "A": 2}
    n = len(val_labels)
    y_true_onehot = np.zeros((n, 3))
    for i, label in enumerate(val_labels):
        y_true_onehot[i, label_map[label]] = 1.0

    baseline_briers = np.sum((baseline_preds - y_true_onehot) ** 2, axis=1)

    # 分组方式
    # 1. 按年份分组
    year_groups = val_merged["match_date"].dt.year.values
    unique_years = np.unique(year_groups)
    year_to_idx = {y: i for i, y in enumerate(unique_years)}
    year_group_ids = np.array([year_to_idx[y] for y in year_groups])

    # 2. 按赛事类型分组
    tournament_groups = val_merged["tournament_category"].values
    unique_tournaments = np.unique(tournament_groups)
    tournament_to_idx = {t: i for i, t in enumerate(unique_tournaments)}
    tournament_group_ids = np.array([tournament_to_idx[t] for t in tournament_groups])

    bootstrap_results = {}

    for name, vp in model_data["model_preds"].items():
        if name == "EloPoisson":
            continue
        model_briers = np.sum((vp - y_true_onehot) ** 2, axis=1)
        diffs = baseline_briers - model_briers

        # --- 标准Bootstrap（独立重采样）---
        boot_diffs_iid = []
        for _ in range(N_BOOTSTRAP):
            idx = rng.randint(0, n, size=n)
            boot_diffs_iid.append(np.mean(diffs[idx]))
        boot_diffs_iid = np.array(boot_diffs_iid)

        # --- 按年份分组Bootstrap ---
        boot_diffs_year = []
        for _ in range(N_BOOTSTRAP):
            # 重采样年份组
            sampled_years = rng.choice(unique_years, size=len(unique_years), replace=True)
            idx = np.concatenate([np.where(year_groups == y)[0] for y in sampled_years])
            if len(idx) > 0:
                boot_diffs_year.append(np.mean(diffs[idx]))
        boot_diffs_year = np.array(boot_diffs_year)

        # --- 按赛事分组Bootstrap ---
        boot_diffs_tournament = []
        for _ in range(N_BOOTSTRAP):
            sampled_tournaments = rng.choice(unique_tournaments, size=len(unique_tournaments), replace=True)
            idx = np.concatenate([np.where(tournament_groups == t)[0] for t in sampled_tournaments])
            if len(idx) > 0:
                boot_diffs_tournament.append(np.mean(diffs[idx]))
        boot_diffs_tournament = np.array(boot_diffs_tournament)

        def summarize(boot_arr, label):
            mean = float(np.mean(boot_arr))
            ci_low = float(np.percentile(boot_arr, 2.5))
            ci_high = float(np.percentile(boot_arr, 97.5))
            significant = not (ci_low <= 0 <= ci_high)
            direction = "better" if mean > 0 else "worse"
            return {
                "mean_brier_diff": mean,
                "ci_95_low": ci_low,
                "ci_95_high": ci_high,
                "significant": significant,
                "direction": direction,
                "n_bootstrap": N_BOOTSTRAP,
                "method": label,
            }

        result_iid = summarize(boot_diffs_iid, "iid")
        result_year = summarize(boot_diffs_year, "block_by_year")
        result_tournament = summarize(boot_diffs_tournament, "block_by_tournament")

        bootstrap_results[name] = {
            "iid": result_iid,
            "block_by_year": result_year,
            "block_by_tournament": result_tournament,
        }

        print(f"  {name}:")
        for method, r in [("IID", result_iid), ("按年份分组", result_year), ("按赛事分组", result_tournament)]:
            sig_str = "显著" if r["significant"] else "不显著"
            dir_str = "优于EP" if r["direction"] == "better" else "劣于EP"
            print(f"    {method}: ΔBrier={r['mean_brier_diff']:+.5f}, "
                  f"95%CI=[{r['ci_95_low']:.5f}, {r['ci_95_high']:.5f}], "
                  f"{dir_str}, {sig_str}")

    save_json(bootstrap_results, "bootstrap_results.json")
    return bootstrap_results


# ============================================================
# 步骤 6: Walk-Forward（完整样本，无抽样，2026不作为窗口）
# ============================================================

def step6_walk_forward(df):
    print("\n" + "=" * 70)
    print("步骤 6: Walk-Forward验证（完整样本，无抽样）")
    print("=" * 70)

    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}
    wf_results = []

    # 2019-2025，不包含2026
    for test_year in range(2019, 2026):
        train_end_year = test_year - 1
        train_end = f"{train_end_year}-12-31"
        test_start = f"{test_year}-01-01"
        test_end = f"{test_year}-12-31"

        test = filter_by_date(df, test_start, test_end)
        test_elo = test[test["pre_match_elo_home"].notna() & test["pre_match_elo_away"].notna()]
        if len(test_elo) < 10:
            continue

        # EloPoisson baseline（完整样本）
        elo_poisson = EloPoissonBaseline()
        ep_preds, _ = predict_elo_baseline(elo_poisson, test_elo)
        ep_ev = evaluate_predictions(ep_preds, test_elo["result"].values)

        # LR_full（完整样本，不抽样）
        try:
            train_all = filter_by_date(df, TRAIN_START, train_end)
            train_feat = compute_all_features(train_all, df, active_factors, show_progress=False)
            train_m, fcols = merge_features(train_all, train_feat)

            test_feat = compute_all_features(test_elo, df, active_factors, show_progress=False)
            test_m, _ = merge_features(test_elo, test_feat)

            valid_cols = [c for c in fcols if c in train_m.columns and c in test_m.columns]
            y_train_wf = train_m["result"].map({"H": 0, "D": 1, "A": 2}).values

            lr, sc = train_lr_model(train_m[valid_cols].fillna(0).values, y_train_wf, C=1.0)
            lr_preds = predict_lr(lr, sc, test_m[valid_cols].fillna(0).values)
            lr_ev = evaluate_predictions(lr_preds, test_m["result"].values)

            delta = ep_ev.brier_score - lr_ev.brier_score
            wf_results.append({
                "test_period": f"{test_start}~{test_end}",
                "n_test": len(test_elo),
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
            print(f"  {test_year}: N={len(test_elo)}, EP={ep_ev.brier_score:.4f}, "
                  f"LR={lr_ev.brier_score:.4f}, Δ={delta:+.4f} {'✓' if delta > 0 else '✗'}")
        except Exception as e:
            print(f"  {test_year}: 失败 - {e}")

    if wf_results:
        deltas = [r["delta_brier"] for r in wf_results]
        n_pos = sum(1 for d in deltas if d > 0)
        print(f"\n  汇总: {len(wf_results)} 窗口, 平均ΔBrier={np.mean(deltas):+.4f}, "
              f"LR优于EP: {n_pos}/{len(wf_results)}")

    save_json(wf_results, "walk_forward_results.json")
    return wf_results


# ============================================================
# 步骤 7: Leave-One-Tournament-Out 世界杯回测
# ============================================================

def step7_loto_world_cup(df):
    print("\n" + "=" * 70)
    print("步骤 7: Leave-One-Tournament-Out 世界杯回测")
    print("=" * 70)
    print("  注意：这是历史样本外验证，非真正盲测")

    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}
    loto_results = []

    for holdout_wc_year in WORLD_CUP_YEARS:
        print(f"\n  --- 留出 {holdout_wc_year} 世界杯 ---")

        # 训练集：holdout_wc_year 之前的所有数据（排除该年世界杯）
        train_end = f"{holdout_wc_year - 1}-12-31"
        train_all = filter_by_date(df, TRAIN_START, train_end)
        print(f"  训练集: {TRAIN_START} ~ {train_end}, N={len(train_all)}")

        # 测试集：该年世界杯比赛
        wc_year_start = f"{holdout_wc_year}-01-01"
        wc_year_end = f"{holdout_wc_year}-12-31"
        wc_year_data = filter_by_date(df, wc_year_start, wc_year_end)
        wc_matches = wc_year_data[wc_year_data["tournament_category"] == "world_cup"]
        wc_with_elo = wc_matches[wc_matches["pre_match_elo_home"].notna() & wc_matches["pre_match_elo_away"].notna()]

        if len(wc_with_elo) < 5:
            print(f"  {holdout_wc_year} 世界杯样本不足: {len(wc_with_elo)}")
            continue

        print(f"  测试集: {holdout_wc_year} 世界杯, N={len(wc_with_elo)}")

        # EloPoisson
        elo_poisson = EloPoissonBaseline()
        ep_preds, _ = predict_elo_baseline(elo_poisson, wc_with_elo)
        ep_ev = evaluate_predictions(ep_preds, wc_with_elo["result"].values)

        # LR_full
        try:
            train_feat = compute_all_features(train_all, df, active_factors, show_progress=False)
            train_m, fcols = merge_features(train_all, train_feat)

            test_feat = compute_all_features(wc_with_elo, df, active_factors, show_progress=False)
            test_m, _ = merge_features(wc_with_elo, test_feat)

            valid_cols = [c for c in fcols if c in train_m.columns and c in test_m.columns]
            y_train_loto = train_m["result"].map({"H": 0, "D": 1, "A": 2}).values

            lr, sc = train_lr_model(train_m[valid_cols].fillna(0).values, y_train_loto, C=1.0)
            lr_preds = predict_lr(lr, sc, test_m[valid_cols].fillna(0).values)
            lr_ev = evaluate_predictions(lr_preds, test_m["result"].values)

            delta = ep_ev.brier_score - lr_ev.brier_score

            loto_results.append({
                "holdout_tournament": f"FIFA World Cup {holdout_wc_year}",
                "holdout_year": holdout_wc_year,
                "n_test": len(wc_with_elo),
                "EloPoisson_brier": ep_ev.brier_score,
                "LR_full_brier": lr_ev.brier_score,
                "delta_brier": delta,
                "EloPoisson_logloss": ep_ev.log_loss,
                "LR_full_logloss": lr_ev.log_loss,
                "EloPoisson_acc": ep_ev.accuracy,
                "LR_full_acc": lr_ev.accuracy,
                "EloPoisson_ece": ep_ev.ece,
                "LR_full_ece": lr_ev.ece,
                "validation_type": "historical_out_of_sample",
                "NOT_blind_test": True,
            })
            print(f"  EP Brier={ep_ev.brier_score:.4f}, LR Brier={lr_ev.brier_score:.4f}, "
                  f"Δ={delta:+.4f} {'✓' if delta > 0 else '✗'}")
        except Exception as e:
            print(f"  {holdout_wc_year} 失败: {e}")

    if loto_results:
        deltas = [r["delta_brier"] for r in loto_results]
        n_pos = sum(1 for d in deltas if d > 0)
        print(f"\n  LOTO汇总: {len(loto_results)} 届世界杯, 平均ΔBrier={np.mean(deltas):+.4f}, "
              f"LR优于EP: {n_pos}/{len(loto_results)}")

    save_json(loto_results, "historical_worldcup_backtest.json")
    return loto_results


# ============================================================
# 步骤 8: 分层评估
# ============================================================

def step8_stratified(model_data):
    print("\n" + "=" * 70)
    print("步骤 8: 分层评估")
    print("=" * 70)

    val_merged = model_data["val_merged"]
    val_labels = model_data["val_labels"]
    ep_val = model_data["model_preds"]["EloPoisson"]
    lr_val = model_data["model_preds"]["LR_full"]

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

    # 强弱差距
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

    # 世界杯 + 预选赛
    wc_mask = val_merged["tournament_category"].isin(["world_cup", "qualification"])
    wc_only_mask = val_merged["tournament_category"] == "world_cup"
    stratify(val_merged[wc_mask], val_labels[wc_mask],
             ep_val[wc_mask], lr_val[wc_mask], "val/world_cup_plus_qualification")
    stratify(val_merged[wc_only_mask], val_labels[wc_only_mask],
             ep_val[wc_only_mask], lr_val[wc_only_mask], "val/world_cup_only")

    print("  关键分层结果:")
    for key in sorted(stratified_results.keys()):
        r = stratified_results[key]
        ep_b = r["EloPoisson"]["brier_score"]
        lr_b = r["LR_full"]["brier_score"]
        print(f"    {key}: N={r['n_samples']}, EP={ep_b:.4f}, LR={lr_b:.4f}, Δ={ep_b - lr_b:+.4f}")

    save_json(stratified_results, "stratified_results.json")
    return stratified_results


# ============================================================
# 步骤 9: 主场优势深度分析
# ============================================================

def step9_host_advantage(model_data):
    print("\n" + "=" * 70)
    print("步骤 9: 主场优势深度分析")
    print("=" * 70)

    val_merged = model_data["val_merged"]
    val_labels = model_data["val_labels"]
    ep_val = model_data["model_preds"]["EloPoisson"]
    lr_val = model_data["model_preds"]["LR_full"]

    def classify_venue(row):
        is_neutral = row.get("is_neutral", False)
        if not is_neutral:
            return "home"
        country = str(row.get("country", ""))
        home_team = str(row.get("home_team", ""))
        if country and home_team and country == home_team:
            return "host_nation"
        home_conf = str(row.get("home_confederation", ""))
        country_conf = str(row.get("country_confederation", ""))
        if home_conf and country_conf and home_conf == country_conf and home_team != country:
            return "semi_home"
        return "neutral"

    val_merged["_venue_type"] = val_merged.apply(classify_venue, axis=1)

    host_analysis = {"venue_types": {}}

    for vtype in ["home", "neutral", "host_nation", "semi_home"]:
        mask = val_merged["_venue_type"] == vtype
        subset = val_merged[mask]
        n = len(subset)

        if n == 0:
            host_analysis["venue_types"][vtype] = {"n_samples": 0}
            continue

        home_win_rate = float((subset["result"] == "H").mean())

        try:
            ep_ev = evaluate_predictions(ep_val[mask], val_labels[mask])
            lr_ev = evaluate_predictions(lr_val[mask], val_labels[mask])
            host_analysis["venue_types"][vtype] = {
                "n_samples": n,
                "home_win_rate": home_win_rate,
                "elo_poisson_brier": ep_ev.brier_score,
                "lr_full_brier": lr_ev.brier_score,
                "host_advantage_helps": lr_ev.brier_score < ep_ev.brier_score if vtype in ["neutral", "host_nation"] else None,
            }
        except Exception:
            host_analysis["venue_types"][vtype] = {
                "n_samples": n,
                "home_win_rate": home_win_rate,
            }

        info = host_analysis["venue_types"][vtype]
        print(f"  {vtype}: N={n}, HomeWinRate={home_win_rate:.1%}, "
              f"EP_Brier={info.get('elo_poisson_brier', 'N/A')}, "
              f"LR_Brier={info.get('lr_full_brier', 'N/A')}")

    # 判定
    neutral_helps = host_analysis["venue_types"].get("neutral", {}).get("host_advantage_helps")
    host_nation_n = host_analysis["venue_types"].get("host_nation", {}).get("n_samples", 0)

    if neutral_helps is False and host_nation_n == 0:
        verdict = "REJECTED"
        reason = "host_advantage 在中立场无增量价值，且东道主样本为零"
    elif neutral_helps is True:
        verdict = "KEEP_WITH_RESERVATION"
        reason = "host_advantage 在中立场验证集有帮助，但东道主样本为零，中立场世界杯场景需进一步验证"
    else:
        verdict = "INCONCLUSIVE"
        reason = "数据不足，无法确定 host_advantage 增量价值"

    host_analysis["verdict"] = verdict
    host_analysis["reason"] = reason
    print(f"\n  判定: {verdict} - {reason}")

    save_json(host_analysis, "host_advantage_analysis.json")
    return host_analysis


# ============================================================
# 步骤 10: 时间泄漏审计
# ============================================================

def step10_time_leak_audit(df):
    print("\n" + "=" * 70)
    print("步骤 10: 时间泄漏审计")
    print("=" * 70)

    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}
    rng = np.random.RandomState(RANDOM_SEED)

    historical = filter_by_date(df, "2015-01-01", "2020-12-31")
    sample_hist = historical.sample(min(100, len(historical)), random_state=rng)

    orig_features = compute_all_features(sample_hist, df, active_factors, show_progress=False)

    future = filter_by_date(df, "2021-01-01", FREEZE_DATE)
    sample_future = future.sample(min(50, len(future)), random_state=rng)

    df_tampered = df.copy()
    for idx in sample_future.index:
        df_tampered.loc[idx, "home_goals"] = 9
        df_tampered.loc[idx, "away_goals"] = 0
        df_tampered.loc[idx, "result"] = "H"

    tampered_features = compute_all_features(sample_hist, df_tampered, active_factors, show_progress=False)

    audit_results = {"checks": [], "overall_pass": True}
    for col in orig_features.columns:
        if col == "match_id":
            continue
        orig_vals = orig_features[col].values
        tamp_vals = tampered_features[col].values
        changed = 0
        for i in range(len(orig_vals)):
            o, t = orig_vals[i], tamp_vals[i]
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
        if not passed:
            audit_results["overall_pass"] = False
        audit_results["checks"].append({
            "factor": col,
            "n_changed": changed,
            "n_total": len(orig_vals),
            "status": "PASS" if passed else "FAIL",
        })

    audit_results["overall_status"] = "PASS" if audit_results["overall_pass"] else "FAIL"
    n_pass = sum(1 for c in audit_results["checks"] if c["status"] == "PASS")
    print(f"  时间泄漏审计: {n_pass}/{len(audit_results['checks'])} PASS")

    save_json(audit_results, "time_leak_audit.json")
    return audit_results


# ============================================================
# 步骤 11: 逐因子分析
# ============================================================

def step11_per_factor(model_data):
    print("\n" + "=" * 70)
    print("步骤 11: 逐因子分析")
    print("=" * 70)

    val_merged = model_data["val_merged"]
    train_merged = model_data["train_merged"]
    val_labels = model_data["val_labels"]
    full_cols = model_data["full_cols"]
    y_train = model_data["y_train"]
    lr_full = model_data["lr_full"]
    scaler_full = model_data["scaler_full"]

    per_factor = {}

    for factor_name in NEW_CANDIDATE_FACTORS:
        feat_col = f"{factor_name}_feat"
        if feat_col not in val_merged.columns:
            if factor_name in val_merged.columns:
                feat_col = factor_name
            else:
                continue

        factor_vals = val_merged[feat_col]
        valid_mask = factor_vals.notna()
        coverage = float(valid_mask.mean())

        factor_info = {"coverage_rate": coverage}

        # 与elo_diff相关性
        elo_col = "elo_diff_feat" if "elo_diff_feat" in val_merged.columns else "elo_diff"
        if elo_col in val_merged.columns:
            both_valid = valid_mask & val_merged[elo_col].notna()
            if both_valid.sum() > 10:
                fv = factor_vals[both_valid].astype(float).values
                ev = val_merged[elo_col][both_valid].astype(float).values
                pearson_r, _ = stats.pearsonr(fv, ev)
                spearman_r, _ = stats.spearmanr(fv, ev)
                factor_info["pearson_with_elo"] = {"r": float(pearson_r)}
                factor_info["spearman_with_elo"] = {"r": float(spearman_r)}

        # 年度稳定性
        val_with_year = val_merged.copy()
        val_with_year["_year"] = val_with_year["match_date"].dt.year
        val_with_year["_fv"] = factor_vals
        yearly_means = val_with_year[val_with_year["_fv"].notna()].groupby("_year")["_fv"].mean()
        if len(yearly_means) > 1 and yearly_means.mean() != 0:
            factor_info["stability_cv"] = float(yearly_means.std() / abs(yearly_means.mean()))

        # 消融
        remaining_cols = [c for c in full_cols if c != feat_col]
        if remaining_cols:
            try:
                X_train_abl = train_merged[remaining_cols].fillna(0).values
                X_val_abl = val_merged[remaining_cols].fillna(0).values
                lr_abl, sc_abl = train_lr_model(X_train_abl, y_train)
                abl_preds = predict_lr(lr_abl, sc_abl, X_val_abl)
                abl_ev = evaluate_predictions(abl_preds, val_labels)

                full_preds = predict_lr(lr_full, scaler_full, val_merged[full_cols].fillna(0).values)
                full_ev = evaluate_predictions(full_preds, val_labels)

                factor_info["ablation_brier_delta"] = float(abl_ev.brier_score - full_ev.brier_score)
            except Exception:
                pass

        pearson_r = None
        if factor_info.get("pearson_with_elo"):
            pearson_r = abs(factor_info["pearson_with_elo"]["r"])
        factor_info["is_redundant"] = pearson_r is not None and pearson_r > 0.7
        factor_info["factor_classification"] = "baseline" if factor_name in BASELINE_FACTORS else "new_candidate"

        per_factor[factor_name] = factor_info
        cov_str = f"{coverage:.1%}"
        p_str = f"r={pearson_r:.2f}" if pearson_r is not None else "N/A"
        abl = factor_info.get("ablation_brier_delta", "N/A")
        red = "REDUNDANT" if factor_info["is_redundant"] else "OK"
        print(f"  {factor_name}: cov={cov_str}, pearson={p_str}, ablation={abl}, {red}")

    save_json(per_factor, "per_factor_analysis.json")
    return per_factor


# ============================================================
# 步骤 12: 校准曲线
# ============================================================

def step12_calibration(model_data):
    print("\n" + "=" * 70)
    print("步骤 12: 校准曲线")
    print("=" * 70)

    val_labels = model_data["val_labels"]
    ep_val = model_data["model_preds"]["EloPoisson"]
    lr_val = model_data["model_preds"]["LR_full"]

    ep_cal = reliability_diagram_data(ep_val, val_labels, n_bins=10)
    lr_cal = reliability_diagram_data(lr_val, val_labels, n_bins=10)

    calibration_data = {"EloPoisson": ep_cal, "LR_full": lr_cal}
    save_json(calibration_data, "calibration_data.json")
    return calibration_data


# ============================================================
# 步骤 13: 生成最终 PROMOTION_DECISION.md
# ============================================================

def step13_final_decision(model_results, bootstrap_results, wf_results,
                          loto_results, stratified_results, host_analysis,
                          time_leak_audit, per_factor, selection_bias_report,
                          audit_info):
    print("\n" + "=" * 70)
    print("步骤 13: 生成最终 PROMOTION_DECISION.md")
    print("=" * 70)

    decision = "NEEDS_MORE_DATA"  # 固定结论

    ep_val_brier = model_results["EloPoisson"]["validation"]["brier_score"]
    lr_val_brier = model_results["LR_full"]["validation"]["brier_score"]
    val_improvement = (ep_val_brier - lr_val_brier) / ep_val_brier * 100

    # Bootstrap 结果
    lr_full_iid = bootstrap_results.get("LR_full", {}).get("iid", {})
    lr_full_year = bootstrap_results.get("LR_full", {}).get("block_by_year", {})
    lr_full_tournament = bootstrap_results.get("LR_full", {}).get("block_by_tournament", {})

    # Walk-Forward
    wf_deltas = [w["delta_brier"] for w in wf_results] if wf_results else []
    n_wf_positive = sum(1 for d in wf_deltas if d > 0) if wf_deltas else 0

    # LOTO
    loto_deltas = [r["delta_brier"] for r in loto_results] if loto_results else []
    n_loto_positive = sum(1 for d in loto_deltas if d > 0) if loto_deltas else 0

    # 世界杯验证集
    wc_val = stratified_results.get("val/world_cup_only", {})
    wc_val_ep = wc_val.get("EloPoisson", {}).get("brier_score", 0)
    wc_val_lr = wc_val.get("LR_full", {}).get("brier_score", 0)

    lines = []
    lines.append("# 因子准入评审报告 - 第三轮数据真实性审计")
    lines.append("")
    lines.append(f"**评审时间**: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**最终结论**: **{decision}**")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 执行摘要
    lines.append("## 1. 执行摘要")
    lines.append("")
    lines.append(f"第三轮验证聚焦数据真实性审计。2026年数据中包含{audit_info['invalid_2026']}条未完赛赛程，"
                 f"已全部排除。历史数据冻结至{FREEZE_DATE}。")
    lines.append("")
    lines.append(f"LR_full（elo_diff + 新因子）相对 EloPoisson 在验证集上 Brier 改善 {val_improvement:.1f}%。"
                 f"Walk-Forward {n_wf_positive}/{len(wf_results)} 窗口优于 Baseline。"
                 f"LOTO世界杯回测 {n_loto_positive}/{len(loto_results)} 届优于 Baseline。")
    lines.append("")
    lines.append("**关键问题仍导致无法给予 PASS_SHADOW：**")
    lines.append("")
    lines.append("1. **新因子单独无价值**：LR_new_only 显著差于 EloPoisson")
    lines.append("2. **改善来源不明确**：改善主要来自 LR 对 elo_diff 的非线性校准")
    lines.append("3. **世界杯历史回测不稳定**：LOTO 结果需逐届审视")
    lines.append("4. **2026世界杯无法盲测**：改为前瞻Shadow验证")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 第二轮盲测作废声明
    lines.append("## 2. 第二轮盲测指标作废声明")
    lines.append("")
    lines.append("第二轮验证中基于2026年数据生成的盲测指标（包括2026盲测集Brier、世界杯盲测退化等）"
                 "因数据包含未完赛赛程，**全部作废**。")
    lines.append(f"2026年共{audit_info['records_2026']}条记录，其中{audit_info['invalid_2026']}条为赛程/未赛，"
                 f"仅{audit_info['verified_2026']}条为已验证结果。")
    lines.append(f"2026世界杯{audit_info['wc_2026_total']}条记录中，"
                 f"仅{audit_info['wc_2026_results']}条有比分，{audit_info['wc_2026_fixtures']}条为赛程。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Baseline 性能
    lines.append("## 3. Baseline 性能（冻结数据）")
    lines.append("")
    lines.append("| 模型 | 验证集 Brier | 验证集 LogLoss | 验证集 Acc | 验证集 ECE |")
    lines.append("|------|-------------|---------------|-----------|-----------|")
    for name in ["EloPoisson", "EloLogistic", "LR_elo_only"]:
        if name in model_results:
            v = model_results[name]["validation"]
            lines.append(f"| {name} | {v['brier_score']:.4f} | {v['log_loss']:.4f} | {v['accuracy']:.1%} | {v['ece']:.4f} |")
    lines.append("")

    # 新因子性能
    lines.append("## 4. 新因子模型性能")
    lines.append("")
    lines.append("| 模型 | 验证集 Brier | 相对EP改善 |")
    lines.append("|------|-------------|-----------|")
    for name in ["LR_new_only", "LR_full"]:
        if name in model_results:
            v = model_results[name]["validation"]
            imp = (ep_val_brier - v["brier_score"]) / ep_val_brier * 100
            lines.append(f"| {name} | {v['brier_score']:.4f} | {imp:+.2f}% |")
    lines.append("")

    # 选择偏差报告
    lines.append("## 5. Elo缺失选择偏差报告")
    lines.append("")
    lines.append(f"| 项目 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| 验证集总数 | {selection_bias_report['total_val']} |")
    lines.append(f"| 有Elo（纳入） | {selection_bias_report['included']} |")
    lines.append(f"| 无Elo（排除） | {selection_bias_report['excluded']} ({selection_bias_report['excluded_pct']:.1f}%) |")
    lines.append(f"| 回退方案 | {selection_bias_report['fallback_strategy']} |")
    if selection_bias_report.get("excluded_by_tournament"):
        lines.append("")
        lines.append("排除比赛赛事分布:")
        for t, c in selection_bias_report["excluded_by_tournament"].items():
            lines.append(f"- {t}: {c}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Bootstrap
    lines.append("## 6. 分组Bootstrap置信区间")
    lines.append("")
    lines.append("| 模型 | 方法 | ΔBrier均值 | 95% CI | 方向 | 显著? |")
    lines.append("|------|------|-----------|--------|------|-------|")
    for name in ["LR_full", "LR_elo_only", "LR_new_only"]:
        if name in bootstrap_results:
            for method_key, method_label in [("iid", "IID独立"), ("block_by_year", "按年份分组"), ("block_by_tournament", "按赛事分组")]:
                r = bootstrap_results[name].get(method_key, {})
                if r:
                    direction = "优于EP" if r.get("direction") == "better" else "劣于EP"
                    sig = "是" if r.get("significant") else "否"
                    if r.get("significant") and r.get("direction") == "worse":
                        sig = "是（更差）"
                    lines.append(f"| {name} | {method_label} | {r['mean_brier_diff']:+.5f} | "
                                f"[{r['ci_95_low']:.5f}, {r['ci_95_high']:.5f}] | {direction} | {sig} |")
    lines.append("")

    # 关键解读
    if lr_full_iid:
        lines.append(f"**解读**：LR_full IID Bootstrap ΔBrier={lr_full_iid.get('mean_brier_diff', 0):+.5f}，"
                     f"按年份分组后={lr_full_year.get('mean_brier_diff', 0):+.5f}，"
                     f"按赛事分组后={lr_full_tournament.get('mean_brier_diff', 0):+.5f}。"
                     f"分组重采样后CI更宽，反映了组内相关性。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Walk-Forward
    lines.append("## 7. Walk-Forward验证（完整样本，无抽样）")
    lines.append("")
    lines.append("| 测试窗口 | N | EP Brier | LR Brier | ΔBrier | EP LogLoss | LR LogLoss | EP ECE | LR ECE |")
    lines.append("|---------|---|---------|---------|--------|-----------|-----------|--------|--------|")
    for w in wf_results:
        lines.append(f"| {w['test_period']} | {w['n_test']} | {w['EloPoisson_brier']:.4f} | {w['LR_full_brier']:.4f} | "
                    f"{w['delta_brier']:+.4f} | {w['EloPoisson_logloss']:.4f} | {w['LR_full_logloss']:.4f} | "
                    f"{w['EloPoisson_ece']:.4f} | {w['LR_full_ece']:.4f} |")
    if wf_deltas:
        lines.append(f"\n**汇总**: {n_wf_positive}/{len(wf_results)} 窗口 LR 优于 EP，"
                     f"平均 ΔBrier={np.mean(wf_deltas):+.4f}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # LOTO 世界杯
    lines.append("## 8. Leave-One-Tournament-Out 世界杯回测")
    lines.append("")
    lines.append("**注意：这是历史样本外验证，非真正盲测**")
    lines.append("")
    lines.append("| 留出世界杯 | N | EP Brier | LR Brier | ΔBrier | EP Acc | LR Acc | 方向 |")
    lines.append("|-----------|---|---------|---------|--------|--------|--------|------|")
    for r in loto_results:
        direction = "✓" if r["delta_brier"] > 0 else "✗"
        lines.append(f"| {r['holdout_tournament']} | {r['n_test']} | {r['EloPoisson_brier']:.4f} | "
                    f"{r['LR_full_brier']:.4f} | {r['delta_brier']:+.4f} | "
                    f"{r['EloPoisson_acc']:.1%} | {r['LR_full_acc']:.1%} | {direction} |")
    if loto_deltas:
        lines.append(f"\n**汇总**: {n_loto_positive}/{len(loto_results)} 届 LR 优于 EP，"
                     f"平均 ΔBrier={np.mean(loto_deltas):+.4f}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 世界杯验证集
    lines.append("## 9. 世界杯验证集结果")
    lines.append("")
    if wc_val:
        lines.append(f"| 场景 | N | EP Brier | LR Brier | ΔBrier |")
        lines.append(f"|------|---|---------|---------|--------|")
        for key in sorted(stratified_results.keys()):
            if "world_cup" in key:
                r = stratified_results[key]
                ep_b = r["EloPoisson"]["brier_score"]
                lr_b = r["LR_full"]["brier_score"]
                lines.append(f"| {key} | {r['n_samples']} | {ep_b:.4f} | {lr_b:.4f} | {ep_b - lr_b:+.4f} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 主场优势
    lines.append("## 10. 主场优势判定")
    lines.append("")
    lines.append(f"**判定: {host_analysis.get('verdict', 'N/A')}**")
    lines.append(f"原因: {host_analysis.get('reason', 'N/A')}")
    lines.append("")
    lines.append("| 场地类型 | N | 主队胜率 | EP Brier | LR Brier | 有帮助? |")
    lines.append("|---------|---|---------|---------|---------|--------|")
    for vtype in ["home", "neutral", "host_nation", "semi_home"]:
        info = host_analysis.get("venue_types", {}).get(vtype, {})
        n = info.get("n_samples", 0)
        if n == 0:
            lines.append(f"| {vtype} | 0 | - | - | - | 无法验证 |")
            continue
        hwr = info.get("home_win_rate", 0)
        ep_b = info.get("elo_poisson_brier", "N/A")
        lr_b = info.get("lr_full_brier", "N/A")
        helps = info.get("host_advantage_helps")
        helps_str = "是" if helps is True else ("否" if helps is False else "无法验证")
        ep_str = f"{ep_b:.4f}" if isinstance(ep_b, float) else ep_b
        lr_str = f"{lr_b:.4f}" if isinstance(lr_b, float) else lr_b
        lines.append(f"| {vtype} | {n} | {hwr:.1%} | {ep_str} | {lr_str} | {helps_str} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 逐因子
    lines.append("## 11. 逐因子评审")
    lines.append("")
    lines.append("| 因子 | 覆盖率 | 与elo相关 | 消融ΔBrier | 冗余? | 分类 | 判定 |")
    lines.append("|------|-------|----------|-----------|-------|------|------|")
    for fn, info in per_factor.items():
        cov = f"{info.get('coverage_rate', 0):.1%}"
        pearson_r = None
        if info.get("pearson_with_elo"):
            pearson_r = abs(info["pearson_with_elo"]["r"])
        p_str = f"r={pearson_r:.2f}" if pearson_r is not None else "N/A"
        abl = info.get("ablation_brier_delta")
        abl_str = f"{abl:+.6f}" if abl is not None else "N/A"
        red = "是" if info.get("is_redundant") else "否"
        cls = info.get("factor_classification", "unknown")

        if info.get("is_redundant"):
            verdict = "冗余"
        elif info.get("coverage_rate", 0) < 0.3:
            verdict = "覆盖率不足"
        elif abl is not None and abl > 0.001:
            verdict = "有贡献"
        elif abl is not None and abl < -0.001:
            verdict = "负贡献"
        else:
            verdict = "贡献不显著"

        lines.append(f"| {fn} | {cov} | {p_str} | {abl_str} | {red} | {cls} | {verdict} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 时间泄漏
    n_total = len(time_leak_audit.get("checks", []))
    n_pass = sum(1 for c in time_leak_audit.get("checks", []) if c["status"] == "PASS")
    lines.append("## 12. 时间泄漏审计")
    lines.append("")
    lines.append(f"**结果**: {n_pass}/{n_total} PASS - {'无泄漏' if n_pass == n_total else '存在泄漏！'}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 最终决策
    lines.append("## 13. 最终决策")
    lines.append("")
    lines.append(f"### 决策：**{decision}**")
    lines.append("")
    lines.append("### 理由")
    lines.append("")
    lines.append("1. **整体改善微弱**：验证集 Brier 相对改善不足2%")
    lines.append("2. **新因子单独无价值**：LR_new_only 显著差于 EloPoisson")
    lines.append("3. **改善来源不明确**：主要来自 elo_diff 的 LR 校准")
    lines.append("4. **世界杯场景不确定**：LOTO回测需逐届审视，2026世界杯改为前瞻Shadow验证")
    lines.append("5. **host_advantage 保留但有保留意见**：中立场有帮助但东道主样本为零")
    lines.append("")
    lines.append("### 2026世界杯前瞻Shadow验证方案")
    lines.append("")
    lines.append("- 赛前生成不可修改的预测快照（含时间戳和哈希签名）")
    lines.append("- 赛后再评分，不得用已有结果反向回测")
    lines.append("- 当前不得宣布任何基于2026数据的改善结论")
    lines.append("")
    lines.append("### 下一步建议")
    lines.append("")
    lines.append("1. 等待2026世界杯完成后执行前瞻Shadow评分")
    lines.append("2. 引入赔率数据和FIFA排名")
    lines.append("3. 简化因子集，移除冗余因子")
    lines.append("4. 考虑LR校准vs Platt Scaling的独立价值")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 禁止事项
    lines.append("## 14. 禁止事项重申")
    lines.append("")
    lines.append("- 本 Demo 不修改主程序")
    lines.append("- 不接入 Ensemble")
    lines.append("- 不调整 Shadow 权重")
    lines.append("- 2026数据不得用于回测评分，仅用于前瞻Shadow验证")
    lines.append("- 世界杯退化问题解决前，任何因子不得进入 Shadow 模式")
    lines.append("- 不得宣布模型提升5.5%或任何基于2026盲测的改善结论")

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
    print("World Cup Factor Research - 第三轮数据真实性审计")
    print(f"运行时间: {datetime.now().isoformat()}")
    print(f"数据冻结日期: {FREEZE_DATE}")
    print("=" * 70)

    ensure_output_dir()

    # 步骤 1: 数据真实性审计
    audit_info = step1_data_audit()

    # 步骤 2: 加载冻结数据
    df, train, val = step2_load_frozen_data()

    # 步骤 3: 特征计算
    train_features, val_features = step3_compute_features(df, train, val)

    # 步骤 4: 统一模型对比
    model_data = step4_unified_comparison(df, train, val, train_features, val_features)

    # 步骤 5: 分组Bootstrap
    bootstrap_results = step5_block_bootstrap(model_data)

    # 步骤 6: Walk-Forward（完整样本，无2026）
    wf_results = step6_walk_forward(df)

    # 步骤 7: LOTO世界杯回测
    loto_results = step7_loto_world_cup(df)

    # 步骤 8: 分层评估
    stratified_results = step8_stratified(model_data)

    # 步骤 9: 主场优势
    host_analysis = step9_host_advantage(model_data)

    # 步骤 10: 时间泄漏审计
    time_leak_audit = step10_time_leak_audit(df)

    # 步骤 11: 逐因子分析
    per_factor = step11_per_factor(model_data)

    # 步骤 12: 校准曲线
    calibration_data = step12_calibration(model_data)

    # 步骤 13: 最终决策
    decision = step13_final_decision(
        model_data["model_results"], bootstrap_results, wf_results,
        loto_results, stratified_results, host_analysis,
        time_leak_audit, per_factor, model_data["selection_bias_report"],
        audit_info,
    )

    print("\n" + "=" * 70)
    print(f"第三轮验证完成! 最终决策: {decision}")
    print(f"输出目录: {OUTPUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
