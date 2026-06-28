# API 文档

基础路径：`/api`

交互式文档：http://127.0.0.1:8000/docs

## 认证

写接口（POST / DELETE / PATCH）需要 `X-API-Key` 请求头认证。

- 若 `ADMIN_API_KEY` 环境变量为空，则认证禁用（向后兼容）
- 若已配置，则所有写请求必须携带 `X-API-Key: <your-key>` 头
- 认证失败返回 `401 Unauthorized`

```bash
# 示例：带认证的 POST 请求
curl -X POST http://127.0.0.1:8000/api/refresh \
  -H "X-API-Key: your-admin-key"
```

## 限流

| 方法 | 限制 | 窗口 |
|------|------|------|
| GET | 120 次/分钟 | 按 IP 滑动窗口 |
| POST/DELETE/PATCH | 60 次/分钟 | 按 IP 滑动窗口 |

超限返回 `429 Too Many Requests`，响应头包含 `Retry-After`。

---

## 仪表盘

### GET /api/health

健康检查，返回系统状态和依赖可用性。

**响应示例：**

```json
{
  "status": "ok",
  "revision_id": 1,
  "dependencies": {
    "database": "ok",
    "ai_providers": "available",
    "apscheduler": "running",
    "scheduled_refresh": "disabled",
    "auto_ai_workflow": "disabled",
    "snapshot_lock": "enabled",
    "maintenance": "enabled",
    "last_successful_run": "2026-06-19T08:00:00+00:00"
  }
}
```

- `status`: `"ok"` 或 `"degraded"`
- `database`: `"ok"` 或 `"no_revision"`
- `ai_providers`: `"available"` 或 `"no_api_keys"`
- `scheduled_refresh`: `"enabled"` 或 `"disabled"`
- `auto_ai_workflow`: `"enabled"` 或 `"disabled"`；表示后端是否已注册 `world-cup-auto-ai` 调度任务
- `snapshot_lock`: `"enabled"` 或 `"disabled"`
- `maintenance`: `"enabled"` 或 `"disabled"`

### GET /api/dashboard

获取完整仪表盘数据，包含所有小组、比赛、预测。

**响应包含：**
- `revision`：当前活跃版本信息
- `groups`：12 个小组的积分榜和比赛
- `summary`：全局统计

### GET /api/groups/{group_code}

获取指定小组详情。`group_code` 为 A-L 中的一个字母。

### GET /api/matches

获取所有比赛列表。可选查询参数 `status` 过滤比赛状态（`scheduled` / `live` / `final`）。

### GET /api/matches/{match_id}

获取比赛详情，包含预测、快照、AI 预测、集成预测、球队画像展示等完整信息。

**响应示例（部分）：**

```json
{
  "match": {
    "id": "group-a-1",
    "home_team": "Mexico",
    "away_team": "Brazil",
    "kickoff": "2026-06-11T22:00:00+00:00",
    "status": "scheduled",
    "stage": "group"
  },
  "prediction": {
    "home_win": 0.22,
    "draw": 0.26,
    "away_win": 0.52,
    "home_xg": 0.85,
    "away_xg": 1.62,
    "confidence_label": "medium"
  },
  "snapshot": { ... },
  "ai_predictions": [ ... ],
  "ensemble": { ... }
}
```

### GET /api/teams/{team_id}

获取球队详情，包含评级、画像、小组信息。

### GET /api/decision

获取决策视图数据，包含所有比赛的推荐方向和锁定状态。

### GET /api/data-sources

获取数据源和情报提供商状态。

### GET /api/sync-runs

获取数据同步运行历史。

### POST /api/refresh

手动触发数据刷新和重算。需要认证。

**响应示例：**

```json
{
  "status": "success",
  "finalized_matches": 2,
  "updated_matches": 3,
  "warnings": [],
  "revision_id": 5
}
```

说明：

- 这是底层刷新接口，主要负责外部数据同步和重算
- 调用时会先同步官方淘汰赛占位/晋级状态，再在同一个 active revision 中重算当前可预测比赛
- 首页动作区通常走 `/api/workflows/*` 接口，而不是直接调用这个端点
- `ENABLE_SCHEDULED_REFRESH=false` 时，不会后台自动周期性调用该接口

---

## AI 预测

### GET /api/ai-models

列出所有已配置的 AI 模型及其状态。

当前用户侧默认只展示可见的 DeepSeek 系列模型；停用、欠费或已下线后不再对用户侧暴露的模型不会出现在工作台展示说明中。

**响应示例：**

