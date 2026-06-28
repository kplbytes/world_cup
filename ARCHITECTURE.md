# 系统架构

## 总体架构

系统采用前后端分离的单体架构，本地优先设计，无需云服务即可完整运行。

```
┌──────────────────────────────────────────────────────────────┐
│                      浏览器 (Browser)                         │
│  React SPA ←── JSON API ──→ FastAPI Backend                  │
└──────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
        ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
        │  SQLite    │  │  AI API   │  │  数据源    │
        │  (WAL)     │  │  (HTTP)   │  │  (HTTP)   │
        └───────────┘  └───────────┘  └───────────┘
```

## 后端架构

### 技术选型

| 组件 | 技术 | 说明 |
|------|------|------|
| Web 框架 | FastAPI | 异步 ASGI，自动 OpenAPI 文档 |
| ORM | SQLAlchemy 2.0 | 声明式映射，类型安全 |
| 数据库 | SQLite (WAL) | 单文件，零配置，WAL 模式支持并发读 |
| 定时任务 | APScheduler | 后台调度；默认开启快照锁定与维护，赛果刷新可选 |
| HTTP 客户端 | httpx | 异步支持，用于 AI API 和数据源 |
| 数学计算 | NumPy + SciPy | Elo 评级、Poisson 模型、Monte Carlo |
| 配置管理 | pydantic-settings | 类型安全的环境变量加载 |

### 应用生命周期

```
启动 → 初始化数据库 → 种子数据 → 首次重算 → 修复锁定/卡住工作流 → 启动调度器 → 就绪
                                                                             │
                                              ┌──────────────────────────────┼─────────────────────┐
                                              │                              │                     │
                                        可选定时刷新                    定时锁定               定时维护
                                     (默认关闭，15min/2min)             (1min)                 (6h)
```

1. **`create_app()`**：创建 FastAPI 实例，注册中间件和路由
2. **`initialize_database()`**：建表、种子数据、首次重算、修复锁定、清理异常中断后遗留的 running workflow
3. **调度器**：默认两个后台任务——快照锁定和维护；定时刷新赛果/赛程只有在 `ENABLE_SCHEDULED_REFRESH=true` 时才启用
4. **关闭**：停止调度器，关闭 AI 提供商客户端

### 中间件栈（外→内）

```
RateLimitMiddleware    → IP 级滑动窗口限流（GET: 120/min, 其他: 60/min）
ApiKeyMiddleware       → 写接口认证（X-API-Key header）
RequestIdMiddleware    → 请求 ID 注入（日志追踪）
AccessLogMiddleware    → 访问日志记录
CORSMiddleware         → 跨域配置（CORS_ALLOWED_ORIGINS）
```

### 目录结构

