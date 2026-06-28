# AI_PROJECT_CONSTRAINTS.md
# 2026 世界杯预测系统长期约束

开始前必须先阅读项目根目录的 `AI_PROJECT_CONSTRAINTS.md`，并严格遵守。若本次新增了长期约束，必须同步更新该文件。

如果任务涉及前端页面、布局、组件或交互，还必须同时阅读 `FRONTEND_UI_RULES.md`。

## 1. 文件定位

这不是通用编码规范，而是本项目的长期业务约束入口。它解决的问题是：

- 新接手的 AI 不知道现有业务边界；
- README、交接文档、实现代码已经出现时间差；
- 一些核心规则分散在 `workflow`、`snapshots`、`scoring`、`ai`、`team_profiles` 中，容易被误改。

当以下信息冲突时，按这个优先级判断：

1. 本文件；
2. 当前实际代码；
3. `TEAM_PROFILE_IMPLEMENTATION_REPORT.md` / `TEAM_PROFILE_HANDOFF_PLAN.md`；
4. `README.md`。

说明：`README.md` 用于总览，但任何时候只要与当前代码冲突，都不能高于本文件和实际实现。

## 2. 项目当前定位

这是一个本地运行的 2026 世界杯预测系统，不是通用足球平台，也不是生产 SaaS。

当前核心链路是：

1. baseline `elo-poisson-v1`
2. shadow / numerical variants
3. AI prediction
4. ensemble
5. 赛前决策快照（开赛前最新有效预测）
6. 赛后评分与复盘
7. Team Profile 画像输入与独立评估
8. 前端工作台 / 比赛中心 / 模型复盘 / 冠军与赛程

禁止无授权扩 scope：

- 不要擅自引入新的顶层业务模块；
- 不要把“实验模型”伪装成默认模型；
- 不要把“页面展示需要”直接改成“底层评分口径变化”；
- 不要把 mock/seed 数据写成真实历史数据。

## 3. 时间、时区、命名口径

### 3.1 时间口径

- 存储、比较、锁定、评分以 UTC 为准；
- 用户展示以北京时间为准；
- 今日 / 昨日 / 明日这类用户业务概念，以北京时间自然日为准；
- SQLite 可能返回 naive datetime，涉及比较时必须显式补 UTC；
- 任何“今天 / 昨天 / 刚结束 / 即将开赛”的解释，最终都要能落到具体时间窗口。

### 3.2 北京时间展示

- 前端展示比赛时间时，默认使用北京时间格式化；
- 不允许前端直接裸渲染 UTC 字符串；
- 如果一个页面展示的是业务时间，必须让用户看到这是北京时间，而不是浏览器本地随机时区。

### 3.3 中文队名口径

- 用户侧优先展示统一中文名；
- 不要在组件里临时硬编码一套新译名；
- 队名映射应复用现有本地化/展示工具。

## 4. 首页工作流触发约束

当前默认行为已经改为**手动触发**，不要再把首页恢复成“刷新页面就自动调用 workflow”。

真实逻辑：

- 前端通过 `/api/workflows/status` 获取 `next_action`、`button_states` 和最近一次运行进度；
- 首页动作区的 `更新今日数据`、`同步赛果`、`运行 AI 预测`、`一键更新全部` 由用户手动点击后才会调用对应 POST 接口；
- `AUTO_RUN_DAILY_WORKFLOW_ON_OPEN` 和 `AUTO_RUN_AI_ON_OPEN` 默认都是 `false`；
- `WORKFLOW_AUTO_RUN_COOLDOWN_MINUTES` 当前主要用于 AI 预测按钮冷却，不适用于“更新今日数据”和“同步赛果”。
- 顶部 Header 不再放重复的“同步赛果”入口，相关操作统一留在今日工作台动作区。

因此：

- 不能再声称“首页打开会自动跑 daily-open”；
- 也不能把页面刷新误改回自动触发；
- 与动作区相关的修改，必须同时检查前端按钮状态、后端 `button_states` 和进度条展示。

## 5. 模型版本隔离约束

任何新模型必须独立 `model_version`，不得污染 baseline。

当前至少包含：

- baseline: `elo-poisson-v1`
- profile: `elo-poisson-v1-team-profile`
- numerical shadow: `elo-poisson-v1-intel-numeric`
- AI: `ai-*`
- ensemble: `ensemble-v1`

