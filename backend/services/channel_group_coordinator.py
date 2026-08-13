"""ChannelGroupCoordinator — 单机内多通道结算联动协调器 (RFC 10 v3.13.0).

客户视角:
  RFC 10 客户需求 4: 双工位 A 站如果判 NG, B 站也要同步判 NG (同一物件
  在两个角度同时被检测).

  实现路径:
    1. 配置 channel_groups 表: name="Group-A", member_channel_ids=[0, 1],
       settle_strategy="synchronized_any_ng"
    2. VSM end_cycle 调 coordinator.on_cycle_settled(channel_id, cycle_id, is_good)
    3. Coordinator 看到 channel 0 是组 1 成员 → 检查策略 → NG → 立即给 channel 1
       设 pending_override = "NG"
    4. channel 1 下次 end_cycle 时调 get_pending_override 取走 NG → 强制覆盖
    5. fire channel_group_settle_start hook 通知插件

设计选择:
  - 单例 (惰性初始化), 通过 get_coordinator() 拿
  - 内部状态 (groups / channel_to_group / pending_override) 用 threading.Lock 保护
  - 不开后台线程: 所有逻辑都是事件驱动的同步函数 (VSM 结算线程调进来即返回)
  - timeout 机制 v3.13.0 暂不实现 (留 RFC 10 §4.1 的 timeout_action 字段, 待下个版本)
  - synchronized_all_ok / master_slave 策略 v3.13.0 暂不实现 (仅 synchronized_any_ng)

零差异默认:
  - 没有任何 channel_group 配置时, on_cycle_settled / get_pending_override / 等所有
    入口都直接返回, 与 v3.12 行为字节级一致
  - reload_groups 时若组列表为空 → 内部状态全清, 完全无负担
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional


_singleton: Optional["ChannelGroupCoordinator"] = None
_singleton_lock = threading.Lock()


def get_coordinator() -> "ChannelGroupCoordinator":
    """获取单例 (惰性初始化, 进程内一份)."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ChannelGroupCoordinator()
    return _singleton


def reset_coordinator_for_testing() -> None:
    """测试用: 重置单例, 防 fixture 互相污染.

    会先取消所有 pending timer 防 Timer 线程跨用例触发.
    """
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            try:
                _singleton.cleanup_timers_for_testing()
            except Exception:
                pass
        _singleton = None


