"""
数据维护 API：备份、清理、清理设置、存储信息。
从 sessions.py 拆出。通过 sessions.router.include_router(router) 挂接。
"""
import os
import shutil
import threading
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.core.config import settings
from backend.db.database import get_db, SessionLocal
from backend.models.models import (
    DetectionSession, DetectionCycle, StepRecord,
    VideoClip, SystemConfig,
)

router = APIRouter()

_DEFAULT_RETENTION = 30
_DEFAULT_OK_DAYS = 7
_DEFAULT_NG_DAYS = 180
_DEFAULT_LOG_DAYS = 0  # 过程日志(MES通讯/扫码/外设)独立保留天数; 0 = 跟随全局 retention_days
_DEFAULT_EXPORT_DAYS = 0  # 导出文件独立保留天数; 0 = 跟随全局 retention_days

# A5 层2/层3 托底扫描: 绝不允许作为"导出清理目录"的危险路径(盘根/系统目录)。
# 元素均为 _norm_cleanup_dir 标准化后形式(小写 + 去尾分隔符)。
_CLEANUP_DIR_BLACKLIST = {
    "/", "/root", "/home", "/etc", "/bin", "/sbin", "/usr", "/var", "/boot",
    "/lib", "/lib64", "/opt", "/dev", "/proc", "/sys", "/tmp",
    "c:", "c:\\windows", "c:\\program files", "c:\\program files (x86)",
    "c:\\users", "d:", "e:", "f:",
}
# 自动清理只认这些导出格式扩展名(托底扫描时)
_EXPORT_FILE_EXTS = {".txt", ".csv", ".docx", ".xlsx", ".pdf"}


def _norm_cleanup_dir(p: str) -> str:
    """标准化目录路径用于黑名单匹配: 小写 + 去尾分隔符。空 → '/'。"""
    s = (p or "").strip().lower().rstrip("/\\")
    return s or "/"


# ---- internal helpers (also used by main.py 启动时调用) ----