禁止：

1. 直接改写 baseline 结果冒充 baseline；
2. 将 shadow/profile/AI 结果写回 baseline 表；
3. 因为个别比赛输赢就静默改 baseline 口径；
4. 样本不足时自动把实验模型切成默认主模型。

## 6. 赛前决策快照与评分口径

### 6.1 核心规则：开赛前最新有效预测

赛后评分以"开赛前用户可见的最后一份有效预测"为准，不再以 24h 锁定为核心口径。

用户真实使用场景是在北京时间白天查看今晚/凌晨比赛预测。赛后复盘应使用开赛前用户可见的最后一份有效预测快照。

24h 锁定如保留字段，只作为兼容或辅助信息，不作为核心评分依据。

24h 锁定的业务规则：
1. 距离开赛 ≤24h 时，生成 locked snapshot
2. 开赛前新预测到来时，locked snapshot 原地更新（始终保持赛前最新版本）
3. 开赛后 locked snapshot 冻结，不允许赛后数据覆盖
4. 距离开赛 >24h 的比赛，不提前锁定

### 6.2 当前评分选择逻辑

当前 `scoring.py` 的 scorable snapshot 选择规则是：

**选择开赛前（kickoff 前）最新的用户可见预测快照。**

不再区分 is_pre_match_locked / is_fallback_locked 优先级。只要快照创建时间 < kickoff，就是有效的赛前决策快照。

因此任何复盘、报表、前端文案都必须区分：

- 已完赛比赛数；
- 有赛前预测比赛数；
- 有开球前快照的比赛数；
- 实际进入 model-score 的比赛数。

不能把"已完赛场次"直接当成"评分样本数"。

### 6.3 未评分原因必须明确

当前系统使用的原因码包括：

- `no_final_score`
- `no_prediction`
- `no_pre_match_snapshot`
- `excluded_after_kickoff`
- `ai_missing`
- `ensemble_missing`

不再使用 `no_locked_snapshot` 作为排除原因。只要存在开球前快照即可参与评分，无需 24h 锁定。

新增口径时应尽量复用已有 reason code，不要同义词泛滥。

### 6.4 实时预测边界

- 开赛后生成的预测只能作为实时展示；
- 不得让用户误以为所有实时预测都会进入赛后评分；
- 前端必须明确展示：是否参与评分、快照生成时间、距离开赛多久、是否为实时展示。

## 7. Team Profile 约束

### 7.1 当前真实状态

Team Profile 已经接入当前代码，且当前重算链路会把画像特征转换成 `MatchContext` 调整项；同时系统仍保留独立评估端点用于单独复盘画像效果。

这意味着：

- 结构、接口、评分、抽屉展示和主预测链路都要验证；
- 历史画像来源可能是真实公开数据，也可能回退到 `seed_mock_v1`；
- 任何页面、接口、文档、报告都必须明确标注来源和可信度。

### 7.2 画像输入约束

当前要求：

- profile 数据和独立评估结果仍存独立表；
- 进入主预测链路的画像修正必须保留 caps、来源和 `as_of` 边界；
- 不得用未来数据、mock 或主观判断偷偷放大主预测结果；
- baseline 家族的最终输出变化必须能回溯到具体画像输入和权重配置。

### 7.3 时间切片

Team Profile 必须支持 `as_of` 视角。

- 预测某场比赛时，只能使用该场 kickoff 之前可见的数据；
- 禁止未来数据回填到过去画像；
- 禁止用 2026 已赛结果污染该结果发生前的赛前画像。

### 7.4 调整上限

画像修正必须可解释且受限：

- 单个 outcome 概率修正不超过 5%；
- 单场总 L1 修正不超过 8%；
- xG 修正不超过 `±0.15`；
- 最终概率必须归一化。

### 7.5 小样本保护

- `sample_count < 6` 时，不得下强结论；
- 不得强打标签；
- 不得触发激进修正；
- 前端要展示“弱提示 / 样本不足”，而不是假装稳定可靠。

## 8. AI 与 Ensemble 约束

### 8.1 AI 状态必须真实

AI 模型状态不能只显示 enabled 或"已运行 N 个"。

当前实现至少要区分：

- `ready`
- `disabled_no_key`
- `provider_error`

