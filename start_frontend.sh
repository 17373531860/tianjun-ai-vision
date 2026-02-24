#!/bin/bash
# 启动前端服务脚本

cd "$(dirname "$0")/frontend"

# 清理旧进程
cleanup() {
    echo ""
    echo "正在停止前端服务..."
    lsof -ti:6001 | xargs -r kill -9 2>/dev/null
    echo "前端服务已停止"
}

trap cleanup EXIT INT TERM

echo "检查并清理旧进程..."
lsof -ti:6001 | xargs -r kill -9 2>/dev/null
sleep 1

echo "正在启动前端开发服务器..."
echo "访问地址: http://localhost:6001"
echo "按 Ctrl+C 停止服务"

npm run dev
