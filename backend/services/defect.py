"""
缺陷记录与自动分类服务

负责缺陷自动记录（Cycle NG → 从检测标签映射缺陷代码）、
手动录入、统计查询。
"""
import uuid
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc, func as sql_func
from backend.models.mes_models import DefectRecord, DefectCode


class DefectService:

    # ---- 自动记录 (由 MESHook 在 Cycle NG 时调用) ----

    def auto_record(self, db: Session, workpiece_id: int, cycle_id: int,
                    inspection_id: int = None, event_name: str = None,
                    result_reason: str = None, step_sequence: list = None,
                    project_id: int = None):
        """根据 NG 事件自动创建缺陷记录

        策略:
        1. 从 result_reason 提取涉及的步骤标签
        2. 在 defect_codes 中按 detection_labels 匹配
        3. 未匹配到则使用 event_name 作为通用缺陷
        """
        labels = self._extract_labels(result_reason, step_sequence)

        if not labels:
            self._create_generic_defect(
                db, workpiece_id, cycle_id, inspection_id,
                event_name or "NG", result_reason
            )
            return

        matched_any = False
        codes = self._get_active_codes(db, project_id)

        for label in labels:
            for code_entry in codes:
                mapping = code_entry.detection_labels or []
                if label in mapping:
                    self._create_defect(
                        db, workpiece_id, cycle_id, inspection_id,
                        code_entry.code, code_entry.name, code_entry.category,
                        code_entry.severity, label, result_reason
                    )
                    matched_any = True
                    break

            if not matched_any:
                self._create_defect(
                    db, workpiece_id, cycle_id, inspection_id,
                    f"AUTO-{label[:20]}", label, "other", "minor",
                    label, result_reason
                )
                matched_any = True

    def _extract_labels(self, result_reason: str, step_sequence: list = None) -> list[str]:
        """从 result_reason 提取涉及的步骤/标签名"""
        labels = []
        if result_reason:
            for part in result_reason.replace("，", ",").replace("、", ",").split(","):
                part = part.strip()
                if part and len(part) < 64:
                    cleaned = part.split("缺失")[-1].split("多余")[-1].strip()
                    if cleaned:
                        labels.append(cleaned)
                    elif part:
                        labels.append(part)
        if not labels and step_sequence:
            labels = [str(s) for s in step_sequence[-3:]]
        return labels

    def _get_active_codes(self, db: Session, project_id: int = None) -> list[DefectCode]:
        q = db.query(DefectCode).filter(DefectCode.is_active == True)
        if project_id:
            q = q.filter(
                (DefectCode.project_id == project_id) | (DefectCode.project_id.is_(None))
            )
        return q.all()

    def _create_defect(self, db: Session, workpiece_id: int, cycle_id: int,
                       inspection_id: int, code: str, name: str, category: str,
                       severity: str, detection_label: str = None,
                       description: str = None):
        rec = DefectRecord(
            defect_uuid=uuid.uuid4().hex[:16],
            workpiece_id=workpiece_id,
            cycle_id=cycle_id,
            inspection_id=inspection_id,
            defect_code=code,
            defect_name=name,
            defect_category=category,
            severity=severity,
            detection_label=detection_label,
            description=description,
            source="auto",
        )
        db.add(rec)
        db.flush()

    def _create_generic_defect(self, db: Session, workpiece_id: int, cycle_id: int,
                               inspection_id: int, event_name: str, reason: str):
        self._create_defect(
            db, workpiece_id, cycle_id, inspection_id,
            "NG-GENERAL", event_name, "other", "minor",
            description=reason
        )

    # ---- 手动录入 ----

    def manual_record(self, db: Session, data: dict) -> DefectRecord:
        rec = DefectRecord(
            defect_uuid=uuid.uuid4().hex[:16],
            workpiece_id=data["workpiece_id"],
            cycle_id=data.get("cycle_id"),
            inspection_id=data.get("inspection_id"),
            defect_code=data["defect_code"],
            defect_name=data["defect_name"],
            defect_category=data.get("defect_category", "other"),
            severity=data.get("severity", "minor"),
            confidence=data.get("confidence"),
            detection_label=data.get("detection_label"),
            bbox_x=data.get("bbox_x"),
            bbox_y=data.get("bbox_y"),
            bbox_w=data.get("bbox_w"),
            bbox_h=data.get("bbox_h"),
            screenshot_path=data.get("screenshot_path"),
            description=data.get("description"),
            source="manual",
        )
        db.add(rec)
        db.flush()
        return rec

    # ---- 查询 ----

    def list_defects(self, db: Session, *, workpiece_id: int = None,
                     order_id: int = None, category: str = None,
                     severity: str = None, project_id: int = None,
                     date_from: str = None, date_to: str = None,
                     skip: int = 0, limit: int = 50) -> tuple[list[DefectRecord], int]:
        from backend.models.mes_models import Workpiece
        q = db.query(DefectRecord)
        if workpiece_id:
            q = q.filter(DefectRecord.workpiece_id == workpiece_id)
        if order_id:
            q = q.join(Workpiece).filter(Workpiece.order_id == order_id)
        if category:
            q = q.filter(DefectRecord.defect_category == category)
        if severity:
            q = q.filter(DefectRecord.severity == severity)
        if project_id:
            q = q.join(Workpiece).filter(Workpiece.project_id == project_id)
        if date_from:
            q = q.filter(DefectRecord.created_at >= date_from)
        if date_to:
            q = q.filter(DefectRecord.created_at <= date_to)

        total = q.count()
        items = q.order_by(desc(DefectRecord.created_at)).offset(skip).limit(limit).all()
        return items, total

    def get_pareto_data(self, db: Session, project_id: int = None,
                        order_id: int = None, limit: int = 10) -> list[dict]:
        """Pareto 图数据: 缺陷代码 → 出现次数, 按数量降序"""
        from backend.models.mes_models import Workpiece
        q = (
            db.query(
                DefectRecord.defect_code,
                DefectRecord.defect_name,
                DefectRecord.defect_category,
                sql_func.count(DefectRecord.id).label("count"),
            )
            .group_by(DefectRecord.defect_code, DefectRecord.defect_name,
                      DefectRecord.defect_category)
        )
        if project_id:
            q = q.join(Workpiece).filter(Workpiece.project_id == project_id)
        if order_id:
            q = q.join(Workpiece).filter(Workpiece.order_id == order_id)

        rows = q.order_by(desc("count")).limit(limit).all()
        return [
            {"code": r.defect_code, "name": r.defect_name,
             "category": r.defect_category, "count": r.count}
            for r in rows
        ]

    # ---- 缺陷代码字典 ----

    def get_defect_codes(self, db: Session, project_id: int = None) -> list[DefectCode]:
        q = db.query(DefectCode).filter(DefectCode.is_active == True)
        if project_id:
            q = q.filter(
                (DefectCode.project_id == project_id) | (DefectCode.project_id.is_(None))
            )
        return q.all()

    def create_defect_code(self, db: Session, data: dict) -> DefectCode:
        code = DefectCode(
            code=data["code"],
            name=data["name"],
            category=data.get("category", "other"),
            severity=data.get("severity", "minor"),
            project_id=data.get("project_id"),
            detection_labels=data.get("detection_labels"),
            description=data.get("description"),
        )
        db.add(code)
        db.flush()
        return code

    def update_defect_code(self, db: Session, code_id: int, data: dict) -> Optional[DefectCode]:
        code = db.query(DefectCode).filter(DefectCode.id == code_id).first()
        if not code:
            return None
        for field in ["name", "category", "severity", "detection_labels",
                      "description", "is_active"]:
            if field in data:
                setattr(code, field, data[field])
        db.flush()
        return code

    def delete_defect_code(self, db: Session, code_id: int) -> bool:
        code = db.query(DefectCode).filter(DefectCode.id == code_id).first()
        if not code:
            return False
        db.delete(code)
        db.flush()
        return True
