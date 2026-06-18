# 数据质量检查报告

检查时间: 2026-06-13T11:06:25.700867+00:00

## 概览

- 总检查项: 10
- ✅ 通过: 8
- ⚠️ 警告: 2
- ❌ 失败: 0
- 整体状态: warn

## 详细结果

### ✅ duplicate_match_id
- 状态: pass
- 数量: 0

### ✅ missing_elo_ratings
- 状态: pass
- 数量: 0

### ✅ final_match_without_score
- 状态: pass
- 数量: 0

### ✅ scheduled_match_with_score
- 状态: pass
- 数量: 0

### ⚠️ missing_locked_snapshot
- 状态: warn
- 数量: 4
- 详情: 2026-A-MEX-RSA-2026-06-11, 2026-A-KOR-CZE-2026-06-11, 2026-B-CAN-BIH-2026-06-12, 2026-D-USA-PAR-2026-06-12
- 备注: 已结束但缺少锁定快照的比赛将不参与评分

### ⚠️ fallback_snapshot_ratio
- 状态: warn
- 数量: 2
- 备注: 降级快照占比 100%，过高可能影响评分质量

### ✅ intelligence_after_t30
- 状态: pass
- 数量: 0
- 备注: T-30后情报不应进入赛后评分

### ✅ unmatched_market_odds
- 状态: pass
- 数量: 0

### ✅ missing_player_importance
- 状态: pass
- 数量: 0
- 备注: 缺少球员重要性数据，数值修正可能不准确

### ✅ abnormal_xg_values
- 状态: pass
- 数量: 0
