#!/bin/bash
# 启动前端服务脚本

cd "$(dirname "$0")/frontend"

echo "正在启动前端开发服务器..."
echo "访问地址: http://localhost:6001"
echo "按 Ctrl+C 停止服务"

npm run dev
