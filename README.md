# 2026 FIFA World Cup Prediction Workbench

> **状态：生产就绪 (Production Ready)**

本地优先、多层融合的 2026 FIFA 世界杯预测系统。覆盖 48 支球队、12 个小组、72 场小组赛，支持淘汰赛路径推演、赛前锁定、赛后复盘、AI 融合预测和球队画像。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | FastAPI + SQLAlchemy + SQLite (WAL) + APScheduler |
| 前端 | React 19 + Vite 6 + TypeScript 5.8 + TanStack Query |
| AI | DeepSeek V4 Flash/Pro + 小米 MiMo V2/V2.5 Pro (OpenAI 兼容 API) |
| 数学 | Elo 评级 + Poisson 进球模型 + Monte Carlo 模拟 |
| 数据 | OpenFootball + football-data.org + 体彩赔率 + WorldCup26 |

## 快速开始

```bash
# 1. 安装
./scripts/setup.sh

# 2. 配置 API Key（可选，不配置也能运行）
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY 和/或 XIAOMI_API_KEY

# 3. 启动
./start.sh

# 4. 访问
# 前端: http://127.0.0.1:5173
# 后端: http://127.0.0.1:8000
# API 文档: http://127.0.0.1:8000/docs
```

> 详细步骤请参阅 [QUICK_START.md](QUICK_START.md)

## 核心功能

### 预测流水线

```
数据同步 → 基线预测 (Elo+Poisson) → AI 预测 → 集成融合 → 24h 锁定 → 开赛 → 评分
```

1. **基线模型**：`elo-poisson-v1` 生成胜/平/负概率、xG 和比分矩阵。`elo-poisson-v3-profile` 在基线之上融合球队画像（攻防、状态、FIFA 排名）进行预测调整
2. **AI 预测**：多个 AI 模型独立生成预测（v2 提示词不含基线概率，避免锚定偏差）
3. **集成融合**：基线 + 市场赔率 + AI 预测的加权融合，缺失来源自动权重重分配
4. **24h 锁定**：赛前 24h 内生成快照，开赛时冻结，赛后数据不可覆盖赛前决策
5. **评分**：赛后 Brier 分数、Log Loss、命中率评估

### xG 校准与 Poisson 色散

基线模型已针对 WC 2026 实际数据校准：

| 参数 | v1 | 校准后 | 效果 |
|------|-----|--------|------|
| `base_goal_mean_home` | 1.25 | 1.55 | xG 从 2.37 → ~2.93（实际 3.03） |
| `base_goal_mean_away` | 1.10 | 1.35 | 同上 |
| `strength_coeff_home` | 0.90 | 1.20 | 强弱队 xG 分化更明显 |
| `max_xg` | 3.50 | 4.50 | 允许高比分预测 |
| `poisson_dispersion` | — | 1.10 | 幂变换轻微展平分布 |

回测对比（37 场 WC 2026 比赛）：精确比分 5.4% → 13.5%（2.5 倍），±1 球 73.0% → 75.7%，方向 48.6% → 51.4%，Brier 0.6071 → 0.6096（噪声范围内）。

### AI 预测

- **多模型**：DeepSeek V4 Flash/Pro、小米 MiMo V2/V2.5 Pro
- **双提示词**：v1（含基线参考）+ v2（独立判断，无基线泄漏）
- **去重**：跳过已有成功预测的模型（除非 force）
- **基线抄袭检测**：标记与系统预测完全一致的 AI 输出
- **独立审计**：`/api/ai-independence` 检查 AI 与基线的偏差程度

### 集成融合

| 场景 | 系统权重 | 市场权重 | AI 权重 |
|------|---------|---------|--------|
| 全部可用 | 40% | 20% | 40% |
| 无市场 | 50% | - | 50% |
| 无 AI | 75% | 25% | - |
| 仅系统 | 100% | - | - |

### 球队画像

- **已接入预测引擎**：`elo-poisson-v3-profile` 模型通过 `profile_adapter.py` 将画像分数（0-100）转换为 `MatchContext` 调整项，在 `predict_match()` 中按 `profile_weight` 加权影响攻防、状态、FIFA 排名；`profile_weight=0` 时完全回退到 v1 行为
- 使用 Mart Jürisoo 国际比赛结果快照（2022-01-01 至 2026-06-19 已完赛比赛）构建真实历史样本；`seed_mock_v1` 仅作无真实样本时的本地 fallback，并会降低数据可信度
- FIFA 排名优先从 FIFA 官方 FDCP API 导入，当前覆盖 48/48 支球队；Elo/FIFA source 会进入球队画像 `source_list`
- 七个结构化模块：基础实力、近期状态、攻防能力、战术风格、阵容与球员风险、比赛环境适应、数据可信度
- 阵容模块已接入 FIFA 官方 2026-06-20 Squad List，覆盖 48/48 队 26 人名单、位置深度、国家队出场和进球；伤停、停赛和首发确认仍保持 unavailable
- 比赛环境模块已用真实赛程和场地 registry 补充 `rest_days`、`schedule_fatigue_score`、旅行距离、时差、下一场和后续场地；历史气候基线来自 Open-Meteo Historical Weather API，且标记为 `is_match_forecast=false`
- 缺失数据显式标记 `unavailable` / `missing`，不会用 mock 伪装真实伤停、首发、旅行、气候或球员信息
- `data_quality_score` 会列出关键缺失 penalty；StatsBomb xG 只覆盖 2018/2022 世界杯样本，命中球队会在 `attack_defense.xg` 展示样本均值，未覆盖球队保留 `xg=unavailable`
- 前端在球队详情和比赛详情中展示画像分、优势、风险、缺失字段、数据来源和更新时间

