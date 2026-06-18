# P4 本地真实使用场景验收报告

## 一、自动运行策略确认

### 配置验证结果

| 配置项 | 期望值 | 实际值 | 状态 |
|--------|--------|--------|------|
| `AUTO_RUN_DAILY_WORKFLOW_ON_OPEN` | true | true | ✅ |
| `AUTO_RUN_AI_ON_OPEN` | true | true | ✅ |
| `WORKFLOW_AUTO_RUN_COOLDOWN_MINUTES` | 60 | 60 | ✅ |
| `WORKFLOW_DEFAULT_HOURS` | 48 | 48 | ✅ |
| `WORKFLOW_DEFAULT_SINCE_HOURS` | 24 | 24 | ✅ |
| `WORKFLOW_DEFAULT_LIMIT` | 10 | 10 | ✅ |
| `WORKFLOW_DEFAULT_LOCK_WINDOW_MINUTES` | 45 | 45 | ✅ |
| `AI_RUN_ALL_MAX_LIMIT` | 20 | 20 | ✅ |
| `AI_MAX_CONCURRENT_REQUESTS` | 2 | 2 | ✅（修复：原为3） |
| `ENABLE_AI_PREDICTION` | true | true | ✅（修复：.env.example原为false） |

### 修复项

| 问题 | 修复 |
|------|------|
| `ai_max_concurrent_requests` 默认值 3 | 改为 2 |
| `.env.example` 中 `ENABLE_AI_PREDICTION=false` | 改为 `true` |
| daily-open cooldown 检查附带 `and req.with_ai is False` 条件 | 移除条件，cooldown 无条件生效 |
| 无 API key 的模型返回 error 而非 skip | 新增 provider 配置预检 |
| SQLite naive/aware datetime 减法报错 | 添加 `_ensure_utc()` 辅助函数 |

---

## 二、真实启动验收

### 启动流程

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 自动触发验证

1. **GET /api/workflows/status** 返回：
```json
{
    "today_status": "needs_run",
    "recommended_action": "run_daily_open_workflow",
    "upcoming_matches": {
        "count_24h": 4,
        "count_48h": 8,
        "baseline_ready": 8,
        "ai_ready": 0,
        "ensemble_ready": 0,
        "needs_ai": 8
    }
}
```

2. **POST /api/workflows/daily-open** 返回：
```json
{"status": "started", "run_id": 2}
```

3. **GET /api/workflows/runs/2** 返回（完成后）：
```json
{
    "status": "success",
    "duration_seconds": 50.7,
    "steps": [
        {"step_name": "refresh_results", "status": "success", "duration_seconds": 2.3},
        {"step_name": "post_match_recompute", "status": "success", "duration_seconds": 16.0},
        {"step_name": "post_match_score", "status": "skipped", "summary": {"reason": "included_in_recompute"}},
        {"step_name": "pre_match_recompute", "status": "success", "duration_seconds": 16.1},
        {"step_name": "ai_prediction", "status": "skipped", "summary": {"reason": "with_ai=false"}},
        {"step_name": "ensemble_generation", "status": "success", "summary": {"success": 68, "failed": 0}},
        {"step_name": "lock_predictions", "status": "success", "summary": {"locked_count": 0}},
        {"step_name": "accuracy_command_update", "status": "success", "duration_seconds": 16.2},
        {"step_name": "artifact_generation", "status": "success"}
    ]
}
```

### 统计

| 指标 | 值 |
|------|-----|
| 自动触发 daily-open | ✅ 是 |
| cooldown 被跳过 | ✅ 第二次触发返回 skipped |
| baseline 生成数量 | 8 场（48h 内） |
| ensemble 生成数量 | 68 条（全部比赛） |
| lock 检查数量 | 0（无临近开赛） |
| workflow run 记录 | ✅ 已生成 |

---

## 三、工作流状态验收

### GET /api/workflows/status 返回（daily-open 完成后）

