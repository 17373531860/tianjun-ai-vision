# -*- coding: utf-8 -*-
"""录像归档规则引擎 (v3.53 一~四期) — 周期录像自动交付为质量证据。

架构 (照 cluster_report_spool 先例的"独立 sender + 失败落盘 FIFO 重放"模式):

    录像延迟释放线程 (_delayed_release, release() 成功后)
        └─ notify_cycle_video_ready()   ← 只投递, 永不阻塞 (不变量 15)
              └─ 内存队列 (满则落 spool)
                    └─ video-archive-worker 独立 daemon 线程
                          ├─ 等文件尺寸稳定 (release 后的保险)
                          ├─ 等 cycle 行落库 + 结果回写 (persist FIFO 竞态守门)
                          ├─ 逐规则匹配 (结果/工位/项目筛选 + 时间窗)
                          ├─ 变换: 整段 / 事件切片(尾段 N 秒, ffmpeg -c copy)
                          ├─ 证据组装: 录像 + NG 关键帧 + sidecar 报告 (+ 打包 zip)
                          ├─ 交付: adapter 分发 (local/ftp/sftp/s3/http/插件),
                          │        本地 .tmp→rename 原子落位, 可限速
                          ├─ 台账 video_archive_logs + 规则计数
                          └─ 联动: video_archived 插件 hook + MES 网关事件
                                   + (可选) 校验后删本地源 (归档即分层)
              失败任务 → {DATA_DIR}/video_archive_spool.jsonl, 周期性重放
                          (网盘断开等瞬态故障断点续传, 超过尝试上限记败丢弃;
                           时间窗外的等待不消耗重试预算)

零开销守门: 无启用规则时 notify 直接短路返回 (_rules_active 缓存标志,
规则 CRUD 后由 API 层调 refresh_rules_cache() 刷新), worker 线程也不启动;
无 attach_keyframe 规则时 NG 结算一帧不抽 (keyframe_wanted())。
"""
import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.core.config import DATA_DIR
from backend.services.archive_adapters import (
    PermanentDeliveryError, get_adapter,
)
from backend.services.archive_secrets import decrypt_config

# ---------------------------------------------------------------------------
# 目录护栏
# ---------------------------------------------------------------------------

# 与 backend/api/sessions_maintenance._CLEANUP_DIR_BLACKLIST 保持一致
# (那边是 api 层模块, service 层不反向 import, 复制常量并注明来源)。
_DEST_DIR_BLACKLIST = {
    "/", "/root", "/home", "/etc", "/bin", "/sbin", "/usr", "/var", "/boot",
    "/lib", "/lib64", "/opt", "/dev", "/proc", "/sys", "/tmp",
    "c:", "c:\\windows", "c:\\program files", "c:\\program files (x86)",
    "c:\\users", "d:", "e:", "f:",
}


def _norm_dir(p: str) -> str:
    """标准化目录路径用于黑名单匹配: 小写 + 去尾分隔符。空 → '/'。"""
    s = (p or "").strip().lower().rstrip("/\\")
    return s or "/"


def validate_dest_dir(p: str) -> Optional[str]:
    """校验 local_dir 归档目标目录, 合法返回 None, 非法返回中文原因。

    护栏 (照 sessions_maintenance 导出清理目录的先例):
    - 必须是绝对路径 (UNC \\\\server\\share 也算绝对)
    - 不允许盘根/系统目录黑名单
    - 不允许指到本地录像目录自身/子目录 (防自拷贝循环)
    """
    s = (p or "").strip()
    if not s:
        return "目标目录不能为空"
    expanded = os.path.expanduser(s)
    if not os.path.isabs(expanded):
        return "目标目录必须是绝对路径"
    if _norm_dir(expanded) in _DEST_DIR_BLACKLIST:
        return "该目录为系统/盘根目录，禁止设为归档目录"
    # v3.54: 录像根可自定义, 默认根和自定义根都不允许指入
    rec_roots = [os.path.abspath(os.path.join(DATA_DIR, "recordings"))]
    try:
        from backend.services.recording_storage import get_custom_root
        custom = get_custom_root()
        if custom:
            rec_roots.append(os.path.abspath(os.path.expanduser(custom)))
    except Exception:
        pass
    dest_abs = os.path.abspath(expanded)
    for rec_root in rec_roots:
        try:
            if os.path.commonpath([dest_abs, rec_root]) == rec_root:
                return "目标目录不能指向本地录像目录内部（会形成自拷贝循环）"
        except ValueError:
            pass  # 不同盘符, commonpath 抛错 = 肯定不在录像目录内
    return None


_WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


def validate_window(spec: Optional[str]) -> Optional[str]:
    if not spec:
        return None
    if not _WINDOW_RE.match(spec.strip()):
        return "时间窗格式应为 HH:MM-HH:MM (如 22:00-06:00)"
    return None


def _in_window(spec: Optional[str], now: Optional[datetime] = None) -> bool:
    """时间窗判定, 支持跨午夜 (22:00-06:00)。空/非法 = 全天放行。"""
    if not spec:
        return True
    m = _WINDOW_RE.match(spec.strip())
    if not m:
        return True
    now = now or datetime.now()
    cur = now.hour * 60 + now.minute
    a = int(m.group(1)) * 60 + int(m.group(2))
    b = int(m.group(3)) * 60 + int(m.group(4))
    if a == b:
        return True
    if a < b:
        return a <= cur < b
    return cur >= a or cur < b  # 跨午夜


# ---------------------------------------------------------------------------
# 运行时状态
# ---------------------------------------------------------------------------

_SPOOL_FILE = os.path.join(DATA_DIR, "video_archive_spool.jsonl")
_QUEUE_MAX = 500
_MAX_ATTEMPTS = 20           # 单任务最多尝试次数 (含首发), 之后记败丢弃
_SPOOL_DRAIN_INTERVAL = 30.0  # spool 重放周期 (秒)
_FILE_READY_TIMEOUT = 8.0    # 等文件尺寸稳定的上限 (release 后通常立即就绪)
_CYCLE_READY_TIMEOUT = 10.0  # 等 cycle 行落库 + end_time 回写的上限

_task_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=_QUEUE_MAX)
_worker_thread: Optional[threading.Thread] = None
_worker_lock = threading.Lock()
_stop_event = threading.Event()
_spool_lock = threading.Lock()

_rules_active = False     # 有启用规则时才入队/起 worker (零开销守门)
_keyframe_active = False  # 有启用 attach_keyframe 规则时才在 NG 结算抽帧
_keyframe_watermark = True  # 任一 attach_keyframe 规则要水印就烧 (捕获时机早于规则匹配)

_stats_lock = threading.Lock()
_stats: Dict[str, Any] = {
    "enqueued": 0, "success": 0, "failed": 0, "skipped": 0,
    "spooled": 0, "dropped": 0, "deferred": 0,
    "last_error": None, "last_error_at": None, "last_success_at": None,
}


def _bump(key: str, n: int = 1):
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + n


def _note_error(msg: str):
    with _stats_lock:
        _stats["last_error"] = (msg or "")[:500]
        _stats["last_error_at"] = datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def refresh_rules_cache():
    """规则 CRUD / 启动时调用: 刷新守门缓存, 有活干则确保 worker 在跑。"""
    global _rules_active, _keyframe_active, _keyframe_watermark
    try:
        from backend.db.database import SessionLocal
        from backend.models.archive_models import VideoArchiveRule
        db = SessionLocal()
        try:
            enabled = db.query(VideoArchiveRule).filter(
                VideoArchiveRule.enabled.is_(True)).all()
            _rules_active = len(enabled) > 0
            _keyframe_active = any(r.attach_keyframe for r in enabled)
            _keyframe_watermark = any(
                r.attach_keyframe and r.keyframe_watermark for r in enabled)
        finally:
            db.close()
        # spool 里可能还有存量待重放任务, 即使规则全关也要把 worker 拉起来清账
        if _rules_active or _spool_depth() > 0:
            _ensure_worker()
    except Exception as e:
        print(f"[VideoArchive] 规则缓存刷新失败: {e}")


def keyframe_wanted() -> bool:
    """VSM 侧 NG 结算抽帧守门: 无 attach_keyframe 规则时零开销。"""
    return _keyframe_active


def keyframe_watermark_wanted() -> bool:
    """关键帧是否烧水印 (任一启用的 attach_keyframe 规则要求即烧)。"""
    return _keyframe_watermark


