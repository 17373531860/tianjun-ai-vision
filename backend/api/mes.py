"""
MES 系统 REST API

工单管理、工件追溯、缺陷记录、质量统计的完整 CRUD 端点。
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from backend.db.database import SessionLocal
from backend.services.work_order import WorkOrderService
from backend.services.workpiece import WorkpieceService
from backend.services.defect import DefectService

router = APIRouter(prefix="/mes", tags=["MES"])

_wo_svc = WorkOrderService()
_wp_svc = WorkpieceService()
_defect_svc = DefectService()


# ============================================================
# Pydantic Schema
# ============================================================

class OrderCreate(BaseModel):
    order_no: str
    product_name: str
    product_code: Optional[str] = None
    product_spec: Optional[str] = None
    planned_qty: int = 0
    project_id: Optional[int] = None
    priority: int = 3
    status: str = "draft"
    source: str = "manual"
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    customer_name: Optional[str] = None
    remark: Optional[str] = None
    extra_data: Optional[dict] = None
    created_by: Optional[str] = None

class OrderUpdate(BaseModel):
    product_name: Optional[str] = None
    product_code: Optional[str] = None
    product_spec: Optional[str] = None
    planned_qty: Optional[int] = None
    project_id: Optional[int] = None
    priority: Optional[int] = None
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    customer_name: Optional[str] = None
    remark: Optional[str] = None
    extra_data: Optional[dict] = None

class StatusChange(BaseModel):
    status: str
    reason: Optional[str] = None

class BatchCreate(BaseModel):
    batch_no: str
    material_lot: Optional[str] = None
    planned_qty: int = 0

class WorkpieceRegister(BaseModel):
    serial_no: str
    project_id: int
    raw_barcode: Optional[str] = None
    order_id: Optional[int] = None
    batch_id: Optional[int] = None
    channel_id: Optional[int] = None
    operator: Optional[str] = None
    scan_source: str = "manual"

class WorkpieceAction(BaseModel):
    action: str  # "rework", "scrap", "confirm"
    final_result: Optional[str] = None
    reason: Optional[str] = None

class DefectManual(BaseModel):
    workpiece_id: int
    defect_code: str
    defect_name: str
    defect_category: str = "other"
    severity: str = "minor"
    cycle_id: Optional[int] = None
    inspection_id: Optional[int] = None
    detection_label: Optional[str] = None
    confidence: Optional[float] = None
    bbox_x: Optional[float] = None
    bbox_y: Optional[float] = None
    bbox_w: Optional[float] = None
    bbox_h: Optional[float] = None
    screenshot_path: Optional[str] = None
    description: Optional[str] = None

class DefectCodeCreate(BaseModel):
    code: str
    name: str
    category: str = "other"
    severity: str = "minor"
    project_id: Optional[int] = None
    detection_labels: Optional[List[str]] = None
    description: Optional[str] = None

class DefectCodeUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    severity: Optional[str] = None
    detection_labels: Optional[List[str]] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


def _serialize_order(o):
    return {
        "id": o.id, "order_no": o.order_no, "external_id": o.external_id,
        "product_name": o.product_name, "product_code": o.product_code,
        "product_spec": o.product_spec,
        "planned_qty": o.planned_qty, "completed_qty": o.completed_qty,
        "good_qty": o.good_qty, "ng_qty": o.ng_qty,
        "rework_qty": o.rework_qty, "scrap_qty": o.scrap_qty,
        "yield_rate": o.yield_rate,
        "project_id": o.project_id, "priority": o.priority,
        "status": o.status, "source": o.source,
        "planned_start": o.planned_start.isoformat() if o.planned_start else None,
        "planned_end": o.planned_end.isoformat() if o.planned_end else None,
        "actual_start": o.actual_start.isoformat() if o.actual_start else None,
        "actual_end": o.actual_end.isoformat() if o.actual_end else None,
        "customer_name": o.customer_name, "remark": o.remark,
        "extra_data": o.extra_data, "created_by": o.created_by,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "updated_at": o.updated_at.isoformat() if o.updated_at else None,
    }


def _serialize_workpiece(w):
    return {
        "id": w.id, "serial_no": w.serial_no, "raw_barcode": w.raw_barcode,
        "order_id": w.order_id, "batch_id": w.batch_id, "project_id": w.project_id,
        "status": w.status, "inspection_count": w.inspection_count,
        "latest_cycle_id": w.latest_cycle_id, "final_result": w.final_result,
        "channel_id": w.channel_id, "operator": w.operator,
        "scan_source": w.scan_source, "scan_device_id": w.scan_device_id,
        "registered_at": w.registered_at.isoformat() if w.registered_at else None,
        "first_inspect_at": w.first_inspect_at.isoformat() if w.first_inspect_at else None,
        "last_inspect_at": w.last_inspect_at.isoformat() if w.last_inspect_at else None,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


def _serialize_defect(d):
    return {
        "id": d.id, "defect_uuid": d.defect_uuid,
        "workpiece_id": d.workpiece_id, "inspection_id": d.inspection_id,
        "cycle_id": d.cycle_id, "step_record_id": d.step_record_id,
        "defect_code": d.defect_code, "defect_name": d.defect_name,
        "defect_category": d.defect_category, "severity": d.severity,
        "confidence": d.confidence, "detection_label": d.detection_label,
        "bbox_x": d.bbox_x, "bbox_y": d.bbox_y,
        "bbox_w": d.bbox_w, "bbox_h": d.bbox_h,
        "screenshot_path": d.screenshot_path, "description": d.description,
        "source": d.source,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _serialize_inspection(i):
    return {
        "id": i.id, "workpiece_id": i.workpiece_id,
        "cycle_id": i.cycle_id, "session_id": i.session_id,
        "inspection_seq": i.inspection_seq, "result": i.result,
        "event_name": i.event_name, "result_reason": i.result_reason,
        "channel_id": i.channel_id, "duration": i.duration,
        "created_at": i.created_at.isoformat() if i.created_at else None,
    }


# ============================================================
# 工单 API
# ============================================================

@router.get("/orders")
def list_orders(
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = SessionLocal()
    try:
        items, total = _wo_svc.list_orders(
            db, status=status, project_id=project_id, keyword=keyword,
            date_from=date_from, date_to=date_to, skip=skip, limit=limit,
        )
        return {
            "items": [_serialize_order(o) for o in items],
            "total": total, "skip": skip, "limit": limit,
        }
    finally:
        db.close()


@router.post("/orders")
def create_order(body: OrderCreate):
    db = SessionLocal()
    try:
        order = _wo_svc.create_order(db, body.model_dump())
        db.commit()
        return _serialize_order(order)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.get("/orders/{order_id}")
def get_order(order_id: int):
    db = SessionLocal()
    try:
        order = _wo_svc.get_order(db, order_id)
        if not order:
            raise HTTPException(404, "工单不存在")
        return _serialize_order(order)
    finally:
        db.close()


@router.put("/orders/{order_id}")
def update_order(order_id: int, body: OrderUpdate):
    db = SessionLocal()
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        order = _wo_svc.update_order(db, order_id, data)
        if not order:
            raise HTTPException(404, "工单不存在")
        db.commit()
        return _serialize_order(order)
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.post("/orders/{order_id}/status")
def change_order_status(order_id: int, body: StatusChange):
    db = SessionLocal()
    try:
        order = _wo_svc.change_status(db, order_id, body.status, body.reason)
        if not order:
            raise HTTPException(404, "工单不存在")
        db.commit()
        return _serialize_order(order)
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.delete("/orders/{order_id}")
def delete_order(order_id: int):
    db = SessionLocal()
    try:
        ok = _wo_svc.delete_order(db, order_id)
        if not ok:
            raise HTTPException(404, "工单不存在")
        db.commit()
        return {"success": True}
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.get("/orders/{order_id}/summary")
def get_order_summary(order_id: int):
    db = SessionLocal()
    try:
        return _wo_svc.get_order_summary(db, order_id)
    finally:
        db.close()


# ---- 批次 ----

@router.post("/orders/{order_id}/batches")
def create_batch(order_id: int, body: BatchCreate):
    db = SessionLocal()
    try:
        batch = _wo_svc.create_batch(db, order_id, body.model_dump())
        db.commit()
        return {
            "id": batch.id, "batch_no": batch.batch_no,
            "order_id": batch.order_id, "material_lot": batch.material_lot,
            "planned_qty": batch.planned_qty,
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.get("/orders/{order_id}/batches")
def list_batches(order_id: int):
    db = SessionLocal()
    try:
        batches = _wo_svc.list_batches(db, order_id)
        return [{
            "id": b.id, "batch_no": b.batch_no, "order_id": b.order_id,
            "material_lot": b.material_lot, "planned_qty": b.planned_qty,
            "completed_qty": b.completed_qty, "good_qty": b.good_qty,
            "ng_qty": b.ng_qty, "status": b.status,
        } for b in batches]
    finally:
        db.close()


# ============================================================
# 工件 API
# ============================================================

@router.get("/workpieces")
def list_workpieces(
    order_id: Optional[int] = None,
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = SessionLocal()
    try:
        items, total = _wp_svc.list_workpieces(
            db, order_id=order_id, status=status, project_id=project_id,
            keyword=keyword, date_from=date_from, date_to=date_to,
            skip=skip, limit=limit,
        )
        return {
            "items": [_serialize_workpiece(w) for w in items],
            "total": total, "skip": skip, "limit": limit,
        }
    finally:
        db.close()


@router.post("/workpieces")
def register_workpiece(body: WorkpieceRegister):
    db = SessionLocal()
    try:
        wp = _wp_svc.register(db, body.serial_no, body.project_id,
                              **body.model_dump(exclude={"serial_no", "project_id"}))
        db.commit()
        return _serialize_workpiece(wp)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.get("/workpieces/{workpiece_id}")
def get_workpiece(workpiece_id: int):
    db = SessionLocal()
    try:
        wp = _wp_svc.get_by_id(db, workpiece_id)
        if not wp:
            raise HTTPException(404, "工件不存在")
        return _serialize_workpiece(wp)
    finally:
        db.close()


@router.get("/workpieces/{workpiece_id}/trace")
def get_workpiece_trace(workpiece_id: int):
    db = SessionLocal()
    try:
        trace = _wp_svc.get_full_trace(db, workpiece_id)
        if not trace:
            raise HTTPException(404, "工件不存在")
        return {
            "workpiece": _serialize_workpiece(trace["workpiece"]),
            "inspections": [_serialize_inspection(i) for i in trace["inspections"]],
            "defects": [_serialize_defect(d) for d in trace["defects"]],
        }
    finally:
        db.close()


@router.post("/workpieces/{workpiece_id}/action")
def workpiece_action(workpiece_id: int, body: WorkpieceAction):
    db = SessionLocal()
    try:
        if body.action == "rework":
            wp = _wp_svc.mark_rework(db, workpiece_id, body.reason)
        elif body.action == "scrap":
            wp = _wp_svc.mark_scrapped(db, workpiece_id, body.reason)
        elif body.action == "confirm":
            wp = _wp_svc.confirm_result(db, workpiece_id, body.final_result or "ok")
        else:
            raise HTTPException(400, f"不支持的操作: {body.action}")

        if not wp:
            raise HTTPException(404, "工件不存在")
        db.commit()
        return _serialize_workpiece(wp)
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.get("/workpieces/search/{keyword}")
def search_workpieces(keyword: str, project_id: Optional[int] = None,
                      limit: int = Query(20, ge=1, le=100)):
    db = SessionLocal()
    try:
        items = _wp_svc.search_by_serial(db, keyword, project_id, limit)
        return [_serialize_workpiece(w) for w in items]
    finally:
        db.close()


# ============================================================
# 缺陷 API
# ============================================================

@router.get("/defects")
def list_defects(
    workpiece_id: Optional[int] = None,
    order_id: Optional[int] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    project_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = SessionLocal()
    try:
        items, total = _defect_svc.list_defects(
            db, workpiece_id=workpiece_id, order_id=order_id,
            category=category, severity=severity, project_id=project_id,
            date_from=date_from, date_to=date_to, skip=skip, limit=limit,
        )
        return {
            "items": [_serialize_defect(d) for d in items],
            "total": total, "skip": skip, "limit": limit,
        }
    finally:
        db.close()


@router.post("/defects")
def create_defect(body: DefectManual):
    db = SessionLocal()
    try:
        rec = _defect_svc.manual_record(db, body.model_dump())
        db.commit()
        return _serialize_defect(rec)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.get("/defects/pareto")
def get_pareto(project_id: Optional[int] = None,
               order_id: Optional[int] = None,
               limit: int = Query(10, ge=1, le=50)):
    db = SessionLocal()
    try:
        return _defect_svc.get_pareto_data(db, project_id, order_id, limit)
    finally:
        db.close()


# ---- 缺陷代码字典 ----

@router.get("/defect-codes")
def list_defect_codes(project_id: Optional[int] = None):
    db = SessionLocal()
    try:
        codes = _defect_svc.get_defect_codes(db, project_id)
        return [{
            "id": c.id, "code": c.code, "name": c.name,
            "category": c.category, "severity": c.severity,
            "project_id": c.project_id,
            "detection_labels": c.detection_labels,
            "description": c.description, "is_active": c.is_active,
        } for c in codes]
    finally:
        db.close()


@router.post("/defect-codes")
def create_defect_code(body: DefectCodeCreate):
    db = SessionLocal()
    try:
        code = _defect_svc.create_defect_code(db, body.model_dump())
        db.commit()
        return {"id": code.id, "code": code.code, "name": code.name}
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.put("/defect-codes/{code_id}")
def update_defect_code(code_id: int, body: DefectCodeUpdate):
    db = SessionLocal()
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        code = _defect_svc.update_defect_code(db, code_id, data)
        if not code:
            raise HTTPException(404, "缺陷代码不存在")
        db.commit()
        return {"id": code.id, "code": code.code, "name": code.name}
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.delete("/defect-codes/{code_id}")
def delete_defect_code(code_id: int):
    db = SessionLocal()
    try:
        ok = _defect_svc.delete_defect_code(db, code_id)
        if not ok:
            raise HTTPException(404, "缺陷代码不存在")
        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()
