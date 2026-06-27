"""
在途报警台账 (External Active Alarm Board)

通用能力, 服务于"外部生产管控系统报警闭环":
  出站推送报警 → 登记一条在途报警 (record_active_alarm)
  外部系统回推消除命令 → 按唯一键匹配并消除 (clear_alarms)
  监控页轮询 → 取未消除报警做持续横幅 (list_active_alarms)

唯一区分键参照客户约定 = task_no + product_code + step_code + operator。
匹配用哪几个字段、去重窗口秒数, 都由调用方 (入站配置) 传入, 本模块不含任何客户分支。
"""
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func

from backend.models.mes_models import ExternalActiveAlarm
from backend.core import debug_center


# 默认唯一键字段 (与客户 v4 第 6 条一致)
DEFAULT_MATCH_FIELDS = ["task_no", "product_code", "step_code", "operator"]

# 台账原生列 (5 个独立列, 可被索引/列等值匹配)
NATIVE_FIELDS = ["task_no", "product_code", "step_code", "operator", "warning_text"]
# 向后兼容别名
LEDGER_FIELDS = NATIVE_FIELDS

# extra_data 里存扩展维度的命名空间 (避免与其它 extra_data 用途冲突)
EXTRA_NS = "ext"
# 扩展维度键的合法字符 (用于 json_extract 路径, 防注入/非法路径)
_SAFE_DIM_KEY = re.compile(r"^[A-Za-z0-9_]+$")


def _split_fields(fields: dict):
    """归一化输入字段 → (native: 5 原生列 dict, extra: 其余扩展维度 dict)。

    扩展维度只收"非原生列、有值、键名合法"的字段, 存进 extra_data.ext。
    """
    native = {k: _norm(fields.get(k)) for k in NATIVE_FIELDS}
    extra = {}
    for k, v in (fields or {}).items():
        if k in NATIVE_FIELDS:
            continue
        nv = _norm(v)
        if nv is not None and _SAFE_DIM_KEY.match(str(k)):
            extra[str(k)] = nv
    return native, extra


def _match_col(key: str):
    """返回某匹配键对应的 SQL 表达式: 原生列直接取列, 扩展维度走 json_extract。"""
    if key in NATIVE_FIELDS:
        return getattr(ExternalActiveAlarm, key)
    return func.json_extract(ExternalActiveAlarm.extra_data, f"$.{EXTRA_NS}.{key}")


def _match_value(native: dict, extra: dict, key: str):
    return native.get(key) if key in NATIVE_FIELDS else extra.get(key)


def _resolve_match_keys(match_fields):
    """整理匹配键列表: 去空、扩展维度键名必须合法; 空则回落四要素。"""
    keys = []
    for k in (match_fields or DEFAULT_MATCH_FIELDS):
        if not k:
            continue
        if k in NATIVE_FIELDS or _SAFE_DIM_KEY.match(str(k)):
            keys.append(str(k))
    return keys or DEFAULT_MATCH_FIELDS


def record_active_alarm(db, fields: dict, event_type: Optional[str] = None,
                        channel_id: Optional[int] = None,
                        dedup_sec: int = 0, match_fields=None) -> dict:
    """登记一条在途报警。返回 {"recorded": bool, "skipped": bool, "id": int|None}。

    dedup_sec > 0 时: 同唯一键已有一条 active 报警且 raised_at 距今 <= dedup_sec
    → 跳过登记 (去重), 不重复刷台账。
    match_fields 指定"什么算同一条报警" (默认四要素); 与消除 clear_alarms 用同一套配置,
    避免去重口径与消除口径不一致 (客户改了匹配维度, 去重也要跟着改)。
    """
    native, extra = _split_fields(fields)

    if dedup_sec and dedup_sec > 0:
        existing = _find_recent_active(db, native, extra, dedup_sec,
                                      match_fields=match_fields)
        if existing is not None:
            debug_center.dbg("backend.mes", "在途报警去重跳过",
                             f"唯一键已在 {dedup_sec}s 窗口内登记过 (id={existing.id}), 不重复登记")
            return {"recorded": False, "skipped": True, "id": existing.id}

    row = ExternalActiveAlarm(
        task_no=native.get("task_no"),
        product_code=native.get("product_code"),
        step_code=native.get("step_code"),
        operator=native.get("operator"),
        warning_text=native.get("warning_text"),
        # 扩展维度存进 extra_data.ext (命名空间隔离), 供 json_extract 去重/消除/匹配
        extra_data={EXTRA_NS: extra} if extra else None,
        channel_id=channel_id,
        event_type=event_type,
        status="active",
        # 显式落本地时间: 去重窗口与 datetime.now() 同口径, 不被 SQLite func.now()(UTC) 错开
        raised_at=datetime.now(),
    )
    db.add(row)
    db.flush()
    debug_center.dbg("backend.mes", "登记在途报警",
                     f"id={row.id} task_no={native.get('task_no')} "
                     f"product={native.get('product_code')} text={native.get('warning_text')}"
                     + (f" 扩展维度={extra}" if extra else ""))
    return {"recorded": True, "skipped": False, "id": row.id}


