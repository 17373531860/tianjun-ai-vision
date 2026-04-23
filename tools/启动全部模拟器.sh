#!/usr/bin/env bash
# 启动虚拟扫码器（Linux 版）
# 扫码器: 0.0.0.0:55256
# --auto-start，启动即监听。

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

PY="${PYTHON:-python3}"

if ! "$PY" -c "import PyQt5" 2>/dev/null; then
    echo "[依赖缺失] 安装 PyQt5..."
    "$PY" -m pip install --user -q PyQt5
fi

LOG_DIR="/tmp/tianjun-sim-logs"
mkdir -p "$LOG_DIR"

echo "启动虚拟扫码器（日志: $LOG_DIR/scanner.log）"
nohup "$PY" "$DIR/virtual_scanner.py" --port 55256 --name 扫码器 --auto-start \
    > "$LOG_DIR/scanner.log" 2>&1 &
PID=$!
echo "  PID=$PID  TCP 0.0.0.0:55256"

sleep 2
ss -tlnp 2>/dev/null | grep ':55256 ' || \
    netstat -tlnp 2>/dev/null | grep ':55256 ' || \
    echo "  (无法用 ss/netstat 查询，可手动验证)"
echo
echo "停止: kill $PID"
