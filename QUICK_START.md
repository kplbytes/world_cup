# 快速开始指南

本指南帮助你在本地搭建并运行 2026 FIFA 世界杯预测系统。

## 前置条件

| 依赖 | 最低版本 | 验证命令 |
|------|---------|---------|
| Python | 3.12+ | `python3 --version` |
| Node.js | 18+ | `node --version` |
| npm | 随 Node.js | `npm --version` |
| Git | 任意 | `git --version` |

> macOS 用户：推荐使用 Homebrew 安装：`brew install python node`

## 一键安装（推荐）

```bash
./scripts/setup.sh
```

此脚本将自动完成：
1. 创建 Python 虚拟环境 `backend/.venv`
2. 安装后端依赖（含测试依赖）
3. 安装前端依赖
4. 构建前端生产版本

## 手动安装

### 1. 后端设置

```bash
# 创建虚拟环境
python3 -m venv backend/.venv

# 安装依赖
backend/.venv/bin/pip install -e "backend[test]"
```

### 2. 前端设置

```bash
cd frontend
npm install
npm run build
cd ..
```

### 3. 环境配置

```bash
# 复制环境变量模板
cp .env.example .env
```

**最小配置**（不配置 AI 也能运行）：

```env
# 数据库（默认值即可）
DATABASE_PATH=data/world-cup.sqlite3
```

**启用 AI 预测**（可选）：

```env
ENABLE_AI_PREDICTION=true

# DeepSeek
DEEPSEEK_API_KEY=sk-your-deepseek-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

> **获取 API Key：**
> - DeepSeek：访问 https://platform.deepseek.com 注册并创建 API Key

## 启动系统

### 方式一：使用启动脚本（推荐）

```bash
./start.sh
```

启动后访问：
- 前端：http://127.0.0.1:5173
- 后端：http://127.0.0.1:8000
- API 文档：http://127.0.0.1:8000/docs

关闭系统：
```bash
./stop.sh
```

### 方式二：手动启动

```bash
# 终端 1：启动后端
cd backend
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 终端 2：启动前端（开发模式）
cd frontend
npx vite --host 127.0.0.1 --port 5173
```

### 方式三：开发模式（热重载）

```bash
./scripts/dev.sh
```

## 验证系统运行

### 1. 健康检查

```bash
curl http://127.0.0.1:8000/api/health
```

正常响应示例：
```json
{
  "status": "ok",
  "revision_id": 1,
  "dependencies": {
    "database": "ok",
    "ai_providers": "available",
    "apscheduler": "running",
    "scheduled_refresh": "disabled",
    "snapshot_lock": "enabled",
    "maintenance": "enabled",
    "last_successful_run": "2026-06-19T08:00:00+00:00"
  }
}
```

- `status: "ok"` — 系统正常运行
- `status: "degraded"` — 数据库未初始化或调度器未运行
- `scheduled_refresh: "disabled"` — 默认不启用后台自动刷新，需手动点击工作台按钮

### 2. 查看仪表盘

```bash
curl http://127.0.0.1:8000/api/dashboard | python3 -m json.tool | head -20
```

### 3. 查看比赛列表

```bash
curl http://127.0.0.1:8000/api/matches | python3 -m json.tool | head -20
```

## 首次 AI 预测运行

确保已配置至少一个 AI API Key，然后：

### 1. 查看可用 AI 模型

```bash
curl http://127.0.0.1:8000/api/ai-models
```

### 2. 对单场比赛运行 AI 预测

```bash
# 替换 MATCH_ID 为实际比赛 ID
curl -X POST "http://127.0.0.1:8000/api/ai-predictions/run?match_id=MATCH_ID"
```

### 3. 批量运行 AI 预测

```bash
# 对未来未预测的比赛批量运行（最多 10 场）
curl -X POST "http://127.0.0.1:8000/api/ai-predictions/run-all?limit=10&only_missing=true"
```

### 4. 生成集成预测

```bash
curl -X POST "http://127.0.0.1:8000/api/ensemble/run?match_id=MATCH_ID"
```

### 5. 通过工作流运行

```bash
# 每日更新：同步赛果、重算、集成、锁定（默认不跑 AI）
curl -X POST "http://127.0.0.1:8000/api/workflows/daily-open" \
  -H "Content-Type: application/json" \
  -d '{"with_ai": false, "with_ensemble": true, "auto_lock": true}'

# AI 预测工作流：对应首页“运行 AI 预测”
curl -X POST "http://127.0.0.1:8000/api/workflows/pre-match" \
  -H "Content-Type: application/json" \
  -d '{"with_ai": true, "with_ensemble": true, "only_missing": true}'
```

> 首页刷新不会自动触发上述工作流；当前默认是纯手动点击执行。

## 常见问题

### Q: 启动后端报 `ModuleNotFoundError`

确保使用虚拟环境中的 Python：
```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Q: 前端页面空白

确保已构建前端：
```bash
cd frontend && npm run build
```

### Q: AI 预测返回 "AI prediction is not enabled"

检查 `.env` 中 `ENABLE_AI_PREDICTION=true` 且至少配置了一个 API Key。

### Q: 端口被占用

```bash
# 查找并关闭占用端口的进程
lsof -ti:8000 | xargs kill    # 后端
lsof -ti:5173 | xargs kill    # 前端
```

### Q: 数据库初始化失败

删除旧数据库重新初始化：
```bash
rm data/world-cup.sqlite3
# 重启后端，系统会自动初始化
```