def _read_cleanup_settings(db):
    """从数据库读取清理设置（复用传入的 db，不自开关）。返回 dict。

    - retention_days / auto_cleanup: 全局保留天数 + 自动清理开关（原有）
    - video_split_ok_ng: 周期录像是否按 OK/NG 分别保留（新增，默认 False = 关）
    - video_ok_retention_days / video_ng_retention_days: 开启后 OK/NG 各自保留天数
    """
    def _val(key):
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        return row.value if row else None

    def _int(key, default):
        v = _val(key)
        try:
            return int(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default

    return {
        "retention_days": _int("retention_days", _DEFAULT_RETENTION),
        "auto_cleanup": (_val("auto_cleanup") != "false"),  # 无配置 / 非 false → True（同原行为）
        "video_split_ok_ng": (_val("video_split_ok_ng") == "true"),  # 默认 False
        "video_ok_retention_days": _int("video_ok_retention_days", _DEFAULT_OK_DAYS),
        "video_ng_retention_days": _int("video_ng_retention_days", _DEFAULT_NG_DAYS),
        # 过程日志保留天数: 0 = 跟随全局 retention_days（默认满足"设了保留期就该清"诉求）
        "log_retention_days": _int("log_retention_days", _DEFAULT_LOG_DAYS),
        # A1: 每天固定时点清理 "HH:MM"; 空 = 不启用(只保留开机+24h 旧行为)
        "cleanup_daily_time": (_val("cleanup_daily_time") or "").strip(),
        # 导出文件保留天数: 0 = 跟随全局
        "export_retention_days": _int("export_retention_days", _DEFAULT_EXPORT_DAYS),
        # A5 层2: 数据中心登记的"导出根目录"(客户专门准备的导出落地目录, 授权托底清理)
        "export_cleanup_dir": (_val("export_cleanup_dir") or "").strip(),
        # A5 层3: 是否对登记的导出根目录做托底扫描(默认关; 台账精准清已覆盖主场景)
        "export_cleanup_scan_dir": (_val("export_cleanup_scan_dir") == "true"),
    }


def _get_cleanup_settings_from_db():
    """兼容旧签名：仅返回 (retention_days, auto_cleanup)。"""
    db = SessionLocal()
    try:
        cfg = _read_cleanup_settings(db)
        return cfg["retention_days"], cfg["auto_cleanup"]
    except Exception:
        return _DEFAULT_RETENTION, True
    finally:
        db.close()


def _delete_cycles_by_filter(db, cycle_filter):
    """删除满足 cycle_filter 的周期：连同其步骤记录、周期/步骤录像文件与录像记录一起删。

    以"周期"为归属单位删，避免历史"录像按独立时间删 → 周期记录删了录像还在 / 录像记录指向
    已删周期"的孤儿问题。返回 (cycle_count, step_count, video_count, deleted_files)。
    """
    cids = [row[0] for row in db.query(DetectionCycle.id).filter(cycle_filter).all()]
    if not cids:
        return 0, 0, 0, 0

    deleted_files = 0
    step_ids = [row[0] for row in db.query(StepRecord.id).filter(
        StepRecord.cycle_id.in_(cids)
    ).all()]

    # 周期录像 + 步骤录像的物理文件
    vid_q = db.query(VideoClip).filter(or_(
        and_(VideoClip.clip_type == 'cycle', VideoClip.related_id.in_(cids)),
        and_(VideoClip.clip_type == 'step', VideoClip.related_id.in_(step_ids)) if step_ids else False,
    ))
    for v in vid_q.all():
        if v.file_path and os.path.isfile(v.file_path):
            try:
                os.remove(v.file_path)
                deleted_files += 1
            except Exception as e:
                print(f"[自动清理] 删除录像文件失败: {v.file_path}, {e}")

    video_count = vid_q.delete(synchronize_session=False)
    step_count = db.query(StepRecord).filter(
        StepRecord.cycle_id.in_(cids)
    ).delete(synchronize_session=False)
    cycle_count = db.query(DetectionCycle).filter(
        DetectionCycle.id.in_(cids)
    ).delete(synchronize_session=False)
    return cycle_count, step_count, video_count, deleted_files


def _checkpoint_wal():
    """A3: 收缩 WAL 文件（仅 SQLite）。

    项目开了 WAL 模式但从不主动做 checkpoint(TRUNCATE)，-wal 文件只涨不收，
    是"删了数据但磁盘不降"的原因之一。清理/清空删行后调一次，把 WAL 写回主库
    并清空 -wal 文件。用 raw_connection 绕过 ORM 事务，确保 PRAGMA 真正落地。
    独立错误隔离：busy / 异常都不影响清理结果，下次清理时再收缩。
    """
    try:
        from backend.db.database import engine
        if engine.dialect.name != "sqlite":
            return
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            cur.close()
            raw.commit()
        finally:
            raw.close()
    except Exception as e:
        print(f"[自动清理] WAL 收缩跳过: {e}")


def _calc_data_size():
    """估算数据目录总占用字节（用于清理前后对比释放了多少）。失败返回 0。"""
    try:
        total = 0
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(base_dir, "sql_app.db")
        for extra in ("", "-wal", "-shm"):
            p = db_path + extra
            if os.path.isfile(p):
                total += os.path.getsize(p)
        for d in (settings.RECORDING_DIR, settings.VIDEO_UPLOAD_DIR):
            if os.path.isdir(d):
                for dp, _, fns in os.walk(d):
                    for f in fns:
                        try:
                            total += os.path.getsize(os.path.join(dp, f))
                        except OSError:
                            pass
        return total
    except Exception:
        return 0


def _record_cleanup_status(size_before, summary):
    """A7: 记录本次清理结果（时间 / 释放空间 / 明细）到 SystemConfig，供设置页展示。"""
    import json
    try:
        size_after = _calc_data_size()
        freed = max(size_before - size_after, 0)
        db = SessionLocal()
        try:
            _set_system_config(db, "last_cleanup_at", datetime.now().isoformat(), "上次自动清理时间")
            _set_system_config(db, "last_cleanup_freed_bytes", str(freed), "上次清理释放字节数")
            _set_system_config(db, "last_cleanup_summary", json.dumps(summary, ensure_ascii=False), "上次清理明细")
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[自动清理] 记录清理状态失败: {e}")


def _perform_auto_cleanup():
    """按保留期自动清理：周期(可按 OK/NG 分别保留) + 会话/步骤记录 + 录像文件 + 孤儿 + 缓存 + 过期上传视频。

    以"周期"为基本归属单位：一个周期过期 → 连同它的步骤记录、录像文件、录像记录一起删，
    彻底杜绝"周期记录删了、录像还在"的孤儿（修历史口径不一致 bug）。
    开启 OK/NG 分开存时，OK / NG 周期各用自己的保留期；无结果标记的旧周期走全局保留天数。
    会话仅在其名下已无剩余周期且自身已过全局保留期时才删——这样 NG 周期长留时，承载它的
    会话（回放需要的父记录）也会被保住。
    """
    size_before = _calc_data_size()
    result_holder = {}
    db = SessionLocal()
    try:
        cfg = _read_cleanup_settings(db)
        if not cfg["auto_cleanup"] or cfg["retention_days"] <= 0:
            return

        now = datetime.now()
        retention_days = cfg["retention_days"]
        base_cutoff = now - timedelta(days=retention_days)
        cutoff_ts = base_cutoff.timestamp()
        split = cfg["video_split_ok_ng"]
        ok_days = max(cfg["video_ok_retention_days"], 0)
        ng_days = max(cfg["video_ng_retention_days"], 0)

        if split:
            print(f"[自动清理] 开始清理 (OK/NG 录像分开存: OK {ok_days}天 / "
                  f"NG {ng_days}天; 数据记录及其余 {retention_days}天)")
        else:
            print(f"[自动清理] 开始清理 {retention_days} 天前的数据 (截止: {base_cutoff.strftime('%Y-%m-%d %H:%M:%S')})")

        session_count = cycle_count = step_count = video_count = deleted_files = 0

        # 1. 周期"数据记录"清理（归属单位：周期 + 其步骤 + 其录像一起删）。
        #    语义边界（关键）：OK/NG 分开存只决定"录像文件"保留多久，**数据记录**至少保留全局
        #    retention_days；当某结果的录像保留期更长（如 NG 180>30），其数据记录跟着延长，
        #    以免录像还在却没有可回放的父记录。OK 录像若更短（7<30）只在步骤 1b 单删录像、
        #    数据记录仍按全局保留，绝不提前删。无录像标记（result 为空）的旧周期一律走全局保留期。
        ok_cids_sub = db.query(VideoClip.related_id).filter(
            VideoClip.clip_type == 'cycle', VideoClip.result == 'OK')
        ng_cids_sub = db.query(VideoClip.related_id).filter(
            VideoClip.clip_type == 'cycle', VideoClip.result == 'NG')

        if not split:
            cycle_filters = [DetectionCycle.start_time < base_cutoff]
        else:
            ok_data_cutoff = now - timedelta(days=max(retention_days, ok_days))
            ng_data_cutoff = now - timedelta(days=max(retention_days, ng_days))
            cycle_filters = [
                # OK 录像周期：数据记录保留 max(全局, OK录像保留)
                and_(DetectionCycle.start_time < ok_data_cutoff,
                     DetectionCycle.id.in_(ok_cids_sub)),
                # NG 录像周期：数据记录保留 max(全局, NG录像保留)
                and_(DetectionCycle.start_time < ng_data_cutoff,
                     DetectionCycle.id.in_(ng_cids_sub)),
                # 无录像标记的旧周期：全局保留
                and_(DetectionCycle.start_time < base_cutoff,
                     ~DetectionCycle.id.in_(ok_cids_sub),
                     ~DetectionCycle.id.in_(ng_cids_sub)),
            ]

        for cf in cycle_filters:
            cc, sc, vc, df = _delete_cycles_by_filter(db, cf)
            cycle_count += cc
            step_count += sc
            video_count += vc
            deleted_files += df
        db.commit()

        # 1b. 录像文件单独清理：某结果的录像保留期 < 其数据记录保留期时（常见于 OK 7<全局30），
        #     录像先过期删除，但保留周期数据记录（统计仍在，仅回放文件已清，回放引用一并置空）。
        if split:
            for result, vdays in (('OK', ok_days), ('NG', ng_days)):
                if vdays >= retention_days:
                    continue  # 录像保留 >= 数据保留 → 已在步骤 1 随周期删除
                vcut = now - timedelta(days=vdays)
                vq = db.query(VideoClip).filter(
                    VideoClip.clip_type == 'cycle',
                    VideoClip.result == result,
                    VideoClip.created_at < vcut,
                )
                rows = vq.all()
                if not rows:
                    continue
                stale_cids = [v.related_id for v in rows if v.related_id]
                for v in rows:
                    if v.file_path and os.path.isfile(v.file_path):
                        try:
                            os.remove(v.file_path)
                            deleted_files += 1
                        except Exception as e:
                            print(f"[自动清理] 删除过期{result}录像失败: {v.file_path}, {e}")
                video_count += vq.delete(synchronize_session=False)
                if stale_cids:
                    db.query(DetectionCycle).filter(
                        DetectionCycle.id.in_(stale_cids)
                    ).update(
                        {DetectionCycle.video_path: None, DetectionCycle.video_id: None},
                        synchronize_session=False,
                    )
            db.commit()

        # 2. 会话级清理：过了全局保留期、且名下已无剩余周期的会话才删（连带会话录像）。
        old_sessions = db.query(DetectionSession).filter(
            DetectionSession.start_time < base_cutoff
        ).all()
        for s in old_sessions:
            has_cycle = db.query(DetectionCycle.id).filter(
                DetectionCycle.session_id == s.id
            ).first()
            if has_cycle:
                continue  # 仍挂着未过期周期（如长留 NG）→ 保住父会话以便回放
            svids = db.query(VideoClip).filter(
                VideoClip.clip_type == 'session', VideoClip.related_id == s.id
            )
            for v in svids.all():
                if v.file_path and os.path.isfile(v.file_path):
                    try:
                        os.remove(v.file_path)
                        deleted_files += 1
                    except Exception as e:
                        print(f"[自动清理] 删除会话录像失败: {v.file_path}, {e}")
            video_count += svids.delete(synchronize_session=False)
            db.delete(s)
            session_count += 1
        db.commit()

        print(f"[自动清理] 数据库: {session_count}个会话, {cycle_count}个周期, {step_count}条步骤, {video_count}个视频记录, {deleted_files}个关联文件")

        # 2c. 过程日志表清理（扫码 / MES通讯 / 称重外设三张只增不减的过程流水）。
        #     这些是排查用流水, 不是业务数据（工单/工件/缺陷等业务表一律不碰）;
        #     随生产无限增长是"磁盘只涨"的主因之一。默认按全局保留期删, 可用
        #     log_retention_days 单独配更久（留排查）; 0 = 跟随全局。
        #     独立 try/commit 做错误隔离: 即便失败也不影响已完成的数据清理与后续孤儿清理。
        log_days = cfg["log_retention_days"]
        log_cutoff = (now - timedelta(days=log_days)) if log_days > 0 else base_cutoff
        log_deleted = 0
        try:
            from backend.models.mes_models import ScanLog, MESCommLog, ExternalDeviceLog
            for LogModel, log_name in (
                (ScanLog, "扫码日志"),
                (MESCommLog, "MES通讯日志"),
                (ExternalDeviceLog, "外设日志"),
            ):
                log_deleted += db.query(LogModel).filter(
                    LogModel.created_at < log_cutoff
                ).delete(synchronize_session=False)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"[自动清理] 清理过程日志失败: {e}")
        if log_deleted:
            print(f"[自动清理] 过程日志: 删除 {log_deleted} 行 (扫码/MES通讯/外设)")

        # 2d. A5 层1: 导出文件按"导出台账"精准清理（无论客户把地址改到哪都安全）。
        #     每次导出都在 ExportRunLog 记了确切 output_file。这里只删:
        #       (a) 台账已过期(triggered_at < cutoff)；
        #       (b) 且无更近台账也指向同一文件(防 overwrite 同名覆盖误删活跃文件)；
        #       (c) 且文件本身 mtime 也已过期(双保险, 防文件近期被覆盖更新)。
        #     这样只删我们自己产出、确认非活跃的导出文件, 绝不碰目录里客户其他东西。
        #     导出保留期可用 export_retention_days 单独配; 0 = 跟随全局。
        exp_days = cfg["export_retention_days"]
        exp_cutoff = (now - timedelta(days=exp_days)) if exp_days > 0 else base_cutoff
        exp_cutoff_ts = exp_cutoff.timestamp()
        exp_file_deleted = exp_log_deleted = 0
        try:
            from backend.models.export_models import ExportRunLog
            old_success = db.query(ExportRunLog).filter(
                ExportRunLog.triggered_at < exp_cutoff,
                ExportRunLog.status == "success",
            ).all()
            for lg in old_success:
                fp = lg.output_file
                if not (fp and os.path.isfile(fp)):
                    continue
                # (b) 近期台账仍指向同一文件 → 文件活跃, 只清旧台账不删文件
                recent_ref = db.query(ExportRunLog.id).filter(
                    ExportRunLog.output_file == fp,
                    ExportRunLog.triggered_at >= exp_cutoff,
                ).first()
                if recent_ref:
                    continue
                # (c) 文件本身近期被更新过 → 不删
                try:
                    if os.path.getmtime(fp) >= exp_cutoff_ts:
                        continue
                    os.remove(fp)
                    exp_file_deleted += 1
                except Exception as e:
                    print(f"[自动清理] 删除导出文件失败: {fp}, {e}")
            # 过期台账行整体清理(含 failed/skipped, 防 export_run_logs 表膨胀)
            exp_log_deleted = db.query(ExportRunLog).filter(
                ExportRunLog.triggered_at < exp_cutoff,
            ).delete(synchronize_session=False)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"[自动清理] 清理导出台账失败: {e}")
        if exp_file_deleted or exp_log_deleted:
            print(f"[自动清理] 导出文件: 删除 {exp_file_deleted} 个文件, {exp_log_deleted} 条台账")

        # 2e. A5 层3: 对数据中心登记的"导出根目录"做托底扫描（默认关）。
        #     仅当客户显式开启托底 且 登记了导出根目录时执行。这是客户专门准备的导出
        #     落地目录(主动授权), 但仍加硬护栏: 目录不在黑名单; 只删导出格式扩展名;
        #     只删文件不删目录; 不递归子目录; mtime 过期才删。
        exp_scan_deleted = 0
        scan_dir = cfg["export_cleanup_dir"]
        if cfg["export_cleanup_scan_dir"] and scan_dir:
            if _norm_cleanup_dir(scan_dir) in _CLEANUP_DIR_BLACKLIST:
                print(f"[自动清理] 导出目录托底跳过(黑名单): {scan_dir}")
            elif not os.path.isdir(scan_dir):
                print(f"[自动清理] 导出目录托底跳过(不存在): {scan_dir}")
            else:
                for fname in os.listdir(scan_dir):
                    fpath = os.path.join(scan_dir, fname)
                    if not os.path.isfile(fpath):
                        continue  # 不删目录, 不递归子目录
                    if os.path.splitext(fname)[1].lower() not in _EXPORT_FILE_EXTS:
                        continue  # 只动导出格式文件, 客户其他文件一律不碰
                    try:
                        if os.path.getmtime(fpath) < exp_cutoff_ts:
                            os.remove(fpath)
                            exp_scan_deleted += 1
                    except Exception as e:
                        print(f"[自动清理] 删除导出目录文件失败: {fpath}, {e}")
        if exp_scan_deleted:
            print(f"[自动清理] 导出目录托底: 删除 {exp_scan_deleted} 个文件")

        # 2b. 录制目录孤儿文件
        known_paths = set()
        for (fp,) in db.query(VideoClip.file_path).all():
            if fp:
                known_paths.add(os.path.abspath(fp))

        orphan_deleted = 0
        for rec_dir in [settings.SESSION_VIDEO_DIR, settings.CYCLE_VIDEO_DIR, settings.STEP_VIDEO_DIR]:
            if not os.path.isdir(rec_dir):
                continue
            for fname in os.listdir(rec_dir):
                fpath = os.path.join(rec_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                if os.path.abspath(fpath) in known_paths:
                    continue
                try:
                    if os.path.getmtime(fpath) < cutoff_ts:
                        os.remove(fpath)
                        orphan_deleted += 1
                except Exception as e:
                    print(f"[自动清理] 删除孤儿文件失败: {fpath}, {e}")
        if orphan_deleted:
            print(f"[自动清理] 孤儿录制文件: 删除 {orphan_deleted} 个")

        # 3. 视频转换缓存
        cache_dir = os.path.join(settings.RECORDING_DIR, "cache")
        cache_deleted = 0
        if os.path.isdir(cache_dir):
            for fname in os.listdir(cache_dir):
                fpath = os.path.join(cache_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        if os.path.getmtime(fpath) < cutoff_ts:
                            os.remove(fpath)
                            cache_deleted += 1
                    except Exception:
                        pass
        if cache_deleted:
            print(f"[自动清理] 缓存文件: 删除 {cache_deleted} 个")

        # 4. 上传的检测视频源
        upload_deleted = 0
        if os.path.isdir(settings.VIDEO_UPLOAD_DIR):
            for fname in os.listdir(settings.VIDEO_UPLOAD_DIR):
                fpath = os.path.join(settings.VIDEO_UPLOAD_DIR, fname)
                if not os.path.isfile(fpath):
                    continue
                try:
                    if os.path.getmtime(fpath) < cutoff_ts:
                        os.remove(fpath)
                        upload_deleted += 1
                except Exception as e:
                    print(f"[自动清理] 删除上传视频失败: {fpath}, {e}")
        if upload_deleted:
            print(f"[自动清理] 上传视频: 删除 {upload_deleted} 个")

        total_files = deleted_files + orphan_deleted + cache_deleted + upload_deleted
        if total_files == 0 and session_count == 0:
            print("[自动清理] 没有过期数据需要清理")
        else:
            print(f"[自动清理] 总计删除文件: {total_files}")
        result_holder.update({
            "sessions": session_count, "cycles": cycle_count, "steps": step_count,
            "video_records": video_count, "files": total_files,
            "process_logs": log_deleted, "export_files": exp_file_deleted,
            "export_scan_files": exp_scan_deleted,
        })
    except Exception as e:
        db.rollback()
        print(f"[自动清理] 出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
    # A3: 删行后收缩 WAL（仅 SQLite），让磁盘真正回落
    _checkpoint_wal()
    # A7: 记录本次清理结果（仅真正执行了清理时）
    if result_holder:
        _record_cleanup_status(size_before, result_holder)


# A1: 清理互斥锁——防止"开机清理 / 每日固定时点清理 / 手动立即清理"三者并发删库
_cleanup_lock = threading.Lock()


def _perform_auto_cleanup_safe():
    """带互斥锁的清理入口。拿不到锁(已有清理在跑)就跳过本次，返回是否真正执行。"""
    if not _cleanup_lock.acquire(blocking=False):
        print("[自动清理] 已有清理在执行，本次跳过")
        return False
    try:
        _perform_auto_cleanup()
        return True
    finally:
        _cleanup_lock.release()


# ---- routes ----

@router.get("/backup/database")
def backup_database():
    """备份数据库文件，下载 sql_app.db"""
    db_path = os.path.abspath(os.path.join(settings.UPLOAD_DIR, "..", "sql_app.db"))
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="数据库文件不存在")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return FileResponse(
        path=db_path,
        filename=f"sql_app_backup_{timestamp}.db",
        media_type="application/octet-stream",
    )


@router.delete("/clear/all",
                dependencies=[Depends(require_perm("data.cleanup"))])
def clear_all_data(db: Session = Depends(get_db)):
    """清空所有历史数据：会话、周期、步骤、视频记录、录制文件、缓存、上传视频"""
    try:
        deleted_files = 0
        for vd in [settings.SESSION_VIDEO_DIR, settings.CYCLE_VIDEO_DIR, settings.STEP_VIDEO_DIR]:
            if os.path.isdir(vd):
                for fn in os.listdir(vd):
                    fp = os.path.join(vd, fn)
                    try:
                        if os.path.isfile(fp):
                            os.remove(fp)
                            deleted_files += 1
                    except Exception as e:
                        print(f"删除视频文件失败: {fp}, {e}")

        cache_dir = os.path.join(settings.RECORDING_DIR, "cache")
        if os.path.isdir(cache_dir):
            for fn in os.listdir(cache_dir):
                fp = os.path.join(cache_dir, fn)
                try:
                    if os.path.isfile(fp):
                        os.remove(fp)
                        deleted_files += 1
                except Exception:
                    pass

        upload_deleted = 0
        if os.path.isdir(settings.VIDEO_UPLOAD_DIR):
            for fn in os.listdir(settings.VIDEO_UPLOAD_DIR):
                fp = os.path.join(settings.VIDEO_UPLOAD_DIR, fn)
                try:
                    if os.path.isfile(fp):
                        os.remove(fp)
                        upload_deleted += 1
                except Exception as e:
                    print(f"删除上传视频失败: {fp}, {e}")

        step_count = db.query(StepRecord).delete(synchronize_session=False)
        video_count = db.query(VideoClip).delete(synchronize_session=False)
        cycle_count = db.query(DetectionCycle).delete(synchronize_session=False)
        session_count = db.query(DetectionSession).delete(synchronize_session=False)
        db.commit()
        _checkpoint_wal()  # A3: 清空全部数据后收缩 WAL

        return {
            "success": True,
            "message": "数据清理完成",
            "deleted": {
                "sessions": session_count, "cycles": cycle_count,
                "steps": step_count, "videos": video_count,
                "files": deleted_files, "upload_videos": upload_deleted,
            },
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")


class DateRangeCleanup(BaseModel):
    start_date: str
    end_date: str


@router.delete("/clear/range",
                dependencies=[Depends(require_perm("data.cleanup"))])
def clear_data_by_range(req: DateRangeCleanup, db: Session = Depends(get_db)):
    """删除指定日期范围内 (YYYY-MM-DD) 的历史数据"""
    try:
        start_dt = datetime.strptime(req.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(req.end_date, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式无效，请使用 YYYY-MM-DD")

    try:
        target_sessions = db.query(DetectionSession).filter(and_(
            DetectionSession.start_time >= start_dt,
            DetectionSession.start_time <= end_dt,
        )).all()
        if not target_sessions:
            return {"success": True, "message": "该日期范围内没有数据",
                    "deleted": {"sessions": 0, "cycles": 0, "steps": 0, "videos": 0, "files": 0}}

        sids = [s.id for s in target_sessions]
        cids = [c.id for c in db.query(DetectionCycle).filter(
            DetectionCycle.session_id.in_(sids)
        ).all()]

        target_videos = db.query(VideoClip).filter(and_(
            VideoClip.created_at >= start_dt,
            VideoClip.created_at <= end_dt,
        )).all()
        deleted_files = 0
        for v in target_videos:
            if v.file_path and os.path.isfile(v.file_path):
                try:
                    os.remove(v.file_path)
                    deleted_files += 1
                except Exception as e:
                    print(f"删除视频文件失败: {v.file_path}, {e}")

        step_count = 0
        if cids:
            step_count = db.query(StepRecord).filter(
                StepRecord.cycle_id.in_(cids)
            ).delete(synchronize_session=False)
        video_count = db.query(VideoClip).filter(and_(
            VideoClip.created_at >= start_dt, VideoClip.created_at <= end_dt,
        )).delete(synchronize_session=False)
        cycle_count = db.query(DetectionCycle).filter(
            DetectionCycle.session_id.in_(sids)
        ).delete(synchronize_session=False)
        session_count = db.query(DetectionSession).filter(
            DetectionSession.id.in_(sids)
        ).delete(synchronize_session=False)
        db.commit()
        _checkpoint_wal()  # A3: 范围清理后收缩 WAL

        return {
            "success": True,
            "message": f"已清理 {req.start_date} 至 {req.end_date} 的数据",
            "deleted": {
                "sessions": session_count, "cycles": cycle_count,
                "steps": step_count, "videos": video_count,
                "files": deleted_files,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")


class CleanupSettingsUpdate(BaseModel):
    retention_days: Optional[int] = None
    auto_cleanup: Optional[bool] = None
    video_split_ok_ng: Optional[bool] = None
    video_ok_retention_days: Optional[int] = None
    video_ng_retention_days: Optional[int] = None
    log_retention_days: Optional[int] = None
    export_retention_days: Optional[int] = None
    export_cleanup_dir: Optional[str] = None
    export_cleanup_scan_dir: Optional[bool] = None
    cleanup_daily_time: Optional[str] = None


def _set_system_config(db, key: str, value: str, desc: str):
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value, description=desc))


@router.get("/cleanup-settings")
def get_cleanup_settings(db: Session = Depends(get_db)):
    """获取数据清理设置（含 OK/NG 录像分开存策略）"""
    return _read_cleanup_settings(db)


@router.put("/cleanup-settings",
             dependencies=[Depends(require_perm("data.cleanup"))])
def update_cleanup_settings(req: CleanupSettingsUpdate, db: Session = Depends(get_db)):
    """更新数据清理设置"""
    if req.retention_days is not None:
        _set_system_config(db, "retention_days", str(req.retention_days), "数据保留天数")
    if req.auto_cleanup is not None:
        _set_system_config(db, "auto_cleanup", "true" if req.auto_cleanup else "false", "是否启用自动清理")
    if req.video_split_ok_ng is not None:
        _set_system_config(db, "video_split_ok_ng", "true" if req.video_split_ok_ng else "false", "录像按OK/NG分开保留")
    if req.video_ok_retention_days is not None:
        _set_system_config(db, "video_ok_retention_days", str(req.video_ok_retention_days), "OK录像保留天数")
    if req.video_ng_retention_days is not None:
        _set_system_config(db, "video_ng_retention_days", str(req.video_ng_retention_days), "NG录像保留天数")
    if req.log_retention_days is not None:
        _set_system_config(db, "log_retention_days", str(req.log_retention_days), "过程日志保留天数(0=跟随全局)")
    if req.export_retention_days is not None:
        _set_system_config(db, "export_retention_days", str(req.export_retention_days), "导出文件保留天数(0=跟随全局)")
    if req.export_cleanup_dir is not None:
        d = req.export_cleanup_dir.strip()
        if d and _norm_cleanup_dir(d) in _CLEANUP_DIR_BLACKLIST:
            raise HTTPException(status_code=400,
                                detail="该目录为系统/盘根目录，禁止设为导出清理目录")
        _set_system_config(db, "export_cleanup_dir", d, "数据中心登记的导出根目录(授权托底清理)")
    if req.export_cleanup_scan_dir is not None:
        _set_system_config(db, "export_cleanup_scan_dir",
                           "true" if req.export_cleanup_scan_dir else "false",
                           "是否对登记导出目录做托底扫描清理")
    if req.cleanup_daily_time is not None:
        t = req.cleanup_daily_time.strip()
        if t:
            import re
            if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", t):
                raise HTTPException(status_code=400, detail="时点格式应为 HH:MM (00:00~23:59)")
        _set_system_config(db, "cleanup_daily_time", t, "每日固定时点清理(HH:MM, 空=关闭)")
    db.commit()
    return _read_cleanup_settings(db)


@router.get("/cleanup/status")
def get_cleanup_status(db: Session = Depends(get_db)):
    """A7: 上次清理结果（时间 / 释放空间 / 明细），供设置页显示给客户。"""
    import json

    def _v(k):
        row = db.query(SystemConfig).filter(SystemConfig.key == k).first()
        return row.value if row else None

    summary = None
    raw = _v("last_cleanup_summary")
    if raw:
        try:
            summary = json.loads(raw)
        except Exception:
            summary = None
    freed = _v("last_cleanup_freed_bytes")
    try:
        freed_bytes = int(freed) if freed else 0
    except (TypeError, ValueError):
        freed_bytes = 0
    return {
        "last_cleanup_at": _v("last_cleanup_at"),
        "last_cleanup_freed_bytes": freed_bytes,
        "last_cleanup_freed_mb": round(freed_bytes / 1024 / 1024, 2),
        "last_cleanup_summary": summary,
    }


@router.get("/storage-info")
def get_storage_info():
    """获取存储空间信息"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _dir_size(path):
        size = 0
        if os.path.isdir(path):
            for dp, _, fns in os.walk(path):
                for f in fns:
                    try:
                        size += os.path.getsize(os.path.join(dp, f))
                    except OSError:
                        pass
        return size

    dir_sizes = {}
    db_path = os.path.join(base_dir, "sql_app.db")
    dir_sizes["database"] = os.path.getsize(db_path) if os.path.isfile(db_path) else 0
    dir_sizes["recordings"] = _dir_size(settings.RECORDING_DIR)
    dir_sizes["upload_videos"] = _dir_size(settings.VIDEO_UPLOAD_DIR)
    dir_sizes["upload_models"] = _dir_size(settings.MODEL_UPLOAD_DIR)

    total_size = sum(dir_sizes.values())
    disk = shutil.disk_usage(base_dir)
    return {
        "data_size": total_size,
        "data_size_mb": round(total_size / 1024 / 1024, 2),
        "breakdown": {k: round(v / 1024 / 1024, 2) for k, v in dir_sizes.items()},
        "disk_total": disk.total,
        "disk_used": disk.used,
        "disk_free": disk.free,
        "disk_total_gb": round(disk.total / 1024 / 1024 / 1024, 2),
        "disk_used_gb": round(disk.used / 1024 / 1024 / 1024, 2),
        "disk_free_gb": round(disk.free / 1024 / 1024 / 1024, 2),
        "disk_usage_percent": round(disk.used / disk.total * 100, 1),
    }


@router.post("/cleanup/vacuum",
              dependencies=[Depends(require_perm("data.cleanup"))])
def vacuum_database():
    """A2: 手动收缩数据库（VACUUM），把删除行留下的空洞还给磁盘。

    降级为"手动按钮"是有意为之：VACUUM 会独占锁库、需约等于库大小的临时磁盘、
    期间不能写，**绝不能在产线检测中途自动跑**。这里:
      - 仅 SQLite 执行(PG 由其 autovacuum 负责);
      - 与自动清理共用互斥锁, 不并发;
      - VACUUM 前先把 WAL 落盘;
      - 数据库忙(检测运行中等)→ 返回 409 让用户停检测后再试, 绝不强占。
    """
    from backend.db.database import engine
    if engine.dialect.name != "sqlite":
        return {"success": False, "message": "当前数据库非 SQLite，无需手动收缩"}
    if not _cleanup_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有清理/收缩在执行，请稍后再试")
    try:
        size_before = _calc_data_size()
        _checkpoint_wal()  # 先把 WAL 落盘再 VACUUM
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute("VACUUM")
            cur.close()
            raw.commit()
        finally:
            raw.close()
        # WAL 模式下 VACUUM 把空闲页写进 WAL, 主库文件要再 checkpoint(TRUNCATE)
        # 才真正还盘, 否则磁盘体积不变(客户"清了没变小"的根因之一)
        _checkpoint_wal()
        size_after = _calc_data_size()
        freed = max(size_before - size_after, 0)
        return {
            "success": True,
            "freed_bytes": freed,
            "freed_mb": round(freed / 1024 / 1024, 2),
            "message": f"数据库已收缩，释放 {round(freed / 1024 / 1024, 2)} MB",
        }
    except Exception as e:
        msg = str(e)
        if "lock" in msg.lower() or "busy" in msg.lower():
            raise HTTPException(status_code=409,
                                detail="数据库忙（可能检测运行中），请停止检测后再收缩")
        raise HTTPException(status_code=500, detail=f"收缩失败: {msg}")
    finally:
        _cleanup_lock.release()


@router.post("/cleanup/run",
              dependencies=[Depends(require_perm("data.cleanup"))])
def run_cleanup_now():
    """立即执行一次自动清理（按保留天数）"""
    try:
        ran = _perform_auto_cleanup_safe()
        return {"success": True,
                "message": "清理已执行" if ran else "已有清理在执行，已跳过本次"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")
