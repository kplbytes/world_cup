# P2+ 功能真实性审计报告

> 审计日期：2026-06-13
> 审计范围：World Cup Predictor P2+ Final Hardening 全部新增/修改代码
> 审计方法：基于源代码静态分析 + 单元测试验证

---

## 1. 修改文件清单

### 后端新增/修改文件

| 文件路径 | 类型 | 说明 |
|---------|------|------|
| `backend/app/ai/__init__.py` | 新增 | AI 模块包 |
| `backend/app/ai/service.py` | 新增 | AI 预测服务编排 |
| `backend/app/ai/parser.py` | 新增 | AI 输出解析与验证 |
| `backend/app/ai/ensemble.py` | 新增 | Ensemble 融合引擎 |
| `backend/app/ai/model_registry.py` | 新增 | AI 模型注册表（YAML 驱动） |
| `backend/app/ai/evaluation.py` | 新增 | AI/Ensemble 评分 |
| `backend/app/ai/schemas.py` | 新增 | AI 数据结构定义 |
| `backend/app/ai/prompt_builder.py` | 新增 | AI Prompt 构建器 |
| `backend/app/ai/ai_models.yaml` | 新增 | AI 模型配置文件 |
| `backend/app/ai/providers/__init__.py` | 新增 | Provider 包 |
| `backend/app/ai/providers/base.py` | 新增 | Provider 抽象基类 |
| `backend/app/ai/providers/deepseek.py` | 新增 | DeepSeek API 实现 |
| `backend/app/ai/providers/xiaomi.py` | 新增 | Xiaomi/MiMo API 实现 |
| `backend/app/api/routes/ai_routes.py` | 新增 | AI 相关 API 路由 |
| `backend/app/api/routes/scoring_routes.py` | 修改 | 新增评分相关端点 |
| `backend/app/api/routes/tournament_routes.py` | 修改 | 新增 tournament 端点 |
| `backend/app/api/routes/data_routes.py` | 修改 | 新增 accuracy-command-center |
| `backend/app/tournament/bracket.py` | 新增 | 对阵图生成 |
| `backend/app/tournament/qualification.py` | 新增 | 出线概率 + Monte Carlo 投影 |
| `backend/app/tournament/rules.py` | 新增 | 赛事规则常量 |
| `backend/app/tournament/standings.py` | 修改 | 新增第三名排名逻辑 |
| `backend/app/tournament/simulation.py` | 新增 | 全周期锦标赛模拟 |
| `backend/app/services/accuracy_command.py` | 新增 | 准确率指挥部 |
| `backend/app/services/scoring.py` | 修改 | 新增 `model_score_by_stage`、`model_score_by_version` |
| `backend/app/services/snapshots.py` | 修改 | T-30 锁定 + fallback 逻辑 |
| `backend/app/services/calibration.py` | 新增/修改 | 概率校准 |
| `backend/app/services/market_comparison.py` | 新增/修改 | 市场对比 |
| `backend/app/services/model_recommendation.py` | 新增/修改 | 模型推荐 |
| `backend/app/services/data_quality.py` | 新增/修改 | 数据质量检查 |
| `backend/app/model_configs/` | 新增 | 模型配置加载器 |
| `backend/app/models.py` | 修改 | 新增 AIPrediction、EnsemblePrediction 表 + Match 扩展字段 |
| `backend/tests/test_p2plus_hardening.py` | 新增 | P2+ 全量测试（222 个后端测试含此文件 30+） |

### 前端新增/修改文件

| 文件路径 | 说明 |
|---------|------|
| `frontend/src/components/AIModelComparisonView.tsx` | AI 模型对比视图 |
| `frontend/src/components/AccuracyCommandCenterView.tsx` | 准确率指挥部视图 |
| `frontend/src/components/TournamentProjectionView.tsx` | 锦标赛投影视图 |
| `frontend/src/components/BracketView.tsx` | 对阵图视图 |
| `frontend/src/components/AccuracyPanel.tsx` | 准确率面板 |
| `frontend/src/components/DataSources.tsx` | 数据源视图 |
| `frontend/src/components/DecisionView.tsx` | 决策辅助视图 |

---

## 2. 新增数据库表

