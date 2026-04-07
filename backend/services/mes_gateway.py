"""
MES 外部对接网关

职责:
1. 加载所有启用的 MESConnection 配置
2. 根据 push_events 过滤, 将事件分发到对应适配器
3. 失败重试 (retry_count × retry_interval_sec)
4. 记录每次通讯到 MESCommLog
"""
import json
import time
import traceback
from datetime import datetime
from typing import Optional

from backend.db.database import SessionLocal
from backend.models.mes_models import MESConnection, MESCommLog
from backend.services.mes_adapters import get_adapter
from backend.services.mes_adapters.base import render_template


class MESGateway:

    def __init__(self):
        self.enabled = True
        self._extra_fields: dict[int, dict] = {}

    def set_extra_fields(self, channel_id: int, fields: dict):
        """Monitor 页实时输入的额外字段 (如 weight)"""
        self._extra_fields[channel_id] = fields

    def get_extra_fields(self, channel_id: int) -> dict:
        return self._extra_fields.get(channel_id, {})

    def dispatch(self, event_type: str, context: dict, channel_id: int = 0):
        """分发事件到所有匹配的外部 MES 连接"""
        if not self.enabled:
            return

        db = SessionLocal()
        try:
            connections = (
                db.query(MESConnection)
                .filter(MESConnection.enabled == True)
                .all()
            )
            for conn in connections:
                events = conn.push_events or []
                if event_type not in events:
                    continue
                self._send_to_connection(db, conn, event_type, context, channel_id)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"[MES Gateway] 分发失败: {e}", flush=True)
            traceback.print_exc()
        finally:
            db.close()

    def _send_to_connection(self, db, conn: MESConnection,
                            event_type: str, context: dict, channel_id: int):
        """向单个连接发送数据, 含重试"""
        config = conn.config or {}
        static = config.get("static_fields", {})
        extra = self._extra_fields.get(channel_id, {})

        full_context = {
            **context,
            "extra": {**extra},
            "timestamp": datetime.now().isoformat(),
        }
        for dotted_key, val in static.items():
            parts = dotted_key.split(".", 1)
            if len(parts) == 2:
                ns, key = parts
                if ns not in full_context:
                    full_context[ns] = {}
                if isinstance(full_context[ns], dict):
                    full_context[ns][key] = val
            else:
                full_context[dotted_key] = val

        try:
            adapter = get_adapter(conn.adapter_type)
        except ValueError as e:
            self._log(db, conn.id, event_type, "push",
                      error_msg=str(e), success=False)
            return

        payload = adapter.build_payload(full_context, config)
        request_body = json.dumps(payload, ensure_ascii=False, default=str)

        retry_count = conn.retry_count or 0
        retry_interval = conn.retry_interval_sec or 5
        last_result = None

        for attempt in range(1 + retry_count):
            if attempt > 0:
                time.sleep(retry_interval)
                print(f"[MES Gateway] 重试 {attempt}/{retry_count}: {conn.name}", flush=True)

            result = adapter.send(payload, config)
            last_result = result
            is_ok = adapter.check_response(result, config)

            if is_ok:
                self._log(
                    db, conn.id, event_type, "push",
                    method=config.get("method", "POST"),
                    url=config.get("url"),
                    request_body=request_body,
                    response_body=json.dumps(result.get("body"), ensure_ascii=False, default=str)
                        if result.get("body") else None,
                    status_code=result.get("status_code"),
                    duration_ms=result.get("duration_ms"),
                    success=True,
                )
                conn.last_sync_at = datetime.now()
                db.flush()
                print(f"[MES Gateway] 推送成功: {conn.name} ({event_type})", flush=True)
                return

        error_msg = last_result.get("error") if last_result else "未知错误"
        if not error_msg and last_result:
            error_msg = f"HTTP {last_result.get('status_code')}: 响应校验失败"
        self._log(
            db, conn.id, event_type, "push",
            method=config.get("method", "POST"),
            url=config.get("url"),
            request_body=request_body,
            response_body=json.dumps(last_result.get("body"), ensure_ascii=False, default=str)
                if last_result and last_result.get("body") else None,
            status_code=last_result.get("status_code") if last_result else 0,
            duration_ms=last_result.get("duration_ms") if last_result else 0,
            success=False,
            error_msg=error_msg,
        )
        print(f"[MES Gateway] 推送失败: {conn.name} ({event_type}) - {error_msg}", flush=True)

    def _log(self, db, connection_id: int, event_type: str, direction: str, **kwargs):
        log = MESCommLog(
            connection_id=connection_id,
            direction=direction,
            event_type=event_type,
            method=kwargs.get("method"),
            url=kwargs.get("url"),
            request_body=kwargs.get("request_body"),
            response_body=kwargs.get("response_body"),
            status_code=kwargs.get("status_code"),
            success=kwargs.get("success", True),
            error_msg=kwargs.get("error_msg"),
            duration_ms=kwargs.get("duration_ms"),
        )
        db.add(log)
        db.flush()

    def build_context_from_cycle(self, db, cycle_id: int,
                                 workpiece_id: int = None,
                                 order_id: int = None,
                                 is_good: bool = True,
                                 event_name: str = None,
                                 result_reason: str = None,
                                 duration: float = None,
                                 step_sequence: list = None,
                                 project_id: int = None) -> dict:
        """从 cycle 数据构建标准化上下文"""
        context = {
            "cycle": {
                "id": cycle_id,
                "is_good": is_good,
                "result": "OK" if is_good else "NG",
                "duration": duration,
                "event_name": event_name,
                "ng_reason": result_reason,
            },
            "project": {"id": project_id},
            "steps": [],
            "defects": [],
            "workpiece": {},
            "order": {},
            "operator": {},
        }

        if project_id:
            from backend.models.models import Project
            proj = db.query(Project).filter(Project.id == project_id).first()
            if proj:
                context["project"]["name"] = proj.name

        from backend.models.models import DetectionCycle, StepRecord, Operator
        cycle = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
        if cycle:
            context["cycle"]["completed_steps"] = cycle.completed_steps
            context["cycle"]["total_steps"] = cycle.total_steps
            context["cycle"]["start_time"] = cycle.start_time.isoformat() if cycle.start_time else None
            context["cycle"]["end_time"] = cycle.end_time.isoformat() if cycle.end_time else None
            op_id = getattr(cycle, 'operator_id', None)
            if op_id:
                op = db.query(Operator).filter(Operator.id == op_id).first()
                if op:
                    context["operator"] = {"name": op.name, "employee_no": op.employee_no, "id": op.id}

        steps = db.query(StepRecord).filter(StepRecord.cycle_id == cycle_id).order_by(StepRecord.id).all()
        for s in steps:
            context["steps"].append({
                "label": s.step_label,
                "index": s.step_index,
                "duration": s.duration_seconds,
                "is_good": s.is_good,
                "confidence": s.confidence,
                "start_time": s.start_time.isoformat() if s.start_time else None,
                "end_time": s.end_time.isoformat() if s.end_time else None,
            })

        if workpiece_id:
            from backend.models.mes_models import Workpiece, DefectRecord
            wp = db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()
            if wp:
                context["workpiece"] = {
                    "id": wp.id,
                    "serial_no": wp.serial_no,
                    "status": wp.status,
                    "inspection_count": wp.inspection_count,
                }
            defects = db.query(DefectRecord).filter(
                DefectRecord.workpiece_id == workpiece_id,
                DefectRecord.cycle_id == cycle_id
            ).all()
            for d in defects:
                context["defects"].append({
                    "defect_code": d.defect_code,
                    "defect_name": d.defect_name,
                    "category": d.defect_category,
                    "severity": d.severity,
                })

        if order_id:
            from backend.services.work_order import WorkOrderService
            wo_svc = WorkOrderService()
            summary = wo_svc.get_order_summary(db, order_id)
            if summary:
                context["order"] = summary

        return context

    def build_context_from_session(self, db, session_id: int,
                                   project_id: int = None) -> dict:
        """从 session 数据构建标准化上下文"""
        from backend.models.models import DetectionSession
        context = {
            "session": {"id": session_id},
            "project": {"id": project_id},
            "order": {},
        }

        session = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
        if session:
            context["session"].update({
                "total_cycles": session.total_cycles,
                "good_cycles": session.good_cycles,
                "ng_cycles": session.ng_cycles,
                "start_time": session.start_time.isoformat() if session.start_time else None,
                "end_time": session.end_time.isoformat() if session.end_time else None,
            })
            if hasattr(session, 'order_id') and session.order_id:
                from backend.services.work_order import WorkOrderService
                summary = WorkOrderService().get_order_summary(db, session.order_id)
                if summary:
                    context["order"] = summary

        if project_id:
            from backend.models.models import Project
            proj = db.query(Project).filter(Project.id == project_id).first()
            if proj:
                context["project"]["name"] = proj.name

        return context

    def manual_push(self, connection_id: int, event_type: str, context: dict,
                    channel_id: int = 0) -> dict:
        """手动推送 (API 调用), 返回结果"""
        db = SessionLocal()
        try:
            conn = db.query(MESConnection).filter(MESConnection.id == connection_id).first()
            if not conn:
                return {"success": False, "error": "连接不存在"}
            self._send_to_connection(db, conn, event_type, context, channel_id)
            db.commit()
            log = (
                db.query(MESCommLog)
                .filter(MESCommLog.connection_id == connection_id)
                .order_by(MESCommLog.id.desc())
                .first()
            )
            if log:
                return {
                    "success": log.success,
                    "status_code": log.status_code,
                    "error": log.error_msg,
                    "duration_ms": log.duration_ms,
                }
            return {"success": False, "error": "日志记录缺失"}
        except Exception as e:
            db.rollback()
            return {"success": False, "error": str(e)}
        finally:
            db.close()


_gateway_instance: Optional[MESGateway] = None


def get_mes_gateway() -> MESGateway:
    global _gateway_instance
    if _gateway_instance is None:
        _gateway_instance = MESGateway()
    return _gateway_instance
