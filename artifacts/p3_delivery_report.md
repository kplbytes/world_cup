# P3 世界杯本地实战运行闭环 - 交付报告

## 1. 修改文件列表

### 后端新增文件
| 文件 | 说明 |
|------|------|
| `backend/app/ai/providers/openai_compat.py` | OpenAI 兼容基类，消除 deepseek/xiaomi 重复 |
| `backend/app/ai/lock_status.py` | T-30 锁定逻辑共享工具 |
| `backend/app/ai/provider_registry.py` | Provider 注册表，管理实例和关闭 |
| `backend/app/services/dashboard.py` | 单场/单队直查函数 |
| `backend/scripts/run_pre_match_workflow.py` | 赛前预测流程脚本 |
| `backend/scripts/lock_pre_match_predictions.py` | T-30 锁定流程脚本 |
| `backend/scripts/run_post_match_workflow.py` | 赛后复盘流程脚本 |
| `backend/scripts/__init__.py` | 包初始化 |

### 后端修改文件
| 文件 | 说明 |
|------|------|
| `backend/app/ai/providers/deepseek.py` | 150 行 → 12 行，继承基类 |
| `backend/app/ai/providers/xiaomi.py` | 156 行 → 12 行，继承基类 |
| `backend/app/ai/service.py` | 配置统一 + 概率校验 + 并发调用 + 防重复 + 锁定提取 + 默认值 |
| `backend/app/ai/ensemble.py` | 使用共享锁定逻辑 |
| `backend/app/config.py` | 新增 app_mode、ai_run_all_max_limit、ai_max_concurrent_requests |
| `backend/app/main.py` | Provider 关闭 + lifespan shutdown |
| `backend/app/api/routes/ai_routes.py` | limit 校验 + retry_failed 参数 |
| `backend/app/api/routes/dashboard_routes.py` | 单场/单队走直查 |
| `backend/app/services/accuracy_command.py` | ACC 补全 18 个字段 |

### 后端新增测试
| 文件 | 测试数 |
|------|--------|
| `tests/test_openai_compat_provider.py` | 13 |
| `tests/test_config_consistency.py` | 7 |
| `tests/test_ai_improvements.py` | 20 |
| `tests/test_cost_control.py` | 6 |
| `tests/test_provider_cleanup.py` | 5 |

### 前端修改文件
| 文件 | 说明 |
|------|------|
| `frontend/src/api.ts` | fetchWithTimeout 超时控制 |
| `frontend/src/components/ProbabilityBar.tsx` | NaN 处理 |
| `frontend/src/components/AllMatches.tsx` | 动态比赛数量 |
| `frontend/src/components/AIModelComparisonView.tsx` | 状态/成功失败/Brier/LogLoss |
| `frontend/src/components/MatchCard.tsx` | 多 AI 展示 + 锁定状态 + ensemble 权重 |
| `frontend/src/components/AccuracyPanel.tsx` | 样本不足警告 + 默认推荐 baseline |
| `frontend/src/components/AccuracyCommandCenterView.tsx` | 推荐模型/错误模式/AI效果 |
| `frontend/src/main.tsx` | mutations retry=false |

### 配置文件
| 文件 | 说明 |
|------|------|
| `.env.example` | 补全所有配置项 |
| `backend/pyproject.toml` | 添加 pytest-asyncio 依赖 |

---

## 2. 后端 pytest 完整输出

```
273 passed, 1 warning in 47.88s
```

---

## 3. 前端 test/typecheck/build 输出

```
Test Files  1 passed (1)
Tests       3 passed (3)

typecheck: 通过
build: 314.01 kB JS, 19.29 kB CSS
```

---

## 4. pyproject 依赖变更

新增：`pytest-asyncio>=0.21` 到 `[project.optional-dependencies] test`

---

## 5. .env.example 最终内容

```env
# Local SQLite database
DATABASE_PATH=backend/data/world-cup.sqlite3

# Optional
FOOTBALL_DATA_API_TOKEN=

# Refresh and simulation settings
REFRESH_INTERVAL_MINUTES=15
LIVE_REFRESH_INTERVAL_MINUTES=2
SIMULATION_ITERATIONS=50000
SIMULATION_SEED=20260613

# Numerical adjustment
ENABLE_NUMERICAL_ADJUSTMENTS=false

# AI prediction settings
APP_MODE=local
ENABLE_AI_PREDICTION=false
AI_RUN_MODE=manual
AI_PROMPT_VERSION=worldcup-ai-v1
AI_TEMPERATURE=0
AI_TIMEOUT_SECONDS=30
AI_MAX_RETRIES=2
AI_MAX_CONCURRENT_REQUESTS=2
AI_RUN_ALL_MAX_LIMIT=20

# DeepSeek AI provider
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=

# Xiaomi MiMo AI provider
XIAOMI_API_KEY=
XIAOMI_BASE_URL=https://api.xiaomimimo.com/v1
```

