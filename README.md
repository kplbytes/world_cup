# 2026 世界杯预测系统

本项目是一个纯本地运行的 2026 世界杯预测工作台。当前覆盖 48 支球队、12 个小组、72 场小组赛，并在现有实现上继续向淘汰赛路径、赛前锁定、赛后复盘、AI 融合和球队画像方向扩展。

它的核心不是“做一个漂亮的数据页”，而是围绕这条业务链路工作：

1. 白天打开系统，看昨晚哪些比赛已经结束；
2. 用赛前有效快照做赛后复盘；
3. 看今天和接下来 48 小时比赛的预测与风险；
4. 在需要时手动补跑 AI、生成 ensemble、检查 24h 锁定；
5. 让复盘继续服务赛前预测，而不是污染赛前口径。

## AI 协作入口

任何 AI 代理在开始修改本项目之前，都应先阅读项目根目录的 [AI_PROJECT_CONSTRAINTS.md](/Users/liudapeng/Documents/code/others/world_cup/AI_PROJECT_CONSTRAINTS.md)。

涉及前端页面、布局、组件或交互时，还应同时阅读 [FRONTEND_UI_RULES.md](/Users/liudapeng/Documents/code/others/world_cup/FRONTEND_UI_RULES.md)。

如果本次工作新增了长期有效的业务约束，也必须同步更新对应文件。

## 当前系统定位

当前代码已经不是最初的单一 Elo + Poisson 看板，而是一个本地化、可审计的多层预测系统。当前真实业务链路是：

1. baseline `elo-poisson-v1`
2. shadow / calibrated / numerical experiment versions
3. AI prediction
4. ensemble
5. 24h 赛前锁定
6. 赛后评分与复盘
7. Team Profile 独立候选模型
8. 前端四个主入口：今日工作台、比赛中心、模型复盘、冠军与赛程

## 主要能力

### 预测与模拟

- baseline Elo + Poisson 胜平负预测、xG、最可能比分
- 小组赛蒙特卡洛模拟与晋级概率
- 多个实验型 `model_version` 配置，通过 `backend/app/model_configs/model_configs.yaml` 管理
- 市场赔率对照与轻量 market blend / calibration 评估

### 赛前决策闭环

- 24h 赛前锁定
- fallback 快照逻辑
- 决策快照状态检查
- 今日工作台中的下一步建议、昨晚复盘、今日比赛、未来 48 小时比赛

### 赛后复盘

- `/api/model-score` 统一评分
- 按版本、按阶段、按比赛明细查看评分
- 错误归因、校准、市场对照、模型推荐
- Accuracy Command Center 汇总当前模型表现

### AI 与 Ensemble

- 多模型 AI 预测，当前配置文件中包含 DeepSeek 和 Xiaomi MiMo
- AI 真实状态区分 `ready`、`disabled_no_key`、`provider_error` 等
- AI 手动补跑、批量运行、only-missing、防重复调用
- Ensemble 结合 baseline、market、AI 权重生成集成预测

### Team Profile

- 已接入独立的 Team Profile 模块
- 独立模型版本：`elo-poisson-v1-team-profile`
- 支持 profile as-of 时间切片、独立评分和前端展示
- 当前历史画像数据模式是 `seed_mock_v1`

说明：`seed_mock_v1` 仅用于功能验证，不代表真实历史表现。

## 当前前端结构

前端顶层只保留四个固定入口：

1. 今日工作台
2. 比赛中心
3. 模型复盘
4. 冠军与赛程

### 今日工作台

当前首页是运营工作台，不是开发调试面板。主要承载：

- workflow 今日状态
- 自动触发结果与下一步建议
- 昨晚比赛复盘
- 今日比赛（按北京时间自然日）
- 未来 48 小时比赛
- 模型性能概览
- 最近运行记录

### 比赛中心

当前比赛中心包含：

- 今日比赛
- 全部比赛
- 分组视图
- 淘汰赛路径

比赛详情通过共享抽屉 `MatchDetailDrawer` 展示，不再在卡片内部纵向展开长详情。

### 模型复盘

模型复盘页当前聚合：

- 当前结论
- 模型版本对比
- AI 对比
- 错误归因
- 历史比赛复盘
- 球队画像模型表现
- 未参与评分比赛的排除原因

### 冠军与赛程

当前冠军与赛程页聚合：

- 当前对阵路径
- 晋级概率
- 团队路径与阶段概率

说明：当前淘汰赛路径仍带有“简化模拟”提示，不能当作最终正式赛制参考。

## 后端模块结构

当前后端主要按下面几层组织：

