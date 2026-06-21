#!/usr/bin/env python3
"""完整因子研究流水线

一键运行所有研究阶段，输出所有研究产物。
用法: python -m scripts.run_full_research [--output-dir OUTPUT_DIR] [--skip-data-load] [--sample-size N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# 添加项目路径
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from scripts.pipeline_utils import (
    # 常量
    DATA_START, TRAIN_START, TRAIN_END, VAL_START, VAL_END, FREEZE_DATE,
    BASELINE_FACTORS, NEW_CANDIDATE_FACTORS, SKIP_FACTORS, ALL_MODEL_FACTORS,
    RANDOM_SEED, WORLD_CUP_YEARS,
    # 日志
    setup_logging,
    # 数据加载
    load_and_prepare_data,
    # Elo + 特征
    compute_elo_and_features,
    # 模型训练与评估
    train_evaluate_model,
    run_baseline_comparison,
    # Bootstrap
    run_bootstrap_comparison,
    # 结果格式化与保存
    format_results_table,
    save_results,
    generate_promotion_decision,
    print_section,
    # 特征合并
    merge_features, get_factor_cols,
    # 模型工具
    predict_elo_baseline, train_lr_model, predict_lr,
    # 内部工具
    eval_to_dict, _json_default,
)

from src.data.loader import load_international_results, filter_by_date, validate_data
from src.features.as_of import compute_all_features
from src.features.calculator import FACTOR_FUNCTIONS
from src.models.baseline import (
    HomeFixedBaseline, FrequencyBaseline,
    EloLogisticBaseline, EloPoissonBaseline,
)
from src.evaluation.metrics import evaluate_predictions, compare_models
from src.evaluation.backtest import (
    time_based_split, rolling_backtest,
    evaluate_by_segment, evaluate_single_factor,
)
from src.evaluation.calibration import reliability_diagram_data
from src.utils.elo_replay import replay_elo_history, EloConfig

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

CSV_PATH = PROJECT_DIR.parent.parent / "data" / "external" / "international_results.csv"


# ============================================================
# Phase 0: 验证研究协议
# ============================================================

def phase_0_validate_protocols():
    """验证研究协议文件是否存在。"""
    print_section("Phase 0: 验证研究协议")

    protocol = PROJECT_DIR / "RESEARCH_PROTOCOL.md"
    registry = PROJECT_DIR / "factor_registry.yaml"
    time_bounds = PROJECT_DIR / "config" / "time_boundaries.yaml"
    leak_fields = PROJECT_DIR / "config" / "leak_forbidden_fields.yaml"

    all_exist = True
    for f in [protocol, registry, time_bounds, leak_fields]:
        if f.exists():
            print(f"  ✓ {f.name}")
        else:
            print(f"  ✗ {f.name} MISSING!")
            all_exist = False

    if all_exist:
        print("  Phase 0 完成：所有协议文件就绪。")
    else:
        print("  Phase 0 完成：部分协议文件缺失，请检查。")
    return all_exist


# ============================================================
# Phase 1: 数据加载、验证、去重
# ============================================================

def phase_1_data_loading(output_dir: Path, start_date: str, end_date: str):
    """数据采集、标准化与去重。"""
    print_section("Phase 1: 数据加载、验证、去重")

    # 加载原始数据
    print("  加载历史比赛数据...")
    df = load_international_results()
    print(f"  全量数据: {len(df)} 场比赛")

    # 过滤 NA 比分（赛程/未赛）
    n_before = len(df)
    df = df[df["result"].notna()].copy()
    print(f"  过滤NA比分: {n_before} → {len(df)} (移除 {n_before - len(df)})")

    # 冻结至指定日期
    df = filter_by_date(df, DATA_START, end_date)
    print(f"  冻结数据 ({DATA_START} ~ {end_date}): {len(df)} 场")

    # 验证数据质量
    report = validate_data(df)
    print(f"  日期范围: {report['date_range']}")
    print(f"  球队数量: {report['unique_teams']}")
    print(f"  重复记录: {report['duplicates']}")

    if report["issues"]:
        print("  数据质量问题:")
        for issue in report["issues"]:
            print(f"    - {issue}")

    # 保存处理后的数据
    output_path = output_dir / "standardized_matches.parquet"
    df.to_parquet(output_path, index=False)
    print(f"  已保存到: {output_path}")

    print("  Phase 1 完成。")
    return df


# ============================================================
# Phase 2: Elo 回放 + 特征计算
# ============================================================

def phase_2_elo_and_features(df: pd.DataFrame, output_dir: Path, sample_size: int | None = None):
    """Elo 回放与时间点特征工程。"""
    print_section("Phase 2: Elo 回放 + 特征计算")

    # 计算 Elo
    print("  计算 Elo 历史...")
    df = replay_elo_history(df, EloConfig())

    # 计算特征
    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}
    print(f"  活跃因子数: {len(active_factors)}")

    target = df
    if sample_size is not None and len(df) > sample_size:
        target = df.sample(sample_size, random_state=RANDOM_SEED)
        print(f"  抽样 {sample_size} 场比赛计算特征...")

    features = compute_all_features(target, df, active_factors, show_progress=True)

    # 覆盖率报告
    print("\n  因子覆盖率:")
    for col in features.columns:
        if col == "match_id":
            continue
        coverage = features[col].notna().mean()
        print(f"    {col}: {coverage:.1%}")

    # 保存特征
    output_path = output_dir / "features.parquet"
    features.to_parquet(output_path, index=False)
    print(f"  已保存到: {output_path}")

    print("  Phase 2 完成。")
    return df, features


# ============================================================
# Phase 3: Baseline 对比（全部 5 个模型）
# ============================================================

def phase_3_baseline_comparison(df: pd.DataFrame, output_dir: Path):
    """建立 Baseline 并对比。"""
    print_section("Phase 3: Baseline 对比")

    # 时间划分
    train, val, test = time_based_split(df, "2018-12-31", "2021-12-31", FREEZE_DATE)
    print(f"  训练集: {len(train)} 场")
    print(f"  验证集: {len(val)} 场")
    print(f"  测试集: {len(test)} 场")

    all_results = {}

    # 1. Home Fixed Baseline
    print("\n  评估 HomeFixed Baseline...")
    model = HomeFixedBaseline.from_data(train)
    preds = []
    for _, m in test.iterrows():
        p = model.predict()
        preds.append([p.home_win, p.draw, p.away_win])
    all_results["HomeFixed"] = evaluate_predictions(np.array(preds), test["result"].values)

    # 2. Frequency Baseline
    print("  评估 Frequency Baseline...")
    freq_model = FrequencyBaseline().fit(train)
    preds = []
    for _, m in test.iterrows():
        p = freq_model.predict(m)
        preds.append([p.home_win, p.draw, p.away_win])
    all_results["Frequency"] = evaluate_predictions(np.array(preds), test["result"].values)

    # 3. Elo Logistic Baseline
    print("  评估 EloLogistic Baseline...")
    elo_model = EloLogisticBaseline()
    preds = []
    for _, m in test.iterrows():
        p = elo_model.predict(
            elo_home=m["pre_match_elo_home"],
            elo_away=m["pre_match_elo_away"],
            is_neutral=m["is_neutral"],
        )
        preds.append([p.home_win, p.draw, p.away_win])
    all_results["EloLogistic"] = evaluate_predictions(np.array(preds), test["result"].values)

    # 4. Elo + Poisson Baseline
    print("  评估 EloPoisson Baseline...")
    poisson_model = EloPoissonBaseline()
    preds = []
    for _, m in test.iterrows():
        p = poisson_model.predict(
            elo_home=m["pre_match_elo_home"],
            elo_away=m["pre_match_elo_away"],
            is_neutral=m["is_neutral"],
        )
        preds.append([p.home_win, p.draw, p.away_win])
    all_results["EloPoisson"] = evaluate_predictions(np.array(preds), test["result"].values)

    # 打印对比结果
    print("\n" + compare_models(all_results))

    # 保存结果
    save_results(all_results, output_dir, "baseline_results.json")

    # 分片评估
    print("\n  按赛事类别评估 (EloPoisson):")
    elo_preds = []
    for _, m in test.iterrows():
        p = poisson_model.predict(
            elo_home=m["pre_match_elo_home"],
            elo_away=m["pre_match_elo_away"],
            is_neutral=m["is_neutral"],
        )
        elo_preds.append([p.home_win, p.draw, p.away_win])

    segment_results = evaluate_by_segment(test, np.array(elo_preds), "tournament_category")
    for seg, r in segment_results.items():
        print(f"    {seg}: {r}")

    print("  Phase 3 完成。")
    return all_results


# ============================================================
# Phase 4: 单因子分析
# ============================================================

def phase_4_single_factor_analysis(df: pd.DataFrame, output_dir: Path):
    """单因子研究：覆盖率、方向稳定性、分箱胜率、Brier。"""
    print_section("Phase 4: 单因子分析")

    val = filter_by_date(df, VAL_START, VAL_END)
    if len(val) == 0:
        print("  验证集为空，跳过。")
        return

    # 抽样计算特征
    sample_size = min(1000, len(val))
    sample = val.sample(sample_size, random_state=RANDOM_SEED)

    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}
    features = compute_all_features(sample, df, active_factors, show_progress=True)

    # 单因子分析
    print("\n  单因子分析:")
    factor_report = []

    for col in features.columns:
        if col == "match_id":
            continue

        values = features[col].values
        result = evaluate_single_factor(sample, col, values)
        factor_report.append(result)

        coverage = result["coverage"]
        print(f"    {col}: coverage={coverage:.1%}, mean={result['mean']:.3f}, std={result['std']:.3f}")

    # 保存报告
    save_results(factor_report, output_dir, "single_factor_report.json")
    print("  Phase 4 完成。")
    return factor_report


# ============================================================
# Phase 5: 组合模型 + 消融实验
# ============================================================

def phase_5_combined_models(df: pd.DataFrame, output_dir: Path):
    """组合模型与消融实验。"""
    print_section("Phase 5: 组合模型与消融实验")

    # 准备数据
    train = filter_by_date(df, TRAIN_START, TRAIN_END)
    val = filter_by_date(df, VAL_START, VAL_END)

    if len(train) == 0 or len(val) == 0:
        print("  数据不足，跳过。")
        return

    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}

    # 计算特征
    print("  计算训练集特征...")
    train_features = compute_all_features(train, df, active_factors, show_progress=True)
    print("  计算验证集特征...")
    val_features = compute_all_features(val, df, active_factors, show_progress=True)

    # 合并
    train_merged, all_feature_cols = merge_features(train, train_features)
    val_merged, _ = merge_features(val, val_features)

    baseline_cols = get_factor_cols(train_merged, BASELINE_FACTORS)
    new_cols = get_factor_cols(train_merged, NEW_CANDIDATE_FACTORS)
    full_cols = baseline_cols + new_cols

    # 过滤无 Elo 的验证集
    val_has_elo = val_merged["pre_match_elo_home"].notna() & val_merged["pre_match_elo_away"].notna()
    val_eval = val_merged[val_has_elo]
    val_labels = val_eval["result"].values

    y_train = train_merged["result"].map({"H": 0, "D": 1, "A": 2}).values

    # ---- 模型训练与预测 ----
    model_results = {}
    model_preds = {}

    # EloPoisson
    print("  训练/评估 EloPoisson...")
    elo_poisson = EloPoissonBaseline()
    val_preds_ep, _ = predict_elo_baseline(elo_poisson, val_eval)
    model_preds["EloPoisson"] = val_preds_ep
    model_results["EloPoisson"] = {"validation": eval_to_dict(evaluate_predictions(val_preds_ep, val_labels))}

    # EloLogistic
    print("  训练/评估 EloLogistic...")
    elo_logistic = EloLogisticBaseline()
    val_preds_el, _ = predict_elo_baseline(elo_logistic, val_eval)
    model_preds["EloLogistic"] = val_preds_el
    model_results["EloLogistic"] = {"validation": eval_to_dict(evaluate_predictions(val_preds_el, val_labels))}

    # LR(elo_diff only)
    print("  训练/评估 LR(elo_diff only)...")
    X_train_base = train_merged[baseline_cols].fillna(0).values
    lr_base, scaler_base = train_lr_model(X_train_base, y_train)
    val_preds_lr_base = predict_lr(lr_base, scaler_base, val_eval[baseline_cols].fillna(0).values)
    model_preds["LR_elo_only"] = val_preds_lr_base
    model_results["LR_elo_only"] = {"validation": eval_to_dict(evaluate_predictions(val_preds_lr_base, val_labels))}

    # LR(new factors only)
    print("  训练/评估 LR(new factors only)...")
    X_train_new = train_merged[new_cols].fillna(0).values
    lr_new, scaler_new = train_lr_model(X_train_new, y_train)
    val_preds_lr_new = predict_lr(lr_new, scaler_new, val_eval[new_cols].fillna(0).values)
    model_preds["LR_new_only"] = val_preds_lr_new
    model_results["LR_new_only"] = {"validation": eval_to_dict(evaluate_predictions(val_preds_lr_new, val_labels))}

    # LR(elo_diff + new factors)
    print("  训练/评估 LR(elo_diff + new factors)...")
    X_train_full = train_merged[full_cols].fillna(0).values
    lr_full, scaler_full = train_lr_model(X_train_full, y_train)
    val_preds_lr_full = predict_lr(lr_full, scaler_full, val_eval[full_cols].fillna(0).values)
    model_preds["LR_full"] = val_preds_lr_full
    model_results["LR_full"] = {"validation": eval_to_dict(evaluate_predictions(val_preds_lr_full, val_labels))}

    # 打印结果
    for name, r in model_results.items():
        v = r["validation"]
        print(f"    {name}: Brier={v['brier_score']:.4f}, LogLoss={v['log_loss']:.4f}, "
              f"Acc={v['accuracy']:.1%}, ECE={v['ece']:.4f}")

    save_results(model_results, output_dir, "unified_comparison_table.json")

    # 消融实验
    print("\n  消融实验 (逐个移除因子):")
    ablation_results = {"full_model": model_results["LR_full"]["validation"]["brier_score"]}

    for col in full_cols:
        remaining_cols = [c for c in full_cols if c != col]
        X_train_abl = train_merged[remaining_cols].fillna(0).values
        X_val_abl = val_eval[remaining_cols].fillna(0).values

        lr_abl, sc_abl = train_lr_model(X_train_abl, y_train)
        abl_preds = predict_lr(lr_abl, sc_abl, X_val_abl)
        abl_ev = evaluate_predictions(abl_preds, val_labels)

        delta = abl_ev.brier_score - model_results["LR_full"]["validation"]["brier_score"]
        ablation_results[col] = {
            "brier_without": abl_ev.brier_score,
            "delta": delta,
            "direction": "worse" if delta > 0 else "better",
        }
        print(f"    -{col}: Brier={abl_ev.brier_score:.4f} (Δ={delta:+.4f})")

    save_results(ablation_results, output_dir, "ablation_results.json")
    print("  Phase 5 完成。")

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
    }


# ============================================================
# Phase 6: Walk-Forward 验证
# ============================================================

def phase_6_walk_forward(df: pd.DataFrame, output_dir: Path):
    """嵌套 Walk-Forward 验证。"""
    print_section("Phase 6: Walk-Forward 验证")

    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}
    wf_results = []

    for test_year in range(2019, 2026):
        train_end_year = test_year - 1
        test_start = f"{test_year}-01-01"
        test_end = f"{test_year}-12-31"

        test = filter_by_date(df, test_start, test_end)
        test_elo = test[test["pre_match_elo_home"].notna() & test["pre_match_elo_away"].notna()]
        if len(test_elo) < 10:
            continue

        # EloPoisson baseline
        elo_poisson = EloPoissonBaseline()
        ep_preds, _ = predict_elo_baseline(elo_poisson, test_elo)
        ep_ev = evaluate_predictions(ep_preds, test_elo["result"].values)

        # LR_full
        try:
            train_all = filter_by_date(df, TRAIN_START, f"{train_end_year}-12-31")
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

    save_results(wf_results, output_dir, "walk_forward_results.json")
    print("  Phase 6 完成。")
    return wf_results


# ============================================================
# Phase 7: 按世界杯届次的时间递进回测
# ============================================================

def phase_7_chronological_wc_backtest(df: pd.DataFrame, output_dir: Path):
    """严格时间递进世界杯回测。"""
    print_section("Phase 7: 严格时间递进世界杯回测")

    active_factors = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}
    wc_results = []

    for wc_year in WORLD_CUP_YEARS:
        print(f"\n  --- {wc_year} 世界杯 ---")

        train_end = f"{wc_year - 1}-12-31"
        train_all = filter_by_date(df, DATA_START, train_end)
        print(f"  训练集: {DATA_START} ~ {train_end}, N={len(train_all)}")

        wc_year_start = f"{wc_year}-01-01"
        wc_year_end = f"{wc_year}-12-31"
        wc_year_data = filter_by_date(df, wc_year_start, wc_year_end)
        wc_matches = wc_year_data[wc_year_data["tournament"] == "FIFA World Cup"]
        wc_with_elo = wc_matches[wc_matches["pre_match_elo_home"].notna() & wc_matches["pre_match_elo_away"].notna()]

        # 64场校验
        EXPECTED_WC_MATCHES = 64
        if len(wc_matches) != EXPECTED_WC_MATCHES:
            print(f"  [异常] {wc_year} 世界杯正赛 {len(wc_matches)} 场，期望 {EXPECTED_WC_MATCHES} 场！")
            wc_results.append({
                "world_cup_year": wc_year,
                "n_test": len(wc_matches),
                "error": f"expected_{EXPECTED_WC_MATCHES}_got_{len(wc_matches)}",
                "validation_type": "strict_chronological",
            })
            continue

        if len(wc_with_elo) < 5:
            print(f"  {wc_year} 世界杯样本不足: {len(wc_with_elo)}")
            continue

        print(f"  测试集: {wc_year} 世界杯, N={len(wc_with_elo)}")

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
            y_train_wc = train_m["result"].map({"H": 0, "D": 1, "A": 2}).values

            lr, sc = train_lr_model(train_m[valid_cols].fillna(0).values, y_train_wc, C=1.0)
            lr_preds = predict_lr(lr, sc, test_m[valid_cols].fillna(0).values)
            lr_ev = evaluate_predictions(lr_preds, test_m["result"].values)

            delta = ep_ev.brier_score - lr_ev.brier_score
            rel_change = delta / ep_ev.brier_score * 100 if ep_ev.brier_score > 0 else 0

            wc_results.append({
                "world_cup_year": wc_year,
                "n_test": len(wc_with_elo),
                "train_end": train_end,
                "EloPoisson_brier": ep_ev.brier_score,
                "LR_full_brier": lr_ev.brier_score,
                "delta_brier": delta,
                "relative_change_pct": rel_change,
                "EloPoisson_logloss": ep_ev.log_loss,
                "LR_full_logloss": lr_ev.log_loss,
                "EloPoisson_ece": ep_ev.ece,
                "LR_full_ece": lr_ev.ece,
                "EloPoisson_acc": ep_ev.accuracy,
                "LR_full_acc": lr_ev.accuracy,
                "validation_type": "strict_chronological",
                "data_used": f"only_matches_before_{wc_year}",
            })
            print(f"  EP Brier={ep_ev.brier_score:.4f}, LR Brier={lr_ev.brier_score:.4f}, "
                  f"Δ={delta:+.4f} ({rel_change:+.1f}%)")
        except Exception as e:
            print(f"  {wc_year} 失败: {e}")

    if wc_results:
        valid_wc = [r for r in wc_results if "error" not in r]
        if valid_wc:
            deltas = [r["delta_brier"] for r in valid_wc]
            n_pos = sum(1 for d in deltas if d > 0)
            print(f"\n  汇总: {len(valid_wc)} 届, 平均ΔBrier={np.mean(deltas):+.4f}, "
                  f"LR优于EP: {n_pos}/{len(valid_wc)}")

    save_results(wc_results, output_dir, "chronological_worldcup_backtest.json")
    print("  Phase 7 完成。")
    return wc_results


# ============================================================
# Phase 8: 概率校准
# ============================================================

def phase_8_calibration(model_data: dict, output_dir: Path):
    """概率校准曲线。"""
    print_section("Phase 8: 概率校准")

    val_labels = model_data["val_labels"]
    ep_val = model_data["model_preds"]["EloPoisson"]
    lr_val = model_data["model_preds"]["LR_full"]

    print("  计算 EloPoisson 校准曲线...")
    ep_cal = reliability_diagram_data(ep_val, val_labels, n_bins=10)

    print("  计算 LR_full 校准曲线...")
    lr_cal = reliability_diagram_data(lr_val, val_labels, n_bins=10)

    calibration_data = {"EloPoisson": ep_cal, "LR_full": lr_cal}

    for model_name, cal in calibration_data.items():
        print(f"\n  {model_name} 校准:")
        for outcome, data in cal.items():
            centers = data["bin_centers"]
            freqs = data["actual_frequencies"]
            counts = data["counts"]
            print(f"    {outcome}:")
            for c, f, n in zip(centers, freqs, counts):
                print(f"      pred={c:.2f} -> actual={f:.3f} (N={n})")

    save_results(calibration_data, output_dir, "calibration_data.json")
    print("  Phase 8 完成。")
    return calibration_data


# ============================================================
# Phase 9: 分层评估
# ============================================================

def phase_9_stratified(model_data: dict, output_dir: Path):
    """按年份、赛事、场地等维度分层评估。"""
    print_section("Phase 9: 分层评估")

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

    # 打印关键结果
    print("  关键分层结果:")
    for key in sorted(stratified_results.keys()):
        r = stratified_results[key]
        ep_b = r["EloPoisson"]["brier_score"]
        lr_b = r["LR_full"]["brier_score"]
        print(f"    {key}: N={r['n_samples']}, EP={ep_b:.4f}, LR={lr_b:.4f}, Δ={ep_b - lr_b:+.4f}")

    save_results(stratified_results, output_dir, "stratified_results.json")
    print("  Phase 9 完成。")
    return stratified_results


# ============================================================
# Phase 10: 因子准入决策
# ============================================================

def phase_10_promotion_decision(
    model_data: dict,
    bootstrap_results: dict,
    wf_results: list,
    wc_results: list,
    stratified_results: dict,
    output_dir: Path,
):
    """因子准入评审与决策。"""
    print_section("Phase 10: 因子准入决策")

    model_results = model_data["model_results"]
    val_labels = model_data["val_labels"]
    val_merged = model_data["val_merged"]
    train_merged = model_data["train_merged"]
    full_cols = model_data["full_cols"]
    y_train = model_data["y_train"]
    lr_full = model_data["lr_full"]
    scaler_full = model_data["scaler_full"]

    # ---- 逐因子分析 ----
    print("  逐因子分析...")
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

        # 与 elo_diff 相关性
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
        print(f"    {factor_name}: cov={cov_str}, pearson={p_str}, ablation={abl}, {red}")

    save_results(per_factor, output_dir, "per_factor_analysis.json")

    # ---- Bootstrap 对比 ----
    print("\n  Bootstrap 对比 (LR_full vs EloPoisson)...")
    ep_preds = model_data["model_preds"]["EloPoisson"]
    lr_preds = model_data["model_preds"]["LR_full"]
    bootstrap_result = run_bootstrap_comparison(ep_preds, val_labels, lr_preds, n_bootstrap=5000)
    print(f"    ΔBrier={bootstrap_result['mean_brier_diff']:+.5f}, "
          f"95%CI=[{bootstrap_result['ci_95_low']:.5f}, {bootstrap_result['ci_95_high']:.5f}], "
          f"显著={bootstrap_result['significant']}")

    save_results(bootstrap_result, output_dir, "bootstrap_comparison.json")

    # ---- 准入决策 ----
    baseline_brier = model_results["EloPoisson"]["validation"]["brier_score"]
    decisions = generate_promotion_decision(per_factor, baseline_brier, threshold=0.02)

    print("\n  准入评审结果:")
    for name, info in decisions.items():
        print(f"    {name}: {info['decision']} - {info['reason']}")

    save_results(decisions, output_dir, "factor_candidates.json")

    # ---- 生成准入报告 ----
    _generate_promotion_md(
        model_results, bootstrap_result, wf_results, wc_results,
        stratified_results, per_factor, decisions, output_dir,
    )

    print("  Phase 10 完成。")
    return {
        "per_factor": per_factor,
        "bootstrap_result": bootstrap_result,
        "decisions": decisions,
    }


def _generate_promotion_md(
    model_results, bootstrap_result, wf_results, wc_results,
    stratified_results, per_factor, decisions, output_dir,
):
    """生成 PROMOTION_DECISION.md 报告。"""
    ep_val_brier = model_results["EloPoisson"]["validation"]["brier_score"]
    lr_val_brier = model_results["LR_full"]["validation"]["brier_score"]
    val_improvement = (ep_val_brier - lr_val_brier) / ep_val_brier * 100

    wf_deltas = [w["delta_brier"] for w in wf_results] if wf_results else []
    n_wf_positive = sum(1 for d in wf_deltas if d > 0) if wf_deltas else 0

    valid_wc = [r for r in wc_results if "error" not in r]
    wc_deltas = [r["delta_brier"] for r in valid_wc] if valid_wc else []
    n_wc_positive = sum(1 for d in wc_deltas if d > 0) if wc_deltas else 0

    # 决策逻辑
    lr_full_significant = bootstrap_result.get("significant", False)
    lr_full_positive = bootstrap_result.get("mean_brier_diff", 0) > 0

    if len(wf_results) >= 3:
        wf_mean = np.mean(wf_deltas)
        n_wf_pos = sum(1 for d in wf_deltas if d > 0)
        stable_across_windows = n_wf_pos / len(wf_results) >= 0.75 and wf_mean > 0
    else:
        stable_across_windows = False

    wc_no_degradation = True
    for key in stratified_results:
        if "world_cup_only" in key:
            r = stratified_results[key]
            ep_b = r["EloPoisson"]["brier_score"]
            lr_b = r["LR_full"]["brier_score"]
            if lr_b > ep_b + 0.005:
                wc_no_degradation = False
                break

    if lr_full_significant and lr_full_positive and stable_across_windows and wc_no_degradation:
        decision = "PASS_SHADOW"
    elif (lr_full_significant and lr_full_positive) or (stable_across_windows and wc_no_degradation):
        decision = "NEEDS_MORE_DATA"
    else:
        decision = "REJECTED"

    lines = []
    lines.append("# 因子准入评审报告 - 完整研究流水线")
    lines.append("")
    lines.append(f"**评审时间**: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**最终结论**: **{decision}**")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 1. 执行摘要")
    lines.append("")
    lines.append(f"LR_full 相对 EloPoisson 验证集 Brier 改善 {val_improvement:.1f}%。"
                 f"Walk-Forward {n_wf_positive}/{len(wf_results)} 窗口优于 Baseline。"
                 f"世界杯严格时间回测 {n_wc_positive}/{len(valid_wc)} 届优于 Baseline。")
    lines.append("")

    lines.append("## 2. Baseline 性能")
    lines.append("")
    lines.append("| 模型 | 验证集 Brier | 验证集 LogLoss | 验证集 Acc | 验证集 ECE |")
    lines.append("|------|-------------|---------------|-----------|-----------|")
    for name in ["EloPoisson", "EloLogistic", "LR_elo_only"]:
        if name in model_results:
            v = model_results[name]["validation"]
            lines.append(f"| {name} | {v['brier_score']:.4f} | {v['log_loss']:.4f} | {v['accuracy']:.1%} | {v['ece']:.4f} |")
    lines.append("")

    lines.append("## 3. 新因子模型性能")
    lines.append("")
    lines.append("| 模型 | 验证集 Brier | 相对EP改善 |")
    lines.append("|------|-------------|-----------|")
    for name in ["LR_new_only", "LR_full"]:
        if name in model_results:
            v = model_results[name]["validation"]
            imp = (ep_val_brier - v["brier_score"]) / ep_val_brier * 100
            lines.append(f"| {name} | {v['brier_score']:.4f} | {imp:+.2f}% |")
    lines.append("")

    lines.append("## 4. Bootstrap 置信区间")
    lines.append("")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| ΔBrier 均值 | {bootstrap_result['mean_brier_diff']:+.5f} |")
    lines.append(f"| 95% CI | [{bootstrap_result['ci_95_low']:.5f}, {bootstrap_result['ci_95_high']:.5f}] |")
    lines.append(f"| 显著 | {'是' if bootstrap_result['significant'] else '否'} |")
    lines.append("")

    lines.append("## 5. Walk-Forward 验证")
    lines.append("")
    lines.append("| 窗口 | N | EP Brier | LR Brier | ΔBrier |")
    lines.append("|------|---|---------|---------|--------|")
    for w in wf_results:
        lines.append(f"| {w['test_period']} | {w['n_test']} | {w['EloPoisson_brier']:.4f} | "
                     f"{w['LR_full_brier']:.4f} | {w['delta_brier']:+.4f} |")
    if wf_deltas:
        lines.append(f"\n**汇总**: {n_wf_positive}/{len(wf_results)} 窗口 LR 优于 EP，"
                     f"平均ΔBrier={np.mean(wf_deltas):+.4f}")
    lines.append("")

    lines.append("## 6. 世界杯严格时间回测")
    lines.append("")
    lines.append("| 世界杯 | N | EP Brier | LR Brier | ΔBrier | 相对变化 | 方向 |")
    lines.append("|--------|---|---------|---------|--------|---------|------|")
    for r in wc_results:
        if "error" in r:
            lines.append(f"| {r['world_cup_year']} | {r['n_test']} | 异常: {r['error']} | | | | |")
        else:
            direction = "✓" if r["delta_brier"] > 0 else "✗"
            lines.append(f"| {r['world_cup_year']} | {r['n_test']} | {r['EloPoisson_brier']:.4f} | "
                        f"{r['LR_full_brier']:.4f} | {r['delta_brier']:+.4f} | "
                        f"{r['relative_change_pct']:+.1f}% | {direction} |")
    if wc_deltas:
        lines.append(f"\n**汇总**: {n_wc_positive}/{len(valid_wc)} 届 LR 优于 EP，"
                     f"平均ΔBrier={np.mean(wc_deltas):+.4f}")
    lines.append("")

    lines.append("## 7. 逐因子评审")
    lines.append("")
    lines.append("| 因子 | 覆盖率 | 与elo相关 | 消融ΔBrier | 冗余? | 判定 |")
    lines.append("|------|-------|----------|-----------|-------|------|")
    for fn, info in per_factor.items():
        cov = f"{info.get('coverage_rate', 0):.1%}"
        pearson_r = None
        if info.get("pearson_with_elo"):
            pearson_r = abs(info["pearson_with_elo"]["r"])
        p_str = f"r={pearson_r:.2f}" if pearson_r is not None else "N/A"
        abl = info.get("ablation_brier_delta")
        abl_str = f"{abl:+.6f}" if abl is not None else "N/A"
        red = "是" if info.get("is_redundant") else "否"
        dec = decisions.get(fn, {}).get("decision", "unknown")
        lines.append(f"| {fn} | {cov} | {p_str} | {abl_str} | {red} | {dec} |")
    lines.append("")

    lines.append("## 8. 决策推理")
    lines.append("")
    lines.append(f"1. LR_full 显著优于EP: **{'是' if lr_full_significant and lr_full_positive else '否'}**")
    lines.append(f"2. Walk-Forward 稳定: **{'是' if stable_across_windows else '否'}** (LR优于EP窗口: {n_wf_positive}/{len(wf_results)})")
    lines.append(f"3. 世界杯无退化: **{'是' if wc_no_degradation else '否'}**")
    lines.append(f"\n综合决策: **{decision}**")
    lines.append("")

    lines.append("## 9. 禁止事项")
    lines.append("")
    lines.append("- 本 Demo 不修改主程序")
    lines.append("- 不接入 Ensemble")
    lines.append("- 不调整 Shadow 权重")
    lines.append("- 2026数据不得用于回测评分")
    lines.append("- 世界杯退化问题解决前，任何因子不得进入 Shadow 模式")

    md_path = output_dir / "PROMOTION_DECISION.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  已保存: {md_path}")


# ============================================================
# Phase 11: 生成所有输出产物
# ============================================================

def phase_11_generate_artifacts(
    df: pd.DataFrame,
    model_data: dict,
    bootstrap_result: dict,
    wf_results: list,
    wc_results: list,
    stratified_results: dict,
    per_factor: dict,
    decisions: dict,
    output_dir: Path,
):
    """生成所有输出产物摘要。"""
    print_section("Phase 11: 生成输出产物摘要")

    summary = {
        "run_time": datetime.now().isoformat(),
        "data_size": len(df),
        "train_size": len(filter_by_date(df, TRAIN_START, TRAIN_END)),
        "val_size": len(filter_by_date(df, VAL_START, VAL_END)),
        "n_factors": len(NEW_CANDIDATE_FACTORS),
        "n_candidates": sum(1 for d in decisions.values() if d["decision"] == "candidate"),
        "n_rejected": sum(1 for d in decisions.values() if d["decision"] == "rejected"),
        "n_needs_more_data": sum(1 for d in decisions.values() if d["decision"] == "needs_more_data"),
        "bootstrap_significant": bootstrap_result.get("significant", False),
        "wf_windows": len(wf_results),
        "wf_positive": sum(1 for w in wf_results if w["delta_brier"] > 0) if wf_results else 0,
        "wc_editions": len([r for r in wc_results if "error" not in r]),
        "wc_positive": sum(1 for r in wc_results if r.get("delta_brier", 0) > 0),
        "output_dir": str(output_dir),
    }

    save_results(summary, output_dir, "run_summary.json")

    # 列出所有产物
    print("\n  输出产物:")
    for f in sorted(output_dir.iterdir()):
        if f.is_file():
            size = f.stat().st_size
            print(f"    {f.name} ({size:,} bytes)")

    print("  Phase 11 完成。")
    return summary


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="完整因子研究流水线")
    parser.add_argument("--output-dir", type=str, default="outputs/full_research",
                        help="输出目录 (默认: outputs/full_research)")
    parser.add_argument("--skip-data-load", action="store_true",
                        help="跳过数据加载（如果已处理数据存在）")
    parser.add_argument("--sample-size", type=int, default=None,
                        help="限制样本量以快速测试")
    parser.add_argument("--start-date", type=str, default="2010-01-01",
                        help="起始日期过滤 (默认: 2010-01-01)")
    parser.add_argument("--end-date", type=str, default="2025-12-31",
                        help="结束日期过滤 (默认: 2025-12-31)")
    args = parser.parse_args()

    output_dir = PROJECT_DIR / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logging(output_dir)

    print("=" * 70)
    print("完整因子研究流水线")
    print(f"运行时间: {datetime.now().isoformat()}")
    print(f"输出目录: {output_dir}")
    print("=" * 70)

    # Phase 0
    phase_0_validate_protocols()

    # Phase 1
    df = None
    if not args.skip_data_load:
        df = phase_1_data_loading(output_dir, args.start_date, args.end_date)
    else:
        parquet_path = output_dir / "standardized_matches.parquet"
        if parquet_path.exists():
            print("\n  跳过数据加载，从缓存读取...")
            df = pd.read_parquet(parquet_path)
        else:
            print("\n  缓存不存在，重新加载数据...")
            df = phase_1_data_loading(output_dir, args.start_date, args.end_date)

    if df is None:
        print("  数据加载失败，终止流水线。")
        return

    # Phase 2
    df, features = phase_2_elo_and_features(df, output_dir, args.sample_size)

    # Phase 3
    baseline_results = phase_3_baseline_comparison(df, output_dir)

    # Phase 4
    factor_report = phase_4_single_factor_analysis(df, output_dir)

    # Phase 5
    model_data = phase_5_combined_models(df, output_dir)

    # Phase 6
    wf_results = phase_6_walk_forward(df, output_dir)

    # Phase 7
    wc_results = phase_7_chronological_wc_backtest(df, output_dir)

    # Phase 8
    calibration_data = phase_8_calibration(model_data, output_dir)

    # Phase 9
    stratified_results = phase_9_stratified(model_data, output_dir)

    # Phase 10
    promotion = phase_10_promotion_decision(
        model_data, {}, wf_results, wc_results,
        stratified_results, output_dir,
    )

    # Phase 11
    phase_11_generate_artifacts(
        df, model_data, promotion["bootstrap_result"],
        wf_results, wc_results, stratified_results,
        promotion["per_factor"], promotion["decisions"],
        output_dir,
    )

    print("\n" + "=" * 70)
    print("完整研究流水线执行完毕！")
    print(f"输出目录: {output_dir}")
    print("=" * 70)


if __name__ == "__main__":
    main()