---

## 6. 新增脚本说明

### run_pre_match_workflow.py
```bash
cd backend
.venv/bin/python scripts/run_pre_match_workflow.py --hours 48 --limit 10 --with-ai --with-ensemble
```
- 查找未来 48 小时内未开赛比赛
- 刷新基础数据 + recompute baseline
- `--with-ai`: 对每场比赛调用 enabled AI models（遵守 only_missing + limit）
- `--with-ensemble`: 生成 ensemble-v1
- 输出 `artifacts/pre_match_workflow_report.md`

### lock_pre_match_predictions.py
```bash
cd backend
.venv/bin/python scripts/lock_pre_match_predictions.py --window-minutes 45
```
- 查找开赛窗口内的比赛
- 锁定 baseline / AI / ensemble 预测
- T-30 后生成的预测标记 real_time_only
- 输出 `artifacts/lock_report.md`

### run_post_match_workflow.py
```bash
cd backend
.venv/bin/python scripts/run_post_match_workflow.py --since-hours 24
```
- Recompute（触发赛后评分）
- 查找最近 24 小时已完赛比赛
- 获取预测摘要 + 模型评分
- 输出 `artifacts/post_match_report.md` + `artifacts/model_score_by_version.md`

---

## 7-9. 报告示例

运行对应脚本后自动生成在 `artifacts/` 目录下。

---

## 10. /api/accuracy-command-center 返回字段

| 字段 | 说明 |
|------|------|
| `recommended_model` | 当前推荐模型 |
| `recommendation_reason` | 推荐理由 |
| `sample_sufficient` | 样本是否足够 |
| `baseline_score` | baseline 评分 |
| `market_score` | 市场评分 |
| `ai_model_scores` | 各 AI 模型评分 |
| `ensemble_score` | ensemble 评分 |
| `max_error_type` | 最大误差类型 |
| `draw_underestimated` | 平局是否被低估 |
| `strong_team_overestimated` | 强队是否被高估 |
| `upset_underestimated` | 冷门是否被低估 |
| `ai_helpful` | AI 是否有帮助 |
| `ensemble_helpful` | ensemble 是否有帮助 |
| `next_recommended_version` | 下一轮推荐版本 |
| `cannot_conclude_reason` | 不能下结论的原因 |
| `recent_match_scores` | 最近 5 场评分明细 |
| `upcoming_matches` | 下一批需预测比赛 |

---

## 12. 当前仍不能下准确率结论的原因

1. **样本不足**：当前系统缺少足够的终场真实比赛数据，Brier/LogLoss/HitRate 等指标不具备统计显著性
2. **模型未充分对比**：AI 模型（flash/pro）和 ensemble 需要在更多比赛中与 baseline 对比
3. **校准度未验证**：概率校准（predicted 60% 是否真的约 60% 发生）需要大量样本
4. **建议**：积累至少 20-30 场终场数据后，Accuracy Command Center 才能给出有意义的推荐

---

## 13. 世界杯期间每日使用流程建议

### 每轮比赛前（赛前 1-2 天）
```bash
cd backend
.venv/bin/python scripts/run_pre_match_workflow.py --hours 48 --limit 10 --with-ai --with-ensemble
```

### 开赛前 30 分钟
```bash
cd backend
.venv/bin/python scripts/lock_pre_match_predictions.py --window-minutes 45
```

### 赛后（每天早上）
```bash
cd backend
.venv/bin/python scripts/run_post_match_workflow.py --since-hours 24
```

### 查看结果
打开前端 `http://localhost:5173`：
1. **准确率指挥室** → 当前哪个模型最好、样本够不够
2. **AI 模型对比** → flash vs pro 成功/失败/Brier
3. **MatchCard** → 每场比赛 baseline/flash/pro/ensemble 对比
4. **决策视图** → 复盘已完赛比赛

### 日常注意
- 不要默认启用 AI（`ENABLE_AI_PREDICTION=false`）
- 使用 `--with-ai` 手动触发，避免误操作浪费 API 调用
- `limit` 有上限保护（默认 20）
- `only_missing=true` 避免重复调用
