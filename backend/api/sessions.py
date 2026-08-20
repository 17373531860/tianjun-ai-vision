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

from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.models import (
    DetectionSession, DetectionCycle, StepRecord,
    VideoClip, DataExportSetting, Project, SystemConfig,
)
# v3.10+ 阶段 4: operator_id 列语义重定向到 user_id, 查 User 表
from backend.models.auth_models import User
from backend.core.config import settings
# ========== FFmpeg 路径 (统一从 source 导入) ==========
from backend.api.source import get_ffmpeg_path, get_cached_ffmpeg_path  # noqa: F401


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









# ============ Pydantic 模型 ============

class SessionCreate(BaseModel):
    project_id: int
    name: Optional[str] = None  # 客户自定义会话标识


class SessionRename(BaseModel):
    name: Optional[str] = None  # None 或空 → 清空标识


class SessionResponse(BaseModel):
    id: int
    session_uuid: str
    name: Optional[str] = None  # 客户自定义会话标识
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
    from backend.api.source_session_lifecycle_mixin import _clean_session_name
    project = db.query(Project).filter(Project.id == req.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        cleaned_name = _clean_session_name(req.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    session = DetectionSession(
        session_uuid=str(uuid.uuid4())[:8],
        name=cleaned_name,
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
        name=session.name,
        project_id=session.project_id,
        project_name=project.name,
        start_time=session.start_time.strftime("%Y-%m-%d %H:%M:%S"),
        status=session.status
    )


@router.patch("/sessions/{session_id}/name", response_model=SessionResponse)
def rename_session(session_id: int, req: SessionRename, db: Session = Depends(get_db)):
    """重命名会话（Data 页"重命名"按钮调用）

    name 为 null/空字符串 → 清空标识；
    含禁用字符 (/ \\ : * ? " < > |) → 400。
    """
    from backend.api.source_session_lifecycle_mixin import _clean_session_name
    session = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        cleaned_name = _clean_session_name(req.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session.name = cleaned_name
    db.commit()
    db.refresh(session)

    project = db.query(Project).filter(Project.id == session.project_id).first()
    return SessionResponse(
        id=session.id,
        session_uuid=session.session_uuid,
        name=session.name,
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
        operator_id=getattr(session, 'operator_id', None),
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
            u = db.query(User).filter(User.id == op_id).first()
            op_name = (u.display_name or u.username) if u else None
        result.append(SessionResponse(
            id=session.id,
            session_uuid=session.session_uuid,
            name=session.name,
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
    # v3.35.1: 班次标记放开 day/night 硬编码, 支持自定义班次名 (白班/午班/夜班…)
    if shift:
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
        from backend.db.sql_compat import hour_minute
        cycle_time_col = hour_minute(DetectionCycle.start_time)
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
            s_u = db.query(User).filter(User.id == s_op_id).first()
            s_op_name = (s_u.display_name or s_u.username) if s_u else None
        session_responses.append(SessionResponse(
            id=session.id,
            session_uuid=session.session_uuid,
            name=session.name,
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
        g_u = db.query(User).filter(User.id == g_op_id).first()
        g_op_name = (g_u.display_name or g_u.username) if g_u else None
    
    return SessionResponse(
        id=session.id,
        session_uuid=session.session_uuid,
        name=session.name,
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
    result: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """获取会话的周期（分页）

    result: 可选 'ok' / 'ng' —— 按判定结果过滤（v3.48.1, 数据中心"只看NG录像"）。
    """
    base = db.query(DetectionCycle).filter(
        DetectionCycle.session_id == session_id
    )
    if operator_id is not None:
        base = base.filter(DetectionCycle.operator_id == operator_id)
    if result == 'ok':
        base = base.filter(DetectionCycle.is_good.is_(True))
    elif result == 'ng':
        base = base.filter(DetectionCycle.is_good.is_(False))
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
            c_u = db.query(User).filter(User.id == c_op_id).first()
            c_op_name = (c_u.display_name or c_u.username) if c_u else None
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


@router.get("/cycles/by-serial/{serial_no}")
def get_cycles_by_serial(
    serial_no: str,
    skip: int = 0,
    limit: int = 50,
    fuzzy: bool = True,
    db: Session = Depends(get_db),
):
    """v3.4.3: 按工件条码全局检索关联检测周期 (跨 session/日期).
    返回结构跟 /sessions/{id}/cycles 完全一致, 前端能复用同一套渲染.
    每条额外附带 session_uuid / session_id / project_name 让用户知道来源.
    """
    from backend.models.mes_models import WorkpieceInspection, Workpiece

    serial_no = (serial_no or "").strip()
    if not serial_no:
        return {"items": [], "total": 0, "matched_workpieces": []}

    # 模糊匹配 serial (默认开), 严格 fuzzy=False 时只命中完全相等
    # ilike: SQLite 的 LIKE 对 ASCII 本就不分大小写, PG 的 LIKE 区分 —
    # 用 ilike 让双方言行为一致 (与 services/workpiece.py 的搜索口径相同)
    if fuzzy:
        wp_q = db.query(Workpiece).filter(
            Workpiece.serial_no.ilike(f"%{serial_no}%")
        )
    else:
        wp_q = db.query(Workpiece).filter(Workpiece.serial_no == serial_no)
    wps = wp_q.order_by(Workpiece.id.desc()).all()
    if not wps:
        return {"items": [], "total": 0, "matched_workpieces": []}

    wp_ids = [w.id for w in wps]
    wp_serial_map = {w.id: w.serial_no for w in wps}

    # 拿到所有 inspection 行 (一件可有多次检测)
    insps = (
        db.query(WorkpieceInspection)
        .filter(WorkpieceInspection.workpiece_id.in_(wp_ids))
        .filter(WorkpieceInspection.cycle_id.isnot(None))
        .all()
    )
    cycle_id_to_serial: dict = {}
    for ins in insps:
        if ins.cycle_id and ins.cycle_id not in cycle_id_to_serial:
            cycle_id_to_serial[ins.cycle_id] = wp_serial_map.get(ins.workpiece_id)
    if not cycle_id_to_serial:
        return {
            "items": [], "total": 0,
            "matched_workpieces": [
                {"id": w.id, "serial_no": w.serial_no} for w in wps
            ],
        }

    cycle_ids = list(cycle_id_to_serial.keys())
    base = db.query(DetectionCycle).filter(DetectionCycle.id.in_(cycle_ids))
    total = base.count()
    cycles = (
        base.order_by(DetectionCycle.start_time.desc())
        .offset(skip).limit(limit).all()
    )

    # 一次性拿来源 session/project 信息, 避免 N+1
    sess_ids = list({c.session_id for c in cycles if c.session_id})
    sess_rows = (
        db.query(DetectionSession).filter(DetectionSession.id.in_(sess_ids)).all()
        if sess_ids else []
    )
    sess_map = {s.id: s for s in sess_rows}
    proj_ids = list({s.project_id for s in sess_rows if s.project_id})
    proj_rows = (
        db.query(Project).filter(Project.id.in_(proj_ids)).all()
        if proj_ids else []
    )
    proj_map = {p.id: p.name for p in proj_rows}

    op_ids = list({c.operator_id for c in cycles if c.operator_id})
    op_map = {}
    if op_ids:
        for u in db.query(User).filter(User.id.in_(op_ids)).all():
            op_map[u.id] = u.display_name or u.username

    items = []
    for cycle in cycles:
        sess = sess_map.get(cycle.session_id)
        items.append({
            "id": cycle.id,
            "cycle_uuid": cycle.cycle_uuid,
            "cycle_number": cycle.cycle_number,
            "start_time": cycle.start_time.strftime("%Y-%m-%d %H:%M:%S")
                          if cycle.start_time else None,
            "end_time": cycle.end_time.strftime("%Y-%m-%d %H:%M:%S")
                        if cycle.end_time else None,
            "duration": cycle.duration,
            "is_good": cycle.is_good,
            "event_name": cycle.event_name,
            "result_reason": cycle.result_reason,
            "step_sequence": cycle.step_sequence,
            "video_id": cycle.video_id,
            "operator_id": cycle.operator_id,
            "operator_name": op_map.get(cycle.operator_id) if cycle.operator_id else None,
            "serial_no": cycle_id_to_serial.get(cycle.id),
            # 跨 session 检索特有的来源信息
            "session_id": cycle.session_id,
            "session_uuid": sess.session_uuid if sess else None,
            "session_start_time": (sess.start_time.strftime("%Y-%m-%d %H:%M:%S")
                                    if sess and sess.start_time else None),
            "project_id": sess.project_id if sess else None,
            "project_name": proj_map.get(sess.project_id) if sess else None,
        })

    return {
        "items": items,
        "total": total,
        "matched_workpieces": [
            {"id": w.id, "serial_no": w.serial_no} for w in wps
        ],
    }


@router.get("/cycles/{cycle_id}", response_model=CycleResponse)
def get_cycle(cycle_id: int, db: Session = Depends(get_db)):
    """获取单个周期详情"""
    cycle = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="周期不存在")
    
    gc_op_id = getattr(cycle, 'operator_id', None)
    gc_op_name = None
    if gc_op_id:
        gc_u = db.query(User).filter(User.id == gc_op_id).first()
        gc_op_name = (gc_u.display_name or gc_u.username) if gc_u else None
    
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
    """将视频转换为浏览器兼容的H.264格式

    v3.48.1 治「视频加载失败」两根因:
    1. 旧实现直接往缓存路径写, 转码超时/被杀会留残缺文件, 且下次请求
       os.path.exists 命中坏缓存直接回传 → 该视频从此永远播放失败。
       改为先写 .tmp 临时文件、转码成功后原子 rename, 缓存要么完整要么没有。
    2. 缓存名 _h264 → _h264v2: 让历史上已经写坏的旧缓存自然失效重转。
    3. 超时 60s → 300s: 会话级长录像 60s 根本转不完, 超时后回退原始
       mp4v 编码文件, Chromium 解不了照样黑屏报错。
    """
    cache_dir = os.path.join(settings.RECORDING_DIR, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    filename = os.path.basename(input_path)
    base_name = os.path.splitext(filename)[0]
    output_path = os.path.join(cache_dir, f"{base_name}_h264v2.mp4")
    tmp_path = output_path + ".tmp.mp4"

    # 完整缓存(原子 rename 落位的)才可信
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return output_path

    try:
        ffmpeg_path = get_cached_ffmpeg_path()
        cmd = [
            ffmpeg_path, "-y",
            "-i", input_path,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-movflags", "+faststart",  # 支持流式播放
            tmp_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            os.replace(tmp_path, output_path)  # 原子落位, 不存在半成品缓存
            print(f"视频转换成功: {output_path}")
            # 顺手清掉旧命名的坏缓存(如有), 不占磁盘
            legacy = os.path.join(cache_dir, f"{base_name}_h264.mp4")
            if os.path.exists(legacy):
                try:
                    os.remove(legacy)
                except OSError:
                    pass
            return output_path
        print(f"视频转换失败: {(result.stderr or '')[-500:]}")
    except Exception as e:
        print(f"视频转换异常: {e}")
    finally:
        # 失败/超时的半成品必须清掉, 绝不能留给下次命中
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return input_path  # 转换失败回退原文件(老编码浏览器可能放不了, 但至少可下载)


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


@router.put("/export-settings", response_model=ExportSettingResponse,
             dependencies=[Depends(require_perm("data.export"))])
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
    week: Optional[str] = None,
    month: Optional[str] = None,
    start_hour: Optional[str] = None,
    end_hour: Optional[str] = None,
    project_id: Optional[int] = Query(None, description="按项目过滤，None=全部项目"),
    channel_id: Optional[int] = Query(None, description="按工位过滤(0-based)，None=全部工位"),
    pt_mode: Optional[str] = Query(None, description="PT 显示口径: avg/last/current; avg 时 CSV 增加 step 平均列"),
    ct_mode: Optional[str] = Query(None, description="CT 显示口径: avg/last/current; avg 时 CSV 增加 cycle 平均列"),
    output_format: str = Query("csv", description="输出格式: csv | txt | xlsx | docx | pdf (v3.8.x+)"),
    db: Session = Depends(get_db)
):
    """导出报表 (实现见 sessions_export.py，按 export_type=session/cycle/all 分派)

    pt_mode/ct_mode 跟随前端"系统设置 → 显示设置 → PT/CT 显示口径"开关，
    avg 时为相应列额外输出"(平均)"列；last/current/None 时保持原列结构。

    v3.8.x: 新增 output_format 支持 csv/txt/xlsx/docx/pdf 五种格式直接下载。
    """
    from backend.api.sessions_export import build_csv_response
    return build_csv_response(
        db, _get_step_order_map,
        export_type=export_type, session_id=session_id, cycle_id=cycle_id,
        date=date, start_date=start_date, end_date=end_date,
        week=week, month=month, start_hour=start_hour, end_hour=end_hour,
        project_id=project_id, channel_id=channel_id,
        pt_mode=pt_mode, ct_mode=ct_mode,
        output_format=output_format,
    )

# === 子模块挂载 (拆分自原 sessions.py) ===
from backend.api.sessions_stats import router as _stats_router  # noqa: E402
from backend.api.sessions_maintenance import router as _maint_router  # noqa: E402
router.include_router(_stats_router)
router.include_router(_maint_router)
