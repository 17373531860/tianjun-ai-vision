#!/usr/bin/env bash
# ==================== CI 盯盘 + 收尾切回 private（统一参数化版） ====================
#
# 发版铁律：CI 用 free runner 仅 public 仓可用，构建期间临时 public，
# 结束后无论成功失败都必须切回 private。本脚本取代历史上散落根目录的
# _ci_watch_v32xx.sh / _ci_guard_*.sh / _ci_watch_private.sh 一次性变体
# （以 v3.29 版为基线合并）。
#
# 用法:
#   scripts/ci/watch_and_private.sh [-r <tag_run_id>] [-t <超时小时>] [-i <轮询秒>] [-l <日志文件>] [-n]
#
#   -r  tag 构建的 run id（可选；不传则自动取最近一次 tag push 触发的 run）
#   -t  最长守护小时数，默认 6
#   -i  轮询间隔秒，默认 300
#   -l  日志文件，默认 _ci_watch.log（已被 .gitignore 的 *.log 覆盖）
#   -n  no-private：只盯盘不切 private（调试用）
#
# 典型（update-release skill 第 8 步）:
#   nohup scripts/ci/watch_and_private.sh -r <run_id> >/dev/null 2>&1 &
set -u
cd "$(dirname "$0")/../.." || exit 1

TAG_RUN=""
MAX_HOURS=6
INTERVAL=300
LOG="_ci_watch.log"
DO_PRIVATE=1

while getopts "r:t:i:l:n" opt; do
  case "$opt" in
    r) TAG_RUN="$OPTARG" ;;
    t) MAX_HOURS="$OPTARG" ;;
    i) INTERVAL="$OPTARG" ;;
    l) LOG="$OPTARG" ;;
    n) DO_PRIVATE=0 ;;
    *) echo "用法: $0 [-r run_id] [-t 小时] [-i 秒] [-l 日志] [-n]" >&2; exit 2 ;;
  esac
done

MAX_SECONDS=$((MAX_HOURS * 3600))
START=$(date +%s)

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

# 未指定 run id 时自动抓最近的 tag 触发 run
if [ -z "$TAG_RUN" ]; then
  TAG_RUN=$(gh run list --limit 20 --json databaseId,headBranch,event \
    -q '[.[] | select(.event=="push" and (.headBranch | startswith("v")))][0].databaseId' 2>>"$LOG" || true)
  log "未指定 run id, 自动选取 tag_run=${TAG_RUN:-<未找到>}"
fi

log "watcher 启动 tag_run=${TAG_RUN:-无} max=${MAX_HOURS}h interval=${INTERVAL}s private=${DO_PRIVATE}"

# ── 轮询直到本批 run 全部结束（或超时兜底） ──
while true; do
  NOW=$(date +%s)
  ELAPSED=$((NOW - START))
  if [ "$ELAPSED" -ge "$MAX_SECONDS" ]; then
    log "超过 ${MAX_HOURS}h 上限, 强制进入收尾"
    break
  fi
  RUNNING=$(gh run list --limit 12 --json status \
    -q '[.[] | select(.status=="in_progress" or .status=="queued")] | length' 2>>"$LOG")
  if [ -n "$TAG_RUN" ]; then
    TAGSTATUS=$(gh run view "$TAG_RUN" --json status,conclusion \
      -q '.status+"/"+(.conclusion // "-")' 2>>"$LOG")
  else
    TAGSTATUS="-"
  fi
  log "elapsed=${ELAPSED}s running=${RUNNING:-?} tag_build=${TAGSTATUS:-?}"
  if [ "${RUNNING:-1}" = "0" ]; then
    log "全部 run 结束"
    break
  fi
  sleep "$INTERVAL"
done

# ── 切回 private（重试 3 次，发版铁律：无论构建成败都要切） ──
if [ "$DO_PRIVATE" = "1" ]; then
  for i in 1 2 3; do
    if gh repo edit --visibility private --accept-visibility-change-consequences >>"$LOG" 2>&1; then
      log "✅ 已切回 private"
      break
    fi
    log "切 private 失败, 第 $i 次重试"
    sleep 20
  done
else
  log "跳过切 private (-n)"
fi

# ── 最终结论 ──
if [ -n "$TAG_RUN" ]; then
  gh run view "$TAG_RUN" --json status,conclusion \
    -q '"tag_build: "+.status+" / "+(.conclusion // "-")' >> "$LOG" 2>&1
fi
log "watcher 退出"