```
backend/app/
├── main.py              # 应用入口、中间件、生命周期
├── config.py            # pydantic-settings 配置
├── db.py                # 数据库引擎、会话管理、Schema 迁移
├── models.py            # SQLAlchemy ORM 模型（20+ 表）
├── schemas.py           # Pydantic 请求/响应模型
├── middleware.py         # RequestId + AccessLog 中间件
├── logging_config.py    # 结构化 JSON 日志
│
├── api/routes/          # API 路由层
│   ├── dashboard_routes.py   # 仪表盘、比赛、健康检查
│   ├── scoring_routes.py     # 评分、校准、误差归因
│   ├── ai_routes.py          # AI 预测、集成、独立审计
│   ├── workflow_routes.py    # 工作流管理
│   ├── tournament_routes.py  # 赛事模拟、对阵、积分
│   ├── data_routes.py        # 手动调整、准确率中心
│   └── team_profile_routes.py # 球队画像
│
├── services/            # 业务逻辑层
│   ├── dashboard.py          # 仪表盘数据组装
│   ├── refresh.py            # 数据刷新 & 结果同步
│   ├── recompute.py          # 全量重算
│   ├── scoring.py            # 评分引擎
│   ├── snapshots.py          # 快照锁定
│   ├── market.py             # 市场赔率获取
│   ├── market_comparison.py  # 模型 vs 市场对比
│   ├── calibration.py        # 概率校准
│   ├── accuracy_command.py   # 准确率指挥中心
│   ├── adaptive_weights.py   # 自适应集成权重
│   ├── ai_independence.py    # AI 独立性审计
│   ├── model_recommendation.py # 模型推荐
│   ├── data_quality.py       # 数据质量检查
│   ├── error_attribution.py  # 误差归因
│   ├── manual_adjustments.py # 手动调整
│   ├── seed.py               # 种子数据加载
│   ├── team_matching.py      # 队名/别名匹配
│   └── localization.py       # 本地化（球队名翻译）
│
├── ai/                  # AI 预测子系统
│   ├── ai_models.yaml        # 模型配置（声明式）
│   ├── model_registry.py     # 模型注册表
│   ├── provider_registry.py  # 提供商注册表
│   ├── service.py            # AI 预测服务
│   ├── prompt_builder.py     # 提示词构建
│   ├── parser.py             # AI 响应解析
│   ├── ensemble.py           # 集成融合
│   ├── evaluation.py         # AI 评估
│   ├── lock_status.py        # 锁定状态计算
│   ├── schemas.py            # AI 数据模型
│   └── providers/            # AI 提供商实现
│       ├── base.py               # 抽象基类
│       ├── openai_compat.py      # OpenAI 兼容客户端
│       ├── deepseek.py           # DeepSeek 提供商
│       └── xiaomi.py             # 备用兼容提供商实现（当前未在 ai_models.yaml 启用）
│
├── prediction/          # 基线预测引擎
│   ├── elo.py                # Elo 评级计算
│   ├── poisson.py            # Poisson 进球模型
│   ├── confidence.py         # 置信度评估
│   ├── explanation.py        # 预测解释生成
│   └── shadow.py             # 影子模型（对比用）
│
├── team_profiles/       # 球队画像子系统
│   ├── service.py            # 画像服务
│   ├── data_loader.py        # 数据加载
│   ├── feature_engineering.py # 特征工程
│   ├── scorer.py             # 画像调整计算
│   └── evaluation.py         # 画像独立评估（模型复盘）
│
├── tournament/          # 赛事逻辑
│   ├── standings.py          # 积分榜计算
│   ├── bracket.py            # 对阵表生成
│   ├── simulation.py         # Monte Carlo 模拟
│   ├── qualification.py      # 出线规则
│   └── rules.py              # 排名规则
│
├── workflows/           # 工作流引擎
│   ├── service.py            # 工作流执行
│   ├── scheduler.py          # 自动调度
│   ├── state.py              # 运行状态管理
│   └── schemas.py            # 请求模型
│
├── providers/           # 数据源提供商
│   ├── base.py               # 抽象基类
│   ├── openfootball.py       # OpenFootball
│   ├── football_data.py      # football-data.org
│   ├── sporttery.py          # 体彩赔率
│   └── worldcup26.py         # WorldCup26
│
└── intelligence/        # 情报子系统
    ├── engine.py             # 情报引擎
    ├── pipeline.py           # 情报流水线
    ├── cache.py              # 情报缓存
    ├── quota.py              # 配额管理
    └── providers/            # 情报提供商
```

球队画像数据质量由 `source_list` / `source_summary`、`missing_fields` 和 `quality_penalties` 解释；mock fallback 只保留为低可信展示，不暴露为已验证核心评分。

## 前端架构

### 技术选型

| 组件 | 技术 | 说明 |
|------|------|------|
| UI 框架 | React 19 | 函数组件 + Hooks |
| 构建工具 | Vite 6 | 快速 HMR，生产构建 |
| 类型系统 | TypeScript 5.8 | 严格模式 |
| 数据获取 | TanStack Query v5 | 缓存、重试、运行中轮询 |
| 测试 | Vitest + Testing Library | 单元测试 + 组件测试 |

### 页面结构

```
App.tsx
├── AppHeader               # 顶部品牌、版本、模型标签
├── PageShell               # 页面宽度与布局容器
├── DailyDashboard          # 今日工作台（默认首页）
│   ├── StatusStrip
│   ├── WorkflowProgressBar
│   ├── ActionButton
│   ├── MatchSummaryCard
│   └── MatchDetailDrawer
├── MatchCenter             # 比赛中心
│   ├── GroupNav
│   ├── GroupDashboard
│   ├── MatchSummaryCard
│   ├── MatchDetailDrawer
│   └── BracketView
├── ModelReviewCenter       # 模型复盘中心
├── TournamentCenter        # 冠军与赛程
│   ├── 冠军概率
│   ├── 晋级概率
│   └── 淘汰赛路径
└── TeamDetail              # 球队详情抽屉
```

