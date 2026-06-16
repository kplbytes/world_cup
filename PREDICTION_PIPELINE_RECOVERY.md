# 预测管线恢复报告

> **状态：已恢复 + 快照合法性已审计**
>
> 恢复时间：2026-06-16
>
> 分支：`feature/fix-prediction-snapshot-pipeline`

## 1. 问题描述

预测管线完全无数据产出：

| 表 | 恢复前 |
|------|--------|
| `matches` | 0 |
| `match_predictions` | 0 |
| `prediction_snapshots` | 0 |
| `teams` | 0 |
| `dashboard_revisions` | 0 |

已完赛2026世界杯比赛仅存在于 `historical_matches`，无赛前预测快照。

## 2. 根因分析

### 2.1 数据库路径不匹配

- 应用默认路径：`data/world-cup.sqlite3`（不存在）
- 实际数据库：`backend/world_cup.db`（有8115行 historical_matches）
- 无 `.env` 文件覆盖路径

### 2.2 应用从未启动

- `initialize_database()` 仅在 FastAPI lifespan 中调用
- 如果应用从未启动，`seed_tournament()` 永远不会执行
- `matches` 表为空导致整条管线无数据

### 2.3 因果链

```
seed_tournament() 未执行
    → matches = 0, teams = 0
    → refresh_tournament() 忽略所有 incoming 比赛
    → recompute_all() 因每组 0 场比赛而抛异常
    → match_predictions = 0
    → write_snapshots() 因无 match 而直接 return
    → prediction_snapshots = 0
```

## 3. 修复措施

### 3.1 创建 `.env` 文件

设置 `DATABASE_PATH=backend/world_cup.db`，确保应用指向正确的数据库。
`.env` 已在 `.gitignore` 中，不提交到版本控制。

### 3.2 创建恢复脚本

`backend/scripts/recover_pipeline.py` 执行完整流程：
1. `create_database()` — 确保schema存在
2. `seed_tournament()` — 从 `world-cup-2026.json` 写入 48 队 + 72 场
3. `seed_ratings()` — 写入 Elo 评分
4. `seed_team_aliases()` — 写入球队别名
5. `rebuild_team_profiles()` — 构建球队画像
6. `recompute_all()` — 生成预测 + 快照
7. **`purge_invalid_post_kickoff_snapshots()`** — 清除已完赛比赛的赛后快照
8. `lock_due_predictions()` — 锁定 T-24h 快照

### 3.3 快照合法性审计

发现并清除了70个无效赛后快照（14场已完赛 × 5模型版本）。详见 [SNAPSHOT_LEGALITY_AUDIT.md](SNAPSHOT_LEGALITY_AUDIT.md)。

### 3.4 增加回归测试

`backend/tests/test_pipeline_recovery.py` — 15 项测试：
- seed 后 `matches > 0`
- seed 后 `teams > 0`
- matches 有有效 ID
- refresh 后 `match_predictions > 0`
- 未赛比赛有预测
- dashboard revision 存在
- `prediction_snapshots > 0`
- T-24h 比赛有锁定快照
- 已完赛无赛前锁定快照（禁止补造）
- 已完赛标记为 not_scorable
- 已完赛无赛后快照（已清除）
- 已完赛无任何快照
- 评分逻辑只使用赛前快照（0场可评分）
- 数据库路径正确
- 数据库文件存在

## 4. 恢复结果

| 指标 | 恢复前 | 清除赛后快照后 |
|------|--------|---------------|
| `teams` | 0 | 48 |
| `matches` | 0 | 72 (final=16, scheduled=56) |
| `match_predictions` | 0 | 630 |
| `prediction_snapshots` | 0 | 540 |
| `prediction_snapshots` (locked) | 0 | 20 |
| `dashboard_revisions` | 0 | 2 |
| 已完赛有赛前快照 | 0 | **0** |
| 已完赛无赛前快照 | 8 | **16** |
| 无效赛后快照 | 0→70→**0** | 已清除 |

### 未来24h比赛

| 比赛 | 开球时间 | 预测 | 锁定快照 |
|------|----------|------|----------|
| FRA vs SEN | 06-16 19:00 | ✅ 10个模型 | ✅ 5个锁定 |
| IRQ vs NOR | 06-16 22:00 | ✅ 10个模型 | ✅ 5个锁定 |
| ARG vs ALG | 06-17 01:00 | ✅ 10个模型 | ✅ 5个锁定 |
| AUT vs JOR | 06-17 04:00 | ✅ 10个模型 | ✅ 5个锁定 |

## 5. 已完赛比赛处理

16场已完赛比赛全部无赛前锁定快照，全部标记为 `not_scorable_no_snapshot`。

**不得补造赛前快照。** 这些比赛将永久不可评分。

## 6. 评分逻辑验证

`_select_scorable_snapshot()` (scoring.py:31-61) 只选择 `snapshotted_at < kickoff` 的快照。
当前 `_scorable_snapshot_rows()` 返回 0 场可评分配对，确认评分逻辑正确。

## 7. 验收标准

| 标准 | 状态 |
|------|------|
| `matches > 0` | ✅ 72 |
| `match_predictions > 0` | ✅ 630 |
| 未来未赛比赛存在 Baseline 预测 | ✅ 56场有预测 |
| T-24h 内未赛比赛存在赛前 locked snapshot | ✅ 4场×5模型=20个锁定 |
| 已完赛16场均为 `not_scorable_no_snapshot` | ✅ |
| 无效赛后快照已清除 | ✅ 70个已清除 |
| 评分逻辑不使用赛后快照 | ✅ 0场可评分 |
| `.env` 不在提交中 | ✅ 已在 `.gitignore` |
| 15/15 回归测试通过 | ✅ |

## 8. 文件变更

| 文件 | 变更 |
|------|------|
| `.env` | 新增（不提交，在 `.gitignore` 中） |
| `.env.example` | 更新数据库路径说明 |
| `backend/scripts/recover_pipeline.py` | 新增，管线恢复脚本（含赛后快照清除） |
| `backend/tests/test_pipeline_recovery.py` | 新增，15项回归测试 |
| `PREDICTION_PIPELINE_RECOVERY.md` | 新增，本报告 |
| `SNAPSHOT_LEGALITY_AUDIT.md` | 新增，快照合法性审计 |
