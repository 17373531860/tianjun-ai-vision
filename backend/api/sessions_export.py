"""
CSV 导出模块
从 sessions.py 抽出来的 export_csv 实现，按 export_type 分派给三个内部 builder。

外部入口: build_csv_response(db, params) -> StreamingResponse
"""
import csv
import io
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, and_, or_

from backend.models.models import (
    DetectionSession, DetectionCycle, StepRecord,
    DataExportSetting, Project,
)


# ----- helpers -----

def _load_export_opts(db) -> dict:
    s = db.query(DataExportSetting).first()
    g = lambda k, d=True: getattr(s, k, d) if s else d
    return {
        'session_info':   g('export_session_info'),
        'counters':       g('export_counters'),
        'cycle_result':   g('export_cycle_result'),
        'cycle_duration': g('export_cycle_duration'),
        'cycle_interval': g('export_cycle_interval'),
        'step_duration':  g('export_step_duration'),
        'step_interval':  g('export_step_interval'),
        'step_event':     g('export_step_event'),
    }


def _resolve_week_month(week: Optional[str], month: Optional[str],
                        start_date: Optional[str], end_date: Optional[str]):
    """把 week / month 参数解析成 start_date/end_date"""
    if week:
        try:
            year, week_num = week.split('-W')
            year = int(year); week_num = int(week_num)
            from datetime import date as date_type
            jan_4 = date_type(year, 1, 4)
            first_monday = jan_4 - timedelta(days=jan_4.weekday())
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
    return start_date, end_date


# ----- builders for the three export_type branches -----

def _write_session_export(writer, db, session_id: int, opts: dict, get_order_map):
    session = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    sess_proj = db.query(Project).filter(Project.id == session.project_id).first()
    sess_proj_name = sess_proj.name if sess_proj else "Unknown"
    sess_ch_label = f"工位{(session.channel_id or 0) + 1}"

    if opts['session_info']:
        writer.writerow(["会话信息"])
        writer.writerow(["会话ID", "项目", "工位", "开始时间", "结束时间", "总周期数", "合格数", "不良数", "平均周期时间"])
        writer.writerow([
            session.session_uuid, sess_proj_name, sess_ch_label,
            session.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            session.end_time.strftime("%Y-%m-%d %H:%M:%S") if session.end_time else "",
            session.total_cycles or 0, session.good_cycles or 0, session.ng_cycles or 0,
            f"{session.avg_cycle_time:.2f}s" if session.avg_cycle_time else "",
        ])
        writer.writerow([])

    if opts['counters'] and session.counters_snapshot:
        writer.writerow(["计数器统计"])
        writer.writerow(["计数器名称", "数值"])
        for k, v in session.counters_snapshot.items():
            writer.writerow([k, v])
        writer.writerow([])

    cycles = db.query(DetectionCycle).filter(DetectionCycle.session_id == session_id).all()
    if not cycles:
        return

    writer.writerow(["周期详情"])
    headers = ["周期序号", "开始时间", "结束时间"]
    if opts['cycle_duration']: headers.append("耗时(秒)")
    if opts['cycle_interval']: headers.append("周期间隔(秒)")
    if opts['cycle_result']:   headers.extend(["结果", "事件"])
    headers.append("步骤序列")
    writer.writerow(headers)

    for cycle in cycles:
        row = [
            cycle.cycle_number,
            cycle.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            cycle.end_time.strftime("%Y-%m-%d %H:%M:%S") if cycle.end_time else "",
        ]
        if opts['cycle_duration']:
            row.append(f"{cycle.duration:.2f}" if cycle.duration else "")
        if opts['cycle_interval']:
            iv = getattr(cycle, 'interval_to_next', None)
            row.append(f"{iv:.2f}" if iv else "")
        if opts['cycle_result']:
            row.extend(["合格" if cycle.is_good else "不良", cycle.event_name or ""])
        row.append(" -> ".join(cycle.step_sequence) if cycle.step_sequence else "")
        writer.writerow(row)

    if not (opts['step_duration'] or opts['step_interval'] or opts['step_event']):
        return

    writer.writerow([])
    writer.writerow(["步骤详情"])
    step_headers = ["周期序号", "步骤序号", "步骤名称", "开始时间"]
    if opts['step_duration']: step_headers.append("耗时(秒)")
    if opts['step_interval']: step_headers.append("到下步间隔(秒)")
    writer.writerow(step_headers)

    order_map = get_order_map(db, session_id=session_id)
    for cycle in cycles:
        steps = db.query(StepRecord).filter(StepRecord.cycle_id == cycle.id).all()
        steps.sort(key=lambda s: order_map.get(s.step_label, 999))
        for step in steps:
            cfg_order = order_map.get(step.step_label, step.step_order - 1) + 1
            row = [
                cycle.cycle_number, cfg_order,
                step.step_name or step.step_label,
                step.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            ]
            if opts['step_duration']:
                row.append(f"{step.duration:.2f}" if step.duration else "")
            if opts['step_interval']:
                iv = getattr(step, 'interval_to_next', None) or step.interval_from_prev
                row.append(f"{iv:.2f}" if iv else "")
            writer.writerow(row)