- `backend/app/api/routes/`
  - 看板、数据、评分、AI、workflow、tournament、team profile 路由
- `backend/app/services/`
  - `dashboard.py`：统一看板与详情数据组装
  - `refresh.py`：赛果刷新与重算触发
  - `recompute.py`：全量重算、revision 发布、profile candidate 生成
  - `scoring.py`：赛后评分、排除原因、评分明细
  - `snapshots.py`：24h 锁定与 fallback 相关逻辑
  - `accuracy_command.py`：准确率指挥中心
- `backend/app/ai/`
  - AI provider、prompt、parser、ensemble、evaluation
- `backend/app/team_profiles/`
  - 数据加载、特征工程、画像服务、画像评分
- `backend/app/workflows/`
  - 自动工作流状态、调度和执行
- `backend/app/tournament/`
  - standings、bracket、tournament simulation

## 关键业务规则

### 时间与展示

- 存储、比较、锁定、评分以 UTC 为准
- 用户展示以北京时间为准
- 今日 / 昨日 / 明日等业务概念按 `Asia/Shanghai` 自然日解释

### 赛前锁定优先于赛后解释

系统核心是赛前预测。赛后复盘必须基于赛前有效快照，不允许把开赛后的实时预测混成赛前决策样本。

### 评分样本口径

评分必须区分：

- 已完赛比赛数
- 有赛前预测比赛数
- 有 pre-kickoff snapshot 的比赛数
- 有 locked / fallback 快照的比赛数
- 实际进入评分的比赛数

不能把“已完赛场次”直接当成“评分样本数”。

### 首页自动 workflow

页面打开后，前端会先请求 `/api/workflows/status`。当后端返回 `recommended_action=run_daily_open_workflow` 时，首页会自动触发 `POST /api/workflows/daily-open`。

这个自动触发受以下条件限制：

- 当前是否已有 workflow 在跑
- 当日是否已运行
- cooldown 是否有效
- 是否允许 auto AI

因此它不是“每次打开页面都必跑”，而是“第一次有效打开时按规则自动跑”。

## 数据来源

当前主链路和可选来源如下：

| 来源 | 当前用途 |
|------|------|
| OpenFootball | 主赛程、基础比分、默认公开数据源 |
| football-data.org | 可选赛果补充 |
| 中国体彩网 / sporttery | 市场赔率对照 |
| API-Football / SportMonks | 情报与阵容能力入口，取决于 token 与配置 |
| World Football Elo Ratings | 初始 Elo |
| `data/seed/` | 本地种子与回放基础数据 |
| `seed_mock_v1` | Team Profile 功能验证用历史画像数据 |

上游不可用时，系统尽量保留最后一次成功数据，不会因为单个来源失败而直接清空看板。

## 安装与启动

### 环境要求

- Python 3.12+
- Node.js 20+
- npm

### 首次安装

```bash
./scripts/setup.sh
```

这个脚本会：

- 创建 `backend/.venv`
- 安装后端依赖
- 安装前端依赖
- 预构建前端产物

### 推荐的日常启动方式

如果你是日常本地使用，推荐使用根目录脚本：

```bash
./start.sh
./stop.sh
```

它会：

- 自动清理已有 8000 / 5173 端口进程
- 启动后端 `uvicorn`
- 启动前端 Vite
- 在终端中持续显示日志

启动后访问：

- 前端：<http://127.0.0.1:5173>
- 后端：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>

### 开发模式

如果你希望只用仓库内标准脚本：

```bash
./scripts/dev.sh
```

它会同时启动：

- 热重载后端 `uvicorn --reload`
- 前端 Vite dev server

### 仅后端服务已构建前端

```bash
./scripts/start.sh
```

这个脚本会在缺少 `frontend/dist` 时先构建前端，然后只启动后端，由后端直接托管前端静态文件。

## 配置

复制 `.env.example` 为 `.env` 后，常用配置如下：

```env
# 数据库
DATABASE_PATH=backend/data/world-cup.sqlite3

# 刷新与模拟
REFRESH_INTERVAL_MINUTES=15
LIVE_REFRESH_INTERVAL_MINUTES=2
SNAPSHOT_LOCK_INTERVAL_MINUTES=1
SIMULATION_ITERATIONS=50000
SIMULATION_SEED=20260613

# AI
ENABLE_AI_PREDICTION=true
AI_RUN_MODE=manual
AI_MAX_CONCURRENT_REQUESTS=2
AI_RUN_ALL_MAX_LIMIT=20
DEEPSEEK_API_KEY=
XIAOMI_API_KEY=

# 自动 workflow
AUTO_RUN_DAILY_WORKFLOW_ON_OPEN=true
AUTO_RUN_AI_ON_OPEN=true
WORKFLOW_AUTO_RUN_COOLDOWN_MINUTES=60
WORKFLOW_DEFAULT_HOURS=48
WORKFLOW_DEFAULT_SINCE_HOURS=24
WORKFLOW_DEFAULT_LIMIT=10
WORKFLOW_DEFAULT_LOCK_WINDOW_HOURS=24
```

