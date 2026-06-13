# 2026 世界杯小组赛预测系统

纯本地运行的 2026 世界杯数据看板，覆盖 A–L 组、48 支球队和 72 场小组赛。系统提供积分榜、每场胜平负概率、xG、最可能比分、晋级概率、模型解释和数据来源状态。所有预测在本机完成计算，数据存储在本地 SQLite，不依赖云服务。

## 功能概览

- A–L 组完整赛程与实时积分榜（FIFA 官方排名规则，含相互战绩 tie-break）
- 每场未赛比赛的 Elo + Poisson 预测：胜/平/负概率、双方 xG、最可能比分
- 50,000 次蒙特卡洛模拟：12 组排名 + 8 个最佳第三名晋级概率
- 中文球队名称、确定性中文模型解释与数据置信度（数据新鲜度、排名覆盖、历史覆盖、来源一致性）
- 数据质量置信度与模型确定性分离展示，避免把“数据完整”误读成“结果一定准”
- 本地 SQLite 持久化，每次完整计算形成独立 revision，支持审计回溯
- 程序运行期间自动检查公开赛果；比赛窗口内加速至 2 分钟刷新
- 页面"同步赛果"按钮支持手动触发刷新
- 支持人工赛前修正：可按比赛录入伤停、轮换、战意等修正量，并即时重算预测
- 模型评分接口支持按 `model_version` 聚合历史评分，便于对比不同版本的 Brier、LogLoss 和命中率
- 上游不可用时保留最后一次成功数据，不清空本地看板
- 可选 football-data.org 实时赛果适配器（需免费 API Token）
- 可选中国体彩网 HAD 赔率解析器，仅作市场对照，不覆盖模型结果

## 技术选型

### 后端

| 组件 | 技术 | 说明 |
|------|------|------|
| 语言 | Python 3.12+ | 运行环境为 3.14，向下兼容 3.12 |
| Web 框架 | FastAPI | 异步 HTTP API，自带 OpenAPI 文档 |
| ORM | SQLAlchemy 2 | 声明式模型，WAL 模式 SQLite |
| 验证 | Pydantic 2 + pydantic-settings | 数据契约校验与环境配置 |
| HTTP 客户端 | HTTPX | 外部数据源抓取，支持超时与重定向 |
| 定时任务 | APScheduler | 后台间隔刷新，支持动态调整间隔 |
| 数值计算 | NumPy + SciPy | Poisson 分布矩阵与蒙特卡洛采样 |
| 测试 | pytest | 78 个后端测试 |

### 前端

| 组件 | 技术 | 说明 |
|------|------|------|
| 框架 | React 19 | 函数式组件 + Hooks |
| 语言 | TypeScript 5.8 | 严格模式 |
| 构建 | Vite 6 | 开发热更新 + 生产构建 |
| 数据获取 | TanStack Query v5 | API 状态管理，1 分钟 staleTime |
| 图表 | 手写概率条 + 卡片式信息设计 | 无额外图表依赖 |
| 测试 | Vitest + Testing Library | 3 个集成测试 |
| 样式 | 手写 CSS + CSS Variables | 暗色主题，oklch 色彩空间，响应式 |

## 项目架构

```
world_cup/
├── backend/
│   ├── app/
│   │   ├── api/routes.py              # 14 个 REST 端点
│   │   ├── config.py                  # 环境变量与路径配置
│   │   ├── db.py                      # SQLite 引擎、WAL、事务管理
│   │   ├── main.py                    # FastAPI 生命周期、调度器、SPA 服务
│   │   ├── models.py                  # 15 张 SQLAlchemy 表
│   │   ├── schemas.py                 # Pydantic 数据契约（48 队 / 72 场校验）
│   │   ├── domain/
│   │   │   └── standings.py           # FIFA 积分排名 + 相互战绩 + 最佳第三名
│   │   ├── prediction/
│   │   │   ├── elo.py                 # Elo 评级（零和更新 + 进球差乘数）
│   │   │   ├── poisson.py             # xG 转换 + Poisson 比分矩阵
│   │   │   ├── confidence.py          # 数据驱动的置信度评分
│   │   │   └── explanation.py         # 确定性中文解释文本
│   │   ├── providers/
│   │   │   ├── base.py                # Provider 协议定义
│   │   │   ├── openfootball.py        # OpenFootball 本地/远程适配器
│   │   │   ├── football_data.py       # football-data.org 可选适配器
│   │   │   └── sporttery.py           # 中国体彩网 HAD 赔率解析
│   │   ├── simulation/
│   │   │   └── qualification.py       # 蒙特卡洛晋级模拟（向量化采样）
│   │   └── services/
│   │       ├── manual_adjustments.py  # 人工修正聚合与序列化
│   │       ├── seed.py                # 种子数据导入（幂等）
│   │       ├── localization.py        # 中文展示名称映射
│   │       ├── recompute.py           # 原子化全量重算 + revision 发布
│   │       ├── refresh.py             # 增量刷新 + 终场冲突检测
│   │       └── dashboard.py           # revision 一致的看板数据组装
│   └── tests/                         # 75 个测试，覆盖全部模块
├── frontend/
│   ├── src/
│   │   ├── App.tsx                    # 根组件（分组/全部比赛/决策视图切换）
│   │   ├── api.ts                     # fetch 封装
│   │   ├── types.ts                   # 完整类型定义
│   │   ├── styles.css                 # 暗色主题 + 响应式布局
│   │   ├── components/
│   │   │   ├── Header.tsx             # 标题 + revision 信息 + 同步按钮
│   │   │   ├── DataSources.tsx        # 数据来源状态条
│   │   │   ├── GroupNav.tsx           # A–L 分组导航
│   │   │   ├── GroupDashboard.tsx     # 积分表 + 晋级概率 + 比赛卡片
│   │   │   ├── MatchCard.tsx          # 可展开比赛详情（xG/比分/解释）
│   │   │   ├── AllMatches.tsx         # 全部比赛筛选视图
│   │   │   ├── TeamDetail.tsx         # 球队详情抽屉
│   │   │   └── ProbabilityBar.tsx     # 概率条组件
│   │   └── test/
│   │       └── App.test.tsx           # 集成测试
│   └── dist/                          # 生产构建产物
├── data/
│   ├── seed/
│   │   ├── world-cup-2026.json        # 48 队 72 场规范化种子
│   │   └── elo-ratings-2026.json      # 48 队 Elo 快照
│   └── world-cup.sqlite3              # 运行时数据库（WAL 模式）
├── scripts/
│   ├── setup.sh                       # 一键安装
│   ├── dev.sh                         # 开发模式启动
│   └── start.sh                       # 生产模式启动
├── .env.example                       # 配置模板
└── README.md
```

