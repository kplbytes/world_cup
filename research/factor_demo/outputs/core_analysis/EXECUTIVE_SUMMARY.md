# 执行摘要: 深度因子分析与预测优化

生成时间: 2026-06-19 11:06:28

## 核心发现

### 1. 因子评估: 52 个因子
- PROMOTED: 0
- ACCEPTED_SHADOW: 17
- NEEDS_MORE_DATA: 9
- REJECTED: 26

Top 5 因子:
  - fifa_points_diff: 综合得分=0.914 (ACCEPTED_SHADOW)
  - fifa_rank_diff_factor: 综合得分=0.862 (ACCEPTED_SHADOW)
  - elo_diff: 综合得分=0.728 (ACCEPTED_SHADOW)
  - defense_strength: 综合得分=0.720 (ACCEPTED_SHADOW)
  - home_away_neutral_form: 综合得分=0.704 (ACCEPTED_SHADOW)

### 2. 平局预测突破
- 基线 Draw F1: 5.4%
- 最佳方案: approach_1_separate (Draw F1=40.6%)
- Draw 命中率提升: 3.0% → 73.6%

### 3. 模型架构对比
- 最佳模型: calibrated_platt (Brier=0.9780)
- 最差模型: time_weighted_lgb (Brier=1.1155)

### 4. 校准分析
- 多分类 ECE: 0.3133
- Brier 总分: 1.0797
- Brier 平局: 0.1725

## 关键结论

1. **平局预测是最大瓶颈**: 即使最佳方案的 Draw F1 仍然较低，
   平局概率的准确估计是提升整体 Brier 的关键路径。

2. **因子增量有限**: 大多数因子相对于 elo_diff 的增量贡献 < 2%，
   需要更强的信号源（如赔率数据、更精细的 xG 模型）。

3. **模型架构差异不大**: Stacking/校准/特征选择等方案对 Brier 的改善
   在统计上不显著，说明预测天花板受限于因子质量而非模型复杂度。