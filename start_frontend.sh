#!/bin/bash
# 启动前端服务脚本

cd "$(dirname "$0")/frontend"

# Node 版本检查 + 自动切换 (Vite 7 要求 Node >= 20.19)
# 装了 nvm 且当前 node < 20 就自动切到 nvm 里 >= 20 的最高版本.
# 没装 nvm 的客户机/开发机不受影响, 仍然用系统 node.
ensure_node_version() {
    local current_major
    current_major=$(node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')
    if [ -n "$current_major" ] && [ "$current_major" -ge 20 ] 2>/dev/null; then
        return 0
    fi

    # nvm 不在子 shell 默认 PATH 里, 必须 source 一次
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [ -s "$NVM_DIR/nvm.sh" ]; then
        # shellcheck disable=SC1091
        . "$NVM_DIR/nvm.sh" >/dev/null 2>&1
        # 只匹真实已装版本: nvm ls 已装行格式是 "<空白>v20.19.5" 或 "->     v24.12.0",
        # alias 行格式是 "lts/krypton -> v24.15.0" — 后者不能被 nvm use, 必须排除.
        # 策略: 只取行首正则 ^[ \t>->]*v[0-9]+\.[0-9]+\.[0-9]+ 的那批.
        local target
        target=$(nvm ls --no-colors 2>/dev/null \
                 | grep -oE '^[[:space:]>*-]*v(20|22|24)\.[0-9]+\.[0-9]+' \
                 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' \
                 | sort -V | tail -1)
        if [ -n "$target" ]; then
            # nvm use 在某些 .npmrc 配置下退出码非零, 但实际切换是成功的.
            # 不看退出码, 而是切换后重新校验 node 版本.
            nvm use --delete-prefix "$target" --silent >/dev/null 2>&1 || \
                nvm use "$target" >/dev/null 2>&1 || true
            local new_major
            new_major=$(node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')
            if [ -n "$new_major" ] && [ "$new_major" -ge 20 ] 2>/dev/null; then
                echo "已通过 nvm 切到 node $(node -v) (原 v${current_major:-?} 不满足 Vite 7 要求)"
                return 0
            fi
            echo "警告: nvm use $target 后 node 仍是 $(node -v), 请手动 'nvm use $target' 后重试"
            return 1
        else
            echo "警告: nvm 已装, 但找不到 v20/22/24 已安装版本. 执行 'nvm install 20' 后再试"
            return 1
        fi
    else
        echo "警告: 当前 node $(node -v) 不满足 Vite 7 要求 (>= v20.19), 且未检测到 nvm."
        echo "请升级 node, 或 'curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash' 装 nvm 后重试"
        return 1
    fi
}

ensure_node_version || exit 1

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
