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


def _calc_aggregates(cycles, steps_iterable=None):
    """计算 cycle/step 在给定集合内的平均时长。

    返回 (avg_cycle_dur_str, step_avg_map) 二元组：
      - avg_cycle_dur_str: "12.34" 字符串（无数据返回空串）
      - step_avg_map: {step_label: "1.23" 字符串}
    """
    cycle_durs = [c.duration for c in cycles if c.duration]
    avg_c = sum(cycle_durs) / len(cycle_durs) if cycle_durs else None
    avg_c_s = f"{avg_c:.2f}" if avg_c is not None else ""

    step_buckets = {}
    if steps_iterable:
        for s in steps_iterable:
            if s.duration:
                step_buckets.setdefault(s.step_label, []).append(s.duration)
    step_avg_map = {
        lbl: f"{(sum(durs) / len(durs)):.2f}"
        for lbl, durs in step_buckets.items()
    }
    return avg_c_s, step_avg_map


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

def _write_session_export(writer, db, session_id: int, opts: dict, get_order_map,
                          *, pt_mode: Optional[str] = None,
                          ct_mode: Optional[str] = None):
    session = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 显示口径联动：avg 时多加平均列，last/current/None 保持原列结构
    ct_avg_on = (ct_mode == 'avg')
    pt_avg_on = (pt_mode == 'avg')

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

    # 平均聚合（仅 avg 模式用）
    all_steps_for_avg = []
    if pt_avg_on:
        all_steps_for_avg = (
            db.query(StepRecord)
            .join(DetectionCycle, StepRecord.cycle_id == DetectionCycle.id)
            .filter(DetectionCycle.session_id == session_id)
            .all()
        )
    avg_cycle_dur_s, step_avg_map = _calc_aggregates(
        cycles, all_steps_for_avg if pt_avg_on else None,
    )

    writer.writerow(["周期详情"])
    headers = ["周期序号", "开始时间", "结束时间"]
    if opts['cycle_duration']:
        headers.append("耗时(秒)")
        if ct_avg_on:
            headers.append("耗时(平均/秒)")
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
            if ct_avg_on:
                row.append(avg_cycle_dur_s)
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
    if opts['step_duration']:
        step_headers.append("耗时(秒)")
        if pt_avg_on:
            step_headers.append("耗时(平均/秒)")
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
                if pt_avg_on:
                    row.append(step_avg_map.get(step.step_label, ""))
            if opts['step_interval']:
                iv = getattr(step, 'interval_to_next', None) or step.interval_from_prev
                row.append(f"{iv:.2f}" if iv else "")
            writer.writerow(row)


def _write_cycle_export(writer, db, cycle_id: int, opts: dict, get_order_map,
                        *, pt_mode: Optional[str] = None,
                        ct_mode: Optional[str] = None):
    cycle = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="周期不存在")
    # 单 cycle 导出"平均"= 该 session 的全局平均（最有意义的对比基准）
    ct_avg_on = (ct_mode == 'avg')
    pt_avg_on = (pt_mode == 'avg')
    avg_cycle_dur_s = ""
    step_avg_map: dict = {}
    if (ct_avg_on or pt_avg_on) and cycle.session_id:
        sess_cycles = (
            db.query(DetectionCycle)
            .filter(DetectionCycle.session_id == cycle.session_id)
            .all()
        )
        sess_steps = []
        if pt_avg_on:
            sess_steps = (
                db.query(StepRecord)
                .join(DetectionCycle, StepRecord.cycle_id == DetectionCycle.id)
                .filter(DetectionCycle.session_id == cycle.session_id)
                .all()
            )
        avg_cycle_dur_s, step_avg_map = _calc_aggregates(sess_cycles, sess_steps)

    writer.writerow(["周期信息"])
    headers = ["周期ID", "开始时间", "结束时间"]
    if opts['cycle_duration']:
        headers.append("耗时(秒)")
        if ct_avg_on:
            headers.append("耗时(平均/秒)")
    if opts['cycle_result']:   headers.extend(["结果", "事件", "原因"])
    writer.writerow(headers)

    row = [
        cycle.cycle_uuid,
        cycle.start_time.strftime("%Y-%m-%d %H:%M:%S"),
        cycle.end_time.strftime("%Y-%m-%d %H:%M:%S") if cycle.end_time else "",
    ]
    if opts['cycle_duration']:
        row.append(f"{cycle.duration:.2f}" if cycle.duration else "")
        if ct_avg_on:
            row.append(avg_cycle_dur_s)
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
    if opts['step_duration']:
        step_headers.append("耗时(秒)")
        if pt_avg_on:
            step_headers.append("耗时(平均/秒)")
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
            if pt_avg_on:
                row.append(step_avg_map.get(step.step_label, ""))
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
    from backend.db.sql_compat import hour_minute
    ct_col = hour_minute(DetectionCycle.start_time)
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
                        project_id, channel_id,
                        pt_mode: Optional[str] = None,
                        ct_mode: Optional[str] = None):
    sessions = _filter_sessions_for_range(
        db, date, start_date, end_date, start_hour, end_hour, project_id, channel_id,
    )
    shift_cycle_ids, sessions = _shift_filter_cycles(
        db, sessions, date, start_date, start_hour, end_hour,
    )
    # 显示口径联动
    ct_avg_on = (ct_mode == 'avg')
    pt_avg_on = (pt_mode == 'avg')

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

        # 该会话内 cycle / step 的平均聚合（仅 avg 模式用）
        sess_steps_for_avg = []
        if pt_avg_on:
            sess_steps_for_avg = (
                db.query(StepRecord)
                .filter(StepRecord.cycle_id.in_([c.id for c in cycles]))
                .all()
            )
        avg_cycle_dur_s, step_avg_map = _calc_aggregates(
            cycles, sess_steps_for_avg if pt_avg_on else None,
        )

        writer.writerow([f"[{sp_name} / {sch}] 会话 {s.session_uuid} 的周期详情"])
        headers = ["周期序号", "开始时间"]
        if opts['cycle_duration']:
            headers.append("耗时(秒)")
            if ct_avg_on:
                headers.append("耗时(平均/秒)")
        if opts['cycle_interval']: headers.append("周期间隔(秒)")
        if opts['cycle_result']:   headers.extend(["结果", "事件"])
        headers.append("步骤序列")
        writer.writerow(headers)

        for cycle in cycles:
            row = [cycle.cycle_number, cycle.start_time.strftime("%Y-%m-%d %H:%M:%S")]
            if opts['cycle_duration']:
                row.append(f"{cycle.duration:.2f}" if cycle.duration else "")
                if ct_avg_on:
                    row.append(avg_cycle_dur_s)
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
        if opts['step_duration']:
            step_headers.append("耗时(秒)")
            if pt_avg_on:
                step_headers.append("耗时(平均/秒)")
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
                    if pt_avg_on:
                        row.append(step_avg_map.get(step.step_label, ""))
                if opts['step_interval']:
                    iv = getattr(step, 'interval_to_next', None) or step.interval_from_prev
                    row.append(f"{iv:.2f}" if iv else "")
                if opts['step_event']:
                    row.append("是" if step.is_valid else "否")
                writer.writerow(row)
        writer.writerow([])