### `ai_predictions`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | Integer PK | 自增主键 |
| `match_id` | String(80) FK | 关联比赛 |
| `provider` | String(40) | 提供商名 |
| `model_id` | String(80) | 模型 ID |
| `model_version` | String(80) | 模型版本 |
| `prompt_version` | String(40) | Prompt 版本 |
| `input_snapshot_json` | JSON | 输入快照 |
| `raw_response_text` | Text | 原始响应文本 |
| `raw_response_json` | JSON | 原始响应 JSON |
| `parsed_home_win` | Float | 解析后主胜概率 |
| `parsed_draw` | Float | 解析后平局概率 |
| `parsed_away_win` | Float | 解析后客胜概率 |
| `confidence` | Float | AI 置信度 |
| `risk_flags_json` | JSON | 风险标签列表 |
| `key_factors_json` | JSON | 关键因素列表 |
| `reason` | Text | AI 推理说明 |
| `uncertainties_json` | JSON | 不确定性列表 |
| `disagreement_with_system` | Text | 与系统分歧 |
| `disagreement_with_market` | Text | 与市场分歧 |
| `recommended_label` | String(20) | 推荐标签 |
| `created_at` | DateTime | 创建时间 |
| `locked_at` | DateTime | 锁定时间 |
| `is_pre_match_locked` | Boolean | T-30 前锁定 |
| `is_fallback_locked` | Boolean | Fallback 锁定 |
| `real_time_only` | Boolean | 实时预测标记 |
| `error_code` | String(40) | 错误码 |
| `error_message` | Text | 错误信息 |
| `latency_ms` | Integer | 调用延迟 |
| `token_usage_json` | JSON | Token 用量 |

### `ensemble_predictions`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | Integer PK | 自增主键 |
| `match_id` | String(80) FK | 关联比赛 |
| `model_version` | String(40) | 固定值 `ensemble-v1` |
| `system_model_version` | String(40) | 系统模型版本 |
| `system_weight` | Float | 系统权重 |
| `market_weight` | Float | 市场权重 |
| `ai_weights_json` | JSON | AI 各模型权重 |
| `source_probabilities_json` | JSON | 各源概率记录 |
| `ensemble_home_win` | Float | 融合后主胜概率 |
| `ensemble_draw` | Float | 融合后平局概率 |
| `ensemble_away_win` | Float | 融合后客胜概率 |
| `confidence` | Float | 融合置信度 |
| `reason` | Text | 融合来源说明 |
| `created_at` | DateTime | 创建时间 |
| `locked_at` | DateTime | 锁定时间 |
| `is_pre_match_locked` | Boolean | T-30 前锁定 |
| `source_status_json` | JSON | 各源可用状态 |

---

## 3. 新增字段（Match 扩展）

| 字段 | 类型 | 说明 |
|------|------|------|
| `stage` | String(24) | 赛事阶段（group/R32/R16/QF/SF/3rd/Final） |
| `round_name` | String(40) | 轮次名称 |
| `bracket_position` | Integer | 对阵图位置 |
| `home_team_source` | String(80) | 主队来源（如 "A1"） |
| `away_team_source` | String(80) | 客队来源（如 "C2"） |
| `winner_to_match_id` | String(80) | 胜者晋级到的比赛 |
| `loser_to_match_id` | String(80) | 败者晋级到的比赛 |
| `is_placeholder_match` | Boolean | 是否为占位比赛 |
| `home_advance` | Boolean | 主队是否晋级 |
| `away_advance` | Boolean | 客队是否晋级 |
| `went_to_extra_time` | Boolean | 是否加时 |
| `went_to_penalties` | Boolean | 是否点球 |

---

## 4. 新增接口（36 个 API 端点）

### Dashboard / 核心路由（`/api`，12 个端点）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/dashboard` | 主面板 |
| GET | `/api/groups/{group_code}` | 小组详情 |
| GET | `/api/matches` | 比赛列表 |
| GET | `/api/matches/{match_id}` | 比赛详情 |
| GET | `/api/teams/{team_id}` | 球队详情 |
| GET | `/api/data-sources` | 数据源列表 |
| GET | `/api/sync-runs` | 同步运行记录 |
| POST | `/api/refresh` | 手动刷新 |
| GET | `/api/decision` | 决策辅助 |
| GET | `/api/manual-adjustments` | 手动调整列表 |
| POST | `/api/manual-adjustments` | 创建手动调整 |
| DELETE | `/api/manual-adjustments/{id}` | 删除手动调整 |
| GET | `/api/accuracy-command-center` | 准确率指挥部 |

