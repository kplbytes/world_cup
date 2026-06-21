# 赔率因子准入决策报告

生成时间: 2026-06-19 11:43:12

## 核心问题

**赔率因子能否突破 2% Brier 改善阈值？**

### 答案: 是 ✓

- EloPoisson 基线 Brier: 0.6824
- 最佳模型 (odds_calibrated_elo): Brier = 0.6020
- 相对改善: 11.79%
- 阈值: 2%

## 赔率因子表现

### Top 5 赔率因子 (按 |IC| 排名)

| 因子 | IC | ICIR | 方向稳定性 | 覆盖率 |
|------|-----|------|-----------|--------|
| odds_implied_away | -0.345 | -11.528 | 100.0% | 39.1% |
| odds_implied_home | 0.344 | 11.723 | 100.0% | 39.1% |
| pinnacle_implied_away | -0.338 | -13.872 | 100.0% | 86.0% |
| pinnacle_implied_home | 0.338 | 14.016 | 100.0% | 86.0% |
| odds_vs_elo_home | 0.264 | 9.506 | 100.0% | 39.1% |

### 赔率因子 Brier 改善 (加到 elo_diff 基线)

| 因子 | Brier改善 | 相对改善 |
|------|----------|---------|
| pinnacle_implied_home | 0.0498 | 7.63% |
| pinnacle_implied_away | 0.0489 | 7.49% |
| odds_implied_home | 0.0488 | 7.47% |
| odds_implied_away | 0.0477 | 7.31% |
| odds_vs_elo_away | 0.0307 | 4.70% |
| odds_vs_elo_home | 0.0306 | 4.69% |
| odds_value_home | 0.0306 | 4.69% |
| odds_disagreement | 0.0123 | 1.89% |
| odds_favorite_strength | 0.0114 | 1.75% |
| pinnacle_implied_draw | 0.0089 | 1.37% |
| odds_draw_signal | 0.0076 | 1.17% |
| odds_implied_draw | 0.0076 | 1.16% |
| odds_vs_elo_draw | 0.0055 | 0.84% |
| odds_value_draw | 0.0055 | 0.84% |
| closing_vs_opening_home | 0.0013 | 0.19% |
| closing_vs_opening_draw | 0.0003 | 0.05% |
| odds_margin | -0.0000 | -0.00% |

## 模型对比

| 模型 | Brier | 相对改善 | Draw命中率 |
|------|-------|---------|-----------|
| odds_calibrated_elo | 0.6020 | 11.79% | 0.0% |
| full_model | 0.6107 | 10.51% | 8.9% |
| odds_only | 0.6129 | 10.20% | 10.8% |
| elo_plus_odds | 0.6129 | 10.19% | 10.1% |
| draw_enhanced | 0.6136 | 10.09% | 21.2% |
| elo_poisson_baseline | 0.6824 | 0.00% | 0.0% |

## 关键发现

1. **赔率隐含概率是最强因子**: 市场平均隐含概率的 IC 远超其他因子，
   证明博彩市场包含了大量模型无法捕捉的信息。

2. **Draw 预测显著改善**: 赔率数据提供了比 Elo 更准确的平局概率估计，
   Draw 命中率从接近 0% 提升到有意义的水平。

3. **Pinnacle 是最敏锐的博彩公司**: Pinnacle 隐含概率的预测质量最高，
   符合其'最敏锐博彩公司'的市场定位。

4. **赔率 vs Elo 分歧是有价值的信号**: 当赔率和 Elo 模型不一致时，
   赔率通常是更准确的，odds_vs_elo 因子有正的 IC。

## 决策

**PROMOTED**: 赔率因子突破 2% Brier 改善阈值。
建议将赔率因子纳入正式预测系统。

## 外推性讨论

本研究基于欧洲联赛数据（58K+ 比赛），需注意以下外推性限制：
- 联赛比赛的主场优势比国际比赛更显著
- 国际比赛中平局率更高（中立场 + 淘汰赛）
- 世界杯期间赔率市场效率可能不同（投注量更大）
- 但赔率因子的核心价值（市场信息聚合）在不同赛事中应保持一致