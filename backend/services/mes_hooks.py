"""
MES 检测引擎回调管理器

通过明确的 Hook 点与 source.py 的 VideoSourceManager 交互:
- on_scan_received: 扫码器收到数据
- on_cycle_start:   Cycle 开始 (commit 后)
- on_cycle_end:     Cycle 结束 (commit 后)
- on_session_start: Session 开始 (commit 后)
- on_session_end:   Session 结束 (commit 后)

设计原则:
1. 所有 Hook 在 try/except 中执行, 异常只记日志不影响检测
2. 使用独立 db session, 不共享检测引擎的 session
3. 异步队列消费, 不阻塞检测帧率
"""
import threading
import queue
import time
import traceback
from datetime import datetime
from typing import Optional

from backend.db.database import SessionLocal
from backend.services.work_order import WorkOrderService
from backend.services.workpiece import WorkpieceService
from backend.services.defect import DefectService


class MESHookManager:

    def __init__(self):
        self.enabled = False
        self._work_order_svc = WorkOrderService()
        self._workpiece_svc = WorkpieceService()
        self._defect_svc = DefectService()

        # channel_id -> workpiece_id (最近扫码但尚未开始检测的工件)
        self._pending_workpiece: dict[int, int] = {}
        # channel_id -> [workpiece_id, ...] (queue 模式下的待检队列)
        self._pending_queue: dict[int, list] = {}
        # channel_id -> order_id (当前活跃工单)
        self._active_orders: dict[int, int] = {}
        # channel_id -> workpiece_id (当前正在检测的工件)
        self._inspecting_workpiece: dict[int, int] = {}
        # channel_id -> {serial_no, workpiece_id, timestamp} (最近扫码事件)
        self._last_scan_event: dict[int, dict] = {}
        # channel_id -> {workpiece_id, cycle_id, timestamp} (manual rebind 提示)
        self._rebind_prompt: dict[int, dict] = {}

        self._task_queue: queue.Queue = queue.Queue(maxsize=500)
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self):
        """启动后台工作线程"""
        self.enabled = True
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="mes-hook-worker"
        )
        self._worker_thread.start()
        print("[MES] Hook 管理器已启动", flush=True)

    def stop(self):
        """停止后台工作线程"""
        self.enabled = False
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5)
        print("[MES] Hook 管理器已停止", flush=True)

    def _worker_loop(self):
        """后台工作线程: 从队列消费任务, 批量处理"""
        while not self._stop_event.is_set():
            try:
                task = self._task_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            db = SessionLocal()
            try:
                func, args, kwargs = task
                func(db, *args, **kwargs)
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"[MES] Hook 任务执行失败: {e}", flush=True)
                traceback.print_exc()
            finally:
                db.close()

    def _enqueue(self, func, *args, **kwargs):
        """将任务放入队列, 队列满则丢弃 (不阻塞检测)"""
        if not self.enabled:
            return
        try:
            self._task_queue.put_nowait((func, args, kwargs))
        except queue.Full:
            print("[MES] 任务队列已满, 丢弃任务", flush=True)

    # ====== 5 个核心 Hook ======

    def on_scan_received(self, channel_id: int, serial_no: str,
                         raw_data: str, project_id: int,
                         device_id: int = None):
        """扫码器收到数据后调用 (ScannerService -> 此方法)"""
        self._enqueue(
            self._handle_scan, channel_id, serial_no, raw_data,
            project_id, device_id
        )

    def on_cycle_start(self, channel_id: int, cycle_id: int,
                       session_id: int, project_id: int):
        """source.py start_cycle() commit 成功后调用"""
        self._enqueue(
            self._handle_cycle_start, channel_id, cycle_id,
            session_id, project_id
        )

    def on_cycle_end(self, channel_id: int, cycle_id: int,
                     is_good: bool, event_name: str = None,
                     result_reason: str = None, duration: float = None,
                     step_sequence: list = None, project_id: int = None):
        """source.py end_cycle() commit 成功后调用"""
        self._enqueue(
            self._handle_cycle_end, channel_id, cycle_id,
            is_good, event_name, result_reason, duration,
            step_sequence, project_id
        )

    def on_session_start(self, channel_id: int, session_id: int,
                         project_id: int):
        """source.py start_session() 后调用"""
        self._enqueue(
            self._handle_session_start, channel_id, session_id, project_id
        )

    def on_session_end(self, channel_id: int, session_id: int):
        """source.py end_session() 后调用"""
        self._enqueue(
            self._handle_session_end, channel_id, session_id
        )

    def on_channel_removed(self, channel_id: int):
        """工位被移除（降工位）时调用，清理该通道在所有 dict 里的残留状态。
        避免降工位再升工位时，新通道的第一周期被残留数据命中（比如 had_workpiece 误判）。
        v2.7.2: 修复"切换工位后新通道未绑码告警不弹"。"""
        removed = []
        for name, d in (
            ("_pending_workpiece", self._pending_workpiece),
            ("_pending_queue", self._pending_queue),
            ("_active_orders", self._active_orders),
            ("_inspecting_workpiece", self._inspecting_workpiece),
            ("_last_scan_event", self._last_scan_event),
            ("_rebind_prompt", self._rebind_prompt),
        ):
            if channel_id in d:
                d.pop(channel_id, None)
                removed.append(name)
        if removed:
            print(f"[MES] ch{channel_id} 被移除，已清理残留: {removed}", flush=True)

    # ====== 获取当前状态 (供 get_detection_results 使用, 需线程安全) ======

    def has_pending_workpiece(self, channel_id: int) -> bool:
        """同步检查该工位是否有待检工件（供 start_cycle 阻止无码周期）"""
        return channel_id in self._pending_workpiece

    def _conn_serves_channel(self, conn, channel_id: int) -> bool:
        """判断一个扫码器连接是否服务于指定通道（含 broadcast）"""
        channels = conn.broadcast_channels if conn.broadcast_channels else [conn.channel_id]
        return channel_id in channels

    def is_scan_required(self, channel_id: int) -> bool:
        """查询该工位是否要求先扫码才能开始周期"""
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                if self._conn_serves_channel(conn, channel_id) and conn.scan_required:
                    return True
        except Exception as e:
            print(f"[MES] is_scan_required 异常: {e}", flush=True)
        return False

    def is_warn_no_barcode(self, channel_id: int) -> bool:
        """查询该工位是否启用无码告警"""
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                if self._conn_serves_channel(conn, channel_id) and conn.warn_no_barcode:
                    return True
        except Exception as e:
            print(f"[MES] is_warn_no_barcode 异常: {e}", flush=True)
        return False

    def get_last_scan_event(self, channel_id: int) -> Optional[dict]:
        """返回最近扫码事件（供轮询接口消费，前端去重）"""
        return self._last_scan_event.get(channel_id)

    def get_current_workpiece(self, channel_id: int) -> Optional[dict]:
        """返回当前工位的工件信息 (用于 Monitor 显示)"""
        wp_id = (self._inspecting_workpiece.get(channel_id)
                 or self._pending_workpiece.get(channel_id))
        if not wp_id:
            return None
        db = SessionLocal()
        try:
            wp = self._workpiece_svc.get_by_id(db, wp_id)
            if not wp:
                return None
            return {
                "id": wp.id,
                "serial_no": wp.serial_no,
                "status": wp.status,
                "inspection_count": wp.inspection_count,
                "order_id": wp.order_id,
            }
        except Exception as e:
            # 轮询路径：若工件真的存在却查失败，说明 DB/ORM 有问题，要能看到
            print(f"[MES] get_current_workpiece(ch{channel_id}, wp={wp_id}) 失败: {e}",
                  flush=True)
            return None
        finally:
            db.close()

    def get_active_order(self, channel_id: int) -> Optional[dict]:
        """返回当前工位的活跃工单信息"""
        order_id = self._active_orders.get(channel_id)
        if not order_id:
            return None
        db = SessionLocal()
        try:
            return self._work_order_svc.get_order_summary(db, order_id)
        except Exception as e:
            print(f"[MES] get_active_order(ch{channel_id}, order={order_id}) 失败: {e}",
                  flush=True)
            return None
        finally:
            db.close()

    # ====== 内部处理方法 (在工作线程中执行, 接受 db session) ======

    def _get_duplicate_scan_action(self, channel_id: int) -> str:
        """获取该工位的重复扫码策略"""
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                if self._conn_serves_channel(conn, channel_id):
                    return conn.duplicate_scan_action or "overwrite"
        except Exception:
            pass
        return "overwrite"

    def _get_bind_timing(self, channel_id: int) -> str:
        """获取该工位的绑定时机"""
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                if self._conn_serves_channel(conn, channel_id):
                    return conn.bind_timing or "mid_cycle"
        except Exception:
            pass
        return "mid_cycle"

    def _get_current_cycle_id(self, channel_id: int) -> Optional[int]:
        """获取该工位当前活跃周期 ID"""
        try:
            from backend.api.channel_manager import channel_manager
            mgr = channel_manager.get(channel_id)
            return mgr.current_cycle_id
        except Exception:
            return None

    def _get_current_session_id(self, channel_id: int) -> Optional[int]:
        """获取该工位当前会话 ID"""
        try:
            from backend.api.channel_manager import channel_manager
            mgr = channel_manager.get(channel_id)
            return mgr.current_session_id
        except Exception:
            return None

    def _get_rebind_mode(self, channel_id: int) -> str:
        """获取该工位的误检重绑策略"""
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                if self._conn_serves_channel(conn, channel_id):
                    return conn.rebind_mode or "rescan"
        except Exception:
            pass
        return "rescan"

    def _get_ok_rescan_cooldown(self, channel_id: int) -> int:
        """获取该工位的"OK 后同码冷却秒数"配置。0 表示关闭。"""
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                if self._conn_serves_channel(conn, channel_id):
                    return int(getattr(conn, "ok_rescan_cooldown_sec", 0) or 0)
        except Exception:
            pass
        return 0

    def get_rebind_prompt(self, channel_id: int) -> Optional[dict]:
        """获取 manual rebind 弹窗数据"""
        return self._rebind_prompt.get(channel_id)

    def resolve_rebind(self, channel_id: int, action: str):
        """处理 manual rebind 选择（continue=继续当前工件，new=扫新工件）"""
        prompt = self._rebind_prompt.pop(channel_id, None)
        if not prompt:
            return
        if action == "continue":
            wp_id = prompt["workpiece_id"]
            from backend.db.database import SessionLocal
            db = SessionLocal()
            try:
                from backend.models.mes_models import Workpiece
                wp = db.query(Workpiece).filter(Workpiece.id == wp_id).first()
                if wp:
                    wp.status = "queued"
                    db.commit()
                self._pending_workpiece[channel_id] = wp_id
                print(f"[MES] 手动重绑: 工件#{wp_id} 放回待检", flush=True)
            finally:
                db.close()

    def _handle_scan(self, db, channel_id: int, serial_no: str,
                     raw_data: str, project_id: int, device_id: int = None):
        """处理扫码事件"""
        from backend.models.mes_models import ScanLog

        # 同码二次扫抑制：上次检测合格 & 距完成时间 < 冷却秒数 → 静默丢弃
        # 用于过滤搬运过程中扫码器误扫到已合格工件的情况，避免脏数据。
        cooldown = self._get_ok_rescan_cooldown(channel_id)
        if cooldown > 0:
            existing = self._workpiece_svc.find_by_serial(db, serial_no, project_id)
            if (existing and existing.status == "ok"
                    and existing.last_inspect_at is not None):
                elapsed = (datetime.now() - existing.last_inspect_at).total_seconds()
                if 0 <= elapsed < cooldown:
                    scan_log = ScanLog(
                        device_id=device_id, channel_id=channel_id,
                        raw_data=raw_data, parsed_serial=serial_no,
                        workpiece_id=existing.id, success=False,
                        error_msg=f"OK冷却期内重复扫码忽略 ({elapsed:.1f}s/{cooldown}s)",
                    )
                    db.add(scan_log)
                    print(f"[MES] 扫码冷却过滤: {serial_no} 工件#{existing.id} "
                          f"OK后{elapsed:.1f}s (冷却{cooldown}s, 工位{channel_id})",
                          flush=True)
                    return

        action = self._get_duplicate_scan_action(channel_id)

        if action == "reject" and channel_id in self._pending_workpiece:
            print(f"[MES] 扫码拒绝(已有待检工件): {serial_no} (工位{channel_id})", flush=True)
            scan_log = ScanLog(
                device_id=device_id, channel_id=channel_id,
                raw_data=raw_data, parsed_serial=serial_no,
                success=False, error_msg="已有待检工件，扫码被拒绝",
            )
            db.add(scan_log)
            return

        wp = self._workpiece_svc.register(
            db, serial_no, project_id,
            raw_barcode=raw_data,
            channel_id=channel_id,
            scan_source="scanner",
            scan_device_id=device_id,
        )

        order_id = self._active_orders.get(channel_id)
        if order_id and not wp.order_id:
            wp.order_id = order_id
            db.flush()

        scan_log = ScanLog(
            device_id=device_id,
            channel_id=channel_id,
            raw_data=raw_data,
            parsed_serial=serial_no,
            workpiece_id=wp.id,
            success=True,
        )
        db.add(scan_log)

        if action == "queue":
            if channel_id not in self._pending_queue:
                self._pending_queue[channel_id] = []
            self._pending_queue[channel_id].append(wp.id)
            if channel_id not in self._pending_workpiece:
                self._pending_workpiece[channel_id] = wp.id
            print(f"[MES] 扫码入队: {serial_no} -> 工件#{wp.id} (工位{channel_id}, 队列长度{len(self._pending_queue[channel_id])})", flush=True)
        else:
            self._pending_workpiece[channel_id] = wp.id
            print(f"[MES] 扫码登记: {serial_no} -> 工件#{wp.id} (工位{channel_id})", flush=True)

        self._last_scan_event[channel_id] = {
            "serial_no": serial_no,
            "workpiece_id": wp.id,
            "timestamp": time.time(),
        }

        # mid_cycle: 如果当前有活跃周期且未绑定工件，立即绑定
        if self._get_bind_timing(channel_id) == "mid_cycle":
            if channel_id not in self._inspecting_workpiece:
                current_cid = self._get_current_cycle_id(channel_id)
                if current_cid:
                    consumed_id = self._pending_workpiece.pop(channel_id, None)
                    if consumed_id:
                        self._workpiece_svc.mark_inspecting(db, consumed_id)
                        session_id = self._get_current_session_id(channel_id)
                        self._workpiece_svc.link_to_cycle(
                            db, consumed_id, current_cid,
                            session_id=session_id, channel_id=channel_id
                        )
                        self._inspecting_workpiece[channel_id] = consumed_id
                        print(f"[MES] 中途绑定: 工件#{consumed_id} -> Cycle#{current_cid} (工位{channel_id})", flush=True)

    def _handle_cycle_start(self, db, channel_id: int, cycle_id: int,
                            session_id: int, project_id: int):
        """Cycle 开始: 关联待检工件"""
        wp_id = self._pending_workpiece.pop(channel_id, None)
        if not wp_id:
            return

        # queue 模式：消费队列头部，将下一个设为 pending
        q = self._pending_queue.get(channel_id)
        if q:
            if wp_id in q:
                q.remove(wp_id)
            if q:
                self._pending_workpiece[channel_id] = q[0]

        self._workpiece_svc.mark_inspecting(db, wp_id)
        insp = self._workpiece_svc.link_to_cycle(
            db, wp_id, cycle_id, session_id=session_id, channel_id=channel_id
        )

        self._inspecting_workpiece[channel_id] = wp_id

        order_id = self._active_orders.get(channel_id)
        if order_id:
            from backend.models.models import DetectionCycle
            cycle = db.query(DetectionCycle).filter(
                DetectionCycle.id == cycle_id
            ).first()
            if cycle and hasattr(cycle, 'order_id'):
                cycle.order_id = order_id
                db.flush()

        print(f"[MES] Cycle#{cycle_id} 关联工件#{wp_id}", flush=True)

    def _handle_cycle_end(self, db, channel_id: int, cycle_id: int,
                          is_good: bool, event_name: str, result_reason: str,
                          duration: float, step_sequence: list,
                          project_id: int):
        """Cycle 结束: 更新工件状态, 记录缺陷, 更新工单"""
        wp_id = self._inspecting_workpiece.pop(channel_id, None)
        if not wp_id:
            print(f"[MES] Cycle#{cycle_id} ch{channel_id} 结束但未绑定工件 "
                  f"(_inspecting_workpiece 为空) → 跳过 MES/集群分发", flush=True)
            return

        self._workpiece_svc.set_result(db, wp_id, is_good, cycle_id)
        self._workpiece_svc.update_inspection_result(
            db, wp_id, cycle_id,
            result="ok" if is_good else "ng",
            event_name=event_name,
            result_reason=result_reason,
            duration=duration,
        )

        if not is_good:
            wp = self._workpiece_svc.get_by_id(db, wp_id)
            insp = (
                db.query(self._workpiece_svc.__class__)
                if False else None
            )
            from backend.models.mes_models import WorkpieceInspection
            insp = (
                db.query(WorkpieceInspection)
                .filter(WorkpieceInspection.workpiece_id == wp_id,
                        WorkpieceInspection.cycle_id == cycle_id)
                .first()
            )
            self._defect_svc.auto_record(
                db, wp_id, cycle_id,
                inspection_id=insp.id if insp else None,
                event_name=event_name,
                result_reason=result_reason,
                step_sequence=step_sequence,
                project_id=project_id,
            )

        order_id = self._active_orders.get(channel_id)
        if order_id:
            self._work_order_svc.increment_completed(db, order_id, is_good)
            if self._work_order_svc.check_completion(db, order_id):
                self._work_order_svc.change_status(db, order_id, "completed")
                print(f"[MES] 工单#{order_id} 已自动完成", flush=True)

        print(f"[MES] Cycle#{cycle_id} 结束: {'OK' if is_good else 'NG'} (工件#{wp_id})",
              flush=True)

        # v2.7.9: 在调集群分发之前，先 commit 释放 SQLite 写锁。
        # 否则这个 session 还持有前面 set_result/auto_record/increment_completed 拿到的写锁，
        # 紧接着 receive_station_report() 在新 session 里写 box_aggregations 会 locked 30s+ 重试失败。
        try:
            db.commit()
        except Exception as e:
            import traceback
            print(f"[MES] Cycle#{cycle_id} 预提交失败（释放写锁）: {e}\n{traceback.format_exc()}",
                  flush=True)
            db.rollback()

        # 外部 MES 推送 + 集群汇总
        try:
            from backend.services.mes_gateway import get_mes_gateway
            gw = get_mes_gateway()
            ctx = gw.build_context_from_cycle(
                db, cycle_id,
                workpiece_id=wp_id,
                order_id=order_id,
                is_good=is_good,
                event_name=event_name,
                result_reason=result_reason,
                duration=duration,
                step_sequence=step_sequence,
                project_id=project_id,
            )

            skip_cycle_push = self._cluster_dispatch(
                db, ctx, channel_id, is_good, event_name,
            )

            if not skip_cycle_push:
                gw.dispatch("cycle_end", ctx, channel_id)
        except Exception as e:
            import traceback
            print(f"[MES] 外部推送(cycle_end)失败: {e}\n{traceback.format_exc()}",
                  flush=True)

        rebind = self._get_rebind_mode(channel_id)
        if rebind == "auto_rebind" and not is_good:
            from backend.models.mes_models import Workpiece
            wp_obj = db.query(Workpiece).filter(Workpiece.id == wp_id).first()
            if wp_obj:
                wp_obj.status = "queued"
                db.commit()
            self._pending_workpiece[channel_id] = wp_id
            print(f"[MES] 自动重绑: 工件#{wp_id} 放回待检 (auto_rebind)", flush=True)
        elif rebind == "manual" and not is_good:
            self._rebind_prompt[channel_id] = {
                "workpiece_id": wp_id,
                "cycle_id": cycle_id,
                "timestamp": time.time(),
            }
            print(f"[MES] 等待手动选择: 工件#{wp_id} (manual)", flush=True)

    def _cluster_dispatch(self, db, cycle_context: dict, channel_id: int,
                          is_good: bool, event_name: str) -> bool:
        """集群模式：主机收集本地数据，从机上报给主机。
        返回 True 表示应跳过本次 cycle_end 的直接 MES 推送（wait_all 模式）。
        """
        try:
            from backend.services.cluster_collector import get_cluster_collector
            collector = get_cluster_collector()
            config = collector.get_config(db)

            if not config.get("enabled"):
                print(f"[Cluster/Dispatch] ch{channel_id} 跳过: 集群未启用", flush=True)
                return False

            box_serial = cycle_context.get("workpiece", {}).get("serial_no")
            if not box_serial:
                wp_preview = cycle_context.get("workpiece", {})
                print(f"[Cluster/Dispatch] ch{channel_id} 跳过: cycle_context 里没有 workpiece.serial_no "
                      f"(workpiece={wp_preview})", flush=True)
                return False

            station_id = config["station_id"]
            role = config["role"]
            sync_mode = config.get("sync_mode", "wait_all")

            # 优先使用"通道→站点"映射表；命中则直接采用映射值（不再拼后缀），
            # 这样多视觉通道可以自由指定各自归属哪个站点（含两路都归到同一站点的场景）。
            ch_map = config.get("channel_station_map") or {}
            mapped = ch_map.get(str(channel_id))
            if mapped:
                station_id = mapped
            else:
                from backend.api.channel_manager import channel_manager
                if channel_manager.channel_count > 1:
                    station_id = f"{station_id}-{channel_id}"

            print(f"[Cluster/Dispatch] ch{channel_id} role={role} station={station_id} "
                  f"serial={box_serial} is_good={is_good} sync={sync_mode}", flush=True)

            if role == "master":
                result = collector.receive_station_report(
                    station_id=station_id,
                    box_serial=box_serial,
                    cycle_context=cycle_context,
                    source_address=f"local:{channel_id}",
                    channel_id=channel_id,
                    is_good=is_good,
                    event_name=event_name,
                )
                print(f"[Cluster/Dispatch] master 本地入库结果: {result}", flush=True)
                return sync_mode == "wait_all"
            elif role == "slave":
                master_url = config.get("master_url")
                if not master_url:
                    print(f"[Cluster/Dispatch] slave 跳过上报: master_url 为空", flush=True)
                    return False
                result = collector.report_to_master(
                    cycle_context=cycle_context,
                    box_serial=box_serial,
                    station_id=station_id,
                    master_url=master_url,
                    is_good=is_good,
                    event_name=event_name,
                )
                print(f"[Cluster/Dispatch] slave 上报 {master_url} 结果: {result}", flush=True)
                return sync_mode == "wait_all"
            else:
                print(f"[Cluster/Dispatch] ch{channel_id} 未识别 role={role}，跳过", flush=True)
        except Exception as e:
            import traceback
            print(f"[Cluster/Dispatch] ch{channel_id} 集群分发异常: {e}\n{traceback.format_exc()}",
                  flush=True)
        return False

    def _handle_session_start(self, db, channel_id: int, session_id: int,
                              project_id: int):
        """Session 开始: 查找活跃工单"""
        order = self._work_order_svc.get_active_order(db, project_id)
        if order:
            self._active_orders[channel_id] = order.id
            from backend.models.models import DetectionSession
            session = db.query(DetectionSession).filter(
                DetectionSession.id == session_id
            ).first()
            if session and hasattr(session, 'order_id'):
                session.order_id = order.id
                db.flush()
            print(f"[MES] Session#{session_id} 关联工单 {order.order_no}", flush=True)

    def _handle_session_end(self, db, channel_id: int, session_id: int):
        """Session 结束: 清理状态"""
        self._pending_workpiece.pop(channel_id, None)
        self._pending_queue.pop(channel_id, None)
        self._inspecting_workpiece.pop(channel_id, None)
        print(f"[MES] Session#{session_id} 结束, 清理工位{channel_id}状态", flush=True)

        # 外部 MES 推送
        try:
            from backend.services.mes_gateway import get_mes_gateway
            gw = get_mes_gateway()
            ctx = gw.build_context_from_session(db, session_id)
            gw.dispatch("session_end", ctx, channel_id)
        except Exception as e:
            import traceback
            print(f"[MES] 外部推送(session_end)失败: {e}\n{traceback.format_exc()}",
                  flush=True)


# 全局单例
_mes_hook_instance: Optional[MESHookManager] = None


def get_mes_hook() -> MESHookManager:
    global _mes_hook_instance
    if _mes_hook_instance is None:
        _mes_hook_instance = MESHookManager()
    return _mes_hook_instance
