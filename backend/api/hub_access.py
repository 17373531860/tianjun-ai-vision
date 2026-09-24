"""
Web 集中管控枢纽 · 边缘侧接入端点（RFC 15 M0 读面 + M3 写入口）

路由前缀: /api/v1/hub

端点列表:
  GET  /config          读 hub 接入开关 (settings.edit)
  PUT  /config          写 hub 接入开关 (settings.edit)
  GET  /handshake       纳管前探测: 身份/版本/License 状态/能力档案 hash (无鉴权)
  GET  /profile         完整能力档案 (X-API-Key scope=hub)
  GET  /health-summary  高频轮询摘要: 工位状态 + 资源指标 (X-API-Key scope=hub)
  POST /ops             枢纽写操作入口: 启停检测/切项目 (X-API-Key scope=hub, M3)
  GET  /projects        精简项目列表 (枢纽切项目下拉用, X-API-Key scope=hub, M3)
  GET  /events          周期结算事件增量拉取, DB 游标断点续传 (X-API-Key, P0-10)

守门语义:
  - 全部业务端点挂 `hub_access.enabled` KV 守门, 默认关闭时 404 —— 未纳管的
    存量客户机零行为差异 (RFC 15 不变量"边缘零风险")。/config 两端点不受守门
    (否则永远开不了)。
  - handshake 不要求 API Key: 只回身份事实, 由枢纽读 license.state 决定
    是否纳管 ("握手拒绝"发生在枢纽侧, 边缘只报事实)。
  - License 事实源是前端 IPC 写入的 `license.cache` KV
    (backend/api/system_display.py → PUT /system/license-cache);
    缓存为空时 state='unknown', 由枢纽按策略处理 (默认拒绝纳管)。

能力档案 (Capability Profile) 契约:
  - schema 见 RFC 15 §9.4: property/action/event 三元组, api_contract 整数
    契约号独立于营销版本号, 枢纽只比较契约号。
  - 字段增删必须过 tests/test_hub_access.py 的 schema 契约测试。
"""
import hashlib
import json
import os
import socket
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.api_key import require_api_key
from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.models import Project, SystemConfig

router = APIRouter()

# API 契约号: 枢纽据此判断兼容性 (RFC 15 不变量 3 "禁止 if-version")。
# 档案/握手/健康摘要的 schema 有不兼容变更时 +1。
HUB_API_CONTRACT = 1

_ENABLED_KEY = "hub_access.enabled"
_NODE_ID_KEY = "hub_access.node_id"


# ============================================================
# KV helpers
# ============================================================

def _get_kv(db: Session, key: str) -> Optional[str]:
    cfg = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return cfg.value if cfg else None


def _set_kv(db: Session, key: str, value: str, desc: str) -> None:
    cfg = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if cfg:
        cfg.value = value
    else:
        db.add(SystemConfig(key=key, value=value, description=desc))


def _is_enabled(db: Session) -> bool:
    raw = _get_kv(db, _ENABLED_KEY)
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def _ensure_enabled(db: Session) -> None:
    """hub 接入未开启时对外表现为端点不存在 (404), 与升级前零差异。"""
    if not _is_enabled(db):
        raise HTTPException(status_code=404, detail="Not Found")


def _node_id(db: Session) -> str:
    """稳定节点 ID: 首次生成后落 KV, 与 machineId 解耦 (License 未激活也有身份)。"""
    nid = _get_kv(db, _NODE_ID_KEY)
    if nid:
        return nid
    nid = f"edge-{uuid.uuid4().hex[:8]}"
    _set_kv(db, _NODE_ID_KEY, nid, desc="Hub 纳管节点 ID (RFC 15, 自动生成)")
    db.commit()
    return nid


# ============================================================
# 档案构建
# ============================================================

def _license_block(db: Session) -> Dict[str, Any]:
    """从 license.cache KV 提取授权状态事实 (缓存由前端 IPC 写入)。"""
    raw = _get_kv(db, "license.cache")
    if not raw:
        return {"state": "unknown", "expires_at": None,
                "days_remaining": None, "digest": None}
    try:
        cache = json.loads(raw)
    except Exception:
        return {"state": "unknown", "expires_at": None,
                "days_remaining": None, "digest": None}

    days = cache.get("days_remaining")
    if cache.get("is_perpetual"):
        state = "valid"
    elif days is not None and int(days) < 0:
        state = "expired"
    else:
        state = "valid"
    return {
        "state": state,
        "expires_at": cache.get("expires_at"),
        "days_remaining": days,
        "machine_id": cache.get("machine_id"),
        "digest": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
    }