def _write_cycle_export(writer, db, cycle_id: int, opts: dict, get_order_map):
    cycle = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="周期不存在")

    writer.writerow(["周期信息"])
    headers = ["周期ID", "开始时间", "结束时间"]
    if opts['cycle_duration']: headers.append("耗时(秒)")
    if opts['cycle_result']:   headers.extend(["结果", "事件", "原因"])
    writer.writerow(headers)

    row = [
        cycle.cycle_uuid,
        cycle.start_time.strftime("%Y-%m-%d %H:%M:%S"),
        cycle.end_time.strftime("%Y-%m-%d %H:%M:%S") if cycle.end_time else "",
    ]
    if opts['cycle_duration']:
        row.append(f"{cycle.duration:.2f}" if cycle.duration else "")
    if opts['cycle_result']:
        row.extend([
            "合格" if cycle.is_good else "不良",
            cycle.event_name or "",
            cycle.result_reason or "",
        ])
    writer.writerow(row)
    writer.writerow([])

    steps = db.query(StepRecord).filter(StepRecord.cycle_id == cycle_id).all()
    if not steps:
        return
    order_map = get_order_map(db, cycle_id=cycle_id)
    steps.sort(key=lambda s: order_map.get(s.step_label, 999))

    writer.writerow(["步骤详情"])
    step_headers = ["序号", "步骤名称", "开始时间", "结束时间"]
    if opts['step_duration']: step_headers.append("耗时(秒)")
    if opts['step_interval']: step_headers.append("到下步间隔(秒)")
    writer.writerow(step_headers)

    for step in steps:
        cfg_order = order_map.get(step.step_label, step.step_order - 1) + 1
        row = [
            cfg_order,
            step.step_name or step.step_label,
            step.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            step.end_time.strftime("%Y-%m-%d %H:%M:%S") if step.end_time else "",
        ]
        if opts['step_duration']:
            row.append(f"{step.duration:.2f}" if step.duration else "")
        if opts['step_interval']:
            iv = getattr(step, 'interval_to_next', None) or step.interval_from_prev
            row.append(f"{iv:.2f}" if iv else "")
        writer.writerow(row)


def _filter_sessions_for_range(db, date, start_date, end_date,
                               start_hour, end_hour, project_id, channel_id):
    q = db.query(DetectionSession)
    if date:
        q = q.filter(func.date(DetectionSession.start_time) == date)
    elif start_date and end_date:
        q = q.filter(DetectionSession.start_time >= start_date)
        q = q.filter(DetectionSession.start_time <= end_date + " 23:59:59")
    if project_id is not None:
        q = q.filter(DetectionSession.project_id == project_id)
    if channel_id is not None:
        q = q.filter(DetectionSession.channel_id == channel_id)
    if start_hour and end_hour and start_hour > end_hour and date:
        next_d = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        q = q.filter(or_(
            func.date(DetectionSession.start_time) == date,
            func.date(DetectionSession.start_time) == next_d,
        ))
    return q.order_by(DetectionSession.start_time).all()


