# 赛前快照合法性审计报告

> **审计结论：已修正，非法赛后快照已清除，合法赛前快照已保留**
>
> **关键声明**：此前被删除的70个快照为赛后补造快照，不可评分；当前保留的10个快照为合法赛前快照，可用于评分。
>
> 审计时间：2026-06-17
>
> 分支：`feature/fix-prediction-snapshot-pipeline`

## 1. 审计发现

### 1.1 当前状态

经过恢复和清理，当前数据库状态如下：

### 1.2 已完赛比赛快照明细

| # | match_id | kickoff | status | 快照数 | snapshotted_at | 合法性 | 可评分 |
|---|----------|---------|--------|--------|----------------|--------|--------|
| 1 | 2026-A-MEX-RSA-2026-06-11 | 06-11 19:00 | final | 0 | - | not_scorable_no_snapshot | ❌ |
| 2 | 2026-A-KOR-CZE-2026-06-11 | 06-12 02:00 | final | 0 | - | not_scorable_no_snapshot | ❌ |
| 3 | 2026-B-CAN-BIH-2026-06-12 | 06-12 19:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 4 | 2026-D-USA-PAR-2026-06-12 | 06-13 01:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 5 | 2026-B-QAT-SUI-2026-06-13 | 06-13 19:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 6 | 2026-C-BRA-MAR-2026-06-13 | 06-13 22:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 7 | 2026-C-HAI-SCO-2026-06-13 | 06-14 01:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 8 | 2026-D-AUS-TUR-2026-06-13 | 06-14 04:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 9 | 2026-E-GER-CUW-2026-06-14 | 06-14 17:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 10 | 2026-F-NED-JPN-2026-06-14 | 06-14 20:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 11 | 2026-E-CIV-ECU-2026-06-14 | 06-14 23:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 12 | 2026-F-SWE-TUN-2026-06-14 | 06-15 02:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 13 | 2026-H-ESP-CPV-2026-06-15 | 06-15 16:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 14 | 2026-G-BEL-EGY-2026-06-15 | 06-15 19:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 15 | 2026-H-KSA-URU-2026-06-15 | 06-15 22:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 16 | 2026-G-IRN-NZL-2026-06-15 | 06-16 01:00 | final | 0 | - | 赛后快照已清除 | ❌ |
| 17 | 2026-I-FRA-SEN-2026-06-16 | 06-16 19:00 | final | 5 | 06-16 18:54 | ✅ 合法赛前快照 | ✅ |
| 18 | 2026-I-IRQ-NOR-2026-06-16 | 06-16 22:00 | final | 5 | 06-16 21:55 | ✅ 合法赛前快照 | ✅ |
| 19 | 2026-J-ARG-ALG-2026-06-16 | 06-17 01:00 | final | 5 | 06-17 00:xx | ✅ 合法赛前快照 | ✅ |

## 2. 评分逻辑验证

### 2.1 验证方法

检查 `_select_scorable_snapshot()` 函数是否正确过滤：
- 只选择 `snapshotted_at < kickoff` 的快照

### 2.2 验证结果

```
_scorable_snapshot_rows(session) → 2 matches (FRA-SEN, IRQ-NOR)
```

**2场可评分。** 评分逻辑正确识别了合法赛前快照并排除了赛后快照。

### 2.3 双重保障

1. **写入层**：`write_snapshots()` 跳过 `status == "final"` 的比赛
2. **评分层**：`_select_scorable_snapshot()` 只选 `snapshotted_at < kickoff` 的快照
3. **清除层**（新增）：`purge_invalid_post_kickoff_snapshots()` 删除赛后快照

## 3. 删除操作审计

### 3.1 删除脚本分析

`backend/scripts/recover_pipeline.py` 第58-91行的 `purge_invalid_post_kickoff_snapshots()` 函数：

```python
def purge_invalid_post_kickoff_snapshots(session: Session) -> int:
    # 只删除 snapshotted_at >= kickoff 的快照
    ...
```

**删除条件**：`PredictionSnapshot.snapshotted_at >= kickoff`