def notify_cycle_video_ready(channel_id: int, cycle_uuid: Optional[str],
                             filepath: str):
    """录像延迟释放线程在 release() 成功后调用。只投递, 永不阻塞。"""
    if not _rules_active:
        return
    if not cycle_uuid or not filepath:
        return
    task = {
        "kind": "cycle",
        "channel_id": channel_id,
        "cycle_uuid": cycle_uuid,
        "filepath": filepath,
        "attempts": 0,
        "created_at": time.time(),
    }
    _bump("enqueued")
    try:
        _task_queue.put_nowait(task)
    except queue.Full:
        _spool_append(task)
    _ensure_worker()


def get_status() -> Dict[str, Any]:
    """Data 页状态卡数据源。"""
    with _stats_lock:
        snap = dict(_stats)
    snap["queue_depth"] = _task_queue.qsize()
    snap["spool_depth"] = _spool_depth()
    snap["worker_alive"] = bool(_worker_thread and _worker_thread.is_alive())
    snap["rules_active"] = _rules_active
    snap["keyframe_active"] = _keyframe_active
    return snap


def archive_cycle_now(cycle_id: int,
                      rule_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """同步归档指定周期 (UI「试归档」/ 测试用), 绕过队列直接执行。

    rule_id 给定时只跑那一条规则 (允许 disabled 且无视时间窗, 便于上线前试跑);
    不给则跑全部启用规则。返回逐规则结果列表。
    """
    from backend.db.database import SessionLocal
    from backend.models.models import DetectionCycle
    from backend.models.archive_models import VideoArchiveRule

    db = SessionLocal()
    try:
        cycle = db.query(DetectionCycle).filter(
            DetectionCycle.id == cycle_id).first()
        if not cycle:
            return [{"status": "failed", "error": f"周期 {cycle_id} 不存在"}]
        if not cycle.video_path:
            return [{"status": "failed", "error": "该周期没有录像文件"}]
        q = db.query(VideoArchiveRule)
        if rule_id is not None:
            rules = q.filter(VideoArchiveRule.id == rule_id).all()
            if not rules:
                return [{"status": "failed", "error": f"规则 {rule_id} 不存在"}]
        else:
            rules = q.filter(VideoArchiveRule.enabled.is_(True)).all()
            if not rules:
                return [{"status": "skipped", "error": "没有启用的归档规则"}]
        return _archive_cycle_with_rules(
            db, cycle, cycle.video_path, rules,
            channel_id=None, enforce_filters=(rule_id is None),
            ignore_window=(rule_id is not None))
    finally:
        db.close()


def backfill_rule(rule_id: int, date_from: Optional[str] = None,
                  date_to: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
    """历史回补 (四期): 把已有带录像的周期按规则补归档, 入队异步执行。

    date_from/date_to: 'YYYY-MM-DD' (含双端, 空 = 不限)。
    只入队源文件仍然在盘的周期; 交付幂等性靠重名策略 (rename 会出 _1 副本,
    回补建议目标端用 skip/overwrite)。
    """
    from backend.db.database import SessionLocal
    from backend.models.models import DetectionCycle
    from backend.models.archive_models import VideoArchiveRule

    db = SessionLocal()
    try:
        rule = db.query(VideoArchiveRule).filter(
            VideoArchiveRule.id == rule_id).first()
        if not rule:
            return {"enqueued": 0, "error": f"规则 {rule_id} 不存在"}
        q = db.query(DetectionCycle).filter(
            DetectionCycle.video_path.isnot(None),
            DetectionCycle.end_time.isnot(None))
        if date_from:
            q = q.filter(DetectionCycle.start_time
                         >= datetime.strptime(date_from, "%Y-%m-%d"))
        if date_to:
            q = q.filter(DetectionCycle.start_time
                         < datetime.strptime(date_to, "%Y-%m-%d")
                         .replace(hour=23, minute=59, second=59))
        rows = q.order_by(DetectionCycle.id.desc()).limit(
            max(1, min(limit, 5000))).all()
        n = 0
        for c in rows:
            if not c.video_path or not os.path.isfile(c.video_path):
                continue
            task = {"kind": "cycle", "channel_id": None,
                    "cycle_uuid": c.cycle_uuid, "filepath": c.video_path,
                    "attempts": 0, "created_at": time.time(),
                    "only_rule_id": rule_id, "backfill": True}
            try:
                _task_queue.put_nowait(task)
            except queue.Full:
                _spool_append(task)
            n += 1
        if n:
            _ensure_worker()
        return {"enqueued": n}
    finally:
        db.close()


def build_evidence_pack(db, cycle_ids: List[int],
                        out_path: str) -> Dict[str, Any]:
    """手动证据包 (二期): 把一批周期的 录像+关键帧+元数据 打成一个 zip。

    Data 页勾选日期/会话批量导出用, 同步执行 (调用方限制批量上限)。
    缺录像/缺关键帧的周期尽力打包不报错, 返回逐周期收录情况。
    """
    from backend.models.models import DetectionCycle
    from backend.services.archive_media import build_evidence_zip, find_keyframe

    entries: List[Dict[str, Any]] = []
    included, missing_video = 0, 0
    for cid in cycle_ids:
        cycle = db.query(DetectionCycle).filter(
            DetectionCycle.id == cid).first()
        if not cycle:
            continue
        folder = f"cycle_{cycle.id}_{'OK' if cycle.is_good else 'NG'}"
        has_video = bool(cycle.video_path and os.path.isfile(cycle.video_path))
        if has_video:
            entries.append({"arcname": f"{folder}/video.mp4",
                            "path": cycle.video_path})
        else:
            missing_video += 1
        kf = find_keyframe(cycle.cycle_uuid) if cycle.cycle_uuid else None
        if kf:
            entries.append({"arcname": f"{folder}/keyframe.jpg", "path": kf})
        entries.append({"arcname": f"{folder}/meta.json",
                        "data": _cycle_meta_json(cycle, None)})
        included += 1
    if included == 0:
        return {"ok": False, "error": "没有可打包的周期", "included": 0}
    build_evidence_zip(out_path, entries)
    return {"ok": True, "included": included,
            "missing_video": missing_video, "path": out_path,
            "size": os.path.getsize(out_path)}


# ---------------------------------------------------------------------------
# spool 落盘 (JSONL, 照 gateway_spool / cluster_report_spool 先例)
# ---------------------------------------------------------------------------

def _spool_append(task: Dict[str, Any]):
    try:
        with _spool_lock:
            with open(_SPOOL_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(task, ensure_ascii=False, default=str) + "\n")
        _bump("spooled")
    except Exception as e:
        _bump("dropped")
        _note_error(f"spool 落盘失败: {e}")
        print(f"[VideoArchive] spool 落盘失败, 任务丢弃: {e}")


def _spool_depth() -> int:
    try:
        if not os.path.exists(_SPOOL_FILE):
            return 0
        with open(_SPOOL_FILE, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _spool_take_all() -> List[Dict[str, Any]]:
    """取出全部 spool 任务并清空文件 (损坏行跳过)。重放失败的由调用方写回。"""
    with _spool_lock:
        if not os.path.exists(_SPOOL_FILE):
            return []
        tasks: List[Dict[str, Any]] = []
        try:
            with open(_SPOOL_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        tasks.append(json.loads(line))
                    except Exception:
                        continue  # 损坏行跳过
            os.remove(_SPOOL_FILE)
        except Exception as e:
            print(f"[VideoArchive] spool 读取失败: {e}")
            return []
        return tasks


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------

def _ensure_worker():
    global _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _stop_event.clear()
        _worker_thread = threading.Thread(
            target=_worker_loop, daemon=True, name="video-archive-worker")
        _worker_thread.start()


def stop_worker(timeout: float = 5.0):
    """测试/关机用: 停 worker 线程。"""
    _stop_event.set()
    t = _worker_thread
    if t and t.is_alive():
        t.join(timeout)


def _worker_loop():
    print("[VideoArchive] 归档 worker 启动")
    last_drain = 0.0
    while not _stop_event.is_set():
        now = time.time()
        if now - last_drain >= _SPOOL_DRAIN_INTERVAL:
            last_drain = now
            for task in _spool_take_all():
                _run_task(task)
                if _stop_event.is_set():
                    return
        try:
            task = _task_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        _run_task(task)


def _run_task(task: Dict[str, Any]):
    """执行一个任务。三种收场:
    done  → 了结 (成功/跳过/永久失败已记账)
    retry → 瞬态失败, attempts+1 回 spool, 超上限记败丢弃
    defer → 时间窗外等待, 回 spool 但不消耗重试预算
    """
    try:
        outcome = _process_cycle_task(task)
    except Exception as e:
        _note_error(str(e))
        outcome = "retry"
    if outcome == "done":
        return
    if outcome == "defer":
        _bump("deferred")
        _spool_append(task)
        return
    task["attempts"] = int(task.get("attempts", 0)) + 1
    if task["attempts"] >= _MAX_ATTEMPTS:
        _bump("dropped")
        _write_log(rule_id=None, cycle_id=task.get("cycle_db_id"),
                   channel_id=task.get("channel_id"),
                   src_path=task.get("filepath"), dest_path=None,
                   status="failed",
                   error=f"重试 {_MAX_ATTEMPTS} 次仍失败, 放弃归档")
        print(f"[VideoArchive] 任务超过重试上限, 丢弃: {task.get('filepath')}")
    else:
        _spool_append(task)


def _wait_file_ready(path: str, timeout: float = _FILE_READY_TIMEOUT) -> bool:
    """等文件存在且尺寸稳定 (两拍相同且 > 0)。release 后通常一拍就过。"""
    deadline = time.time() + timeout
    last_size = -1
    while time.time() < deadline and not _stop_event.is_set():
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        if size > 0 and size == last_size:
            return True
        last_size = size
        time.sleep(0.3)
    return False


def _process_cycle_task(task: Dict[str, Any]) -> str:
    """处理一个周期归档任务。返回 'done' | 'retry' | 'defer'。"""
    from backend.db.database import SessionLocal
    from backend.models.models import DetectionCycle
    from backend.models.archive_models import VideoArchiveRule

    filepath = task["filepath"]
    cycle_uuid = task["cycle_uuid"]

    if not os.path.exists(filepath):
        # 首发时文件必在 (release 刚完成); spool 重放时可能已被本地清理删掉
        if task.get("attempts", 0) > 0:
            _bump("dropped")
            _write_log(rule_id=None, cycle_id=task.get("cycle_db_id"),
                       channel_id=task.get("channel_id"), src_path=filepath,
                       dest_path=None, status="failed",
                       error="源录像文件已不存在 (可能已被本地清理)")
            return "done"
        return "retry"

    if not _wait_file_ready(filepath):
        return "retry"

    # 等 cycle 行落库且结算收尾 (end_time 回写) — persist FIFO 与 1.5s drain
    # 的竞态守门: result_filter 依赖 is_good 终值。
    db = SessionLocal()
    try:
        cycle = None
        deadline = time.time() + _CYCLE_READY_TIMEOUT
        while time.time() < deadline and not _stop_event.is_set():
            db.expire_all()
            cycle = db.query(DetectionCycle).filter(
                DetectionCycle.cycle_uuid == cycle_uuid).first()
            if cycle is not None and cycle.end_time is not None:
                break
            time.sleep(0.5)
        if cycle is None:
            return "retry"  # 行还没落库, 回 spool 再等
        q = db.query(VideoArchiveRule)
        if task.get("only_rule_id"):  # 回补任务只跑指定规则
            rules = q.filter(VideoArchiveRule.id == task["only_rule_id"]).all()
        else:
            rules = q.filter(VideoArchiveRule.enabled.is_(True)).all()
        if not rules:
            return "done"
        results = _archive_cycle_with_rules(
            db, cycle, filepath, rules,
            channel_id=task.get("channel_id"), enforce_filters=True)
        # 时间窗外被搁置的规则 → 整任务 defer (不耗重试预算)
        if any(r.get("window_deferred") for r in results):
            return "defer"
        # 任一规则瞬态失败 → 整任务重试 (已成功的规则重放时靠重名策略幂等)
        if any(r["status"] == "failed" and r.get("transient")
               for r in results):
            return "retry"
        # 归档即分层: 全部命中规则成功 + 任一规则要求删源 → 校验后删本地件
        _maybe_delete_source(db, cycle, filepath, rules, results)
        return "done"
    finally:
        db.close()


def _maybe_delete_source(db, cycle, filepath: str, rules, results):
    """delete_source_after: 所有命中规则 success 且至少一条规则要求删源时,
    删本地录像并回写 cycle.video_path=None (回放入口随之消失, 分层语义)。"""
    executed = [r for r in results if r["status"] in ("success", "failed")]
    if not executed or any(r["status"] != "success" for r in executed):
        return
    rule_by_id = {r.id: r for r in rules}
    if not any(rule_by_id.get(r.get("rule_id"))
               and rule_by_id[r["rule_id"]].delete_source_after
               for r in executed):
        return
    try:
        # 至少一份本地交付时校验字节数一致再删 (远端交付信任 adapter 成功语义)
        src_size = os.path.getsize(filepath)
        for r in executed:
            dp = r.get("dest_path")
            if dp and os.path.isfile(dp) and os.path.getsize(dp) != src_size \
                    and not dp.endswith(".zip"):
                print(f"[VideoArchive] 删源取消: 目的地字节数不一致 {dp}")
                return
        os.remove(filepath)
        cycle.video_path = None
        db.commit()
        print(f"[VideoArchive] 归档即分层: 已删本地源 {os.path.basename(filepath)}")
    except Exception as e:
        db.rollback()
        print(f"[VideoArchive] 删源失败 (归档结果不受影响): {e}")


# ---------------------------------------------------------------------------
# 归档执行 (worker 与 test-run 共用)
# ---------------------------------------------------------------------------

def _match_rule(rule, cycle, channel_id: Optional[int],
                project_id: Optional[int]) -> bool:
    if rule.result_filter == "ng_only" and cycle.is_good:
        return False
    if rule.result_filter == "ok_only" and not cycle.is_good:
        return False
    if rule.channel_filter and channel_id is not None \
            and channel_id not in rule.channel_filter:
        return False
    if rule.project_filter and project_id is not None \
            and project_id not in rule.project_filter:
        return False
    return True


def _adapter_cfg_for(rule) -> Dict[str, Any]:
    """组 adapter 配置: local_dir 用规则自身字段, 远端用解密后的 dest_config。"""
    if (rule.dest_type or "local_dir") == "local_dir":
        return {"dest_dir": rule.dest_dir,
                "overwrite_policy": rule.overwrite_policy or "rename"}
    return decrypt_config(rule.dest_config) or {}


def _prepare_artifacts(db, cycle, filepath: str, rule, ctx,
                       basename: str, tmpdir: str) -> List[Dict[str, str]]:
    """按规则组装证据文件清单 [{'path':, 'name':}]。第一项是主件 (录像/切片)。"""
    from backend.services.archive_media import clip_tail, find_keyframe

    artifacts: List[Dict[str, str]] = []
    # 1) 录像主件 (整段或事件切片; 切片失败回退整段 — 证据宁全勿缺)
    video_src = filepath
    if (rule.transform or "none") == "clip_tail":
        try:
            clipped = os.path.join(tmpdir, basename + "_clip.mp4")
            clip_tail(filepath, clipped, int(rule.clip_seconds or 10))
            video_src = clipped
        except Exception as e:
            print(f"[VideoArchive] 事件切片失败, 回退整段归档: {e}")
    artifacts.append({"path": video_src, "name": basename + ".mp4"})

    # 2) NG 关键帧 (只对 NG 周期有意义; OK 周期抽帧点不存在)
    if rule.attach_keyframe and not cycle.is_good:
        kf = find_keyframe(cycle.cycle_uuid)
        if kf:
            artifacts.append({"path": kf, "name": basename + ".jpg"})

    # 3) sidecar 伴随报告 (绑定自定义导出模板, 成对渲染)
    if rule.sidecar_template_id:
        from backend.services.archive_ecosystem import render_sidecar
        sc = render_sidecar(db, rule.sidecar_template_id, ctx,
                            tmpdir, basename)
        if sc:
            artifacts.append({"path": sc, "name": os.path.basename(sc)})
    return artifacts


def _archive_cycle_with_rules(db, cycle, filepath: str, rules,
                              channel_id: Optional[int],
                              enforce_filters: bool,
                              ignore_window: bool = False) -> List[Dict[str, Any]]:
    """对一个周期逐规则执行归档。返回逐规则结果
    (status/dest/error/transient/window_deferred)。"""
    from backend.services.export_context import build_cycle_context
    from backend.services.export_renderer import render_filename
    from backend.services.archive_media import build_evidence_zip

    # 项目 ID / 工位: session 行上有 (筛选用)
    project_id = None
    try:
        if cycle.session is not None:
            project_id = getattr(cycle.session, "project_id", None)
    except Exception:
        project_id = None
    if channel_id is None:
        try:
            channel_id = getattr(cycle.session, "channel_id", None) \
                if cycle.session is not None else None
        except Exception:
            channel_id = None

    ctx: Optional[Dict[str, Any]] = None
    results: List[Dict[str, Any]] = []
    for rule in rules:
        if enforce_filters and not _match_rule(rule, cycle, channel_id, project_id):
            continue
        # 时间窗: 窗外整任务回 spool 等 (不耗重试预算, 试归档无视窗口)
        if not ignore_window and not _in_window(rule.active_window):
            results.append({"rule_id": rule.id, "rule_name": rule.name,
                            "status": "deferred", "dest_path": None,
                            "error": f"时间窗外等待 ({rule.active_window})",
                            "transient": False, "window_deferred": True})
            continue
        t0 = time.time()
        status, dest_path, error, transient = "failed", None, None, False
        tmpdir = None
        try:
            if (rule.dest_type or "local_dir") == "local_dir":
                guard = validate_dest_dir(rule.dest_dir)
                if guard:
                    raise _PermanentError(guard)
            if ctx is None:
                ctx = build_cycle_context(db, cycle.id)
            filename = render_filename(
                rule.filename_template or "{{ cycle.id }}.mp4",
                ctx, fallback_ext="mp4")
            basename = os.path.splitext(filename)[0]
            subdir = datetime.now().strftime("%Y-%m-%d") \
                if rule.subdir_by_date else ""

            tmpdir = tempfile.mkdtemp(prefix="va_")
            artifacts = _prepare_artifacts(
                db, cycle, filepath, rule, ctx, basename, tmpdir)

            adapter = get_adapter(rule.dest_type or "local_dir")
            cfg = _adapter_cfg_for(rule)
            throttle = rule.bandwidth_limit_kbps or None

            if rule.bundle_zip:
                # 证据包: 全部证据打一个 zip 交付
                meta = _cycle_meta_json(cycle, ctx)
                zip_path = os.path.join(tmpdir, basename + ".zip")
                entries = [{"arcname": a["name"], "path": a["path"]}
                           for a in artifacts]
                entries.append({"arcname": basename + "_meta.json",
                                "data": meta})
                build_evidence_zip(zip_path, entries)
                dest_path = adapter(zip_path, subdir, basename + ".zip",
                                    cfg, throttle)
            else:
                # 散件交付: 主件在前, 附件跟随 (附件失败不推翻主件成功)
                dest_path = adapter(artifacts[0]["path"], subdir,
                                    artifacts[0]["name"], cfg, throttle)
                for extra in artifacts[1:]:
                    try:
                        adapter(extra["path"], subdir, extra["name"],
                                cfg, throttle)
                    except Exception as _xe:
                        print(f"[VideoArchive] 附件交付失败 (主件已成功): "
                              f"{extra['name']}: {_xe}")

            if dest_path and dest_path.startswith("[skip]"):
                status = "skipped"
                dest_path = dest_path[len("[skip]"):]
                error = "目标已存在 (skip 策略)"
            else:
                status = "success"
        except (_PermanentError, PermanentDeliveryError) as e:
            error = str(e)
        except Exception as e:
            # IO/网络类故障视为瞬态, 回 spool 重试; 模板渲染错是永久失败
            error = str(e)
            transient = not isinstance(e, (KeyError, ValueError, TypeError))
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)
        duration_ms = int((time.time() - t0) * 1000)
        fsize = None
        try:
            fsize = os.path.getsize(filepath)
        except OSError:
            pass
        # 瞬态失败不记台账不计数 (整任务会回 spool 重来), 只记 last_error
        if transient:
            _note_error(f"规则[{rule.name}] {error}")
        else:
            _write_log(rule_id=rule.id, cycle_id=cycle.id,
                       channel_id=channel_id, src_path=filepath,
                       dest_path=dest_path if status == "success" else None,
                       status=status, error=error,
                       file_size=fsize, duration_ms=duration_ms, db=db)
            _bump(status if status in ("success", "failed", "skipped")
                  else "failed")
            if status == "success":
                with _stats_lock:
                    _stats["last_success_at"] = datetime.now().isoformat(
                        timespec="seconds")
                _notify_ecosystem(db, cycle, rule, dest_path, channel_id)
            elif status == "failed":
                _note_error(f"规则[{rule.name}] {error}")
            _update_rule_stats(db, rule, status, error, dest_path)
        results.append({
            "rule_id": rule.id, "rule_name": rule.name, "status": status,
            "dest_path": dest_path, "error": error, "transient": transient,
        })
    return results


def _cycle_meta_json(cycle, ctx: Optional[Dict[str, Any]]) -> str:
    """证据包内的周期元数据 JSON (人可读, 客户质量部要的"这单是什么")。"""
    meta = {
        "cycle_id": cycle.id,
        "cycle_uuid": cycle.cycle_uuid,
        "cycle_number": cycle.cycle_number,
        "start_time": str(cycle.start_time),
        "end_time": str(cycle.end_time),
        "result": "OK" if cycle.is_good else "NG",
        "ng_reason": getattr(cycle, "result_reason", None),
        "step_sequence": getattr(cycle, "step_sequence", None),
    }
    try:
        if ctx:
            wp = ctx.get("workpiece") or {}
            od = ctx.get("order") or {}
            meta["serial_no"] = wp.get("serial_no")
            meta["order_no"] = od.get("order_no")
    except Exception:
        pass
    return json.dumps(meta, ensure_ascii=False, indent=2, default=str)


def _notify_ecosystem(db, cycle, rule, dest_path: Optional[str],
                      channel_id: Optional[int]):
    """归档成功后的生态联动 (三/四期): 插件 hook + MES 网关事件。
    全部错误隔离 — 联动失败不影响归档结果。"""
    try:
        from backend.services.archive_ecosystem import on_video_archived
        on_video_archived(db, cycle, rule, dest_path, channel_id)
    except Exception as e:
        print(f"[VideoArchive] 归档联动失败 (不影响归档): {e}")


class _PermanentError(Exception):
    """明确不可重试的失败 (护栏拒绝等)。"""


def _update_rule_stats(db, rule, status: str, error: Optional[str],
                       dest_path: Optional[str]):
    try:
        rule.last_run_time = datetime.now()
        rule.last_run_status = status
        rule.last_run_error = error
        if status == "success":
            rule.success_count = (rule.success_count or 0) + 1
            rule.last_dest_file = dest_path
        elif status == "failed":
            rule.failed_count = (rule.failed_count or 0) + 1
        else:
            rule.skipped_count = (rule.skipped_count or 0) + 1
        db.commit()
    except Exception:
        db.rollback()


_LOG_TRIM_EVERY = 200
_log_write_count = 0


def _write_log(rule_id, cycle_id, channel_id, src_path, dest_path,
               status, error=None, file_size=None, duration_ms=None, db=None):
    """写台账 (独立 session 兜底); 每 200 条顺手裁掉最旧的, 只留 5000 条。"""
    global _log_write_count
    from backend.models.archive_models import VideoArchiveLog
    own = db is None
    if own:
        from backend.db.database import SessionLocal
        db = SessionLocal()
    try:
        db.add(VideoArchiveLog(
            rule_id=rule_id, cycle_id=cycle_id, channel_id=channel_id,
            src_path=src_path, dest_path=dest_path,
            status=status, error=error,
            file_size=file_size, duration_ms=duration_ms,
        ))
        db.commit()
        _log_write_count += 1
        if _log_write_count % _LOG_TRIM_EVERY == 0:
            _trim_logs(db)
    except Exception as e:
        db.rollback()
        print(f"[VideoArchive] 台账写入失败: {e}")
    finally:
        if own:
            db.close()


def _trim_logs(db, keep: int = 5000):
    from backend.models.archive_models import VideoArchiveLog
    try:
        ids = [r[0] for r in db.query(VideoArchiveLog.id).order_by(
            VideoArchiveLog.id.desc()).offset(keep).limit(1).all()]
        if ids:
            db.query(VideoArchiveLog).filter(
                VideoArchiveLog.id <= ids[0]).delete(synchronize_session=False)
            db.commit()
    except Exception:
        db.rollback()