### 关键设计

- **统一推荐逻辑**：所有比赛卡片和详情使用同一 `getMatchRecommendation()` 函数
  1. 集成预测（如有）→ 显示集成推荐
  2. AI 预测（无集成时）→ 显示 AI 推荐
  3. 基线预测（无 AI 时）→ 显示基线推荐
  4. 无预测 → 显示"待生成"

- **固定四个顶层入口**：今日工作台、比赛中心、模型复盘、冠军与赛程；导航位置在各入口间保持一致
- **实时状态**：3 小时内开赛的比赛显示在"即将开赛"列表
- **工作流进度可见**：今日工作台动作按钮和状态区共用同一组 workflow progress 数据

## 数据流

### 核心预测流水线

```
种子数据 (seed/)
    │
    ▼
数据同步 (refresh) ──── 外部数据源 (OpenFootball, football-data.org, 体彩)
    │
    ▼
基线重算 (recompute) ── Elo 评级 + Poisson 进球模型
    │                      │
    │                      ├── 胜/平/负概率
    │                      ├── xG 期望进球
    │                      ├── 比分矩阵 (score_matrix)
    │                      └── 置信度标签
    │
    ▼
AI 预测 (ai/service) ── 多模型独立预测
    │                      │
    │                      ├── DeepSeek V4 Flash / Pro
    │                      ├── DeepSeek V4 Flash (Independent, v2 prompt)
    │                      └── 去重 + 1 小时重跑冷却 + 抄袭检测
    │
    ▼
集成融合 (ensemble) ─── 加权融合
    │                      │
    │                      ├── 默认 system 权重 (35%)
    │                      ├── 默认 market 权重 (30%)
    │                      └── 默认 AI 权重 (35%, 按模型归一化分配)
    │
    ▼
快照锁定 (snapshots) ── 赛前 24h 锁定
    │                      │
    │                      ├── 锁定快照 (is_pre_match_locked)
    │                      └── 降级快照 (is_fallback_locked)
    │
    ▼
赛后评分 (scoring) ─── Brier + LogLoss + 命中率
                           │
                           ├── 误差归因
                           ├── 校准分析
                           └── 模型推荐
```

### 工作流步骤

| 步骤 | 名称 | 说明 |
|------|------|------|
| 1 | `refresh_results` | 同步比赛结果 |
| 2 | `post_match_recompute` | 赛后重算 |
| 3 | `post_match_score` | 赛后评分 |
| 4 | `pre_match_recompute` | 赛前重算 |
| 5 | `ai_prediction` | AI 预测 |
| 6 | `ensemble_generation` | 集成融合 |
| 7 | `lock_predictions` | 锁定预测 |
| 8 | `accuracy_command_update` | 准确率更新 |
| 9 | `artifact_generation` | 产物生成 |

## AI 提供商架构

### OpenAI 兼容设计

所有 AI 提供商通过统一的 OpenAI 兼容接口访问：

```
┌─────────────────────────────────────┐
│          AI Service Layer            │
│  ┌─────────┐ ┌─────────┐           │
│  │ Prompt  │ │ Parser  │           │
│  │ Builder │ │         │           │
│  └────┬────┘ └────┬────┘           │
│       │           │                 │
│  ┌────▼───────────▼────┐           │
│  │  OpenAI Compat      │           │
│  │  Provider            │           │
│  └────┬───────────┬────┘           │
└───────┼───────────┼────────────────┘
        │           │
   ┌────▼────┐ ┌───▼────────┐
   │DeepSeek │ │ Compat API │
   │  API    │ │ (optional) │
   └─────────┘ └────────────┘
```

### 模型配置（声明式）

模型定义在 `ai_models.yaml` 中，无需代码修改即可添加新模型：

```yaml
providers:
  deepseek:
    enabled: true
    api_key_env: DEEPSEEK_API_KEY
    models:
      - model_id: deepseek-v4-flash
        enabled: true
        model_version: ai-deepseek-v4-flash-v1
        ensemble_weight: 0.33
        prompt_version: worldcup-ai-v1
```

