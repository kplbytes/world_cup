#!/bin/bash
# 世界杯预测系统 - 启动脚本

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
PID_DIR="$PROJECT_DIR/.pids"

mkdir -p "$PID_DIR"

echo "========================================="
echo "  世界杯预测系统 启动中..."
echo "========================================="

# 先关闭已有进程
echo "[0/2] 清理已有进程..."

# 按端口杀后端（最可靠的方式）
BACKEND_PID=$(lsof -ti:8000 2>/dev/null || true)
if [ -n "$BACKEND_PID" ]; then
    echo "  关闭占用 8000 端口的进程 (PID: $BACKEND_PID)..."
    kill $BACKEND_PID 2>/dev/null || true
fi
pkill -f "uvicorn app.main:app" 2>/dev/null || true

# 按端口杀前端（最可靠的方式）
FRONTEND_PID=$(lsof -ti:5173 2>/dev/null || true)
if [ -n "$FRONTEND_PID" ]; then
    echo "  关闭占用 5173 端口的进程 (PID: $FRONTEND_PID)..."
    kill $FRONTEND_PID 2>/dev/null || true
fi
pkill -f "vite" 2>/dev/null || true

# 清理 PID 文件
rm -f "$PID_DIR/backend.pid" "$PID_DIR/frontend.pid"

# 等待端口释放
sleep 2

# 启动后端
echo "[1/2] 启动后端..."
cd "$BACKEND_DIR"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
echo $BACKEND_PID > "$PID_DIR/backend.pid"
echo "  后端 PID: $BACKEND_PID (http://127.0.0.1:8000)"

# 等待后端启动
sleep 2

# 启动前端
echo "[2/2] 启动前端..."
cd "$FRONTEND_DIR"
npx vite --host 127.0.0.1 --port 5173 &
FRONTEND_PID=$!
echo $FRONTEND_PID > "$PID_DIR/frontend.pid"
echo "  前端 PID: $FRONTEND_PID (http://127.0.0.1:5173)"

echo ""
echo "========================================="
echo "  启动完成！"
echo "  前端: http://127.0.0.1:5173"
echo "  后端: http://127.0.0.1:8000"
echo "  API文档: http://127.0.0.1:8000/docs"
echo ""
echo "  关闭请运行: ./stop.sh"
echo "========================================="

# 保持脚本运行，显示日志
echo ""
echo "按 Ctrl+C 停止所有服务..."
trap "echo '正在关闭...'; cd '$PROJECT_DIR' && ./stop.sh; exit 0" SIGINT SIGTERM
wait