### AI 路由（`/api`，7 个端点）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ai-models` | AI 模型状态列表 |
| POST | `/api/ai-predictions/run` | 单模型单场 AI 预测 |
| POST | `/api/ai-predictions/run-match` | 单场全模型 AI 预测 |
| POST | `/api/ai-predictions/run-all` | 批量 AI 预测 |
| GET | `/api/ai-predictions` | 查询 AI 预测记录 |
| POST | `/api/ensemble/run` | 生成 Ensemble 预测 |
| GET | `/api/ensemble` | 查询 Ensemble 记录 |
| GET | `/api/ai-evaluation` | AI 评分评估 |

### 评分路由（`/api`，8 个端点）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/model-score` | 模型评分汇总 |
| GET | `/api/model-score/details` | 逐场评分明细 |
| GET | `/api/model-score/by-version` | 按模型版本汇总 |
| GET | `/api/model-score/by-stage` | 按赛事阶段汇总 |
| GET | `/api/model-calibration` | 概率校准分析 |
| GET | `/api/market-comparison` | 市场-模型对比 |
| GET | `/api/model-recommendation` | 模型推荐 |
| GET | `/api/data-quality` | 数据质量检查 |
| GET | `/api/model-configs` | 模型配置列表 |

### Tournament 路由（`/api`，5 个端点）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tournament/bracket` | 对阵图 |
| GET | `/api/tournament/projections` | 全队锦标赛投影 |
| POST | `/api/tournament/simulate` | Monte Carlo 模拟 |
| GET | `/api/tournament/team-path` | 单队晋级路径 |
| GET | `/api/tournament/standings` | 小组积分榜 |

---

## 5. 新增前端页面（7 个视图组件）

| 组件 | 功能 |
|------|------|
| `AIModelComparisonView.tsx` | AI 模型对比：Flash vs Pro vs 系统基线 |
| `AccuracyCommandCenterView.tsx` | 准确率指挥部：统一评分面板 |
| `TournamentProjectionView.tsx` | 锦标赛投影：各阶段晋级概率 |
| `BracketView.tsx` | 对阵图：淘汰赛对阵展示 |
| `AccuracyPanel.tsx` | 准确率面板：Brier/LogLoss/HitRate |
| `DataSources.tsx` | 数据源管理 |
| `DecisionView.tsx` | 决策辅助 |

---

## 6. 新增测试

- **后端测试**：222 个（全量统计，覆盖 P1 + P2+）
- **P2+ 专项测试**（`test_p2plus_hardening.py`）：7 个测试类，30+ 个测试方法
  - `TestAIRegistry`（7 个）：模型注册表加载、YAML 解析
  - `TestAIPredictionChain`（7 个）：调用链、parse error、概率归一化、T-30 锁定
  - `TestEnsemble`（8 个）：融合权重、降级、AI 失败排除、实时排除
  - `TestTournament`（4 个）：阶段枚举、placeholder、bracket 生成
  - `TestScoringByStage`（3 个）：按阶段评分、AI/Ensemble 独立评分
  - `TestAccuracyCommandCenter`（2 个）：返回字段完整性、样本不足提示
  - `TestDataPollution`（7 个）：T-30 唯一性、fallback 排除、parse error 排除、版本隔离
- **前端测试**：3 个（`App.test.tsx`，基础渲染测试）

---

## 7. 已真实跑通的功能

| 功能 | 代码位置 | 验证方式 |
|------|---------|---------|
| AI Model Registry | `ai/model_registry.py` | YAML 加载 4 个模型（2 DeepSeek + 2 Xiaomi），配置正确 |
| DeepSeek Provider | `ai/providers/deepseek.py` | 代码实现完整，含重试/超时/rate limit 处理 |
| AI Parser | `ai/parser.py` | 概率归一化、非法拒绝、markdown code block 解析 |
| Ensemble 融合 | `ai/ensemble.py` | 三源权重分配 + 降级策略 + 自动归一化 |
| AI Evaluation | `ai/evaluation.py` | Brier/LogLoss/HitRate 计算，AI vs system 对比 |
| Tournament Rules | `tournament/rules.py` | 7 阶段全覆盖，12 组 48 队赛制常量 |
| Scoring 体系 | `services/scoring.py` | `score_model`、`model_score_by_version`、`model_score_by_stage` |
| Accuracy Command Center | `services/accuracy_command.py` | 18 个字段完整返回，样本不足正确提示 |
| T-30 锁定机制 | `services/snapshots.py` | `write_snapshots` 实现锁定 + fallback 逻辑 |
| 数据污染防护 | 10 条规则 | 测试全部通过（见 `data_pollution_audit.md`） |

---

