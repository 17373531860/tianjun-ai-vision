"""
统计 API（步骤平均、周期平均），从 sessions.py 拆出。
通过 sessions.router.include_router(router) 挂接。
"""
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.models.models import DetectionSession, DetectionCycle, StepRecord

router = APIRouter()


def _filter_cycle_ids_by_scope(db: Session, session_id, date, start_date, end_date):
    if session_id:
        rows = db.query(DetectionCycle.id).filter(DetectionCycle.session_id == session_id).all()
        return [r.id for r in rows]
    if not (date or start_date or end_date):
        return None  # no filter
    sq = db.query(DetectionSession.id)
    if date:
        sq = sq.filter(func.date(DetectionSession.start_time) == date)
    if start_date:
        sq = sq.filter(DetectionSession.start_time >= start_date)
    if end_date:
        sq = sq.filter(DetectionSession.start_time <= end_date + " 23:59:59")
    sids = [s.id for s in sq.all()]
    rows = db.query(DetectionCycle.id).filter(DetectionCycle.session_id.in_(sids)).all()
    return [r.id for r in rows]


def _good_cycle_ids(db: Session, session_id, date, start_date, end_date):
    if session_id:
        rows = db.query(DetectionCycle.id).filter(
            DetectionCycle.session_id == session_id,
            DetectionCycle.is_good == True,  # noqa: E712
        ).all()
        return {r.id for r in rows}
    if not (date or start_date or end_date):
        return set()
    sq = db.query(DetectionSession.id)
    if date:
        sq = sq.filter(func.date(DetectionSession.start_time) == date)
    if start_date:
        sq = sq.filter(DetectionSession.start_time >= start_date)
    if end_date:
        sq = sq.filter(DetectionSession.start_time <= end_date + " 23:59:59")
    sids = [s.id for s in sq.all()]
    rows = db.query(DetectionCycle.id).filter(
        DetectionCycle.session_id.in_(sids),
        DetectionCycle.is_good == True,  # noqa: E712
    ).all()
    return {r.id for r in rows}


@router.get("/stats/step-averages")
def get_step_averages(
    session_id: Optional[int] = None,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """步骤平均耗时和间隔。间隔仅统计正常周期 (is_good=True)。"""
    from backend.api.sessions import _get_step_order_map

    query = db.query(StepRecord)
    cycle_ids = _filter_cycle_ids_by_scope(db, session_id, date, start_date, end_date)
    if cycle_ids is not None:
        query = query.filter(StepRecord.cycle_id.in_(cycle_ids))

    steps = query.all()
    good_cycle_ids = _good_cycle_ids(db, session_id, date, start_date, end_date)

    step_stats = {}
    for step in steps:
        label = step.step_label
        ss = step_stats.setdefault(label, {
            "label": label,
            "name": step.step_name or label,
            "count": 0,
            "durations": [],
            "intervals": [],
        })
        ss["count"] += 1
        if step.duration is not None:
            ss["durations"].append(step.duration)
        if step.cycle_id in good_cycle_ids:
            iv = step.interval_to_next if step.interval_to_next is not None else step.interval_from_prev
            if iv is not None:
                ss["intervals"].append(iv)

    order_map = {}
    if session_id:
        order_map = _get_step_order_map(db, session_id=session_id)
    elif date or start_date or end_date:
        sq = db.query(DetectionSession)
        if date:
            sq = sq.filter(func.date(DetectionSession.start_time) == date)
        if start_date:
            sq = sq.filter(DetectionSession.start_time >= start_date)
        if end_date:
            sq = sq.filter(DetectionSession.start_time <= end_date + " 23:59:59")
        first = sq.first()
        if first:
            order_map = _get_step_order_map(db, project_id=first.project_id)

    result = []
    for label, st in step_stats.items():
        avg_d = sum(st["durations"]) / len(st["durations"]) if st["durations"] else 0
        avg_i = sum(st["intervals"]) / len(st["intervals"]) if st["intervals"] else 0
        result.append({
            "label": label,
            "name": st["name"],
            "count": st["count"],
            "avg_duration": round(avg_d, 2),
            "min_duration": round(min(st["durations"]), 2) if st["durations"] else 0,
            "max_duration": round(max(st["durations"]), 2) if st["durations"] else 0,
            "avg_interval": round(avg_i, 2),
            "min_interval": round(min(st["intervals"]), 2) if st["intervals"] else 0,
            "max_interval": round(max(st["intervals"]), 2) if st["intervals"] else 0,
        })
    result.sort(key=lambda r: order_map.get(r["label"], 999))
    return {"steps": result}


@router.get("/stats/cycle-averages")
def get_cycle_averages(
    session_id: Optional[int] = None,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """周期平均耗时统计"""
    query = db.query(DetectionCycle)
    if session_id:
        query = query.filter(DetectionCycle.session_id == session_id)
    elif date or start_date or end_date:
        sq = db.query(DetectionSession.id)
        if date:
            sq = sq.filter(func.date(DetectionSession.start_time) == date)
        if start_date:
            sq = sq.filter(DetectionSession.start_time >= start_date)
        if end_date:
            sq = sq.filter(DetectionSession.start_time <= end_date + " 23:59:59")
        sids = [s.id for s in sq.all()]
        query = query.filter(DetectionCycle.session_id.in_(sids))

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
        "yield_rate": round(good_count / len(cycles) * 100, 2) if cycles else 0,
    }