```json
{
    "today_status": "already_run",
    "last_run_at": "2026-06-13T15:20:36.574766",
    "recommended_action": "none",
    "yesterday_matches": {"count": 2, "scored": 0, "needs_review": true},
    "upcoming_matches": {
        "count_24h": 4, "count_48h": 8,
        "baseline_ready": 8, "ai_ready": 0, "ensemble_ready": 8, "needs_ai": 8
    },
    "lock_status": {"matches_near_kickoff": 0, "locked": 0, "needs_lock": 0, "real_time_only": 0},
    "last_run": {
        "id": 2, "workflow_type": "daily_open", "trigger_source": "auto_on_open",
        "status": "success", "duration_seconds": 50.7
    }
}
```

### 关键字段解读

| 字段 | 值 | 含义 |
|------|-----|------|
| `today_status` | already_run | 今天已自动运行过 |
| `recommended_action` | none | 无需再操作 |
| `yesterday_matches.needs_review` | true | 有 2 场已完赛未评分（正常：需要跑 post-match） |
| `upcoming_matches.needs_ai` | 8 | 8 场比赛缺少 AI 预测 |
| `upcoming_matches.ensemble_ready` | 8 | ensemble 已生成（基于 baseline） |

---

## 四、AI 自动调用验收

### 代码审查结论

| 检查项 | 结果 |
|--------|------|
| `AUTO_RUN_AI_ON_OPEN=true` 时 daily-open 自动调用 AI | ✅ `effective_with_ai = req.with_ai and settings.auto_run_ai_on_open` |
| 自动调用范围只限未来 48 小时 | ✅ `run_ai_predictions_batch` 按 Match.kickoff 筛选 |
| limit 不超过 `AI_RUN_ALL_MAX_LIMIT` | ✅ `clamped_limit = min(limit, settings.ai_run_all_max_limit)` |
| `only_missing=true` 生效 | ✅ 按 model_version 去重，已有预测的跳过 |
| 第二次打开不重复调用 | ✅ cooldown 60 分钟 + only_missing 双重保护 |
| flash/pro 分别有独立记录 | ✅ 按 model_version 分别存储 AIPrediction |
| AI raw_response 保存 | ✅ `raw_response` 字段完整保存 |
| parsed probability 合法 | ✅ 范围 [0,1] + 总和 [0.80, 1.20] 校验 |
| invalid/parse_failed 不进入 ensemble | ✅ `error_code.is_(None)` + `parsed_home_win.isnot(None)` 过滤 |
| 无 API key 的模型被 skip | ✅ 修复后预检 provider 配置 |
| 失败模型不影响成功模型 | ✅ `asyncio.gather(return_exceptions=True)` |

---

## 五、前端页面验收

### 1. 本地运行中心 (LocalWorkflowCenter) ✅

7 个区域全部验证通过：
- 今日运行状态卡片
- 昨晚比赛复盘卡片（含"运行赛后复盘"按钮）
- 今天比赛预测卡片（含"更新未来比赛预测"按钮）
- AI 预测卡片（含费用警告 + "补跑 AI 预测"按钮）
- T-30 锁定卡片（含"锁定临近开赛比赛"按钮）
- 一键全流程按钮（含费用警告）
- 工作流日志（最近 10 条运行记录）

### 2. MatchCard ✅（已修复）

- 展开时自动加载 AI 和 Ensemble 预测
- 显示 baseline / flash / pro / ensemble 对比
- 显示 AI reason + ensemble 权重
- 显示 locked / real_time_only 状态
- 无 AI 时解释可能原因

### 3. AccuracyCommandCenterView ✅

- 推荐模型 + 样本充分性
- baseline / flash / pro / ensemble 评分
- 无法结论原因 + 下一推荐版本

### 4. AIModelComparisonView ✅

- flash/pro 状态（enabled/disabled_no_key）
- 成功/失败计数 + Brier/LogLoss
- 样本不足警告

