# 前端健壮性报告

## 修复内容

### 1. API fetch 超时

**问题：** 所有 `fetch` 调用无超时保护，蒙特卡洛模拟等端点可能耗时很长。

**修复：** 新增 `fetchWithTimeout` 辅助函数，默认 30 秒超时，AI POST 端点 60 秒超时。

**修改文件：** `frontend/src/api.ts`

### 2. ProbabilityBar NaN 处理

**问题：** `value` 为 NaN 时 CSS transform 变为 `scaleX(NaN)`，进度条显示空白。

**修复：** 添加 `value == null || isNaN(value)` 检查，异常值显示 "N/A"。

**修改文件：** `frontend/src/components/ProbabilityBar.tsx`

### 3. AllMatches 动态数量

**问题：** 硬编码 "72 场比赛"，淘汰赛阶段后数量会变化。

**修复：** 使用 `groups.reduce()` 动态计算比赛总数。

**修改文件：** `frontend/src/components/AllMatches.tsx`

### 4. 移除未使用依赖

**问题：** `recharts` 声明为依赖但无任何组件使用。

**修复：** 执行 `npm uninstall recharts`，减少打包体积。

### 5. React Query 重试配置

**问题：** 未区分 GET/POST 的重试策略，AI POST 调用失败可能被自动重试。

**修复：** queries 设置 `retry: 2`，mutations 设置 `retry: false`。

**修改文件：** `frontend/src/main.tsx`

## 验证结果

- typecheck: 通过
- build: 成功（296.95 kB JS）
- test: 3 passed

## 剩余风险

- 前端测试覆盖仍然不足（仅 3 个测试），新增的超时、NaN 处理等逻辑未覆盖。
- `fetchWithTimeout` 的 AbortController 在某些旧浏览器中不支持，但项目目标环境为现代浏览器。

## 对准确率链路的影响

无影响。前端变更仅涉及展示层和请求层，不影响后端计算逻辑。
