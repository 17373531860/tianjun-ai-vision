"""
全局动作注册表 (RFC 14 维度三) — RFC 13 plc/rule_actions.py 上移而来。

PLC 规则引擎与统一触发中心共用这一套动作面; 插件注册一次两边可用。
全部动作复用主程序既有链路:

- bind_sn         → ScannerService.simulate_scan (与 USB 键盘枪同一生产级入口,
                    去重/防呆/自动建工件/绑定时机/MES 推送全继承)
- switch_project  → project_match.resolve_project_id_by_spec + activate_project_core
                    (与 MES 入站开工切项目完全同口径: 精确→通配符→同名子串)
- start_detection / stop_detection → channel_manager (对照 mes_inbound
                    _auto_start_detection 的复活路径语义)
- write_points    → PLC 引擎写队列 (值支持 常量/模板/value_map; 非 PLC 触发源
                    通过 action.connection 指定目标 PLC 连接)
- alarm           → alarm_router.trigger_alarm (归入共享灯柱四类优先级, 不变量 16)
- set_var         → 引擎上下文变量 (供后续动作/写回模板引用)
- manual_settle   → per_item 手动结算 (等价界面手动结算: 只代替时机不代替结果)
- clear_reset     → end_session + reset_stats (等价监控页「清零」)
- trigger_event   → VSM._trigger_event 事件中心 (报警/语音/计数/插件 hook 全联动)
- ack_alarm       → external_alarm.clear_all_active_alarms (等价 USB 确认按钮语义)
- resume_scanner  → ScannerService.resume_scanning_manual (v3.50 人工恢复扫码,
                    resume_on='ok_only' 下 NG 灭灯的脚踏板/PLC 出口)

动作签名: fn(engine, rule, action, ctx)。engine 只要求鸭子接口:
  .name / .vars / .options / ._log(dir, detail) / ._trigger_alarm(event)
  (write_points 额外要求 .enqueue_write, 非 PLC 引擎走 connection 参数解析)
动作在各自 manager 的动作线程执行 (可以慢), 单个动作异常不阻断后续动作。
"""
import logging
import re
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

_TMPL_RE = re.compile(r"\{\{\s*([\w\.\u4e00-\u9fff]+)\s*\}\}")

ACTION_REGISTRY: Dict[str, Callable] = {}


def register_trigger_action(name: str, fn: Callable = None):
    """注册动作。可作装饰器用; 插件也走这里 (覆盖同名即定制)。

    PLC 侧的 register_plc_action 是本函数的兼容别名 (RFC 13 既有插件零改动)。
    """
    if fn is not None:
        ACTION_REGISTRY[name] = fn
        return fn

    def _wrap(f):
        ACTION_REGISTRY[name] = f
        return f
    return _wrap


# ================= 模板与取值 (PLC write_dispatcher 亦复用) =================

def render_template(tmpl: str, ctx: dict) -> str:
    """轻量 {{key}} 模板 (支持点路径), 未知键渲染为空串。不引入 Jinja2。"""
    def _sub(m):
        cur: Any = ctx
        for part in m.group(1).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return ""
        return "" if cur is None else str(cur)
    return _TMPL_RE.sub(_sub, str(tmpl))


def ctx_get(ctx: dict, path: str) -> Any:
    """按点路径取上下文值 ("cycle.result" / "workpiece.serial_no")。"""
    cur: Any = ctx
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def resolve_write_value(write_cfg: dict, ctx: dict) -> Any:
    """写项取值三来源 (优先级): value_map(source 字段映射) > 模板字符串 > 常量。

    value_map: {"source": "result", "value_map": {"OK": 1, "NG": 2}, "default": 9}
    模板:      {"value": "{{workpiece.serial_no}}"}
    常量:      {"value": 1}
    """
    vmap = write_cfg.get("value_map")
    if isinstance(vmap, dict) and vmap:
        source_key = write_cfg.get("source") or "result"
        src = ctx_get(ctx, source_key)
        for k, v in vmap.items():
            if src == k or str(src) == str(k):
                return v
        return write_cfg.get("default", 0)
    value = write_cfg.get("value")
    if isinstance(value, str) and "{{" in value:
        return render_template(value, ctx)
    return value