def _shift_filter_cycles(db, sessions, date, start_date, start_hour, end_hour):
    if not (start_hour and end_hour and sessions):
        return set(), sessions
    sess_ids = [s.id for s in sessions]
    cq = db.query(DetectionCycle).filter(DetectionCycle.session_id.in_(sess_ids))
    ct_col = func.strftime('%H:%M', DetectionCycle.start_time)
    if start_hour <= end_hour:
        cq = cq.filter(and_(
            func.date(DetectionCycle.start_time) == (date or start_date),
            ct_col >= start_hour, ct_col < end_hour,
        ))
    else:
        base_d = date or start_date
        next_d = (datetime.strptime(base_d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        cq = cq.filter(or_(
            and_(func.date(DetectionCycle.start_time) == base_d, ct_col >= start_hour),
            and_(func.date(DetectionCycle.start_time) == next_d, ct_col < end_hour),
        ))
    cs = cq.all()
    shift_ids = set(c.id for c in cs)
    if not shift_ids:
        return set(), []
    involved = set(c.session_id for c in cs)
    return shift_ids, [s for s in sessions if s.id in involved]


def _write_range_export(writer, db, opts, get_order_map, *,
                        date, start_date, end_date, start_hour, end_hour,
                        project_id, channel_id):
    sessions = _filter_sessions_for_range(
        db, date, start_date, end_date, start_hour, end_hour, project_id, channel_id,
    )
    shift_cycle_ids, sessions = _shift_filter_cycles(
        db, sessions, date, start_date, start_hour, end_hour,
    )

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

    if opts['session_info']:
        writer.writerow(["会话列表"])
        writer.writerow(["会话ID", "项目", "工位", "开始时间", "结束时间", "周期数", "合格", "不良", "平均CT"])
        for s in sessions:
            p = db.query(Project).filter(Project.id == s.project_id).first()
            writer.writerow([
                s.session_uuid,
                p.name if p else "Unknown",
                f"工位{(s.channel_id or 0) + 1}",
                s.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                s.end_time.strftime("%Y-%m-%d %H:%M:%S") if s.end_time else "",
                s.total_cycles or 0, s.good_cycles or 0, s.ng_cycles or 0,
                f"{s.avg_cycle_time:.2f}s" if s.avg_cycle_time else "",
            ])
        writer.writerow([])

    for s in sessions:
        cq = db.query(DetectionCycle).filter(DetectionCycle.session_id == s.id)
        if shift_cycle_ids:
            cq = cq.filter(DetectionCycle.id.in_(shift_cycle_ids))
        cycles = cq.all()
        if not cycles:
            continue

        sp = db.query(Project).filter(Project.id == s.project_id).first()
        sp_name = sp.name if sp else "Unknown"
        sch = f"工位{(s.channel_id or 0) + 1}"

        writer.writerow([f"[{sp_name} / {sch}] 会话 {s.session_uuid} 的周期详情"])
        headers = ["周期序号", "开始时间"]
        if opts['cycle_duration']: headers.append("耗时(秒)")
        if opts['cycle_interval']: headers.append("周期间隔(秒)")
        if opts['cycle_result']:   headers.extend(["结果", "事件"])
        headers.append("步骤序列")
        writer.writerow(headers)

        for cycle in cycles:
            row = [cycle.cycle_number, cycle.start_time.strftime("%Y-%m-%d %H:%M:%S")]
            if opts['cycle_duration']:
                row.append(f"{cycle.duration:.2f}" if cycle.duration else "")
            if opts['cycle_interval']:
                iv = getattr(cycle, 'interval_to_next', None)
                row.append(f"{iv:.2f}" if iv else "")
            if opts['cycle_result']:
                row.extend(["OK" if cycle.is_good else "NG", cycle.event_name or ""])
            row.append(" -> ".join(cycle.step_sequence) if cycle.step_sequence else "")
            writer.writerow(row)
        writer.writerow([])

        if not (opts['step_duration'] or opts['step_interval'] or opts['step_event']):
            continue

        writer.writerow([f"[{sp_name} / {sch}] 会话 {s.session_uuid} 的步骤详情"])
        step_headers = ["周期序号", "步骤序号", "步骤名称", "开始时间"]
        if opts['step_duration']: step_headers.append("耗时(秒)")
        if opts['step_interval']: step_headers.append("到下步间隔(秒)")
        if opts['step_event']:    step_headers.append("是否有效")
        writer.writerow(step_headers)

        order_map = get_order_map(db, session_id=s.id)
        for cycle in cycles:
            steps = db.query(StepRecord).filter(StepRecord.cycle_id == cycle.id).all()
            steps.sort(key=lambda x: order_map.get(x.step_label, 999))
            for step in steps:
                cfg_order = order_map.get(step.step_label, step.step_order - 1) + 1
                row = [
                    cycle.cycle_number, cfg_order,
                    step.step_name or step.step_label,
                    step.start_time.strftime("%Y-%m-%d %H:%M:%S") if step.start_time else "",
                ]
                if opts['step_duration']:
                    row.append(f"{step.duration:.2f}" if step.duration else "")
                if opts['step_interval']:
                    iv = getattr(step, 'interval_to_next', None) or step.interval_from_prev
                    row.append(f"{iv:.2f}" if iv else "")
                if opts['step_event']:
                    row.append("是" if step.is_valid else "否")
                writer.writerow(row)
        writer.writerow([])


# ----- public entry -----

def build_csv_response(db, get_order_map, *,
                       export_type: str,
                       session_id: Optional[int],
                       cycle_id: Optional[int],
                       date: Optional[str],
                       start_date: Optional[str],
                       end_date: Optional[str],
                       week: Optional[str],
                       month: Optional[str],
                       start_hour: Optional[str],
                       end_hour: Optional[str],
                       project_id: Optional[int],
                       channel_id: Optional[int]) -> StreamingResponse:
    try:
        opts = _load_export_opts(db)
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        start_date, end_date = _resolve_week_month(week, month, start_date, end_date)

        if export_type == "session" and session_id:
            _write_session_export(writer, db, session_id, opts, get_order_map)
        elif export_type == "cycle" and cycle_id:
            _write_cycle_export(writer, db, cycle_id, opts, get_order_map)
        else:
            _write_range_export(
                writer, db, opts, get_order_map,
                date=date, start_date=start_date, end_date=end_date,
                start_hour=start_hour, end_hour=end_hour,
                project_id=project_id, channel_id=channel_id,
            )

        buffer.seek(0)
        filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_content = ('\ufeff' + buffer.getvalue()).encode('utf-8')
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/csv; charset=utf-8",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"导出CSV失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")
