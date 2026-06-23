# -*- coding: utf-8 -*-
"""调试中心 — 按类别开关的运行时调试日志总线.

设计原则:
  - 默认全关, 内存态, 重启归零 — 客户机不可能被误留调试开销
  - 关闭时 dbg() 只做一次 dict 查询即返回 (热路径零字符串拼接由调用方
    `if debug_center.is_on(cat):` 守门保证)
  - 开启时: 写环形缓冲 (deque maxlen=3000, 自增 seq) + 打 stdout (进
    Electron backend.log) + 写 DATA_DIR/logs/backend-debug.log (UTF-8 滚动)
  - 前端埋点经 POST /debug/client-log 也汇入同一缓冲, 调试面板单一日志流

消费方:
  - GET  /api/v1/debug/flags  / PUT 同路径 — 调试设置页开关矩阵
  - GET  /api/v1/debug/logs?since_seq=     — 调试设置页 1s 增量轮询
"""
import os
import threading
from collections import deque
from datetime import datetime

# ==================== 类别目录 (后端) ====================
# key 命名: backend.<module>; label/group 供调试设置页渲染分组卡片
BACKEND_CATEGORIES = {
    "backend.detection":  {"label": "检测推理 (模型加载/推理循环/卡顿/逐帧FPS)", "group": "检测核心"},
    "backend.source":     {"label": "视频源生命周期 (启动/停止/暂停/待机/恢复)", "group": "检测核心"},
    "backend.capture":    {"label": "采集循环 (读帧耗时/帧序号推进/各锁耗时/丢帧)", "group": "检测核心"},
    "backend.stream":     {"label": "视频推流 (MJPEG 连接/让位/推帧FPS/编码耗时/画面停滞)", "group": "检测核心"},
    "backend.settlement": {"label": "结算状态机 (周期判定/OK-NG 结算)", "group": "检测核心"},
    "backend.per_item":   {"label": "逐件覆盖 (周期锁定/覆盖进度/漏件NG原因)", "group": "检测核心"},
    "backend.session":    {"label": "数据记录 (Session/Cycle/Step 写库)", "group": "检测核心"},
    "backend.hik":        {"label": "海康 SDK (NVR/工业相机详细日志)", "group": "检测核心"},
    "backend.mes":        {"label": "MES Hook (周期联动/工单/工件/缺陷)", "group": "MES"},
    "backend.scanner":    {"label": "扫码器 (连接/收码/注入/LON-WMax)", "group": "MES"},
    "backend.gateway":    {"label": "MES 推送网关 (payload/重试/失败)", "group": "MES"},
    "backend.pull":       {"label": "工单拉取 (主动查询/HTTP/解析/入库)", "group": "MES"},
    "backend.packaging":  {"label": "包装箱结算 (扫码开单/算箱/滑块结算/尾箱/切项目)", "group": "MES"},
    "backend.cluster":    {"label": "集群主从 (心跳/box 聚齐/上报)", "group": "MES"},
    "backend.alarm":      {"label": "报警系统 (串口指令/灯塔/蜂鸣)", "group": "系统"},
    "backend.export":     {"label": "自定义导出 (模板渲染/落盘/实时规则)", "group": "系统"},
    "backend.api":        {"label": "API 异常 (端点报错统一记录)", "group": "系统"},
}

_BUFFER_MAX = 3000
_FILE_MAX = 10 * 1024 * 1024  # 10MB 滚动

_lock = threading.Lock()
_flags: dict = {k: False for k in BACKEND_CATEGORIES}
_buffer: deque = deque(maxlen=_BUFFER_MAX)
_seq = 0
_file = None
_file_size = 0


def _log_path() -> str:
    from backend.core.config import DATA_DIR
    d = os.path.join(DATA_DIR, "logs")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "backend-debug.log")


def _write_file(line: str) -> None:
    """UTF-8 滚动写盘; 任何 IO 失败静默, 调试设施不能反噬主流程"""
    global _file, _file_size
    try:
        if _file is None:
            p = _log_path()
            try:
                if os.path.exists(p) and os.path.getsize(p) > _FILE_MAX:
                    old = p.replace(".log", ".1.log")
                    if os.path.exists(old):
                        os.unlink(old)
                    os.rename(p, old)
            except OSError:
                pass
            _file = open(p, "a", encoding="utf-8")
            _file_size = _file.tell()
        _file.write(line + "\n")
        _file.flush()
        _file_size += len(line) + 1
        if _file_size > _FILE_MAX:
            _file.close()
            _file = None
    except Exception:
        _file = None


# ==================== 对外 API ====================

def is_on(category: str) -> bool:
    """热路径守门: 调用方先查这个再拼日志字符串"""
    return _flags.get(category, False)


def dbg(category: str, action: str, detail: str = "", source: str = "backend") -> None:
    """记一条调试日志. 类别未开启时一次 dict 查询直接返回."""
    if not _flags.get(category, False):
        return
    global _seq
    now = datetime.now()
    entry = {
        "ts": now.strftime("%H:%M:%S.%f")[:-3],
        "category": category,
        "action": action,
        "detail": str(detail)[:2000] if detail else "",
        "source": source,
    }
    with _lock:
        _seq += 1
        entry["seq"] = _seq
        _buffer.append(entry)
    line = f"[{entry['ts']}] [{category}] {action}" + (f" :: {entry['detail']}" if entry["detail"] else "")
    print(f"[DBG]{line}", flush=True)
    _write_file(line)


def ingest_client(category: str, action: str, detail: str = "") -> None:
    """前端埋点回传入口: 不查后端开关 (前端已自行守门), 直接入缓冲+落盘"""
    global _seq
    now = datetime.now()
    entry = {
        "ts": now.strftime("%H:%M:%S.%f")[:-3],
        "category": str(category)[:60],
        "action": str(action)[:300],
        "detail": str(detail)[:2000] if detail else "",
        "source": "frontend",
    }
    with _lock:
        _seq += 1
        entry["seq"] = _seq
        _buffer.append(entry)
    line = f"[{entry['ts']}] [{entry['category']}] {entry['action']}" + (f" :: {entry['detail']}" if entry["detail"] else "")
    _write_file("[FE] " + line)


def get_flags() -> dict:
    return dict(_flags)


def set_flags(updates: dict) -> dict:
    """只接受目录里已注册的类别, 防任意 key 注入"""
    for k, v in (updates or {}).items():
        if k in BACKEND_CATEGORIES:
            _flags[k] = bool(v)
    return dict(_flags)


def get_logs(since_seq: int = 0, categories=None, limit: int = 500) -> dict:
    """增量拉取: 返回 seq > since_seq 的日志 (最多 limit 条) + 当前最大 seq"""
    with _lock:
        items = [e for e in _buffer if e["seq"] > since_seq]
    if categories:
        cats = set(categories)
        items = [e for e in items if e["category"] in cats]
    items = items[-limit:]
    return {"logs": items, "max_seq": _seq, "buffer_size": len(_buffer)}


def clear_logs() -> None:
    global _seq
    with _lock:
        _buffer.clear()
