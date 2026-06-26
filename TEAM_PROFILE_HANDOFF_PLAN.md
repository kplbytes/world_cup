# Team Profile 完整开发交接计划

> 注：这是 Team Profile 引入阶段的交接计划快照，不是 2026-06-26 当前运行态的最高真相源。当前行为请以 `AI_PROJECT_CONSTRAINTS.md`、`README.md`、`docs/team_profiles.md` 和实际代码为准。

## 目标

在现有 2026 世界杯预测系统中完成可审计、可时间切片、可关闭、可独立评分的 Team Profile 模块，并完成后端、AI Prompt、前端和真实运行验证。

## 强制约束

1. 不允许 `git reset`、`git checkout --`、清理工作区或覆盖用户已有改动。
2. 不允许 commit、push、merge 或创建 PR。
3. 使用现有工作区继续，不创建 worktree。
4. baseline `elo-poisson-v1` 必须保持原行为；画像只能作为独立版本 `elo-poisson-v1-team-profile`。
5. 所有历史画像必须按 `profile_as_of < match.kickoff` 切片，禁止未来数据泄漏。
6. 当前历史数据是 `seed_mock_v1`，必须在 API、UI 和报告中明确标记，不能描述成真实历史数据。
7. 单 outcome 概率修正不超过 5%，单场概率总修正不超过 8%，xG 修正不超过 ±0.15，最终概率归一化。
8. 样本少于 6 场不得产生强标签。
9. 使用 `apply_patch` 做手工编辑，保持修改范围聚焦。

## 当前已完成

### 数据模型

已在 `backend/app/models.py` 增加：

- `TeamProfileMatchHistory`
- `TeamProfile`
- `TeamProfilePrediction`

### Team Profile 模块

已新增：

- `backend/app/team_profiles/__init__.py`
- `backend/app/team_profiles/data_loader.py`
- `backend/app/team_profiles/feature_engineering.py`
- `backend/app/team_profiles/scorer.py`
- `backend/app/team_profiles/service.py`
- `backend/app/team_profiles/evaluation.py`

已实现：

- opponent tier：elite / strong / mid / weak
- deterministic `seed_mock_v1` 历史明细
- as-of 日期过滤
- 基础进攻、防守、大小球、平局、热门稳定性、弱队韧性、大赛经验指标
- 基于证据的 traits
- profile adjustment cap 和概率归一化
- baseline/profile Brier 对比及 helped/hurt/neutral

### 预测融合

`backend/app/services/recompute.py` 已尝试在 baseline 之外写入 `TeamProfilePrediction`。

`backend/app/services/snapshots.py` 已增加 profile 预测 24h 锁定。

需要重点审查：

- profile 预测代码是否只插入一次且位置正确；
- group 与 knockout 是否都覆盖；
- baseline 表和模拟矩阵是否完全未被 profile 修改；
- profile snapshot 是否保存正确 `profile_version`、`profile_as_of`；
- fallback 和赛后评分逻辑是否完整。

### API

已新增：

- `GET /api/team-profiles`
- `GET /api/team-profiles/{team_id}`
- `POST /api/team-profiles/rebuild`
- `GET /api/team-profiles/evaluation`
- `GET /api/team-profile-predictions/{match_id}`

路由文件：`backend/app/api/routes/team_profile_routes.py`

### AI Prompt

已修改：

- `backend/app/ai/providers/base.py`
- `backend/app/ai/prompt_builder.py`
- `backend/app/ai/parser.py`
- `backend/app/ai/schemas.py`
- `backend/app/ai/service.py`

目标字段：

- `home_team_profile`
- `away_team_profile`
- `profile_factors`
- `profile_risk_flags`

需要补测试，确认 prompt 中包含 profile，且 AI 输出字段能解析并保存到现有 risk/key factors。

### 前端

已修改：

- `frontend/src/types.ts`
- `frontend/src/api.ts`
- `frontend/src/components/MatchDetailDrawer.tsx`
- `frontend/src/components/TeamDetail.tsx`
- `frontend/src/components/ModelReviewCenter.tsx`
- `frontend/src/styles.css`

已通过一次 `npm run typecheck`。

目标 UI：

- 比赛详情抽屉：主客队画像、本场 profile 概率影响、依据、数据模式；
- 球队详情：画像标签、样本量、平局/韧性/进攻防守；
- 模型复盘：baseline vs profile Brier、helped/hurt、有效/误导 trait。

需要补前端测试，并用浏览器实际截图验收。

### 当前测试证据

- `backend/tests/test_team_profiles.py`: 6 passed
- Team Profile 相关组合测试：46 passed
- 修改后后端全量：312 passed，1 个 Starlette 弃用 warning
- 前端 typecheck：通过

注意：上述后端全量通过发生在最后一次前端修改之前，但后端代码当时已基本完成。仍必须重新执行全部验收。

## 被中断的操作

最后执行的是：

```bash
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/build_team_profiles.py
PYTHONPATH=backend backend/.venv/bin/python -c '... recompute_all ...'
```

用户中断后进程已经不存在。可能第一条已提交、第二条未完成。必须先查询真实数据库：

- `team_profiles` 数量是否为 48；
- `team_profile_match_history` 数量是否合理；
- `team_profile_predictions` 是否存在重复或半成品；
- active revision 是否有效；
- 服务数据库必须是 `backend/data/world-cup.sqlite3`。

## 必须继续完成的任务

### 1. 审计并修复模型/迁移