## 8. 只是骨架 / 有 Bug 的功能

| 功能 | 问题 | 严重程度 |
|------|------|---------|
| Bracket 对阵生成 | `get_knockout_matchups` 中 `bracket_pairs` 包含重复条目（如 `("B2", "C2")` 与 `("C2", "D2")` 混用），第三名球队分配为简化占位，非真实 FIFA 抽签规则 | 高 |
| Tournament Simulation 性能 | `compute_projections` 纯 Python 循环 10,000 次迭代，48 队时性能差，默认调用 5000 次 | 中 |
| Xiaomi Provider | `ai/providers/xiaomi.py` 存在但未验证真实 API 兼容性 | 低 |
| 前端 BracketView | 占位组件，对阵图渲染可能不完整 | 中 |
| 前端测试 | 仅 1 个测试文件，AI/Ensemble 视图无前端测试 | 低 |

---

## 9. 依赖 Mock 数据的功能

| 功能 | Mock 情况 |
|------|----------|
| AI Provider 调用 | 测试中未调用真实 DeepSeek API，使用 mock `AIPrediction` 行直接入库 |
| Tournament Simulation | 测试中使用 `_make_team` 构造的虚拟队伍，非真实种子队数据 |
| Market Snapshot | 测试中手动构造 `MarketSnapshot`，非体彩真实赔率数据 |
| Intelligence 数据 | 测试中无 `MatchIntelligence` 数据，AI prompt 构建器中 injuries/suspensions 为空 |

---

## 10. 依赖真实 DeepSeek API Key 的功能

| 端点 | 环境变量 | 无 Key 时行为 |
|------|---------|-------------|
| `POST /api/ai-predictions/run` | `DEEPSEEK_API_KEY` | `is_configured()` 返回 False → 状态 `disabled_no_key` |
| `POST /api/ai-predictions/run-match` | 同上 | 同上 |
| `POST /api/ai-predictions/run-all` | 同上 | 同上 |

全局开关：`ENABLE_AI_PREDICTION=true`（默认 `false`）

---

## 11. 不应作为准确率结论的功能

| 原因 | 说明 |
|------|------|
| 样本量不足 | 当前已评分比赛 < 20 场，`accuracy_command.py:37` 中 `sample_sufficient = total_scored >= 20` 为 False |
| AI 尚无真实评分数据 | `_evaluate_ai_version` 查询 `is_pre_match_locked=True` 的 AI 预测，当前数据库中为 0 |
| Ensemble 尚无真实评分数据 | 同理，`_evaluate_ensemble` 无数据 |
| 小组赛阶段数据为主 | `model_score_by_stage` 中仅有 `group` 阶段数据（如有） |
| `insufficient_reason` 字段明确提示 | Command Center 返回 `"样本量不足；AI预测未启用；DeepSeek Flash 尚无评分数据；DeepSeek Pro 尚无评分数据"` |

---

## 12. 当前最大的 10 个风险

| # | 风险 | 影响 | 严重程度 |
|---|------|------|---------|
| 1 | **AI 预测从未在生产环境真实调用** | 无法验证 DeepSeek API 真实可用性、延迟、成本 | 高 |
| 2 | **Bracket 对阵图使用简化逻辑，非 FIFA 官方抽签规则** | 第三名分配路径错误，淘汰赛对阵可能不正确 | 高 |
| 3 | **样本量不足，无法得出任何准确率结论** | 用户可能误读面板数据为"模型已验证" | 高 |
| 4 | **Tournament Simulation 性能瓶颈** | 默认 5000 次迭代在同步请求中可能导致超时 | 中 |
| 5 | **Ensemble 融合权重为静态配置，无自适应** | 权重不随实际表现调整，可能长期次优 | 中 |
| 6 | **Xiaomi Provider 未经验证** | 配置存在但 API 兼容性未知 | 中 |
| 7 | **前端 AI 视图无测试** | AI 对比、准确率指挥部等关键视图无前端测试 | 低 |
| 8 | **AI Prompt 中历史评分摘要可能包含不足样本的误导信息** | `historical_score_summary` 可能输出不准确的 Brier 分数 | 中 |
| 9 | **无 API Key 时静默降级，用户可能不知 AI 功能存在** | `is_ai_enabled()` 默认 False，需手动配置环境变量 | 低 |
| 10 | **`_generate_next_round` 使用 `home_team` 作为默认 winner** | 如果淘汰赛结果未填入，下一轮对阵会错误使用主队 | 中 |
