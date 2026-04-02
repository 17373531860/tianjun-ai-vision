"""
工件追溯与状态管理服务

负责工件登记、状态流转、检测关联、追溯查询。
设计为轻量调用，不阻塞检测帧率。
"""
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_
from backend.models.mes_models import Workpiece, WorkpieceInspection


class WorkpieceService:

    # ---- 登记 & 查找 ----

    def register(self, db: Session, serial_no: str, project_id: int,
                 **kwargs) -> Workpiece:
        """登记新工件，若同项目内已有相同序列号则返回已有记录"""
        existing = self.find_by_serial(db, serial_no, project_id)
        if existing:
            if existing.status in ("ok", "ng"):
                existing.status = "queued"
            return existing

        wp = Workpiece(
            serial_no=serial_no,
            project_id=project_id,
            raw_barcode=kwargs.get("raw_barcode"),
            order_id=kwargs.get("order_id"),
            batch_id=kwargs.get("batch_id"),
            channel_id=kwargs.get("channel_id"),
            operator=kwargs.get("operator"),
            scan_source=kwargs.get("scan_source", "manual"),
            scan_device_id=kwargs.get("scan_device_id"),
            extra_data=kwargs.get("extra_data"),
        )
        db.add(wp)
        db.flush()
        return wp

    def find_by_serial(self, db: Session, serial_no: str,
                       project_id: int) -> Optional[Workpiece]:
        return (
            db.query(Workpiece)
            .filter(Workpiece.serial_no == serial_no,
                    Workpiece.project_id == project_id)
            .first()
        )

    def get_by_id(self, db: Session, workpiece_id: int) -> Optional[Workpiece]:
        return db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()

    # ---- 状态更新 ----

    def update_status(self, db: Session, workpiece_id: int, new_status: str):
        wp = db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()
        if wp:
            wp.status = new_status
            wp.updated_at = datetime.now()
            db.flush()

    def mark_inspecting(self, db: Session, workpiece_id: int):
        wp = db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()
        if not wp:
            return
        wp.status = "inspecting"
        wp.inspection_count += 1
        now = datetime.now()
        if not wp.first_inspect_at:
            wp.first_inspect_at = now
        wp.last_inspect_at = now
        db.flush()

    def set_result(self, db: Session, workpiece_id: int, is_good: bool,
                   cycle_id: int = None):
        wp = db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()
        if not wp:
            return
        wp.status = "ok" if is_good else "ng"
        if cycle_id:
            wp.latest_cycle_id = cycle_id
        wp.last_inspect_at = datetime.now()
        db.flush()

    # ---- 人工操作 ----

    def mark_rework(self, db: Session, workpiece_id: int, reason: str = None):
        wp = db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()
        if not wp:
            return None
        if wp.status != "ng":
            raise ValueError(f"仅 NG 工件可标记返工，当前: {wp.status}")
        wp.status = "rework"
        db.flush()
        return wp

    def mark_scrapped(self, db: Session, workpiece_id: int, reason: str = None):
        wp = db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()
        if not wp:
            return None
        if wp.status not in ("ng", "rework"):
            raise ValueError(f"仅 NG/rework 工件可报废，当前: {wp.status}")
        wp.status = "scrapped"
        wp.final_result = "scrapped"
        db.flush()
        return wp

    def confirm_result(self, db: Session, workpiece_id: int, final_result: str):
        wp = db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()
        if not wp:
            return None
        wp.final_result = final_result
        db.flush()
        return wp

    # ---- 检测关联 ----

    def link_to_cycle(self, db: Session, workpiece_id: int, cycle_id: int,
                      session_id: int = None, channel_id: int = None) -> WorkpieceInspection:
        """创建工件-检测周期关联记录"""
        wp = db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()
        seq = wp.inspection_count if wp else 1

        insp = WorkpieceInspection(
            workpiece_id=workpiece_id,
            cycle_id=cycle_id,
            session_id=session_id,
            inspection_seq=seq,
            channel_id=channel_id,
        )
        db.add(insp)
        db.flush()
        return insp

    def update_inspection_result(self, db: Session, workpiece_id: int,
                                 cycle_id: int, result: str, **kwargs):
        insp = (
            db.query(WorkpieceInspection)
            .filter(WorkpieceInspection.workpiece_id == workpiece_id,
                    WorkpieceInspection.cycle_id == cycle_id)
            .first()
        )
        if insp:
            insp.result = result
            insp.event_name = kwargs.get("event_name")
            insp.result_reason = kwargs.get("result_reason")
            insp.duration = kwargs.get("duration")
            db.flush()

    # ---- 追溯查询 ----

    def get_full_trace(self, db: Session, workpiece_id: int) -> dict:
        """完整追溯链: 工件 + 所有检测 + 所有缺陷"""
        wp = self.get_by_id(db, workpiece_id)
        if not wp:
            return {}

        inspections = (
            db.query(WorkpieceInspection)
            .filter(WorkpieceInspection.workpiece_id == workpiece_id)
            .order_by(WorkpieceInspection.inspection_seq)
            .all()
        )

        from backend.models.mes_models import DefectRecord
        defects = (
            db.query(DefectRecord)
            .filter(DefectRecord.workpiece_id == workpiece_id)
            .order_by(DefectRecord.created_at)
            .all()
        )

        return {
            "workpiece": wp,
            "inspections": inspections,
            "defects": defects,
        }

    def list_workpieces(self, db: Session, *, order_id: int = None,
                        status: str = None, project_id: int = None,
                        keyword: str = None, date_from: str = None,
                        date_to: str = None,
                        skip: int = 0, limit: int = 50) -> tuple[list[Workpiece], int]:
        q = db.query(Workpiece)
        if order_id:
            q = q.filter(Workpiece.order_id == order_id)
        if status:
            q = q.filter(Workpiece.status == status)
        if project_id:
            q = q.filter(Workpiece.project_id == project_id)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(or_(
                Workpiece.serial_no.ilike(like),
                Workpiece.raw_barcode.ilike(like),
            ))
        if date_from:
            q = q.filter(Workpiece.created_at >= date_from)
        if date_to:
            q = q.filter(Workpiece.created_at <= date_to)

        total = q.count()
        items = q.order_by(desc(Workpiece.created_at)).offset(skip).limit(limit).all()
        return items, total

    def search_by_serial(self, db: Session, keyword: str,
                         project_id: int = None, limit: int = 20) -> list[Workpiece]:
        q = db.query(Workpiece).filter(
            Workpiece.serial_no.ilike(f"%{keyword}%")
        )
        if project_id:
            q = q.filter(Workpiece.project_id == project_id)
        return q.order_by(desc(Workpiece.created_at)).limit(limit).all()
