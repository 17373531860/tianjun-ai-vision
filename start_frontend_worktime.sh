#!/bin/bash
# worktime 树专用前端启动脚本: 端口 6004 (本树分配位)。
# baseURL 由 frontend/.env.development.local 钉到 http://localhost:8004/api/v1,
# 与 start_backend_worktime.sh 对位。

cd "$(dirname "$0")/frontend"

# Node 版本检查 + 自动切换 (Vite 7 要求 Node >= 20.19), 与 start_frontend.sh 同款
ensure_node_version() {
    local current_major
    current_major=$(node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')
    if [ -n "$current_major" ] && [ "$current_major" -ge 20 ] 2>/dev/null; then
        return 0
    fi
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [ -s "$NVM_DIR/nvm.sh" ]; then
        # shellcheck disable=SC1091
        . "$NVM_DIR/nvm.sh" >/dev/null 2>&1
        local target
        target=$(nvm ls --no-colors 2>/dev/null \
                 | grep -oE '^[[:space:]>*-]*v(20|22|24)\.[0-9]+\.[0-9]+' \
                 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' \
                 | sort -V | tail -1)
        if [ -n "$target" ]; then
            nvm use --delete-prefix "$target" --silent >/dev/null 2>&1 || \
                nvm use "$target" >/dev/null 2>&1 || true
            local new_major
            new_major=$(node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')
            if [ -n "$new_major" ] && [ "$new_major" -ge 20 ] 2>/dev/null; then
                echo "已通过 nvm 切到 node $(node -v)"
                return 0
            fi
            echo "警告: nvm use $target 后 node 仍是 $(node -v), 请手动切换后重试"
            return 1
        else
            echo "警告: nvm 已装但无 v20/22/24, 执行 'nvm install 20' 后再试"
            return 1
        fi
    else
        echo "警告: node $(node -v) 不满足 Vite 7 要求 (>= v20.19), 且未检测到 nvm"
        return 1
    fi
}
ensure_node_version || exit 1

# 确认 baseURL 指向本树后端, 防止 axios 打到别的 worktree 污染主 DB
if ! grep -q "8004" .env.development.local 2>/dev/null; then
    echo "警告: frontend/.env.development.local 未钉 8004, 前端可能连错后端!"
    echo "  期望: VITE_API_BASE_URL=http://localhost:8004/api/v1"
fi

cleanup() {
    echo ""
    echo "正在停止前端服务..."
    lsof -ti:6004 | xargs -r kill -9 2>/dev/null
    echo "前端服务已停止"
}
trap cleanup EXIT INT TERM

echo "检查并清理旧进程 (端口 6004)..."
lsof -ti:6004 | xargs -r kill -9 2>/dev/null
sleep 1

echo "正在启动 worktime 前端 (端口 6004)..."
echo "访问地址: http://localhost:6004"
echo "按 Ctrl+C 停止服务"

npx vite --port 6004 --strictPort