def _identity_block(db: Session) -> Dict[str, Any]:
    lic = _license_block(db)
    return {
        "node_id": _node_id(db),
        "hostname": socket.gethostname(),
        "machine_id": lic.get("machine_id"),
        "app_version": os.environ.get("TIANJUN_APP_VERSION", ""),
        "api_contract": HUB_API_CONTRACT,
        "license": {k: v for k, v in lic.items() if k != "machine_id"},
    }


# 工位级标准三元组 (RFC 15 §9.4 "工位 Device Type 1.0"):
# detecting / active_project_id 是孪生 desired 白名单的来源 (带 write);
# M0 全通道同一能力面, 后续按 logic_mode / 外设差异化。
_STATION_PROPERTIES = [
    {"id": "detecting", "access": ["read", "write", "notify"], "format": "bool"},
    {"id": "active_project_id", "access": ["read", "write", "notify"], "format": "int"},
    {"id": "logic_mode", "access": ["read"], "format": "str"},
    # M7: health-summary 早就带这两项, 档案补声明让枢纽 UI 自动渲染 (差距 B1)
    {"id": "source_type", "access": ["read"], "format": "str"},
    {"id": "fps_inference", "access": ["read"], "format": "float"},
]
_STATION_ACTIONS: list = [
    # M3: 启停检测 (POST /hub/ops action=start_detection/stop_detection)。
    # 语义上是写 detecting property, 但作为 action 登记让枢纽 UI 直接渲染按钮。
    {"id": "start_detection", "label": "开始检测", "confirm": "normal"},
    {"id": "stop_detection", "label": "停止检测", "confirm": "danger"},
    # M4: 消警 (远程停掉本工位报警器, 对齐 POST /alarm/stop 语义, 幂等)。
    # 一次性 RPC 动作 (RFC 15 §10.3-5): 枢纽不为它写 desired。
    {"id": "ack_alarm", "label": "消警", "confirm": "normal"},
]
_STATION_EVENTS: list = []           # M3 暂无事件通道; 后续随 /hub/events + WS 补
_NODE_ACTIONS: list = [
    # M3: 切项目 (节点级 —— 激活项目影响整机全部工位, 检测中被边缘 409 拒绝)。
    {"id": "activate_project", "label": "切换项目", "confirm": "danger",
     "params": [{"id": "project_id", "format": "int"}]},
]


def _stations_block() -> list:
    """按 ChannelManager 现状枚举工位 (函数体内 import, 避免模块导入拉起 cv2)。"""
    from backend.api.channel_manager import channel_manager

    try:
        sources = channel_manager.get_channel_sources() or {}
    except Exception:
        sources = {}

    stations = []
    for ch_id in sorted(channel_manager.channels.keys()):
        mgr = channel_manager.channels[ch_id]
        pc = getattr(mgr, "project_config", None) or {}
        stations.append({
            "channel_id": ch_id,
            "logic_mode": pc.get("logic_mode", "detection"),
            "bound_project_id": (sources.get(str(ch_id)) or {}).get("project_id"),
            "properties": _STATION_PROPERTIES,
            "actions": _STATION_ACTIONS,
            "events": _STATION_EVENTS,
        })
    return stations


