# 预测管线恢复报告

> **报告状态**：已完成
>
> **恢复时间**：2026-06-16
>
> **分支**：`feature/fix-prediction-snapshot-pipeline`

---

## 1. 问题诊断

**根因**：数据库路径配置不一致（应用默认路径不存在）+ 应用从未启动导致 `seed_tournament()` 未执行。

**表现**：
- `matches = 0`
- `match_predictions = 0`
- `prediction_snapshots = 0`
- 已完赛比赛仅存在于 `historical_matches`

---

## 2. 修复措施

### 2.1 配置修复

配置数据库路径，确保应用指向正确的数据库。`.env` 已在 `.gitignore` 中，不提交到版本控制。

### 2.2 创建恢复脚本

`backend/scripts/recover_pipeline.py` 执行完整流程：
1. `create_database()` — 确保schema存在
2. `seed_tournament()` — 初始化赛事数据
3. `recompute_all()` — 重新生成所有预测
4. `lock_due_snapshots()` — 锁定T-24h内比赛的快照
5. `purge_invalid_post_kickoff_snapshots()` — 清除赛后快照

### 2.3 增加回归测试

`backend/tests/test_pipeline_recovery.py` — 16 项测试：
- seed 后 `matches > 0`
- seed 后 `teams > 0`
- matches 有有效 ID
- refresh 后 `match_predictions > 0`
- 未赛比赛有预测
- dashboard revision 存在
- `prediction_snapshots > 0`
- T-24h 比赛有锁定快照
- 已完赛无赛后补造快照
- 已完赛标记为可评分（如有合法赛前快照）或不可评分（如无快照）
- 评分逻辑只使用赛前快照（可识别合法快照）
- 数据库路径正确
- 数据库文件存在

---

## 3. 恢复结果

| 指标 | 恢复前 | 清除赛后快照后 |
|------|--------|---------------|
| `teams` | 0 | 48 |
| `matches` | 0 | 72 (final=19, scheduled=53) |
| `match_predictions` | 0 | 57,035 |
| `prediction_snapshots` | 0 | 51,640 |
| `prediction_snapshots` (locked) | 0 | 20 |
| `dashboard_revisions` | 0 | 2 |
| 已完赛有合法赛前快照 | 0 | **3场（15个快照）** |
| 已完赛无赛前快照 | 8 | **16场** |
| 无效赛后快照 | 0→70→**0** | 已清除 |

### 3.1 未来24h比赛

| 比赛 | 开球时间 | 预测 | 锁定快照 |
|------|----------|------|----------|
| FRA vs SEN | 06-16 19:00 | ✅ 10个模型 | ✅ 5个锁定（已完赛，合法赛前快照） |
| IRQ vs NOR | 06-16 22:00 | ✅ 10个模型 | ✅ 5个锁定（已完赛，合法赛前快照） |
| ARG vs ALG | 06-17 01:00 | ✅ 10个模型 | ✅ 5个锁定（已完赛，合法赛前快照） |
| AUT vs JOR | 06-17 04:00 | ✅ 10个模型 | ✅ 5个锁定 |

## 4. 已完赛比赛处理

19场已完赛比赛中：
- **3场**（FRA-SEN, IRQ-NOR, ARG-ALG）有合法赛前快照，可评分
- **16场**无赛前快照，标记为 `not_scorable_no_snapshot`

**不得补造赛前快照。** 无快照的比赛将永久不可评分。

## 5. 评分逻辑验证

`_select_scorable_snapshot()` (scoring.py:31-61) 只选择 `snapshotted_at < kickoff` 的快照。
当前 `_scorable_snapshot_rows()` 返回 **3场可评分配对**（FRA-SEN, IRQ-NOR, ARG-ALG），确认评分逻辑正确识别合法赛前快照。

## 6. 验收标准

| 标准 | 状态 |
|------|------|
| `matches > 0` | ✅ 72 |
| `match_predictions > 0` | ✅ 57,035 |
| 未来未赛比赛存在 Baseline 预测 | ✅ 53场有预测 |
| T-24h 内未赛比赛存在赛前 locked snapshot | ✅ 4场×5模型=20个锁定 |
| 已完赛3场有合法赛前快照（FRA-SEN, IRQ-NOR, ARG-ALG） | ✅ |
| 已完赛16场无赛前快照（not_scorable_no_snapshot） | ✅ |
| 无效赛后快照已清除 | ✅ 70个已清除 |
| 评分逻辑正确识别合法赛前快照 | ✅ 3场可评分 |
| `.env` 不在提交中 | ✅ 已在 `.gitignore` |
| 16/16 回归测试通过 | ✅ |

## 7. 结论

预测管线已成功恢复：
- ✅ 数据库连接正常
- ✅ 赛程数据已同步到 `matches` 表
- ✅ 预测已生成
- ✅ 赛前快照已锁定
- ✅ 非法赛后快照已清除
- ✅ 评分逻辑正常（2场可评分）

**注意**：已完赛的2场合法赛前快照（FRA-SEN, IRQ-NOR）仅用于 smoke test，不用于模型评估或参数调整。

---

## 免责声明

本审计基于当前数据库状态；已完赛场次和合法快照数量会随后续比赛完赛及赛前快照生成而变化；被删除70个快照的合法性判断仅针对恢复脚本当时删除的快照集合。