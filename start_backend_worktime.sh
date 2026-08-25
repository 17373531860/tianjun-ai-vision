#!/bin/bash
# worktime 树专用后端启动脚本 (与 start_backend.sh 的区别):
#   - 端口 8004 (本树分配位, 避免与 main/副本的 8001/8002 撞车)
#   - conda 用 ~/miniconda3 的 tianjun 环境 (本机开发环境, 非发版 runtime)
#   - 带 TIANJUN_APP_VERSION=3.46.0: lg-worktime 插件 main_version_min=3.46.0,
#     主程序 3.45.0 会被版本门禁拒载 (v3.46.0 正式发版后此行可删)

source ~/miniconda3/etc/profile.d/conda.sh
conda activate tianjun

cd "$(dirname "$0")"
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# v3.1.3: OpenCV 内置 ffmpeg 单线程解码, 避免视频回放偶发 SIGABRT
export OPENCV_FFMPEG_CAPTURE_OPTIONS="threads;1"

# lg-worktime 插件版本门禁 (见文件头注释)
export TIANJUN_APP_VERSION=3.46.0

# 插件验签密钥: dev_keys/ 存在才用, 否则回退主程序内置统一密钥
_PLUGIN_SECRET_FILE="$(cd "$(dirname "$0")" && pwd)/dev_keys/PLUGIN_SECRET.txt"
if [[ -f "$_PLUGIN_SECRET_FILE" ]]; then
  export PLUGIN_SECRET_FILE="$_PLUGIN_SECRET_FILE"
  echo "插件验签: 使用 dev_keys/PLUGIN_SECRET.txt"
else
  echo "插件验签: 回退主程序内置统一密钥"
fi

cleanup() {
    echo ""
    echo "正在停止服务..."
    lsof -ti:8004 | xargs -r kill -9 2>/dev/null
    echo "服务已停止"
}
trap cleanup EXIT INT TERM

echo "检查并清理旧进程 (端口 8004)..."
lsof -ti:8004 | xargs -r kill -9 2>/dev/null
sleep 1

echo "正在启动 worktime 后端 (端口 8004)..."
echo "API 文档地址: http://localhost:8004/docs"
echo "按 Ctrl+C 停止服务"
echo ""

# 不开 --reload: 本树常跑真模型推理, 热重载会在改文件时杀掉推理中的
# MPS/CUDA 进程; 需要热重载调后端时手动加 --reload
python -u -m uvicorn backend.main:app --host 127.0.0.1 --port 8004 --log-level warning