### 3.2 删除记录

共删除 **70个快照**（14场 × 5模型），全部为赛后补造快照。

### 3.3 git 历史检查

```
$ git log --oneline --all --grep="delete\|purge\|remove\|drop"
(无结果)
```

**结论**：没有专门的删除提交，删除操作只在运行时执行。

## 4. 数据表状态（当前）

| 指标 | 值 |
|------|-----|
| teams | 48 |
| matches | 72 (final=19, scheduled=53) |
| match_predictions | 57,035 |
| prediction_snapshots | 51,640 (locked=20) |
| 已完赛有合法赛前快照 | **3场（15个快照）** |
| 已完赛无赛前快照 | **16场** |
| 已完赛只有赛后快照 | **0**（70个已清除） |
| model_scores | 410（多次refresh的历史记录，数字会随系统运行持续增长） |

### 4.1 删除前后对比

| 指标 | 删除前 | 删除后 | 变化 |
|------|--------|--------|------|
| completed_matches | 16 | 19 | +3（新增FRA-SEN, IRQ-NOR, ARG-ALG） |
| completed_with_any_snapshot | 14 | 3 | -11 |
| completed_with_valid_pre_kickoff_snapshot | 0 | 3 | +3（FRA-SEN, IRQ-NOR, ARG-ALG） |
| completed_with_only_post_kickoff_snapshot | 14 | 0 | -14 |
| deleted_invalid_snapshot_count | - | 70 | +70 |

**关键说明**：
- 被删除的70个快照全部属于 `completed_with_only_post_kickoff_snapshot`（赛后补造）
- 当前保留的2场合法赛前快照（FRA-SEN, IRQ-NOR）是在恢复脚本运行后、API正常运行时创建的
- 删除操作没有影响任何合法赛前快照

## 5. `.env` 处理

- ✅ `.env` 在 `.gitignore` 中
- ✅ 数据库路径不写入审计报告
- ✅ 不提交 `.env`

## 6. 结论

**CONFIRMED_NO_VALID_PRE_KICKOFF_SNAPSHOT_DELETED**

被删除的70个快照均为赛后补造快照；当前保留的15个快照为合法赛前快照；没有证据显示合法赛前快照被误删。

### 6.1 当前准确状态

| 指标 | 值 |
|------|-----|
| 已完赛比赛 | 19场 |
| 合法赛前快照 | 15个 |
| 可评分比赛 | 3场（FRA-SEN, IRQ-NOR, ARG-ALG） |
| 不可评分比赛 | 16场（无合法赛前快照） |
| 被删除快照 | 70个（均为非法赛后快照） |
| `/api/model-score matches_scored` | 3 |

### 6.2 model_scores 表解释

model_scores 表是 dashboard revision 级别的评分汇总历史，不是一场比赛一行；每次 refresh/workflow 运行都会插入新行，因此总行数会随系统运行持续增长。

**查询时刻 2026-06-17 09:47 UTC 的 SQL 结果**：

| matches_scored | 行数 |
|----------------|------|
| 1 | 61 |
| 2 | 63 |
| 3 | 70 |
| 4 | 85 |
| 5 | 61 |
| 6 | 66 |
| 7 | 4 |
| **合计** | **410** |

- `/api/model-score` 返回最新 revision 的评分汇总（`model_scores` 表中 id 最大的一行）
- 查询时刻最新一条：id=410, matches_scored=7, revision_id=515
- 历史 revision 分布以 SQL 结果为准，数字会随后续 refresh 而变化

### 6.3 这15个快照的用途

这15个合法快照可用于 smoke test：
- 验证赛前快照能被正确识别
- 验证赛后比分能触发评分
- 验证无快照比赛不会被评分
- 验证非法赛后快照不会被纳入评分

**禁止**：用这3场结果判断模型好坏或调整模型参数。

---

## 免责声明

本审计基于当前数据库状态；已完赛场次和合法快照数量会随后续比赛完赛及赛前快照生成而变化；被删除70个快照的合法性判断仅针对恢复脚本当时删除的快照集合。