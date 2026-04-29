#!/bin/bash
# 启动后端服务脚本

# 激活与客户现场/发版一致的后端运行环境
source ~/anaconda3/etc/profile.d/conda.sh
conda activate tianjun-runtime

# 切换到后端目录
cd "$(dirname "$0")"

# 设置 Python 路径
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# v3.1.3: 强制 OpenCV 内置 ffmpeg 单线程解码,避免视频文件回放时偶发的
# "Assertion fctx->async_lock failed at libavcodec/pthread_frame.c:175" SIGABRT
# 触发后会把 uvicorn worker 子进程整个 abort 掉,--reload 模式下不会自动复活
export OPENCV_FFMPEG_CAPTURE_OPTIONS="threads;1"

# 清理旧进程 (启动前自动杀死占用端口的进程)
cleanup() {
    echo ""
    echo "正在停止服务..."
    pkill -f "uvicorn backend.main:app" 2>/dev/null
    # 额外检查端口占用并杀死
    lsof -ti:8001 | xargs -r kill -9 2>/dev/null
    echo "服务已停止"
}

# 捕获退出信号，确保进程被清理
trap cleanup EXIT INT TERM

# 启动前先清理可能存在的旧进程
echo "检查并清理旧进程..."
pkill -f "uvicorn backend.main:app" 2>/dev/null
lsof -ti:8001 | xargs -r kill -9 2>/dev/null
sleep 1

# 启动 FastAPI 服务
echo "正在启动 Tianjun Machine Vision 后端服务..."
echo "API 文档地址: http://localhost:8001/docs"
echo "按 Ctrl+C 停止服务"
echo ""

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload --log-level warning
