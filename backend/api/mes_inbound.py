"""
外部生产管控系统「入站对接」REST API

- POST /api/v1/mes/inbound/task   外部系统调它推开工任务 (无鉴权, 工控内网 M2M; 配置驱动响应)
- GET  /api/v1/mes/inbound/config 读入站配置 (前端管理)
- PUT  /api/v1/mes/inbound/config 存入站配置
- GET  /api/v1/mes/inbound/logs   最近入站通讯日志
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.db.database import get_db, SessionLocal
from backend.models.mes_models import MESCommLog
from backend.services.mes_inbound import (
    get_mes_inbound, http_status_for, check_inbound_auth, to_xml,
)
from backend.core import debug_center


# 入站路径别名: 允许的动作 → 处理 (task_start/alarm_clear 走 _handle_inbound; health 直接探活)
_ALIAS_ACTIONS = ("task_start", "alarm_clear", "health")
# 已动态注册的别名路由对象 (供热更新时先移除再重建, 支持增删改)
_alias_routes = []


def _make_response(cfg: dict, resp: dict, status: int):
    """按 response.format 组装最终 HTTP 响应。返回 (Response 对象, 落库用文本)。"""
    rc = (cfg or {}).get("response") or {}
    if (rc.get("format") or "json").lower() == "xml":
        xml = to_xml(resp, rc.get("xml_root") or "response",
                     rc.get("xml_declaration", True))
        return Response(content=xml, media_type="application/xml",
                        status_code=status), xml
    return JSONResponse(content=resp, status_code=status), \
        json.dumps(resp, ensure_ascii=False)

router = APIRouter(prefix="/mes/inbound", tags=["MES-Inbound"])


# ============================================================
# 接收端点 (外部系统调用; 无鉴权)
# ============================================================
@router.post("/task")
async def receive_inbound_task(request: Request, db: Session = Depends(get_db)):
    return await _handle_inbound(request, db, "task_start")


@router.get("/task")
async def receive_inbound_task_get(request: Request, db: Session = Depends(get_db)):
    # 少数系统用 GET + URL 参数推任务; 走同一处理 (query 参数即报文)
    return await _handle_inbound(request, db, "task_start")


@router.post("/alarm/clear")
async def receive_inbound_alarm_clear(request: Request, db: Session = Depends(get_db)):
    # 外部生产管控系统回推"报警消除命令" (A → B); 按唯一键匹配在途报警并消除
    return await _handle_inbound(request, db, "alarm_clear")


@router.get("/alarm/clear")
async def receive_inbound_alarm_clear_get(request: Request, db: Session = Depends(get_db)):
    return await _handle_inbound(request, db, "alarm_clear")


def _dispatch_handler(svc, event_type: str):
    """按入站事件类型选处理器。"""
    if event_type == "alarm_clear":
        return svc.handle_alarm_clear
    return svc.handle_task_start


async def _handle_inbound(request: Request, db: Session, event_type: str = "task_start"):
    svc = get_mes_inbound()
    cfg = svc.get_config(db)

    # 来源校验 (共享密钥头 / IP 白名单, 默认关)
    auth_err = check_inbound_auth(request, cfg)
    if auth_err is not None:
        resp = svc.error_response(cfg, "unauthorized", auth_err)
        status = http_status_for(cfg, "unauthorized", default=401)
        obj, text = _make_response(cfg, resp, status)
        _log_inbound(db, request.url.query or "", text, success=False, status_code=status)
        return obj

    raw = await request.body()
    max_bytes = cfg.get("max_body_bytes") or 0
    if max_bytes and len(raw) > max_bytes:
        # 客户协议常约定请求体超限回 413
        raise HTTPException(status_code=413, detail="请求体超出大小限制")

    raw_text = raw.decode("utf-8", "replace") if raw else ""
    body, err = await _parse_inbound_body(request, raw_text, cfg)
    if err is not None:
        resp = svc.error_response(cfg, "bad_request", err)
        status = http_status_for(cfg, "bad_request")
        obj, text = _make_response(cfg, resp, status)
        _log_inbound(db, raw_text or request.url.query, text, success=False, status_code=status)
        return obj

    handler = _dispatch_handler(svc, event_type)
    result = handler(db, body, cfg)
    try:
        db.commit()
    except Exception:
        db.rollback()
    status = http_status_for(cfg, result["code_key"])
    if debug_center.is_on("backend.mes"):
        debug_center.dbg("backend.mes", "入站接收",
                         f"event={event_type} fmt={cfg.get('input_format')} ok={result['ok']} "
                         f"code_key={result['code_key']} http={status} mapped={result['mapped']}")
    obj, text = _make_response(cfg, result["response"], status)
    _log_inbound(db, raw_text or request.url.query, text,
                 success=result["ok"], status_code=status, event_type=event_type)
    return obj


# ============================================================
# 入站接收路径别名 (启动动态注册; 客户可在根路径自定义接收 URL)
# ============================================================
def _make_alias_endpoint(action: str):
    """生成一个别名路由的处理函数 (自管 DB 会话, 不走 Depends)。"""
    if action == "health":
        async def _health_ep(request: Request):
            db = SessionLocal()
            try:
                svc = get_mes_inbound()
                cfg = svc.get_config(db)
                resp = svc.build_response(cfg, "success", "ok")
                obj, _ = _make_response(cfg, resp, 200)
                return obj
            finally:
                db.close()
        return _health_ep

    async def _inbound_ep(request: Request):
        db = SessionLocal()
        try:
            return await _handle_inbound(request, db, action)
        finally:
            db.close()
    return _inbound_ep


def register_inbound_aliases(app) -> int:
    """按入站配置 receive_paths 动态注册根路径别名。先移除旧别名再重建 (支持增删改)。

    返回成功注册的别名数。path 必须以 / 开头且非 /api/ 前缀; 与已有路由冲突则跳过。
    """
    global _alias_routes
    db = SessionLocal()
    try:
        cfg = get_mes_inbound().get_config(db)
    except Exception as e:
        debug_center.dbg("backend.mes", "读入站配置失败,跳过别名注册", str(e))
        return 0
    finally:
        db.close()

    # 移除上轮注册的别名路由
    if _alias_routes:
        app.router.routes[:] = [r for r in app.router.routes if r not in _alias_routes]
        _alias_routes = []

    paths = cfg.get("receive_paths") or []
    existing = {getattr(r, "path", None) for r in app.router.routes}
    seen = set()
    count = 0
    for entry in paths:
        if not isinstance(entry, dict):
            continue
        path = (entry.get("path") or "").strip()
        action = (entry.get("action") or "task_start").strip()
        methods = entry.get("methods") or ["GET", "POST"]
        if not path or not path.startswith("/"):
            debug_center.dbg("backend.mes", "别名路径非法,跳过", repr(path))
            continue
        if path.startswith("/api/"):
            debug_center.dbg("backend.mes", "别名禁用 /api/ 前缀,跳过", path)
            continue
        if path in seen:
            continue
        if path in existing:
            debug_center.dbg("backend.mes", "别名与已有路由冲突,跳过", path)
            continue
        if action not in _ALIAS_ACTIONS:
            action = "task_start"
        seen.add(path)
        try:
            ep = _make_alias_endpoint(action)
            app.add_api_route(path, ep,
                              methods=[str(m).upper() for m in methods],
                              include_in_schema=False)
            _alias_routes.append(app.router.routes[-1])
            count += 1
        except Exception as e:
            debug_center.dbg("backend.mes", "别名注册失败", f"{path}: {e}")
    if count:
        debug_center.dbg("backend.mes", "入站路径别名已注册", f"count={count}")
    return count


async def _parse_inbound_body(request: Request, raw_text: str, cfg: dict):
    """按配置的入站编码解析报文 → dict。返回 (body, error_msg|None)。

    支持: json / form(urlencoded+multipart) / query(URL 参数) / xml; auto 按
    Content-Type 识别, 兜底顺序 json→form→query。可选并入 query 参数。
    """
    fmt = (cfg.get("input_format") or "auto").lower()
    ctype = (request.headers.get("content-type") or "").lower()
    method = request.method.upper()

    def _json():
        return json.loads(raw_text) if raw_text.strip() else {}

    async def _form():
        form = await request.form()
        return {k: (v if isinstance(v, str) else str(v)) for k, v in form.items()}

    def _query():
        return dict(request.query_params)

    try:
        if method == "GET":
            body = _query()
        elif fmt == "json":
            body = _json()
        elif fmt == "form":
            body = await _form()
        elif fmt == "query":
            body = _query()
        elif fmt == "xml":
            body = _xml_to_dict(raw_text)
        elif "json" in ctype:
            body = _json()
        elif "x-www-form-urlencoded" in ctype or "multipart/form-data" in ctype:
            body = await _form()
        elif "xml" in ctype:
            body = _xml_to_dict(raw_text)
        else:
            # auto 兜底: 先试 JSON, 不行再试表单, 再退 query
            try:
                body = _json()
            except Exception:
                try:
                    body = await _form()
                except Exception:
                    body = _query()
    except Exception as e:
        return {}, f"报文解析失败 ({fmt}): {e}"

    if not isinstance(body, dict):
        return {}, "报文解析结果不是对象"

    # 并入 URL query 参数 (body 同名键优先)
    if cfg.get("merge_query_params", True) and method != "GET":
        try:
            q = dict(request.query_params)
            if q:
                body = {**q, **body}
        except Exception:
            pass
    return body, None


def _xml_to_dict(text: str) -> dict:
    """极简 XML → dict (stdlib, 无依赖)。剥命名空间, 同名子标签聚成 list。

    <root><TaskNo>x</TaskNo></root> → {"root": {"TaskNo": "x"}}, 映射路径用
    root.TaskNo。SOAP 类报文按信封路径映射即可。属性暂不解析。
    """
    import xml.etree.ElementTree as ET

    def _node(elem):
        children = list(elem)
        if not children:
            return (elem.text or "").strip()
        d = {}
        for c in children:
            tag = c.tag.split('}')[-1]  # 剥命名空间
            v = _node(c)
            if tag in d:
                if not isinstance(d[tag], list):
                    d[tag] = [d[tag]]
                d[tag].append(v)
            else:
                d[tag] = v
        return d

    if not text or not text.strip():
        return {}
    root = ET.fromstring(text)
    return {root.tag.split('}')[-1]: _node(root)}


def _log_inbound(db, req_text: str, resp_text: str, success: bool,
                 status_code: int = 200, event_type: str = "task_start"):
    """写一条入站通讯日志 (复用 MESCommLog, connection_id=None, direction='inbound')。"""
    url = "/api/v1/mes/inbound/alarm/clear" if event_type == "alarm_clear" \
        else "/api/v1/mes/inbound/task"
    try:
        db.add(MESCommLog(
            connection_id=None,
            direction="inbound",
            event_type=event_type,
            method="POST",
            url=url,
            request_body=(req_text or "")[:4000],
            response_body=(resp_text or "")[:4000],
            status_code=status_code,
            success=bool(success),
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        debug_center.dbg("backend.mes", "入站日志写入失败", str(e))


# ============================================================
# 配置管理 (前端; 鉴权沿用网关 edit 权限)
# ============================================================
@router.get("/config", dependencies=[Depends(require_perm("mes.gateway.edit"))])
def get_inbound_config(db: Session = Depends(get_db)):
    return get_mes_inbound().get_config(db)


@router.put("/config", dependencies=[Depends(require_perm("mes.gateway.edit"))])
def put_inbound_config(payload: dict, request: Request, db: Session = Depends(get_db)):
    merged = get_mes_inbound().save_config(db, payload or {})
    db.commit()
    # 接收路径别名即时生效 (增删改): 重建动态路由, 无需重启
    try:
        register_inbound_aliases(request.app)
    except Exception as e:
        debug_center.dbg("backend.mes", "保存后重建别名失败", str(e))
    return merged


@router.get("/health")
@router.post("/health")
async def inbound_health(request: Request, db: Session = Depends(get_db)):
    """健康检查 (外部生产管控系统探活, 无鉴权)。

    按入站响应配置回 {code:0, message:"ok"} (川南协议约定格式); 响应格式 (json/xml)、
    业务码键名 (code_field/message_field) 均沿用入站配置, 与三个业务接口一致。
    """
    svc = get_mes_inbound()
    cfg = svc.get_config(db)
    resp = svc.build_response(cfg, "success", "ok")
    obj, _ = _make_response(cfg, resp, 200)
    return obj


@router.get("/active-alarms")
def get_active_alarms(channel_id: int = None, limit: int = 100,
                      db: Session = Depends(get_db)):
    """取未消除的在途报警 (供监控页持续横幅轮询)。不鉴权: 与监控页其它轮询端点一致。"""
    from backend.services.external_alarm import list_active_alarms
    return list_active_alarms(db, channel_id=channel_id, limit=limit)


@router.get("/logs", dependencies=[Depends(require_perm("mes.gateway.edit"))])
def get_inbound_logs(limit: int = 50, db: Session = Depends(get_db)):
    limit = max(1, min(int(limit or 50), 200))
    rows = (
        db.query(MESCommLog)
        .filter(MESCommLog.direction == "inbound")
        .order_by(MESCommLog.id.desc())
        .limit(limit)
        .all()
    )
    out = []
    for r in rows:
        created = getattr(r, "created_at", None)
        out.append({
            "id": r.id,
            "event_type": r.event_type,
            "success": r.success,
            "request_body": r.request_body,
            "response_body": r.response_body,
            "created_at": created.isoformat() if created else None,
        })
    return out
