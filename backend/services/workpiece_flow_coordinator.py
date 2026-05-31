"""WorkpieceFlowCoordinator — 单机内多工位串行流水线协调器 (RFC 11 v3.14.0).

客户视角:
  RFC 11 客户需求: 同一工件依次走过 N 个工位 (例 工位 1 检前 3 步, 工位 2 检后 2 步),
  全部 OK 才算合格. 任一工位 NG 立即停后续 (short_circuit) 节省检测资源.

  实现路径:
    1. 配置 workpiece_flow_configs 表 (一台机器可有 0~N 个 flow).
    2. 一个 Trigger (scan / time_window / physical) 在工件入口触发
       coordinator.on_workpiece_enter(flow_config_id, serial_no, ...).
    3. Coordinator 创建 WorkpieceFlowRun (status=in_progress), 启 timeout Timer.
    4. 工位 N 的 VSM cycle_start → coordinator.on_cycle_started(ch=N, cycle_id=...)
       把 cycle 绑到 flow_run 的 station N 位置.
    5. 工位 N 的 VSM cycle_end → coordinator.on_cycle_settled(ch=N, cycle_id=..., is_good=...)
       写 station_cycle_ids / station_results.
       - 若 is_good=False + short_circuit_on_ng=True + 后面还有工位 → SHORT_CIRCUITED
       - 若已到最后工位 → COMPLETED_OK/NG, 推 MES, 触发报警
    6. 超时 → per timeout_action 处理 (force_ng / drop / alarm_only).

设计选择:
  - 单例 (惰性初始化), 通过 get_coordinator() 拿. 与 ChannelGroupCoordinator 同设计.
  - 内部状态 (flows / in_flight runs / timers) 用 threading.Lock 保护.
  - 不开后台线程: 所有逻辑都是事件驱动的同步函数 (Trigger 调进来即返回).
  - timeout 用 threading.Timer (daemon=True), 跟 ChannelGroupCoordinator 一致.
  - 与 ChannelGroupCoordinator 完全独立, 不依赖, 不共享状态.
  - v1 仅实现 settle_strategy="all_ok_required". 其他策略 v2 评估.

零差异默认:
  - 没有任何 workpiece_flow_configs 启用时, 所有入口都直接返回, 与 v3.13 字节级一致.
  - reload_flows 时若 enabled 列表为空 → 内部状态全清, 完全无负担.

线程模型:
  - 进程内单例.
  - VSM 结算线程调 on_cycle_settled 时不阻塞 (lock 内更新 dict, hook 自带错误隔离).
  - Timer 后台线程回调 _on_workpiece_timeout 时也只更新 dict + fire hook.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional


_singleton: Optional["WorkpieceFlowCoordinator"] = None
_singleton_lock = threading.Lock()


def get_coordinator() -> "WorkpieceFlowCoordinator":
    """获取单例 (惰性初始化, 进程内一份)."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = WorkpieceFlowCoordinator()
    return _singleton


def reset_coordinator_for_testing() -> None:
    """测试用: 重置单例, 防 fixture 互相污染.

    会先取消所有 in-flight timer 防 Timer 线程跨用例触发.
    """
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            try:
                _singleton.cleanup_for_testing()
            except Exception:
                pass
        _singleton = None


# 状态机终态集合
_TERMINAL_STATES = {"completed", "timeout", "short_circuited", "aborted"}