def _resolve_channel(action: dict, rule: dict, engine) -> int:
    for holder in (action, rule):
        if holder.get("channel") is not None:
            return int(holder["channel"])
    return int((engine.options or {}).get("default_channel") or 0)


def _get_vsm(engine, ch: int):
    """取工位 VideoSourceManager, 不存在则记日志返回 None。"""
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.channels.get(ch)
    if not mgr:
        engine._log("error", f"工位 {ch} 不存在")
    return mgr


# ================= 内建动作 (RFC 13 原有六件套) =================

@register_trigger_action("bind_sn")
def _act_bind_sn(engine, rule: dict, action: dict, ctx: dict):
    """读到的产品号 → 扫码链路 (PLC/触发源 = 一把虚拟扫码枪)。"""
    barcode = render_template(action.get("template") or "", ctx).strip()
    ch = _resolve_channel(action, rule, engine)
    if not barcode:
        on_empty = (action.get("on_empty") or "alarm").lower()
        engine._log("error", f"bind_sn 产品号为空 (工位 {ch}, 策略 {on_empty})")
        if on_empty == "alarm":
            engine._trigger_alarm(action.get("empty_event") or "event2")
        return
    from backend.services.scanner import get_scanner_service
    result = get_scanner_service().simulate_scan(
        barcode, device_id=action.get("scanner_device_id"), channel_id=ch)
    engine._log("event", f"bind_sn → 工位{ch} 编号 {barcode} "
                         f"(scanner={result.get('device_name')})")


@register_trigger_action("switch_project")
def _act_switch_project(engine, rule: dict, action: dict, ctx: dict):
    """点位/上下文值 → 检测项目 (与 MES 入站开工切项目同口径)。"""
    source_key = action.get("source")
    raw = ctx.get(source_key) if source_key else None
    if raw is None and action.get("template"):
        raw = render_template(action["template"], ctx)
    spec = str(raw if raw is not None else "").strip()

    from backend.db.database import SessionLocal
    from backend.services.project_match import resolve_project_id_by_spec
    db = SessionLocal()
    try:
        project_id, hit_by = resolve_project_id_by_spec(
            db, spec, action.get("mapping") or {},
            match_by_name=bool(action.get("match_by_name", False)),
            strict_boundary=bool(action.get("strict_boundary", False)))
        if project_id is None:
            unknown = (action.get("unknown") or "alarm").lower()
            engine._log("error", f"switch_project 未知值 {spec!r} (策略 {unknown})")
            if unknown == "alarm":
                engine._trigger_alarm(action.get("unknown_event") or "event2")
                return
            if unknown == "default" and action.get("default_project_id"):
                project_id, hit_by = int(action["default_project_id"]), "默认项目"
            else:
                return

        from backend.models.models import Project
        active = db.query(Project).filter(Project.is_active == True).first()  # noqa: E712
        if active and active.id == int(project_id):
            engine._log("event", f"switch_project {spec!r} → 项目#{project_id} 已激活, 跳过")
            return
        from backend.api.projects import activate_project_core
        activate_project_core(db, int(project_id))
        engine._log("event", f"switch_project {spec!r} → 项目#{project_id} (经{hit_by})")
    finally:
        db.close()


@register_trigger_action("start_detection")
def _act_start_detection(engine, rule: dict, action: dict, ctx: dict):
    ch = _resolve_channel(action, rule, engine)
    mgr = _get_vsm(engine, ch)
    if not mgr:
        return
    if mgr.is_detecting:
        return
    if not mgr.is_running and not getattr(mgr, "source_type", None):
        engine._log("error", f"start_detection 工位 {ch} 未配置视频源")
        return
    mgr.start_detection()
    engine._log("event", f"start_detection 工位 {ch} 已拉起检测")


