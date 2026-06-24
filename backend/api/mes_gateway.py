"""
外部 MES 对接 REST API

连接管理 CRUD + 测试连接 + 手动推送 + 通讯日志查询
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from backend.core.auth_deps import require_perm
from backend.db.database import SessionLocal
from backend.models.mes_models import MESConnection, MESCommLog
from backend.services.mes_gateway import get_mes_gateway

router = APIRouter(prefix="/mes/gateway", tags=["MES-Gateway"])


# ============================================================
# Pydantic Schema
# ============================================================

class ConnectionCreate(BaseModel):
    name: str
    adapter_type: str = "rest"
    enabled: bool = False
    config: Optional[dict] = None
    push_events: Optional[List[str]] = None
    pull_enabled: bool = False
    pull_interval_sec: int = 60
    retry_count: int = 3
    retry_interval_sec: int = 5
    extra_fields_schema: Optional[List[dict]] = None
    bound_channels: Optional[List[int]] = None

class ConnectionUpdate(BaseModel):
    name: Optional[str] = None
    adapter_type: Optional[str] = None
    enabled: Optional[bool] = None
    config: Optional[dict] = None
    push_events: Optional[List[str]] = None
    pull_enabled: Optional[bool] = None
    pull_interval_sec: Optional[int] = None
    retry_count: Optional[int] = None
    retry_interval_sec: Optional[int] = None
    extra_fields_schema: Optional[List[dict]] = None
    bound_channels: Optional[List[int]] = None

class TestPayload(BaseModel):
    config: Optional[dict] = None
    event_type: Optional[str] = None  # cycle_end / box_complete / box_timeout / session_end

class ManualPush(BaseModel):
    event_type: str = "cycle_end"
    cycle_id: Optional[int] = None
    session_id: Optional[int] = None
    channel_id: int = 0

class ExtraFieldsUpdate(BaseModel):
    channel_id: int = 0
    fields: dict

class PullTest(BaseModel):
    """测试拉取: 直接拿编辑中的 config.pull 试一次, 不依赖已保存的连接。"""
    pull_config: dict
    job_no: str = ""

class PullRun(BaseModel):
    """执行拉取: dry_run=True + max_items=1 即"试同步 1 条看看"; dry_run=False 正式入库。"""
    job_no: str = ""
    dry_run: bool = False
    max_items: Optional[int] = None


class AsyncDispatchToggle(BaseModel):
    enabled: bool = False


# ==================== B1②: 外部 MES 推送并发派发开关 (默认关) ====================
# 开 → cycle_end 外推甩到"每工位一条"的执行器, 慢/挂的客户 MES 不再堵住整个 hook 队列;
# 关(默认) → 原内联派发, 与旧版一致。开关存 SystemConfig(mes_async_dispatch), 即时生效。
@router.get("/async-dispatch")
def get_async_dispatch():
    from backend.services.mes_hooks import get_mes_hook
    return {"enabled": get_mes_hook().get_async_dispatch()}


@router.put("/async-dispatch", dependencies=[Depends(require_perm("settings.edit"))])
def set_async_dispatch(body: AsyncDispatchToggle):
    from backend.services.mes_hooks import get_mes_hook
    hook = get_mes_hook()
    hook.set_async_dispatch(body.enabled)
    return {"status": "success", "enabled": hook.get_async_dispatch()}


def _serialize_conn(c):
    return {
        "id": c.id, "name": c.name,
        "adapter_type": c.adapter_type, "enabled": c.enabled,
        "config": c.config, "push_events": c.push_events,
        "pull_enabled": c.pull_enabled,
        "pull_interval_sec": c.pull_interval_sec,
        "retry_count": c.retry_count,
        "retry_interval_sec": c.retry_interval_sec,
        "extra_fields_schema": c.extra_fields_schema,
        "bound_channels": c.bound_channels,
        "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _serialize_log(l):
    return {
        "id": l.id, "connection_id": l.connection_id,
        "direction": l.direction, "event_type": l.event_type,
        "method": l.method, "url": l.url,
        "request_body": l.request_body, "response_body": l.response_body,
        "status_code": l.status_code, "success": l.success,
        "error_msg": l.error_msg, "duration_ms": l.duration_ms,
        "created_at": l.created_at.isoformat() if l.created_at else None,
    }


# ============================================================
# 连接 CRUD
# ============================================================

@router.get("/connections")
def list_connections():
    db = SessionLocal()
    try:
        items = db.query(MESConnection).order_by(MESConnection.id).all()
        return [_serialize_conn(c) for c in items]
    finally:
        db.close()


@router.post("/connections",
              dependencies=[Depends(require_perm("mes.gateway.edit"))])
def create_connection(body: ConnectionCreate):
    db = SessionLocal()
    try:
        conn = MESConnection(
            name=body.name,
            adapter_type=body.adapter_type,
            enabled=body.enabled,
            config=body.config,
            push_events=body.push_events,
            pull_enabled=body.pull_enabled,
            pull_interval_sec=body.pull_interval_sec,
            retry_count=body.retry_count,
            retry_interval_sec=body.retry_interval_sec,
            extra_fields_schema=body.extra_fields_schema,
            bound_channels=body.bound_channels,
        )
        db.add(conn)
        db.commit()
        db.refresh(conn)
        return _serialize_conn(conn)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.get("/connections/by-channel")
def list_connections_by_channel(channel: int = Query(..., description="工位通道号")):
    """查询绑定到指定工位的连接列表（反向查看）"""
    db = SessionLocal()
    try:
        items = db.query(MESConnection).filter(MESConnection.enabled == True).all()
        result = []
        for c in items:
            bound = c.bound_channels
            if not bound or channel in bound:
                result.append(_serialize_conn(c))
        return result
    finally:
        db.close()


@router.get("/connections/{conn_id}")
def get_connection(conn_id: int):
    db = SessionLocal()
    try:
        conn = db.query(MESConnection).filter(MESConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(404, "连接不存在")
        return _serialize_conn(conn)
    finally:
        db.close()


@router.put("/connections/{conn_id}",
             dependencies=[Depends(require_perm("mes.gateway.edit"))])
def update_connection(conn_id: int, body: ConnectionUpdate):
    db = SessionLocal()
    try:
        conn = db.query(MESConnection).filter(MESConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(404, "连接不存在")
        data = body.model_dump(exclude_unset=True)
        for field, val in data.items():
            setattr(conn, field, val)
        db.commit()
        db.refresh(conn)
        return _serialize_conn(conn)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.delete("/connections/{conn_id}",
                dependencies=[Depends(require_perm("mes.gateway.edit"))])
def delete_connection(conn_id: int):
    db = SessionLocal()
    try:
        conn = db.query(MESConnection).filter(MESConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(404, "连接不存在")
        db.delete(conn)
        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


# ============================================================
# 测试连接
# ============================================================

def _build_test_context_cycle_end() -> dict:
    """cycle_end 事件的测试上下文: 单工位检测完成一次循环."""
    return {
        "workpiece": {"id": 0, "serial_no": "TEST-001", "status": "ok", "inspection_count": 1},
        "cycle": {"id": 0, "is_good": True, "result": "OK", "duration": 5.2,
                  "event_name": "test", "ng_reason": None, "completed_steps": 3, "total_steps": 3},
        "order": {"order_no": "WO-TEST-001", "product_name": "测试产品",
                  "planned_qty": 100, "completed_qty": 50, "good_qty": 48,
                  "ng_qty": 2, "yield_rate": 96.0, "progress": 50.0},
        "project": {"id": 1, "name": "测试项目"},
        "steps": [
            {"label": "Step-A", "index": 0, "duration": 1.5, "is_good": True,
             "confidence": 0.95, "start_time": "2026-01-01T00:00:00", "end_time": "2026-01-01T00:00:01"},
            {"label": "Step-B", "index": 1, "duration": 2.0, "is_good": True,
             "confidence": 0.88, "start_time": "2026-01-01T00:00:02", "end_time": "2026-01-01T00:00:04"},
        ],
        "ng_steps": [],
        "defects": [],
        "session": {"id": 0, "total_cycles": 50, "good_cycles": 48, "ng_cycles": 2},
        "extra": {},
        "result": "OK",
        "overall_result": "OK",
        "order_no": "WO-TEST-001",
        "workpiece_id": "TEST-001",
        "ng_items": [],
        "timestamp": datetime.now().isoformat(),
    }


def _build_test_context_box_complete() -> dict:
    """box_complete 事件的测试上下文: 集群汇总一箱 (含 NG 步骤, 便于测物料映射).

    结构完全模拟 cluster_collector._check_and_dispatch 产出的 aggregated.
    """
    return {
        "box_serial": "BOX-TEST-001",
        "overall_result": "NG",
        "result": "NG",
        "total_stations": 2,
        "completed_stations": 2,
        "order_no": "WO-TEST-001",
        "workpiece_id": "SN-TEST-001",
        "ng_items": ["螺丝A", "垫片B"],
        "stations": [
            {"station_id": "A", "is_good": True, "event_name": "ok",
             "ng_steps": [],
             "order": {"order_no": "WO-TEST-001"},
             "workpiece": {"serial_no": "SN-TEST-001"}},
            {"station_id": "B", "is_good": False, "event_name": "ng",
             "ng_steps": [{"label": "螺丝A"}, {"label": "垫片B"}],
             "order": {"order_no": "WO-TEST-001"},
             "workpiece": {"serial_no": "SN-TEST-001"}},
        ],
        "extra": {},
        "timestamp": datetime.now().isoformat(),
    }


def _build_test_context_box_timeout() -> dict:
    """box_timeout 事件的测试上下文: 箱超时, 有缺失工位."""
    ctx = _build_test_context_box_complete()
    ctx.update({
        "box_serial": "BOX-TEST-TIMEOUT",
        "overall_result": "TIMEOUT",
        "result": "NG",
        "completed_stations": 1,
        "missing_stations": ["B"],
        "ng_items": ["MISSING-B"],
    })
    return ctx


@router.post("/connections/{conn_id}/test",
              dependencies=[Depends(require_perm("mes.gateway.edit"))])
def test_connection(conn_id: int, body: TestPayload = None):
    """发送测试数据到外部 MES, 验证连接+格式.

    走和实时推送**完全一致**的预处理管道: label_mapping → _apply_auth_to_headers,
    确保测试通过 == 真实推送一定通过.

    push_on_result 在测试里**故意不应用**: 测试就是为了看请求能否发到对端,
    不该因为过滤规则把 payload 偷偷丢掉.
    """
    from backend.services.mes_gateway import MESGateway

    db = SessionLocal()
    try:
        conn = db.query(MESConnection).filter(MESConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(404, "连接不存在")

        config = body.config if body and body.config else conn.config or {}
        event_type = (body.event_type if body else None) or "cycle_end"

        if event_type == "box_complete":
            test_context = _build_test_context_box_complete()
        elif event_type == "box_timeout":
            test_context = _build_test_context_box_timeout()
        else:
            test_context = _build_test_context_cycle_end()

        static = config.get("static_fields", {})
        for dotted_key, val in static.items():
            parts = dotted_key.split(".", 1)
            if len(parts) == 2:
                ns, key = parts
                if ns not in test_context:
                    test_context[ns] = {}
                if isinstance(test_context[ns], dict):
                    test_context[ns][key] = val
            else:
                test_context[dotted_key] = val

        label_mapping = config.get("label_mapping") or {}
        if label_mapping and isinstance(test_context.get("ng_items"), list):
            mapped = [label_mapping.get(x, x) for x in test_context["ng_items"]]
            if config.get("label_mapping_mode", "replace") == "replace":
                test_context["ng_items"] = mapped
            else:
                test_context["ng_items_mapped"] = mapped

        effective_config = MESGateway._apply_auth_to_headers(config)

        from backend.services.mes_adapters import get_adapter
        adapter = get_adapter(conn.adapter_type)
        payload = adapter.build_payload(test_context, effective_config)
        result = adapter.send(payload, effective_config)
        is_ok = adapter.check_response(result, effective_config)

        return {
            "success": is_ok,
            "event_type": event_type,
            "payload_preview": payload,
            "status_code": result.get("status_code"),
            "response_body": result.get("body"),
            "error": result.get("error"),
            "duration_ms": result.get("duration_ms"),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        db.close()


# ============================================================
# 手动推送
# ============================================================

@router.post("/connections/{conn_id}/push",
              dependencies=[Depends(require_perm("mes.gateway.edit"))])
def manual_push(conn_id: int, body: ManualPush):
    """手动推送指定 cycle/session 的数据"""
    db = SessionLocal()
    try:
        gw = get_mes_gateway()

        if body.event_type == "cycle_end" and body.cycle_id:
            context = gw.build_context_from_cycle(
                db, body.cycle_id, project_id=None
            )
        elif body.event_type == "session_end" and body.session_id:
            context = gw.build_context_from_session(
                db, body.session_id, project_id=None
            )
        else:
            raise HTTPException(400, "请指定 cycle_id 或 session_id")

        result = gw.manual_push(conn_id, body.event_type, context, body.channel_id)
        return result
    finally:
        db.close()


# ============================================================
# 工单主动拉取 (v3.20: 反向对接外部 MES, 把工单拉回来落库)
# ============================================================

@router.post("/pull-test",
              dependencies=[Depends(require_perm("mes.gateway.edit"))])
def pull_test(body: PullTest):
    """测试拉取连接: 发一次请求, 自动识别返回结构 (数组路径+字段候选), 不落库.

    给前端"测试连接"用 —— 编辑中尚未保存也能测, 拉回真实样本供点选映射。
    """
    from backend.services.mes_puller import get_mes_puller
    return get_mes_puller().test_connection(body.pull_config or {}, job_no=body.job_no)


@router.post("/connections/{conn_id}/pull",
              dependencies=[Depends(require_perm("mes.gateway.edit"))])
def pull_orders(conn_id: int, body: PullRun = None):
    """按某条连接的 config.pull 执行一次工单拉取.

    dry_run=True (+max_items=1) = "试同步看看"(解析映射但不入库);
    dry_run=False = "立即同步"(按 import_mode upsert 工单)。
    """
    from backend.services.mes_puller import get_mes_puller
    db = SessionLocal()
    try:
        conn = db.query(MESConnection).filter(MESConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(404, "连接不存在")
        body = body or PullRun()
        result = get_mes_puller().pull_for_connection(
            db, conn, job_no=body.job_no,
            dry_run=body.dry_run, max_items=body.max_items,
        )
        db.commit()
        return result
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


# ============================================================
# 额外字段 (Monitor 页实时输入)
# ============================================================

@router.post("/extra-fields",
              dependencies=[Depends(require_perm("mes.gateway.edit"))])
def set_extra_fields(body: ExtraFieldsUpdate):
    """设置当前工位的额外字段值"""
    gw = get_mes_gateway()
    gw.set_extra_fields(body.channel_id, body.fields)
    return {"success": True, "channel_id": body.channel_id, "fields": body.fields}


@router.get("/extra-fields")
def get_extra_fields(channel_id: int = 0):
    gw = get_mes_gateway()
    return {"channel_id": channel_id, "fields": gw.get_extra_fields(channel_id)}


@router.get("/extra-fields-schema")
def get_extra_fields_schema():
    """获取所有启用连接的 extra_fields_schema (用于 Monitor 页渲染输入框)"""
    db = SessionLocal()
    try:
        connections = (
            db.query(MESConnection)
            .filter(MESConnection.enabled == True)
            .all()
        )
        schemas = []
        for c in connections:
            efs = c.extra_fields_schema
            if efs:
                schemas.extend(efs)
        seen = set()
        unique = []
        for s in schemas:
            key = s.get("key")
            if key and key not in seen:
                seen.add(key)
                unique.append(s)
        return unique
    finally:
        db.close()


# ============================================================
# 通讯日志
# ============================================================

@router.get("/logs")
def list_logs(
    connection_id: Optional[int] = None,
    success: Optional[bool] = None,
    event_type: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = SessionLocal()
    try:
        q = db.query(MESCommLog)
        if connection_id is not None:
            q = q.filter(MESCommLog.connection_id == connection_id)
        if success is not None:
            q = q.filter(MESCommLog.success == success)
        if event_type:
            q = q.filter(MESCommLog.event_type == event_type)

        total = q.count()
        items = q.order_by(MESCommLog.id.desc()).offset(skip).limit(limit).all()
        return {
            "items": [_serialize_log(l) for l in items],
            "total": total, "skip": skip, "limit": limit,
        }
    finally:
        db.close()