class WorkpieceFlowCoordinator:
    """单机内多工位串行流水线协调器.

    内部数据结构:
      _flows: flow_config_id → flow 配置 + 运行时状态 dict
          {
            "id": int, "name": str, "enabled": bool,
            "station_channel_ids": List[int],  # 有序!
            "trigger_mode": str,
            "scan_device_id": Optional[int],
            "scan_bind_strategy": str,
            "fifo_max_in_flight": int,
            "cycle_to_cycle_window_ms": int,
            "physical_trigger_config": Optional[dict],
            "settle_strategy": str,
            "short_circuit_on_ng": bool,
            "workpiece_timeout_ms": int,
            "timeout_action": str,
          }

      _channel_to_flow: channel_id → flow_config_id (反向索引)
          注: 一个 channel 只能属于一个 flow (启用时 API 校验, 这里 last-write-wins).

      _in_flight_runs: flow_uuid → in-flight run 状态 dict
          {
            "flow_uuid": str, "flow_config_id": int,
            "serial_no": str | None,
            "workpiece_id": int | None,
            "run_db_id": int,   # workpiece_flow_runs.id
            "station_cycle_ids": List[Optional[int]],  # 长度 = stations 数, 占位 None
            "station_results": List[Optional[str]],    # 长度 = stations 数, "OK"/"NG"/None
            "current_station_index": int,  # 0..len(stations)-1, 当前等待 cycle_start 的工位
            "started_at": float,
            "timeout_timer": threading.Timer | None,
            "trigger_mode": str,
            "trigger_source_id": int | None,
            "status": str,  # in_progress / 终态
          }

      _flow_to_in_flight: flow_config_id → List[flow_uuid] (按入队顺序, 用于 FIFO)
    """

    def __init__(self) -> None:
        self._flows: Dict[int, Dict[str, Any]] = {}
        self._channel_to_flow: Dict[int, int] = {}
        self._in_flight_runs: Dict[str, Dict[str, Any]] = {}
        self._flow_to_in_flight: Dict[int, List[str]] = {}
        # flow_id → FlowTriggerBase 实例 (策略对象, 每次 reload_flows 时重建)
        self._triggers: Dict[int, Any] = {}
        self._lock = threading.Lock()

    # =============================================================
    # 配置加载 / 卸载
    # =============================================================

    def reload_flows(self, db) -> int:
        """重新加载 workpiece_flow_configs 表 (启动 / 配置变更时调).

        从 DB 拉所有 enabled=True 的 flow, 重建内部状态.
        in-flight runs 不动 (跨周期场景下可能有合理 in-flight, 配置变更 API
        层应该已阻止有 in-flight 时修改).

        返回加载的 flow 数量.
        """
        from backend.models.mes_models import WorkpieceFlowConfig

        rows = db.query(WorkpieceFlowConfig).filter(
            WorkpieceFlowConfig.enabled.is_(True)
        ).all()
        with self._lock:
            # 卸载老 trigger
            for trig in self._triggers.values():
                try:
                    trig.detach()
                except Exception:
                    pass
            self._triggers.clear()

            self._flows.clear()
            self._channel_to_flow.clear()
            for row in rows:
                stations = row.station_channel_ids or []
                if not isinstance(stations, list) or len(stations) < 2:
                    # 流水线至少 2 工位才有意义, 跳过非法配置
                    continue
                try:
                    stations_int = [int(c) for c in stations]
                except (TypeError, ValueError):
                    continue

                flow_snapshot = {
                    "id": row.id,
                    "name": row.name,
                    "enabled": True,
                    "station_channel_ids": stations_int,
                    "trigger_mode": row.trigger_mode or "time_window",
                    "scan_device_id": row.scan_device_id,
                    "scan_bind_strategy": row.scan_bind_strategy or "entry",
                    "fifo_max_in_flight": int(row.fifo_max_in_flight or 3),
                    "cycle_to_cycle_window_ms": int(row.cycle_to_cycle_window_ms or 15000),
                    "physical_trigger_config": row.physical_trigger_config,
                    "settle_strategy": row.settle_strategy or "all_ok_required",
                    "short_circuit_on_ng": bool(row.short_circuit_on_ng),
                    "workpiece_timeout_ms": int(row.workpiece_timeout_ms or 60000),
                    "timeout_action": row.timeout_action or "force_ng",
                }
                self._flows[row.id] = flow_snapshot
                for cid in stations_int:
                    self._channel_to_flow[cid] = row.id
                # 初始化空 FIFO
                self._flow_to_in_flight.setdefault(row.id, [])
                # 实例化对应 trigger
                trig = self._build_trigger(flow_snapshot)
                if trig is not None:
                    try:
                        trig.attach(self, flow_snapshot)
                        self._triggers[row.id] = trig
                    except Exception as e:
                        print(f"[WorkpieceFlow] trigger attach 失败 (flow={row.id}): {e}")
        return len(self._flows)

    def _build_trigger(self, flow: Dict[str, Any]):
        """根据 trigger_mode 实例化对应 Trigger 类."""
        mode = flow.get("trigger_mode") or "time_window"
        try:
            if mode == "time_window":
                from backend.services.flow_triggers.time_window_trigger import TimeWindowTrigger
                return TimeWindowTrigger()
            if mode == "scan":
                # M3 接入
                try:
                    from backend.services.flow_triggers.scan_trigger import ScanTrigger
                    return ScanTrigger()
                except ImportError:
                    return None
            if mode == "physical":
                # M7 接入
                try:
                    from backend.services.flow_triggers.physical_trigger import PhysicalTrigger
                    return PhysicalTrigger()
                except ImportError:
                    return None
        except Exception as e:
            print(f"[WorkpieceFlow] _build_trigger 异常 (mode={mode}): {e}")
        return None

    def on_channel_removed(self, channel_id: int) -> None:
        """ChannelManager.set_channel_count 减少通道时调.

        清理 _channel_to_flow 中该 channel 的痕迹.
        flow 配置 (_flows) 不动 — 配置变更走 reload_flows, 不在这里处理.
        in-flight runs 也不动 — 它们走 timeout 自然终态.
        """
        with self._lock:
            self._channel_to_flow.pop(channel_id, None)

    def cleanup_for_testing(self) -> None:
        """测试用: 取消所有 in-flight timer + detach 所有 trigger.

        测试 teardown 时调, 防 Timer 线程跨用例污染 + 防 Trigger 残留外部回调.
        """
        with self._lock:
            for state in self._in_flight_runs.values():
                t = state.get("timeout_timer")
                if t:
                    try:
                        t.cancel()
                    except Exception:
                        pass
            self._in_flight_runs.clear()
            self._flow_to_in_flight.clear()
            for trig in self._triggers.values():
                try:
                    trig.detach()
                except Exception:
                    pass
            self._triggers.clear()
            self._flows.clear()
            self._channel_to_flow.clear()

    # =============================================================
    # 进程启动恢复
    # =============================================================

    def abort_in_progress_on_startup(self, db) -> int:
        """进程重启时把所有 status=in_progress 的 run 标 aborted.

        避免上次进程崩溃留下的孤儿 run 在前端展示成"in-flight" (实际后端无状态).
        返回 aborted 的数量.
        """
        from backend.models.mes_models import WorkpieceFlowRun

        try:
            rows = db.query(WorkpieceFlowRun).filter(
                WorkpieceFlowRun.status == "in_progress"
            ).all()
            count = 0
            now = time.time()
            for row in rows:
                row.status = "aborted"
                from datetime import datetime, timezone
                row.completed_at = datetime.now(timezone.utc)
                count += 1
            db.commit()
            return count
        except Exception as e:
            print(f"[WorkpieceFlow] abort_in_progress_on_startup 异常: {e}")
            try:
                db.rollback()
            except Exception:
                pass
            return 0

    # =============================================================
    # 主入口: Trigger 把工件送进来
    # =============================================================

    def on_workpiece_enter(
        self,
        flow_config_id: int,
        serial_no: Optional[str],
        trigger_mode: str,
        trigger_source_id: Optional[int] = None,
        workpiece_id: Optional[int] = None,
        db=None,
    ) -> Optional[str]:
        """Trigger 调: 工件进流水线入口.

        创建一条 WorkpieceFlowRun (status=in_progress), 启 timeout Timer,
        fire workpiece_flow_enter hook.

        FIFO 满 (in_flight 数量已到 fifo_max_in_flight) → 拒绝, 返回 None.
        重复扫码 (同 serial_no 1 秒内) → 幂等忽略, 返回老的 flow_uuid.

        返回: flow_uuid (创建成功) 或 None (拒绝).
        """
        with self._lock:
            flow = self._flows.get(flow_config_id)
            if not flow or not flow.get("enabled"):
                return None

            # 重复扫码去重 (1 秒内同 serial_no 已有 in-flight → 幂等忽略)
            if serial_no:
                for existing_uuid in self._flow_to_in_flight.get(flow_config_id, []):
                    existing = self._in_flight_runs.get(existing_uuid)
                    if not existing:
                        continue
                    if existing.get("serial_no") == serial_no and \
                       existing.get("status") == "in_progress" and \
                       (time.time() - existing.get("started_at", 0)) < 1.0:
                        return existing_uuid  # 幂等返回

            # FIFO 满判定
            in_flight_count = len([
                u for u in self._flow_to_in_flight.get(flow_config_id, [])
                if self._in_flight_runs.get(u, {}).get("status") == "in_progress"
            ])
            if in_flight_count >= flow["fifo_max_in_flight"]:
                print(
                    f"[WorkpieceFlow][{flow['name']}] FIFO 满 "
                    f"({in_flight_count}/{flow['fifo_max_in_flight']}), 拒绝 serial={serial_no}"
                )
                # 触发 alarm (FIFO 满是工件流速过快或上游卡住, 客户需要知道)
                self._fire_alarm_safe("event2", flow["station_channel_ids"][0])
                return None

            flow_uuid = uuid.uuid4().hex
            station_count = len(flow["station_channel_ids"])

            state = {
                "flow_uuid": flow_uuid,
                "flow_config_id": flow_config_id,
                "serial_no": serial_no,
                "workpiece_id": workpiece_id,
                "run_db_id": None,  # 落库后补
                "station_cycle_ids": [None] * station_count,
                "station_results": [None] * station_count,
                "current_station_index": 0,
                "started_at": time.time(),
                "timeout_timer": None,
                "trigger_mode": trigger_mode,
                "trigger_source_id": trigger_source_id,
                "status": "in_progress",
            }
            self._in_flight_runs[flow_uuid] = state
            self._flow_to_in_flight.setdefault(flow_config_id, []).append(flow_uuid)

            # 启 timeout timer
            timer = threading.Timer(
                flow["workpiece_timeout_ms"] / 1000.0,
                self._on_workpiece_timeout,
                args=(flow_uuid, flow["timeout_action"]),
            )
            timer.daemon = True
            state["timeout_timer"] = timer
            timer.start()

            flow_snapshot = dict(flow)

        # 落库 (锁外, 避免长事务卡住 lock)
        run_db_id = self._insert_flow_run(db, state, flow_snapshot)
        if run_db_id:
            with self._lock:
                if flow_uuid in self._in_flight_runs:
                    self._in_flight_runs[flow_uuid]["run_db_id"] = run_db_id

        # fire hook
        self._fire_hook_safe("workpiece_flow_enter", "trigger_enter", "post", {
            "flow_uuid": flow_uuid,
            "flow_config_id": flow_config_id,
            "flow_name": flow_snapshot["name"],
            "serial_no": serial_no,
            "workpiece_id": workpiece_id,
            "trigger_mode": trigger_mode,
            "trigger_source_id": trigger_source_id,
            "station_channel_ids": flow_snapshot["station_channel_ids"],
        })

        print(
            f"[WorkpieceFlow][{flow_snapshot['name']}] enter: serial={serial_no} "
            f"trigger={trigger_mode} uuid={flow_uuid[:8]} stations={flow_snapshot['station_channel_ids']}"
        )
        return flow_uuid

    # =============================================================
    # 主入口: VSM 把 cycle 信号送进来
    # =============================================================

    def on_cycle_started(
        self,
        channel_id: int,
        cycle_id: int,
        db=None,
    ) -> None:
        """VSM start_cycle commit 后调. 把 cycle 绑到对应 flow_run 的 station 位置.

        步骤:
          1. 若 channel 不属于任何 flow → 直接 return (独立工位).
          2. 询问 Trigger 是否要自动入口工件 (time_window: 入口工位 cycle_start 即新工件).
          3. FIFO 绑 cycle: 拿队列里第一个 status=in_progress 且本 channel 还没被绑过 cycle 的 run.
          4. 若没有匹配的 in-flight run → 静默忽略 (扫码/物理模式下工件未到先开始 cycle).
        """
        # 锁内拿 flow + trigger 快照
        with self._lock:
            flow_id = self._channel_to_flow.get(channel_id)
            if flow_id is None:
                return  # 该 channel 不属于任何 flow → 独立工位行为
            flow = self._flows.get(flow_id)
            if not flow:
                return
            trig = self._triggers.get(flow_id)

        # 锁外: 询问 Trigger 是否要触发新工件入口
        auto_serial = None
        if trig is not None:
            try:
                auto_serial = trig.evaluate_cycle_start(channel_id)
            except Exception as e:
                print(f"[WorkpieceFlow] trigger.evaluate_cycle_start 异常 (隔离): {e}")

        if auto_serial:
            # Trigger 决定新工件入口 (time_window 模式: 入口工位 cycle_start)
            self.on_workpiece_enter(
                flow_config_id=flow_id,
                serial_no=auto_serial,
                trigger_mode=flow.get("trigger_mode", "time_window"),
                db=db,
            )

        # 锁内: 绑 cycle 到队首 in-flight run
        with self._lock:
            flow = self._flows.get(flow_id)
            if not flow:
                return
            station_index = self._find_station_index(flow, channel_id)
            if station_index is None:
                return

            target_uuid = None
            for u in self._flow_to_in_flight.get(flow_id, []):
                run = self._in_flight_runs.get(u)
                if not run:
                    continue
                if run["status"] != "in_progress":
                    continue
                if run["station_cycle_ids"][station_index] is None:
                    target_uuid = u
                    break

            if target_uuid is None:
                # 没有 in-flight run 等这个工位 → 独立工位行为
                return

            run = self._in_flight_runs[target_uuid]
            run["station_cycle_ids"][station_index] = cycle_id
            run_db_id = run.get("run_db_id")
            station_cycle_ids_snap = list(run["station_cycle_ids"])
            flow_uuid = target_uuid
            flow_name = flow["name"]

        # 锁外落库
        self._update_run_station_cycles(db, run_db_id, station_cycle_ids_snap)
        print(
            f"[WorkpieceFlow][{flow_name}] cycle_started: ch={channel_id} "
            f"cycle={cycle_id} → station_index={station_index} uuid={flow_uuid[:8]}"
        )

    def on_cycle_settled(
        self,
        channel_id: int,
        cycle_id: int,
        is_good: bool,
        db=None,
    ) -> None:
        """VSM end_cycle commit 后调. 推进 flow_run 状态机.

        - 写 station_results[station_index]
        - 若是最后一站 / 任一 NG + short_circuit → 终态结算
        - 终态: fire workpiece_flow_completed hook + 推 MES + alarm + 取消 timer
        """
        with self._lock:
            flow_id = self._channel_to_flow.get(channel_id)
            if flow_id is None:
                return

            flow = self._flows.get(flow_id)
            if not flow:
                return

            station_index = self._find_station_index(flow, channel_id)
            if station_index is None:
                return

            # 找绑了这个 cycle 的 run
            target_uuid = None
            for u in self._flow_to_in_flight.get(flow_id, []):
                run = self._in_flight_runs.get(u)
                if not run or run["status"] != "in_progress":
                    continue
                if run["station_cycle_ids"][station_index] == cycle_id:
                    target_uuid = u
                    break

            if target_uuid is None:
                return  # 没找到对应的 run

            run = self._in_flight_runs[target_uuid]
            run["station_results"][station_index] = "OK" if is_good else "NG"

            # 决定下一步
            total_stations = len(flow["station_channel_ids"])
            is_last_station = (station_index == total_stations - 1)
            short_circuit = flow["short_circuit_on_ng"] and not is_good and not is_last_station

            if is_last_station:
                # 最后一站 → 合并结果
                all_ok = all(r == "OK" for r in run["station_results"] if r is not None)
                # 检查是否还有空位 (跳工位的情况)
                has_skipped = any(r is None for r in run["station_results"])
                final_result = "OK" if (all_ok and not has_skipped) else "NG"
                run["final_result"] = final_result
                run["status"] = "completed"
                terminal_reason = "completed"
            elif short_circuit:
                run["final_result"] = "NG"
                run["status"] = "short_circuited"
                terminal_reason = "short_circuited"
            else:
                # 继续等下一工位
                run["current_station_index"] = station_index + 1
                terminal_reason = None

            run_snapshot = dict(run)
            flow_snapshot = dict(flow)

        # 锁外: 推进 hook + DB + 终态处理
        if terminal_reason is None:
            # 中间工位完成, fire station_done hook + DB 更新
            self._update_run_station_results(
                db,
                run_snapshot["run_db_id"],
                run_snapshot["station_results"],
            )
            self._fire_hook_safe("workpiece_flow_station_done", "cycle_settled", "post", {
                "flow_uuid": run_snapshot["flow_uuid"],
                "flow_config_id": flow_snapshot["id"],
                "flow_name": flow_snapshot["name"],
                "station_index": station_index,
                "channel_id": channel_id,
                "cycle_id": cycle_id,
                "is_good": is_good,
                "serial_no": run_snapshot["serial_no"],
                "station_results_so_far": list(run_snapshot["station_results"]),
            })
            print(
                f"[WorkpieceFlow][{flow_snapshot['name']}] station_done: "
                f"ch={channel_id} cycle={cycle_id} result={'OK' if is_good else 'NG'} "
                f"station={station_index + 1}/{total_stations} uuid={run_snapshot['flow_uuid'][:8]}"
            )
        else:
            # 终态: 收尾 (取 timer / 推 hook / DB 写终态 / MES / alarm)
            self._finalize_run(db, run_snapshot, flow_snapshot, terminal_reason)

    # =============================================================
    # 扫码入口 (M3): mes_hooks.on_scan_received 钩子点调
    # =============================================================

    def on_scan_received(
        self,
        channel_id: int,
        barcode: str,
        scanner_device_id: Optional[int] = None,
        project_id: Optional[int] = None,
        db=None,
    ) -> Optional[str]:
        """扫码事件 → 分发给本通道所属 flow 的 trigger.

        返回:
          flow_uuid (新工件已 enter) 或 None (该通道不在任何 flow / trigger 拒绝).
        """
        with self._lock:
            flow_id = self._channel_to_flow.get(channel_id)
            if flow_id is None:
                return None  # 不在任何 flow → mes_hooks 走原 scan_pair 路径
            flow = self._flows.get(flow_id)
            if not flow:
                return None
            trig = self._triggers.get(flow_id)

        if trig is None:
            return None

        try:
            serial = trig.evaluate_scan(
                barcode=barcode,
                scanner_device_id=scanner_device_id,
                channel_id=channel_id,
            )
        except Exception as e:
            print(f"[WorkpieceFlow] trigger.evaluate_scan 异常 (隔离): {e}")
            return None

        if not serial:
            return None  # trigger 静默忽略

        # 复用 WorkpieceService.register 创建/查找工件 (用 raw_barcode 落库追溯)
        workpiece_id = self._register_workpiece_safe(
            db, serial, project_id, scanner_device_id, channel_id
        )

        return self.on_workpiece_enter(
            flow_config_id=flow_id,
            serial_no=serial,
            trigger_mode=flow.get("trigger_mode", "scan"),
            trigger_source_id=scanner_device_id,
            workpiece_id=workpiece_id,
            db=db,
        )

    def on_physical_signal(
        self,
        device_id: int,
        signal_key: str,
        project_id: Optional[int] = None,
        db=None,
    ) -> Optional[str]:
        """物理 GPIO/Modbus 信号到达 → 分发给所有 physical 模式 flow 的 trigger.

        与 on_scan_received 不同: 物理信号没有 channel 概念, 它是"全局信号",
        所以遍历所有 flow 找出 trigger_mode=physical 的 flow 分别评估.

        返回:
          首个被触发的 flow_uuid, 或 None.
        """
        with self._lock:
            physical_flows = [
                (fid, flow) for fid, flow in self._flows.items()
                if (flow.get("trigger_mode") == "physical") and flow.get("enabled")
            ]
            triggers_snapshot = {fid: self._triggers.get(fid) for fid, _ in physical_flows}

        for flow_id, flow in physical_flows:
            trig = triggers_snapshot.get(flow_id)
            if trig is None:
                continue
            try:
                serial = trig.evaluate_physical_signal(device_id, signal_key)
            except Exception as e:
                print(f"[WorkpieceFlow] trigger.evaluate_physical_signal 异常 (隔离): {e}")
                continue
            if not serial:
                continue

            entry_channel = (flow.get("station_channel_ids") or [None])[0]
            workpiece_id = self._register_workpiece_safe(
                db, serial, project_id, device_id, entry_channel
            )

            uuid = self.on_workpiece_enter(
                flow_config_id=flow_id,
                serial_no=serial,
                trigger_mode="physical",
                trigger_source_id=device_id,
                workpiece_id=workpiece_id,
                db=db,
            )
            if uuid:
                return uuid
        return None

    def _register_workpiece_safe(
        self,
        db,
        serial_no: str,
        project_id: Optional[int],
        scanner_device_id: Optional[int],
        channel_id: int,
    ) -> Optional[int]:
        """复用 WorkpieceService.register 落库工件, 拿到 workpiece_id."""
        if db is None or project_id is None or not serial_no:
            return None
        try:
            from backend.services.workpiece import WorkpieceService
            svc = WorkpieceService()
            wp = svc.register(
                db,
                serial_no=serial_no,
                project_id=project_id,
                raw_barcode=serial_no,
                scan_source="scanner",
                scan_device_id=scanner_device_id,
                channel_id=channel_id,
            )
            db.commit()
            return wp.id
        except Exception as e:
            print(f"[WorkpieceFlow] _register_workpiece_safe 异常 (隔离): {e}")
            try:
                db.rollback()
            except Exception:
                pass
            return None

    # =============================================================
    # 终态处理
    # =============================================================

    def _finalize_run(
        self,
        db,
        run: Dict[str, Any],
        flow: Dict[str, Any],
        reason: str,
        timeout_action: Optional[str] = None,
    ) -> None:
        """终态收尾 (锁外调). reason ∈ completed / short_circuited / timeout / aborted."""
        flow_uuid = run["flow_uuid"]

        # 取消 timer (避免后续 timeout 误触)
        with self._lock:
            state = self._in_flight_runs.get(flow_uuid)
            if state:
                t = state.get("timeout_timer")
                if t:
                    try:
                        t.cancel()
                    except Exception:
                        pass
                state["timeout_timer"] = None
                state["status"] = reason

        # fire hook (workpiece_flow_completed 是 returnable, 插件可改 final_result)
        if reason == "timeout":
            hook_results = self._fire_hook_safe(
                "workpiece_flow_timeout", "timer_fire", "post", {
                    "flow_uuid": flow_uuid,
                    "flow_config_id": flow["id"],
                    "flow_name": flow["name"],
                    "serial_no": run["serial_no"],
                    "default_action": timeout_action or flow["timeout_action"],
                    "station_cycle_ids": list(run["station_cycle_ids"]),
                    "station_results": list(run["station_results"]),
                }
            )
            # 插件可 override timeout_action
            override = self._merge_hook_overrides(hook_results, "override_timeout_action")
            effective_action = override or (timeout_action or flow["timeout_action"])

            if effective_action == "force_ng":
                final_result = "NG"
            elif effective_action == "drop":
                final_result = None  # NULL → 不推 MES
            else:  # alarm_only
                final_result = None
            run["final_result"] = final_result

        elif reason == "short_circuited":
            self._fire_hook_safe(
                "workpiece_flow_short_circuit", "ng_station", "post", {
                    "flow_uuid": flow_uuid,
                    "flow_config_id": flow["id"],
                    "flow_name": flow["name"],
                    "serial_no": run["serial_no"],
                    "station_cycle_ids": list(run["station_cycle_ids"]),
                    "station_results": list(run["station_results"]),
                }
            )

        # workpiece_flow_completed hook (所有终态都触发)
        completed_results = self._fire_hook_safe(
            "workpiece_flow_completed", reason, "post", {
                "flow_uuid": flow_uuid,
                "flow_config_id": flow["id"],
                "flow_name": flow["name"],
                "serial_no": run["serial_no"],
                "workpiece_id": run["workpiece_id"],
                "reason": reason,
                "final_result": run["final_result"],
                "station_cycle_ids": list(run["station_cycle_ids"]),
                "station_results": list(run["station_results"]),
            }
        )
        # 插件可 override final_result (returnable hook)
        override_final = self._merge_hook_overrides(completed_results, "override_final_result")
        if override_final in ("OK", "NG"):
            run["final_result"] = override_final

        # 写 DB 终态
        self._update_run_terminal(
            db,
            run["run_db_id"],
            status=reason,
            final_result=run["final_result"],
            station_results=run["station_results"],
            station_cycle_ids=run["station_cycle_ids"],
        )

        # alarm 联动
        if run["final_result"] == "NG":
            # 取最后一个有 cycle 的 channel 报警 (NG 工位的负责人)
            ng_channel = None
            for idx, r in enumerate(run["station_results"]):
                if r == "NG":
                    ng_channel = flow["station_channel_ids"][idx]
                    break
            if ng_channel is None and flow["station_channel_ids"]:
                ng_channel = flow["station_channel_ids"][-1]
            if ng_channel is not None:
                self._fire_alarm_safe("event2", ng_channel)

        # MES push (复用 WorkpieceService.set_result 链路)
        if run["final_result"] in ("OK", "NG") and run["workpiece_id"]:
            self._push_workpiece_result_safe(
                db, run["workpiece_id"], run["final_result"] == "OK"
            )

        # 从 in-flight 清出 (FIFO 队列同步清)
        with self._lock:
            self._in_flight_runs.pop(flow_uuid, None)
            q = self._flow_to_in_flight.get(flow["id"])
            if q:
                try:
                    q.remove(flow_uuid)
                except ValueError:
                    pass

        print(
            f"[WorkpieceFlow][{flow['name']}] finalized: reason={reason} "
            f"final={run['final_result']} serial={run['serial_no']} uuid={flow_uuid[:8]}"
        )

    def _on_workpiece_timeout(self, flow_uuid: str, default_action: str) -> None:
        """Timer 后台线程回调. 不持 db session — 通过 SessionLocal 自取."""
        from backend.db.database import SessionLocal

        with self._lock:
            state = self._in_flight_runs.get(flow_uuid)
            if not state or state["status"] != "in_progress":
                return  # 已终态 / 已清出, 无事可做
            flow = self._flows.get(state["flow_config_id"])
            if not flow:
                return
            run_snapshot = dict(state)
            flow_snapshot = dict(flow)

        db = SessionLocal()
        try:
            self._finalize_run(db, run_snapshot, flow_snapshot, "timeout",
                               timeout_action=default_action)
        except Exception as e:
            print(f"[WorkpieceFlow] _on_workpiece_timeout finalize 异常: {e}")
        finally:
            try:
                db.close()
            except Exception:
                pass

    # =============================================================
    # DB 辅助 (错误吞掉, 防主流程崩)
    # =============================================================

    def _insert_flow_run(
        self,
        db,
        state: Dict[str, Any],
        flow: Dict[str, Any],
    ) -> Optional[int]:
        if db is None:
            return None
        from backend.models.mes_models import WorkpieceFlowRun

        try:
            row = WorkpieceFlowRun(
                flow_config_id=state["flow_config_id"],
                workpiece_id=state.get("workpiece_id"),
                flow_uuid=state["flow_uuid"],
                serial_no=state.get("serial_no"),
                status="in_progress",
                station_cycle_ids=list(state["station_cycle_ids"]),
                station_results=list(state["station_results"]),
                final_result=None,
                trigger_mode=state.get("trigger_mode"),
                trigger_source_id=state.get("trigger_source_id"),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        except Exception as e:
            print(f"[WorkpieceFlow] _insert_flow_run 异常: {e}")
            try:
                db.rollback()
            except Exception:
                pass
            return None

    def _update_run_station_cycles(
        self,
        db,
        run_db_id: Optional[int],
        station_cycle_ids: List[Optional[int]],
    ) -> None:
        if db is None or run_db_id is None:
            return
        from backend.models.mes_models import WorkpieceFlowRun

        try:
            row = db.query(WorkpieceFlowRun).filter(
                WorkpieceFlowRun.id == run_db_id
            ).first()
            if not row:
                return
            row.station_cycle_ids = list(station_cycle_ids)
            db.commit()
        except Exception as e:
            print(f"[WorkpieceFlow] _update_run_station_cycles 异常: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    def _update_run_station_results(
        self,
        db,
        run_db_id: Optional[int],
        station_results: List[Optional[str]],
    ) -> None:
        if db is None or run_db_id is None:
            return
        from backend.models.mes_models import WorkpieceFlowRun

        try:
            row = db.query(WorkpieceFlowRun).filter(
                WorkpieceFlowRun.id == run_db_id
            ).first()
            if not row:
                return
            row.station_results = list(station_results)
            db.commit()
        except Exception as e:
            print(f"[WorkpieceFlow] _update_run_station_results 异常: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    def _update_run_terminal(
        self,
        db,
        run_db_id: Optional[int],
        status: str,
        final_result: Optional[str],
        station_results: List[Optional[str]],
        station_cycle_ids: List[Optional[int]],
    ) -> None:
        if db is None or run_db_id is None:
            return
        from backend.models.mes_models import WorkpieceFlowRun
        from datetime import datetime, timezone

        try:
            row = db.query(WorkpieceFlowRun).filter(
                WorkpieceFlowRun.id == run_db_id
            ).first()
            if not row:
                return
            row.status = status
            row.final_result = final_result
            row.station_results = list(station_results)
            row.station_cycle_ids = list(station_cycle_ids)
            row.completed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as e:
            print(f"[WorkpieceFlow] _update_run_terminal 异常: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    # =============================================================
    # 集成辅助 (错误隔离)
    # =============================================================

    def _push_workpiece_result_safe(self, db, workpiece_id: int, is_good: bool) -> None:
        """调 WorkpieceService.set_result 写工件最终结果, 复用现有 MES 推送链."""
        try:
            from backend.services.workpiece import WorkpieceService
            svc = WorkpieceService()
            svc.set_result(db, workpiece_id, is_good)
        except Exception as e:
            print(f"[WorkpieceFlow] _push_workpiece_result_safe 异常 (隔离): {e}")

    def _fire_alarm_safe(self, event_id: str, channel_id: int) -> None:
        try:
            from backend.api.alarm import alarm_router
            alarm_router.trigger_alarm(event_id, channel_id=channel_id)
        except Exception as e:
            print(f"[WorkpieceFlow] alarm trigger ch{channel_id} 异常 (隔离): {e}")

    def _fire_hook_safe(self, hook_name: str, action: str, phase: str, ctx: dict) -> dict:
        """触发插件 hook. 返回 fire_plugin_hook 聚合后的 dict (按 RETURNABLE_HOOK_FIELDS
        白名单过滤 + priority 排序), 非 returnable hook 返回空 dict.
        """
        try:
            from backend.plugin_system.hook_dispatch import fire_plugin_hook
            return fire_plugin_hook(hook_name, action, phase, ctx) or {}
        except Exception as e:
            print(f"[WorkpieceFlow] hook {hook_name} 异常 (隔离): {e}")
            return {}

    def _merge_hook_overrides(self, hook_results: dict, field_name: str) -> Optional[Any]:
        """从聚合 hook dict 里取 override 字段."""
        if not isinstance(hook_results, dict):
            return None
        v = hook_results.get(field_name)
        return v if v is not None else None

    # =============================================================
    # 内部 helper
    # =============================================================

    @staticmethod
    def _find_station_index(flow: Dict[str, Any], channel_id: int) -> Optional[int]:
        """在 flow.station_channel_ids 里找 channel 的位置 (有序)."""
        stations = flow["station_channel_ids"]
        try:
            return stations.index(channel_id)
        except ValueError:
            return None

    # =============================================================
    # PluginHost / API 查询用
    # =============================================================

    def list_flows(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(f) for f in self._flows.values()]

    def get_flow(self, flow_config_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            f = self._flows.get(flow_config_id)
            return dict(f) if f else None

    def list_in_flight(self, flow_config_id: int) -> List[Dict[str, Any]]:
        """返回某 flow 的当前 in-flight 工件状态快照 (诊断 / Monitor UI 用)."""
        with self._lock:
            out = []
            for u in self._flow_to_in_flight.get(flow_config_id, []):
                run = self._in_flight_runs.get(u)
                if run and run.get("status") == "in_progress":
                    out.append({
                        "flow_uuid": u,
                        "serial_no": run.get("serial_no"),
                        "current_station_index": run.get("current_station_index"),
                        "station_cycle_ids": list(run.get("station_cycle_ids", [])),
                        "station_results": list(run.get("station_results", [])),
                        "started_at": run.get("started_at"),
                        "trigger_mode": run.get("trigger_mode"),
                    })
            return out

    def is_channel_in_flow(self, channel_id: int) -> bool:
        with self._lock:
            return channel_id in self._channel_to_flow

    def get_flow_id_for_channel(self, channel_id: int) -> Optional[int]:
        with self._lock:
            return self._channel_to_flow.get(channel_id)
