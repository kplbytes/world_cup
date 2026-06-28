# 变更日志

## [2026-06-28] - 淘汰赛链路与文档同步

### 淘汰赛

- **官方淘汰赛赛程入库**：新增 `data/seed/world-cup-2026-knockout.json`，按官方 Match 73-104 生成 32 场淘汰赛占位赛程
- **最佳第三名官方组合表**：新增 `data/seed/world-cup-2026-third-place-combinations.json`，替换旧的简化第三名分配口径
- **自动晋级推进**：已结束淘汰赛会按比分或 `home_advance` / `away_advance` 自动写入下一轮；支持加时/点球标记
- **淘汰赛详情复用**：冠军与赛程页中的对阵卡现在会复用共享 `MatchDetailDrawer`，点击即可查看同一套比赛详情
- **启动自修复**：服务启动时会同步淘汰赛占位状态，并修复异常中断后残留的 running workflow

### 预测与复盘

- **后端自动 AI workflow**：`AI_RUN_MODE=auto` 时会注册 `world-cup-auto-ai` 调度任务，按刷新间隔定时尝试触发 `pre-match`，并复用现有工作流锁、冷却和按钮可用性判断
- **同版重算修复**：`recompute_all()` 现在会先同步淘汰赛占位/晋级状态，再在同一个 active revision 中补齐小组赛与淘汰赛预测，避免 knockout 预测缺失或落到孤立 revision
- **画像进入淘汰赛重算链路**：淘汰赛重算会继续加载 Team Profile 调整项，而不是在 knockout 阶段静默禁用画像权重
- **模型复盘与用户侧模型展示继续收敛**：停用、欠费或已下线模型不再出现在用户侧说明中
- **模型复盘状态降级**：模型复盘页明确区分加载、部分失败和空数据状态，避免用户一直看到“加载中”
- **工作流运行摘要增强**：`/api/workflows/runs` 现在返回 run / step 级 summary、started_at、finished_at，首页顶部状态和最近运行记录会展示步骤级摘要
- **淘汰赛占位赛跳过修复**：`ensemble_generation` 遇到官方占位赛且对阵未决时记为 `skipped / teams_tbd`，不再把正常待定状态误报成失败
- **部分成功终态补全**：`partial_success` 步骤现在会写入 `finished_at` 和 `duration_seconds`，避免历史记录长期显示为未结束

### 文档

- **README / QUICK_START / API / ARCHITECTURE / DEPLOY** 已同步到当前淘汰赛实现
- **AI_PROJECT_CONSTRAINTS / FRONTEND_UI_RULES / docs/team_profiles.md** 已补充官方淘汰赛、手动工作流、保留型 auto 开关边界，以及当前画像进入 baseline 但不直接进入 AI prompt 的约束

## [2026-06-26] - 工作流与文档对齐

### 工作流与前端

- **首页动作区改为纯手动触发**：取消“刷新页面自动跑 workflow”的默认行为，当前 `更新今日数据`、`同步赛果`、`运行 AI 预测`、`一键更新全部` 都通过按钮手动触发
- **AI 冷却范围收敛**：60 分钟冷却只作用于 AI 预测按钮，不再误伤“更新今日数据”和“同步赛果”
- **统一进度展示**：工作流运行中时，首页动作按钮和顶部状态条都会展示百分比进度
- **导航与模型复盘修复**：固定四个顶层入口，修复模型复盘页加载和布局漂移问题

### 后端与运行时

- **后台定时刷新默认关闭**：`ENABLE_SCHEDULED_REFRESH=false` 时不再自动周期性刷新赛果/赛程，仅保留快照锁定和维护任务
- **异常中断自修复**：启动时自动把上次非正常退出遗留的 `running` workflow / step 标记为 `failed`
- **评分与复盘性能优化**：仪表盘、模型复盘、画像评估、评分统计改为批量查询和按需字段加载，减少大 JSON 解码和 N+1 查询
- **AI 模型展示清理**：用户侧不再暴露停用模型信息