def _profile_payload(db: Session) -> Dict[str, Any]:
    payload = {
        "profile_schema": 1,
        "identity": _identity_block(db),
        "stations": _stations_block(),
        "node_actions": _NODE_ACTIONS,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    payload["profile_hash"] = "sha256:" + hashlib.sha256(
        canonical.encode("utf-8")).hexdigest()[:32]
    return payload


# ============================================================
# 端点: 开关管理 (不受 enabled 守门)
# ============================================================

class HubConfigPayload(BaseModel):
    enabled: bool


class HubConfigResponse(BaseModel):
    enabled: bool


class HubHandshakeResponse(BaseModel):
    identity: Dict[str, Any]
    profile_hash: str
    station_count: int


class HubProfileResponse(BaseModel):
    # 顶层键用 profile_schema 而非 "schema": 后者是 pydantic BaseModel 保留名
    profile_schema: int
    identity: Dict[str, Any]
    stations: list
    node_actions: list
    profile_hash: str


class HubHealthSummaryResponse(BaseModel):
    node_id: str
    active_project_id: Optional[int]
    stations: list
    resources: Dict[str, Any]
    detecting_stations: int


class HubOpsPayload(BaseModel):
    action: str                        # start_detection / stop_detection / activate_project
    channel: int = 0
    project_id: Optional[int] = None   # activate_project 用


class HubOpsResponse(BaseModel):
    ok: bool
    action: str
    channel: int
    message: str


class HubProjectsResponse(BaseModel):
    items: list


class HubEventsResponse(BaseModel):
    events: list
    next_cursor: int


class HubLiveResponse(BaseModel):
    """工位实时投影 (M7.5): 值班读面白名单字段, 大载荷已剥离。"""
    channel_id: int
    is_detecting: bool
    is_running: bool
    logic_mode: Optional[str] = None
    project_id: Optional[int] = None
    project_name: Optional[str] = None
    fps_inference: Optional[float] = None
    counters: Dict[str, Any] = {}
    steps: list = []
    cycle: Dict[str, Any] = {}
    tracking: Optional[Dict[str, Any]] = None
    recent_events: list = []


@router.get("/config", response_model=HubConfigResponse,
            summary="读 Hub 接入开关",
            dependencies=[Depends(require_perm("settings.edit"))])
def get_hub_config(db: Session = Depends(get_db)):
    """读 hub 接入开关 (边缘设置页 / 纳管向导用)"""
    return {"enabled": _is_enabled(db)}


@router.put("/config", response_model=HubConfigResponse,
            summary="写 Hub 接入开关",
            dependencies=[Depends(require_perm("settings.edit"))])
def put_hub_config(payload: HubConfigPayload, db: Session = Depends(get_db)):
    """写 hub 接入开关。开启后 /hub/handshake 等端点才可见。"""
    _set_kv(db, _ENABLED_KEY, "true" if payload.enabled else "false",
            desc="Web 集中管控枢纽接入开关 (RFC 15); 关闭时 /hub/* 业务端点 404")
    db.commit()
    return {"enabled": payload.enabled}


# ============================================================
# 端点: 纳管与轮询 (受 enabled 守门)
# ============================================================

@router.get("/handshake", response_model=HubHandshakeResponse,
            summary="纳管前握手探测 (身份/License 状态/档案 hash)")
def hub_handshake(db: Session = Depends(get_db)):
    """纳管前探测: 身份 + License 状态 + 档案 hash。

    无 API Key 要求 (只回身份事实, 不含业务数据); 是否纳管由枢纽判定
    (license.state != 'valid' 时枢纽应拒绝, RFC 15 §3.4)。
    """
    _ensure_enabled(db)
    profile = _profile_payload(db)
    return {
        "identity": profile["identity"],
        "profile_hash": profile["profile_hash"],
        "station_count": len(profile["stations"]),
    }


@router.get("/profile", response_model=HubProfileResponse,
            summary="完整能力档案 (RFC 15 §9.4 三元组)",
            dependencies=[Depends(require_api_key("hub"))])
def hub_profile(db: Session = Depends(get_db)):
    """完整能力档案 (RFC 15 §4.1/§9.4)。枢纽仅在 handshake hash 变化时拉全量。"""
    _ensure_enabled(db)
    return _profile_payload(db)


@router.get("/health-summary", response_model=HubHealthSummaryResponse,
            summary="健康摘要 (工位状态 + 资源指标, 枢纽高频轮询)",
            dependencies=[Depends(require_api_key("hub"))])
def hub_health_summary(db: Session = Depends(get_db)):
    """高频轮询摘要: 一次拉齐工位状态 + 资源指标, 避免枢纽打散装端点。"""
    _ensure_enabled(db)
    from backend.api.channel_manager import channel_manager
    from backend.api.system_display import _collect_cpu_mem_disk, _collect_gpus

    active = db.query(Project).filter(Project.is_active == True).first()  # noqa: E712

    stations = []
    for ch_id in sorted(channel_manager.channels.keys()):
        mgr = channel_manager.channels[ch_id]
        pc = getattr(mgr, "project_config", None) or {}
        stations.append({
            "channel_id": ch_id,
            "is_running": bool(getattr(mgr, "is_running", False)),
            "is_detecting": bool(getattr(mgr, "is_detecting", False)),
            "logic_mode": pc.get("logic_mode", "detection"),
            "source_type": getattr(mgr, "source_type", None),
            "fps_inference": getattr(mgr, "fps_inference", 0),
        })

    try:
        sysinfo = _collect_cpu_mem_disk()
    except Exception:
        sysinfo = {"cpu": None, "memory": None, "disk": None}
    try:
        gpuinfo = _collect_gpus()
    except Exception:
        gpuinfo = {"gpus": [], "cuda_available": False}

    return {
        "node_id": _node_id(db),
        "active_project_id": active.id if active else None,
        "stations": stations,
        "resources": {
            "cpu": sysinfo.get("cpu"),
            "memory": sysinfo.get("memory"),
            "disk": sysinfo.get("disk"),
            "gpus": gpuinfo.get("gpus", []),
        },
        "detecting_stations": sum(1 for s in stations if s["is_detecting"]),
    }


# ============================================================
# 端点: 写操作入口 (RFC 15 M3)
# ============================================================

@router.post("/ops", response_model=HubOpsResponse,
             summary="枢纽写操作入口 (启停检测/切项目, RFC 15 M3)",
             dependencies=[Depends(require_api_key("hub"))])
def hub_ops(payload: HubOpsPayload, db: Session = Depends(get_db)):
    """枢纽的唯一写操作入口。

    为什么不让枢纽直接转发 /source/detection/start 等本机端点:
      1. 鉴权体系不同 —— 本机端点挂 require_perm (用户 token 体系), 枢纽持有的
         是 M2M API Key; 边缘开启鉴权后转发必撞权限墙。
      2. /detection/start 的请求体承载了前端 Monitor 拼装的模型/阈值 payload,
         空 body 会把 conf/iou 静默重置成默认值 (0.25/0.45), 覆盖客户调好的参数。
    因此这里对齐的是后端侧既有先例:
      - start: mes_inbound._auto_start_detection 的门槛语义 (源已配置/模型就绪/
        未在检测), 用通道上已加载模型, 不碰 conf/iou; 成功后按 HTTP /detection/start
        同款开 session (数据页有会话记录)。
      - stop: 对齐 POST /detection/stop (end_session + stop_detection)。
      - activate_project: 对齐 POST /projects/{id}/activate 的"检测中 409"守门 +
        activate_project_core (与手动激活/开工切项目同一核心)。
      - ack_alarm (M4): 对齐 POST /alarm/stop (alarm_router.stop_alarm, 幂等)。
    幂等语义: start 时已在检测 / stop 时已停止 → ok=True 原样返回, 不报错
    (枢纽收敛循环可能重放意图)。
    """
    _ensure_enabled(db)
    from backend.api.channel_manager import channel_manager

    action = payload.action
    ch = payload.channel

    if action == "activate_project":
        if not payload.project_id:
            raise HTTPException(status_code=400, detail="activate_project 需要 project_id")
        detecting = [
            ch_id for ch_id, m in channel_manager.channels.items()
            if getattr(m, "is_detecting", False)
        ]
        if detecting:
            raise HTTPException(
                status_code=409,
                detail=f"有工位正在检测中 (通道: {sorted(detecting)}), "
                       f"请先停止检测再切换项目",
            )
        from backend.api.projects import activate_project_core
        try:
            db_project, _, _, _ = activate_project_core(db, payload.project_id)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"激活项目失败: {e}")
        return {"ok": True, "action": action, "channel": ch,
                "message": f"已激活项目 {db_project.name} (id={db_project.id})"}

    mgr = channel_manager.channels.get(ch)
    if mgr is None:
        raise HTTPException(status_code=404, detail=f"通道 {ch} 不存在")

    if action == "start_detection":
        if getattr(mgr, "is_detecting", False):
            return {"ok": True, "action": action, "channel": ch,
                    "message": f"ch{ch} 已在检测中"}
        if not mgr.is_running and not getattr(mgr, "source_type", None):
            raise HTTPException(status_code=409, detail=f"ch{ch} 未配置视频源")
        if getattr(mgr, "model", None) is None \
                and getattr(mgr, "source_type", None) != "synthetic":
            raise HTTPException(
                status_code=409,
                detail=f"ch{ch} 模型未就绪 (请在项目管理为该项目配置默认模型)")
        try:
            mgr.start_detection()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"启动检测失败: {e}")
        session_msg = ""
        try:
            pc = getattr(mgr, "project_config", None) or {}
            if pc.get("id"):
                info = mgr.start_session(pc["id"])
                if info:
                    session_msg = f", 会话 {info.get('session_id')}"
        except Exception:
            pass  # 会话开启失败不回滚检测 (与本机行为一致: 检测已在跑)
        return {"ok": True, "action": action, "channel": ch,
                "message": f"ch{ch} 检测已启动{session_msg}"}

    if action == "stop_detection":
        if not getattr(mgr, "is_detecting", False):
            return {"ok": True, "action": action, "channel": ch,
                    "message": f"ch{ch} 已是停止状态"}
        try:
            mgr.end_session()
            mgr.stop_detection()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"停止检测失败: {e}")
        return {"ok": True, "action": action, "channel": ch,
                "message": f"ch{ch} 检测已停止"}

    if action == "ack_alarm":
        # 对齐 POST /alarm/stop: 停本工位报警器 (灯塔/蜂鸣)。幂等 —— 没在响也
        # 安全返回; 不碰 mes_inbound 在途报警横幅 (那是外部 MES 对接契约,
        # 默认只能由上游回推消除, 枢纽不越权)。
        from backend.api.alarm import alarm_router
        try:
            alarm_router.stop_alarm(ch)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"消警失败: {e}")
        return {"ok": True, "action": action, "channel": ch,
                "message": f"ch{ch} 报警已消除"}

    raise HTTPException(status_code=400, detail=f"不支持的 action: {action}")


