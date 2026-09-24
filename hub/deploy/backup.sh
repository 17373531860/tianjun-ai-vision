#!/usr/bin/env bash
# =============================================================
# TianJun Fleet Hub 数据备份 (在线一致性备份, 服务不用停)
#
# 用法:
#   bash hub/deploy/backup.sh [备份存放目录]     # 默认 /var/backups/tianjun-hub
#
# 备份内容 = 整个数据目录 (HUB_DATA_DIR, 默认 /var/lib/tianjun-hub):
#   - hub.db        SQLite 库 (用 sqlite3 backup API 拷快照, WAL 下也一致)
#   - fernet.key 等 API Key 加密密钥 (丢了密钥 = 全部边缘要重新纳管)
#
# 轮转: 保留最近 14 份, 更老的自动删。
# crontab 示例 (每天 03:00):
#   0 3 * * * /usr/bin/bash /opt/tianjun-hub/app/hub/deploy/backup.sh
# =============================================================
set -euo pipefail

DATA_DIR="${HUB_DATA_DIR:-/var/lib/tianjun-hub}"
OUT_DIR="${1:-/var/backups/tianjun-hub}"
KEEP=14
PY="${HUB_VENV_PY:-/opt/tianjun-hub/venv/bin/python}"
[ -x "$PY" ] || PY=python3

[ -d "$DATA_DIR" ] || { echo "数据目录不存在: $DATA_DIR" >&2; exit 1; }
mkdir -p "$OUT_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. SQLite 在线一致性快照 (python 标准库 backup API, 不锁写不撕裂)
if [ -f "$DATA_DIR/hub.db" ]; then
  "$PY" - "$DATA_DIR/hub.db" "$WORK/hub.db" <<'EOF'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
EOF
fi

# 2. 数据目录其余文件 (密钥等; 排除 db 本体与 wal/shm —— 快照已含其内容)
(cd "$DATA_DIR" && find . -maxdepth 1 -type f \
    ! -name 'hub.db' ! -name '*.db-wal' ! -name '*.db-shm' \
    -exec cp {} "$WORK/" \;)

tar -czf "$OUT_DIR/hub_backup_$STAMP.tar.gz" -C "$WORK" .
echo "已备份: $OUT_DIR/hub_backup_$STAMP.tar.gz ($(du -h "$OUT_DIR/hub_backup_$STAMP.tar.gz" | cut -f1))"

# 3. 轮转
ls -1t "$OUT_DIR"/hub_backup_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) \
    | xargs -r rm -f

# 恢复方法 (README 也有):
#   systemctl stop tianjun-hub
#   rm -rf /var/lib/tianjun-hub/* && tar -xzf 备份包 -C /var/lib/tianjun-hub
#   chown -R tianjun-hub:tianjun-hub /var/lib/tianjun-hub
#   systemctl start tianjun-hub
