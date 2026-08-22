"""v3.53.0a 现场热补丁：tracking 挂账周期人工合格放行。

本文件是独立 sidecar，不替换客户机上任何 CORE ``.pyd``。HTTP/触发动作线程
只排队；真正结算由推理线程在 ``_tick_combo_guard`` 之后消费。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

PATCH_VERSION = "v3.53.0b"


def _pipeline_config(mgr) -> Dict[str, Any]:
    project = getattr(mgr, "project_config", None) or {}
    return project.get("pipeline_config") or {}


def _manual_pass_gate(mgr) -> Tuple[bool, str]:
    project = getattr(mgr, "project_config", None) or {}
    pcfg = project.get("pipeline_config") or {}
    if project.get("logic_mode") != "tracking":
        return False, "仅跟踪清点模式支持人工合格放行"
    if pcfg.get("manual_pass_enabled", True) is not True:
        return False, "当前项目已禁用人工合格放行"
    if not bool(pcfg.get("tracking_settle_on_complete", False)):
        return False, "请先开启“全部合格立即结算”"
    if pcfg.get("tracking_cycle_strategy") not in ("roi_exit", "container"):
        return False, "仅 ROI离开或容器模式支持人工合格放行"
    if not bool(getattr(mgr, "is_detecting", False)):
        return False, "检测未运行，不能人工合格放行"
    if not bool(getattr(mgr, "_tracking_cycle_active", False)):
        return False, "当前没有挂账周期"
    if bool(getattr(mgr, "_tracking_was_complete", False)):
        return False, "已齐件，请等自动结算"
    if bool(getattr(mgr, "_force_settling_in_progress", False)):
        return False, "当前周期正在结算，请稍候"
    return True, ""


def request_manual_pass(
    self,
    scope: str = "channel",
    source: str = "api",
    user: str = "",
) -> Dict[str, Any]:
    """由非推理线程调用：严格守门后只写一次性请求旗标。"""
    ok, msg = _manual_pass_gate(self)
    if not ok:
        return {"ok": False, "msg": msg}

    lock = getattr(self, "_manual_pass_request_lock", None)
    if lock is None:
        lock = threading.Lock()
        self._manual_pass_request_lock = lock
    with lock:
        if getattr(self, "_manual_pass_request", None):
            return {"ok": False, "msg": "人工合格放行已在排队，请勿重复点击"}
        self._manual_pass_request = {
            "scope": scope if scope in ("channel", "group") else "channel",
            "source": str(source or "api"),
            "user": str(user or ""),
            "requested_at": time.time(),
        }
    return {
        "ok": True,
        "msg": "人工合格放行已排队",
        "channel": int(getattr(self, "channel_id", 0) or 0),
    }


def _consume_manual_pass_request(self) -> Optional[Dict[str, Any]]:
    """推理线程入口：原 tick 完成后取走旗标并执行结算。"""
    lock = getattr(self, "_manual_pass_request_lock", None)
    if lock is None:
        request = getattr(self, "_manual_pass_request", None)
        self._manual_pass_request = None
    else:
        with lock:
            request = getattr(self, "_manual_pass_request", None)
            self._manual_pass_request = None
    if not request:
        return None
    return self.force_pass_counting_cycle(request)


def _merged_counters(mgr) -> Dict[str, int]:
    merged = dict(getattr(mgr, "_tracking_class_counters", {}) or {})
    for name, count in (getattr(mgr, "_event_counters", {}) or {}).items():
        merged[name] = merged.get(name, 0) + int(count or 0)
    for name, count in (getattr(mgr, "_stack_counters", {}) or {}).items():
        merged[name] = max(merged.get(name, 0), int(count or 0))
    return merged


def _missing_items(mgr, expected_items: Dict[str, int]) -> List[str]:
    """保留真实账本差额；只用于审计说明，不伪造缺失 StepRecord。"""
    checklist = getattr(mgr, "_tracking_item_checklist", {}) or {}
    missing: List[str] = []
    if checklist.get("_container_mode") and isinstance(checklist.get("_boxes"), dict):
        for box_id, box in checklist["_boxes"].items():
            for name, info in (box.get("items") or {}).items():
                actual = int(info.get("counted", 0) or 0)
                expected = int(info.get("expected", 0) or 0)
                if expected > 0 and actual < expected:
                    missing.append(f"{box_id}/{name}:{actual}/{expected}")
        return missing

    if checklist:
        for name, info in checklist.items():
            if name.startswith("_") or not isinstance(info, dict):
                continue
            actual = int(info.get("counted", 0) or 0)
            expected = int(info.get("expected", 0) or 0)
            if expected > 0 and actual < expected:
                missing.append(f"{name}:{actual}/{expected}")
        if missing:
            return missing

    # 老现场对象若 checklist 尚未来得及刷新，按同一份 counters 口径兜底；
    # 仍只描述真实差额，不增补对象或 StepRecord。
    aggregate = _merged_counters(mgr)
    container_label = getattr(mgr, "_container_label", None)
    for name, expected in expected_items.items():
        if name == container_label:
            continue
        actual = int(aggregate.get(name, 0) or 0)
        expected_int = int(expected or 0)
        if actual < expected_int:
            missing.append(f"{name}:{actual}/{expected_int}")
    return missing


def force_pass_counting_cycle(self, request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """推理线程内把当前 incomplete 挂账周期按 OK 走既有结算链路。"""
    request = dict(request or {})
    ok, msg = _manual_pass_gate(self)
    if not ok:
        return {"ok": False, "msg": msg}

    settle_lock = getattr(self, "_settle_lock", None)
    if settle_lock is not None and not settle_lock.acquire(blocking=False):
        return {"ok": False, "msg": "当前周期正在结算，请稍候"}
    if bool(getattr(self, "_force_settling_in_progress", False)):
        if settle_lock is not None:
            settle_lock.release()
        return {"ok": False, "msg": "当前周期正在结算，请稍候"}

    # 第一次 gate 与拿到结算锁之间，自动结算可能刚好完成并重置账本。
    # 必须在锁内重检，避免对刚清空的工位补开新周期、重复累计 OK/产量。
    ok, msg = _manual_pass_gate(self)
    if not ok:
        if settle_lock is not None:
            settle_lock.release()
        return {"ok": False, "msg": msg}

    self._force_settling_in_progress = True
    try:
        now = time.time()
        if not self._soc_ensure_db_cycle(now):
            print(
                f"[Hotfix {PATCH_VERSION}] ch{getattr(self, 'channel_id', '?')} "
                "DB 周期守门未通过，人工合格放行保持挂账",
                flush=True,
            )
            return {"ok": False, "msg": "周期尚未绑定扫码，账本继续挂起"}

        pcfg = _pipeline_config(self)
        expected_items = dict(pcfg.get("counting_expected_items") or {})
        missing = _missing_items(self, expected_items)
        user = str(request.get("user") or "")
        source = str(request.get("source") or "api")
        scope = request.get("scope") if request.get("scope") in ("channel", "group") else "channel"
        reason = (
            f"人工合格放行 user={user} source={source} "
            f"missing=[{', '.join(missing)}]"
        )

        from backend.plugin_system.hook_dispatch import fire_plugin_hook

        hook_ctx = {
            "channel_id": int(getattr(self, "channel_id", 0) or 0),
            "cycle_id": getattr(self, "current_cycle_id", None),
            "scope": scope,
            "source": source,
            "user": user,
            "missing": list(missing),
            "class_counters": _merged_counters(self),
        }
        decision = fire_plugin_hook(
            "pre_manual_pass", "pre_cycle", "pre", hook_ctx
        ) or {}
        if decision.get("veto") is True:
            print(
                f"[Hotfix {PATCH_VERSION}] ch{getattr(self, 'channel_id', '?')} "
                "人工合格放行被插件 veto，账本保持挂起",
                flush=True,
            )
            return {"ok": False, "msg": "插件已取消人工合格放行"}

        exempt = getattr(self, "_settle_complete_exempt", None)
        if exempt is None:
            exempt = {}
            self._settle_complete_exempt = exempt
        for track_id, obj in (getattr(self, "_tracking_objects", {}) or {}).items():
            exempt[track_id] = {
                "ts": now,
                "bbox": dict(obj.get("bbox") or {}),
                "label": obj.get("class_name"),
            }

        self._manual_pass_ok_hint = True
        self._manual_pass_settle_hint = {
            "reason": reason,
            "missing": list(missing),
            "scope": scope,
        }
        print(
            f"[Hotfix {PATCH_VERSION}] ch{getattr(self, 'channel_id', '?')} {reason}",
            flush=True,
        )
        self._settle_counting_cycle(
            expected_items,
            bool(pcfg.get("tracking_check_order", False)),
            list(pcfg.get("tracking_expected_order") or []),
        )
        return {
            "ok": True,
            "msg": "人工合格放行已结算",
            "reason": reason,
            "missing": missing,
        }
    except Exception as exc:
        print(
            f"[Hotfix {PATCH_VERSION}] ch{getattr(self, 'channel_id', '?')} "
            f"人工合格放行失败: {exc}",
            flush=True,
        )
        return {"ok": False, "msg": f"人工合格放行失败: {exc}"}
    finally:
        self._manual_pass_ok_hint = False
        self._manual_pass_settle_hint = None
        self._force_settling_in_progress = False
        if settle_lock is not None:
            settle_lock.release()


def _group_info(channel_id: int) -> Optional[Dict[str, Any]]:
    try:
        from backend.services.channel_group_coordinator import get_coordinator

        coordinator = get_coordinator()
        with coordinator._lock:
            group_id = coordinator._channel_to_group.get(int(channel_id))
            group = coordinator._groups.get(group_id)
            return dict(group) if group else None
    except Exception as exc:
        print(f"[Hotfix {PATCH_VERSION}] 读取工位组失败: {exc}", flush=True)
        return None


def _normalise_channel_ids(values: Iterable[Any]) -> List[int]:
    result = []
    for value in values or []:
        try:
            channel_id = int(value)
        except (TypeError, ValueError):
            continue
        if channel_id >= 0 and channel_id not in result:
            result.append(channel_id)
    return result


def _scanner_broadcast_member_ids(channel_id: int) -> List[int]:
    """复用 ScannerService 的绑定解析与检测分母，禁止旁路解释配置。"""
    channel_id = int(channel_id)
    found = set()
    try:
        from backend.services.scanner import get_scanner_service

        service = get_scanner_service()
        connections = list((getattr(service, "_connections", {}) or {}).values())
    except Exception:
        return []

    for conn in connections:
        try:
            if getattr(conn, "device_type", "text_lon") != "text_lon":
                continue
            if (getattr(conn, "scan_mode", "") or "") not in ("once_per_cycle", "D", "E"):
                continue
            if service._effective_resume_on(conn) != "ok_only":
                continue
            bound = service._resolve_bound_channels(conn)
            if channel_id not in bound:
                continue
            found.update(service._detecting_channels(bound))
        except Exception:
            # 一把坏枪不能挡住其它有效广播面的人工放行入口。
            continue
    return sorted(_normalise_channel_ids(found))


def _manual_pass_group_member_ids(channel_id: int) -> List[int]:
    """ChannelGroup 与等灯扫码枪广播面的稳定并集。"""
    channel_id = int(channel_id)
    members = set(_scanner_broadcast_member_ids(channel_id))
    group = _group_info(channel_id)
    if group and group.get("settle_strategy") == "synchronized_all_ok":
        members.update(group.get("member_channel_ids", []))
    return sorted(_normalise_channel_ids(members))


def _member_skip_reason(manager) -> str:
    ok, reason = _manual_pass_gate(manager)
    return "" if ok else reason


def manual_pass_group_available(channel_id: int) -> bool:
    """成员面含其它工位即露出整组入口，不依赖兄弟此刻是否挂账。"""
    channel_id = int(channel_id)
    return any(member != channel_id for member in _manual_pass_group_member_ids(channel_id))


def list_hung_manual_pass_siblings(channel_id: int) -> List[Any]:
    """只返回并集内仍有 incomplete 活跃账本且通过门禁的兄弟工位。"""
    channel_id = int(channel_id)
    try:
        from backend.api.channel_manager import channel_manager

        siblings = []
        for member_id in _manual_pass_group_member_ids(channel_id):
            if member_id == channel_id:
                continue
            mgr = channel_manager.channels.get(member_id)
            if mgr is None:
                continue
            if _member_skip_reason(mgr):
                continue
            siblings.append(mgr)
        return siblings
    except Exception as exc:
        print(f"[Hotfix {PATCH_VERSION}] 枚举兄弟工位失败: {exc}", flush=True)
        return []


def queue_manual_pass_scope(
    channel_id: int,
    scope: str = "channel",
    source: str = "api",
    user: str = "",
) -> Dict[str, Any]:
    """API/触发动作共用 fan-out；VSM 自身永远只标记自己的请求。"""
    from backend.api.channel_manager import channel_manager

    channel_id = int(channel_id)
    mgr = channel_manager.channels.get(channel_id)
    if mgr is None:
        return {"ok": False, "msg": f"工位 {channel_id} 不存在", "queued": [], "skipped": []}
    if scope not in ("channel", "group"):
        return {"ok": False, "msg": "scope 只能是 channel 或 group", "queued": [], "skipped": []}
    if scope == "group" and not manual_pass_group_available(channel_id):
        return {
            "ok": False,
            "msg": "当前工位没有可整组放行的 synchronized_all_ok 工位组或等灯扫码枪广播面",
            "queued": [],
            "skipped": [],
        }

    member_ids = [channel_id]
    if scope == "group":
        member_ids.extend(_manual_pass_group_member_ids(channel_id))
    member_ids = [channel_id] + sorted(
        member for member in set(member_ids) if member != channel_id
    )

    queued: List[int] = []
    skipped: List[Dict[str, Any]] = []
    for member_id in member_ids:
        target = channel_manager.channels.get(member_id)
        if target is None:
            skipped.append({"channel": member_id, "reason": "工位不存在"})
            continue
        gate_ok, gate_msg = _manual_pass_gate(target)
        if not gate_ok:
            skipped.append({"channel": member_id, "reason": gate_msg})
            continue
        result = target.request_manual_pass(
            scope="channel", source=source, user=user
        )
        if result.get("ok"):
            queued.append(member_id)
        else:
            skipped.append({"channel": member_id, "reason": result.get("msg", "操作失败")})

    if channel_id not in queued:
        current_reason = next(
            (row["reason"] for row in skipped if row["channel"] == channel_id),
            "当前工位未能排队",
        )
        return {"ok": False, "msg": current_reason, "queued": queued, "skipped": skipped}
    return {
        "ok": True,
        "msg": "人工合格放行已排队",
        "scope": scope,
        "queued": queued,
        "skipped": skipped,
    }


def inject_manual_pass_tracking(result: Any, mgr) -> Any:
    """给已挂载的 results 结果补字段；非 dict/非 tracking 原样返回。"""
    if not isinstance(result, dict):
        return result
    project = getattr(mgr, "project_config", None) or {}
    tracking = result.get("tracking")
    if not isinstance(tracking, dict):
        return result

    pcfg = project.get("pipeline_config") or {}
    manual_pass_enabled = pcfg.get("manual_pass_enabled", True) is True
    settle_on_complete = bool(
        project.get("logic_mode") == "tracking"
        and pcfg.get("tracking_settle_on_complete", False)
        and pcfg.get("tracking_cycle_strategy") in ("roi_exit", "container")
    )
    available = bool(
        settle_on_complete
        and manual_pass_enabled
        and getattr(mgr, "_tracking_cycle_active", False)
        and not getattr(mgr, "_tracking_was_complete", False)
        and getattr(mgr, "is_detecting", False)
    )
    tracking["settle_on_complete"] = settle_on_complete
    tracking["manual_pass_enabled"] = manual_pass_enabled
    tracking["manual_pass_available"] = available
    tracking["manual_pass_group_available"] = bool(
        available and manual_pass_group_available(getattr(mgr, "channel_id", 0))
    )
    return result


# PATCHED_V3530A
# PATCHED_V3530B_CUMULATIVE
