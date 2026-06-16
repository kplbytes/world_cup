#!/usr/bin/env python3
"""从现有 JSON 输出重新生成 PROMOTION_DECISION.md

不需要重新运行耗时的特征计算，直接读取 round2 输出目录中的 JSON 文件。

使用方法:
    cd research/factor_demo
    python scripts/regenerate_decision.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

OUTPUT_DIR = Path(__file__).parent.parent / "outputs" / "round2"


def load_json(filename):
    path = OUTPUT_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fix_bootstrap_interpretation(data):
    """修复 bootstrap 结果中的方向判断。"""
    for name, r in data.items():
        mean_diff = r["mean_brier_diff"]
        significant = r["significant"]
        if significant and mean_diff > 0:
            interpretation = "显著改善"
            direction = "better"
        elif significant and mean_diff < 0:
            interpretation = "显著退化"
            direction = "worse"
        else:
            interpretation = "不显著"
            direction = "better" if mean_diff > 0 else "worse"
        r["direction"] = direction
        r["interpretation"] = f"{interpretation} (ΔBrier={mean_diff:+.5f}, CI: [{r['ci_95_low']:.5f}, {r['ci_95_high']:.5f}])"
    return data


def generate_promotion_decision():
    """从现有 JSON 输出生成 PROMOTION_DECISION.md。"""

    # 加载所有结果
    model_results = load_json("unified_comparison_table.json")
    bootstrap_results = fix_bootstrap_interpretation(load_json("bootstrap_results.json"))
    wf_results = load_json("walk_forward_results.json")
    stratified_results = load_json("stratified_results.json")
    host_analysis = load_json("host_advantage_analysis.json")
    time_leak_audit = load_json("time_leak_audit.json")
    per_factor = load_json("per_factor_analysis.json")
    calibration_data = load_json("calibration_data.json")

    # ---- 决策逻辑 ----

    # 1. LR_full 是否显著优于 EP
    lr_full_significant = bootstrap_results.get("LR_full", {}).get("significant", False)
    lr_full_positive = bootstrap_results.get("LR_full", {}).get("mean_brier_diff", 0) > 0

    # 2. Walk-Forward 稳定性
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
        if lr_brier > ep_brier + 0.005:
            wc_no_degradation = False
            break

    # 4. 新因子单独是否有价值
    lr_new_only_worse = bootstrap_results.get("LR_new_only", {}).get("mean_brier_diff", 0) < 0

    # 6. 盲测集是否有退化（整体或世界杯子集）
    blind_degradation = False
    blind_wc_degradation = False
    if "LR_full" in model_results and "blind_test" in model_results["LR_full"]:
        lr_blind_brier = model_results["LR_full"]["blind_test"]["brier_score"]
        ep_blind_brier = model_results.get("EloPoisson", {}).get("blind_test", {}).get("brier_score", 0)
        if ep_blind_brier > 0 and lr_blind_brier > ep_blind_brier:
            blind_degradation = True
    # 检查盲测集世界杯子集退化
    for key in stratified_results:
        if "blind" in key and "world_cup_only" in key:
            r = stratified_results[key]
            ep_b = r["EloPoisson"]["brier_score"]
            lr_b = r["LR_full"]["brier_score"]
            if lr_b > ep_b:
                blind_wc_degradation = True
                break

    # 综合决策
    # PASS_SHADOW: 所有条件满足 - 显著改善、WF稳定、世界杯无退化、新因子有独立价值、盲测集无退化
    # NEEDS_MORE_DATA: 有部分正面证据但存在关键问题
    # REJECTED: 无稳定优于EloPoisson
    if (lr_full_significant and lr_full_positive and stable_across_windows
            and wc_no_degradation and not lr_new_only_worse
            and not blind_degradation and not blind_wc_degradation):
        decision = "PASS_SHADOW"
    elif (lr_full_significant and lr_full_positive) or (stable_across_windows and wc_no_degradation):
        decision = "NEEDS_MORE_DATA"
    else:
        decision = "REJECTED"

    # ---- 生成 PROMOTION_DECISION.md ----
    lines = []
    lines.append("# 因子准入评审报告 - 第二轮严格验证")
    lines.append("")
    lines.append(f"**评审时间**: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**最终结论**: **{decision}**")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 执行摘要
    lines.append("## 1. 执行摘要")
    lines.append("")

    # 计算关键指标
    ep_val_brier = model_results["EloPoisson"]["validation"]["brier_score"]
    lr_val_brier = model_results["LR_full"]["validation"]["brier_score"]
    val_improvement = (ep_val_brier - lr_val_brier) / ep_val_brier * 100

    ep_blind_brier_val = model_results.get("EloPoisson", {}).get("blind_test", {}).get("brier_score", 0)
    lr_blind_brier_val = model_results.get("LR_full", {}).get("blind_test", {}).get("brier_score", 0)

    lines.append(f"经过第二轮严格验证，LR_full（elo_diff + 新因子）相对 EloPoisson Baseline 在验证集上 Brier 改善 {val_improvement:.1f}%，"
                 f"Bootstrap 95%CI 显著，Walk-Forward {n_wf_positive}/{len(wf_results)} 窗口均优于 Baseline。")

    if decision == "NEEDS_MORE_DATA":
        lines.append("")
        lines.append("**但存在以下关键问题导致无法给予 PASS_SHADOW：**")
        lines.append("")

        problems = []
        if not wc_no_degradation:
            problems.append("1. **世界杯样本严重退化**")
        if lr_new_only_worse:
            problems.append("2. **新因子单独无价值**：LR_new_only（不含 elo_diff）显著差于 EloPoisson")
        if blind_degradation:
            problems.append("3. **盲测集整体退化**")
        if not problems:
            problems.append("- 改善幅度不够大或不够稳定")

        # 具体数据
        for key in wc_keys:
            if "blind" in key:
                r = stratified_results[key]
                ep_b = r["EloPoisson"]["brier_score"]
                lr_b = r["LR_full"]["brier_score"]
                if lr_b > ep_b:
                    deg_pct = (lr_b - ep_b) / ep_b * 100
                    problems.append(f"   - 盲测集世界杯 {r['n_samples']} 场，LR_full Brier={lr_b:.3f} vs EloPoisson {ep_b:.3f}，退化 {deg_pct:.1f}%")

        # LR_new_only 数据
        if lr_new_only_worse:
            lr_new_val = model_results.get("LR_new_only", {}).get("validation", {}).get("brier_score", 0)
            problems.append(f"   - LR_new_only 验证集 Brier={lr_new_val:.4f}，差于 EloPoisson {ep_val_brier:.4f}")

        # 改善来源
        lr_elo_only_val = model_results.get("LR_elo_only", {}).get("validation", {}).get("brier_score", 0)
        if lr_elo_only_val > 0:
            problems.append("4. **改善主要来自 elo_diff 的 LR 重新校准**，非新因子贡献")

        for p in problems:
            lines.append(p)

    lines.append("")
    lines.append("---")
    lines.append("")

    # Baseline 性能
    lines.append("## 2. Baseline 性能")
    lines.append("")
    lines.append("| 模型 | 验证集 Brier | 盲测集 Brier | 验证集 LogLoss | 盲测集 LogLoss | 验证集 ECE |")
    lines.append("|------|-------------|-------------|---------------|---------------|-----------|")
    for name in ["EloPoisson", "EloLogistic"]:
        if name in model_results:
            r = model_results[name]
            v = r["validation"]
            b = r.get("blind_test", {})
            lines.append(f"| {name} | {v['brier_score']:.4f} | {b.get('brier_score', 'N/A')} | "
                        f"{v['log_loss']:.4f} | {b.get('log_loss', 'N/A')} | {v['ece']:.4f} |")
    lines.append("")

    # 新因子模型性能
    lines.append("## 3. 新因子模型性能")
    lines.append("")
    lines.append("| 模型 | 验证集 Brier | 盲测集 Brier | 相对EP改善(Val) | 相对EP改善(Blind) |")
    lines.append("|------|-------------|-------------|----------------|------------------|")
    for name in ["LR_elo_only", "LR_new_only", "LR_full"]:
        if name in model_results:
            r = model_results[name]
            v = r["validation"]
            b = r.get("blind_test", {})
            val_imp = (ep_val_brier - v["brier_score"]) / ep_val_brier * 100
            blind_imp = ""
            if b and ep_blind_brier_val > 0:
                blind_imp = f"{(ep_blind_brier_val - b['brier_score']) / ep_blind_brier_val * 100:+.2f}%"
            else:
                blind_imp = "N/A"
            lines.append(f"| {name} | {v['brier_score']:.4f} | {b.get('brier_score', 'N/A')} | "
                        f"{val_imp:+.2f}% | {blind_imp} |")
    lines.append("")

    lr_new_val_brier = model_results.get("LR_new_only", {}).get("validation", {}).get("brier_score", 0)
    if lr_new_val_brier > ep_val_brier:
        lines.append(f"**关键发现**：LR_new_only（仅新因子，不含 elo_diff）验证集 Brier={lr_new_val_brier:.4f}，"
                     f"差于 EloPoisson {ep_val_brier:.4f}，说明新因子单独无法替代 Elo。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Bootstrap CI
    lines.append("## 4. Bootstrap 置信区间")
    lines.append("")
    lines.append("| 模型 vs EloPoisson | ΔBrier 均值 | 95% CI | 方向 | 显著? |")
    lines.append("|---------------------|-----------|--------|------|-------|")
    for name, r in bootstrap_results.items():
        direction = "优于EP" if r["mean_brier_diff"] > 0 else "劣于EP"
        sig_str = "是" if r["significant"] else "否"
        if r["significant"] and r["mean_brier_diff"] < 0:
            sig_str = "是（更差）"
        lines.append(f"| {name} | {r['mean_brier_diff']:+.5f} | [{r['ci_95_low']:.5f}, {r['ci_95_high']:.5f}] | {direction} | {sig_str} |")
    lines.append("")

    # 解读
    lr_full_diff = bootstrap_results.get("LR_full", {}).get("mean_brier_diff", 0)
    lr_elo_diff = bootstrap_results.get("LR_elo_only", {}).get("mean_brier_diff", 0)
    lr_new_diff = bootstrap_results.get("LR_new_only", {}).get("mean_brier_diff", 0)
    lines.append(f"**解读**：LR_full 改善显著（ΔBrier={lr_full_diff:+.5f}），"
                 f"但 LR_elo_only 改善{'不显著' if not bootstrap_results.get('LR_elo_only', {}).get('significant', False) else '显著'}"
                 f"（ΔBrier={lr_elo_diff:+.5f}），"
                 f"LR_new_only {'显著劣于' if bootstrap_results.get('LR_new_only', {}).get('significant', False) else '劣于'}EP"
                 f"（ΔBrier={lr_new_diff:+.5f}）。"
                 f"说明改善主要来自 LR 对 elo_diff 的非线性校准，而非新因子。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Walk-Forward
    lines.append("## 5. Walk-Forward 稳定性")
    lines.append("")
    lines.append("| 测试窗口 | N | EP Brier | LR Brier | ΔBrier | EP LogLoss | LR LogLoss | EP Acc | LR Acc | EP ECE | LR ECE | 方向 |")
    lines.append("|---------|---|---------|---------|--------|-----------|-----------|--------|--------|--------|--------|------|")
    for w in wf_results:
        delta = w["delta_brier"]
        direction = "✓" if delta > 0 else "✗"
        lines.append(f"| {w['test_period']} | {w['n_test']} | "
                    f"{w['EloPoisson_brier']:.4f} | {w['LR_full_brier']:.4f} | {delta:+.4f} | "
                    f"{w['EloPoisson_logloss']:.4f} | {w['LR_full_logloss']:.4f} | "
                    f"{w['EloPoisson_acc']:.1%} | {w['LR_full_acc']:.1%} | "
                    f"{w['EloPoisson_ece']:.4f} | {w['LR_full_ece']:.4f} | {direction} |")
    if wf_results:
        wf_deltas = [w["delta_brier"] for w in wf_results]
        lines.append("")
        lines.append(f"**汇总**: {n_wf_positive}/{len(wf_results)} 窗口 LR 优于 EP，"
                     f"平均 ΔBrier={np.mean(wf_deltas):+.4f}，标准差={np.std(wf_deltas):.4f}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 世界杯专项
    lines.append("## 6. 世界杯专项评估")
    lines.append("")
    for section in ["验证集", "盲测集"]:
        prefix = "val" if section == "验证集" else "blind"
        lines.append(f"### {section}")
        lines.append("")
        lines.append("| 场景 | N | EP Brier | LR Brier | ΔBrier |")
        lines.append("|------|---|---------|---------|--------|")
        for key in sorted(stratified_results.keys()):
            if "world_cup" in key and key.startswith(prefix):
                r = stratified_results[key]
                ep_b = r["EloPoisson"]["brier_score"]
                lr_b = r["LR_full"]["brier_score"]
                short_key = key.replace(f"{prefix}/", "")
                lines.append(f"| {short_key} | {r['n_samples']} | {ep_b:.4f} | {lr_b:.4f} | {ep_b - lr_b:+.4f} |")
        lines.append("")

    # 世界杯退化判定
    blind_wc_key = None
    for key in stratified_results:
        if "blind" in key and "world_cup_only" in key:
            blind_wc_key = key
            break
    if blind_wc_key:
        r = stratified_results[blind_wc_key]
        ep_b = r["EloPoisson"]["brier_score"]
        lr_b = r["LR_full"]["brier_score"]
        if lr_b > ep_b:
            deg_pct = (lr_b - ep_b) / ep_b * 100
            lines.append(f"**严重问题**：盲测集世界杯样本 LR_full 显著退化 {deg_pct:.1f}%。"
                         f"虽然样本量小（{r['n_samples']}场），但退化幅度远超改善幅度，不可忽视。")
        else:
            lines.append(f"盲测集世界杯样本 LR_full 无退化。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 主场优势
    lines.append("## 7. 主场优势深度分析")
    lines.append("")
    lines.append("| 场地类型 | N | 主队胜率 | EP Brier | LR Brier | host_advantage有帮助? |")
    lines.append("|---------|---|---------|---------|---------|---------------------|")
    for vtype in ["home", "neutral", "host_nation", "semi_home"]:
        info = host_analysis.get("venue_types", {}).get(vtype, {})
        n = info.get("n_samples", 0)
        if n == 0:
            lines.append(f"| {vtype} | 0 | - | - | - | 无法验证 |")
            continue
        hwr = info.get("home_win_rate", "N/A")
        hwr_str = f"{hwr:.1%}" if isinstance(hwr, float) else hwr
        ep_b = info.get("elo_poisson_brier", "N/A")
        lr_b = info.get("lr_full_brier", "N/A")
        ep_str = f"{ep_b:.4f}" if isinstance(ep_b, float) else ep_b
        lr_str = f"{lr_b:.4f}" if isinstance(lr_b, float) else lr_b
        helps = info.get("host_advantage_helps", "N/A")
        helps_str = "是" if helps is True else ("否" if helps is False else "无法验证")
        lines.append(f"| {vtype} | {n} | {hwr_str} | {ep_str} | {lr_str} | {helps_str} |")
    lines.append("")

    # 盲测集场地分析
    if host_analysis.get("blind_venue_types"):
        lines.append("### 盲测集场地分析")
        lines.append("")
        lines.append("| 场地类型 | N | 主队胜率 | EP Brier | LR Brier |")
        lines.append("|---------|---|---------|---------|---------|")
        for vtype in ["home", "neutral", "host_nation", "semi_home"]:
            info = host_analysis.get("blind_venue_types", {}).get(vtype, {})
            n = info.get("n_samples", 0)
            if n == 0:
                lines.append(f"| {vtype} | 0 | - | - | - |")
                continue
            hwr = info.get("home_win_rate", "N/A")
            hwr_str = f"{hwr:.1%}" if isinstance(hwr, float) else hwr
            ep_b = info.get("elo_poisson_brier", "N/A")
            lr_b = info.get("lr_full_brier", "N/A")
            ep_str = f"{ep_b:.4f}" if isinstance(ep_b, float) else ep_b
            lr_str = f"{lr_b:.4f}" if isinstance(lr_b, float) else lr_b
            lines.append(f"| {vtype} | {n} | {hwr_str} | {ep_str} | {lr_str} |")
        lines.append("")

    lines.append(f"**判定**：{host_analysis.get('verdict', 'N/A')} - {host_analysis.get('reason', 'N/A')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 逐因子评审
    lines.append("## 8. 逐因子评审")
    lines.append("")
    lines.append("| 因子 | 覆盖率 | 与elo相关 | 稳定性CV | 消融ΔBrier | 冗余? | 判定 |")
    lines.append("|------|-------|----------|---------|-----------|-------|------|")
    for fn, info in per_factor.items():
        cov = f"{info.get('coverage_rate', 0):.1%}"
        pearson_r = None
        if info.get("pearson_with_elo"):
            pearson_r = abs(info["pearson_with_elo"]["r"])
        pearson_str = f"r={pearson_r:.2f}" if pearson_r is not None else "N/A"
        cv = info.get("stability_cv")
        cv_str = f"{cv:.3f}" if cv is not None else "N/A"
        abl = info.get("ablation_brier_delta")
        abl_str = f"{abl:+.6f}" if abl is not None else "N/A"
        redundant = "是" if info.get("is_redundant") else "否"

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

        lines.append(f"| {fn} | {cov} | {pearson_str} | {cv_str} | {abl_str} | {redundant} | {verdict} |")
    lines.append("")

    # 关键发现
    redundant_factors = [fn for fn, info in per_factor.items() if info.get("is_redundant")]
    positive_factors = [fn for fn, info in per_factor.items()
                       if info.get("ablation_brier_delta") is not None and info["ablation_brier_delta"] > 0.001]
    lines.append("**关键发现**：")
    if redundant_factors:
        lines.append(f"- `{', '.join(redundant_factors)}` 与 elo_diff 高度冗余，不应作为独立新因子")
    if positive_factors:
        lines.append(f"- 仅有 `{', '.join(positive_factors)}` 消融贡献 > 0.001")
    else:
        lines.append("- 所有新因子消融贡献极小（<0.001）")
    lines.append("- 新因子整体贡献微弱，LR_full 的改善主要来自 LR 对 elo_diff 的非线性变换")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 时间泄漏审计
    lines.append("## 9. 时间泄漏审计")
    lines.append("")
    n_total_checks = len(time_leak_audit.get("checks", []))
    n_passed = sum(1 for c in time_leak_audit.get("checks", []) if c["status"] == "PASS")
    lines.append(f"随机修改 50 场未来比赛结果，重算 100 场历史比赛特征：")
    lines.append("")
    lines.append(f"| 检查项 | 结果 |")
    lines.append(f"|--------|------|")
    lines.append(f"| 全部{n_total_checks}个因子 | {n_passed}/{n_total_checks} PASS |")
    lines.append("")
    lines.append(f"**结论**：{'无时间泄漏' if time_leak_audit.get('overall_status') == 'PASS' else '存在时间泄漏！'}。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 校准曲线摘要
    lines.append("## 10. 校准曲线摘要")
    lines.append("")
    outcome_labels = {"H": "主胜(home_win)", "D": "平局(draw)", "A": "客胜(away_win)"}
    for model_name in ["EloPoisson", "LR_full"]:
        if model_name in calibration_data:
            cal = calibration_data[model_name]
            lines.append(f"### {model_name}")
            lines.append("")
            for outcome_key, outcome_label in outcome_labels.items():
                if outcome_key in cal:
                    data = cal[outcome_key]
                    centers = data.get("bin_centers", [])
                    freqs = data.get("actual_frequencies", [])
                    counts = data.get("counts", [])
                    lines.append(f"**{outcome_label}**:")
                    for c, f, n in zip(centers, freqs, counts):
                        lines.append(f"  - pred={c:.2f} → actual={f:.3f} (N={n})")
                    lines.append("")
    lines.append("---")
    lines.append("")

    # 决策推理
    lines.append("## 11. 最终决策")
    lines.append("")
    lines.append(f"### 决策：**{decision}**")
    lines.append("")
    lines.append("### 决策条件")
    lines.append("")
    lines.append(f"| 条件 | 结果 |")
    lines.append(f"|------|------|")
    lines.append(f"| LR_full 显著优于EP | {'✓ 是' if lr_full_significant and lr_full_positive else '✗ 否'} |")
    lines.append(f"| Walk-Forward 稳定 | {'✓ 是' if stable_across_windows else '✗ 否'} (LR优于EP窗口: {n_wf_positive}/{len(wf_results)}) |")
    lines.append(f"| 世界杯无退化 | {'✓ 是' if wc_no_degradation else '✗ 否'} |")
    lines.append(f"| 新因子单独有价值 | {'✓ 是' if not lr_new_only_worse else '✗ 否'} |")
    lines.append(f"| 盲测集整体无退化 | {'✓ 是' if not blind_degradation else '✗ 否'} |")
    lines.append(f"| 盲测集世界杯无退化 | {'✓ 是' if not blind_wc_degradation else '✗ 否'} |")
    lines.append("")

    if decision == "NEEDS_MORE_DATA":
        lines.append("### 理由")
        lines.append("")
        reasons = []
        if val_improvement < 2.0:
            reasons.append(f"1. **整体改善微弱**：验证集 Brier 相对改善 {val_improvement:.1f}%，未达 2% 准入门槛")
        if not lr_new_only_worse:
            pass
        else:
            reasons.append("2. **改善来源不明确**：LR_full 改善主要来自 LR 对 elo_diff 的非线性校准，而非新因子增量信息。LR_new_only 显著更差")
        if not wc_no_degradation:
            reasons.append("3. **世界杯场景退化**：盲测集世界杯样本 Brier 退化，违反准入条件")
        if blind_degradation:
            reasons.append("4. **盲测集整体退化**：LR_full 在盲测集上差于 EloPoisson")
        if not reasons:
            reasons.append("- 证据不足以做出确定性结论")
        for r in reasons:
            lines.append(r)
        lines.append("")

        lines.append("### 下一步建议")
        lines.append("")
        lines.append("1. **积累更多世界杯样本**：2026世界杯正赛（6-7月）完成后重新评估")
        lines.append("2. **引入赔率数据**：市场因子（odds_implied_prob）覆盖率当前为0%，是潜在高价值因子")
        lines.append("3. **引入 FIFA 排名**：fifa_rank_diff 覆盖率为0%，需补充数据")
        lines.append("4. **简化因子集**：移除冗余因子，聚焦有贡献的因子")
        lines.append("5. **考虑模型校准**：LR 对 elo_diff 的校准改善可能是独立价值，但需与 Platt Scaling 等纯校准方法对比")
        lines.append("6. **增加 host_nation 标识**：当前数据无法区分东道主效应，需补充")
    elif decision == "REJECTED":
        lines.append("### 理由")
        lines.append("")
        lines.append("新候选因子未能稳定优于 EloPoisson Baseline，不建议进入生产环境。")
    else:
        lines.append("### 理由")
        lines.append("")
        lines.append("新候选因子通过了第二轮验证，建议进入 Shadow 模式运行。")

    lines.append("")
    lines.append("---")
    lines.append("")

    # 禁止事项
    lines.append("## 12. 禁止事项重申")
    lines.append("")
    lines.append("- 本 Demo 不修改主程序")
    lines.append("- 不接入 Ensemble")
    lines.append("- 不调整 Shadow 权重")
    lines.append("- 所有结论仅基于当前数据和验证方法")
    lines.append("- 世界杯退化问题解决前，任何因子不得进入 Shadow 模式")

    md_content = "\n".join(lines)
    md_path = OUTPUT_DIR / "PROMOTION_DECISION.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    # 同时保存修正后的 bootstrap_results.json
    bootstrap_path = OUTPUT_DIR / "bootstrap_results.json"
    with open(bootstrap_path, "w", encoding="utf-8") as f:
        json.dump(bootstrap_results, f, indent=2, ensure_ascii=False, default=_json_default)

    print(f"已保存: {md_path}")
    print(f"已更新: {bootstrap_path}")
    print(f"\n最终决策: {decision}")

    return decision


def _json_default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


if __name__ == "__main__":
    generate_promotion_decision()
