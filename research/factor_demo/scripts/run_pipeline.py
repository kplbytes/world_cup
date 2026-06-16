#!/usr/bin/env python3
"""一键运行完整研究流水线

使用方法:
    cd research/factor_demo
    python scripts/run_pipeline.py [--phase 0-7] [--skip-baseline]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# 添加项目路径
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import load_international_results, filter_by_date, validate_data
from src.features.as_of import compute_all_features
from src.features.calculator import FACTOR_FUNCTIONS
from src.models.baseline import (
    HomeFixedBaseline,
    FrequencyBaseline,
    EloLogisticBaseline,
    EloPoissonBaseline,
    MarketImpliedBaseline,
)
from src.evaluation.metrics import evaluate_predictions, compare_models
from src.evaluation.backtest import (
    time_based_split,
    rolling_backtest,
    evaluate_by_segment,
    evaluate_single_factor,
)
from src.evaluation.calibration import reliability_diagram_data
from src.utils.elo_replay import replay_elo_history


OUTPUTS_DIR = PROJECT_DIR / "outputs"


def phase_0():
    """Phase 0: 验证研究协议。"""
    print("\n" + "=" * 60)
    print("Phase 0: 验证研究协议")
    print("=" * 60)

    protocol = PROJECT_DIR / "RESEARCH_PROTOCOL.md"
    registry = PROJECT_DIR / "factor_registry.yaml"
    time_bounds = PROJECT_DIR / "config" / "time_boundaries.yaml"
    leak_fields = PROJECT_DIR / "config" / "leak_forbidden_fields.yaml"

    for f in [protocol, registry, time_bounds, leak_fields]:
        if f.exists():
            print(f"  ✓ {f.name}")
        else:
            print(f"  ✗ {f.name} MISSING!")

    print("  Phase 0 完成。")


def phase_1():
    """Phase 1: 数据采集与标准化。"""
    print("\n" + "=" * 60)
    print("Phase 1: 数据采集与标准化")
    print("=" * 60)

    # 加载数据
    print("  加载历史比赛数据...")
    df = load_international_results()

    # 过滤到研究时间范围
    df = filter_by_date(df, "2000-01-01", "2025-12-31")
    print(f"  2000-2025 比赛总数: {len(df)}")

    # 验证数据质量
    report = validate_data(df)
    print(f"  日期范围: {report['date_range']}")
    print(f"  球队数量: {report['unique_teams']}")
    print(f"  重复记录: {report['duplicates']}")
    print(f"  非法比分: {report['invalid_scores']}")
    print(f"  洲际覆盖率: {report['confederation_coverage']:.1%}")

    if report["issues"]:
        print("  数据质量问题:")
        for issue in report["issues"]:
            print(f"    - {issue}")

    # 保存处理后的数据
    output_path = OUTPUTS_DIR / "standardized_matches.parquet"
    df.to_parquet(output_path, index=False)
    print(f"  已保存到: {output_path}")

    print("  Phase 1 完成。")
    return df


def phase_2(df: pd.DataFrame):
    """Phase 2: 时间点特征工程。"""
    print("\n" + "=" * 60)
    print("Phase 2: 时间点特征工程")
    print("=" * 60)

    # 计算 Elo
    print("  计算 Elo 历史...")
    df = replay_elo_history(df)

    # 只对 2010 年后的比赛计算特征
    df_2010 = filter_by_date(df, "2010-01-01", "2025-12-31")
    print(f"  计算 2010-2025 特征 (共 {len(df_2010)} 场比赛)...")

    # 抽样计算（完整计算可能很慢）
    sample_size = min(2000, len(df_2010))
    sample = df_2010.sample(sample_size, random_state=42)

    features = compute_all_features(sample, df, FACTOR_FUNCTIONS)

    # 覆盖率报告
    print("\n  因子覆盖率:")
    for col in features.columns:
        if col == "match_id":
            continue
        coverage = features[col].notna().mean()
        print(f"    {col}: {coverage:.1%}")

    # 保存特征
    output_path = OUTPUTS_DIR / "features_sample.parquet"
    features.to_parquet(output_path, index=False)
    print(f"  已保存到: {output_path}")

    print("  Phase 2 完成。")
    return df, features


def phase_3(df: pd.DataFrame):
    """Phase 3: Baseline 建立。"""
    print("\n" + "=" * 60)
    print("Phase 3: Baseline 建立")
    print("=" * 60)

    # 时间划分
    train, val, test = time_based_split(df, "2018-12-31", "2021-12-31", "2025-12-31")
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
    results_path = OUTPUTS_DIR / "baseline_results.json"
    serializable = {}
    for name, r in all_results.items():
        serializable[name] = {
            "brier_score": r.brier_score,
            "brier_home": r.brier_home,
            "brier_draw": r.brier_draw,
            "brier_away": r.brier_away,
            "log_loss": r.log_loss,
            "accuracy": r.accuracy,
            "ece": r.ece,
            "n_samples": r.n_samples,
        }
    with open(results_path, "w") as f:
        json.dump(serializable, f, indent=2)
    print(f"\n  结果已保存到: {results_path}")

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

    print("\n  Phase 3 完成。")
    return all_results


def phase_4(df: pd.DataFrame):
    """Phase 4: 单因子研究。"""
    print("\n" + "=" * 60)
    print("Phase 4: 单因子研究")
    print("=" * 60)

    # 在验证集上评估
    val = filter_by_date(df, "2019-01-01", "2021-12-31")

    if len(val) == 0:
        print("  验证集为空，跳过。")
        return

    # 抽样计算特征
    sample_size = min(1000, len(val))
    sample = val.sample(sample_size, random_state=42)

    features = compute_all_features(sample, df, FACTOR_FUNCTIONS)

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
    report_path = OUTPUTS_DIR / "single_factor_report.json"
    with open(report_path, "w") as f:
        json.dump(factor_report, f, indent=2, default=str)
    print(f"\n  报告已保存到: {report_path}")

    print("  Phase 4 完成。")


def phase_5(df: pd.DataFrame):
    """Phase 5: 组合模型与消融实验。"""
    print("\n" + "=" * 60)
    print("Phase 5: 组合模型与消融实验")
    print("=" * 60)

    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("  scikit-learn 未安装，跳过。")
        return

    # 准备数据
    train = filter_by_date(df, "2010-01-01", "2018-12-31")
    val = filter_by_date(df, "2019-01-01", "2021-12-31")

    if len(train) == 0 or len(val) == 0:
        print("  数据不足，跳过。")
        return

    # 计算特征
    print("  计算训练集特征...")
    train_features = compute_all_features(train, df, FACTOR_FUNCTIONS)
    print("  计算验证集特征...")
    val_features = compute_all_features(val, df, FACTOR_FUNCTIONS)

    # 合并 - 处理重名列
    train_merged = train.merge(train_features, on="match_id", how="left", suffixes=("_match", "_feat"))
    val_merged = val.merge(val_features, on="match_id", how="left", suffixes=("_match", "_feat"))
    
    # 特征列：优先使用 _feat 版本（来自因子计算），回退到 _match 版本（注入因子）
    feature_cols = []
    for c in train_features.columns:
        if c == "match_id":
            continue
        feat_col = f"{c}_feat"
        match_col = f"{c}_match"
        if feat_col in train_merged.columns:
            feature_cols.append(feat_col)
        elif match_col in train_merged.columns:
            feature_cols.append(match_col)
        elif c in train_merged.columns:
            feature_cols.append(c)
    
    # 对于注入因子（elo_diff等），_feat 版本可能全为 None，用 _match 版本替换
    for c in train_features.columns:
        if c == "match_id":
            continue
        feat_col = f"{c}_feat"
        match_col = f"{c}_match"
        if feat_col in train_merged.columns and match_col in train_merged.columns:
            # 如果 _feat 全为 None，用 _match 填充
            if train_merged[feat_col].isna().all() and not train_merged[match_col].isna().all():
                train_merged[feat_col] = train_merged[match_col]
                val_merged[feat_col] = val_merged[match_col]

    # 准备 X, y
    X_train = train_merged[feature_cols].fillna(0).values
    y_train = train_merged["result"].map({"H": 0, "D": 1, "A": 2}).values

    X_val = val_merged[feature_cols].fillna(0).values
    y_val = val_merged["result"].map({"H": 0, "D": 1, "A": 2}).values

    # 标准化
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)

    # 多项逻辑回归
    print("\n  训练 Logistic Regression...")
    lr = LogisticRegression(max_iter=1000, C=1.0)
    lr.fit(X_train_scaled, y_train)

    val_probs = lr.predict_proba(X_val_scaled)
    val_labels = val_merged["result"].values

    lr_result = evaluate_predictions(val_probs, val_labels)
    print(f"  Logistic Regression: {lr_result}")

    # 消融实验
    print("\n  消融实验 (逐个移除因子):")
    ablation_results = {"full_model": lr_result.brier_score}

    for i, col in enumerate(feature_cols):
        # 移除一个因子
        remaining_cols = [c for c in feature_cols if c != col]
        X_train_abl = train_merged[remaining_cols].fillna(0).values
        X_val_abl = val_merged[remaining_cols].fillna(0).values

        scaler_abl = StandardScaler()
        X_train_abl_scaled = scaler_abl.fit_transform(X_train_abl)
        X_val_abl_scaled = scaler_abl.transform(X_val_abl)

        lr_abl = LogisticRegression(max_iter=1000, C=1.0)
        lr_abl.fit(X_train_abl_scaled, y_train)

        abl_probs = lr_abl.predict_proba(X_val_abl_scaled)
        abl_result = evaluate_predictions(abl_probs, val_labels)

        delta = abl_result.brier_score - lr_result.brier_score
        ablation_results[col] = {
            "brier_without": abl_result.brier_score,
            "delta": delta,
            "direction": "worse" if delta > 0 else "better",
        }
        print(f"    -{col}: Brier={abl_result.brier_score:.4f} (Δ={delta:+.4f})")

    # 保存结果
    ablation_path = OUTPUTS_DIR / "ablation_results.json"
    with open(ablation_path, "w") as f:
        json.dump(ablation_results, f, indent=2, default=str)
    print(f"\n  消融结果已保存到: {ablation_path}")

    print("  Phase 5 完成。")


def phase_6(df: pd.DataFrame):
    """Phase 6: 严格历史回放。"""
    print("\n" + "=" * 60)
    print("Phase 6: 严格历史回放")
    print("=" * 60)

    # Elo Logistic 滚动回测
    print("  EloLogistic 滚动回测:")

    def elo_logistic_factory(train_df):
        model = EloLogisticBaseline()
        # 包装 predict 使其能从 match 行提取参数
        class WrappedModel:
            def __init__(self, inner):
                self._inner = inner
            def predict(self, match, **kwargs):
                return self._inner.predict(
                    elo_home=match.get("pre_match_elo_home", 1500),
                    elo_away=match.get("pre_match_elo_away", 1500),
                    is_neutral=match.get("is_neutral", False),
                )
        return WrappedModel(model)

    results = rolling_backtest(
        df, elo_logistic_factory,
        train_start="2010-01-01",
        initial_train_end="2016-12-31",
        step_years=1,
    )

    for r in results:
        print(f"    {r['train_end']} -> {r['test_end']}: {r['evaluation']}")

    # 保存结果
    rolling_path = OUTPUTS_DIR / "rolling_backtest_results.json"
    serializable = []
    for r in results:
        serializable.append({
            "train_end": r["train_end"],
            "test_end": r["test_end"],
            "n_train": r["n_train"],
            "n_test": r["n_test"],
            "brier_score": r["evaluation"].brier_score,
            "log_loss": r["evaluation"].log_loss,
            "accuracy": r["evaluation"].accuracy,
        })
    with open(rolling_path, "w") as f:
        json.dump(serializable, f, indent=2)
    print(f"\n  结果已保存到: {rolling_path}")

    print("  Phase 6 完成。")


def phase_7():
    """Phase 7: 因子准入评审。"""
    print("\n" + "=" * 60)
    print("Phase 7: 因子准入评审")
    print("=" * 60)

    # 加载所有结果
    baseline_path = OUTPUTS_DIR / "baseline_results.json"
    ablation_path = OUTPUTS_DIR / "ablation_results.json"
    factor_path = OUTPUTS_DIR / "single_factor_report.json"

    baseline_results = {}
    ablation_results = {}
    factor_report = []

    if baseline_path.exists():
        with open(baseline_path) as f:
            baseline_results = json.load(f)

    if ablation_path.exists():
        with open(ablation_path) as f:
            ablation_results = json.load(f)

    if factor_path.exists():
        with open(factor_path) as f:
            factor_report = json.load(f)

    # 准入评审
    baseline_brier = baseline_results.get("EloPoisson", {}).get("brier_score", 1.0)
    threshold = baseline_brier * 0.98  # 2% 改善

    decisions = {}

    print("\n  准入评审结果:")
    for factor_info in factor_report:
        name = factor_info["factor"]
        coverage = factor_info["coverage"]

        # 检查消融结果
        ablation = ablation_results.get(name, {})
        delta = ablation.get("delta", 0.0)

        if coverage < 0.5:
            decision = "needs_more_data"
            reason = f"覆盖率不足 ({coverage:.1%})"
        elif delta > 0.001:
            decision = "candidate"
            reason = f"消融显示正向贡献 (Δ={delta:+.4f})"
        elif delta < -0.001:
            decision = "rejected"
            reason = f"消融显示负向贡献 (Δ={delta:+.4f})"
        else:
            decision = "needs_more_data"
            reason = f"贡献不显著 (Δ={delta:+.4f})"

        decisions[name] = {"decision": decision, "reason": reason}
        print(f"    {name}: {decision} - {reason}")

    # 保存准入决定
    decision_path = OUTPUTS_DIR / "factor_candidates.json"
    with open(decision_path, "w") as f:
        json.dump(decisions, f, indent=2)
    print(f"\n  准入决定已保存到: {decision_path}")

    # 生成准入报告
    promotion_path = OUTPUTS_DIR / "PROMOTION_DECISION.md"
    with open(promotion_path, "w") as f:
        f.write("# 因子准入评审报告\n\n")
        f.write(f"评审时间: {datetime.now().isoformat()}\n\n")
        f.write(f"Baseline Brier Score: {baseline_brier:.4f}\n")
        f.write(f"准入门槛: Brier 相对改善 ≥ 2% (需达到 {threshold:.4f})\n\n")
        f.write("| 因子 | 决定 | 原因 |\n")
        f.write("|------|------|------|\n")
        for name, info in decisions.items():
            f.write(f"| {name} | {info['decision']} | {info['reason']} |\n")

    print(f"  准入报告已保存到: {promotion_path}")
    print("  Phase 7 完成。")


def main():
    parser = argparse.ArgumentParser(description="世界杯历史数据因子研究 Demo")
    parser.add_argument("--phase", type=int, default=0, help="运行到哪个阶段 (0-7)")
    parser.add_argument("--skip-baseline", action="store_true", help="跳过 Baseline 评估")
    args = parser.parse_args()

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("世界杯历史数据因子研究 Demo")
    print(f"运行时间: {datetime.now().isoformat()}")
    print("=" * 60)

    if args.phase >= 0:
        phase_0()

    df = None
    if args.phase >= 1:
        df = phase_1()

    if args.phase >= 2 and df is not None:
        df, features = phase_2(df)

    if args.phase >= 3 and df is not None:
        if not args.skip_baseline:
            phase_3(df)
        else:
            print("\n  跳过 Baseline 评估。")

    if args.phase >= 4 and df is not None:
        phase_4(df)

    if args.phase >= 5 and df is not None:
        phase_5(df)

    if args.phase >= 6 and df is not None:
        phase_6(df)

    if args.phase >= 7:
        phase_7()

    print("\n" + "=" * 60)
    print("流水线执行完毕！")
    print("=" * 60)


if __name__ == "__main__":
    main()
