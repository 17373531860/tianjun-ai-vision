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

    @staticmethod
    def _normalize_binding(data: dict, *, strict: bool = True) -> dict:
        """规范化绑定字段并校验, 返回需要写入的子字段字典.

        v3.1.0: 工单按 binding_scope 三选一绑定:
          - project  : 按 project_id 匹配 (strict 模式下必填)
          - channels : 必须有 target_channels (非空数组), project_id 强制清空
          - cluster  : 必须有 target_stations (非空数组), project_id 强制清空

        strict=False 时为外部 MES 推工单等场景: 没绑就允许 project_id 为空入库,
        前端列表里会显示"未绑定项目"警告, 不会被 get_active_order 拿到 → 自然不计件.
        """
        scope = (data.get("binding_scope") or "project").lower()
        if scope not in ("project", "channels", "cluster"):
            raise ValueError(f"binding_scope 必须是 project/channels/cluster, 收到: {scope}")

        out = {"binding_scope": scope}

        if scope == "project":
            pid = data.get("project_id")
            if pid in (None, "", 0):
                if strict:
                    raise ValueError("绑定项目模式下, project_id 必填")
                out["project_id"] = None
            else:
                out["project_id"] = int(pid)
            out["target_channels"] = None
            out["target_stations"] = None
        elif scope == "channels":
            tc = data.get("target_channels") or []
            if not isinstance(tc, list) or not tc:
                raise ValueError("绑定工位模式下, target_channels 必须是非空数组, 例如 [0, 1]")
            try:
                tc = [int(x) for x in tc]
            except Exception:
                raise ValueError("target_channels 元素必须是整数工位号")
            out["project_id"] = None
            out["target_channels"] = tc
            out["target_stations"] = None
        else:  # cluster
            # v3.1.0 简化: 工单建在主机 = 整个集群所有完成的 box 都给它计件,
            # 不再要求选"目标站点". target_stations 字段保留作高级扩展位 (将来
            # 想做"分站点工单"还能用), UI 不暴露; 默认 None 时由
            # find_cluster_orders 走"取首条 in_progress cluster 工单"的逻辑.
            ts = data.get("target_stations")
            out["project_id"] = None
            out["target_channels"] = None
            if isinstance(ts, list) and ts:
                out["target_stations"] = [str(x) for x in ts]
            else:
                out["target_stations"] = None
        return out

    def create_order(self, db: Session, data: dict) -> WorkOrder:
        # 创建场景: 外部 MES 推送 (source=external) 时允许 project_id 为空
        # (前端列表会显示"未绑定项目"警告), 用户手动新建时前端必须传完整 binding.
        strict = data.get("source") != "external"
        binding = self._normalize_binding(data, strict=strict)
        order = WorkOrder(
            order_no=data["order_no"],
            product_name=data["product_name"],
            product_code=data.get("product_code"),
            product_spec=data.get("product_spec"),
            planned_qty=data.get("planned_qty", 0),
            priority=data.get("priority", 3),
            status=data.get("status", "draft"),
            source=data.get("source", "manual"),
            planned_start=data.get("planned_start"),
            planned_end=data.get("planned_end"),
            customer_name=data.get("customer_name"),
            remark=data.get("remark"),
            extra_data=data.get("extra_data"),
            created_by=data.get("created_by"),
            **binding,
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
            "priority", "planned_start", "planned_end",
            "customer_name", "remark", "extra_data",
        ]
        for field in editable:
            if field in data:
                setattr(order, field, data[field])

        # v3.1.0: 只要请求里出现 binding_scope 就走重新校验流程,
        # 否则即使前端只改了备注, 老 binding 也会被原样保留.
        if "binding_scope" in data or "project_id" in data \
                or "target_channels" in data or "target_stations" in data:
            merged = {
                "binding_scope": data.get("binding_scope", order.binding_scope),
                "project_id": data.get("project_id", order.project_id),
                "target_channels": data.get("target_channels", order.target_channels),
                "target_stations": data.get("target_stations", order.target_stations),
            }
            binding = self._normalize_binding(merged)
            for k, v in binding.items():
                setattr(order, k, v)

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

    def get_active_order(self, db: Session,
                         project_id: Optional[int] = None,
                         channel_id: Optional[int] = None,
                         station_id: Optional[str] = None) -> Optional[WorkOrder]:
        """根据上下文匹配 in_progress 工单, 按 priority+created_at 优先级返回首条.

        v3.1.0: 支持三种 binding_scope (project/channels/cluster). cluster 模式
        不在这里返回 (那是按 box 计件, 由 cluster_collector 触发), 这里只筛
        project/channels.

        匹配规则:
          - project  模式: WorkOrder.project_id == project_id
          - channels 模式: channel_id 在 target_channels 列表中
          - cluster  模式: 跳过 (调用方应在 box_complete 时用 find_cluster_orders)

        无匹配返回 None.
        """
        orders = (
            db.query(WorkOrder)
            .filter(WorkOrder.status == "in_progress")
            .order_by(WorkOrder.priority.asc(), WorkOrder.created_at.asc())
            .all()
        )
        for o in orders:
            scope = (o.binding_scope or "project").lower()
            if scope == "project":
                if project_id is None or o.project_id != project_id:
                    continue
                return o
            elif scope == "channels":
                if channel_id is None:
                    continue
                tc = o.target_channels or []
                if int(channel_id) in [int(x) for x in tc]:
                    return o
            elif scope == "cluster":
                continue
        return None

    def find_cluster_orders(self, db: Session,
                            station_ids: set = None) -> list[WorkOrder]:
        """给 cluster_collector 用: 找当前 in_progress 的集群工单.

        v3.1.0: 简化语义为"工单建在主机 = 主机这边推 box_complete 的全计入".
        默认不过滤 station, 按 priority + created_at 取首条 (同一时刻只让一条
        活跃集群工单跑, 跟 project/channels 模式语义统一).

        target_stations 字段保留作扩展位: 工单显式填了 target_stations 时,
        会跟当前 box 涉及到的 station_ids 做交集过滤; 都没填则默认匹配.

        返回 list 是给将来"多工单同时跑"留口子, 当前实现最多返回 1 条.
        """
        orders = (
            db.query(WorkOrder)
            .filter(WorkOrder.status == "in_progress",
                    WorkOrder.binding_scope == "cluster")
            .order_by(WorkOrder.priority.asc(), WorkOrder.created_at.asc())
            .all()
        )
        if not orders:
            return []

        # 高级路径: 工单显式带了 target_stations 才过滤
        if station_ids:
            wanted = {str(x) for x in station_ids}
            scoped = []
            for o in orders:
                ts = o.target_stations or []
                if not ts:
                    scoped.append(o)
                    continue
                if {str(x) for x in ts} & wanted:
                    scoped.append(o)
            if scoped:
                return [scoped[0]]
            return []

        return [orders[0]]

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
            "product_code": order.product_code,
            "product_spec": order.product_spec,
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
            "source": order.source,
            "external_id": order.external_id,
            "extra_data": order.extra_data or {},
        }