```json
{
  "enabled": true,
  "models": [
    {
      "model_version": "ai-deepseek-v4-flash-v1",
      "display_name": "DeepSeek V4 Flash",
      "provider": "deepseek",
      "enabled": true,
      "has_api_key": true,
      "cost_tier": "low",
      "latency_tier": "fast",
      "prompt_version": "worldcup-ai-v1",
      "status": "ready"
    }
  ]
}
```

### POST /api/ai-predictions/run

运行 AI 预测。需要认证。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `match_id` | string | 是 | 比赛 ID |
| `model_version` | string | 否 | 指定模型版本，省略则运行所有启用模型 |
| `force` | boolean | 否 | 强制重新运行（默认 false） |

**响应示例：**

```json
{
  "results": [
    {
      "status": "success",
      "model_version": "ai-deepseek-v4-flash-v1",
      "parsed_home_win": 0.25,
      "parsed_draw": 0.28,
      "parsed_away_win": 0.47,
      "confidence": 0.7,
      "recommended_label": "away_win",
      "latency_ms": 3200
    }
  ]
}
```

### POST /api/ai-predictions/run-match

对单场比赛运行所有启用的 AI 模型。需要认证。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `match_id` | string | 是 | 比赛 ID |
| `force` | boolean | 否 | 强制重新运行 |

### POST /api/ai-predictions/run-all

批量运行 AI 预测。需要认证。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `stage` | string | 否 | 过滤比赛阶段 |
| `limit` | int | 否 | 最大比赛数（1-20，默认 10） |
| `only_missing` | boolean | 否 | 仅预测缺失的比赛（默认 true） |
| `retry_failed` | boolean | 否 | 重试失败的预测（默认 false） |

### GET /api/ai-predictions

获取指定比赛的所有 AI 预测结果。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `match_id` | string | 是 | 比赛 ID |

### POST /api/ensemble/run

生成集成预测。需要认证。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `match_id` | string | 是 | 比赛 ID |

### GET /api/ensemble

获取指定比赛的集成预测历史。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `match_id` | string | 是 | 比赛 ID |

### GET /api/ai-evaluation

评估 AI 和集成预测与实际结果的对比。

### GET /api/ai-independence

审计 AI 预测与基线预测的偏差程度。

### GET /api/ai-prompt-preview

预览 AI 预测提示词（调试用，不调用 AI 提供商）。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `match_id` | string | 是 | 比赛 ID |
| `prompt_version` | string | 否 | 提示词版本（默认 `worldcup-ai-v1`） |

---

## 评分

### GET /api/model-score

获取当前模型评分汇总。

**响应示例：**

```json
{
  "matches_scored": 15,
  "brier_score": 0.234,
  "log_loss": 0.678,
  "outcome_hit_rate": 0.533,
  "top_score_hit_rate": 0.133,
  "xg_mae": 1.12,
  "scoring_snapshot_rule": "latest_pre_match_snapshot_before_kickoff"
}
```

### GET /api/model-score/details

获取每场比赛的评分详情和排除原因。

### GET /api/model-score/by-version

按模型版本汇总评分。

### GET /api/model-score/by-stage

按赛事阶段（小组赛/淘汰赛）汇总评分。

### GET /api/scoring-exclusions

列出未参与评分的已完成比赛及排除原因。

### GET /api/match-count-breakdown

比赛数量分类统计（总完成/有预测/有快照/有锁定/实际评分）。

### GET /api/error-attribution-summary

误差归因汇总统计。

### GET /api/model-calibration

概率校准分析，按概率区间统计实际命中率。

### GET /api/market-comparison

模型 vs 市场赔率 vs 融合预测对比。

### GET /api/model-recommendation

推荐下一个应使用的模型版本。

### GET /api/data-quality

数据质量检查结果。

### GET /api/model-comparison

结构化模型对比：基线 vs AI v1 vs AI v2 vs 集成。

### GET /api/model-configs

列出可用的模型配置。

### GET /api/decision-snapshot-status

所有近期比赛的决策快照状态。

### GET /api/accuracy-command-center

统一准确率指挥中心——整体模型评估。

---

## 工作流

### GET /api/workflows/status

获取当前工作流状态、按钮可用性、下一步建议和最近一次运行进度。

关键字段包括：

- `today_status`
- `next_action`
- `button_states.daily_open / ai_prediction / post_match / full / lock`
- `ai_status`
- `last_run.progress`

补充说明：

- `button_states.ai_prediction` 会反映 60 分钟冷却状态
- `button_states.daily_open` 和 `button_states.post_match` 不受该冷却限制
- `last_run.progress.percent` 为前端首页顶部状态条和动作按钮共用的百分比进度
- `AI_RUN_MODE=auto` 时，后端调度器会定时尝试触发 `pre-match` workflow
- `AUTO_RUN_DAILY_WORKFLOW_ON_OPEN` / `AUTO_RUN_AI_ON_OPEN` 仍不是当前默认前端入口；页面刷新不会自动触发 workflow

