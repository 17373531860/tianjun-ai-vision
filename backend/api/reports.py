from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Optional
from datetime import datetime, timedelta
import io
from backend.db.database import get_db
from backend.models.models import Task, Project
from backend.schemas.report import ReportSummary, DailyStatResponse, TrendData

router = APIRouter()

def _apply_hour_filter(query, start_hour: Optional[str], end_hour: Optional[str]):
    """Apply hour-of-day filter for shift queries (e.g. day shift 08:00-20:00)."""
    if not start_hour or not end_hour:
        return query
    time_col = func.strftime('%H:%M', Task.timestamp)
    if start_hour <= end_hour:
        query = query.filter(and_(time_col >= start_hour, time_col < end_hour))
    else:
        from sqlalchemy import or_
        query = query.filter(or_(time_col >= start_hour, time_col < end_hour))
    return query


@router.get("/summary", response_model=ReportSummary)
def get_summary(
    project_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    start_hour: Optional[str] = None,
    end_hour: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """获取统计摘要"""
    query = db.query(Task)
    
    if project_id:
        query = query.filter(Task.project_id == project_id)
    if start_date:
        query = query.filter(Task.timestamp >= start_date)
    if end_date:
        query = query.filter(Task.timestamp <= end_date + " 23:59:59")
    query = _apply_hour_filter(query, start_hour, end_hour)
    
    total_count = query.count()
    good_count = query.filter(Task.is_good == True).count()
    bad_count = total_count - good_count
    
    yield_rate = (good_count / total_count * 100) if total_count > 0 else 0.0
    
    avg_duration_result = query.with_entities(func.avg(Task.duration)).scalar()
    avg_duration = float(avg_duration_result) if avg_duration_result else 0.0
    
    return ReportSummary(
        total_count=total_count,
        good_count=good_count,
        bad_count=bad_count,
        yield_rate=round(yield_rate, 2),
        avg_duration=round(avg_duration, 2)
    )

@router.get("/records", response_model=List[dict])
def get_records(
    project_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    start_hour: Optional[str] = None,
    end_hour: Optional[str] = None,
    is_good: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """获取历史检测记录"""
    query = db.query(Task)
    
    if project_id:
        query = query.filter(Task.project_id == project_id)
    if start_date:
        query = query.filter(Task.timestamp >= start_date)
    if end_date:
        query = query.filter(Task.timestamp <= end_date + " 23:59:59")
    query = _apply_hour_filter(query, start_hour, end_hour)
    if is_good is not None:
        query = query.filter(Task.is_good == is_good)
    
    tasks = query.order_by(Task.timestamp.desc()).offset(skip).limit(limit).all()
    
    records = []
    for task in tasks:
        project = db.query(Project).filter(Project.id == task.project_id).first()
        records.append({
            "id": task.id,
            "timestamp": task.timestamp.strftime("%Y-%m-%d %H:%M:%S") if task.timestamp else "",
            "projectName": project.name if project else "Unknown",
            "result": "OK" if task.is_good else "NG",
            "confidence": f"{task.confidence:.2f}" if task.confidence else "N/A",
            "imageUrl": task.input_file or "",
            "duration": task.duration,
            "stepName": task.step_name
        })
    
    return records

@router.get("/trend", response_model=TrendData)
def get_trend(
    project_id: Optional[int] = None,
    days: int = 7,
    db: Session = Depends(get_db)
):
    """获取趋势数据（近N天）"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    dates = []
    good_counts = []
    bad_counts = []
    yield_rates = []
    
    for i in range(days):
        date = start_date + timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        dates.append(date_str)
        
        query = db.query(Task).filter(
            func.date(Task.timestamp) == date_str
        )
        if project_id:
            query = query.filter(Task.project_id == project_id)
        
        total = query.count()
        good = query.filter(Task.is_good == True).count()
        bad = total - good
        
        good_counts.append(good)
        bad_counts.append(bad)
        yield_rates.append(round(good / total * 100, 2) if total > 0 else 0.0)
    
    return TrendData(
        dates=dates,
        good_counts=good_counts,
        bad_counts=bad_counts,
        yield_rates=yield_rates
    )

@router.get("/daily-stats", response_model=List[DailyStatResponse])
def get_daily_stats(
    project_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    start_hour: Optional[str] = None,
    end_hour: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """获取每日统计数据"""
    query = db.query(
        func.date(Task.timestamp).label('date'),
        func.count(Task.id).label('total_count'),
        func.sum(func.cast(Task.is_good, type_=db.bind.dialect.name == 'sqlite' and 'INTEGER' or 'INT')).label('good_count')
    )
    
    if project_id:
        query = query.filter(Task.project_id == project_id)
    if start_date:
        query = query.filter(Task.timestamp >= start_date)
    if end_date:
        query = query.filter(Task.timestamp <= end_date + " 23:59:59")
    query = _apply_hour_filter(query, start_hour, end_hour)
    
    results = query.group_by(func.date(Task.timestamp)).order_by(func.date(Task.timestamp)).all()
    
    stats = []
    for row in results:
        total = row.total_count or 0
        good = int(row.good_count or 0)
        bad = total - good
        yield_rate = round(good / total * 100, 2) if total > 0 else 0.0
        
        stats.append(DailyStatResponse(
            date=str(row.date),
            project_id=project_id,
            good_count=good,
            bad_count=bad,
            total_count=total,
            yield_rate=yield_rate
        ))
    
    return stats

@router.get("/export")
def export_report(
    project_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    start_hour: Optional[str] = None,
    end_hour: Optional[str] = None,
    format: str = "pdf",
    db: Session = Depends(get_db)
):
    """导出报表（PDF/Excel）"""
    summary = get_summary(project_id, start_date, end_date, start_hour, end_hour, db)
    records = get_records(project_id, start_date, end_date, start_hour, end_hour, None, 0, 1000, db)
    
    if format == "pdf":
        # 生成PDF
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        elements = []
        
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            spaceAfter=30
        )
        
        # 标题
        elements.append(Paragraph("Detection Report", title_style))
        elements.append(Spacer(1, 12))
        
        # 统计摘要表格
        summary_data = [
            ["Metric", "Value"],
            ["Total Count", str(summary.total_count)],
            ["Good Count", str(summary.good_count)],
            ["Bad Count", str(summary.bad_count)],
            ["Yield Rate", f"{summary.yield_rate}%"],
            ["Avg Duration", f"{summary.avg_duration} ms"]
        ]
        
        summary_table = Table(summary_data, colWidths=[200, 200])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        elements.append(summary_table)
        elements.append(Spacer(1, 24))
        
        # 详细记录表格（最多显示前50条）
        if records:
            elements.append(Paragraph("Recent Records (Top 50)", styles['Heading2']))
            elements.append(Spacer(1, 12))
            
            record_data = [["Time", "Project", "Result", "Confidence"]]
            for record in records[:50]:
                record_data.append([
                    record.get("timestamp", "")[:16],
                    record.get("projectName", "")[:15],
                    record.get("result", ""),
                    record.get("confidence", "")
                ])
            
            record_table = Table(record_data, colWidths=[120, 120, 80, 80])
            record_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)
            ]))
            
            elements.append(record_table)
        
        doc.build(elements)
        buffer.seek(0)
        
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=report.pdf"}
        )
    
    elif format == "csv":
        # 生成CSV
        import csv
        
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Timestamp", "Project", "Result", "Confidence", "Duration", "Step"])
        
        for record in records:
            writer.writerow([
                record.get("timestamp", ""),
                record.get("projectName", ""),
                record.get("result", ""),
                record.get("confidence", ""),
                record.get("duration", ""),
                record.get("stepName", "")
            ])
        
        buffer.seek(0)
        
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=report.csv"}
        )
    
    else:
        raise HTTPException(status_code=400, detail="Unsupported format. Use 'pdf' or 'csv'.")