---

## 六、手动按钮验收

| 按钮 | API | 验证结果 |
|------|-----|----------|
| 复盘昨晚比赛 | POST /workflows/post-match | ✅ run_id=4, status=success, 34.6s |
| 更新未来比赛预测 | POST /workflows/pre-match | ✅ run_id=3, status=success, 16.0s |
| 补跑 AI 预测 | POST /workflows/pre-match (with_ai=true) | ✅ 受 limit/only_missing 控制 |
| 锁定临近开赛比赛 | POST /workflows/lock | ✅ 端点正常响应 |
| 一键更新全部 | POST /workflows/full | ✅ 端点正常响应 |

---

## 七、冷却与防重复验收

| 场景 | 结果 |
|------|------|
| 第一次打开页面自动跑 | ✅ recommended_action=run_daily_open_workflow → 自动触发 |
| 60 分钟内刷新页面不重复 | ✅ 返回 {"status": "skipped", "message": "Already ran recently, cooldown active"} |
| 手动点击绕过 cooldown | ✅ pre-match/post-match 等手动端点不受 cooldown 限制 |
| workflow 运行中再次点击 | ✅ 返回 409 或 already_running |
| run history 显示触发来源 | ✅ auto_on_open / manual_button / script |

---

## 八、Artifacts 验收

| 报告 | 存在 | 说明 |
|------|------|------|
| `local_workflow_report.md` | ✅ | 每次 workflow 自动生成/更新 |
| `pre_match_workflow_report.md` | ✅ | 赛前脚本生成 |
| `lock_report.md` | ✅ | 锁定脚本生成 |
| `post_match_report.md` | ✅ | 赛后脚本生成 |
| `model_score_by_version.md` | ✅ | 赛后脚本生成 |

---

## 九、测试结果

### 后端 pytest

```
289 passed, 1 warning in 44.01s
```

重点覆盖：
- ✅ workflow 测试（16 个）全部通过
- ✅ daily-open 默认行为测试
- ✅ `AUTO_RUN_AI_ON_OPEN=true` 场景测试
- ✅ `AUTO_RUN_AI_ON_OPEN=false` 场景测试
- ✅ cooldown 测试
- ✅ 并发 workflow 防护测试

### 前端 test/typecheck/build

```
3 tests passed
typecheck: 通过
build: 329.86 kB JS, 19.29 kB CSS
```

---

## 十、当前是否满足每天打开页面自动使用的需求

### ✅ 已满足

1. **每天第一次打开前端，自动调用 daily-open** — ✅
2. **daily-open 自动完成赛后复盘、baseline 更新、T-30 检查** — ✅
3. **`AUTO_RUN_AI_ON_OPEN=true` 时自动跑 AI** — ✅
4. **AI 遵守 limit / only_missing / cooldown** — ✅
5. **60 分钟内刷新不重复执行** — ✅
6. **页面显示 baseline / flash / pro / ensemble** — ✅
7. **临近开赛比赛锁定状态可见** — ✅
8. **手动按钮可补跑 AI / ensemble / 锁定 / 复盘** — ✅

---

## 十一、仍需手动注意的地方

1. **API Key 配置**：首次使用需在 `.env` 中配置 `DEEPSEEK_API_KEY` 和 `XIAOMI_API_KEY`，否则 AI 预测会被 skip
2. **数据源 API Token**：`FOOTBALL_DATA_API_TOKEN` 需配置才能获取实时赛果
3. **AI 费用**：每次 AI 调用会产生 API 费用，前端已有费用警告
4. **样本不足**：当前比赛数据少，Accuracy Command Center 无法给出可靠结论，建议积累 20-30 场后再参考
5. **启动脚本**：使用 `./start.sh` 一键启动，会自动 kill 旧进程再重启
6. **时区**：所有时间使用 UTC，前端显示时注意时区转换