### POST /api/workflows/daily-open

手动触发每日更新工作流。当前前端刷新页面不会自动调用该接口。

**请求体（可选）：**

```json
{
  "hours": 48,
  "since_hours": 24,
  "limit": 10,
  "with_ai": false,
  "with_ensemble": true,
  "auto_lock": true,
  "only_missing": true
}
```

说明：

- `with_ai` 默认 `false`，即“更新今日数据”不会顺带跑 AI
- 如需跑 AI，请使用 `/api/workflows/pre-match` 或 `/api/workflows/full`

### POST /api/workflows/pre-match

手动触发赛前预测工作流。首页“运行 AI 预测”按钮会调用此接口并传 `with_ai=true`。需要认证。

**请求体（可选）：**

```json
{
  "hours": 48,
  "limit": 10,
  "with_ai": true,
  "with_ensemble": true,
  "only_missing": true
}
```

说明：

- 首页按钮是否可点、是否处于 60 分钟冷却期，以 `/api/workflows/status.button_states.ai_prediction` 为准
- 工作流启动后，前端会轮询 `/api/workflows/status` 和 `/api/workflows/runs` 展示百分比进度

### POST /api/workflows/post-match

触发赛后复盘工作流。需要认证。

**请求体（可选）：**

```json
{
  "since_hours": 24
}
```

说明：

- 首页“同步赛果”按钮会调用此接口
- 该按钮只受“当前已有工作流正在运行”和“是否存在可复盘已完赛比赛”影响，不走 60 分钟 AI 冷却

### POST /api/workflows/lock

触发赛前决策快照锁定工作流。需要认证。

**请求体（可选）：**

```json
{
  "window_hours": 24
}
```

### POST /api/workflows/full

触发完整工作流。需要认证。

**请求体（可选）：**

```json
{
  "hours": 48,
  "since_hours": 24,
  "limit": 10,
  "with_ai": true,
  "with_ensemble": true,
  "auto_lock": true,
  "only_missing": true
}
```

### GET /api/workflows/runs

获取最近的工作流运行历史。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `limit` | int | 否 | 返回条数（默认 20） |

### GET /api/workflows/runs/{run_id}

获取指定工作流运行的详情，包含每个步骤的状态和耗时。

---

## 球队画像

### GET /api/team-profiles

获取所有球队的画像概览。球队画像会进入当前预测链路的 `MatchContext`，同时保留独立评估端点用于复盘画像效果。

### GET /api/team-profiles/{team_id}

获取指定球队的画像详情。返回 `profile` 和 `summary`。

`profile` 包含七个结构化模块：

- `profile_modules_json.long_term_strength`
- `profile_modules_json.recent_form`
- `profile_modules_json.attack_defense`
- `profile_modules_json.tactical_style`
- `profile_modules_json.lineup_players`
- `profile_modules_json.environment`
- `profile_modules_json.data_quality`

关键结构化字段：

- `long_term_strength_score`
- `recent_form_score`
- `attack_score`
- `defense_score`
- `stability_score`
- `tournament_experience_score`
- `lineup_integrity_score`
- `injury_risk_score`
- `rest_days`
- `schedule_fatigue_score`
- `environment_adaptation_score`
- `data_quality_score`
- `strengths`
- `weaknesses`
- `risk_flags`
- `missing_fields`
- `source_list`
- `usage_scope`
- `prediction_enabled`

`source_list` 为可追溯来源标签，例如 `historical_real:martj42/international_results:<sha12>`、`elo:world_football_elo+fifa_official_ranking:<date>:<effective_date>`、`fifa_ranking:world_football_elo+fifa_official_ranking:<date>:<effective_date>`、`statsbomb_xg:open_data_world_cup:2018_2022`、`fifa_squad:fifa_official_squad_list:2026-06-20`；完整历史数据 `raw_url` / `raw_sha256` 在 `source_summary_json` 中，评分来源在 `team_profile_data_quality.rating_source` 中。包含 mock fallback 时，`team_profile_data_quality.contains_mock=true`，核心画像评分在 API payload 中返回 `null`，不会伪装成真实评分。

环境模块当前会返回基于真实赛程和场地 registry 的 `rest_days`、`schedule_fatigue_score`、`environment_adaptation_score`、`profile_modules_json.environment.travel_distance_km`、`profile_modules_json.environment.timezone_shift_hours`、`profile_modules_json.environment.next_match`、`profile_modules_json.environment.previous_venue`、`profile_modules_json.environment.next_venue`、`profile_modules_json.environment.upcoming_venues`；天气、气候和场地熟悉度仍为 unavailable。

