#!/bin/bash
# 启动后端服务脚本

# 激活与客户现场/发版一致的后端运行环境
# 自动探测 conda 安装位置（anaconda3 / miniconda3，用户目录或 /opt）
for _conda_sh in ~/anaconda3/etc/profile.d/conda.sh \
                 ~/miniconda3/etc/profile.d/conda.sh \
                 /opt/anaconda3/etc/profile.d/conda.sh \
                 /opt/miniconda3/etc/profile.d/conda.sh; do
    if [[ -f "$_conda_sh" ]]; then
        source "$_conda_sh"
        break
    fi
done
# 环境名回退：优先 tianjun-runtime，没有则用 tianjun
conda activate tianjun-runtime 2>/dev/null || conda activate tianjun || {
    echo "错误: 未找到 tianjun-runtime / tianjun conda 环境" >&2
    exit 1
}

# 切换到后端目录
cd "$(dirname "$0")"

# 设置 Python 路径
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# v3.1.3: 强制 OpenCV 内置 ffmpeg 单线程解码,避免视频文件回放时偶发的
# "Assertion fctx->async_lock failed at libavcodec/pthread_frame.c:175" SIGABRT
# 触发后会把 uvicorn worker 子进程整个 abort 掉,--reload 模式下不会自动复活
export OPENCV_FFMPEG_CAPTURE_OPTIONS="threads;1"

# 插件验签密钥：本地开发优先用 dev_keys/，没有就回退主程序内置统一密钥（与正式安装包一致）。
# v3.15.4: 改成"文件存在才 export"。之前无条件 export 一个被 gitignore、常常不存在的
# 路径，验签器读文件时抛 FileNotFoundError 导致上传插件崩（现已双保险：验签器也加了
# is_file 容错，这里再从源头不污染环境变量）。
_PLUGIN_SECRET_FILE="$(cd "$(dirname "$0")" && pwd)/dev_keys/PLUGIN_SECRET.txt"
if [[ -f "$_PLUGIN_SECRET_FILE" ]]; then
  export PLUGIN_SECRET_FILE="$_PLUGIN_SECRET_FILE"
  echo "插件验签: 使用 dev_keys/PLUGIN_SECRET.txt"
else
  echo "插件验签: 未找到 dev_keys/PLUGIN_SECRET.txt，回退主程序内置统一密钥（与正式包一致，可正常上传插件）"
fi

# 清理旧进程 (启动前自动杀死占用端口的进程)
cleanup() {
    echo ""
    echo "正在停止服务..."
    pkill -f "uvicorn backend.main:app" 2>/dev/null
    # 额外检查端口占用并杀死（不用 xargs -r：macOS BSD xargs 不支持该选项）
    for _pid in $(lsof -ti:8001); do kill -9 "$_pid" 2>/dev/null; done
    echo "服务已停止"
}

# 捕获退出信号，确保进程被清理
trap cleanup EXIT INT TERM

# 启动前先清理可能存在的旧进程
echo "检查并清理旧进程..."
pkill -f "uvicorn backend.main:app" 2>/dev/null
for _pid in $(lsof -ti:8001); do kill -9 "$_pid" 2>/dev/null; done
sleep 1

# 启动 FastAPI 服务
echo "正在启动 Tianjun Machine Vision 后端服务..."
echo "API 文档地址: http://localhost:8001/docs"
echo "按 Ctrl+C 停止服务"
echo ""

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload --log-level warning
