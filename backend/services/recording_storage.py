# -*- coding: utf-8 -*-
"""录像存储位置服务 — 自定义录像根目录, 实时生效 (v3.54)。

客户诉求: 工控机 C 盘容量小, 录像必须能直接写到别的盘/大容量分区,
而不是先落 C 盘再靠归档规则搬运。本服务提供:

  - SystemConfig KV ``recording_storage_dir`` 存自定义根目录 (空 = 默认数据目录)
  - :func:`get_recording_root` / :func:`get_video_dirs` 在每次开录时动态解析,
    改完配置下一段录像立即写新目录, 进行中的录像不受影响
  - :func:`validate_recording_dir` 复用归档目录黑名单护栏 + 真实试写校验
  - 回放免改: DB 存绝对路径, ``GET /data/videos/{id}`` 按路径出文件,
    新旧目录的历史录像都照常可看

可靠性约定 (与录像"绝不因目录问题丢件"的既有立场一致):
  - 自定义目录解析/建目录失败 → 自动回退默认目录并打日志, 录像照录
  - 配置读取带 10s TTL 缓存, 写入端点保存后主动 :func:`refresh_cache`
"""
import os
import threading
import time
from typing import Dict, Optional

from backend.core.config import settings

KV_KEY = "recording_storage_dir"

_SUBDIRS = {
    "sessions": "sessions",
    "cycles": "cycles",
    "steps": "steps",
    "cache": "cache",
}

_cache_lock = threading.Lock()
_cached_value: Optional[str] = None
_cached_at: float = 0.0
_CACHE_TTL = 10.0


def _read_kv() -> Optional[str]:
    """读 SystemConfig KV, 任何异常都回退 None (= 默认目录)。"""
    try:
        from backend.db.database import SessionLocal
        from backend.models.models import SystemConfig
        db = SessionLocal()
        try:
            row = db.query(SystemConfig).filter(
                SystemConfig.key == KV_KEY).first()
            val = (row.value or "").strip() if row else ""
            return val or None
        finally:
            db.close()
    except Exception as e:
        print(f"[RecordingStorage] 读取自定义录像目录失败, 回退默认: {e}")
        return None


def get_custom_root(force: bool = False) -> Optional[str]:
    """当前配置的自定义根目录 (未配置返回 None), 带 TTL 缓存。"""
    global _cached_value, _cached_at
    with _cache_lock:
        if not force and (time.time() - _cached_at) < _CACHE_TTL:
            return _cached_value
    val = _read_kv()
    with _cache_lock:
        _cached_value = val
        _cached_at = time.time()
    return val


def refresh_cache() -> None:
    """配置保存后由 API 层调用, 立即生效不等 TTL。"""
    get_custom_root(force=True)


def validate_recording_dir(p: str) -> Optional[str]:
    """校验自定义录像根目录, 合法返回 None, 非法返回中文原因。

    护栏与归档目的地同源 (video_archive._DEST_DIR_BLACKLIST):
    绝对路径 / 非系统目录盘根 / 真实可写 (试写探针文件)。
    额外禁止与任何启用中的本地归档目的地互相嵌套 (防归档自拷贝循环)。
    """
    from backend.services.video_archive import _DEST_DIR_BLACKLIST, _norm_dir
    s = (p or "").strip()
    if not s:
        return "目录不能为空（恢复默认请用清空操作）"
    expanded = os.path.expanduser(s)
    if not os.path.isabs(expanded):
        return "必须是绝对路径"
    if _norm_dir(expanded) in _DEST_DIR_BLACKLIST:
        return "该目录为系统/盘根目录，禁止设为录像目录"
    target = os.path.abspath(expanded)

    # 与启用中的本地归档目的地互斥: 录像根不能在归档目的地内, 反之亦然
    try:
        from backend.db.database import SessionLocal
        from backend.models.archive_models import VideoArchiveRule
        db = SessionLocal()
        try:
            rules = db.query(VideoArchiveRule).filter(
                VideoArchiveRule.enabled.is_(True)).all()
            for rule in rules:
                dest = (getattr(rule, "dest_dir", None) or "").strip()
                if not dest:
                    continue
                dest_abs = os.path.abspath(os.path.expanduser(dest))
                for a, b in ((target, dest_abs), (dest_abs, target)):
                    try:
                        if os.path.commonpath([a, b]) == b:
                            return (f"与启用中的归档规则「{rule.name}」目的地 "
                                    f"{dest} 互相嵌套，会形成自拷贝循环")
                    except ValueError:
                        pass  # 不同盘符 = 肯定不嵌套
        finally:
            db.close()
    except Exception:
        pass  # 归档表不可用时不拦 (护栏尽力而为, 不能反过来卡录像配置)

    # 真实可写校验: 建目录 + 试写探针
    try:
        os.makedirs(target, exist_ok=True)
        probe = os.path.join(target, ".tj_write_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except Exception as e:
        return f"目录不可写: {e}"
    return None


def get_recording_root() -> str:
    """当前生效的录像根目录 (自定义可用则自定义, 否则默认)。"""
    custom = get_custom_root()
    if custom:
        try:
            os.makedirs(custom, exist_ok=True)
            if os.path.isdir(custom):
                return custom
        except Exception as e:
            print(f"[RecordingStorage] 自定义目录不可用({e}), 本段回退默认目录")
    return settings.RECORDING_DIR


def get_video_dirs() -> Dict[str, str]:
    """返回当前生效的各粒度录像目录 (含转码缓存), 目录保证已创建。

    自定义根下沿用 sessions/cycles/steps/cache 子目录结构,
    与默认目录同构, 清理/孤儿扫描按同一套子目录遍历。
    """
    root = get_recording_root()
    if root == settings.RECORDING_DIR:
        dirs = {
            "root": settings.RECORDING_DIR,
            "sessions": settings.SESSION_VIDEO_DIR,
            "cycles": settings.CYCLE_VIDEO_DIR,
            "steps": settings.STEP_VIDEO_DIR,
            "cache": os.path.join(settings.RECORDING_DIR, "cache"),
        }
    else:
        dirs = {"root": root}
        dirs.update({k: os.path.join(root, sub)
                     for k, sub in _SUBDIRS.items()})
    for key, d in dirs.items():
        if key == "root":
            continue
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass  # 开录时 _ensure_dated_dir 还有一层回退
    return dirs


def all_scan_roots() -> list:
    """清理/孤儿扫描/统计用: 返回需要遍历的录像根目录列表 (默认 + 自定义)。

    自定义目录即使当前被停用也不在这里出现——历史录像的删除走 DB 里的
    绝对路径, 不依赖目录扫描; 这里只影响孤儿文件识别与占用统计。
    """
    roots = [settings.RECORDING_DIR]
    custom = get_custom_root()
    if custom and os.path.isdir(custom):
        custom_abs = os.path.abspath(custom)
        if custom_abs not in (os.path.abspath(r) for r in roots):
            roots.append(custom_abs)
    return roots