如果已生成 `data/seed/world-cup-2026-venue-climate.json`，`profile_modules_json.environment.climate_adaptation` 会返回 Open-Meteo 历史气候基线，且 `is_match_forecast=false`。未生成快照或未覆盖 venue 时，该字段保持 `unavailable`。

攻防模块的 `profile_modules_json.attack_defense.xg` 仅在本地 StatsBomb World Cup xG 文件覆盖该球队时返回结构化对象，包含 `source`、`competition`、`seasons`、`sample_count`、`xg_for_avg`、`xg_against_avg`。当前来源只覆盖 2018/2022 世界杯样本；未覆盖球队返回 `unavailable`，并保留 `xg` 于 `missing_fields`。

阵容模块的 `profile_modules_json.lineup_players` 已接入 FIFA 官方 2026-06-20 Squad List，可返回 `squad_size`、`position_counts`、`average_caps`、`total_caps`、`total_goals`、`top_scorers_in_squad`、`most_capped_players` 和 `bench_depth`。该来源不是伤停、停赛或首发确认 feed，因此 `injury_risk_score`、`confirmed_lineup_level` 等字段仍为 `unavailable`。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `as_of` | datetime | 否 | 历史时间切片 |

### GET /api/team-profiles/evaluation

评估球队画像因子对赛后评分的帮助/损害情况。该端点仍用于单独审视画像效果，即使主预测链路已加载画像输入。

### POST /api/team-profiles/rebuild

重建所有球队画像。需要认证。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `use_seed` | boolean | 否 | 使用种子数据（默认 true） |

### GET /api/team-profile-predictions/{match_id}

历史保留端点。当前不会生成新的画像预测，正常返回 `prediction: null`。

---

## 赛事

### GET /api/tournament/bracket

获取当前淘汰赛对阵表。

当前实现说明：

- 对阵表以本地 `data/seed/world-cup-2026-knockout.json` 中的官方 Match 73-104 赛程为准
- 32 强席位会结合当前积分榜和最佳第三名官方组合表动态填充
- 已结束淘汰赛会按比分或 `home_advance` / `away_advance` 自动推进到下一轮
- 小组赛未全部结束前，未明确的席位会保留 `home_source` / `away_source` 并显示为待定

**响应字段（单场比赛）常见包括：**

- `id`（可继续用于请求 `/api/matches/{id}` 获取共享详情抽屉数据）
- `match_number`
- `stage`
- `round_name`
- `home_source` / `away_source`
- `home_team` / `away_team`
- `home_score` / `away_score`
- `home_advance` / `away_advance`
- `went_to_extra_time` / `went_to_penalties`
- `winner_to_match_id` / `loser_to_match_id`
- `is_placeholder_match`

前端“冠军与赛程”页会先拉取该接口，再在用户点击具体对阵卡时，使用其中的 `id` 调用 `/api/matches/{id}` 展示与比赛中心一致的详情抽屉。

### GET /api/tournament/projections

获取所有球队的赛事晋级概率（5 分钟缓存）。

该端点与 `/api/tournament/bracket` 使用同一套官方淘汰赛赛程和最佳第三名组合表。

**响应示例：**

```json
{
  "projections": [
    {
      "team_id": "brazil",
      "group_qualify": 0.92,
      "round_of_32": 0.88,
      "round_of_16": 0.72,
      "quarter_final": 0.48,
      "semi_final": 0.28,
      "final": 0.15,
      "champion": 0.08
    }
  ]
}
```

### POST /api/tournament/simulate

运行完整赛事 Monte Carlo 模拟。需要认证。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `iterations` | int | 否 | 迭代次数（默认 10000，上限 100000） |
| `seed` | int | 否 | 随机种子（默认 20260613） |

### GET /api/tournament/team-path

获取指定球队的潜在赛事路径。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `team_id` | string | 是 | 球队 ID |

### GET /api/tournament/standings

获取所有小组的当前积分榜。

---

## 数据管理

### GET /api/manual-adjustments

获取手动调整列表。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `match_id` | string | 否 | 过滤指定比赛 |

### POST /api/manual-adjustments

创建手动调整并触发重算。需要认证。

**请求体：**

```json
{
  "match_id": "group-a-1",
  "adjustment_type": "attack_boost",
  "affected_team_id": "mexico",
  "attack_delta": 0.15,
  "defense_delta": 0.0,
  "confidence": "high",
  "note": "主场优势调整",
  "created_by": "admin"
}
```

### DELETE /api/manual-adjustments/{adjustment_id}

删除手动调整并触发重算。需要认证。