说明：

- `DATABASE_PATH` 在当前示例配置中指向 `backend/data/world-cup.sqlite3`
- 如果未配置 AI key，前端必须显示真实不可用状态，而不是假装 ready

## 每日使用方式

### 1. 打开页面

打开首页后，系统会先判断是否需要自动执行 daily-open workflow。

### 2. 查看昨晚复盘

重点看：

- 昨晚比赛复盘
- 未参与评分原因
- 当前模型性能概览

### 3. 查看今日比赛与未来 48 小时比赛

重点看：

- 今日比赛：按北京时间自然日筛选
- 未来 48 小时比赛：明确区别于“今日比赛”
- 比赛卡片上的综合推荐、风险、快照状态

### 4. 打开详情抽屉

比赛详情抽屉当前会展示：

- 综合结论
- Baseline / AI / Ensemble 对比
- Team Profile
- 风险解释
- 锁定 / fallback / 是否参与评分
- AI 错误信息（如果有）

### 5. 需要时手动触发 workflow

当前前端可以手动触发：

- daily-open
- pre-match
- post-match
- lock
- full

## API 分组

README 不再维护逐条全量端点说明，避免再次落后于代码。当前按模块可分为：

### Dashboard / Data

- `/api/health`
- `/api/dashboard`
- `/api/groups/{group}`
- `/api/matches`
- `/api/matches/{match_id}`
- `/api/teams/{team_id}`
- `/api/data-sources`
- `/api/sync-runs`
- `/api/refresh`
- `/api/decision`
- `/api/manual-adjustments`
- `/api/accuracy-command-center`

### Scoring / Review

- `/api/model-score`
- `/api/model-score/details`
- `/api/model-score/by-version`
- `/api/model-score/by-stage`
- `/api/scoring-exclusions`
- `/api/match-count-breakdown`
- `/api/error-attribution-summary`
- `/api/model-calibration`
- `/api/market-comparison`
- `/api/model-recommendation`
- `/api/data-quality`
- `/api/model-configs`
- `/api/decision-snapshot-status`

### AI / Ensemble

- `/api/ai-models`
- `/api/ai-predictions`
- `/api/ai-predictions/run`
- `/api/ai-predictions/run-all`
- `/api/ensemble`
- `/api/ensemble/run`
- `/api/ai-evaluation`

### Workflow

- `/api/workflows/status`
- `/api/workflows/daily-open`
- `/api/workflows/pre-match`
- `/api/workflows/post-match`
- `/api/workflows/lock`
- `/api/workflows/full`
- `/api/workflows/runs`

### Team Profile

- `/api/team-profiles`
- `/api/team-profiles/{team_id}`
- `/api/team-profiles/evaluation`
- `/api/team-profiles/rebuild`
- `/api/team-profile-predictions/{match_id}`

### Tournament

- `/api/tournament/bracket`
- `/api/tournament/projections`
- `/api/tournament/simulate`
- `/api/tournament/team-path`
- `/api/tournament/standings`

完整定义以 FastAPI 文档为准：<http://127.0.0.1:8000/docs>

## 常用命令

### 测试

```bash
cd backend
.venv/bin/python -m pytest tests/ -q

cd frontend
npm test -- --run
npm run typecheck
npm run build
```

### Team Profile 重建

```bash
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/build_team_profiles.py
```

### 赛前 / 赛后脚本

```bash
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/run_pre_match_workflow.py
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/run_post_match_workflow.py
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/lock_pre_match_predictions.py
```

## 当前已知限制

1. Team Profile 当前仍是 `seed_mock_v1`，只能做功能验证，不能当作真实历史画像依据。
2. 部分淘汰赛路径与冠军模拟仍带有“简化模拟”性质，不能当作正式赛制计算结果。
3. 免费或公共数据源会有延迟、WAF、字段漂移和覆盖不完整问题。
4. AI / 情报 /市场能力是否可用，强依赖本地 token 和配置状态。
5. README 只维护“当前真实业务逻辑和入口”，不再承诺逐个组件、逐个测试数量、逐个实验版本都实时更新。

## 免责声明

所有预测仅供信息参考，不构成投注建议。足球比赛始终存在不可建模的偶然性。