### 文档

- **README / QUICK_START / API / DEPLOY / ARCHITECTURE** 已同步到当前实现
- **AI_PROJECT_CONSTRAINTS / docs/team_profiles.md** 已更新为“手动触发 + 当前画像链路”口径
- **失效阶段文档已清理**：移除无引用的旧交接草稿、审计快照和阶段性方案文档，避免继续误导

## [Production Ready] - 2026-06-19

### P0 修复（关键）

- **集成状态 Bug**：修复 `ensemble_predictions.source_status_json` 未正确反映 AI 预测状态的问题，确保集成融合在 AI 预测完成后正确标记来源状态
- **KeyError 修复**：修复评分引擎在处理缺失字段时的 `KeyError` 异常，增加防御性字段访问
- **asyncio.run 冲突**：修复 AI 预测服务在 FastAPI 异步上下文中调用 `asyncio.run()` 导致的事件循环冲突，改用 `await` 调用
- **AI 解析器测试**：修复 AI 响应解析器测试用例，确保各种 AI 输出格式（JSON、Markdown 代码块、纯文本）正确解析
- **工作流集成测试**：修复工作流端到端集成测试，确保 `daily-open`、`pre-match`、`post-match`、`full` 工作流正确执行

### P1 修复（重要）

- **线程安全缓存**：修复赛事模拟投影缓存的线程安全问题，使用 `threading.Lock` 保护缓存读写
- **限流器清理**：修复内存限流器中过期 IP 条目未清理导致的内存泄漏，增加 120 秒清理阈值
- **健康检查**：增强 `/api/health` 端点，增加 AI 提供商可用性、APScheduler 运行状态、上次成功同步时间检查
- **Mock 数据移除**：移除 `player_mock.py` 中的硬编码模拟数据，确保生产环境不使用假数据
- **市场赔率重试**：修复市场赔率获取失败后不重试的问题，增加指数退避重试逻辑
- **AI 提示词清理**：清理 v2 提示词中的基线概率泄漏，确保独立判断模式不包含任何系统预测参考
- **枚举验证**：增加 `app_mode` 和 `ai_run_mode` 的枚举值验证，防止配置错误
- **CORS 配置**：CORS 允许来源改为可配置（`CORS_ALLOWED_ORIGINS` 环境变量），默认 `*` 兼容本地开发

### P2 修复（改进）

- **market_blend 修复**：修复市场赔率融合时 overround 处理逻辑，确保融合后概率归一化
- **player_mock 清理**：移除球员模拟数据中的占位符，避免误导性信息
- **data_loader 防御**：增加数据加载器的防御性检查，处理空数据和格式异常
- **安全加固**：
  - 增加 API Key 认证中间件（`X-API-Key` header）
  - 增加 IP 级滑动窗口限流
  - 增加路径遍历防护（SPA fallback）
  - 移除日志中的敏感信息

## [v0.1.0] - 2026-06-13

### 新功能

- **预测系统**：Elo + Poisson 基线预测引擎，支持 48 队 12 组 72 场小组赛
- **赛事模拟**：Monte Carlo 模拟（默认 50,000 次迭代），小组出线概率、淘汰赛路径
- **数据同步**：OpenFootball + football-data.org + 体彩赔率多源同步
- **赛前锁定**：24h 锁定窗口，开赛冻结，赛后不可覆盖
- **赛后评分**：Brier 分数、Log Loss、命中率、误差归因
- **AI 预测**：DeepSeek V4 Flash/Pro
- **集成融合**：系统 + 市场 + AI 加权融合，自适应权重
- **球队画像**：特征工程、风险标记、触发特征
- **工作流引擎**：自动化每日工作流、赛前工作流、赛后工作流
- **前端仪表盘**：React SPA，每日仪表盘、比赛中心、模型复盘、冠军与赛程
- **结构化日志**：JSON 格式日志，请求 ID 追踪，日志轮转
- **API 文档**：FastAPI 自动生成的 OpenAPI 文档
