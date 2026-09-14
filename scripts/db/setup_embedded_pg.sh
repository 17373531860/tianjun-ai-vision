#!/bin/sh
# 天军 AI 视觉 - 嵌入式 PostgreSQL 一次性初始化 (Linux / macOS)
#
# 语义对齐 Windows 安装器的可选 PG 组件 (electron/build/installer.iss 的
# SetupEmbeddedPostgres): initdb (用户 tianjun / scram-sha-256 / 每机随机密码)
# -> 端口钉 5433 只听 127.0.0.1 -> 建业务库 tianjun -> 起停验证 -> 写 db_config.json。
# 差异: zonky 便携包没有 createdb/psql, 建库走 postgres --single 单用户模式;
#       Windows 注册系统服务开机自启, Linux/macOS 改为应用代管
#       (electron/backend-manager.js 启动后端前 pg_ctl start, 应用关闭时 stop)。
#
# 失败不破坏现状: 任一步失败都不写 db_config.json, 应用继续用 SQLite。
# 已初始化过 (pgdata/PG_VERSION 存在) 直接跳过, 不动数据不改密码 (升级安全)。
#
# 用法:
#   sh setup_embedded_pg.sh                                      # 打包环境自动定位
#   sh setup_embedded_pg.sh --pg-root <目录> --data-root <目录>   # 显式指定
#
# 旧 SQLite 数据迁移: 初始化后用同目录的 sqlite_to_pg.py (见其 --help)。
set -u

PG_ROOT=""
DATA_ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pg-root)   PG_ROOT="$2"; shift 2 ;;
    --data-root) DATA_ROOT="$2"; shift 2 ;;
    *) echo "未知参数: $1 (支持 --pg-root / --data-root)"; exit 2 ;;
  esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# 打包布局: resources/scripts/db/ -> resources/pg-portable
[ -n "$PG_ROOT" ] || PG_ROOT="$SCRIPT_DIR/../../pg-portable"
if [ ! -x "$PG_ROOT/bin/initdb" ]; then
  echo "[ERROR] 找不到便携 PostgreSQL: $PG_ROOT/bin/initdb"
  echo "        本安装包可能未内置 PG 组件, 或需用 --pg-root 指定 pg-portable 目录"
  exit 1
fi
PG_ROOT=$(CDPATH= cd -- "$PG_ROOT" && pwd)

if [ -z "$DATA_ROOT" ]; then
  case "$(uname -s)" in
    Darwin) DATA_ROOT="$HOME/Library/Application Support/tianjun-ai-vision" ;;
    *)      DATA_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/tianjun-ai-vision" ;;
  esac
fi
PGDATA="$DATA_ROOT/pgdata"
CFG="$DATA_ROOT/db_config.json"
PORT=5433

if [ -f "$PGDATA/PG_VERSION" ]; then
  echo "[OK] 已初始化过 ($PGDATA), 不动数据不改密码; 启停由应用代管, 无需重复执行"
  exit 0
fi

mkdir -p "$DATA_ROOT"

# 每机唯一密码: 随机字母 + 时间戳 (同 Windows 侧策略)。
# PG 只监听 127.0.0.1, 密码仅防本机误连, 不承担网络面安全。
PWD_RAND="$(LC_ALL=C tr -dc 'a-z' < /dev/urandom | dd bs=1 count=8 2>/dev/null)$(date +%H%M%S)"
PWFILE=$(mktemp)
printf '%s' "$PWD_RAND" > "$PWFILE"

echo "[1/4] initdb -> $PGDATA"
if ! "$PG_ROOT/bin/initdb" -D "$PGDATA" -U tianjun -E UTF8 \
    --auth=scram-sha-256 --pwfile="$PWFILE" > /dev/null; then
  rm -f "$PWFILE"
  echo "[ERROR] initdb 失败, 不写 db_config.json, 应用继续用 SQLite"
  exit 1
fi
rm -f "$PWFILE"
printf "\nport = %s\nlisten_addresses = '127.0.0.1'\n" "$PORT" >> "$PGDATA/postgresql.conf"

echo "[2/4] 建业务库 tianjun (单用户模式, 便携包无 createdb)"
if ! echo "CREATE DATABASE tianjun;" | \
    "$PG_ROOT/bin/postgres" --single -D "$PGDATA" postgres > /dev/null; then
  echo "[ERROR] 建库失败, 不写 db_config.json, 应用继续用 SQLite"
  exit 1
fi

echo "[3/4] 起停验证 (pg_ctl start/stop)"
if ! "$PG_ROOT/bin/pg_ctl" -D "$PGDATA" -l "$PGDATA/embedded_pg.log" -w -t 60 start > /dev/null; then
  echo "[ERROR] PG 启动验证失败 (详见 $PGDATA/embedded_pg.log), 不写 db_config.json"
  exit 1
fi
"$PG_ROOT/bin/pg_ctl" -D "$PGDATA" -w -t 30 -m fast stop > /dev/null

echo "[4/4] 写 $CFG"
cat > "$CFG" << EOF
{
  "database_url": "postgresql+psycopg2://tianjun:$PWD_RAND@127.0.0.1:$PORT/tianjun",
  "embedded_pg": {
    "pgdata": "$PGDATA",
    "port": $PORT,
    "bin_dir": "$PG_ROOT/bin"
  }
}
EOF

echo "[DONE] 初始化完成。下次启动应用即使用嵌入式 PostgreSQL"
echo "       数据目录: $PGDATA"
echo "       旧 SQLite 数据迁移: python $SCRIPT_DIR/sqlite_to_pg.py --help"