前端类型里可能还有更宽泛的状态枚举，但展示和判断应以真实后端返回为准。

如果 API Key 未配置，不能展示成 ready。

### 8.1.1 AI 有效性口径

"AI 已运行"不等于"AI 有效"。前端和后端必须区分以下概念：

- **配置模型数**：`ai_models.yaml` 中 enabled 的模型数量
- **尝试调用数**：实际发起 HTTP 请求的次数
- **成功解析数**：API 返回且 JSON 解析成功、概率合法的预测数
- **失败数**：API 调用失败或返回错误的预测数
- **解析错误数**：API 返回但 JSON 解析失败或概率非法的预测数
- **有效参与 Ensemble 数**：成功 + 非 real_time_only 的预测数
- **有效参与评分数**：成功 + is_pre_match_locked 或 is_fallback_locked 的预测数

前端今日工作台和模型复盘都不得只显示"AI 已运行 N 个模型"而忽略失败和解析错误。

`/api/workflows/status` 返回的 `ai_status` 字段包含上述完整口径。

### 8.1.2 用户侧可见模型约束

- 停用、欠费或已明确下线的 provider/model，不要继续在首页、模型复盘或 README 中当成当前可用能力展示；
- 当前用户侧文档与页面只应描述实际启用且可见的模型集合。

### 8.2 AI 触发必须真实调用

“刷新 AI 预测”类交互必须真实调用后端，而不是只重新拉取 GET。

当前标准链路：

1. `POST /api/ai-predictions/run?match_id=...`
2. `GET /api/ai-predictions?match_id=...`
3. `POST /api/ensemble/run?match_id=...`
4. `GET /api/ensemble?match_id=...`

### 8.3 AI 失败必须展示原因

不能只写“暂无 AI 预测”。

要优先展示真实错误来源，例如：

- API Key 未配置
- provider 未配置
- 请求超时
- JSON 解析失败
- 概率非法
- provider_error

### 8.4 Prompt 约束

- AI 只能使用系统提供的数据；
- Team Profile 相关结论必须来自 profile 输入；
- 不能凭球队名气自行脑补“传统强队气质”之类声誉信息；
- profile 样本不足时，AI 必须同时表达不确定性。

### 8.5 Ensemble 约束

Ensemble 必须可解释，至少要能说清：

- baseline 是否参与；
- 哪些 AI 参与；
- market 是否参与；
- profile 是否参与；
- 缺失来源后如何降级。

缺失 AI 时，Ensemble 可以降级，但不能伪装成"AI 已参与的集成"。

### 8.5.1 Ensemble 权重归一化

AI 内部权重必须按所有有效模型的 `ensemble_weight` 求和后归一化，再乘以 AI 总权重。

当前配置（`ai_models.yaml`）：

- 3 个启用中的 AI 模型，`ensemble_weight` 分别为 0.33, 0.67, 0.33，总和 = 1.33
- 默认总权重：system = 0.35，market = 0.30，AI = 0.35

3 个模型全部参与时，每个模型的 AI 内部占比：

- `0.33/1.33 ≈ 0.248`
- `0.67/1.33 ≈ 0.504`
- `0.33/1.33 ≈ 0.248`

最终权重 = 上述占比 × 0.35。

如果只有部分 AI 模型参与，`total_config_weight` 只计算参与模型的权重和，不会出现权重被重复放大的问题。

`_compute_weights` 函数末尾有全局归一化步骤，确保所有权重之和 = 1.0。

### 8.6 三分类 Brier 基线

当前系统使用三分类 one-hot Brier：

```
(p_home - o_home)² + (p_draw - o_draw)² + (p_away - o_away)²
```

三分类均匀随机预测的 Brier ≈ **0.667**（不是 0.25）。

0.25 是二分类 Brier 的随机基线，不得用于本系统。

前端模型复盘页面必须显示正确的三分类基线解释。

### 8.7 手动 AI 批量补跑规则

`pre-match`、`full`，以及显式传入 `with_ai=true` 的 `daily-open` 工作流，可以批量为符合条件的比赛补跑 AI 预测：

只运行符合以下条件的比赛：

- 未开赛（status ≠ final）
- 未来 48 小时内
- AI enabled
- API key ready
- 尚无有效 AI 预测
- 不超过 `AI_RUN_ALL_MAX_LIMIT`