def clear_alarms(db, match: dict, match_fields=None,
                 clear_source: str = "external") -> dict:
    """按唯一键消除在途报警。返回 {"matched": bool, "cleared": int}。

    match_fields 指定用哪几个字段匹配 (默认四要素)。只对 match 里**有值**的字段加条件,
    匹配所有 active 记录并标记 cleared。一条都没匹配到 → matched=False (调用方回 40007)。
    """
    keys = _resolve_match_keys(match_fields)
    native, extra = _split_fields(match)
    q = db.query(ExternalActiveAlarm).filter(ExternalActiveAlarm.status == "active")
    applied = 0
    for k in keys:
        v = _match_value(native, extra, k)
        if v is None or v == "":
            continue
        q = q.filter(_match_col(k) == v)
        applied += 1

    # 一个匹配字段都没有 → 视为无效消除 (避免空条件误清全表)
    if applied == 0:
        return {"matched": False, "cleared": 0}

    rows = q.all()
    if not rows:
        return {"matched": False, "cleared": 0}

    now = datetime.now()
    for r in rows:
        r.status = "cleared"
        r.cleared_at = now
        r.clear_source = clear_source
    db.flush()
    return {"matched": True, "cleared": len(rows)}


def list_active_alarms(db, channel_id: Optional[int] = None, limit: int = 100) -> list:
    """取未消除报警 (供监控页持续横幅)。channel_id 给定则只取该工位。"""
    q = db.query(ExternalActiveAlarm).filter(ExternalActiveAlarm.status == "active")
    if channel_id is not None:
        q = q.filter(ExternalActiveAlarm.channel_id == channel_id)
    rows = q.order_by(ExternalActiveAlarm.id.desc()).limit(max(1, min(int(limit or 100), 500))).all()
    return [serialize_alarm(r) for r in rows]


def serialize_alarm(r: ExternalActiveAlarm) -> dict:
    return {
        "id": r.id,
        "task_no": r.task_no,
        "product_code": r.product_code,
        "step_code": r.step_code,
        "operator": r.operator,
        "warning_text": r.warning_text,
        "channel_id": r.channel_id,
        "event_type": r.event_type,
        "status": r.status,
        # 扩展维度回显 (监控横幅/界面可展示自定义维度)
        "extra": (r.extra_data or {}).get(EXTRA_NS) or {},
        "raised_at": r.raised_at.isoformat() if r.raised_at else None,
        "cleared_at": r.cleared_at.isoformat() if r.cleared_at else None,
    }


def find_recent_active_alarm(db, fields: dict, dedup_sec: int, match_fields=None):
    """公共封装: 给原始字段 dict, 判断同唯一键报警是否在去重窗口内已存在 (在途)。

    供出站网关在推送前做"同任务同标签 N 秒去重", 避免重复推送给外部 (川南 v4 第 4 条)。
    match_fields 指定唯一键用哪几个字段 (默认四要素), 与消除口径保持一致。
    返回命中的记录或 None。dedup_sec<=0 直接 None (不去重)。
    """
    if not dedup_sec or dedup_sec <= 0:
        return None
    native, extra = _split_fields(fields)
    return _find_recent_active(db, native, extra, dedup_sec, match_fields=match_fields)


# ============================================================
# 内部工具
# ============================================================
def _find_recent_active(db, native: dict, extra: dict, dedup_sec: int,
                        match_fields=None):
    """找同唯一键、在去重窗口内的 active 报警 (有则说明刚报过, 跳过重复登记)。

    match_fields 指定唯一键字段 (默认四要素), 原生列走列等值、扩展维度走 json_extract;
    与 clear_alarms 共用同一套配置与匹配逻辑, 保证去重/消除口径一致。
    """
    keys = _resolve_match_keys(match_fields)
    q = db.query(ExternalActiveAlarm).filter(ExternalActiveAlarm.status == "active")
    for k in keys:
        v = _match_value(native, extra, k)
        col = _match_col(k)
        if v is None or v == "":
            q = q.filter(col.is_(None))
        else:
            q = q.filter(col == v)
    row = q.order_by(ExternalActiveAlarm.id.desc()).first()
    if row is None or row.raised_at is None:
        return None
    try:
        raised = row.raised_at
        if raised.tzinfo is not None:
            raised = raised.replace(tzinfo=None)
        if datetime.now() - raised <= timedelta(seconds=dedup_sec):
            return row
    except Exception as e:
        debug_center.dbg("backend.mes", "报警去重时间比较异常", str(e))
    return None


def _norm(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s != "" else None
