# Shadow 数据质量审计报告

> **审计结论：`DATA_ISSUE_FOUND`**
>
> 审计时间：2026-06-16
>
> 审计范围：2026 世界杯已完赛小组赛的赛前预测快照完整性

## 1. 审计目标

验证以下数据链路是否完整：

1. 比赛数据是否及时入库
2. 赛前预测是否在开球前生成
3. 预测快照是否在开球前锁定
4. Shadow LR_full 预测是否存在
5. 赛后评分是否基于赛前锁定数据

## 2. 数据源检查

### 2.1 数据库表行数

| 表名 | 行数 | 状态 |
|------|------|------|
| `historical_matches` | 8,115 | ✅ 有数据 |
| `matches`（操作表） | **0** | ❌ 空 |
| `match_predictions` | **0** | ❌ 空 |
| `prediction_snapshots` | **0** | ❌ 空 |
| `ai_predictions` | **0** | ❌ 空 |
| `ensemble_predictions` | **0** | ❌ 空 |
| `dashboard_revisions` | **0** | ❌ 空 |
| `team_ratings` | **0** | ❌ 空 |
| `teams` | **0** | ❌ 空 |

### 2.2 dashboard.json

| 指标 | 值 |
|------|-----|
| 文件存在 | ✅ |
| revision_id | 5 |
| 生成时间 | 2026-06-13T10:19:06 |
| 模型版本 | elo-poisson-v1-intel-numeric |
| 总场次 | 72 |
| 已完赛 | 4（dashboard.json 内） |
| 有预测的未赛场次 | 68 |
| 赛前锁定快照 | 0（2场赛后锁定，无预测数据） |
| Shadow LR_full 预测 | ❌ 不存在 |

### 2.3 historical_matches 中的 2026 世界杯数据

| 日期 | 主队 | 比分 | 客队 | 有预测快照 |
|------|------|------|------|------------|
| 06-11 | Mexico | 2-0 | South Africa | ❌ |
| 06-11 | South Korea | 2-1 | Czech Republic | ❌ |
| 06-12 | Canada | 1-1 | Bosnia & Herz. | ❌ |
| 06-12 | United States | 4-1 | Paraguay | ❌ |
| 06-13 | Qatar | 1-1 | Switzerland | ❌ |
| 06-13 | Brazil | 1-1 | Morocco | ❌ |
| 06-13 | Haiti | 0-1 | Scotland | ❌ |
| 06-13 | Australia | 2-0 | Turkey | ❌ |

## 3. 问题清单

### 问题 1：操作表全部为空（严重）

`matches`、`match_predictions`、`prediction_snapshots` 等操作表均为 0 行。

**根因**：`seed.py` 未被执行，或 `refresh.py` 从未在赛前运行。

**影响**：无法从数据库获取任何赛前预测快照。

**修复建议**：在赛前定时运行 `seed.py` + `refresh.py`，确保比赛和预测数据及时入库。

### 问题 2：dashboard.json 预测为赛后生成（严重）

dashboard.json 生成时间为 2026-06-13T10:19:06，但包含 06-11 和 06-12 的已完赛场次。这些场次的预测在赛后才生成，不满足"赛前锁定"要求。

**影响**：即使 dashboard.json 中有预测数据，也无法用于 Shadow 评分（可能包含赛后信息）。

**修复建议**：确保 `refresh.py` 在每场比赛开球前至少运行一次，并在开球前 24 小时内锁定快照。

### 问题 3：赛后锁定无效（中等）

第3-4场（06-12 Canada vs Bosnia、USA vs Paraguay）在 dashboard.json 中标记为 `locked=True`，但：
- `locked_at=2026-06-13T07:14:31`（比赛已结束数小时后）
- `prediction=None`（无预测数据）

**影响**：锁定标记存在但无实际预测数据，属于无效锁定。

**修复建议**：锁定逻辑应仅在 `prediction` 非空时才标记为 `locked=True`。

### 问题 4：Shadow LR_full 模型未接入（预期）

因子研究 Demo 的 LR_full 模型从未在 2026 实时数据上运行。后端 `shadow.py` 中的4个 Shadow 变体均为 EloPoisson 参数变体，非因子研究 Demo 的 LR_full。

**影响**：无法对 Shadow LR_full 进行评分。

**修复建议**：在 `feature/factor-shadow-validation` 分支中接入 LR_full 作为旁路 Shadow 模型。

### 问题 5：数据源覆盖不完整（中等）

dashboard.json 显示部分数据源已禁用：
- `api-football`：disabled（配额耗尽）
- `sportmonks`：disabled（无 token）

**影响**：可能影响预测质量。

**修复建议**：补充数据源或确认现有数据源足够。

## 4. 数据完整性评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 比赛结果入库 | ✅ 完整 | historical_matches 有8场2026 WC数据 |
| 赛前预测生成 | ❌ 缺失 | 0场有赛前预测 |
| 赛前快照锁定 | ❌ 缺失 | 0场有赛前锁定快照 |
| Shadow 预测 | ❌ 缺失 | LR_full 未接入 |
| 预测-结果配对 | ❌ 缺失 | 无可评分配对 |

**总体评分：`DATA_ISSUE_FOUND`**

## 5. 不可补造声明

以下数据不得事后补造：

1. 赛前预测快照（`prediction_snapshots`）
2. 赛前锁定标记（`is_pre_match_locked`）
3. Shadow LR_full 预测
4. 任何标注为"赛前"的预测数据

已完赛的8场比赛将永久标记为 `not_scorable`，不得在修复数据链路后回填预测。

## 6. 修复优先级

1. **P0**：确保 `seed.py` + `refresh.py` 在赛前自动运行
2. **P0**：确保 `lock_due_predictions()` 在开球前24小时内锁定快照
3. **P1**：在 `feature/factor-shadow-validation` 分支接入 LR_full Shadow 模型
4. **P2**：补充数据源（api-football / sportmonks）
5. **P2**：修复赛后锁定标记逻辑

## 7. 审计结论

**`DATA_ISSUE_FOUND`**

- 8场已完赛2026世界杯比赛均无赛前锁定预测快照
- 预测系统数据链路未在赛前运行
- 无法进行任何 Shadow vs Baseline 评分
- 需修复数据链路后，等待新场次积累赛前快照
- 当前状态不满足 Shadow 验证的任何准入条件