当前默认配置只启用 DeepSeek provider。代码库保留的备用 provider 实现不会自动出现在用户侧页面和文档主说明中。

### 提示词版本

| 版本 | 特点 |
|------|------|
| `worldcup-ai-v1` | 包含基线概率参考，AI 可参考系统预测 |
| `worldcup-ai-v2` | 独立判断，不含基线概率，避免锚定偏差 |

## 数据库 Schema 概览

### 核心表

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| `teams` | 球队信息 | id, name, code, group_code |
| `matches` | 比赛信息 | id, kickoff, status, home/away_score, stage |
| `team_ratings` | 球队评级 | team_id, elo, fifa_rank, effective_date |
| `dashboard_revisions` | 预测版本 | id, model_version, active, simulation_iterations |
| `match_predictions` | 基线预测 | revision_id, match_id, home_win, draw, away_win, home_xg |
| `prediction_snapshots` | 预测快照 | match_id, is_pre_match_locked, is_fallback_locked |
| `ai_predictions` | AI 预测 | match_id, provider, model_version, parsed_* |
| `ensemble_predictions` | 集成预测 | match_id, system/market/ai_weights, ensemble_* |
| `market_snapshots` | 市场赔率 | match_id, provider, home/draw/away_probability |
| `model_scores` | 模型评分 | revision_id, brier_score, log_loss, outcome_hit_rate |
| `team_profiles` | 球队画像展示 | team_id, profile_version, seven-module profile, data_quality, usage_scope |
| `workflow_runs` | 工作流运行 | workflow_type, status, started_at, finished_at |
| `workflow_steps` | 工作流步骤 | workflow_run_id, step_name, status |

### 表关系

```
teams ──1:N──> team_ratings
teams ──1:N──> team_profiles
teams ──1:N──> team_aliases
matches ──1:N──> match_predictions ──N:1──> dashboard_revisions
matches ──1:N──> prediction_snapshots ──N:1──> dashboard_revisions
matches ──1:N──> ai_predictions
matches ──1:N──> ensemble_predictions
matches ──1:N──> market_snapshots
matches ──1:N──> manual_adjustments
dashboard_revisions ──1:1──> model_scores
workflow_runs ──1:N──> workflow_steps
```

### Schema 迁移

数据库使用 `PRAGMA user_version` 进行轻量级版本控制，当前版本为 7。迁移在 `db.py` 的 `_upgrade_schema()` 中执行，支持：

- v1：prediction_snapshots 增加锁定字段
- v2：matches 增加淘汰赛字段 + AI 表
- v3：workflow 系统表
- v4：team_profile 表 + 去重
- v5：prediction_snapshots 主键重构 + 唯一约束
- v6：structured team profile modules
- v7：prediction snapshot access indexes

## 关键设计决策

### 1. 本地优先 (Local-First)

系统设计为完全本地运行，不依赖云服务。SQLite WAL 模式支持并发读取，单文件部署简单。

### 2. 版本化预测 (Revision-Based)

每次重算生成新的 `DashboardRevision`，预测数据通过 `revision_id` 关联。活跃版本标记 `active=True`，历史版本完整保留。

### 3. 赛前锁定 (Pre-Match Lock)

预测快照在赛前 24h 内生成并锁定，开赛后冻结。这是系统的核心业务规则——赛后数据不得覆盖赛前决策。

### 4. 声明式 AI 配置 (Declarative AI Config)

AI 模型通过 YAML 配置，无需代码修改即可添加/禁用模型。提供商实现 OpenAI 兼容接口，统一调用方式。

### 5. 权重自适应 (Adaptive Weighting)

集成融合的权重根据可用数据源自动调整。缺少市场赔率时 AI 权重增加，缺少 AI 时市场权重增加，仅系统可用时使用 100% 系统权重。

### 6. 结构化日志 (Structured Logging)

所有日志使用 JSON 格式，包含请求 ID、时间戳、日志级别。支持按请求追踪、按错误过滤、慢请求分析。

### 7. 限流与认证 (Rate Limiting & Auth)

内置 IP 级滑动窗口限流和可选的 API Key 认证。写接口（POST/DELETE/PATCH）需要 `X-API-Key` 头，读接口开放。
