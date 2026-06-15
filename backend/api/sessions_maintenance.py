"""
数据维护 API：备份、清理、清理设置、存储信息。
从 sessions.py 拆出。通过 sessions.router.include_router(router) 挂接。
"""
import os
import shutil
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


def _perform_auto_cleanup():
    """按保留期自动清理：周期(可按 OK/NG 分别保留) + 会话/步骤记录 + 录像文件 + 孤儿 + 缓存 + 过期上传视频。

    以"周期"为基本归属单位：一个周期过期 → 连同它的步骤记录、录像文件、录像记录一起删，
    彻底杜绝"周期记录删了、录像还在"的孤儿（修历史口径不一致 bug）。
    开启 OK/NG 分开存时，OK / NG 周期各用自己的保留期；无结果标记的旧周期走全局保留天数。
    会话仅在其名下已无剩余周期且自身已过全局保留期时才删——这样 NG 周期长留时，承载它的
    会话（回放需要的父记录）也会被保住。
    """
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
    except Exception as e:
        db.rollback()
        print(f"[自动清理] 出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


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
    db.commit()
    return _read_cleanup_settings(db)


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


@router.post("/cleanup/run",
              dependencies=[Depends(require_perm("data.cleanup"))])
def run_cleanup_now():
    """立即执行一次自动清理（按保留天数）"""
    try:
        _perform_auto_cleanup()
        return {"success": True, "message": "清理已执行"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")
