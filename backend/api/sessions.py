"""
检测会话管理 API
管理设备启动/关闭时间、检测周期、步骤记录和视频
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, desc
from typing import List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel
import uuid
import os
import csv
import io
import subprocess
import tempfile
import shutil

from backend.db.database import get_db
from backend.models.models import (
    DetectionSession, DetectionCycle, StepRecord, 
    VideoClip, DataExportSetting, Project, SystemConfig, Operator
)
from backend.core.config import settings

router = APIRouter()


# ========== 步骤配置顺序辅助函数 ==========

def _get_step_order_map(db, project_id: int = None, session_id: int = None, cycle_id: int = None) -> dict:
    """获取步骤配置顺序映射 {step_label: config_index}"""
    pid = project_id
    if not pid and session_id:
        sess = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
        pid = sess.project_id if sess else None
    if not pid and cycle_id:
        cyc = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
        if cyc:
            sess = db.query(DetectionSession).filter(DetectionSession.id == cyc.session_id).first()
            pid = sess.project_id if sess else None
    if not pid:
        return {}
    proj = db.query(Project).filter(Project.id == pid).first()
    if proj and proj.steps_config:
        return {step.get('label'): idx for idx, step in enumerate(proj.steps_config)}
    return {}


MIN_VALID_STEP_DURATION = 0.1

def _filter_valid_steps(steps: list) -> list:
    """Filter out phantom steps caused by very brief false detections.
    Steps with duration < 0.1s or None are excluded."""
    return [s for s in steps if s.duration is not None and s.duration >= MIN_VALID_STEP_DURATION]


# ========== 自动清理 ==========

def _get_cleanup_settings_from_db():
    """从数据库读取清理设置"""
    from backend.db.database import SessionLocal
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
    """执行按保留天数的自动清理（数据库记录 + 录制文件 + 孤儿文件 + 缓存 + 过期上传视频）"""
    retention_days, auto_cleanup = _get_cleanup_settings_from_db()
    if not auto_cleanup or retention_days <= 0:
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    cutoff_ts = cutoff.timestamp()
    print(f"[自动清理] 开始清理 {retention_days} 天前的数据 (截止: {cutoff.strftime('%Y-%m-%d %H:%M:%S')})")

    from backend.db.database import SessionLocal
    db = SessionLocal()
    try:
        # ---- 1. 清理数据库记录和关联的录制文件 ----
        old_sessions = db.query(DetectionSession).filter(
            DetectionSession.start_time < cutoff
        ).all()

        session_count = 0
        cycle_count = 0
        step_count = 0
        video_count = 0
        deleted_files = 0

        if old_sessions:
            old_session_ids = [s.id for s in old_sessions]
            old_cycles = db.query(DetectionCycle).filter(
                DetectionCycle.session_id.in_(old_session_ids)
            ).all()
            old_cycle_ids = [c.id for c in old_cycles]

            old_videos = db.query(VideoClip).filter(
                VideoClip.created_at < cutoff
            ).all()

            for v in old_videos:
                if v.file_path and os.path.isfile(v.file_path):
                    try:
                        os.remove(v.file_path)
                        deleted_files += 1
                    except Exception as e:
                        print(f"[自动清理] 删除文件失败: {v.file_path}, {e}")

            if old_cycle_ids:
                step_count = db.query(StepRecord).filter(
                    StepRecord.cycle_id.in_(old_cycle_ids)
                ).delete(synchronize_session=False)

            video_count = db.query(VideoClip).filter(
                VideoClip.created_at < cutoff
            ).delete(synchronize_session=False)

            cycle_count = db.query(DetectionCycle).filter(
                DetectionCycle.session_id.in_(old_session_ids)
            ).delete(synchronize_session=False)

            session_count = db.query(DetectionSession).filter(
                DetectionSession.id.in_(old_session_ids)
            ).delete(synchronize_session=False)

            db.commit()

        print(f"[自动清理] 数据库: {session_count}个会话, {cycle_count}个周期, {step_count}条步骤, {video_count}个视频记录, {deleted_files}个关联文件")

        # ---- 2. 清理录制目录中的孤儿文件（不在数据库中 + 超过保留期的） ----
        known_paths = set()
        all_clips = db.query(VideoClip.file_path).all()
        for (fp,) in all_clips:
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
                abs_path = os.path.abspath(fpath)
                if abs_path in known_paths:
                    continue
                try:
                    mtime = os.path.getmtime(fpath)
                    if mtime < cutoff_ts:
                        os.remove(fpath)
                        orphan_deleted += 1
                except Exception as e:
                    print(f"[自动清理] 删除孤儿文件失败: {fpath}, {e}")

        if orphan_deleted:
            print(f"[自动清理] 孤儿录制文件: 删除 {orphan_deleted} 个")

        # ---- 3. 清理视频转换缓存 ----
        cache_dir = os.path.join(settings.RECORDING_DIR, "cache")
        cache_deleted = 0
        if os.path.isdir(cache_dir):
            for fname in os.listdir(cache_dir):
                fpath = os.path.join(cache_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        mtime = os.path.getmtime(fpath)
                        if mtime < cutoff_ts:
                            os.remove(fpath)
                            cache_deleted += 1
                    except Exception:
                        pass
        if cache_deleted:
            print(f"[自动清理] 缓存文件: 删除 {cache_deleted} 个")

        # ---- 4. 清理上传的视频（用户上传的检测用视频源，超过保留期的） ----
        upload_video_dir = settings.VIDEO_UPLOAD_DIR
        upload_deleted = 0
        if os.path.isdir(upload_video_dir):
            for fname in os.listdir(upload_video_dir):
                fpath = os.path.join(upload_video_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                try:
                    mtime = os.path.getmtime(fpath)
                    if mtime < cutoff_ts:
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


# ========== FFmpeg 路径查找 ==========
def get_ffmpeg_path():
    """获取 FFmpeg 可执行文件路径"""
    import sys
    
    possible_paths = []
    
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
        possible_paths.append(os.path.join(base_dir, 'resources', 'ffmpeg', 'ffmpeg.exe'))
        possible_paths.append(os.path.join(base_dir, 'resources', 'ffmpeg', 'ffmpeg'))
        possible_paths.append(os.path.join(base_dir, '..', 'resources', 'ffmpeg', 'ffmpeg.exe'))
    
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    possible_paths.append(os.path.join(project_root, 'ffmpeg', 'ffmpeg.exe'))
    possible_paths.append(os.path.join(project_root, 'ffmpeg', 'ffmpeg'))
    
    for path in possible_paths:
        if os.path.isfile(path):
            return path
    
    return 'ffmpeg'

_FFMPEG_PATH = None

def get_cached_ffmpeg_path():
    global _FFMPEG_PATH
    if _FFMPEG_PATH is None:
        _FFMPEG_PATH = get_ffmpeg_path()
    return _FFMPEG_PATH


# ============ Pydantic 模型 ============

class SessionCreate(BaseModel):
    project_id: int


class SessionResponse(BaseModel):
    id: int
    session_uuid: str
    project_id: int
    project_name: str = ""
    start_time: str
    end_time: Optional[str] = None
    total_cycles: int = 0
    good_cycles: int = 0
    ng_cycles: int = 0
    counters_snapshot: Optional[dict] = None
    avg_cycle_time: float = 0
    min_cycle_time: Optional[float] = None
    max_cycle_time: Optional[float] = None
    video_id: Optional[str] = None
    status: str = "running"
    channel_id: int = 0
    operator_id: Optional[int] = None
    operator_name: Optional[str] = None

    class Config:
        from_attributes = True


class CycleResponse(BaseModel):
    id: int
    cycle_uuid: str
    cycle_number: int
    start_time: str
    end_time: Optional[str] = None
    duration: Optional[float] = None
    is_good: bool
    event_name: Optional[str] = None
    result_reason: Optional[str] = None
    step_sequence: Optional[list] = None
    video_id: Optional[str] = None
    operator_id: Optional[int] = None
    operator_name: Optional[str] = None
    serial_no: Optional[str] = None

    class Config:
        from_attributes = True


class StepRecordResponse(BaseModel):
    id: int
    record_uuid: str
    step_label: str
    step_name: Optional[str] = None
    step_order: int
    start_time: str
    end_time: Optional[str] = None
    duration: Optional[float] = None
    interval_from_prev: Optional[float] = None
    interval_to_next: Optional[float] = None  # 到下一步骤的间隔
    confidence: Optional[float] = None
    is_valid: bool
    video_id: Optional[str] = None

    class Config:
        from_attributes = True


class SessionOverview(BaseModel):
    """会话概览数据"""
    date: str
    sessions: List[SessionResponse]
    total_sessions: int
    total_cycles: int
    total_good: int
    total_ng: int
    avg_cycle_time: float
    counters_summary: dict


class ExportSettingResponse(BaseModel):
    # 记录选项
    record_step_duration: bool = True
    record_step_interval: bool = True
    record_cycle_duration: bool = True
    record_cycle_interval: bool = True
    record_avg_step_time: bool = True
    record_avg_cycle_time: bool = True
    record_counters: bool = True
    record_step_video: bool = False
    record_cycle_video: bool = False
    record_session_video: bool = False
    video_quality: str = "medium"
    video_fps: int = 30
    # 导出选项
    export_step_duration: bool = True
    export_step_interval: bool = True
    export_step_event: bool = True
    export_cycle_duration: bool = True
    export_cycle_interval: bool = True
    export_cycle_result: bool = True
    export_counters: bool = True
    export_session_info: bool = True


class ExportSettingUpdate(BaseModel):
    # 记录选项
    record_step_duration: Optional[bool] = None
    record_step_interval: Optional[bool] = None
    record_cycle_duration: Optional[bool] = None
    record_cycle_interval: Optional[bool] = None
    record_avg_step_time: Optional[bool] = None
    record_avg_cycle_time: Optional[bool] = None
    record_counters: Optional[bool] = None
    record_step_video: Optional[bool] = None
    record_cycle_video: Optional[bool] = None
    record_session_video: Optional[bool] = None
    video_quality: Optional[str] = None
    video_fps: Optional[int] = None
    # 导出选项
    export_step_duration: Optional[bool] = None
    export_step_interval: Optional[bool] = None
    export_step_event: Optional[bool] = None
    export_cycle_duration: Optional[bool] = None
    export_cycle_interval: Optional[bool] = None
    export_cycle_result: Optional[bool] = None
    export_counters: Optional[bool] = None
    export_session_info: Optional[bool] = None


# ============ 会话管理 API ============

@router.post("/sessions", response_model=SessionResponse)
def create_session(req: SessionCreate, db: Session = Depends(get_db)):
    """创建新的检测会话（设备启动时调用）"""
    project = db.query(Project).filter(Project.id == req.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    session = DetectionSession(
        session_uuid=str(uuid.uuid4())[:8],
        project_id=req.project_id,
        start_time=datetime.now(),
        status="running"
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    
    return SessionResponse(
        id=session.id,
        session_uuid=session.session_uuid,
        project_id=session.project_id,
        project_name=project.name,
        start_time=session.start_time.strftime("%Y-%m-%d %H:%M:%S"),
        status=session.status
    )


@router.put("/sessions/{session_id}/end")
def end_session(session_id: int, db: Session = Depends(get_db)):
    """结束检测会话（设备关闭时调用）"""
    session = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    session.end_time = datetime.now()
    session.status = "completed"
    
    # 计算汇总统计
    cycles = db.query(DetectionCycle).filter(DetectionCycle.session_id == session_id).all()
    if cycles:
        session.total_cycles = len(cycles)
        session.good_cycles = len([c for c in cycles if c.is_good])
        session.ng_cycles = session.total_cycles - session.good_cycles
        
        durations = [c.duration for c in cycles if c.duration is not None]
        if durations:
            session.avg_cycle_time = sum(durations) / len(durations)
            session.min_cycle_time = min(durations)
            session.max_cycle_time = max(durations)
    
    db.commit()
    
    return {"status": "success", "message": "会话已结束"}


@router.get("/sessions", response_model=List[SessionResponse])
def list_sessions(
    project_id: Optional[int] = None,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    channel_id: Optional[int] = None,
    operator_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """获取检测会话列表"""
    query = db.query(DetectionSession)
    
    if project_id:
        query = query.filter(DetectionSession.project_id == project_id)
    
    if channel_id is not None:
        query = query.filter(DetectionSession.channel_id == channel_id)

    if operator_id is not None:
        query = query.filter(DetectionSession.operator_id == operator_id)
    
    if date:
        # 单日查询
        query = query.filter(func.date(DetectionSession.start_time) == date)
    else:
        if start_date:
            query = query.filter(DetectionSession.start_time >= start_date)
        if end_date:
            query = query.filter(DetectionSession.start_time <= end_date + " 23:59:59")
    
    sessions = query.order_by(desc(DetectionSession.start_time)).offset(skip).limit(limit).all()
    
    result = []
    for session in sessions:
        project = db.query(Project).filter(Project.id == session.project_id).first()
        op_name = None
        op_id = getattr(session, 'operator_id', None)
        if op_id:
            op = db.query(Operator).filter(Operator.id == op_id).first()
            op_name = op.name if op else None
        result.append(SessionResponse(
            id=session.id,
            session_uuid=session.session_uuid,
            project_id=session.project_id,
            project_name=project.name if project else "Unknown",
            start_time=session.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=session.end_time.strftime("%Y-%m-%d %H:%M:%S") if session.end_time else None,
            total_cycles=session.total_cycles or 0,
            good_cycles=session.good_cycles or 0,
            ng_cycles=session.ng_cycles or 0,
            counters_snapshot=session.counters_snapshot,
            avg_cycle_time=session.avg_cycle_time or 0,
            min_cycle_time=session.min_cycle_time,
            max_cycle_time=session.max_cycle_time,
            video_id=session.video_id,
            status=session.status,
            channel_id=getattr(session, 'channel_id', 0) or 0,
            operator_id=op_id,
            operator_name=op_name,
        ))
    
    return result


@router.get("/sessions/dates")
def get_session_dates(
    project_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    channel_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """获取有会话记录的日期列表"""
    query = db.query(func.date(DetectionSession.start_time).label('date'))
    
    if project_id:
        query = query.filter(DetectionSession.project_id == project_id)
    if channel_id is not None:
        query = query.filter(DetectionSession.channel_id == channel_id)
    if start_date:
        query = query.filter(DetectionSession.start_time >= start_date)
    if end_date:
        query = query.filter(DetectionSession.start_time <= end_date + " 23:59:59")
    
    dates = query.distinct().order_by(desc(func.date(DetectionSession.start_time))).all()
    
    return {"dates": [str(d.date) for d in dates]}


@router.get("/sessions/by-date/{date}", response_model=SessionOverview)
def get_sessions_by_date(
    date: str,
    project_id: Optional[int] = None,
    start_hour: Optional[str] = None,
    end_hour: Optional[str] = None,
    channel_id: Optional[int] = None,
    shift: Optional[str] = None,
    operator_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """获取指定日期的会话概览（班次过滤基于 cycle 级别）"""
    from sqlalchemy import or_

    use_shift = bool(start_hour and end_hour)

    # ---------- 查询会话（日期级别，不做小时过滤） ----------
    sess_query = db.query(DetectionSession).filter(
        func.date(DetectionSession.start_time) == date
    )
    if use_shift and start_hour > end_hour:
        next_date = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        sess_query = db.query(DetectionSession).filter(
            or_(
                func.date(DetectionSession.start_time) == date,
                func.date(DetectionSession.start_time) == next_date,
            )
        )
    if project_id:
        sess_query = sess_query.filter(DetectionSession.project_id == project_id)
    if channel_id is not None:
        sess_query = sess_query.filter(DetectionSession.channel_id == channel_id)
    if shift and shift in ('day', 'night'):
        sess_query = sess_query.filter(DetectionSession.shift_label == shift)
    if operator_id is not None:
        sess_query = sess_query.filter(DetectionSession.operator_id == operator_id)
    sessions = sess_query.order_by(DetectionSession.start_time).all()

    if not use_shift:
        # --- 无班次过滤：使用 session 预聚合（原有逻辑） ---
        total_sessions = len(sessions)
        total_cycles = sum(s.total_cycles or 0 for s in sessions)
        total_good = sum(s.good_cycles or 0 for s in sessions)
        total_ng = sum(s.ng_cycles or 0 for s in sessions)
        ct = [s.avg_cycle_time for s in sessions if s.avg_cycle_time]
        avg_cycle_time = sum(ct) / len(ct) if ct else 0
        counters_summary = {}
        for s in sessions:
            if s.counters_snapshot:
                for k, v in s.counters_snapshot.items():
                    counters_summary[k] = counters_summary.get(k, 0) + v
    else:
        # --- 班次过滤：按 cycle.start_time 归属（谁在哪个班次开始操作就算哪个班次） ---
        session_ids = [s.id for s in sessions]
        if not session_ids:
            return SessionOverview(
                date=date, sessions=[], total_sessions=0,
                total_cycles=0, total_good=0, total_ng=0,
                avg_cycle_time=0, counters_summary={},
            )
        cycle_query = db.query(DetectionCycle).filter(
            DetectionCycle.session_id.in_(session_ids)
        )
        cycle_time_col = func.strftime('%H:%M', DetectionCycle.start_time)
        if start_hour <= end_hour:
            cycle_query = cycle_query.filter(
                and_(
                    func.date(DetectionCycle.start_time) == date,
                    cycle_time_col >= start_hour,
                    cycle_time_col < end_hour,
                )
            )
        else:
            next_date = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            cycle_query = cycle_query.filter(
                or_(
                    and_(func.date(DetectionCycle.start_time) == date, cycle_time_col >= start_hour),
                    and_(func.date(DetectionCycle.start_time) == next_date, cycle_time_col < end_hour),
                )
            )
        filtered_cycles = cycle_query.all()

        total_cycles = len(filtered_cycles)
        total_good = sum(1 for c in filtered_cycles if c.is_good)
        total_ng = total_cycles - total_good
        durations = [c.duration for c in filtered_cycles if c.duration]
        avg_cycle_time = sum(durations) / len(durations) if durations else 0

        involved_session_ids = set(c.session_id for c in filtered_cycles)
        sessions = [s for s in sessions if s.id in involved_session_ids]
        total_sessions = len(sessions)

        counters_summary = {
            '总产量': total_cycles,
            '合格总数': total_good,
            '不良总数': total_ng,
        }
        ng_step_counts = {}
        for c in filtered_cycles:
            if not c.is_good and c.step_sequence:
                steps_config = None
                if project_id:
                    proj = db.query(Project).filter(Project.id == project_id).first()
                    if proj:
                        steps_config = proj.steps_config
                if steps_config:
                    expected = set(s.get('label') for s in steps_config if s.get('label') and not s.get('is_backup'))
                    actual = set(c.step_sequence) if isinstance(c.step_sequence, list) else set()
                    for missing_step in (expected - actual):
                        ng_step_counts[missing_step] = ng_step_counts.get(missing_step, 0) + 1
        if ng_step_counts:
            total_ng_steps = sum(ng_step_counts.values())
            counters_summary['NG步骤'] = total_ng_steps

    # ---------- 构造 session 列表响应 ----------
    if use_shift:
        session_cycle_map = {}
        for c in filtered_cycles:
            session_cycle_map.setdefault(c.session_id, []).append(c)

    session_responses = []
    for session in sessions:
        project = db.query(Project).filter(Project.id == session.project_id).first()
        if use_shift:
            s_cycles = session_cycle_map.get(session.id, [])
            s_total = len(s_cycles)
            s_good = sum(1 for c in s_cycles if c.is_good)
            s_ng = s_total - s_good
            s_durs = [c.duration for c in s_cycles if c.duration]
            s_avg_ct = round(sum(s_durs) / len(s_durs), 2) if s_durs else 0
            s_counters = {'总产量': s_total, '合格总数': s_good, '不良总数': s_ng}
        else:
            s_total = session.total_cycles or 0
            s_good = session.good_cycles or 0
            s_ng = s_total - s_good
            s_avg_ct = session.avg_cycle_time or 0
            s_counters = session.counters_snapshot
        s_op_id = getattr(session, 'operator_id', None)
        s_op_name = None
        if s_op_id:
            s_op = db.query(Operator).filter(Operator.id == s_op_id).first()
            s_op_name = s_op.name if s_op else None
        session_responses.append(SessionResponse(
            id=session.id,
            session_uuid=session.session_uuid,
            project_id=session.project_id,
            project_name=project.name if project else "Unknown",
            start_time=session.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=session.end_time.strftime("%Y-%m-%d %H:%M:%S") if session.end_time else None,
            total_cycles=s_total,
            good_cycles=s_good,
            ng_cycles=s_ng,
            counters_snapshot=s_counters,
            avg_cycle_time=s_avg_ct,
            min_cycle_time=session.min_cycle_time,
            max_cycle_time=session.max_cycle_time,
            video_id=session.video_id,
            status=session.status,
            operator_id=s_op_id,
            operator_name=s_op_name,
        ))

    return SessionOverview(
        date=date,
        sessions=session_responses,
        total_sessions=total_sessions,
        total_cycles=total_cycles,
        total_good=total_good,
        total_ng=total_ng,
        avg_cycle_time=round(avg_cycle_time, 2),
        counters_summary=counters_summary
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: int, db: Session = Depends(get_db)):
    """获取单个会话详情"""
    session = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    project = db.query(Project).filter(Project.id == session.project_id).first()
    g_op_id = getattr(session, 'operator_id', None)
    g_op_name = None
    if g_op_id:
        g_op = db.query(Operator).filter(Operator.id == g_op_id).first()
        g_op_name = g_op.name if g_op else None
    
    return SessionResponse(
        id=session.id,
        session_uuid=session.session_uuid,
        project_id=session.project_id,
        project_name=project.name if project else "Unknown",
        start_time=session.start_time.strftime("%Y-%m-%d %H:%M:%S"),
        end_time=session.end_time.strftime("%Y-%m-%d %H:%M:%S") if session.end_time else None,
        total_cycles=session.total_cycles or 0,
        good_cycles=session.good_cycles or 0,
        ng_cycles=session.ng_cycles or 0,
        counters_snapshot=session.counters_snapshot,
        avg_cycle_time=session.avg_cycle_time or 0,
        min_cycle_time=session.min_cycle_time,
        max_cycle_time=session.max_cycle_time,
        video_id=session.video_id,
        status=session.status,
        operator_id=g_op_id,
        operator_name=g_op_name,
    )


# ============ 周期管理 API ============

@router.get("/sessions/{session_id}/cycles")
def get_session_cycles(
    session_id: int,
    skip: int = 0,
    limit: int = 50,
    operator_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """获取会话的周期（分页）"""
    base = db.query(DetectionCycle).filter(
        DetectionCycle.session_id == session_id
    )
    if operator_id is not None:
        base = base.filter(DetectionCycle.operator_id == operator_id)
    total = base.count()

    cycles = base.order_by(DetectionCycle.cycle_number).offset(skip).limit(limit).all()

    cycle_ids = [c.id for c in cycles]
    serial_map = {}
    if cycle_ids:
        try:
            from backend.models.mes_models import WorkpieceInspection, Workpiece
            rows = (
                db.query(WorkpieceInspection.cycle_id, Workpiece.serial_no)
                .join(Workpiece, WorkpieceInspection.workpiece_id == Workpiece.id)
                .filter(WorkpieceInspection.cycle_id.in_(cycle_ids))
                .all()
            )
            for cid, sn in rows:
                serial_map[cid] = sn
        except Exception:
            pass

    items = []
    for cycle in cycles:
        c_op_id = getattr(cycle, 'operator_id', None)
        c_op_name = None
        if c_op_id:
            c_op = db.query(Operator).filter(Operator.id == c_op_id).first()
            c_op_name = c_op.name if c_op else None
        items.append(CycleResponse(
            id=cycle.id,
            cycle_uuid=cycle.cycle_uuid,
            cycle_number=cycle.cycle_number,
            start_time=cycle.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=cycle.end_time.strftime("%Y-%m-%d %H:%M:%S") if cycle.end_time else None,
            duration=cycle.duration,
            is_good=cycle.is_good,
            event_name=cycle.event_name,
            result_reason=cycle.result_reason,
            step_sequence=cycle.step_sequence,
            video_id=cycle.video_id,
            operator_id=c_op_id,
            operator_name=c_op_name,
            serial_no=serial_map.get(cycle.id),
        ))

    return {"items": items, "total": total}


@router.get("/cycles/{cycle_id}", response_model=CycleResponse)
def get_cycle(cycle_id: int, db: Session = Depends(get_db)):
    """获取单个周期详情"""
    cycle = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="周期不存在")
    
    gc_op_id = getattr(cycle, 'operator_id', None)
    gc_op_name = None
    if gc_op_id:
        gc_op = db.query(Operator).filter(Operator.id == gc_op_id).first()
        gc_op_name = gc_op.name if gc_op else None
    
    return CycleResponse(
        id=cycle.id,
        cycle_uuid=cycle.cycle_uuid,
        cycle_number=cycle.cycle_number,
        start_time=cycle.start_time.strftime("%Y-%m-%d %H:%M:%S"),
        end_time=cycle.end_time.strftime("%Y-%m-%d %H:%M:%S") if cycle.end_time else None,
        duration=cycle.duration,
        is_good=cycle.is_good,
        event_name=cycle.event_name,
        result_reason=cycle.result_reason,
        step_sequence=cycle.step_sequence,
        video_id=cycle.video_id,
        operator_id=gc_op_id,
        operator_name=gc_op_name,
    )


@router.get("/cycles/{cycle_id}/steps", response_model=List[StepRecordResponse])
def get_cycle_steps(cycle_id: int, db: Session = Depends(get_db)):
    """获取周期的所有步骤记录（按配置顺序排列）
    
    Note: _filter_valid_steps is NOT applied here because _reconcile_step_records
    already ensures StepRecords match the cycle's step_sequence exactly.
    Filtering by duration would break this alignment.
    """
    steps = db.query(StepRecord).filter(
        StepRecord.cycle_id == cycle_id
    ).all()
    
    order_map = _get_step_order_map(db, cycle_id=cycle_id)
    steps.sort(key=lambda s: order_map.get(s.step_label, 999))
    
    result = []
    for step in steps:
        config_order = order_map.get(step.step_label, step.step_order - 1) + 1
        result.append(StepRecordResponse(
            id=step.id,
            record_uuid=step.record_uuid,
            step_label=step.step_label,
            step_name=step.step_name,
            step_order=config_order,
            start_time=step.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=step.end_time.strftime("%Y-%m-%d %H:%M:%S") if step.end_time else None,
            duration=step.duration,
            interval_from_prev=step.interval_from_prev,
            interval_to_next=step.interval_to_next,
            confidence=step.confidence,
            is_valid=step.is_valid,
            video_id=step.video_id
        ))
    
    return result


# ============ 视频 API ============

def convert_video_for_browser(input_path: str) -> str:
    """将视频转换为浏览器兼容的H.264格式"""
    # 创建转换后的文件路径
    cache_dir = os.path.join(settings.RECORDING_DIR, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    
    filename = os.path.basename(input_path)
    base_name = os.path.splitext(filename)[0]
    output_path = os.path.join(cache_dir, f"{base_name}_h264.mp4")
    
    # 如果已经转换过，直接返回缓存
    if os.path.exists(output_path):
        return output_path
    
    try:
        # 使用 ffmpeg 转换为 H.264 格式
        ffmpeg_path = get_cached_ffmpeg_path()
        cmd = [
            ffmpeg_path, "-y",
            "-i", input_path,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-movflags", "+faststart",  # 支持流式播放
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0 and os.path.exists(output_path):
            print(f"视频转换成功: {output_path}")
            return output_path
        else:
            print(f"视频转换失败: {result.stderr}")
            return input_path  # 转换失败则返回原文件
    except Exception as e:
        print(f"视频转换异常: {e}")
        return input_path


@router.get("/videos/{video_id}")
def get_video(video_id: str, db: Session = Depends(get_db)):
    """获取视频文件（自动转换为浏览器兼容格式）"""
    video = db.query(VideoClip).filter(VideoClip.video_uuid == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    
    if not os.path.exists(video.file_path):
        raise HTTPException(status_code=404, detail="视频文件不存在")
    
    # 转换视频为浏览器兼容格式
    compatible_path = convert_video_for_browser(video.file_path)
    
    return FileResponse(
        compatible_path,
        media_type="video/mp4",
        filename=video.file_name or f"video_{video_id}.mp4"
    )


@router.get("/videos")
def list_videos(
    clip_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """获取视频列表"""
    query = db.query(VideoClip)
    
    if clip_type:
        query = query.filter(VideoClip.clip_type == clip_type)
    if start_date:
        query = query.filter(VideoClip.created_at >= start_date)
    if end_date:
        query = query.filter(VideoClip.created_at <= end_date + " 23:59:59")
    
    videos = query.order_by(desc(VideoClip.created_at)).offset(skip).limit(limit).all()
    
    return [{
        "id": v.id,
        "video_uuid": v.video_uuid,
        "clip_type": v.clip_type,
        "file_name": v.file_name,
        "file_size": v.file_size,
        "duration": v.duration,
        "created_at": v.created_at.strftime("%Y-%m-%d %H:%M:%S") if v.created_at else None
    } for v in videos]


# ============ 导出设置 API ============

def _build_export_response(setting) -> ExportSettingResponse:
    """构建导出设置响应"""
    return ExportSettingResponse(
        # 记录选项
        record_step_duration=setting.record_step_duration,
        record_step_interval=setting.record_step_interval,
        record_cycle_duration=setting.record_cycle_duration,
        record_cycle_interval=getattr(setting, 'record_cycle_interval', True),
        record_avg_step_time=setting.record_avg_step_time,
        record_avg_cycle_time=setting.record_avg_cycle_time,
        record_counters=setting.record_counters,
        record_step_video=setting.record_step_video,
        record_cycle_video=setting.record_cycle_video,
        record_session_video=setting.record_session_video,
        video_quality=setting.video_quality,
        video_fps=setting.video_fps,
        # 导出选项
        export_step_duration=getattr(setting, 'export_step_duration', True),
        export_step_interval=getattr(setting, 'export_step_interval', True),
        export_step_event=getattr(setting, 'export_step_event', True),
        export_cycle_duration=getattr(setting, 'export_cycle_duration', True),
        export_cycle_interval=getattr(setting, 'export_cycle_interval', True),
        export_cycle_result=getattr(setting, 'export_cycle_result', True),
        export_counters=getattr(setting, 'export_counters', True),
        export_session_info=getattr(setting, 'export_session_info', True),
    )


@router.get("/export-settings", response_model=ExportSettingResponse)
def get_export_settings(db: Session = Depends(get_db)):
    """获取导出设置"""
    setting = db.query(DataExportSetting).first()
    if not setting:
        # 创建默认设置
        setting = DataExportSetting()
        db.add(setting)
        db.commit()
        db.refresh(setting)
    
    return _build_export_response(setting)


@router.put("/export-settings", response_model=ExportSettingResponse)
def update_export_settings(req: ExportSettingUpdate, db: Session = Depends(get_db)):
    """更新导出设置"""
    setting = db.query(DataExportSetting).first()
    if not setting:
        setting = DataExportSetting()
        db.add(setting)
    
    # 更新非空字段
    for field, value in req.dict(exclude_none=True).items():
        if hasattr(setting, field):
            setattr(setting, field, value)
    
    db.commit()
    db.refresh(setting)
    
    return _build_export_response(setting)


# ============ CSV 导出 API ============

@router.get("/export/csv")
def export_csv(
    export_type: str = Query(..., description="导出类型: session, cycle, all"),
    session_id: Optional[int] = None,
    cycle_id: Optional[int] = None,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    week: Optional[str] = None,  # 格式: 2024-W01
    month: Optional[str] = None,  # 格式: 2024-01
    start_hour: Optional[str] = None,
    end_hour: Optional[str] = None,
    project_id: Optional[int] = Query(None, description="按项目过滤，None=全部项目"),
    channel_id: Optional[int] = Query(None, description="按工位过滤(0-based)，None=全部工位"),
    db: Session = Depends(get_db)
):
    """
    导出CSV报表，根据导出设置过滤数据

    export_type:
    - session: 导出单个会话的数据
    - cycle: 导出单个周期的数据
    - all: 导出所有数据（需要指定日期范围）

    v2.7.2: 新增 project_id / channel_id 过滤，避免不同项目/工位的数据混入同一份报表。
    """
    try:
        # 加载导出设置
        exp_setting = db.query(DataExportSetting).first()
        export_opts = {
            'session_info': getattr(exp_setting, 'export_session_info', True) if exp_setting else True,
            'counters': getattr(exp_setting, 'export_counters', True) if exp_setting else True,
            'cycle_result': getattr(exp_setting, 'export_cycle_result', True) if exp_setting else True,
            'cycle_duration': getattr(exp_setting, 'export_cycle_duration', True) if exp_setting else True,
            'cycle_interval': getattr(exp_setting, 'export_cycle_interval', True) if exp_setting else True,
            'step_duration': getattr(exp_setting, 'export_step_duration', True) if exp_setting else True,
            'step_interval': getattr(exp_setting, 'export_step_interval', True) if exp_setting else True,
            'step_event': getattr(exp_setting, 'export_step_event', True) if exp_setting else True,
        }
        
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        
        # 根据周或月解析日期范围
        if week:
            try:
                year, week_num = week.split('-W')
                year = int(year)
                week_num = int(week_num)
                from datetime import date as date_type
                jan_4 = date_type(year, 1, 4)
                days_to_monday = jan_4.weekday()
                first_monday = jan_4 - timedelta(days=days_to_monday)
                target_monday = first_monday + timedelta(weeks=week_num - 1)
                target_sunday = target_monday + timedelta(days=6)
                start_date = target_monday.strftime("%Y-%m-%d")
                end_date = target_sunday.strftime("%Y-%m-%d")
            except Exception as e:
                print(f"周格式解析失败: {week}, 错误: {e}")
        
        if month:
            try:
                year, month_num = month.split('-')
                start_date = f"{year}-{month_num}-01"
                if int(month_num) == 12:
                    end_date = f"{int(year)+1}-01-01"
                else:
                    end_date = f"{year}-{int(month_num)+1:02d}-01"
            except ValueError as e:
                print(f"月格式解析失败: {month}, 错误: {e}")
        
        if export_type == "session" and session_id:
            # 导出单个会话
            session = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
            if not session:
                raise HTTPException(status_code=404, detail="会话不存在")

            # v2.7.2: 会话归档信息里显式写出项目+工位
            sess_proj = db.query(Project).filter(Project.id == session.project_id).first()
            sess_proj_name = sess_proj.name if sess_proj else "Unknown"
            sess_ch_label = f"工位{(session.channel_id or 0) + 1}"

            if export_opts['session_info']:
                writer.writerow(["会话信息"])
                writer.writerow(["会话ID", "项目", "工位", "开始时间", "结束时间", "总周期数", "合格数", "不良数", "平均周期时间"])
                writer.writerow([
                    session.session_uuid,
                    sess_proj_name,
                    sess_ch_label,
                    session.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    session.end_time.strftime("%Y-%m-%d %H:%M:%S") if session.end_time else "",
                    session.total_cycles or 0,
                    session.good_cycles or 0,
                    session.ng_cycles or 0,
                    f"{session.avg_cycle_time:.2f}s" if session.avg_cycle_time else ""
                ])
                writer.writerow([])
            
            if export_opts['counters'] and session.counters_snapshot:
                writer.writerow(["计数器统计"])
                writer.writerow(["计数器名称", "数值"])
                for key, value in session.counters_snapshot.items():
                    writer.writerow([key, value])
                writer.writerow([])
            
            cycles = db.query(DetectionCycle).filter(DetectionCycle.session_id == session_id).all()
            if cycles:
                writer.writerow(["周期详情"])
                # 动态构建表头
                headers = ["周期序号", "开始时间", "结束时间"]
                if export_opts['cycle_duration']:
                    headers.append("耗时(秒)")
                if export_opts['cycle_interval']:
                    headers.append("周期间隔(秒)")
                if export_opts['cycle_result']:
                    headers.extend(["结果", "事件"])
                headers.append("步骤序列")
                writer.writerow(headers)
                
                for cycle in cycles:
                    row = [
                        cycle.cycle_number,
                        cycle.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                        cycle.end_time.strftime("%Y-%m-%d %H:%M:%S") if cycle.end_time else ""
                    ]
                    if export_opts['cycle_duration']:
                        row.append(f"{cycle.duration:.2f}" if cycle.duration else "")
                    if export_opts['cycle_interval']:
                        interval = getattr(cycle, 'interval_to_next', None)
                        row.append(f"{interval:.2f}" if interval else "")
                    if export_opts['cycle_result']:
                        row.extend([
                            "合格" if cycle.is_good else "不良",
                            cycle.event_name or ""
                        ])
                    row.append(" -> ".join(cycle.step_sequence) if cycle.step_sequence else "")
                    writer.writerow(row)
                
                # 导出步骤详情
                if export_opts['step_duration'] or export_opts['step_interval'] or export_opts['step_event']:
                    writer.writerow([])
                    writer.writerow(["步骤详情"])
                    step_headers = ["周期序号", "步骤序号", "步骤名称", "开始时间"]
                    if export_opts['step_duration']:
                        step_headers.append("耗时(秒)")
                    if export_opts['step_interval']:
                        step_headers.append("到下步间隔(秒)")
                    writer.writerow(step_headers)
                    
                    order_map = _get_step_order_map(db, session_id=session_id)
                    for cycle in cycles:
                        steps = db.query(StepRecord).filter(StepRecord.cycle_id == cycle.id).all()
                        steps.sort(key=lambda s: order_map.get(s.step_label, 999))
                        for step in steps:
                            config_order = order_map.get(step.step_label, step.step_order - 1) + 1
                            row = [
                                cycle.cycle_number,
                                config_order,
                                step.step_name or step.step_label,
                                step.start_time.strftime("%Y-%m-%d %H:%M:%S")
                            ]
                            if export_opts['step_duration']:
                                row.append(f"{step.duration:.2f}" if step.duration else "")
                            if export_opts['step_interval']:
                                interval = getattr(step, 'interval_to_next', None) or step.interval_from_prev
                                row.append(f"{interval:.2f}" if interval else "")
                            writer.writerow(row)
        
        elif export_type == "cycle" and cycle_id:
            # 导出单个周期
            cycle = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
            if not cycle:
                raise HTTPException(status_code=404, detail="周期不存在")
            
            writer.writerow(["周期信息"])
            headers = ["周期ID", "开始时间", "结束时间"]
            if export_opts['cycle_duration']:
                headers.append("耗时(秒)")
            if export_opts['cycle_result']:
                headers.extend(["结果", "事件", "原因"])
            writer.writerow(headers)
            
            row = [
                cycle.cycle_uuid,
                cycle.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                cycle.end_time.strftime("%Y-%m-%d %H:%M:%S") if cycle.end_time else ""
            ]
            if export_opts['cycle_duration']:
                row.append(f"{cycle.duration:.2f}" if cycle.duration else "")
            if export_opts['cycle_result']:
                row.extend([
                    "合格" if cycle.is_good else "不良",
                    cycle.event_name or "",
                    cycle.result_reason or ""
                ])
            writer.writerow(row)
            writer.writerow([])
            
            steps = db.query(StepRecord).filter(StepRecord.cycle_id == cycle_id).all()
            order_map = _get_step_order_map(db, cycle_id=cycle_id)
            steps.sort(key=lambda s: order_map.get(s.step_label, 999))
            if steps:
                writer.writerow(["步骤详情"])
                step_headers = ["序号", "步骤名称", "开始时间", "结束时间"]
                if export_opts['step_duration']:
                    step_headers.append("耗时(秒)")
                if export_opts['step_interval']:
                    step_headers.append("到下步间隔(秒)")
                writer.writerow(step_headers)
                
                for step in steps:
                    config_order = order_map.get(step.step_label, step.step_order - 1) + 1
                    row = [
                        config_order,
                        step.step_name or step.step_label,
                        step.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                        step.end_time.strftime("%Y-%m-%d %H:%M:%S") if step.end_time else ""
                    ]
                    if export_opts['step_duration']:
                        row.append(f"{step.duration:.2f}" if step.duration else "")
                    if export_opts['step_interval']:
                        interval = getattr(step, 'interval_to_next', None) or step.interval_from_prev
                        row.append(f"{interval:.2f}" if interval else "")
                    writer.writerow(row)
        
        else:
            # 导出日期范围内的所有数据
            query = db.query(DetectionSession)

            if date:
                query = query.filter(func.date(DetectionSession.start_time) == date)
            elif start_date and end_date:
                query = query.filter(DetectionSession.start_time >= start_date)
                query = query.filter(DetectionSession.start_time <= end_date + " 23:59:59")

            # v2.7.2: 支持按项目/工位过滤
            if project_id is not None:
                query = query.filter(DetectionSession.project_id == project_id)
            if channel_id is not None:
                query = query.filter(DetectionSession.channel_id == channel_id)

            if start_hour and end_hour:
                # 夜班跨日时，也需要查次日的会话
                if start_hour > end_hour and date:
                    from sqlalchemy import or_
                    next_d = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                    query = query.filter(
                        or_(
                            func.date(DetectionSession.start_time) == date,
                            func.date(DetectionSession.start_time) == next_d,
                        )
                    )

            sessions = query.order_by(DetectionSession.start_time).all()
            shift_cycle_ids = set()

            if start_hour and end_hour and sessions:
                sess_ids = [s.id for s in sessions]
                cq = db.query(DetectionCycle).filter(DetectionCycle.session_id.in_(sess_ids))
                ct_col = func.strftime('%H:%M', DetectionCycle.start_time)
                if start_hour <= end_hour:
                    cq = cq.filter(and_(
                        func.date(DetectionCycle.start_time) == (date or start_date),
                        ct_col >= start_hour, ct_col < end_hour,
                    ))
                else:
                    from sqlalchemy import or_
                    base_d = date or start_date
                    next_d = (datetime.strptime(base_d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                    cq = cq.filter(or_(
                        and_(func.date(DetectionCycle.start_time) == base_d, ct_col >= start_hour),
                        and_(func.date(DetectionCycle.start_time) == next_d, ct_col < end_hour),
                    ))
                shift_cycle_ids = set(c.id for c in cq.all())
                involved = set(c.session_id for c in cq.all()) if shift_cycle_ids else set()
                sessions = [s for s in sessions if s.id in involved]
            
            # v2.7.2: 元数据里显式列出项目/工位过滤条件
            proj_label = "全部项目"
            if project_id is not None:
                proj_obj = db.query(Project).filter(Project.id == project_id).first()
                proj_label = f"{proj_obj.name}(id={project_id})" if proj_obj else f"项目{project_id}"
            ch_label = "全部工位" if channel_id is None else f"工位{channel_id + 1}"

            writer.writerow(["数据导出报表"])
            writer.writerow(["导出时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
            writer.writerow(["日期范围", f"{start_date or date or '全部'} 至 {end_date or date or '全部'}"])
            writer.writerow(["项目", proj_label])
            writer.writerow(["工位", ch_label])
            writer.writerow([])

            if export_opts['session_info']:
                writer.writerow(["会话列表"])
                writer.writerow(["会话ID", "项目", "工位", "开始时间", "结束时间", "周期数", "合格", "不良", "平均CT"])

                for session in sessions:
                    project = db.query(Project).filter(Project.id == session.project_id).first()
                    writer.writerow([
                        session.session_uuid,
                        project.name if project else "Unknown",
                        f"工位{(session.channel_id or 0) + 1}",
                        session.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                        session.end_time.strftime("%Y-%m-%d %H:%M:%S") if session.end_time else "",
                        session.total_cycles or 0,
                        session.good_cycles or 0,
                        session.ng_cycles or 0,
                        f"{session.avg_cycle_time:.2f}s" if session.avg_cycle_time else ""
                    ])

                writer.writerow([])
            
            for session in sessions:
                c_q = db.query(DetectionCycle).filter(DetectionCycle.session_id == session.id)
                if shift_cycle_ids:
                    c_q = c_q.filter(DetectionCycle.id.in_(shift_cycle_ids))
                cycles = c_q.all()
                if cycles:
                    # v2.7.2: 分组标题带项目名+工位号，合并导出时一眼能区分
                    sess_proj = db.query(Project).filter(Project.id == session.project_id).first()
                    sess_proj_name = sess_proj.name if sess_proj else "Unknown"
                    sess_ch = f"工位{(session.channel_id or 0) + 1}"
                    writer.writerow([f"[{sess_proj_name} / {sess_ch}] 会话 {session.session_uuid} 的周期详情"])
                    headers = ["周期序号", "开始时间"]
                    if export_opts['cycle_duration']:
                        headers.append("耗时(秒)")
                    if export_opts['cycle_interval']:
                        headers.append("周期间隔(秒)")
                    if export_opts['cycle_result']:
                        headers.extend(["结果", "事件"])
                    headers.append("步骤序列")
                    writer.writerow(headers)
                    
                    for cycle in cycles:
                        row = [
                            cycle.cycle_number,
                            cycle.start_time.strftime("%Y-%m-%d %H:%M:%S")
                        ]
                        if export_opts['cycle_duration']:
                            row.append(f"{cycle.duration:.2f}" if cycle.duration else "")
                        if export_opts['cycle_interval']:
                            interval = getattr(cycle, 'interval_to_next', None)
                            row.append(f"{interval:.2f}" if interval else "")
                        if export_opts['cycle_result']:
                            row.extend([
                                "OK" if cycle.is_good else "NG",
                                cycle.event_name or ""
                            ])
                        row.append(" -> ".join(cycle.step_sequence) if cycle.step_sequence else "")
                        writer.writerow(row)
                    writer.writerow([])
                    
                    # 导出步骤详情（如果启用了步骤相关选项）
                    if export_opts['step_duration'] or export_opts['step_interval'] or export_opts['step_event']:
                        writer.writerow([f"[{sess_proj_name} / {sess_ch}] 会话 {session.session_uuid} 的步骤详情"])
                        step_headers = ["周期序号", "步骤序号", "步骤名称", "开始时间"]
                        if export_opts['step_duration']:
                            step_headers.append("耗时(秒)")
                        if export_opts['step_interval']:
                            step_headers.append("到下步间隔(秒)")
                        if export_opts['step_event']:
                            step_headers.append("是否有效")
                        writer.writerow(step_headers)
                        
                        order_map = _get_step_order_map(db, session_id=session.id)
                        for cycle in cycles:
                            steps = db.query(StepRecord).filter(
                                StepRecord.cycle_id == cycle.id
                            ).all()
                            steps.sort(key=lambda s: order_map.get(s.step_label, 999))
                            for step in steps:
                                config_order = order_map.get(step.step_label, step.step_order - 1) + 1
                                row = [
                                    cycle.cycle_number,
                                    config_order,
                                    step.step_name or step.step_label,
                                    step.start_time.strftime("%Y-%m-%d %H:%M:%S") if step.start_time else ""
                                ]
                                if export_opts['step_duration']:
                                    row.append(f"{step.duration:.2f}" if step.duration else "")
                                if export_opts['step_interval']:
                                    interval = getattr(step, 'interval_to_next', None) or step.interval_from_prev
                                    row.append(f"{interval:.2f}" if interval else "")
                                if export_opts['step_event']:
                                    row.append("是" if step.is_valid else "否")
                                writer.writerow(row)
                        writer.writerow([])
        
        buffer.seek(0)
        filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        # 将CSV内容编码为UTF-8 bytes，并添加BOM
        csv_content = ('\ufeff' + buffer.getvalue()).encode('utf-8')
        
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/csv; charset=utf-8"
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"导出CSV失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")


# ============ 统计 API ============

@router.get("/stats/step-averages")
def get_step_averages(
    session_id: Optional[int] = None,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """获取步骤平均耗时和间隔统计"""
    query = db.query(StepRecord)
    
    if session_id:
        # 通过会话ID过滤
        cycle_ids = db.query(DetectionCycle.id).filter(DetectionCycle.session_id == session_id).all()
        cycle_ids = [c.id for c in cycle_ids]
        query = query.filter(StepRecord.cycle_id.in_(cycle_ids))
    elif date or start_date or end_date:
        # 通过日期过滤
        session_query = db.query(DetectionSession.id)
        if date:
            session_query = session_query.filter(func.date(DetectionSession.start_time) == date)
        if start_date:
            session_query = session_query.filter(DetectionSession.start_time >= start_date)
        if end_date:
            session_query = session_query.filter(DetectionSession.start_time <= end_date + " 23:59:59")
        
        session_ids = [s.id for s in session_query.all()]
        cycle_ids = db.query(DetectionCycle.id).filter(DetectionCycle.session_id.in_(session_ids)).all()
        cycle_ids = [c.id for c in cycle_ids]
        query = query.filter(StepRecord.cycle_id.in_(cycle_ids))
    
    # 按步骤标签分组统计
    # 只统计正常周期（is_good=True）的数据
    steps = query.all()
    
    # 先获取所有正常周期的ID
    good_cycle_ids = set()
    if session_id:
        good_cycles = db.query(DetectionCycle.id).filter(
            DetectionCycle.session_id == session_id,
            DetectionCycle.is_good == True
        ).all()
        good_cycle_ids = {c.id for c in good_cycles}
    elif date or start_date or end_date:
        session_query = db.query(DetectionSession.id)
        if date:
            session_query = session_query.filter(func.date(DetectionSession.start_time) == date)
        if start_date:
            session_query = session_query.filter(DetectionSession.start_time >= start_date)
        if end_date:
            session_query = session_query.filter(DetectionSession.start_time <= end_date + " 23:59:59")
        
        session_ids = [s.id for s in session_query.all()]
        good_cycles = db.query(DetectionCycle.id).filter(
            DetectionCycle.session_id.in_(session_ids),
            DetectionCycle.is_good == True
        ).all()
        good_cycle_ids = {c.id for c in good_cycles}
    
    step_stats = {}
    for step in steps:
        label = step.step_label
        if label not in step_stats:
            step_stats[label] = {
                "label": label,
                "name": step.step_name or label,
                "count": 0,
                "durations": [],
                "intervals": []  # 只统计正常周期的间隔
            }
        
        step_stats[label]["count"] += 1
        if step.duration is not None:
            step_stats[label]["durations"].append(step.duration)
        
        # 间隔只统计正常周期的，使用 interval_to_next（到下一步的间隔）
        if step.cycle_id in good_cycle_ids:
            interval = step.interval_to_next if step.interval_to_next is not None else step.interval_from_prev
            if interval is not None:
                step_stats[label]["intervals"].append(interval)
    
    # 获取配置顺序
    order_map = {}
    if session_id:
        order_map = _get_step_order_map(db, session_id=session_id)
    elif date or start_date or end_date:
        sess_query = db.query(DetectionSession)
        if date:
            sess_query = sess_query.filter(func.date(DetectionSession.start_time) == date)
        if start_date:
            sess_query = sess_query.filter(DetectionSession.start_time >= start_date)
        if end_date:
            sess_query = sess_query.filter(DetectionSession.start_time <= end_date + " 23:59:59")
        first_sess = sess_query.first()
        if first_sess:
            order_map = _get_step_order_map(db, project_id=first_sess.project_id)

    # 计算平均值
    result = []
    for label, stats in step_stats.items():
        avg_duration = sum(stats["durations"]) / len(stats["durations"]) if stats["durations"] else 0
        avg_interval = sum(stats["intervals"]) / len(stats["intervals"]) if stats["intervals"] else 0
        
        result.append({
            "label": label,
            "name": stats["name"],
            "count": stats["count"],
            "avg_duration": round(avg_duration, 2),
            "min_duration": round(min(stats["durations"]), 2) if stats["durations"] else 0,
            "max_duration": round(max(stats["durations"]), 2) if stats["durations"] else 0,
            "avg_interval": round(avg_interval, 2),
            "min_interval": round(min(stats["intervals"]), 2) if stats["intervals"] else 0,
            "max_interval": round(max(stats["intervals"]), 2) if stats["intervals"] else 0
        })
    
    result.sort(key=lambda r: order_map.get(r["label"], 999))
    
    return {"steps": result}


@router.get("/stats/cycle-averages")
def get_cycle_averages(
    session_id: Optional[int] = None,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """获取周期平均耗时统计"""
    query = db.query(DetectionCycle)
    
    if session_id:
        query = query.filter(DetectionCycle.session_id == session_id)
    elif date or start_date or end_date:
        session_query = db.query(DetectionSession.id)
        if date:
            session_query = session_query.filter(func.date(DetectionSession.start_time) == date)
        if start_date:
            session_query = session_query.filter(DetectionSession.start_time >= start_date)
        if end_date:
            session_query = session_query.filter(DetectionSession.start_time <= end_date + " 23:59:59")
        
        session_ids = [s.id for s in session_query.all()]
        query = query.filter(DetectionCycle.session_id.in_(session_ids))
    
    cycles = query.all()
    
    durations = [c.duration for c in cycles if c.duration is not None]
    good_count = len([c for c in cycles if c.is_good])
    ng_count = len(cycles) - good_count
    
    return {
        "total_cycles": len(cycles),
        "good_cycles": good_count,
        "ng_cycles": ng_count,
        "avg_duration": round(sum(durations) / len(durations), 2) if durations else 0,
        "min_duration": round(min(durations), 2) if durations else 0,
        "max_duration": round(max(durations), 2) if durations else 0,
        "yield_rate": round(good_count / len(cycles) * 100, 2) if cycles else 0
    }


# ============ 数据库备份 ============

@router.get("/backup/database")
def backup_database():
    """
    备份数据库文件
    返回数据库文件供下载
    """
    db_path = os.path.join(settings.UPLOAD_DIR, "..", "sql_app.db")
    db_path = os.path.abspath(db_path)
    
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="数据库文件不存在")
    
    # 生成备份文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"sql_app_backup_{timestamp}.db"
    
    # 返回文件下载响应
    return FileResponse(
        path=db_path,
        filename=backup_filename,
        media_type="application/octet-stream"
    )


# ============ 数据清理 ============

@router.delete("/clear/all")
def clear_all_data(db: Session = Depends(get_db)):
    """
    清空所有历史数据
    包括：会话、周期、步骤、视频记录、录制文件、缓存、上传视频
    """
    try:
        deleted_files = 0
        # 1. 删除录制视频文件
        video_dirs = [
            settings.SESSION_VIDEO_DIR,
            settings.CYCLE_VIDEO_DIR,
            settings.STEP_VIDEO_DIR
        ]
        for video_dir in video_dirs:
            if os.path.isdir(video_dir):
                for filename in os.listdir(video_dir):
                    filepath = os.path.join(video_dir, filename)
                    try:
                        if os.path.isfile(filepath):
                            os.remove(filepath)
                            deleted_files += 1
                    except Exception as e:
                        print(f"删除视频文件失败: {filepath}, {e}")

        # 2. 删除视频转换缓存
        cache_dir = os.path.join(settings.RECORDING_DIR, "cache")
        if os.path.isdir(cache_dir):
            for filename in os.listdir(cache_dir):
                filepath = os.path.join(cache_dir, filename)
                try:
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        deleted_files += 1
                except Exception:
                    pass

        # 3. 删除上传的视频文件
        upload_deleted = 0
        if os.path.isdir(settings.VIDEO_UPLOAD_DIR):
            for filename in os.listdir(settings.VIDEO_UPLOAD_DIR):
                filepath = os.path.join(settings.VIDEO_UPLOAD_DIR, filename)
                try:
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        upload_deleted += 1
                except Exception as e:
                    print(f"删除上传视频失败: {filepath}, {e}")

        # 4. 删除数据库记录（按依赖顺序）
        step_count = db.query(StepRecord).delete(synchronize_session=False)
        video_count = db.query(VideoClip).delete(synchronize_session=False)
        cycle_count = db.query(DetectionCycle).delete(synchronize_session=False)
        session_count = db.query(DetectionSession).delete(synchronize_session=False)
        
        db.commit()
        
        return {
            "success": True,
            "message": "数据清理完成",
            "deleted": {
                "sessions": session_count,
                "cycles": cycle_count,
                "steps": step_count,
                "videos": video_count,
                "files": deleted_files,
                "upload_videos": upload_deleted
            }
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")


# ============ 按日期范围清理 ============

class DateRangeCleanup(BaseModel):
    start_date: str
    end_date: str


@router.delete("/clear/range")
def clear_data_by_range(req: DateRangeCleanup, db: Session = Depends(get_db)):
    """
    删除指定日期范围内的历史数据
    日期格式: YYYY-MM-DD
    """
    try:
        start_dt = datetime.strptime(req.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(req.end_date, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式无效，请使用 YYYY-MM-DD")

    try:
        target_sessions = db.query(DetectionSession).filter(
            and_(
                DetectionSession.start_time >= start_dt,
                DetectionSession.start_time <= end_dt
            )
        ).all()

        if not target_sessions:
            return {"success": True, "message": "该日期范围内没有数据", "deleted": {"sessions": 0, "cycles": 0, "steps": 0, "videos": 0, "files": 0}}

        session_ids = [s.id for s in target_sessions]
        target_cycles = db.query(DetectionCycle).filter(
            DetectionCycle.session_id.in_(session_ids)
        ).all()
        cycle_ids = [c.id for c in target_cycles]

        target_videos = db.query(VideoClip).filter(
            and_(
                VideoClip.created_at >= start_dt,
                VideoClip.created_at <= end_dt
            )
        ).all()

        deleted_files = 0
        for v in target_videos:
            if v.file_path and os.path.isfile(v.file_path):
                try:
                    os.remove(v.file_path)
                    deleted_files += 1
                except Exception as e:
                    print(f"删除视频文件失败: {v.file_path}, {e}")

        step_count = 0
        if cycle_ids:
            step_count = db.query(StepRecord).filter(
                StepRecord.cycle_id.in_(cycle_ids)
            ).delete(synchronize_session=False)

        video_count = db.query(VideoClip).filter(
            and_(
                VideoClip.created_at >= start_dt,
                VideoClip.created_at <= end_dt
            )
        ).delete(synchronize_session=False)

        cycle_count = db.query(DetectionCycle).filter(
            DetectionCycle.session_id.in_(session_ids)
        ).delete(synchronize_session=False)

        session_count = db.query(DetectionSession).filter(
            DetectionSession.id.in_(session_ids)
        ).delete(synchronize_session=False)

        db.commit()

        return {
            "success": True,
            "message": f"已清理 {req.start_date} 至 {req.end_date} 的数据",
            "deleted": {
                "sessions": session_count,
                "cycles": cycle_count,
                "steps": step_count,
                "videos": video_count,
                "files": deleted_files
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")


# ============ 清理设置 API ============

class CleanupSettingsUpdate(BaseModel):
    retention_days: Optional[int] = None
    auto_cleanup: Optional[bool] = None


@router.get("/cleanup-settings")
def get_cleanup_settings(db: Session = Depends(get_db)):
    """获取数据清理设置"""
    retention_row = db.query(SystemConfig).filter(SystemConfig.key == "retention_days").first()
    auto_row = db.query(SystemConfig).filter(SystemConfig.key == "auto_cleanup").first()

    return {
        "retention_days": int(retention_row.value) if retention_row and retention_row.value else 30,
        "auto_cleanup": (auto_row.value == "true") if auto_row else True
    }


@router.put("/cleanup-settings")
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
        if row:
            row.value = "true" if req.auto_cleanup else "false"
        else:
            db.add(SystemConfig(key="auto_cleanup", value="true" if req.auto_cleanup else "false", description="是否启用自动清理"))

    db.commit()

    retention_row = db.query(SystemConfig).filter(SystemConfig.key == "retention_days").first()
    auto_row = db.query(SystemConfig).filter(SystemConfig.key == "auto_cleanup").first()

    return {
        "retention_days": int(retention_row.value) if retention_row and retention_row.value else 30,
        "auto_cleanup": (auto_row.value == "true") if auto_row else True
    }


# ============ 磁盘空间查询 ============

@router.get("/storage-info")
def get_storage_info():
    """获取存储空间信息"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _dir_size(path):
        size = 0
        if os.path.isdir(path):
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    try:
                        size += os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass
        return size

    dir_sizes = {}
    total_size = 0

    db_path = os.path.join(base_dir, "sql_app.db")
    if os.path.isfile(db_path):
        dir_sizes["database"] = os.path.getsize(db_path)
    else:
        dir_sizes["database"] = 0

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
        "disk_usage_percent": round(disk.used / disk.total * 100, 1)
    }


# ============ 手动触发清理 ============

@router.post("/cleanup/run")
def run_cleanup_now():
    """立即执行一次自动清理（按保留天数）"""
    try:
        _perform_auto_cleanup()
        return {"success": True, "message": "清理已执行"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")