## 实现逻辑

### 数据流

```
外部数据源 → Provider 适配 → Pydantic 校验 → SQLite 持久化
                                                    ↓
                                    Elo 更新 ← 终场结果写入
                                        ↓
                              Poisson 比分矩阵生成（每场未赛）
                                        ↓
                              蒙特卡洛 50,000 次模拟
                                        ↓
                              原子化 Revision 发布
                                        ↓
                              FastAPI JSON → React 看板
```

### 启动流程

1. `create_app()` 创建 FastAPI 实例，注册 API 路由和 SPA 静态文件服务
2. `lifespan` 事件触发 `initialize_database()`：
   - 创建 SQLite 数据库（WAL 模式，外键约束）
   - 若数据库为空，从 `data/seed/` 导入 48 队 72 场种子数据
   - 导入 48 队 Elo 评级快照
   - 若无活跃 revision，执行首次全量计算
3. APScheduler 启动后台定时刷新（默认 15 分钟间隔）
4. 每次刷新后检测是否有近期比赛，有则自动切换到 2 分钟快速刷新

### 预测模型

**Elo 实力评级**：初始值来自 World Football Elo Ratings 公开数据。新终场结果通过 FIFA 标准 Elo 公式更新（400 分标度 + 进球差对数乘数），保持零和。历史回放支持截止日期过滤。

**Poisson 比分矩阵**：两队相对实力（含近期状态微调）映射为双方预期进球数（xG），xG 裁剪至 [0.20, 3.50] 区间。人工赛前修正会直接作用于攻防期望进球，再生成 0–7 球精确概率 + 8+ 尾部桶，取外积得到 8×8 比分矩阵。胜/平/负概率由矩阵求和得出，归一化至 1.0。

**蒙特卡洛晋级模拟**：锁定已终场比分，对每场剩余比赛从比分矩阵中采样（NumPy 向量化），重复 50,000 次。每次迭代对 12 组分别排名，再对 12 个第三名排序取前 8 名晋级。最终输出每队的第一/二/三/四名频率和总晋级概率，附带蒙特卡洛标准误。

**置信度**：加权平均——数据新鲜度 35%、排名覆盖 25%、历史覆盖 25%、来源一致性 15%。分 High（≥0.8）/ Medium（≥0.6）/ Low（<0.6）三档。置信度由数据质量决定，不因预测差距大而虚高。

### 数据调和规则

1. 比赛身份使用「赛事-组别-主队-客队-日期」组合键，不依赖单一 Provider ID
2. 终场比分优先于 scheduled/live 状态
3. 已保存终场若与新数据冲突，不静默覆盖，写入同步警告
4. 未知球队、重复比赛、无效比分会导致整个 payload 被拒绝
5. 每个接受字段记录 Provider 名称和抓取时间戳

## 安装与启动

### 环境要求

- Python 3.12+
- Node.js 20+ 和 npm

### 一键安装

```bash
./scripts/setup.sh
```

脚本会自动创建 Python 虚拟环境（`backend/.venv`）、安装前后端依赖并构建前端页面。

### 生产模式