以下情况不要补跑赛前 AI：

- 已开赛或已结束
- 已有有效 AI
- API Key 未配置
- 模型 disabled
- 同场 1 小时重跑冷却尚未结束（单场 `force=true` 仍要遵守冷却）

但前端必须显示跳过原因，且“更新今日数据”按钮默认不应顺带调用 AI。

## 9. 前端信息架构约束

### 9.1 顶层导航

顶层只保留四个主入口：

- 今日工作台
- 比赛中心
- 模型复盘
- 冠军与赛程

不要恢复旧的多入口堆叠结构。

### 9.2 比赛详情交互

比赛卡片默认只显示摘要信息。详情必须通过共享抽屉/弹窗组件展示，不要重新改回卡片内部长展开。

当前方向：

- 桌面端右侧抽屉；
- 小屏端居中 Modal；
- 支持遮罩、关闭按钮、ESC 关闭；
- 卡片高度保持基本稳定。

### 9.3 数据展示真实性

前端出现“已生成 / 已锁定 / 可评分 / AI 已就绪 / Ensemble 已生成”时，必须能映射到真实后端状态，不得用猜测文案代替真实状态。

## 10. 数据来源真实性约束

这是高优先级规则。

- 没有真实调用过的系统，不要在首页写成“已运行”；
- 没有真实数据支撑的模块，不要包装成真实来源；
- `seed_mock_v1`、缓存降级、fallback、未配置 API Key，都必须在合适位置显式暴露。

如果一个功能只是“代码路径存在”，但实际上没有被触发、没有数据、没有有效调用，就不能在用户视角把它当作已生效能力。

### 10.1 淘汰赛官方赛程真实性

淘汰赛路径当前必须以真实官方赛程和官方第三名组合表为准，不能再退回“简化模拟”口径写到用户文案里。

当前真实边界：

- Match 73-104 赛程来自本地种子 `data/seed/world-cup-2026-knockout.json`；
- 最佳第三名落位优先使用 `data/seed/world-cup-2026-third-place-combinations.json`；
- 已结束淘汰赛必须按比分或 `home_advance` / `away_advance` 自动推进到下一轮；
- 小组赛未全部结束前，部分席位允许显示 `待定` 或保留 source ref，但不能把临时预览写成最终结果。

## 11. 修改边界与同步义务

### 11.1 修改边界

- 优先做外科手术式修改；
- 不因本次任务顺手重构整站；
- 不删除用户已有未提交改动；
- 不把临时实验说明写成长期事实。

### 11.2 需要同步更新本文件的情况

如果改动影响以下任一事项，必须同步更新本文件：

- 顶层导航结构；
- 自动 workflow 入口；
- 赛前决策快照/评分口径；
- 淘汰赛官方赛程、第三名组合表或晋级推进规则；
- baseline / profile / AI / ensemble 关系；
- Team Profile 数据模式；
- AI 调用与错误显示原则；
- 用户会直接感知的数据真实性边界。

前端视觉、布局、交互、浏览器级验收规则，优先沉淀到 `FRONTEND_UI_RULES.md`。

## 12. 推荐核对清单

在做涉及预测链路的改动前，优先核对这些文件：

- `backend/app/workflows/service.py`
- `backend/app/services/snapshots.py`
- `backend/app/services/scoring.py`
- `backend/app/ai/service.py`
- `backend/app/ai/prompt_builder.py`
- `backend/app/team_profiles/service.py`
- `frontend/src/App.tsx`
- `frontend/src/components/DailyDashboard.tsx`
- `frontend/src/components/MatchDetailDrawer.tsx`
- `frontend/src/api.ts`
- `frontend/src/types.ts`

目标不是机械全读，而是先确认这次改动会不会碰到赛前锁定、评分口径、AI 真实调用、Team Profile 数据边界。

## 13. 日志系统约束

### 13.1 日志架构

当前系统使用集中式日志配置（`backend/app/logging_config.py`），不再允许各模块自行配置 handler。

日志输出三通道：
- Console：人类可读格式，含 request_id / workflow_run_id 上下文
- `data/logs/app.jsonl`：JSON Lines 结构化日志，10MB 轮转，保留 30 个备份
- `data/logs/error.jsonl`：仅 ERROR+，5MB 轮转，保留 10 个备份

### 13.2 日志级别规范