@register_trigger_action("stop_detection")
def _act_stop_detection(engine, rule: dict, action: dict, ctx: dict):
    ch = _resolve_channel(action, rule, engine)
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.channels.get(ch)
    if not mgr or not mgr.is_detecting:
        return
    mgr.stop_detection()
    engine._log("event", f"stop_detection 工位 {ch} 已停止检测")


@register_trigger_action("write_points")
def _act_write_points(engine, rule: dict, action: dict, ctx: dict):
    """写 PLC 点位。PLC 引擎直接入自己写队列; 其他触发源经 action.connection
    指定目标 PLC 连接 (id 或名称), 缺省取第一条在跑连接。"""
    target = engine
    if not hasattr(engine, "enqueue_write"):
        target = _resolve_plc_engine(engine, action)
        if target is None:
            return
    for w in (action.get("writes") or []):
        target.enqueue_write(
            w.get("point"), resolve_write_value(w, ctx),
            reset_after_ms=w.get("reset_after_ms"),
            reset_value=w.get("reset_value"))


def _resolve_plc_engine(engine, action: dict):
    """按 action.connection (id/名称) 解析目标 PLC 引擎。"""
    from backend.services.plc.manager import get_plc_manager
    mgr = get_plc_manager()
    ref = action.get("connection")
    with mgr._lock:
        engines = list(mgr._engines.values())
    if ref is None:
        if len(engines) == 1:
            return engines[0]
        engine._log("error", f"write_points 需指定 connection (当前在跑 {len(engines)} 条 PLC 连接)")
        return None
    for e in engines:
        if str(e.conn_id) == str(ref) or e.name == ref:
            return e
    engine._log("error", f"write_points 目标 PLC 连接 {ref!r} 不在运行中")
    return None


@register_trigger_action("alarm")
def _act_alarm(engine, rule: dict, action: dict, ctx: dict):
    ch = _resolve_channel(action, rule, engine)
    from backend.api.alarm import alarm_router
    alarm_router.trigger_alarm(action.get("event") or "event2", channel_id=ch)
    engine._log("event", f"alarm → 工位{ch} {action.get('event') or 'event2'}")


@register_trigger_action("set_var")
def _act_set_var(engine, rule: dict, action: dict, ctx: dict):
    name = action.get("name")
    if name:
        engine.vars[name] = render_template(str(action.get("value") or ""), ctx)


# ================= 内建动作 (RFC 14 新增四件套) =================

@register_trigger_action("manual_settle")
def _act_manual_settle(engine, rule: dict, action: dict, ctx: dict):
    """手动触发结算 (= 界面手动结算按钮; 虚拟按钮/脚踏板的主诉求)。

    与 /detection/per-item-control?action=settle 同语义: 只代替"时机判定",
    OK/NG 由真实覆盖状态判, 不伪造生产记录。仅 per_item 模式支持手动结算。
    """
    ch = _resolve_channel(action, rule, engine)
    mgr = _get_vsm(engine, ch)
    if not mgr:
        return
    if not getattr(mgr, "_per_item_config", None):
        engine._log("error", f"manual_settle 工位 {ch} 未启用 per_item 模式 "
                             "(手动结算仅 per_item 支持, 其他模式请用 trigger_event/clear_reset)")
        return
    ret = mgr.per_item_manual_settle()
    if ret.get("ok"):
        engine._log("event", f"manual_settle → 工位{ch} 已结算")
    else:
        engine._log("error", f"manual_settle 工位{ch} 未结算: {ret.get('msg', '')}")


