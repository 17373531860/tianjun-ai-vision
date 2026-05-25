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
from sqlalchemy import and_
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.core.config import settings
from backend.db.database import get_db, SessionLocal
from backend.models.models import (
    DetectionSession, DetectionCycle, StepRecord,
    VideoClip, SystemConfig,
)

router = APIRouter()


# ---- internal helpers (also used by main.py 启动时调用) ----

def _get_cleanup_settings_from_db():
    """从数据库读取清理设置"""
    db = SessionLocal()
    try:
        retention_row = db.query(SystemConfig).filter(SystemConfig.key == "retention_days").first()
        auto_row = db.query(SystemConfig).filter(SystemConfig.key == "auto_cleanup").first()
        retention_days = int(retention_row.value) if retention_row and retention_row.value else 30
        auto_cleanup = (auto_row.value == "true") if auto_row else True
        return retention_days, auto_cleanup
    except Exception:
        return 30, True
    finally:
        db.close()


def _perform_auto_cleanup():
    """按保留天数自动清理：DB记录 + 录制文件 + 孤儿文件 + 缓存 + 过期上传视频。"""
    retention_days, auto_cleanup = _get_cleanup_settings_from_db()
    if not auto_cleanup or retention_days <= 0:
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    cutoff_ts = cutoff.timestamp()
    print(f"[自动清理] 开始清理 {retention_days} 天前的数据 (截止: {cutoff.strftime('%Y-%m-%d %H:%M:%S')})")

    db = SessionLocal()
    try:
        # 1. DB 记录 + 关联视频文件
        old_sessions = db.query(DetectionSession).filter(
            DetectionSession.start_time < cutoff
        ).all()

        session_count = cycle_count = step_count = video_count = deleted_files = 0

        if old_sessions:
            old_sids = [s.id for s in old_sessions]
            old_cycles = db.query(DetectionCycle).filter(
                DetectionCycle.session_id.in_(old_sids)
            ).all()
            old_cids = [c.id for c in old_cycles]

            old_videos = db.query(VideoClip).filter(VideoClip.created_at < cutoff).all()
            for v in old_videos:
                if v.file_path and os.path.isfile(v.file_path):
                    try:
                        os.remove(v.file_path)
                        deleted_files += 1
                    except Exception as e:
                        print(f"[自动清理] 删除文件失败: {v.file_path}, {e}")

            if old_cids:
                step_count = db.query(StepRecord).filter(
                    StepRecord.cycle_id.in_(old_cids)
                ).delete(synchronize_session=False)

            video_count = db.query(VideoClip).filter(
                VideoClip.created_at < cutoff
            ).delete(synchronize_session=False)
            cycle_count = db.query(DetectionCycle).filter(
                DetectionCycle.session_id.in_(old_sids)
            ).delete(synchronize_session=False)
            session_count = db.query(DetectionSession).filter(
                DetectionSession.id.in_(old_sids)
            ).delete(synchronize_session=False)

            db.commit()

        print(f"[自动清理] 数据库: {session_count}个会话, {cycle_count}个周期, {step_count}条步骤, {video_count}个视频记录, {deleted_files}个关联文件")

        # 2. 录制目录孤儿文件
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


@router.get("/cleanup-settings")
def get_cleanup_settings(db: Session = Depends(get_db)):
    """获取数据清理设置"""
    rr = db.query(SystemConfig).filter(SystemConfig.key == "retention_days").first()
    ar = db.query(SystemConfig).filter(SystemConfig.key == "auto_cleanup").first()
    return {
        "retention_days": int(rr.value) if rr and rr.value else 30,
        "auto_cleanup": (ar.value == "true") if ar else True,
    }


@router.put("/cleanup-settings",
             dependencies=[Depends(require_perm("data.cleanup"))])
def update_cleanup_settings(req: CleanupSettingsUpdate, db: Session = Depends(get_db)):
    """更新数据清理设置"""
    if req.retention_days is not None:
        row = db.query(SystemConfig).filter(SystemConfig.key == "retention_days").first()
        if row:
            row.value = str(req.retention_days)
        else:
            db.add(SystemConfig(key="retention_days", value=str(req.retention_days), description="数据保留天数"))
    if req.auto_cleanup is not None:
        row = db.query(SystemConfig).filter(SystemConfig.key == "auto_cleanup").first()
        v = "true" if req.auto_cleanup else "false"
        if row:
            row.value = v
        else:
            db.add(SystemConfig(key="auto_cleanup", value=v, description="是否启用自动清理"))
    db.commit()

    rr = db.query(SystemConfig).filter(SystemConfig.key == "retention_days").first()
    ar = db.query(SystemConfig).filter(SystemConfig.key == "auto_cleanup").first()
    return {
        "retention_days": int(rr.value) if rr and rr.value else 30,
        "auto_cleanup": (ar.value == "true") if ar else True,
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


@router.post("/cleanup/run",
              dependencies=[Depends(require_perm("data.cleanup"))])
def run_cleanup_now():
    """立即执行一次自动清理（按保留天数）"""
    try:
        _perform_auto_cleanup()
        return {"success": True, "message": "清理已执行"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")
