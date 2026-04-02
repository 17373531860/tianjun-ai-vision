"""
工单全生命周期管理服务

负责工单的创建、状态流转、计数更新、批次管理。
所有对外暴露的方法都接受 db session 参数，由调用者管理事务。
"""
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_
from backend.models.mes_models import WorkOrder, Batch


class WorkOrderService:

    # ---- 工单 CRUD ----

    def create_order(self, db: Session, data: dict) -> WorkOrder:
        order = WorkOrder(
            order_no=data["order_no"],
            product_name=data["product_name"],
            product_code=data.get("product_code"),
            product_spec=data.get("product_spec"),
            planned_qty=data.get("planned_qty", 0),
            project_id=data.get("project_id"),
            priority=data.get("priority", 3),
            status=data.get("status", "draft"),
            source=data.get("source", "manual"),
            planned_start=data.get("planned_start"),
            planned_end=data.get("planned_end"),
            customer_name=data.get("customer_name"),
            remark=data.get("remark"),
            extra_data=data.get("extra_data"),
            created_by=data.get("created_by"),
        )
        db.add(order)
        db.flush()
        return order

    def update_order(self, db: Session, order_id: int, data: dict) -> Optional[WorkOrder]:
        order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
        if not order:
            return None
        editable = [
            "product_name", "product_code", "product_spec", "planned_qty",
            "project_id", "priority", "planned_start", "planned_end",
            "customer_name", "remark", "extra_data",
        ]
        for field in editable:
            if field in data:
                setattr(order, field, data[field])
        db.flush()
        return order

    def change_status(self, db: Session, order_id: int, new_status: str,
                      reason: str = None) -> Optional[WorkOrder]:
        """状态变更，校验合法性"""
        order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
        if not order:
            return None

        valid = WorkOrder.VALID_TRANSITIONS.get(order.status, [])
        if new_status not in valid:
            raise ValueError(
                f"工单 {order.order_no} 无法从 '{order.status}' 变更为 '{new_status}'，"
                f"允许: {valid}"
            )

        order.status = new_status
        now = datetime.now()

        if new_status == "in_progress" and not order.actual_start:
            order.actual_start = now
        elif new_status in ("completed", "cancelled"):
            order.actual_end = now

        db.flush()
        return order

    def delete_order(self, db: Session, order_id: int) -> bool:
        order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
        if not order:
            return False
        if order.status not in ("draft", "cancelled"):
            raise ValueError(f"仅 draft/cancelled 状态的工单可删除，当前: {order.status}")
        db.delete(order)
        db.flush()
        return True

    # ---- 查询 ----

    def get_order(self, db: Session, order_id: int) -> Optional[WorkOrder]:
        return db.query(WorkOrder).filter(WorkOrder.id == order_id).first()

    def get_order_by_no(self, db: Session, order_no: str) -> Optional[WorkOrder]:
        return db.query(WorkOrder).filter(WorkOrder.order_no == order_no).first()

    def get_active_order(self, db: Session, project_id: int) -> Optional[WorkOrder]:
        """获取指定项目当前 in_progress 的工单（按优先级排序取第一个）"""
        return (
            db.query(WorkOrder)
            .filter(WorkOrder.project_id == project_id,
                    WorkOrder.status == "in_progress")
            .order_by(WorkOrder.priority.asc(), WorkOrder.created_at.asc())
            .first()
        )

    def list_orders(self, db: Session, *, status: str = None, project_id: int = None,
                    keyword: str = None, date_from: str = None, date_to: str = None,
                    skip: int = 0, limit: int = 50) -> tuple[list[WorkOrder], int]:
        q = db.query(WorkOrder)
        if status:
            q = q.filter(WorkOrder.status == status)
        if project_id:
            q = q.filter(WorkOrder.project_id == project_id)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(or_(
                WorkOrder.order_no.ilike(like),
                WorkOrder.product_name.ilike(like),
                WorkOrder.customer_name.ilike(like),
            ))
        if date_from:
            q = q.filter(WorkOrder.created_at >= date_from)
        if date_to:
            q = q.filter(WorkOrder.created_at <= date_to)

        total = q.count()
        items = q.order_by(desc(WorkOrder.created_at)).offset(skip).limit(limit).all()
        return items, total

    # ---- 计数更新 (由 MESHook 调用, 使用 SQL 原子操作) ----

    def increment_completed(self, db: Session, order_id: int, is_good: bool):
        """原子递增工单计数, 避免并发竞争"""
        updates = {
            WorkOrder.completed_qty: WorkOrder.completed_qty + 1,
        }
        if is_good:
            updates[WorkOrder.good_qty] = WorkOrder.good_qty + 1
        else:
            updates[WorkOrder.ng_qty] = WorkOrder.ng_qty + 1

        db.query(WorkOrder).filter(WorkOrder.id == order_id).update(
            updates, synchronize_session="fetch"
        )
        self._refresh_yield(db, order_id)
        db.flush()

    def increment_rework(self, db: Session, order_id: int):
        db.query(WorkOrder).filter(WorkOrder.id == order_id).update(
            {WorkOrder.rework_qty: WorkOrder.rework_qty + 1},
            synchronize_session="fetch"
        )
        db.flush()

    def increment_scrap(self, db: Session, order_id: int):
        db.query(WorkOrder).filter(WorkOrder.id == order_id).update(
            {WorkOrder.scrap_qty: WorkOrder.scrap_qty + 1},
            synchronize_session="fetch"
        )
        db.flush()

    def check_completion(self, db: Session, order_id: int) -> bool:
        """检查工单是否已达成计划数量"""
        order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
        if not order or order.planned_qty <= 0:
            return False
        return order.completed_qty >= order.planned_qty

    def _refresh_yield(self, db: Session, order_id: int):
        order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
        if order and order.completed_qty > 0:
            order.yield_rate = round(order.good_qty / order.completed_qty * 100, 2)

    # ---- 批次管理 ----

    def create_batch(self, db: Session, order_id: int, data: dict) -> Batch:
        batch = Batch(
            order_id=order_id,
            batch_no=data["batch_no"],
            material_lot=data.get("material_lot"),
            planned_qty=data.get("planned_qty", 0),
        )
        db.add(batch)
        db.flush()
        return batch

    def list_batches(self, db: Session, order_id: int) -> list[Batch]:
        return db.query(Batch).filter(Batch.order_id == order_id).all()

    # ---- 统计 ----

    def get_order_summary(self, db: Session, order_id: int) -> dict:
        order = self.get_order(db, order_id)
        if not order:
            return {}
        return {
            "order_no": order.order_no,
            "product_name": order.product_name,
            "status": order.status,
            "planned_qty": order.planned_qty,
            "completed_qty": order.completed_qty,
            "good_qty": order.good_qty,
            "ng_qty": order.ng_qty,
            "rework_qty": order.rework_qty,
            "scrap_qty": order.scrap_qty,
            "yield_rate": order.yield_rate,
            "progress": round(order.completed_qty / order.planned_qty * 100, 1)
                        if order.planned_qty > 0 else 0,
        }