@router.get("/events", response_model=HubEventsResponse,
            summary="周期结算事件增量拉取 (游标断点续传, RFC 15 P0-10)",
            dependencies=[Depends(require_api_key("hub"))])
def hub_events(cursor: Optional[int] = None, limit: int = 100,
               result: Optional[str] = None, db: Session = Depends(get_db)):
    """事件通道真相源: 已结算周期按 DetectionCycle.id 自增游标增量拉取。

    设计 (RFC 15 §9.1):
      - 事实源直接用周期表 —— 零 hook 侵入, 边缘崩溃重启不丢事件,
        游标天然断点续传 (对账真相源; WS 推送只是加速器, P1 再加)。
      - cursor 未传 (首次纳管): 不翻历史帐, 回最近 limit 条 + 当前最大 id,
        "从现在开始订阅"。cursor>=0: 严格增量 (id > cursor)。
      - result=ng: 只回 NG 周期 (枢纽报警中心默认只拉 NG, 省流量省库)。
    """
    _ensure_enabled(db)
    from backend.models.models import DetectionCycle, DetectionSession

    limit = max(1, min(int(limit), 500))
    # RFC 15 M5: 载荷带耗时与项目维度 (枢纽数据中心按项目分组/算节拍),
    # project 从 session 侧取 (周期表无冗余列), outerjoin 容忍项目已删
    q = (db.query(DetectionCycle, DetectionSession.channel_id,
                  DetectionSession.project_id, Project.name)
         .join(DetectionSession,
               DetectionCycle.session_id == DetectionSession.id)
         .outerjoin(Project, DetectionSession.project_id == Project.id)
         .filter(DetectionCycle.end_time.isnot(None)))
    if result == "ng":
        q = q.filter(DetectionCycle.is_good == False)  # noqa: E712

    if cursor is None:
        rows = q.order_by(DetectionCycle.id.desc()).limit(limit).all()
        rows = list(reversed(rows))
    else:
        rows = (q.filter(DetectionCycle.id > int(cursor))
                .order_by(DetectionCycle.id.asc()).limit(limit).all())

    # next_cursor 必须盖住"被 result 过滤掉的行", 否则跳过的 OK 周期会让游标
    # 停滞 → 每轮重扫; 取全表当前最大 id 与本批最大 id 的较大者。
    max_id = db.query(func.max(DetectionCycle.id)).scalar() or 0
    batch_max = rows[-1][0].id if rows else 0
    next_cursor = max(int(cursor or 0), batch_max)
    if cursor is None or not rows:
        next_cursor = max(next_cursor, max_id)
    elif len(rows) < limit:
        # 增量拉完了本批 (没有被 limit 截断) → 可以安全跳到全表最大 id
        next_cursor = max_id

    events = [{
        "id": c.id,
        "kind": "cycle",
        "channel_id": ch if ch is not None else 0,
        "ts": c.end_time.isoformat() if c.end_time else None,
        "result": "OK" if c.is_good else "NG",
        "event_name": c.event_name,
        "reason": c.result_reason,
        # M5 数据中心维度: 耗时毫秒 (None=旧数据无耗时) + 项目 (None=已删/未绑)
        "duration_ms": int(c.duration * 1000) if c.duration else None,
        "project_id": pid,
        "project_name": pname,
    } for c, ch, pid, pname in rows]
    return {"events": events, "next_cursor": next_cursor}