@register_trigger_action("clear_reset")
def _act_clear_reset(engine, rule: dict, action: dict, ctx: dict):
    """清零重置 (= 监控页「清零」: 结束会话 + 重置计数器/步骤统计)。"""
    ch = _resolve_channel(action, rule, engine)
    mgr = _get_vsm(engine, ch)
    if not mgr:
        return
    mgr.end_session()
    mgr.reset_stats()
    engine._log("event", f"clear_reset → 工位{ch} 统计已重置")


@register_trigger_action("trigger_event")
def _act_trigger_event(engine, rule: dict, action: dict, ctx: dict):
    """进 _trigger_event 事件中心 (报警联动/语音/计数/Toast/插件 hook 全继承)。

    event: 项目 events_config 里的事件 id (如 "event1"/"event3")。
    需要工位有激活项目 (无 project_config 时事件中心拒绝, 记日志不报错)。
    """
    ch = _resolve_channel(action, rule, engine)
    mgr = _get_vsm(engine, ch)
    if not mgr:
        return
    event_id = action.get("event") or "event2"
    reason = render_template(
        str(action.get("reason") or f"触发源 {engine.name}"), ctx)
    ok = mgr._trigger_event(event_id, reason)
    engine._log("event" if ok else "error",
                f"trigger_event {event_id} → 工位{ch} "
                f"{'已触发' if ok else '被抑制/无激活项目'} ({reason})")


@register_trigger_action("resume_scanner")
def _act_resume_scanner(engine, rule: dict, action: dict, ctx: dict):
    """v3.50 人工恢复扫码 (= 监控页"恢复扫码"按钮 / POST /scanner/resume)。

    resume_on='ok_only' 的扫码器 NG 后保持灭灯, 本动作是脚踏板/PLC/串口等
    触发源的人工恢复出口。对指定工位所有等待恢复的 text_lon 扫码器解除灭灯锁。
    """
    ch = _resolve_channel(action, rule, engine)
    try:
        from backend.services.scanner import get_scanner_service
        resumed = get_scanner_service().resume_scanning_manual(ch)
    except Exception as e:
        engine._log("error", f"resume_scanner 工位{ch} 失败: {e}")
        return
    if resumed:
        engine._log("event", f"resume_scanner → 工位{ch} 已恢复 {resumed}")
    else:
        engine._log("event", f"resume_scanner → 工位{ch} 无等待恢复的扫码器")


@register_trigger_action("ack_alarm")
def _act_ack_alarm(engine, rule: dict, action: dict, ctx: dict):
    """消除在途报警 (= USB 确认按钮/横幅手动消除语义, 复用 v3.39 运维出口)。

    channel 给定则只清该工位; action.all_channels=true 清全部。
    """
    ch = None
    if not action.get("all_channels"):
        ch = _resolve_channel(action, rule, engine)
    from backend.db.database import SessionLocal
    from backend.services.external_alarm import clear_all_active_alarms
    db = SessionLocal()
    try:
        res = clear_all_active_alarms(db, channel_id=ch, clear_source="trigger_hub")
        db.commit()
        engine._log("event", f"ack_alarm → 消除在途报警 {res.get('cleared', 0)} 条"
                             f"{f' (工位{ch})' if ch is not None else ' (全部工位)'}")
    finally:
        db.close()


# ================= 执行器 (各 manager 动作线程调用) =================

def execute_actions(engine, rule: dict, snapshot: dict):
    """顺序执行规则的动作列表。单个动作失败不阻断后续 (错误隔离)。

    模板上下文 = 点位/触发快照 + 引擎变量 (vars 里同名键让位给快照)。
    """
    ctx = {**engine.vars, **snapshot}
    for action in (rule.get("actions") or []):
        name = action.get("do")
        fn = ACTION_REGISTRY.get(name)
        if fn is None:
            engine._log("error", f"未知动作: {name}")
            continue
        try:
            fn(engine, rule, action, ctx)
        except Exception as e:
            engine._log("error", f"动作 {name} 执行失败: {e}")
            logger.exception("[Trigger] %s 动作 %s 执行失败", engine.name, name)