# ----- public entry -----

def build_csv_string(db, get_order_map, *,
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
                     channel_id: Optional[int],
                     pt_mode: Optional[str] = None,
                     ct_mode: Optional[str] = None) -> str:
    """v3.8.x: 抽出 builder 核心, 返回纯 CSV 字符串 (含 BOM 前缀, 调用方按需 encode)。

    与 build_csv_response 共用全部业务逻辑, 让定时导出 / 多格式 writer / 测试用例
    能直接拿原始内容, 不必经过 StreamingResponse 包一层。
    """
    opts = _load_export_opts(db)
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    start_date, end_date = _resolve_week_month(week, month, start_date, end_date)

    if export_type == "session" and session_id:
        _write_session_export(writer, db, session_id, opts, get_order_map,
                              pt_mode=pt_mode, ct_mode=ct_mode)
    elif export_type == "cycle" and cycle_id:
        _write_cycle_export(writer, db, cycle_id, opts, get_order_map,
                            pt_mode=pt_mode, ct_mode=ct_mode)
    else:
        _write_range_export(
            writer, db, opts, get_order_map,
            date=date, start_date=start_date, end_date=end_date,
            start_hour=start_hour, end_hour=end_hour,
            project_id=project_id, channel_id=channel_id,
            pt_mode=pt_mode, ct_mode=ct_mode,
        )

    buffer.seek(0)
    return '\ufeff' + buffer.getvalue()


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
                       channel_id: Optional[int],
                       pt_mode: Optional[str] = None,
                       ct_mode: Optional[str] = None,
                       output_format: str = "csv") -> StreamingResponse:
    """对外 HTTP 入口 (Data 页 4 个快捷按钮的服务端实现)。

    v3.8.x: 增加 output_format 入参支持 csv / txt / xlsx / docx / pdf 多格式下载,
    底层先拿 CSV 字符串再走 scheduled_writer 转格式 (DRY, 跟定时导出共用一份多格式渲染)。
    """
    try:
        csv_str = build_csv_string(
            db, get_order_map,
            export_type=export_type, session_id=session_id, cycle_id=cycle_id,
            date=date, start_date=start_date, end_date=end_date,
            week=week, month=month,
            start_hour=start_hour, end_hour=end_hour,
            project_id=project_id, channel_id=channel_id,
            pt_mode=pt_mode, ct_mode=ct_mode,
        )
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')

        fmt = (output_format or "csv").lower().strip()
        if fmt in ("csv", ""):
            csv_content = csv_str.encode('utf-8')
            return StreamingResponse(
                iter([csv_content]),
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=export_{ts}.csv",
                    "Content-Type": "text/csv; charset=utf-8",
                },
            )

        from backend.services.export_scheduled_writers import (
            csv_string_to_format_bytes, MEDIA_TYPES, FILE_EXTENSIONS,
        )
        body_bytes = csv_string_to_format_bytes(csv_str, fmt)
        ext = FILE_EXTENSIONS.get(fmt, fmt)
        media = MEDIA_TYPES.get(fmt, "application/octet-stream")
        return StreamingResponse(
            iter([body_bytes]),
            media_type=media,
            headers={
                "Content-Disposition": f"attachment; filename=export_{ts}.{ext}",
                "Content-Type": media,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"导出CSV失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")
