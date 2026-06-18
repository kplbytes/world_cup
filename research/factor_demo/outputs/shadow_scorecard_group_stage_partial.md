# Shadow 赛前预测评分卡 — 2026 世界杯小组赛（部分）

> **结论：`DATA_ISSUE_FOUND`**
>
> 本评分卡不构成训练依据，不构成生产准入依据，仅作观察。

## 1. 执行摘要

| 指标 | 值 |
|------|-----|
| 已完赛 2026 世界杯场次 | 8 |
| 有赛前锁定快照的场次 | **0** |
| 可评分场次（scorable） | **0** |
| Shadow LR_full 预测存在 | **否** |
| EloPoisson 基线预测存在 | 仅 dashboard.json（赛后生成） |
| 结论 | **`DATA_ISSUE_FOUND`** |

## 2. 已完赛场次明细

| # | 日期 | 主队 | 比分 | 客队 | 赛前快照 | 基线预测 | Shadow预测 | 评分状态 |
|---|------|------|------|------|----------|----------|------------|----------|
| 1 | 06-11 | Mexico | 2-0 | South Africa | 无 | 无 | 无 | not_scorable |
| 2 | 06-11 | South Korea | 2-1 | Czech Republic | 无 | 无 | 无 | not_scorable |
| 3 | 06-12 | Canada | 1-1 | Bosnia & Herz. | 无 | 无 | 无 | not_scorable |
| 4 | 06-12 | United States | 4-1 | Paraguay | 无* | 无 | 无 | not_scorable |
| 5 | 06-13 | Qatar | 1-1 | Switzerland | 无 | 无 | 无 | not_scorable |
| 6 | 06-13 | Brazil | 1-1 | Morocco | 无 | 无 | 无 | not_scorable |
| 7 | 06-13 | Haiti | 0-1 | Scotland | 无 | 无 | 无 | not_scorable |
| 8 | 06-13 | Australia | 2-0 | Turkey | 无 | 无 | 无 | not_scorable |

\* 第3-4场在 dashboard.json 中标记为 `locked=True`，但 `locked_at=2026-06-13T07:14:31`（赛后锁定），且 `prediction=None`，仍为 not_scorable。

## 3. 评分指标

**无法计算。** 所有8场已完赛均无赛前锁定预测快照，无法计算 Brier、Log Loss、命中率、ECE 或 ΔBrier。

## 4. 数据链路问题

### 4.1 核心问题：赛前快照缺失

- `prediction_snapshots` 数据库表：**0 行**
- `match_predictions` 数据库表：**0 行**
- `matches` 操作表：**0 行**
- `dashboard.json` 中已完赛场次：`prediction=None`

预测系统从未在赛前为任何 2026 世界杯比赛生成并锁定预测快照。

### 4.2 dashboard.json 状态

- 生成时间：2026-06-13T10:19:06（revision_id=5）
- 模型版本：`elo-poisson-v1-intel-numeric`
- 72场小组赛中68场有预测（均为未赛场次）
- 4场已完赛无预测数据
- 无 Shadow LR_full 预测
- 唯一模型版本：`elo-poisson-v1-intel-numeric`

### 4.3 Shadow LR_full 模型

- 因子研究 Demo 中的 LR_full 模型从未在 2026 实时数据上运行
- 后端 `shadow.py` 中的4个 Shadow 变体（draw_boost, favorite_dampened 等）均基于 EloPoisson 调参，非因子研究 Demo 的 LR_full
- 无任何 Shadow 预测被写入数据库或 dashboard

## 5. 限制声明

- **样本数不足**：0 场可评分
- **仅作观察**：本评分卡仅记录数据链路现状
- **不构成训练依据**：无可评分数据
- **不构成生产准入依据**：结论为 `DATA_ISSUE_FOUND`
- **禁止补造快照**：未获得赛前锁定的场次不得事后补造预测

## 6. 下一步建议

1. **修复数据链路**：确保 `refresh.py` 在赛前自动运行，生成并锁定预测快照
2. **接入 Shadow LR_full**：在 `feature/factor-shadow-validation` 分支中，将因子研究 Demo 的 LR_full 作为旁路 Shadow 模型接入预测管道
3. **等待新场次**：修复数据链路后，等待足够赛前锁定快照（≥30场）再进行 Shadow 评分
4. **不调参、不训练**：当前阶段只修复数据链路，不修改任何模型参数
