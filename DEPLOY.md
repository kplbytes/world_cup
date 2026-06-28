# 部署指南

## 系统要求

| 项目 | 最低要求 | 推荐配置 |
|------|---------|---------|
| 操作系统 | macOS / Linux | macOS 14+ / Ubuntu 22.04+ |
| CPU | 2 核 | 4 核+ |
| 内存 | 2 GB | 4 GB+ |
| 磁盘 | 500 MB | 1 GB+（含日志和数据增长） |
| Python | 3.12+ | 3.12+ |
| Node.js | 18+ | 20+ |

## 环境变量参考

### 必需配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_PATH` | `data/world-cup.sqlite3` | SQLite 数据库路径（相对路径基于项目根目录） |

### 数据源（可选）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FOOTBALL_DATA_API_TOKEN` | 空 | football-data.org API 令牌 |
| `API_FOOTBALL_TOKEN` | 空 | API-Football 令牌 |
| `SPORTMONKS_TOKEN` | 空 | SportMonks 令牌 |

### AI 预测（可选）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLE_AI_PREDICTION` | `true` | 启用 AI 预测 |
| `AI_RUN_MODE` | `manual` | `manual`（手动触发）/ `auto`（自动运行） |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek API 地址 |
| `AI_TEMPERATURE` | `0` | AI 采样温度 |
| `AI_TIMEOUT_SECONDS` | `30` | 请求超时（秒） |
| `AI_MAX_RETRIES` | `2` | 最大重试次数 |
| `AI_MAX_CONCURRENT_REQUESTS` | `2` | 最大并发请求数 |
| `AI_RUN_ALL_MAX_LIMIT` | `20` | 批量运行最大比赛数 |
| `AI_PROMPT_VERSION` | `worldcup-ai-v1` | 默认提示词版本 |

### 调度与模拟

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REFRESH_INTERVAL_MINUTES` | `15` | 常规数据刷新间隔 |
| `LIVE_REFRESH_INTERVAL_MINUTES` | `2` | 比赛期间刷新间隔 |
| `ENABLE_SCHEDULED_REFRESH` | `false` | 是否启用后台定时刷新 |
| `SNAPSHOT_LOCK_INTERVAL_MINUTES` | `1` | 快照锁定检查间隔 |
| `SIMULATION_ITERATIONS` | `50000` | Monte Carlo 迭代次数 |
| `SIMULATION_SEED` | `20260613` | 随机种子 |

### 安全

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ADMIN_API_KEY` | 空 | 写接口认证密钥（空=禁用认证） |
| `CORS_ALLOWED_ORIGINS` | `*` | CORS 允许来源（逗号分隔） |
| `ENABLE_NUMERICAL_ADJUSTMENTS` | `false` | 启用数值自动调整 |

### 工作流

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `APP_MODE` | `local` | 运行模式：`local` / `test` / `production` |
| `AUTO_RUN_DAILY_WORKFLOW_ON_OPEN` | `false` | 前端打开时是否自动运行每日工作流 |
| `AUTO_RUN_AI_ON_OPEN` | `false` | 前端打开时是否自动运行 AI 预测 |
| `WORKFLOW_AUTO_RUN_COOLDOWN_MINUTES` | `60` | AI 工作流冷却时间（仅影响“运行 AI 预测”按钮） |
| `WORKFLOW_DEFAULT_HOURS` | `48` | 默认前瞻小时数 |
| `WORKFLOW_DEFAULT_SINCE_HOURS` | `24` | 默认回溯小时数 |
| `WORKFLOW_DEFAULT_LIMIT` | `10` | 默认 AI 批量限制 |
| `WORKFLOW_DEFAULT_LOCK_WINDOW_HOURS` | `24` | 默认锁定窗口 |

## 生产部署清单

### 部署前

- [ ] **环境变量**：复制 `.env.example` 到 `.env`，填写所有必需配置
- [ ] **API Key**：配置至少一个 AI 提供商的 API Key
- [ ] **定时刷新策略**：明确是否需要把 `ENABLE_SCHEDULED_REFRESH` 打开；默认保持手动刷新
- [ ] **ADMIN_API_KEY**：设置强密码，启用写接口认证
- [ ] **CORS_ALLOWED_ORIGINS**：设置为实际前端域名，不使用 `*`
- [ ] **APP_MODE**：设置为 `production`
- [ ] **启动入口**：部署环境优先使用 `./scripts/start.sh` 或 systemd；根目录 `./start.sh` 仅用于本地双进程开发
- [ ] **数据库备份**：首次部署前备份 `data/world-cup.sqlite3`
- [ ] **前端构建**：`cd frontend && npm run build`
- [ ] **后端依赖**：`cd backend && .venv/bin/pip install -e .`

### 部署步骤

```bash
# 1. 获取代码
git clone <repo-url> && cd world_cup

# 2. 安装
./scripts/setup.sh

# 3. 配置
cp .env.example .env
# 编辑 .env

# 4. 验证
cd backend && .venv/bin/python -m pytest tests/ -q
cd frontend && npm test -- --run && npm run typecheck && npm run build

# 5. 启动（单端口，本地部署验证）
./scripts/start.sh
```

### 部署后验证

```bash
# 健康检查
curl http://127.0.0.1:8000/api/health

# 仪表盘可访问
curl -s http://127.0.0.1:8000/api/dashboard | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Revision: {d[\"revision\"][\"id\"]}, Groups: {len(d[\"groups\"])}')"

# 前端可访问（scripts/start.sh 由后端托管 dist）
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/
```