class ChannelGroupCoordinator:
    """单机内工位组协调器.

    职责:
      1. 加载 channel_groups 表的 enabled 组配置
      2. 接 VSM 的 on_cycle_settled 信号 (cycle 结算落库后)
      3. 按 settle_strategy 决定: 广播给其它成员 / 等待对方 / 超时处理
      4. fire RFC 09 channel_group_settle_start / done hook

    线程模型:
      - 进程内单例
      - _groups / _channel_to_group / _pending_override 由 _lock 保护
      - VSM 结算线程调 on_cycle_settled 时不阻塞: 仅在 _lock 内更新 dict, hook
        触发用 fire_plugin_hook (本身有 try/except 错误隔离)
    """

    def __init__(self) -> None:
        # group_id → 组配置 + 运行时状态 dict
        self._groups: Dict[int, Dict[str, Any]] = {}
        # channel_id → group_id (反向索引, 收 on_cycle_settled 时快速查组)
        self._channel_to_group: Dict[int, int] = {}
        # channel_id → "OK" | "NG" (pending override, 下次 end_cycle 强制采用)
        self._pending_override: Dict[int, str] = {}
        # v3.13.1: synchronized_all_ok 聚合等齐状态机
        # group_id → { "members": {ch: {"cycle_id": int, "is_good": bool}},
        #              "started_at": float, "timer": threading.Timer | None }
        self._pending_aggregations: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    # =============================================================
    # 配置加载 / 卸载
    # =============================================================

    def reload_groups(self, db) -> int:
        """重新加载 channel_groups 表 (启动 / 配置变更时调).

        从 DB 拉所有 enabled=True 的组, 重建内部状态.
        老的 _pending_override 不清 (跨周期场景下可能有合理 pending).

        返回加载的组数量.
        """
        from backend.models.models import ChannelGroup

        rows = db.query(ChannelGroup).filter(ChannelGroup.enabled.is_(True)).all()
        with self._lock:
            self._groups.clear()
            self._channel_to_group.clear()
            for row in rows:
                members = row.member_channel_ids or []
                if not isinstance(members, list):
                    continue
                # v3.51 统一播报开关: 挂在 plugin_data (M3 风格, 不动主 schema)
                _pd = row.plugin_data if isinstance(row.plugin_data, dict) else {}
                self._groups[row.id] = {
                    "id": row.id,
                    "name": row.name,
                    "member_channel_ids": list(members),
                    "settle_strategy": row.settle_strategy or "synchronized_any_ng",
                    "timeout_ms": int(row.timeout_ms or 5000),
                    "timeout_action": row.timeout_action or "fallback_independent",
                    "unified_ok_report": bool(_pd.get("unified_ok_report", False)),
                }
                for cid in members:
                    try:
                        self._channel_to_group[int(cid)] = row.id
                    except (TypeError, ValueError):
                        continue
        return len(rows)

    def on_channel_removed(self, channel_id: int) -> None:
        """ChannelManager.set_channel_count 减少通道时调.

        清理 _channel_to_group + _pending_override + _pending_aggregations 中该 channel 的痕迹.
        组配置 (_groups) 不动 — 组配置变更走 reload_groups, 不在这里处理.
        """
        with self._lock:
            self._channel_to_group.pop(channel_id, None)
            self._pending_override.pop(channel_id, None)
            # 把该 channel 从所有 pending aggregation 里移除. 如果导致某 agg 空了, 取消其 timer.
            for gid in list(self._pending_aggregations.keys()):
                agg = self._pending_aggregations[gid]
                agg["members"].pop(channel_id, None)

    def cleanup_timers_for_testing(self) -> None:
        """测试用: 取消所有 pending aggregation 的后台 timer.

        测试 teardown 时调, 防 Timer 线程跨用例污染.
        """
        with self._lock:
            for agg in self._pending_aggregations.values():
                t = agg.get("timer")
                if t:
                    try:
                        t.cancel()
                    except Exception:
                        pass
            self._pending_aggregations.clear()

    # =============================================================
    # 运行时入口
    # =============================================================

    def should_unify_ok_report(self, channel_id: int) -> bool:
        """v3.51 工位组统一播报: 该通道的个体 OK 播报是否应被抑制.

        True 条件: 通道属于某 enabled 组 + 策略 synchronized_all_ok +
        组开了 unified_ok_report。此时个体 OK 结算照常落库/计数/推 MES,
        但灯/语音/前端 toast 不播 — 等组聚齐全 OK 后统一播一次
        (_finalize_aggregation)。默认关, 零差异。
        """
        with self._lock:
            group_id = self._channel_to_group.get(channel_id)
            if group_id is None:
                return False
            group = self._groups.get(group_id)
            if not group:
                return False
            return (group["settle_strategy"] == "synchronized_all_ok"
                    and bool(group.get("unified_ok_report", False)))

    def _fire_unified_report(self, group: Dict[str, Any], channel_ids: List[int],
                             reason: str) -> None:
        """v3.51: 对组内通道统一补一次"合格"事件响应面 (灯/语音/toast).

        复用 fire_external_event_response — 不 end_cycle、不动周期统计、不计数
        (成员各自结算时计数已 +1, remind_only=True 跳过计数器联动防双计)。
        错误隔离: 任一通道失败不影响其它通道。
        """
        try:
            from backend.api.channel_manager import channel_manager
        except Exception as e:
            print(f"[ChannelGroup] 统一播报 import channel_manager 失败 (隔离): {e}")
            return
        for cid in channel_ids:
            try:
                mgr = channel_manager.channels.get(cid)
                if mgr is None:
                    continue
                mgr.fire_external_event_response(
                    1, reason, source="channel_group", remind_only=True)
            except Exception as e:
                print(f"[ChannelGroup] 统一播报 ch{cid} 异常 (隔离): {e}")
        print(f"[ChannelGroup][{group['name']}] 统一播报: {reason} → ch{channel_ids}")

    def get_pending_override(self, channel_id: int) -> Optional[str]:
        """VSM end_cycle 调: 看本次结算是否被组级联动覆盖.

        - 返回 "OK" / "NG" → 主程序应把该结果强制写入 cycle.group_settle_result
        - 返回 None → 无 override, 按 cycle.is_good 决定 group_settle_result

        语义: take-once. 取走后 pending_override 清空, 下次 cycle 默认无 override.
        """
        with self._lock:
            return self._pending_override.pop(channel_id, None)

    def on_cycle_settled(self, channel_id: int, cycle_id: int, is_good: bool, db) -> None:
        """VSM end_cycle 写库后调 — 工位组联动入口.

        v3.13.0 仅实现 synchronized_any_ng 策略:
          - 本次结算 NG → 同组其它成员设 _pending_override = "NG"
          - 同时把本通道当前 cycle 的 group_settle_result 写为 "NG", group_settled_with=[]
            (其他成员后续结算时再回填 partner cycle_id 到自己的 group_settled_with)
          - fire channel_group_settle_start hook
          - 本次结算 OK → 仅记录 group_settle_result="OK" (无广播)

        其他策略 (synchronized_all_ok / master_slave / independent) v3.13.0 暂只
        记录 group_settle_result, 不做广播.

        独立通道 (不在任何组) → 直接 return, 字节级零差异.
        """
        with self._lock:
            group_id = self._channel_to_group.get(channel_id)
            if group_id is None:
                return  # 通道不在任何组 → 字节级零差异
            group = self._groups.get(group_id)
            if not group:
                return
            strategy = group["settle_strategy"]
            members = list(group["member_channel_ids"])
            timeout_ms = int(group.get("timeout_ms") or 5000)
            timeout_action = group.get("timeout_action") or "fallback_independent"

        # 把本通道当前 cycle 标到组里 (无论策略如何都标, 让 group_settle_result
        # 字段成为"该周期的组级原意"). 即使是 independent 策略也写, 方便审计.
        self._write_cycle_group_fields(
            db, cycle_id,
            channel_group_id=group_id,
            group_settle_result="OK" if is_good else "NG",
            settled_with=None,
        )

        # NG 立即广播到其它成员 (synchronized_any_ng 和 synchronized_all_ok 都立即响应 NG)
        if strategy in ("synchronized_any_ng", "synchronized_all_ok") and not is_good:
            self._broadcast_ng_to_group(
                db, group, channel_id, cycle_id, members,
            )

        # synchronized_all_ok: 把本周期挂入 pending aggregation 等齐
        if strategy == "synchronized_all_ok":
            all_arrived = False
            with self._lock:
                if group_id not in self._pending_aggregations:
                    self._pending_aggregations[group_id] = {
                        "members": {},
                        "started_at": time.time(),
                        "timer": None,
                    }
                agg = self._pending_aggregations[group_id]
                agg["members"][channel_id] = {"cycle_id": cycle_id, "is_good": is_good}
                # 每来一个成员就取消老 timer (避免老 timer 早于 done hook 触发)
                if agg["timer"]:
                    try:
                        agg["timer"].cancel()
                    except Exception:
                        pass
                    agg["timer"] = None
                all_arrived = all(c in agg["members"] for c in members)
                if not all_arrived:
                    t = threading.Timer(
                        timeout_ms / 1000.0,
                        self._on_aggregation_timeout,
                        args=(group_id, timeout_action),
                    )
                    t.daemon = True
                    agg["timer"] = t
                    t.start()

            if all_arrived:
                self._finalize_aggregation(group_id, reason="complete")

    # =============================================================
    # 内部 helper
    # =============================================================

    def _broadcast_ng_to_group(
        self,
        db,
        group: Dict[str, Any],
        trigger_channel_id: int,
        trigger_cycle_id: int,
        members: List[int],
    ) -> None:
        """synchronized_any_ng 策略: NG 触发时给其它成员设 pending override + 驱动报警链路."""
        other_channels = [c for c in members if c != trigger_channel_id]

        # 设 pending override (其它成员下次 end_cycle 取走)
        with self._lock:
            for cid in other_channels:
                self._pending_override[cid] = "NG"

        # v3.13.1: 报警链路联动 — 直接驱动 AlarmRouter 让 B 通道报警灯立刻亮 NG.
        # 跳过 _trigger_event 链路 (避免 event_fire 双触发, 也避开 events_config 里没有
        # 'channel_group_override' 事件 id 的限制). 错误隔离.
        try:
            from backend.api.alarm import alarm_router
            for cid in other_channels:
                try:
                    # event2 = 标准 NG 事件 (客户 alarm 配置里默认就有 event2 = 红灯/蜂鸣 1-3s)
                    alarm_router.trigger_alarm("event2", channel_id=cid)
                except Exception as _e:
                    print(f"[ChannelGroup] alarm trigger ch{cid} 异常 (隔离): {_e}")
        except Exception as e:
            print(f"[ChannelGroup] alarm_router import 失败 (隔离, 不影响主流程): {e}")

        # fire channel_group_settle_start hook
        try:
            from backend.plugin_system.hook_dispatch import fire_plugin_hook
            fire_plugin_hook("channel_group_settle_start", "broadcast_any_ng", "post", {
                "group_id": group["id"],
                "group_name": group["name"],
                "trigger_channel_id": trigger_channel_id,
                "trigger_cycle_id": trigger_cycle_id,
                "member_channel_ids": list(members),
                "strategy": group["settle_strategy"],
            })
        except Exception as e:
            print(f"[ChannelGroup] channel_group_settle_start hook 异常 (隔离): {e}")

        print(
            f"[ChannelGroup][{group['name']}] ch{trigger_channel_id} 结算 NG "
            f"(cycle_id={trigger_cycle_id}) → 广播到 ch{other_channels} (含 alarm 联动)"
        )

    # =============================================================
    # synchronized_all_ok / timeout / done hook (v3.13.1)
    # =============================================================

    def _on_aggregation_timeout(self, group_id: int, timeout_action: str) -> None:
        """Timer 后台线程回调. 不持 db session — 写库已在每个成员 on_cycle_settled 完成.

        timeout_action:
          - fallback_independent: 已结算的不动 (各自的 group_settle_result 已写), 仅 fire done hook
          - force_ng: 给还没到的成员设 _pending_override = "NG", 让它们下次 end_cycle 被强制 NG
        """
        print(f"[ChannelGroup] group {group_id} aggregation timeout, action={timeout_action}")
        try:
            self._finalize_aggregation(group_id, reason="timeout", timeout_action=timeout_action)
        except Exception as e:
            print(f"[ChannelGroup] _finalize_aggregation timeout 异常 (隔离): {e}")

    def _finalize_aggregation(
        self,
        group_id: int,
        reason: str,
        timeout_action: str = "fallback_independent",
    ) -> None:
        """聚齐或超时调用. 计算组级结果 + fire done hook + 清 pending.

        reason ∈ {"complete", "timeout"}.
        """
        with self._lock:
            agg = self._pending_aggregations.pop(group_id, None)
            group = self._groups.get(group_id)

        if not agg or not group:
            return

        # 取消还在跑的 timer (complete 路径可能还有未 cancel 的)
        t = agg.get("timer")
        if t:
            try:
                t.cancel()
            except Exception:
                pass

        members_arrived = agg.get("members", {})
        members_expected = list(group["member_channel_ids"])

        # 计算组级结果
        group_result: str
        if reason == "complete":
            all_ok = all(m["is_good"] for m in members_arrived.values())
            group_result = "OK" if all_ok else "NG"
        else:  # timeout
            if timeout_action == "force_ng":
                group_result = "NG_BY_TIMEOUT"
                # 给没到的成员设 NG override
                with self._lock:
                    for cid in members_expected:
                        if cid not in members_arrived:
                            self._pending_override[cid] = "NG"
            else:
                group_result = "PARTIAL"

        # fire channel_group_settle_done hook
        try:
            from backend.plugin_system.hook_dispatch import fire_plugin_hook
            fire_plugin_hook("channel_group_settle_done", "aggregation_finalize", "post", {
                "group_id": group["id"],
                "group_name": group["name"],
                "reason": reason,
                "group_result": group_result,
                "timeout_action": timeout_action if reason == "timeout" else None,
                "members_arrived": list(members_arrived.keys()),
                "members_expected": members_expected,
                "strategy": group["settle_strategy"],
                "cycle_ids": {ch: m["cycle_id"] for ch, m in members_arrived.items()},
            })
        except Exception as e:
            print(f"[ChannelGroup] channel_group_settle_done hook 异常 (隔离): {e}")

        print(
            f"[ChannelGroup][{group['name']}] aggregation finalized: "
            f"reason={reason} result={group_result} arrived={list(members_arrived.keys())} "
            f"expected={members_expected}"
        )

        # v3.51 统一播报 (unified_ok_report 开时):
        #   - 聚齐且全 OK → 组内所有成员统一播一次"合格" (个体 OK 播报在
        #     _trigger_event 被抑制过, 这里是唯一的用户感知出口)
        #   - 超时 (fallback_independent) → 已到且 OK 的成员补播个体合格,
        #     否则这些工位的工人永远看不到任何 OK 反馈
        #   - 组内出 NG → 不播 OK; NG 个体播报从未被抑制 + any_ng/all_ok 的
        #     NG 广播照旧, 已有完整反馈
        if bool(group.get("unified_ok_report", False)):
            try:
                ok_members = [ch for ch, m in members_arrived.items() if m["is_good"]]
                if reason == "complete" and group_result == "OK":
                    self._fire_unified_report(
                        group, members_expected,
                        f"工位组[{group['name']}]全部合格")
                elif reason == "timeout" and ok_members:
                    self._fire_unified_report(
                        group, ok_members,
                        f"工位组[{group['name']}]等待超时, 本工位已合格")
            except Exception as e:
                print(f"[ChannelGroup] 统一播报调度异常 (隔离): {e}")

    def _write_cycle_group_fields(
        self,
        db,
        cycle_id: int,
        *,
        channel_group_id: Optional[int],
        group_settle_result: Optional[str],
        settled_with: Optional[List[int]],
    ) -> None:
        """把组级字段回写到 detection_cycles 行 — 错误吞掉防主流程崩."""
        from backend.models.models import DetectionCycle
        try:
            row = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
            if not row:
                return
            row.channel_group_id = channel_group_id
            row.group_settle_result = group_settle_result
            if settled_with is not None:
                row.group_settled_with = list(settled_with)
            db.commit()
        except Exception as e:
            print(f"[ChannelGroup] _write_cycle_group_fields cycle={cycle_id} 异常: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    # =============================================================
    # PluginHost 主动 API 用 (M1.3b 解锁)
    # =============================================================

    def list_groups(self) -> List[Dict[str, Any]]:
        """供 PluginHost.list_channel_groups 用 (返 snapshot 副本, 不暴露内部 dict)."""
        with self._lock:
            return [dict(g) for g in self._groups.values()]

    def get_group(self, group_id: int) -> Optional[Dict[str, Any]]:
        """供 PluginHost.query_channel_group 用."""
        with self._lock:
            g = self._groups.get(group_id)
            return dict(g) if g else None

    def is_channel_in_group(self, channel_id: int) -> bool:
        """供 PluginHost.broadcast_to_channel_group 校验用."""
        with self._lock:
            return channel_id in self._channel_to_group