详见 [docs/team_profiles.md](docs/team_profiles.md)。

### 赛事模拟

- Monte Carlo 模拟（默认 50,000 次迭代）
- 小组出线概率、淘汰赛晋级路径
- 淘汰赛对阵表生成
- 第三名排名规则

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (React + Vite)               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │  Daily    │ │  Match   │ │  Model   │ │ Tournament│  │
│  │ Dashboard │ │  Center  │ │  Review  │ │ & Schedule│  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │ REST API
┌────────────────────────▼────────────────────────────────┐
│                   Backend (FastAPI + SQLite)             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │  Elo +   │ │   AI     │ │ Ensemble │ │  Team     │  │
│  │  Poisson │ │ Models   │ │          │ │  Profile  │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │  Market  │ │ Scoring  │ │ Snapshot │ │ Workflow  │  │
│  │  Odds    │ │ Engine   │ │  Locking │ │  Engine   │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
└─────────────────────────────────────────────────────────┘
```

> 详细架构说明请参阅 [ARCHITECTURE.md](ARCHITECTURE.md)

## 配置

复制 `.env.example` 到 `.env` 并按需配置：

### 数据库

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_PATH` | `data/world-cup.sqlite3` | SQLite 数据库路径 |

### 数据源

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FOOTBALL_DATA_API_TOKEN` | 空 | football-data.org API 令牌（可选） |
| `API_FOOTBALL_TOKEN` | 空 | API-Football 令牌（可选） |
| `SPORTMONKS_TOKEN` | 空 | SportMonks 令牌（可选） |

### 模拟

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SIMULATION_ITERATIONS` | `50000` | Monte Carlo 迭代次数 |
| `SIMULATION_SEED` | `20260613` | 随机种子 |
| `REFRESH_INTERVAL_MINUTES` | `15` | 常规刷新间隔 |
| `LIVE_REFRESH_INTERVAL_MINUTES` | `2` | 比赛期间刷新间隔 |

### AI 预测

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLE_AI_PREDICTION` | `true` | 是否启用 AI 预测 |
| `AI_RUN_MODE` | `manual` | 运行模式：`manual` / `auto` |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek API 地址 |
| `XIAOMI_API_KEY` | 空 | 小米 MiMo API 密钥 |
| `XIAOMI_BASE_URL` | `https://api.xiaomimimo.com/v1` | 小米 MiMo API 地址 |
| `AI_TEMPERATURE` | `0` | AI 采样温度 |
| `AI_TIMEOUT_SECONDS` | `30` | 请求超时（秒） |
| `AI_MAX_RETRIES` | `2` | 最大重试次数 |
| `AI_MAX_CONCURRENT_REQUESTS` | `2` | 最大并发请求数 |
| `AI_RUN_ALL_MAX_LIMIT` | `20` | 批量运行最大比赛数 |

### 安全与 CORS

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ADMIN_API_KEY` | 空 | 写接口认证密钥（空=不认证） |
| `CORS_ALLOWED_ORIGINS` | `*` | 允许的 CORS 来源（逗号分隔） |

### 工作流

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AUTO_RUN_DAILY_WORKFLOW_ON_OPEN` | `true` | 前端打开时自动运行每日工作流 |
| `AUTO_RUN_AI_ON_OPEN` | `true` | 自动运行 AI 预测 |
| `WORKFLOW_AUTO_RUN_COOLDOWN_MINUTES` | `60` | 自动运行冷却时间（分钟） |

## 前端页面

| 页面 | 功能 |
|------|------|
| **每日仪表盘** | 今日状态、昨夜复盘、即将开赛、工作流操作 |
| **比赛中心** | 按小组/今日/淘汰赛查看所有比赛 |
| **模型复盘** | 模型对比、AI 评估、误差归因、校准分析 |
| **赛事中心** | 对阵表、晋级概率、球队路径、积分榜 |

比赛详情在共享的 `MatchDetailDrawer` 中展示，包含预测、画像、风险和锁定状态等标签页。

### 界面预览

**今日工作台** — 首屏展示今日运行状态、昨夜比赛复盘、即将开赛预测和工作流操作入口。

![今日工作台](docs/screenshots/daily-dashboard.png)

**比赛中心** — 按小组/今日/淘汰赛维度浏览所有比赛，点击比赛卡片查看详细预测。

