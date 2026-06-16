# 赛前快照合法性审计报告

> **审计结论：已修正，所有赛后快照已清除**
>
> **关键声明**：此前14场所谓快照并非真实赛前快照，而是赛后补造快照，因此已清除，不可评分。
>
> 审计时间：2026-06-16
>
> 分支：`feature/fix-prediction-snapshot-pipeline`

## 1. 审计发现

### 1.1 初始状态（恢复脚本首次运行后）

恢复脚本 `recover_pipeline.py` 首次运行时，种子数据中只有2场标记为 `final`（MEX-RSA, KOR-CZE），其他14场仍为 `scheduled`。`recompute_all()` → `write_snapshots()` 为这些 `scheduled` 比赛创建了快照，但 `snapshotted_at = 2026-06-16 06:22:47`，全部在比赛开球之后。

**问题**：70个赛后快照被创建，涉及14场已完赛比赛 × 5个模型版本。

### 1.2 已完赛比赛快照明细

| # | match_id | kickoff | status | 赛后快照数 | snapshotted_at | 合法性 |
|---|----------|---------|--------|-----------|----------------|--------|
| 1 | 2026-A-MEX-RSA-2026-06-11 | 06-11 19:00 | final | 0 | - | not_scorable_no_snapshot |
| 2 | 2026-A-KOR-CZE-2026-06-11 | 06-12 02:00 | final | 0 | - | not_scorable_no_snapshot |
| 3 | 2026-B-CAN-BIH-2026-06-12 | 06-12 19:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 4 | 2026-D-USA-PAR-2026-06-12 | 06-13 01:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 5 | 2026-B-QAT-SUI-2026-06-13 | 06-13 19:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 6 | 2026-C-BRA-MAR-2026-06-13 | 06-13 22:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 7 | 2026-C-HAI-SCO-2026-06-13 | 06-14 01:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 8 | 2026-D-AUS-TUR-2026-06-13 | 06-14 04:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 9 | 2026-E-GER-CUW-2026-06-14 | 06-14 17:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 10 | 2026-F-NED-JPN-2026-06-14 | 06-14 20:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 11 | 2026-E-CIV-ECU-2026-06-14 | 06-14 23:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 12 | 2026-F-SWE-TUN-2026-06-14 | 06-15 02:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 13 | 2026-H-ESP-CPV-2026-06-15 | 06-15 16:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 14 | 2026-G-BEL-EGY-2026-06-15 | 06-15 19:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 15 | 2026-H-KSA-URU-2026-06-15 | 06-15 22:00 | final | 5→0 | 06-16 06:22 | **已清除** |
| 16 | 2026-G-IRN-NZL-2026-06-15 | 06-16 01:00 | final | 5→0 | 06-16 06:22 | **已清除** |

## 2. 评分逻辑验证

### 2.1 `_select_scorable_snapshot()` 规则

代码位置：`backend/app/services/scoring.py` 第31-61行

```python
# Only consider snapshots created before kickoff
pre_kickoff = [
    snap for snap in snapshots
    if _ensure_utc(snap.snapshotted_at) < kickoff
]
if pre_kickoff:
    return max(pre_kickoff, key=lambda snap: _ensure_utc(snap.snapshotted_at))
return None
```

**规则**：只选择 `snapshotted_at < kickoff` 的快照。如果没有赛前快照，返回 `None`，该比赛不参与评分。

### 2.2 验证结果

```
_scorable_snapshot_rows(session) → 0 matches
```

**0场可评分。** 评分逻辑正确排除了所有赛后快照。

### 2.3 双重保障

1. **写入层**：`write_snapshots()` 跳过 `status == "final"` 的比赛
2. **评分层**：`_select_scorable_snapshot()` 只选 `snapshotted_at < kickoff` 的快照
3. **清除层**（新增）：`purge_invalid_post_kickoff_snapshots()` 删除赛后快照

## 3. locked 数量变化解释