- `DEBUG`：开发调试信息，仅文件输出
- `INFO`：正常业务流程（请求、workflow 步骤、预测生成）
- `WARNING`：可恢复的异常（API 降级、缓存过期、fallback 触发）
- `ERROR`：需要关注的错误（API 失败、数据异常、评分错误）

当前分级配置：
- 核心服务（recompute/snapshots/scoring/dashboard/workflows）：INFO
- AI providers：INFO
- Tournament bracket/qualification：WARNING（小组赛期间第三名分配噪音大）
- 第三方库（httpx/apscheduler/openai）：WARNING

### 13.3 链路追踪

- 每个 HTTP 请求自动分配 `request_id`（支持 `X-Request-ID` header 传入）
- 每个 workflow 运行自动设置 `workflow_run_id`
- 日志中包含 `request_id` 和 `workflow_run_id` 字段，用于关联同一操作链路的所有日志

### 13.4 修改约束

- 不允许在模块中自行添加 `logging.FileHandler` 或 `logging.StreamHandler`
- 新增模块使用 `logger = logging.getLogger(__name__)` 即可，由 `logging_config.py` 统一管理
- 如需调整模块日志级别，修改 `logging_config.py` 中的 `MODULE_LOG_LEVELS`
- 不允许使用 `print()` 替代日志输出

## 19. 今日比赛与赛程筛选约束

这是高优先级约束。此前系统出现过“北京时间 6 月 14 日今日比赛中显示北京时间 6 月 15 日比赛”的问题，后续不得再出现。

### 13.1 今日比赛定义

“今日比赛”只能指北京时间自然日：

- `00:00:00 - 23:59:59`
- 时区固定为 `Asia/Shanghai`

不能使用：

- UTC 自然日；
- 美国当地时间；
- 浏览器默认时区；
- 服务器默认本地时区。

如果页面展示的是未来 48 小时比赛，标题必须明确写“未来 48 小时比赛”，不能标题写“今日比赛”但实际筛选未来 48 小时。

首页主比赛列表固定使用“从当前时刻起未来 24 小时”的滚动窗口，只展示未完赛比赛，标题必须明确写“未来 24 小时比赛（北京时间）”。已完赛比赛必须移入复盘区，并显示最终比分、是否存在赛前快照、是否纳入评分及未纳入原因。

### 13.2 必须区分的赛程概念

任何代码、接口、前端文案都必须区分：

- 今日比赛：北京时间当天 `00:00 - 23:59`
- 明日比赛：北京时间次日 `00:00 - 23:59`
- 未来 24 小时比赛：从当前时刻起往后 24 小时，仅含未完赛比赛
- 未来 48 小时比赛：从当前时刻起往后 48 小时
- 已完赛比赛：`status=final` 或有明确 final score
- 刚结束比赛：必须给出明确时间窗口定义

不得混用。

### 13.3 验收要求

涉及赛程筛选的修改，必须输出验证表，至少包含：

- `match_id`
- `home_team`
- `away_team`
- `raw_kickoff_time`
- `kickoff_time_china`
- `china_date_key`
- `included_in_today`
- `included_in_next_48h`

前端“今日比赛”页面必须只展示 `included_in_today=true` 的比赛。

## 20. 前端 UI 稳定性约束

此前前端出现过比赛卡片横向溢出、长队名撑爆、概率条跑出容器、卡片互相遮挡的问题。后续任何前端改动不得破坏 UI 稳定性。

### 14.1 比赛卡片约束

比赛卡片必须满足：

1. 内容不得横向溢出；
2. 长队名不得撑爆卡片；
3. 概率条不得跑出父容器；
4. 卡片之间不得互相遮挡；
5. 查看详情按钮位置稳定；
6. 卡片内部不得展开长详情；
7. 详情必须通过共享抽屉或弹窗展示；
8. 页面不得出现横向滚动条。

### 14.2 推荐实现方向

今日工作台、比赛中心、全部比赛等位置，应尽量复用统一卡片组件，例如：

- `MatchCard` / `MatchSummaryCard`
- `MatchDetailDrawer`

不要在不同页面重复实现多套比赛卡片逻辑。

### 14.3 浏览器宽度验收

涉及前端 UI 的修改，不允许只跑 typecheck。必须至少检查：