@router.get("/projects", response_model=HubProjectsResponse,
            summary="精简项目列表 (枢纽切项目下拉, RFC 15 M3)",
            dependencies=[Depends(require_api_key("hub"))])
def hub_projects(db: Session = Depends(get_db)):
    """给枢纽的项目下拉数据: 只回 id/name/is_active, 不带 7 个 JSON 配置大字段。

    不复用 GET /projects: 那是本机用户端点 (完整配置载荷 + 用户鉴权体系),
    枢纽只需要"切到哪个"的最小事实, 且鉴权走 API Key 体系保持 /hub/* 内闭环。
    """
    _ensure_enabled(db)
    rows = db.query(Project.id, Project.name, Project.is_active) \
        .order_by(Project.id).all()
    return {"items": [
        {"id": r[0], "name": r[1], "is_active": bool(r[2])} for r in rows
    ]}


@router.get("/live", response_model=HubLiveResponse,
            summary="工位实时投影 (值班读面, RFC 15 M7.5)",
            dependencies=[Depends(require_api_key("hub"))])
def hub_live(channel: int = 0, db: Session = Depends(get_db)):
    """单工位运行实况的白名单裁剪投影。

    - 复用 /source/detection/results 的聚合逻辑 (不重造状态机读面),
      只透出值班读面小字段; 截图/检测框/配置 JSON 等大载荷全部剥掉。
    - 按需调用: 枢纽只在有人打开该工位下钻页时 ~2s 轮询, 不进 poller
      常规链路 —— 无人看就零开销。
    """
    _ensure_enabled(db)
    from backend.api.source_routes import get_detection_results
    full = get_detection_results(channel=channel, known_shots=None)

    # 步骤读面: 配置顺序 + 完成计数 + 本周期是否已过 + in-flight 秒数
    step_counts = full.get("step_counts") or {}
    inflight = full.get("step_inflight_durations") or {}
    cycle_steps = set(full.get("current_cycle_steps") or [])
    steps = []
    for s in (full.get("steps_config") or []):
        label = s.get("label") or s.get("name") or ""
        if not label:
            continue
        steps.append({
            "label": label,
            "enabled": bool(s.get("enabled", True)),
            "count": int(step_counts.get(label, 0) or 0),
            "in_cycle": label in cycle_steps,
            "inflight_s": inflight.get(label),
        })

    recent = [{
        "name": e.get("event_name") or "",
        "kind": e.get("toast_id"),        # ok / ng / 提示类
        "reason": e.get("reason"),
        "ts": e.get("timestamp"),
    } for e in (full.get("recent_events") or [])[-5:]]

    trk = full.get("tracking") or {}
    return {
        "channel_id": channel,
        "is_detecting": bool(full.get("is_detecting")),
        "is_running": bool(full.get("is_running")),
        "logic_mode": full.get("logic_mode"),
        "project_id": full.get("project_id"),
        "project_name": full.get("project_name"),
        "fps_inference": full.get("fps_inference"),
        "counters": full.get("counters") or {},
        "steps": steps,
        "cycle": {
            "active": bool(cycle_steps) or bool(trk.get("cycle_active")),
            "current_time": full.get("current_cycle_time"),
            "average_time": full.get("average_cycle_time"),
            "last_time": full.get("last_cycle_time"),
        },
        "tracking": {
            "active_count": trk.get("active_count"),
            "class_counters": trk.get("class_counters"),
        } if full.get("logic_mode") in ("tracking", "custom_mix") else None,
        "recent_events": recent,
    }