| 时间点 | locked 数量 | 原因 |
|--------|------------|------|
| 恢复脚本首次运行后 | 20 | 4场未来24h比赛 × 5模型 = 20个锁定快照 |
| API 启动后 | 68 | dashboard.json 中的 `snapshot_status.locked` 含义不同于 `is_pre_match_locked`；API refresh 为更多比赛创建了快照 |
| 清除赛后快照后 | 20 | 70个无效赛后快照已删除，仅保留20个合法赛前锁定快照 |

**所有20个锁定快照均为未赛比赛，`snapshotted_at < kickoff`，合法。**

## 4. 数据表状态（清除后）

| 指标 | 值 |
|------|-----|
| teams | 48 |
| matches | 72 (final=16, scheduled=56) |
| match_predictions | 630 |
| prediction_snapshots | 540 (locked=20) |
| 已完赛有赛前快照 | **0** |
| 已完赛无赛前快照 | **16** |
| 已完赛只有赛后快照 | **0**（已清除） |
| model_scores | 0（无可评分配对） |

### 4.1 删除前后对比

| 指标 | 删除前 | 删除后 | 变化 |
|------|--------|--------|------|
| completed_matches | 16 | 16 | 0 |
| completed_with_any_snapshot | 14 | 0 | -14 |
| completed_with_valid_pre_kickoff_snapshot | 0 | 0 | 0 |
| completed_with_only_post_kickoff_snapshot | 14 | 0 | -14 |
| deleted_invalid_snapshot_count | - | 70 | +70 |

**关键说明**：
- `completed_with_valid_pre_kickoff_snapshot` 始终为 0，证明从未存在任何合法赛前快照
- 被删除的70个快照全部属于 `completed_with_only_post_kickoff_snapshot`
- 删除操作没有影响任何合法赛前快照（因为原本就不存在）

## 5. `.env` 处理

- `.env` 已在 `.gitignore` 中（第1行）
- 提交中不包含 `.env`
- 数据库路径配置已写入 `.env.example`

## 6. 恢复脚本边界规则

`recover_pipeline.py` 现在包含 `purge_invalid_post_kickoff_snapshots()` 步骤：

1. ✅ 不给已开赛/已完赛比赛补造可评分快照
2. ✅ 只对未开赛比赛生成预测
3. ✅ 只对 `now <= kickoff` 且距离开赛≤24h的比赛生成 locked snapshot
4. ✅ 对已完赛比赛只更新赛果，不生成赛前快照
5. ✅ 清除所有 `snapshotted_at >= kickoff` 的无效快照

## 7. 测试结果

15/15 通过：

```
TestPipelineSeed::test_matches_greater_than_zero PASSED
TestPipelineSeed::test_teams_greater_than_zero PASSED
TestPipelineSeed::test_matches_have_valid_ids PASSED
TestPipelinePredictions::test_match_predictions_greater_than_zero PASSED
TestPipelinePredictions::test_scheduled_matches_have_predictions PASSED
TestPipelinePredictions::test_dashboard_revision_exists PASSED
TestPipelineSnapshots::test_prediction_snapshots_greater_than_zero PASSED
TestPipelineSnapshots::test_t24h_matches_have_locked_snapshots PASSED
TestCompletedMatchesNoBackfill::test_completed_matches_no_pre_match_locked_snapshots PASSED
TestCompletedMatchesNoBackfill::test_completed_matches_marked_not_scorable PASSED
TestCompletedMatchesNoBackfill::test_completed_matches_no_post_kickoff_snapshots PASSED
TestCompletedMatchesNoBackfill::test_completed_matches_no_snapshots_at_all PASSED
TestScoringOnlyUsesPreKickoffSnapshots::test_scorable_matches_count_is_zero PASSED
TestDatabasePath::test_database_path_points_to_correct_file PASSED
TestDatabasePath::test_database_file_exists PASSED
```

## 8. 审计结论

**已修正。** 70个无效赛后快照已清除，16场已完赛比赛全部标记为 `not_scorable_no_snapshot`。评分逻辑 `_select_scorable_snapshot()` 正确排除了赛后快照。恢复脚本已增加清除步骤，防止未来再次产生无效快照。
