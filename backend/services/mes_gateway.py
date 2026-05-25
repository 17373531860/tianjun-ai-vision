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


class MESGateway:

    def __init__(self):
        self.enabled = True
        self._extra_fields: dict[int, dict] = {}

    def set_extra_fields(self, channel_id: int, fields: dict):
        """Monitor 页实时输入的额外字段 (如 weight)"""
        self._extra_fields[channel_id] = fields

    def get_extra_fields(self, channel_id: int) -> dict:
        return self._extra_fields.get(channel_id, {})

    def dispatch(self, event_type: str, context: dict, channel_id: int = None):
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
                bound = conn.bound_channels
                if bound and channel_id is not None and channel_id not in bound:
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
                            event_type: str, context: dict, channel_id: int = None):
        """向单个连接发送数据, 含重试"""
        config = conn.config or {}
        static = config.get("static_fields", {})
        extra = self._extra_fields.get(channel_id or 0, {})

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

        # 按结果过滤: push_on_result=["OK","NG"] 默认全推; ["NG"] 只推 NG.
        # 适用场景: 客户只关心 NG 情况, OK 无需上报.
        push_on = config.get("push_on_result")
        if push_on:
            r = str(full_context.get("overall_result")
                    or full_context.get("result") or "").upper()
            allowed = [str(x).upper() for x in push_on]
            if r and r not in allowed:
                self._log(db, conn.id, event_type, "push",
                          url=config.get("url"),
                          error_msg=f"skipped by push_on_result={allowed}, result={r}",
                          success=True)
                return

        # 物料名称映射: 把 ng_items 里的中文步骤名替换成客户 MES 的物料代码.
        # mode=replace (默认): 直接替换原数组;
        # mode=keep_both: 原数组保留, 新增 ng_items_mapped 字段.
        label_mapping = config.get("label_mapping") or {}
        if label_mapping and isinstance(full_context.get("ng_items"), list):
            mapped = [label_mapping.get(x, x) for x in full_context["ng_items"]]
            if config.get("label_mapping_mode", "replace") == "replace":
                full_context["ng_items"] = mapped
            else:
                full_context["ng_items_mapped"] = mapped

        # 鉴权统一处理: bearer/api_key/custom_header 合并进 headers, basic 留给 adapter.
        effective_config = self._apply_auth_to_headers(config)

        try:
            adapter = get_adapter(conn.adapter_type)
        except ValueError as e:
            self._log(db, conn.id, event_type, "push",
                      error_msg=str(e), success=False)
            return

        payload = adapter.build_payload(full_context, effective_config)
        request_body = json.dumps(payload, ensure_ascii=False, default=str)

        retry_count = conn.retry_count or 0
        retry_interval = conn.retry_interval_sec or 5
        last_result = None

        for attempt in range(1 + retry_count):
            if attempt > 0:
                time.sleep(retry_interval)
                print(f"[MES Gateway] 重试 {attempt}/{retry_count}: {conn.name}", flush=True)

            result = adapter.send(payload, effective_config)
            last_result = result
            is_ok = adapter.check_response(result, effective_config)

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

    @staticmethod
    def _apply_auth_to_headers(config: dict) -> dict:
        """统一鉴权到 headers.

        auth.type 取值:
          - none          : 不加
          - basic         : 保持在 auth 里, 给 adapter 走 requests.auth
          - bearer        : Authorization: Bearer <token>
          - api_key       : {header|X-API-Key}: <value>
          - custom_header : 把 auth.headers 合并进 config.headers

        同时把顶层 config.custom_headers (列表 [{key,value}]) 合并进 config.headers,
        方便前端 UI 提供 "额外请求头" 编辑器.
        """
        new_config = dict(config) if isinstance(config, dict) else {}
        headers = dict(new_config.get("headers") or {})

        custom = new_config.get("custom_headers")
        if isinstance(custom, list):
            for item in custom:
                if isinstance(item, dict):
                    k = item.get("key") or item.get("name")
                    v = item.get("value")
                    if k:
                        headers[k] = "" if v is None else str(v)
        elif isinstance(custom, dict):
            for k, v in custom.items():
                headers[k] = "" if v is None else str(v)

        auth = new_config.get("auth") or {}
        atype = auth.get("type") if isinstance(auth, dict) else None
        if atype == "bearer":
            token = auth.get("token") or ""
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif atype == "api_key":
            hdr = auth.get("header") or "X-API-Key"
            val = auth.get("value") or ""
            headers[hdr] = val
        elif atype == "custom_header":
            extra = auth.get("headers") or []
            if isinstance(extra, list):
                for item in extra:
                    if isinstance(item, dict):
                        k = item.get("key") or item.get("name")
                        v = item.get("value")
                        if k:
                            headers[k] = "" if v is None else str(v)
            elif isinstance(extra, dict):
                for k, v in extra.items():
                    headers[k] = "" if v is None else str(v)

        new_config["headers"] = headers
        return new_config

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

        from backend.models.models import DetectionCycle, StepRecord
        # v3.10+ 阶段 4: cycle.operator_id 语义改为 user_id, 查 User 表
        from backend.models.auth_models import User
        cycle = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
        if cycle:
            # DetectionCycle ORM 模型本身没有 completed_steps/total_steps 字段，
            # 直接属性访问会 AttributeError 把 cycle_end → MES → 集群分发整条链路冲崩。
            # 用 getattr 兜底，缺失时从 step_sequence 推算。
            completed_steps = getattr(cycle, 'completed_steps', None)
            total_steps = getattr(cycle, 'total_steps', None)
            if total_steps is None and isinstance(cycle.step_sequence, list):
                total_steps = len(cycle.step_sequence)
            context["cycle"]["completed_steps"] = completed_steps
            context["cycle"]["total_steps"] = total_steps
            context["cycle"]["start_time"] = cycle.start_time.isoformat() if cycle.start_time else None
            context["cycle"]["end_time"] = cycle.end_time.isoformat() if cycle.end_time else None
            # operator_id 列保留字段名 (SQLite 无法 rename), 值改为 user.id
            # MES payload key "operator" 字段名稳定: name / employee_no / id (向后兼容客户模板)
            u_id = getattr(cycle, 'operator_id', None)
            if u_id:
                u = db.query(User).filter(User.id == u_id).first()
                if u:
                    context["operator"] = {
                        "name": u.display_name or u.username,
                        "employee_no": u.username,
                        "id": u.id,
                    }

        steps = db.query(StepRecord).filter(StepRecord.cycle_id == cycle_id).order_by(StepRecord.id).all()
        for s in steps:
            # StepRecord 模型字段名：step_order / duration / is_valid
            # 历史上这里写过 step_index / duration_seconds / is_good，会触发 AttributeError
            # 并导致整个 cycle_end 构造 context 失败 → 进而 _cluster_dispatch 不被触发。
            # 用 getattr 防御式访问，兼容模型重命名。
            idx = getattr(s, 'step_index', None)
            if idx is None:
                idx = getattr(s, 'step_order', None)
            dur = getattr(s, 'duration_seconds', None)
            if dur is None:
                dur = getattr(s, 'duration', None)
            good = getattr(s, 'is_good', None)
            if good is None:
                good = bool(getattr(s, 'is_valid', True))
            context["steps"].append({
                "label": getattr(s, 'step_label', None),
                "index": idx,
                "duration": dur,
                "is_good": good,
                "confidence": getattr(s, 'confidence', None),
                "start_time": s.start_time.isoformat() if s.start_time else None,
                "end_time": s.end_time.isoformat() if s.end_time else None,
            })

        # 便利字段：让 MES 端不写 Jinja 过滤器即可直接拿到 NG 步骤明细
        # ng_steps = 所有 is_good=False 的步骤；missing_step_count = 缺失步骤数
        context["ng_steps"] = [s for s in context["steps"] if s.get("is_good") is False]
        completed = context["cycle"].get("completed_steps")
        total = context["cycle"].get("total_steps")
        if isinstance(completed, int) and isinstance(total, int):
            context["cycle"]["missing_step_count"] = max(0, total - completed)
        else:
            context["cycle"]["missing_step_count"] = len(context["ng_steps"])

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