![比赛中心](docs/screenshots/match-center.png)

**模型复盘** — 模型版本对比、AI 独立性评估、误差归因分析和校准曲线。

![模型复盘](docs/screenshots/model-review.png)

**冠军与赛程** — 淘汰赛对阵表、球队晋级概率投影和小组积分榜。

![冠军与赛程](docs/screenshots/tournament-center.png)

## 后端结构

```
backend/app/
├── api/routes/          # FastAPI 端点
├── services/
│   ├── dashboard.py     # 仪表盘 & 比赛详情组装
│   ├── refresh.py       # 比赛结果同步 & 重算触发
│   ├── recompute.py     # 全量重算、版本、基线/Shadow 预测
│   ├── scoring.py       # 赛后评分、排除、详情
│   ├── snapshots.py     # 24h 锁定 & 降级逻辑
│   └── accuracy_command.py  # 准确率指挥中心
├── ai/                  # AI 提供商、提示词、解析器、集成、评估
├── team_profiles/       # 数据加载、特征工程、画像服务
├── workflows/           # 自动化工作流状态 & 执行
├── tournament/          # 积分榜、对阵表、模拟
├── logging_config.py    # 结构化 JSON 日志（带轮转）
└── middleware.py         # 请求 ID 追踪 & 访问日志
```

## API 概览

| 分组 | 主要端点 |
|------|---------|
| 仪表盘 | `GET /api/dashboard`, `GET /api/matches/{id}`, `POST /api/refresh` |
| 评分 | `GET /api/model-score`, `GET /api/accuracy-command-center`, `GET /api/scoring-exclusions` |
| AI | `GET /api/ai-models`, `POST /api/ai-predictions/run`, `POST /api/ensemble/run` |
| 工作流 | `GET /api/workflows/status`, `POST /api/workflows/daily-open`, `POST /api/workflows/full` |
| 画像 | `GET /api/team-profiles`, `GET /api/team-profiles/{team_id}` |
| 赛事 | `GET /api/tournament/bracket`, `GET /api/tournament/projections`, `POST /api/tournament/simulate` |

> 完整 API 文档请参阅 [API.md](API.md) 或 http://127.0.0.1:8000/docs

## 数据源

| 来源 | 用途 |
|------|------|
| OpenFootball | 主要赛程 & 结果 |
| football-data.org | 补充结果 + 实时状态 |
| 体彩（中国） | 市场赔率对比 |
| World Football Elo Ratings | 初始 Elo 评分 |
| `data/seed/` | 本地种子 & 回放数据 |

上游源不可用时，系统保留上次成功获取的数据。

## 业务规则

### 时间与显示

- 所有存储、比较、锁定、评分使用 UTC
- 所有用户界面显示使用北京时间 (UTC+8)
- "今天"/"昨天"/"明天" 遵循 `Asia/Shanghai` 日历

### 赛前锁定优先级

赛前预测具有绝对优先级。赛后数据不得覆盖赛前决策样本。

24h 锁定规则：
1. 比赛开赛 24h 内生成锁定快照
2. 开赛前：锁定快照随最新预测就地更新
3. 开赛时/后：锁定快照永久冻结
4. 超过 24h：不生成锁定快照

### 评分样本标准

评分必须区分：
- 总已完成比赛
- 有赛前预测的比赛
- 有开赛前快照的比赛
- 有锁定/降级快照的比赛
- 实际进入评分的比赛

"已完成比赛"不得等同于"评分样本"。

## 测试

```bash
# 后端
cd backend && .venv/bin/python -m pytest tests/ -q

# 前端
cd frontend && npm test -- --run && npm run typecheck && npm run build
```

## 日志

结构化 JSON 日志位于 `data/logs/`：

```bash
# 仅错误
cat data/logs/error.jsonl | python3 -m json.tool

# 按请求 ID 追踪
grep "REQUEST_ID" data/logs/app.jsonl | python3 -m json.tool

# 慢请求 (>1s)
grep "duration_ms" data/logs/app.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line)
    if d.get('duration_ms', 0) > 1000:
        print(f'{d[\"duration_ms\"]:.0f}ms {d[\"message\"]}')"
```

## 已知限制

1. 球队画像已切换为真实国际比赛结果快照；`seed_mock_v1` 仅作为无真实样本时的本地 fallback
2. 淘汰赛模拟为简化版本，不应视为正式计算
3. 免费/公开数据源可能有延迟、WAF 拦截、字段漂移或覆盖不全
4. AI / 情报 / 市场功能依赖本地 API 令牌配置
5. OpenFootball 和 WorldCup26 提供商不支持 `live` 比赛状态，仅 football-data.org 支持

## 免责声明

所有预测仅供信息参考，不构成投注建议。足球比赛固有的不可预测性无法被完全建模。

## AI 协作

AI 代理修改本项目前须先阅读 `AI_PROJECT_CONSTRAINTS.md`。前端变更还需阅读 `FRONTEND_UI_RULES.md`。任何新的长期业务约束必须反映在这些文件中。