- 确认新表能由 `Base.metadata.create_all()` 创建。
- 如现有 SQLite 需要字段迁移，在 `backend/app/db.py` 添加幂等 migration/user_version。
- 为三个表添加必要唯一约束或应用层去重，防止重复 rebuild/recompute。
- 审查 timezone-aware/SQLite naive datetime 比较。

### 2. 完善 seed 与画像字段

- 保证 48 队每队都有 profile。
- seed 明细至少覆盖世界杯、预选赛、洲际赛事标签。
- API 明确返回 `source_summary_json.mode=seed_mock_v1`。
- tier JSON 必须包含各层胜/平/负、进失球和样本数。
- 样本不足不产生强标签。

### 3. 完善独立 profile 预测

- baseline 预测结果和模拟必须不变。
- 每场未赛比赛生成独立 `TeamProfilePrediction`。
- group 和 knockout 都生成。
- profile 预测必须保存 baseline 概率、修正后概率、xG、deltas、flags、traits、explanation、as-of。
- 24h 只锁定最新合法 profile 预测。
- 开赛后预测标记 `real_time_only`，不参与评分。

### 4. 完善评分

- `evaluate_profile_model()` 必须计算正确 Brier、LogLoss、Hit Rate、Draw Hit、Favorite Wrong、Underdog Miss、Overconfident Wrong。
- helped/hurt/neutral 使用同场 baseline 与 profile Brier 差：阈值 ±0.01。
- 输出 per-match traits、flags 和 explanation。
- 最有用/最误导 trait 按比赛效果聚合。
- 不得使用赛后或未来 profile。

### 5. 完善 API 与 Dashboard

- 检查 `build_match_detail()` 返回 `team_profiles` 和 `profile_prediction`。
- 检查 `build_team_detail()` 返回 `team_profile`。
- API datetime/SQLAlchemy 对象必须 JSON 可序列化。
- rebuild API 默认 seed，但明确返回数据模式。
- 为 API 增加后端测试。

### 6. 完善 AI Prompt

- prompt 只能引用 profile 数据，不允许声誉推断。
- 样本不足必须进入 uncertainty 指令。
- `input_snapshot_json` 保存 home/away profile 和 as-of。
- parser 支持 `profile_factors`、`profile_risk_flags`。
- 增加测试证明 prompt 与 parser 行为。

### 7. 完善前端

- 修复所有 type/build/test 错误。
- 比赛详情抽屉通过 `/api/matches/{id}` 获取画像详情。
- 球队详情通过 `/api/team-profiles/{team_id}` 获取画像。
- 模型复盘通过 `/api/team-profiles/evaluation` 获取 profile 效果。
- 显示 `seed_mock_v1`，避免用户误认为真实历史数据。
- 样本不足显示弱提示。
- 移动端保持可读。
- 增加至少三个前端测试：比赛画像、球队画像、profile 复盘。

### 8. 真实数据库构建与运行验证

依次执行：

```bash
cd /Users/liudapeng/Documents/code/others/world_cup
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/build_team_profiles.py
```

然后用较小 iterations 做一次验证性 recompute，避免长时间阻塞；确认正确后再按项目配置重算一次。

查询并记录：

- profiles 总数；
- history 总数；
- profile predictions 总数；
- 每队是否都有 profile；
- 下一场比赛 profile prediction；
- profile adjustment 是否满足 cap；
- baseline prediction 是否仍存在且未覆盖。

### 9. 浏览器验收

启动后端和前端，使用浏览器检查：

1. 今日比赛卡片 -> 查看详情 -> 球队画像区；
2. 球队详情 -> 球队画像区；
3. 模型复盘 -> 球队画像模型表现；
4. 桌面抽屉和移动端 modal；
5. 保存至少 3 张截图到 `frontend/screenshots/`，文件名含 `team-profile`。

## 必须新增/确认的测试

后端至少覆盖：

1. 表创建；
2. 历史明细入库；
3. opponent tier；
4. vs strong/weak；
5. draw resilience；
6. favorite overconfidence；
7. low score；
8. traits；
9. 小样本；
10. as-of 防泄漏；
11. adjustment cap；
12. normalization；
13. baseline 不变；
14. 独立 model version；
15. helped/hurt；
16. AI prompt；
17. 24h profile snapshot 保存 as-of/version。

前端至少覆盖：

1. 比赛详情显示球队画像；
2. 球队详情显示画像；
3. 模型复盘显示 profile 评价。

## 最终验收命令

```bash
cd /Users/liudapeng/Documents/code/others/world_cup/backend
.venv/bin/python -m pytest tests/ -q

cd /Users/liudapeng/Documents/code/others/world_cup/frontend
npm test -- --run
npm run typecheck
npm run build

cd /Users/liudapeng/Documents/code/others/world_cup
git diff --check
```

## 最终报告要求

完成后必须把结果写入：

`/Users/liudapeng/Documents/code/others/world_cup/TEAM_PROFILE_IMPLEMENTATION_REPORT.md`

报告必须包含：

1. 修改文件列表；
2. 新增表结构；
3. 新增 API；
4. 数据来源说明；
5. 画像计算逻辑；
6. 示例球队画像 JSON；
7. 示例比赛 adjustment；
8. baseline vs profile 对比；
9. 全量测试原始摘要；
10. 浏览器截图路径和效果说明；
11. mock 数据范围；
12. 已接入真实数据范围；
13. 从 mock 升级真实历史数据的下一步；
14. 未解决问题和风险；
15. 真实数据库构建统计。

报告最后写明确结论：是否满足 10 条验收标准；未满足项不能隐藏。
