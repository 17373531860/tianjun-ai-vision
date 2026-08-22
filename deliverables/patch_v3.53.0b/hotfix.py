"""v3.53.0b 捷昌现场累积热补丁加载器。

保留 v3.53.0a 人工合格放行，并追加扫码拒绝复灯、工单范围并集与
PostgreSQL cycle_end aware datetime 修复。只通过运行时重绑，不替换 CORE pyd。
"""
from __future__ import annotations

import functools
import threading
import time
from datetime import datetime as _RealDateTime
from types import MethodType
from typing import Literal, Optional

from fastapi import Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.manual_pass_v3530a import (
    PATCH_VERSION,
    _consume_manual_pass_request,
    force_pass_counting_cycle,
    inject_manual_pass_tracking,
    queue_manual_pass_scope,
    request_manual_pass,
)

_VSM_METHODS_PATCHED = False
_TICK_PATCHED = False
_TICK_PATCH_TARGET = None
_SETTLE_PATCHED = False
_SCANNER_PATCHED = False
_WORK_ORDER_PATCHED = False
_PG_DATETIME_PATCHED = False
_APPLY_COMPLETE = False

_SCANNER_R1_PATCH_ID = "PATCH_JC353_SCANNER_E_R1"
_SCANNER_R1_WATCH_SECONDS = 2.0
_SCANNER_R1_POLL_SECONDS = 0.01


def _supported_runtime() -> bool:
    """运行时二次守门；安装脚本还会校验旧 a 文件与前端 manifest。"""
    try:
        from backend._version import get_main_version

        version = get_main_version()
    except Exception as exc:
        print(f"[Hotfix {PATCH_VERSION}] 读取主程序版本失败，拒绝加载: {exc}", flush=True)
        return False
    if version != "3.53.0":
        print(
            f"[Hotfix {PATCH_VERSION}] 版本不匹配，要求 3.53.0，实际 {version}；补丁未加载",
            flush=True,
        )
        return False
    return True


class ManualPassRequest(BaseModel):
    scope: Literal["channel", "group"] = Field(
        "channel", description="本工位放行或 synchronized_all_ok 整组放行"
    )


def _patch_vsm_methods():
    global _VSM_METHODS_PATCHED
    if _VSM_METHODS_PATCHED:
        return
    from backend.api.source import VideoSourceManager

    VideoSourceManager.request_manual_pass = request_manual_pass
    VideoSourceManager._consume_manual_pass_request = _consume_manual_pass_request
    VideoSourceManager.force_pass_counting_cycle = force_pass_counting_cycle
    _VSM_METHODS_PATCHED = True


