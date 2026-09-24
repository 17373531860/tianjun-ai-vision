#!/usr/bin/env bash
# =============================================================
# TianJun Fleet Hub 一键安装 (Ubuntu 20.04+ / Debian 11+)
#
# 用法 (在仓库检出目录, 或解压的发布包目录, sudo 运行):
#   sudo bash hub/deploy/install.sh
#
# 做什么:
#   1. 校验 python3 (>=3.10); 前端 dist 缺失时校验 node (>=18) 并构建
#   2. 建系统用户 tianjun-hub + 目录 /opt/tianjun-hub, /var/lib/tianjun-hub
#   3. 拷贝 hub/ 代码到 /opt/tianjun-hub/app/hub, venv 装依赖
#   4. 安装 /etc/tianjun-hub.env (已存在不覆盖) + systemd unit
#   5. 启动并健康探活 GET /health
#
# 幂等: 重复运行 = 升级 (代码覆盖 + 依赖更新 + 重启; 数据目录不动)。
# =============================================================
set -euo pipefail

APP_DIR=/opt/tianjun-hub
DATA_DIR=/var/lib/tianjun-hub
SVC_USER=tianjun-hub
PORT="${HUB_PORT:-9100}"

# 仓库根 = 本脚本上溯两级 (hub/deploy/install.sh)
SRC_HUB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say()  { echo -e "\033[1;36m[hub-install]\033[0m $*"; }
fail() { echo -e "\033[1;31m[hub-install] 失败:\033[0m $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || fail "请用 sudo 运行"
[ -f "$SRC_HUB/requirements.txt" ] || fail "找不到 hub/requirements.txt (在仓库目录运行)"

# ---- 1. python 版本 ----
PY=python3
$PY -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || fail "需要 python3 >= 3.10 (当前: $($PY --version 2>&1))"
$PY -m venv --help >/dev/null 2>&1 || fail "缺 python3-venv (apt install python3-venv)"

# ---- 2. 前端 dist (仓库不入库, 缺就地构建) ----
if [ ! -f "$SRC_HUB/frontend/dist/index.html" ]; then
  say "前端 dist 缺失, 尝试本机构建 (需要 node >= 18) ..."
  command -v npm >/dev/null 2>&1 || fail "缺 node/npm: 装 node 18+ 后重跑, 或在有 node 的机器构建好 hub/frontend/dist 再拷来"
  (cd "$SRC_HUB/frontend" && npm ci && npm run build) || fail "前端构建失败"
fi
say "前端 dist 就绪"

# ---- 3. 用户 + 目录 + 代码 ----
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" \
    --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$APP_DIR/app" "$DATA_DIR"

say "同步代码到 $APP_DIR/app/hub"
rm -rf "$APP_DIR/app/hub"
mkdir -p "$APP_DIR/app/hub"
# 只带运行所需: backend + 前端 dist + requirements (不带 node_modules/源码)
cp -R "$SRC_HUB/backend" "$APP_DIR/app/hub/backend"
mkdir -p "$APP_DIR/app/hub/frontend"
cp -R "$SRC_HUB/frontend/dist" "$APP_DIR/app/hub/frontend/dist"
cp "$SRC_HUB/requirements.txt" "$SRC_HUB/README.md" "$APP_DIR/app/hub/"
touch "$APP_DIR/app/hub/__init__.py"
find "$APP_DIR/app" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

# ---- 4. venv + 依赖 ----
[ -x "$APP_DIR/venv/bin/pip" ] || $PY -m venv "$APP_DIR/venv"
say "安装 Python 依赖"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/app/hub/requirements.txt"

chown -R "$SVC_USER:$SVC_USER" "$DATA_DIR"
chown -R root:root "$APP_DIR"

# ---- 5. env + systemd ----
if [ ! -f /etc/tianjun-hub.env ]; then
  cp "$SRC_HUB/deploy/hub.env.example" /etc/tianjun-hub.env
  chmod 600 /etc/tianjun-hub.env
  say "已生成 /etc/tianjun-hub.env —— ⚠️ 请编辑 HUB_ADMIN_PASSWORD"
fi
cp "$SRC_HUB/deploy/tianjun-hub.service" /etc/systemd/system/tianjun-hub.service
systemctl daemon-reload
systemctl enable tianjun-hub >/dev/null
systemctl restart tianjun-hub

# ---- 6. 健康探活 ----
say "等待服务就绪 ..."
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    say "✅ 部署完成: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
    say "   登录: admin + /etc/tianjun-hub.env 里的 HUB_ADMIN_PASSWORD"
    say "   日志: journalctl -u tianjun-hub -f"
    say "   备份: bash hub/deploy/backup.sh (建议进 crontab)"
    exit 0
  fi
  sleep 1
done
journalctl -u tianjun-hub -n 20 --no-pager || true
fail "服务 30s 未就绪, 请看上方日志"