## 使用 systemd 部署（Linux）

### 后端服务

创建 `/etc/systemd/system/worldcup-backend.service`：

```ini
[Unit]
Description=World Cup Predictor Backend
After=network.target

[Service]
Type=simple
User=worldcup
WorkingDirectory=/opt/world_cup
ExecStart=/opt/world_cup/backend/.venv/bin/uvicorn app.main:app --app-dir /opt/world_cup/backend --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
Environment=APP_MODE=production

[Install]
WantedBy=multi-user.target
```

### 前端服务

使用 Nginx 反向代理前端静态文件和后端 API：

```nginx
server {
    listen 80;
    server_name worldcup.example.com;

    # 前端静态文件
    location / {
        root /opt/world_cup/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    # 后端 API 代理
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # API 文档
    location /docs {
        proxy_pass http://127.0.0.1:8000;
    }
    location /openapi.json {
        proxy_pass http://127.0.0.1:8000;
    }
}
```

### 启动服务

```bash
sudo systemctl daemon-reload
sudo systemctl enable worldcup-backend
sudo systemctl start worldcup-backend
sudo systemctl status worldcup-backend
```

## 监控建议

### 健康检查

定期检查 `/api/health` 端点：

```bash
# 简单检查
curl -sf http://127.0.0.1:8000/api/health > /dev/null || echo "ALERT: Backend down"

# 详细检查
curl -s http://127.0.0.1:8000/api/health | python3 -c "
import sys, json
d = json.load(sys.stdin)
if d['status'] != 'ok':
    print(f'ALERT: Status {d[\"status\"]}')
    print(f'  Database: {d[\"dependencies\"][\"database\"]}')
    print(f'  Scheduler: {d[\"dependencies\"][\"apscheduler\"]}')
"
```

### 日志监控

```bash
# 错误日志
tail -f data/logs/error.jsonl | python3 -m json.tool

# 慢请求（>1s）
tail -f data/logs/app.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line)
    if d.get('duration_ms', 0) > 1000:
        print(f'SLOW: {d[\"duration_ms\"]:.0f}ms {d.get(\"message\", \"\")}')"

# 工作流失败
grep 'workflow' data/logs/app.jsonl | grep 'failed' | python3 -m json.tool
```

### 关键指标

| 指标 | 检查方式 | 告警阈值 |
|------|---------|---------|
| 后端可用性 | `/api/health` | 非 `ok` |
| 数据库大小 | `ls -lh data/world-cup.sqlite3` | > 100 MB |
| 日志错误率 | `error.jsonl` 行数/小时 | > 10/h |
| AI 预测延迟 | AI 预测 `latency_ms` | > 30s |
| 磁盘空间 | `df -h` | > 90% |

### 数据库维护

```bash
# 检查数据库完整性
sqlite3 data/world-cup.sqlite3 "PRAGMA integrity_check;"

# 查看数据库大小
ls -lh data/world-cup.sqlite3

# 压缩数据库（停机时执行）
sqlite3 data/world-cup.sqlite3 "VACUUM;"
```

## 常见问题排查

### 后端无法启动

1. **端口占用**：`lsof -ti:8000` 查找占用进程
2. **Python 版本**：确认 `python3 --version` >= 3.12
3. **依赖缺失**：重新安装 `cd backend && .venv/bin/pip install -e .`
4. **数据库损坏**：检查 `sqlite3 data/world-cup.sqlite3 "PRAGMA integrity_check;"`

### 前端页面空白

1. **未构建**：`cd frontend && npm run build`
2. **TypeScript 错误**：`cd frontend && npm run typecheck`
3. **后端不可达**：检查后端是否运行，前端 `api.ts` 中的 API 地址

### AI 预测不工作

1. **API Key 未配置**：检查 `.env` 中 `DEEPSEEK_API_KEY`
2. **AI 未启用**：检查 `ENABLE_AI_PREDICTION=true`
3. **网络问题**：`curl https://api.deepseek.com` 测试连通性
4. **配额用尽**：检查 AI 提供商的 API 配额

### 数据不更新

1. **调度器未运行**：检查 `/api/health` 中 `apscheduler` 状态
2. **数据源不可达**：检查网络和 API 令牌
3. **后台自动刷新默认关闭是正常行为**：`ENABLE_SCHEDULED_REFRESH=false` 时只保留快照锁定和维护任务
4. **手动刷新**：通过首页按钮或 `curl -X POST http://127.0.0.1:8000/api/workflows/daily-open`

### 工作流卡住在 running

系统启动时会自动把上次异常退出遗留的 `running` workflow / step 修复为 `failed`。如果仍有异常：

1. 检查 `workflow_runs` / `workflow_steps` 是否存在长时间未结束记录
2. 检查最近一次服务是否发生异常中断
3. 重启后重新查看 `/api/workflows/status`

### 内存占用过高

1. **日志文件过大**：检查 `data/logs/` 目录，清理旧日志
2. **数据库过大**：执行 `VACUUM` 压缩
3. **限流器内存**：重启后端清理限流器内存

### 数据库锁定

SQLite WAL 模式下极少出现锁定，但如果出现：

```bash
# 检查 WAL 文件
ls -la data/world-cup.sqlite3-wal

# 强制检查点
sqlite3 data/world-cup.sqlite3 "PRAGMA wal_checkpoint(FULL);"
```