- `1440px`
- `1280px`
- `1024px`
- `768px`
- `390px`

每个宽度都要确认：

1. 今日工作台不溢出；
2. 比赛中心不溢出；
3. 队名中文且不撑爆；
4. Drawer / Modal 能正常打开；
5. 页面无横向滚动条。

## 21. 真实数据与接口验收约束

任何涉及数据链路、AI、评分、workflow、Team Profile 的任务，不能只说“已实现”，必须给出真实接口或数据库验证证据。

### 15.1 常用接口验收

根据任务范围，至少检查相关接口：

- `GET /api/dashboard`
- `GET /api/workflows/status`
- `GET /api/ai-models`
- `POST /api/ai-predictions/run?match_id=xxx`
- `GET /api/ai-predictions?match_id=xxx`
- `GET /api/ensemble?match_id=xxx`
- `GET /api/model-score`
- `GET /api/model-score/details`
- `GET /api/accuracy-command-center`
- `GET /api/team-profiles`
- `GET /api/team-profiles/{team_id}`
- `GET /api/team-profiles/evaluation`
- `GET /api/team-profile-predictions/{match_id}`

### 15.2 交付要求

最终报告中必须说明：

1. 调用了哪些接口；
2. 接口返回是否符合预期；
3. 是否真实写入数据库；
4. 前端是否重新刷新显示；
5. 是否存在 mock、fallback、seed 数据；
6. 用户侧是否有明确提示。

## 22. 测试与真实运行约束

### 16.1 后端测试

涉及后端修改时必须运行：

```bash
cd backend
.venv/bin/python -m pytest tests/ -q
```

### 16.2 前端测试

涉及前端修改时必须运行：

```bash
cd frontend
npm test -- --run
npm run typecheck
npm run build
```

### 16.3 格式检查

提交前必须运行：

```bash
git diff --check
```

### 16.4 前后端联动验证

涉及按钮、workflow、AI、评分、详情抽屉、Team Profile 展示时，必须真实启动前后端并完成联动验证。默认验证方式：

```bash
cd backend
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

cd frontend
npm run dev
```

并在浏览器中完成真实操作验证。

## 23. 每次任务开始与结束的强制流程

### 17.1 开始前

每次 AI 接手任务前，必须先阅读：

```bash
cat AI_PROJECT_CONSTRAINTS.md
```

如果任务涉及前端，还必须阅读：

```bash
cat FRONTEND_UI_RULES.md
```

并明确确认：

已阅读 `AI_PROJECT_CONSTRAINTS.md`，将遵守项目长期约束。

涉及前端时还必须确认：

已阅读 `FRONTEND_UI_RULES.md`，将遵守前端长期约束。

未阅读本文件，不得修改代码。

### 17.2 结束时

每次任务完成后，必须输出：

1. 修改文件列表；
2. 遵守了哪些项目约束；
3. 是否产生新的长期约束；
4. 如果产生，是否已更新 `AI_PROJECT_CONSTRAINTS.md`；
4.1. 如果是前端长期规则，是否已更新 `FRONTEND_UI_RULES.md`；
5. 测试结果；
6. 真实接口 / 浏览器验证结果；
7. 剩余问题。

如果本次没有产生新的长期约束，也必须写明：

本次未产生新的长期约束，因此未修改 `AI_PROJECT_CONSTRAINTS.md`。

如果本次是前端任务且未产生新的前端长期约束，也必须写明：

本次未产生新的前端长期约束，因此未修改 `FRONTEND_UI_RULES.md`。

## 24. 新增长期约束的同步规则

如果本次任务过程中发现新的长期规则，例如：

- 新的时间口径；
- 新的评分口径；
- 新的模型隔离关系；
- 新的数据真实性边界；
- 新的前端展示规则；
- 新的 workflow 触发规则；

必须同步写入本文件。

不得只在临时报告、聊天记录、README 或实现报告中说明。长期约束必须沉淀到 `AI_PROJECT_CONSTRAINTS.md`。

当 README、交接报告、实现报告与本文件冲突时，以本文件为准。

如果新增的是纯前端长期规则，例如：

- 卡片结构；
- 抽屉交互；
- 中文化展示；
- 响应式验收；
- 浏览器截图要求；

优先同步到 `FRONTEND_UI_RULES.md`，必要时在本文件中保留入口和原则性说明。