```bash
./scripts/start.sh
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。后端服务前端静态文件，单进程运行，仅监听本机回环地址。首次启动会自动导入种子数据并完成首次预测计算（约需数秒）。

### 开发模式

```bash
./scripts/dev.sh
```

同时启动后端（`http://127.0.0.1:8000`，支持代码热重载）和前端 Vite 开发服务器（`http://127.0.0.1:5173`，API 请求自动代理到后端）。

### 配置

复制 `.env.example` 为 `.env` 可调整运行参数：

```env
# 数据库路径
DATABASE_PATH=data/world-cup.sqlite3

# 可选：football-data.org 免费 API Token（不配置则仅使用 OpenFootball）
FOOTBALL_DATA_API_TOKEN=

# 常规刷新间隔（分钟）
REFRESH_INTERVAL_MINUTES=15

# 比赛窗口期刷新间隔（分钟）
LIVE_REFRESH_INTERVAL_MINUTES=2

# 蒙特卡洛模拟次数
SIMULATION_ITERATIONS=50000

# 随机种子（保证结果可复现）
SIMULATION_SEED=20260613
```

## 使用指南

### 看板操作

- **分组导航**：左侧 A–L 按钮切换分组，每组显示 4 队积分表和 6 场比赛
- **比赛详情**：点击任意比赛卡片展开查看胜平负概率、xG、最可能比分和模型解释
- **人工修正**：同一展开区域会显示已生效的人工赛前修正，包括影响球队、修正类型、攻防增减和备注
- **球队详情**：点击积分表中的队名打开右侧抽屉，显示 Elo、近期状态、晋级概率分布和本组赛程
- **全部比赛**：切换到"全部比赛"视图，可按 scheduled / live / final 筛选 72 场比赛
- **同步赛果**：点击右上角"同步赛果"按钮手动触发数据刷新和全量重算

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查，返回当前 revision ID |
| GET | `/api/dashboard` | 完整看板数据（12 组 / 48 队 / 72 场） |
| GET | `/api/groups/{A-L}` | 单组详情（积分、球队、比赛） |
| GET | `/api/matches` | 全部比赛，可选 `?status=final` 过滤 |
| GET | `/api/matches/{id}` | 单场比赛详情 |
| GET | `/api/teams/{id}` | 单支球队详情及本组赛程 |
| GET | `/api/data-sources` | 数据来源状态（Provider、URL、抓取时间） |
| GET | `/api/decision` | 决策视图数据（今日重点、最稳、最纠结、复盘） |
| GET | `/api/model-score` | 最新评分 + 评分历史 + 按 `model_version` 的版本对比 |
| GET | `/api/manual-adjustments` | 列出人工修正，可选 `?match_id=` 过滤单场 |
| GET | `/api/sync-runs` | 同步运行历史（成功/失败/警告记录） |
| POST | `/api/refresh` | 手动触发刷新，返回同步结果摘要 |
| POST | `/api/manual-adjustments` | 新增人工修正并触发重算 |
| DELETE | `/api/manual-adjustments/{id}` | 删除人工修正并触发重算 |

API 文档自动生成：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### 人工修正示例

```bash
curl -X POST http://127.0.0.1:8000/api/manual-adjustments \
  -H 'Content-Type: application/json' \
  -d '{
    "match_id": "2026-A-CZE-RSA-2026-06-18",
    "adjustment_type": "伤停",
    "affected_team_id": "CZE",
    "attack_delta": -0.15,
    "defense_delta": 0.00,
    "confidence": "medium",
    "note": "主力前锋伤缺，进攻下调。"
  }'
```

建议把 `attack_delta` / `defense_delta` 控制在 `-0.30` 到 `+0.30` 之间。正值表示增强，负值表示削弱。

### 数据备份

数据库文件位于 `data/world-cup.sqlite3`（WAL 模式）。停止程序后直接复制该文件即可备份。恢复时将备份文件复制回原位。

## 数据来源

| 来源 | 用途 | 认证 |
|------|------|------|
| OpenFootball World Cup 2026 | 球队、分组、赛程、终场比分（主力源） | 无需，公共领域 |
| World Football Elo Ratings | 48 队 Elo 初始实力 | 无需，公开数据 |
| football-data.org | 可选实时赛果补充 | 免费 API Token |
| 中国体彩网竞彩足球 | 可选 HAD 赔率市场对照 | 无需，但受 WAF 限制 |
| FIFA 官方页面 | 终场冲突时的人工核验权威 | 无需 |

项目内置规范化种子数据位于 `data/seed/`，即使暂时无法联网也能查看最近一次计算结果。

## 测试

```bash
# 后端测试（78 个）
cd backend && .venv/bin/pytest -q

# 前端测试（3 个）+ 类型检查 + 构建
cd frontend && npm test -- --run && npm run typecheck && npm run build
```

## 限制

- 免费数据源可能延迟或调整字段格式，系统会显示来源时间和失败状态
- 球员名单、伤停和实时身价没有同等稳定、完整且免费的权威 API，不参与核心预测，缺失时不伪造
- 解释文本由确定性模板生成，不会添加数据中不存在的事实
- 所有预测仅供信息参考，不构成投注建议
