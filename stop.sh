#!/bin/bash
# 世界杯预测系统 - 关闭脚本

echo "========================================="
echo "  世界杯预测系统 关闭中..."
echo "========================================="

# 按端口杀后端（最可靠）
BACKEND_PID=$(lsof -ti:8000 2>/dev/null || true)
if [ -n "$BACKEND_PID" ]; then
    echo "[1/2] 关闭后端 (端口 8000, PID: $BACKEND_PID)..."
    kill $BACKEND_PID 2>/dev/null || true
    echo "  后端已关闭"
else
    pkill -f "uvicorn app.main:app" 2>/dev/null && echo "[1/2] 后端已关闭" || echo "[1/2] 后端未运行"
fi

# 按端口杀前端（最可靠）
FRONTEND_PID=$(lsof -ti:5173 2>/dev/null || true)
FRONTEND_PID2=$(lsof -ti:5174 2>/dev/null || true)
if [ -n "$FRONTEND_PID" ] || [ -n "$FRONTEND_PID2" ]; then
    ALL_PIDS="$FRONTEND_PID $FRONTEND_PID2"
    echo "[2/2] 关闭前端 (端口 5173/5174, PID: $ALL_PIDS)..."
    kill $ALL_PIDS 2>/dev/null || true
    echo "  前端已关闭"
else
    pkill -f "vite" 2>/dev/null && echo "[2/2] 前端已关闭" || echo "[2/2] 前端未运行"
fi

# 清理 PID 文件
rm -f .pids/backend.pid .pids/frontend.pid

echo ""
echo "========================================="
echo "  所有服务已关闭"
echo "========================================="
