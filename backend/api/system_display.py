"""
v3.5.0 系统级品牌/显示字段持久化 API

把前端 useSystemStore.display 中的几个字段（之前只在 localStorage）落库到
SystemConfig KV 表（key='display.xxx'），让自定义导出系统的 build_*_context()
能跨进程读取（实时规则在后端线程触发，前端 localStorage 不可达）。

字段：brand_name / app_name / inspector_name / device_number /
     factory_name / line_name

设计：
- GET  /api/v1/system/display      读全部 display 字段
- PUT  /api/v1/system/display      批量更新（前端保存时调用）
- GET  /api/v1/system/license-cache 读 License 缓存
- PUT  /api/v1/system/license-cache  写 License 缓存（前端 IPC 解析后调用）
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.api_key import require_api_key
from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.models import Project, SystemConfig


router = APIRouter()


# ==================== v3.23.x: 深度就绪探针 (不鉴权) ====================
# 给 Electron"加深启动就绪门槛"开关用 (默认关)。比 /source/status 更进一步:
# /source/status 200 只代表 uvicorn 起来了; 本探针真跑一次 ORM 查询确认
# "数据库 + 项目表能查得到", 查得到才算深度就绪。DB 没准备好时查询抛错 →
# 非 200 → Electron 继续等。刻意不挂 require_perm: 健康探针需匿名可达
# (鉴权开了也不能把开机探活挡在 401 外)。
@router.get("/startup-ready")
def get_startup_ready(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """深度就绪: 数据库 + 项目表可查则返回 {ready:true, projects:N}。"""
    n = db.query(Project).count()
    return {"ready": True, "projects": n}

DISPLAY_FIELDS = [
    "brand_name", "app_name",
    "inspector_name", "device_number",
    "factory_name", "line_name",
]


class DisplayPayload(BaseModel):
    brand_name: Optional[str] = None
    app_name: Optional[str] = None
    inspector_name: Optional[str] = None
    device_number: Optional[str] = None
    factory_name: Optional[str] = None
    line_name: Optional[str] = None


def _set_kv(db: Session, key: str, value: Optional[Any], desc: str = "") -> None:
    """upsert SystemConfig 单条 KV"""
    if value is None:
        v = ""
    elif isinstance(value, (str, int, float, bool)):
        v = str(value)
    else:
        import json as _json
        v = _json.dumps(value, ensure_ascii=False)

    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = v
        if desc and not row.description:
            row.description = desc
    else:
        db.add(SystemConfig(key=key, value=v, description=desc or None))


def _get_kv(db: Session, key: str) -> Optional[str]:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row else None


@router.get("/display")
def get_display(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """读 display 全部字段 — 前端启动时调用补全 useSystemStore.display"""
    out = {f: None for f in DISPLAY_FIELDS}
    rows = db.query(SystemConfig).filter(SystemConfig.key.like("display.%")).all()
    for r in rows:
        short_key = r.key.split(".", 1)[1] if "." in r.key else r.key
        if short_key in out:
            out[short_key] = r.value
    return out


@router.put("/display",
             dependencies=[Depends(require_perm("settings.edit"))])
def put_display(payload: DisplayPayload,
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    """更新 display 字段（前端 useSystemStore 保存时调用）"""
    updated = []
    data = payload.model_dump(exclude_unset=True)
    for k in DISPLAY_FIELDS:
        if k in data:
            _set_kv(db, f"display.{k}", data[k],
                    desc=f"display field for export context")
            updated.append(k)
    db.commit()
    return {"status": "ok", "updated": updated}


# ==================== B3 前端管理面板轮询间隔 (毫秒, 当前值作默认) ====================
# 键 polling.<panel>; 客户可调各管理面板数据刷新频率。核心检测循环/时钟不在此列。
POLLING_DEFAULTS = {
    "cluster_boxes": 5000,
    "cluster_slaves": 5000,
    "cluster_heartbeat": 10000,
    "gateway_health": 15000,
    "order_list": 10000,
    "scanner_status": 5000,
    "external_device": 5000,
    "wmax_status": 5000,
    "plc_status": 3000,   # RFC 13 PLC 连接卡片列表刷新
    "plc_live": 1500,     # RFC 13 PLC 点位实时值/IO 日志刷新
    "trigger_status": 3000,  # RFC 14 触发源卡片列表刷新
    "trigger_live": 1500,    # RFC 14 触发源实时状态/历史/日志刷新
}
_POLLING_MIN_MS = 500   # 下限保护: 太频繁会压垮后端


@router.get("/polling")
def get_polling(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """读各管理面板轮询间隔(ms), 缺省回落默认。前端启动时加载一次。"""
    out = dict(POLLING_DEFAULTS)
    rows = db.query(SystemConfig).filter(SystemConfig.key.like("polling.%")).all()
    for r in rows:
        k = r.key.split(".", 1)[1] if "." in r.key else r.key
        if k in out:
            try:
                v = int(float(r.value))
                if v >= _POLLING_MIN_MS:
                    out[k] = v
            except Exception:
                pass
    return out


@router.put("/polling",
            dependencies=[Depends(require_perm("settings.edit"))])
def put_polling(payload: dict, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """更新管理面板轮询间隔(ms)。仅认白名单键, 低于下限或非法值忽略。"""
    updated = []
    for k, v in (payload or {}).items():
        if k not in POLLING_DEFAULTS:
            continue
        try:
            iv = int(float(v))
        except Exception:
            continue
        if iv < _POLLING_MIN_MS:
            continue
        _set_kv(db, f"polling.{k}", iv, desc="frontend panel polling interval(ms)")
        updated.append(k)
    db.commit()
    return {"status": "ok", "updated": updated, "config": get_polling(db)}


# ==================== 日志显示条数可配 ====================
# 键 loglimit.<panel>; 客户可调各管理面板「最近日志」一次拉取/显示的条数, 不写死 20/30/50。
LOG_LIMIT_DEFAULTS = {
    "scanner": 30,
    "external_device": 30,
    "inbound": 50,
    "gateway": 50,
    "cluster": 20,
    "plc": 60,            # RFC 13 PLC IO 日志显示条数
    "trigger": 60,        # RFC 14 触发中心日志显示条数
}
_LOG_LIMIT_MIN = 1
_LOG_LIMIT_MAX = 500


@router.get("/log-limits")
def get_log_limits(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """读各管理面板日志显示条数, 缺省回落默认。前端启动时加载一次。"""
    out = dict(LOG_LIMIT_DEFAULTS)
    rows = db.query(SystemConfig).filter(SystemConfig.key.like("loglimit.%")).all()
    for r in rows:
        k = r.key.split(".", 1)[1] if "." in r.key else r.key
        if k in out:
            try:
                v = int(float(r.value))
                if _LOG_LIMIT_MIN <= v <= _LOG_LIMIT_MAX:
                    out[k] = v
            except Exception:
                pass
    return out


@router.put("/log-limits",
            dependencies=[Depends(require_perm("settings.edit"))])
def put_log_limits(payload: dict, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """更新管理面板日志显示条数。仅认白名单键, 越界值忽略。"""
    updated = []
    for k, v in (payload or {}).items():
        if k not in LOG_LIMIT_DEFAULTS:
            continue
        try:
            iv = int(float(v))
        except Exception:
            continue
        if not (_LOG_LIMIT_MIN <= iv <= _LOG_LIMIT_MAX):
            continue
        _set_kv(db, f"loglimit.{k}", iv, desc="frontend panel log display count")
        updated.append(k)
    db.commit()
    return {"status": "ok", "updated": updated, "config": get_log_limits(db)}


# ==================== RFC12 展会: 设备/系统实时状态 ====================
# 给"设备状态"高科技面板提供真实数据 (GPU 温度/利用率/显存 + CPU/内存/磁盘).
# 全部 try/except 降级: 缺 psutil / pynvml / cuda 不可用时返回 None, 不报错.

def _collect_cpu_mem_disk() -> Dict[str, Any]:
    out = {"cpu": None, "memory": None, "disk": None}
    try:
        import psutil  # type: ignore
    except Exception:
        return out
    try:
        freq = psutil.cpu_freq()
        out["cpu"] = {
            "percent": psutil.cpu_percent(interval=None),
            "cores": psutil.cpu_count(logical=True),
            "freq_mhz": round(freq.current, 0) if freq else None,
        }
    except Exception:
        pass
    try:
        vm = psutil.virtual_memory()
        out["memory"] = {
            "total_mb": round(vm.total / 1024 ** 2, 0),
            "used_mb": round(vm.used / 1024 ** 2, 0),
            "percent": vm.percent,
        }
    except Exception:
        pass
    try:
        du = psutil.disk_usage("/")
        out["disk"] = {
            "total_gb": round(du.total / 1024 ** 3, 1),
            "used_gb": round(du.used / 1024 ** 3, 1),
            "percent": du.percent,
        }
    except Exception:
        pass
    return out


def _collect_gpus() -> Dict[str, Any]:
    """优先 pynvml 拿温度/利用率; 退化到 torch.cuda 拿名称/显存."""
    gpus = []
    cuda_available = False
    # pynvml 路径 (温度/利用率最全)
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        for i in range(count):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
            except Exception:
                util = None
            try:
                temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                temp = None
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode("utf-8", "ignore")
            gpus.append({
                "index": i,
                "name": name,
                "mem_total_mb": round(mem.total / 1024 ** 2, 0),
                "mem_used_mb": round(mem.used / 1024 ** 2, 0),
                "util_percent": util,
                "temperature_c": temp,
            })
        pynvml.nvmlShutdown()
        cuda_available = count > 0
        return {"gpus": gpus, "cuda_available": cuda_available}
    except Exception:
        pass
    # torch.cuda 退化路径 (无温度/利用率)
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            cuda_available = True
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                gpus.append({
                    "index": i,
                    "name": torch.cuda.get_device_name(i),
                    "mem_total_mb": round(props.total_memory / 1024 ** 2, 0),
                    "mem_used_mb": round(torch.cuda.memory_allocated(i) / 1024 ** 2, 0),
                    "util_percent": None,
                    "temperature_c": None,
                })
    except Exception:
        pass
    return {"gpus": gpus, "cuda_available": cuda_available}


@router.get("/device-status")
def get_device_status() -> Dict[str, Any]:
    """RFC12: 设备/系统实时状态. 供监控页"设备状态"面板真实数据驱动.

    无 psutil/pynvml/cuda 时各字段降级为 None, 接口仍 200, 前端按缺省渲染.
    """
    import time as _time
    sysinfo = _collect_cpu_mem_disk()
    gpuinfo = _collect_gpus()
    return {
        "ok": True,
        "ts": int(_time.time() * 1000),
        "cpu": sysinfo["cpu"],
        "memory": sysinfo["memory"],
        "disk": sysinfo["disk"],
        "gpus": gpuinfo["gpus"],
        "cuda_available": gpuinfo["cuda_available"],
    }


@router.get("/license-cache")
def get_license_cache(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """读后端缓存的 License 信息（实时规则触发导出时使用）

    返回 {} 表示尚未缓存（前端启动后调 PUT 写入一次）
    """
    raw = _get_kv(db, "license.cache")
    if not raw:
        return {}
    try:
        import json as _json
        return _json.loads(raw)
    except Exception:
        return {}


class LicenseCachePayload(BaseModel):
    customer: Optional[str] = None
    machine_id: Optional[str] = None
    expires_at: Optional[str] = None
    is_perpetual: Optional[bool] = None
    days_remaining: Optional[int] = None
    features: Optional[list] = None


@router.put("/license-cache",
             dependencies=[Depends(require_api_key("license.cache"))])
# M2M-like: License IPC 缓存写入 — auth=off 放行, auth=on 必须 X-API-Key scope=license.cache
def put_license_cache(payload: LicenseCachePayload,
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    """前端通过 Electron IPC 拿到 License 信息后，PUT 一次到后端缓存

    实时规则触发导出时无前端在场，build_cycle_context 走 SystemConfig 读取。
    """
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "至少需要一个字段")
    _set_kv(db, "license.cache",
            {k: v for k, v in data.items() if v is not None},
            desc="License cache from frontend IPC")
    db.commit()
    return {"status": "ok"}