def _patch_tick_consume():
    global _TICK_PATCHED, _TICK_PATCH_TARGET
    if _TICK_PATCHED:
        return _TICK_PATCH_TARGET
    from backend.api.source import VideoSourceManager

    # 仓库源码的推理循环每帧经过 _tick_combo_guard；部分客户 v3.53.0
    # 编译版 VideoSourceManager 没暴露这个后拆分的方法，但 tracking 主路径
    # 一定经过 _update_tracking_stats。两处都在推理线程，故可安全兼容，且
    # 仍保持 HTTP/Trigger 线程只排队、不直接结算。
    target_name = next(
        (
            name
            for name in ("_tick_combo_guard", "_update_tracking_stats")
            if callable(getattr(VideoSourceManager, name, None))
        ),
        None,
    )
    if target_name is None:
        print(
            f"[Hotfix {PATCH_VERSION}] 未找到人工放行推理线程消费入口，"
            "已跳过消费挂点（主程序继续）",
            flush=True,
        )
        return None

    original = getattr(VideoSourceManager, target_name)
    if getattr(original, "_v3530a_manual_pass_wrapper", False):
        _TICK_PATCHED = True
        _TICK_PATCH_TARGET = target_name
        return target_name

    @functools.wraps(original)
    def _wrapped_inference_tick(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        try:
            self._consume_manual_pass_request()
        except Exception as exc:
            print(
                f"[Hotfix {PATCH_VERSION}] ch{getattr(self, 'channel_id', '?')} "
                f"人工合格放行消费失败: {exc}",
                flush=True,
            )
        return result

    _wrapped_inference_tick._v3530a_manual_pass_wrapper = True
    _wrapped_inference_tick._v3530a_manual_pass_target = target_name
    setattr(VideoSourceManager, target_name, _wrapped_inference_tick)
    _TICK_PATCHED = True
    _TICK_PATCH_TARGET = target_name
    print(
        f"[Hotfix {PATCH_VERSION}] 人工放行推理线程消费入口={target_name}",
        flush=True,
    )
    return target_name


def _patch_settle_hint():
    global _SETTLE_PATCHED
    if _SETTLE_PATCHED:
        return
    from backend.api.source import VideoSourceManager

    original_impl = VideoSourceManager._settle_counting_cycle_impl
    original_box = VideoSourceManager._settle_box

    if not getattr(original_impl, "_v3530a_manual_pass_wrapper", False):
        def _wrapped_impl(self, expected_items, check_order=False, expected_order=None):
            hint = getattr(self, "_manual_pass_settle_hint", None)
            if not getattr(self, "_manual_pass_ok_hint", False) or not hint:
                return original_impl(self, expected_items, check_order, expected_order)

            had_instance_event = "_trigger_event" in getattr(self, "__dict__", {})
            old_instance_event = getattr(self, "__dict__", {}).get("_trigger_event")
            original_event = self._trigger_event
            old_scan_hint = bool(getattr(self, "_scan_pair_settle_hint", False))
            old_was_complete = bool(getattr(self, "_tracking_was_complete", False))

            def _manual_event(_self, _event_id, _reason):
                return original_event(1, hint.get("reason") or "人工合格放行")

            # 让既有 scan-pair OK 分支在 missing/extra NG 分支之前命中；只在本次
            # manual hint 调用栈内生效，旧 force_settle 路径不受影响。
            self._scan_pair_settle_hint = True
            self._tracking_was_complete = True
            object.__setattr__(self, "_trigger_event", MethodType(_manual_event, self))
            try:
                return original_impl(self, expected_items, check_order, expected_order)
            finally:
                self._scan_pair_settle_hint = old_scan_hint
                self._tracking_was_complete = old_was_complete
                if had_instance_event:
                    object.__setattr__(self, "_trigger_event", old_instance_event)
                else:
                    try:
                        object.__delattr__(self, "_trigger_event")
                    except AttributeError:
                        pass

        _wrapped_impl._v3530a_manual_pass_wrapper = True
        VideoSourceManager._settle_counting_cycle_impl = _wrapped_impl

    if not getattr(original_box, "_v3530a_manual_pass_wrapper", False):
        def _wrapped_box(self, box_display_id, expected_items, **kwargs):
            hint = getattr(self, "_manual_pass_settle_hint", None)
            if not getattr(self, "_manual_pass_ok_hint", False) or not hint:
                return original_box(self, box_display_id, expected_items, **kwargs)
            state = (getattr(self, "_box_objects", {}) or {}).get(box_display_id)
            if state is not None:
                # 只改将被 pop 的箱内 sticky 判定，不增补 item/StepRecord。
                state["was_complete"] = True
            kwargs["via_scan_pair"] = True
            kwargs["scan_pair_force_ng"] = False
            return original_box(self, box_display_id, expected_items, **kwargs)

        _wrapped_box._v3530a_manual_pass_wrapper = True
        VideoSourceManager._settle_box = _wrapped_box

    _SETTLE_PATCHED = True


def _add_manual_pass_route(app):
    path = "/api/v1/source/detection/manual-pass"
    if any(getattr(route, "path", None) == path for route in app.routes):
        return

    from backend.core.auth_deps import CurrentUser, get_current_user, require_perm

    def _manual_pass_endpoint(
        body: Optional[ManualPassRequest] = Body(default=None),
        channel: int = Query(0, description="0-based 工位编号"),
        user: CurrentUser = Depends(get_current_user),
    ):
        scope = body.scope if body is not None else "channel"
        username = str(getattr(user, "username", "") or "")
        if username.startswith("__"):
            username = ""
        result = queue_manual_pass_scope(
            channel, scope=scope, source="api", user=username
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("msg", "操作失败"))
        return result

    app.add_api_route(
        path,
        _manual_pass_endpoint,
        methods=["POST"],
        summary="挂账周期人工合格放行（v3.53.0b 累积现场热补丁）",
        description=(
            "仅适用于 tracking + 齐件即结算 + ROI离开/容器模式；HTTP 线程只排队，"
            "由推理线程消费并沿用现有 OK 结算链路。"
        ),
        dependencies=[Depends(require_perm("monitor.detection.control"))],
        tags=["source"],
    )


def _wrap_get_detection_results(app):
    from backend.api import source_routes
    from backend.api.channel_manager import channel_manager
    from fastapi.routing import request_response

    current = source_routes.get_detection_results
    if getattr(current, "_v3530a_manual_pass_wrapper", False):
        wrapped = current
    else:
        @functools.wraps(current)
        def wrapped(*args, **kwargs):
            result = current(*args, **kwargs)
            channel = int(kwargs.get("channel", args[0] if args else 0) or 0)
            mgr = channel_manager.channels.get(channel)
            return inject_manual_pass_tracking(result, mgr) if mgr is not None else result

        wrapped._v3530a_manual_pass_wrapper = True
        source_routes.get_detection_results = wrapped

    def walk_routes(routes):
        for route in routes:
            yield route
            nested = getattr(route, "routes", None)
            if not nested:
                nested = getattr(getattr(route, "original_router", None), "routes", None)
            if nested:
                yield from walk_routes(nested)

    patched_routes = 0
    for route in walk_routes(app.routes):
        endpoint = getattr(route, "endpoint", None)
        route_methods = getattr(route, "methods", set()) or set()
        same_source_endpoint = (
            getattr(endpoint, "__module__", None) == source_routes.__name__
            and getattr(endpoint, "__name__", None) == "get_detection_results"
        )
        if "GET" in route_methods and (endpoint is current or same_source_endpoint):
            route.endpoint = wrapped
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = wrapped
            # APIRoute 在注册时已把 dependant 编译进 ASGI handler；仅改 endpoint /
            # dependant.call 对已经挂载的真实 HTTP 路由不生效，必须重建这一层缓存。
            route.app = request_response(route.get_route_handler())
            patched_routes += 1
    print(
        f"[Hotfix {PATCH_VERSION}] results 路由包装数={patched_routes}",
        flush=True,
    )
    return patched_routes


def _register_trigger_action():
    from backend.services.triggers import actions as trigger_actions

    def _manual_pass_action(engine, rule, action, ctx):
        channel = trigger_actions._resolve_channel(action, rule, engine)
        scope = str(action.get("scope") or "channel")
        result = queue_manual_pass_scope(
            channel,
            scope=scope,
            source=str(action.get("source") or "trigger"),
            user=str((ctx or {}).get("user") or ""),
        )
        direction = "event" if result.get("ok") else "error"
        engine._log(
            direction,
            f"manual_pass_ok 工位{channel} scope={scope}: {result.get('msg')}",
        )
        return result

    trigger_actions.register_trigger_action("manual_pass_ok", _manual_pass_action)


def _register_pre_manual_pass_hook_whitelist():
    from backend.plugin_system.hook_dispatch import RETURNABLE_HOOK_FIELDS

    RETURNABLE_HOOK_FIELDS.setdefault("pre_manual_pass", set()).add("veto")


def _start_scanner_relock_guard(
    conn,
    channel_id: int,
    generation_snapshot,
    serial_snapshot,
    scan_time_snapshot,
):
    """只清当前扫码回调尾部造成的 stale E 模式重锁。"""
    token = object()
    conn._jc353_e_rearm_guard_token = token

    def _watch():
        deadline = time.monotonic() + _SCANNER_R1_WATCH_SECONDS
        while time.monotonic() < deadline:
            if getattr(conn, "_jc353_e_rearm_guard_token", None) is not token:
                return
            if (getattr(conn, "scan_mode", "") or "") != "E":
                return
            if getattr(conn, "_resume_blocked", False):
                return
            if generation_snapshot is not None:
                if getattr(conn, "_jc353_e_scan_generation", None) != generation_snapshot:
                    return
            elif (
                getattr(conn, "last_scan", None) != serial_snapshot
                or getattr(conn, "last_scan_time", None) != scan_time_snapshot
            ):
                return

            if getattr(conn, "_wait_cycle_resume", False):
                conn._wait_cycle_resume = False
                conn._lon_sent = False
                conn._next_lon_after = 0.0
                conn._jc353_e_rearm_guard_token = None
                print(
                    f"[Hotfix {PATCH_VERSION}/{_SCANNER_R1_PATCH_ID}] "
                    f"修复 E 模式快速 OK 后 stale relock: "
                    f"scanner={getattr(conn, 'name', '?')} "
                    f"channel={channel_id} serial={serial_snapshot!r}",
                    flush=True,
                )
                return
            time.sleep(_SCANNER_R1_POLL_SECONDS)

        if getattr(conn, "_jc353_e_rearm_guard_token", None) is token:
            conn._jc353_e_rearm_guard_token = None

    threading.Thread(
        target=_watch,
        daemon=True,
        name=f"jc353-e-rearm-{getattr(conn, 'device_id', 'unknown')}",
    ).start()


def _patch_scanner_rearm():
    """合并任务 A 三拒绝复灯与既有 R1 快速 OK stale relock 守门。"""
    global _SCANNER_PATCHED
    if _SCANNER_PATCHED:
        return

    from backend.services.scanner import ScannerService

    original = ScannerService._on_data_received
    original_resume = ScannerService.resume_after_cycle
    if (
        getattr(original, "_v3530b_rearm_wrapper", False)
        and getattr(original_resume, "_v3530b_fast_ok_wrapper", False)
    ):
        _SCANNER_PATCHED = True
        return
    required = (
        "_loff_on_code_received",
        "_rearm_after_full_reject",
        "_resolve_bound_channels",
    )
    if any(not callable(getattr(ScannerService, name, None)) for name in required):
        raise RuntimeError("ScannerService 结构标记不匹配，拒绝应用累积扫码包装")
    if getattr(original, "_v3530b_rearm_wrapper", False) != getattr(
        original_resume, "_v3530b_fast_ok_wrapper", False
    ):
        raise RuntimeError("ScannerService 累积扫码包装处于半应用状态，拒绝重复叠加")

    @functools.wraps(original)
    def _wrapped_on_data_received(self, conn, raw_data):
        mode = str(getattr(conn, "scan_mode", None) or "continuous")
        wait_mode = mode in ("once_per_cycle", "D", "E")
        if mode == "E":
            conn._jc353_e_scan_generation = (
                int(getattr(conn, "_jc353_e_scan_generation", 0) or 0) + 1
            )
        now = time.time()
        dedup_hit = bool(
            wait_mode
            and raw_data == getattr(conn, "last_scan", None)
            and (now - float(getattr(conn, "last_scan_time", 0) or 0))
            < float(getattr(conn, "dedup_interval_sec", 0) or 0)
        )
        dispatch_before = getattr(conn, "_last_dispatch", None)
        testing = False
        testing_lock = getattr(self, "_testing_ips_lock", None)
        if testing_lock is not None:
            with testing_lock:
                testing = getattr(conn, "ip", None) in getattr(self, "_testing_ips", set())

        result = original(self, conn, raw_data)
        if not wait_mode or testing:
            return result

        reason = None
        rearm_serial = raw_data
        if dedup_hit:
            reason = "物理去重命中"
        else:
            # 成功派发一定会替换 _last_dispatch；成功但无接收者的老逻辑已自行
            # 调 _rearm_after_full_reject 并把它清为 None。仅在状态未变时复验
            # 纯解析器，以区分“解析失败”和“成功但无人接收”，避免重复复灯。
            dispatch_after = getattr(conn, "_last_dispatch", None)
            if dispatch_after is dispatch_before:
                try:
                    parsed = self._parser.parse(raw_data, conn.parse_config)
                    if not getattr(parsed, "success", False):
                        reason = "条码解析失败"
                    elif bool(getattr(conn, "external_only", False)):
                        reason = "external_only 不进视觉"
                        rearm_serial = getattr(parsed, "serial_no", None) or raw_data
                except Exception as exc:
                    print(
                        f"[Hotfix {PATCH_VERSION}] 扫码解析复验失败，保持原行为: {exc}",
                        flush=True,
                    )
        if reason:
            self._rearm_after_full_reject(conn, rearm_serial, reason=reason)
        return result

    _wrapped_on_data_received._v3530b_rearm_wrapper = True
    _wrapped_on_data_received._tj_hotfix_generation_patch_id = _SCANNER_R1_PATCH_ID
    _wrapped_on_data_received._v3530b_original = original

    @functools.wraps(original_resume)
    def _wrapped_resume_after_cycle(
        self, channel_id: int, is_good: bool = None, manual: bool = False
    ):
        snapshots = []
        for conn in list(getattr(self, "_connections", {}).values()):
            if (getattr(conn, "scan_mode", "") or "") != "E":
                continue
            try:
                bound = self._resolve_bound_channels(conn)
            except Exception:
                bound = [getattr(conn, "channel_id", 0)]
            if channel_id not in bound:
                continue
            snapshots.append(
                (
                    conn,
                    getattr(conn, "_jc353_e_scan_generation", None),
                    getattr(conn, "last_scan", None),
                    getattr(conn, "last_scan_time", None),
                )
            )

        resumed = original_resume(
            self, channel_id=channel_id, is_good=is_good, manual=manual
        )
        if not resumed:
            return resumed

        resumed_names = set(resumed)
        for conn, generation, serial, scan_time in snapshots:
            if getattr(conn, "name", None) not in resumed_names:
                continue
            if getattr(conn, "_resume_blocked", False):
                continue
            if generation is not None:
                if getattr(conn, "_jc353_e_scan_generation", None) != generation:
                    continue
            elif (
                getattr(conn, "last_scan", None) != serial
                or getattr(conn, "last_scan_time", None) != scan_time
            ):
                continue
            _start_scanner_relock_guard(
                conn,
                channel_id,
                generation,
                serial,
                scan_time,
            )
        return resumed

    _wrapped_resume_after_cycle._v3530b_fast_ok_wrapper = True
    _wrapped_resume_after_cycle._tj_hotfix_patch_id = _SCANNER_R1_PATCH_ID
    _wrapped_resume_after_cycle._v3530b_original = original_resume

    ScannerService._on_data_received = _wrapped_on_data_received
    ScannerService.resume_after_cycle = _wrapped_resume_after_cycle
    _SCANNER_PATCHED = True
    print(
        f"[Hotfix {PATCH_VERSION}/{_SCANNER_R1_PATCH_ID}] "
        "ScannerService 累积扫码包装已应用（A early-rearm + R1 fast-OK guard）",
        flush=True,
    )


def _patch_work_order_scope():
    """项目筛选保留按工位/按集群工单，total 与分页共用同一 query。"""
    global _WORK_ORDER_PATCHED
    if _WORK_ORDER_PATCHED:
        return

    from sqlalchemy import desc, or_
    from backend.models.mes_models import WorkOrder
    from backend.services.work_order import WorkOrderService

    original = WorkOrderService.list_orders
    if getattr(original, "_v3530b_project_scope_union", False):
        _WORK_ORDER_PATCHED = True
        return

    @functools.wraps(original)
    def _list_orders(
        self,
        db,
        *,
        status=None,
        project_id=None,
        keyword=None,
        date_from=None,
        date_to=None,
        skip=0,
        limit=50,
    ):
        q = db.query(WorkOrder)
        if status:
            q = q.filter(WorkOrder.status == status)
        if project_id:
            q = q.filter(
                or_(
                    WorkOrder.project_id == project_id,
                    WorkOrder.binding_scope.in_(("channels", "cluster")),
                )
            )
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(
                or_(
                    WorkOrder.order_no.ilike(like),
                    WorkOrder.product_name.ilike(like),
                    WorkOrder.customer_name.ilike(like),
                )
            )
        if date_from:
            q = q.filter(WorkOrder.created_at >= date_from)
        if date_to:
            q = q.filter(WorkOrder.created_at <= date_to)
        total = q.count()
        items = q.order_by(desc(WorkOrder.created_at)).offset(skip).limit(limit).all()
        return items, total

    _list_orders._v3530b_project_scope_union = True
    _list_orders._v3530b_original = original
    WorkOrderService.list_orders = _list_orders
    _WORK_ORDER_PATCHED = True
    print(f"[Hotfix {PATCH_VERSION}] 工单项目范围并集包装已应用", flush=True)


class _AwareLocalDateTime(_RealDateTime):
    """datetime 兼容类：省略 tz 时也返回本地 aware 值。"""

    _v3530b_pg_aware = True

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return _RealDateTime.now(tz)
        return _RealDateTime.now().astimezone()

    @classmethod
    def fromtimestamp(cls, timestamp, tz=None):
        if tz is not None:
            return _RealDateTime.fromtimestamp(timestamp, tz)
        local_tz = _RealDateTime.now().astimezone().tzinfo
        return _RealDateTime.fromtimestamp(timestamp, local_tz)


def _patch_pg_aware_datetime():
    """PG 专用：修 cycle_end aware start_time - naive now 的 TypeError。"""
    global _PG_DATETIME_PATCHED
    if _PG_DATETIME_PATCHED:
        return True

    from backend.db.database import get_dialect

    dialect = str(get_dialect() or "").lower()
    if dialect != "postgresql":
        print(
            f"[Hotfix {PATCH_VERSION}] 数据库 dialect={dialect or 'unknown'}，"
            "不注入 PG datetime 兼容层",
            flush=True,
        )
        return False

    from backend.api import source_session_lifecycle_mixin as lifecycle

    current = getattr(lifecycle, "datetime", None)
    if getattr(current, "_v3530b_pg_aware", False):
        _PG_DATETIME_PATCHED = True
        return True
    if current is not _RealDateTime:
        raise RuntimeError("source_session_lifecycle_mixin.datetime 结构标记不匹配")
    lifecycle.datetime = _AwareLocalDateTime
    _PG_DATETIME_PATCHED = True
    print(
        f"[Hotfix {PATCH_VERSION}] PostgreSQL cycle_end aware datetime 兼容层已应用",
        flush=True,
    )
    return True


def _safe_apply(name, callback):
    try:
        callback()
        return True
    except Exception as exc:
        print(f"[Hotfix {PATCH_VERSION}] {name} 应用失败（已隔离）: {exc}", flush=True)
        return False


def apply(app=None):
    global _APPLY_COMPLETE
    if _APPLY_COMPLETE:
        print(f"[Hotfix {PATCH_VERSION}] 已应用，跳过重复加载", flush=True)
        return True
    if not _supported_runtime():
        return False

    results = [
        _safe_apply("人工放行 VSM 方法", _patch_vsm_methods),
        _safe_apply("人工放行推理消费", _patch_tick_consume),
        _safe_apply("人工放行结算提示", _patch_settle_hint),
        _safe_apply("扫码拒绝复灯", _patch_scanner_rearm),
        _safe_apply("工单范围并集", _patch_work_order_scope),
        _safe_apply("PostgreSQL cycle_end datetime", _patch_pg_aware_datetime),
    ]
    if app is not None:
        results.append(_safe_apply("人工放行 API", lambda: _add_manual_pass_route(app)))

        def _patch_results_route():
            if _wrap_get_detection_results(app) == 0:
                # v3.53.0 loader 可能早于 main.py 尾部直挂路由；启动时再试一次。
                app.router.add_event_handler(
                    "startup", lambda: _wrap_get_detection_results(app)
                )

        results.append(_safe_apply("人工放行 results 路由", _patch_results_route))
    results.append(_safe_apply("人工放行触发动作", _register_trigger_action))
    results.append(_safe_apply("人工放行插件白名单", _register_pre_manual_pass_hook_whitelist))

    _APPLY_COMPLETE = True
    ok_count = sum(1 for item in results if item)
    print(
        f"[Hotfix {PATCH_VERSION}] 累积补丁已加载（{ok_count}/{len(results)} 项成功）",
        flush=True,
    )
    return all(results)


# PATCHED_V3530A
# PATCHED_V3530A_R2_COMPAT_TRACKING_HOOK
# PATCHED_V3530B_CUMULATIVE
# PATCHED_V3530B_SCANNER_REARM
# PATCH_JC353_SCANNER_E_R1
# PATCHED_V3530B_WORK_ORDER_SCOPE
# PATCHED_V3530B_PG_AWARE_DATETIME
