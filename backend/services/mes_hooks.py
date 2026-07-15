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
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

from backend.core.config import DATA_DIR
from backend.db.database import SessionLocal
from backend.services.work_order import WorkOrderService
from backend.services.workpiece import WorkpieceService
from backend.services.defect import DefectService
from backend.core import debug_center


class MESHookManager:

    _MAX_PENDING_QUEUE = 500  # B5 queue 模式待绑队列上限(远高于正常节拍)

    def _enqueue_pending(self, channel_id, workpiece_id):
        """B5: 把工件压入 queue 模式待绑队列, 带 FIFO 上限保护。

        队列到顶(异常: 扫码一直来但检测不消费)时丢最旧 + 告警, 防内存无限涨。
        正常节拍(扫一件检一件)队列基本 0~1, 永不触发上限。
        """
        q = self._pending_queue.setdefault(channel_id, [])
        if len(q) >= self._MAX_PENDING_QUEUE:
            dropped = q.pop(0)
            print(f"[MES] WARN pending-bind queue reached limit {self._MAX_PENDING_QUEUE}, "
                  f"dropping oldest workpiece#{dropped} (ch{channel_id}); "
                  f"check if detection stopped / binding stuck", flush=True)
        q.append(workpiece_id)
        return q

    def __init__(self):
        self.enabled = False
        self._work_order_svc = WorkOrderService()
        self._workpiece_svc = WorkpieceService()
        self._defect_svc = DefectService()

        # channel_id -> workpiece_id (最近扫码但尚未开始检测的工件)
        self._pending_workpiece: dict[int, int] = {}
        # channel_id -> [workpiece_id, ...] (queue 模式下的待检队列)
        self._pending_queue: dict[int, list] = {}
        # B5: queue 模式待绑队列上限。正常节拍下扫一件检一件, 队列基本是 0~1;
        #     设 500 远高于任何正常积压, 只有"扫码一直来但检测一直不消费"
        #     (异常: 检测停了没停扫码 / 绑定卡死) 时才会到顶, 到顶丢最旧并告警,
        #     防止队列无限涨吃内存。正常路径永不触发。
        # channel_id -> order_id (当前活跃工单)
        self._active_orders: dict[int, int] = {}
        # channel_id -> workpiece_id (当前正在检测的工件)
        self._inspecting_workpiece: dict[int, int] = {}
        # channel_id -> {serial_no, workpiece_id, timestamp} (最近扫码事件)
        self._last_scan_event: dict[int, dict] = {}
        # channel_id -> {workpiece_id, cycle_id, timestamp} (manual rebind 提示)
        self._rebind_prompt: dict[int, dict] = {}

        # v3.3.0 码-码闭环结算 (bind_timing="scan_pair") 状态:
        # channel_id -> {"serial_no": str, "wp_id": int, "scanned_at": float}
        # 当前窗口的"开始码"。下一码到达 (≠serial_no) 时触发 settle + 用新码替换。
        # 同码二次扫只触发 toast 提醒, 不更新本字段。
        self._scan_pair_active: dict[int, dict] = {}
        # channel_id -> threading.Timer (超时定时器), scan_pair_max_wait_sec > 0 时启用
        self._scan_pair_timeout_timers: dict[int, "threading.Timer"] = {}
        self._scan_pair_lock = threading.Lock()

        self._task_queue: queue.Queue = queue.Queue(maxsize=500)
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._queue_drop_count = 0
        self._queue_block_count = 0
        self._spill_file = os.path.join(DATA_DIR, "mes_hook_spool.jsonl")
        self._spill_lock = threading.Lock()
        self._spill_write_count = 0
        self._spill_replay_count = 0

        # B1②: 外部 MES 推送并发派发 (默认关). 背景: cycle_end 的外推
        #   gw.dispatch() 自带阻塞式重试 (time.sleep(retry_interval)×retry_count),
        #   跑在单条 mes-hook-worker 线程上; 客户 MES 慢/挂时这一下就把整个 hook 队列
        #   (扫码配对/绑工件/...) 全堵住。开关开 → 把外推甩到"每工位一条"的独立执行器:
        #   同工位推送仍 FIFO 严格保序, 但慢工位不再拖垮 worker / 其他工位; 每工位设
        #   积压上限, 超了丢最新一条 + 告警 (MES 长时间不通时的背压, 防内存涨爆)。
        #   默认关时走原内联路径, 字节级一致。开关存 SystemConfig(mes_async_dispatch),
        #   start() 时读, set_async_dispatch() 实时改运行态。
        self._async_dispatch_enabled = False
        self._gateway_executors: dict = {}          # channel_id -> ThreadPoolExecutor(1)
        self._gateway_pending: dict = {}            # channel_id -> 在途+排队任务数
        self._gateway_exec_lock = threading.Lock()
        self._GATEWAY_MAX_PENDING = 200             # 每工位外推积压上限 (正常 0~1)

        # v3.4.2 "禁用扫码"全局开关 (按工位粒度).
        #   _disabled_channels 里的工位:
        #     • on_scan_received 直接丢码 (扫码器物理已 LOFF, 这里再防御一道)
        #     • is_scan_pair_mode / has_pending_workpiece / get_current_workpiece
        #       全部短路返回 False/None, 让 source 走"项目原生结算"路径
        #       (tracking → all_gone, 容器 → box gone-confirm)
        #   联动语义: 用户在工位 N 切禁用 → 由 ScannerService 解析"所有 broadcast
        #   覆盖 N 的扫码器", 把这些扫码器的全部 broadcast 工位一起加进/移出本集合.
        #   所以集合永远是"完整的扫码器联动闭包".
        self._disabled_channels: set[int] = set()
        self._disable_state_lock = threading.RLock()
        self._disable_state_file = os.path.join(
            DATA_DIR, "scanner_runtime_state.json"
        )

    def start(self):
        """启动后台工作线程"""
        self.enabled = True
        self._stop_event.clear()
        self._load_disabled_state_from_disk()
        self._load_async_dispatch_config()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="mes-hook-worker"
        )
        self._worker_thread.start()
        print("[MES] Hook manager started", flush=True)

    def stop(self):
        """停止后台工作线程"""
        self.enabled = False
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5)
        # B1②: 关停所有每工位外推执行器 (不等在途慢推送, 进程退出本就要走)
        with self._gateway_exec_lock:
            execs = list(self._gateway_executors.values())
            self._gateway_executors.clear()
            self._gateway_pending.clear()
        for ex in execs:
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        print("[MES] Hook manager stopped", flush=True)

    # ==================== B1②: 外部 MES 推送并发派发 (默认关) ====================

    def _load_async_dispatch_config(self):
        """启动时从 SystemConfig(mes_async_dispatch) 读开关; 缺省/异常 → 关。"""
        try:
            db = SessionLocal()
            try:
                from backend.models.models import SystemConfig
                row = db.query(SystemConfig).filter(
                    SystemConfig.key == "mes_async_dispatch").first()
                self._async_dispatch_enabled = bool(
                    row and str(row.value).strip().lower() in ("1", "true", "yes", "on"))
            finally:
                db.close()
        except Exception as e:
            self._async_dispatch_enabled = False
            print(f"[MES] read async-dispatch switch failed (default off): {e}", flush=True)
        print(f"[MES] external push async dispatch: {'ON' if self._async_dispatch_enabled else 'OFF(default)'}",
              flush=True)

    def get_async_dispatch(self) -> bool:
        return bool(self._async_dispatch_enabled)

    def set_async_dispatch(self, enabled: bool):
        """实时改运行态 + 落盘 SystemConfig。关时不主动拆已建执行器(空转无害, stop 统一收)。"""
        self._async_dispatch_enabled = bool(enabled)
        try:
            db = SessionLocal()
            try:
                from backend.models.models import SystemConfig
                row = db.query(SystemConfig).filter(
                    SystemConfig.key == "mes_async_dispatch").first()
                if row is None:
                    row = SystemConfig(key="mes_async_dispatch",
                                       description="外部MES推送并发派发(默认关)")
                    db.add(row)
                row.value = "true" if enabled else "false"
                db.commit()
            finally:
                db.close()
        except Exception as e:
            print(f"[MES] persist async-dispatch switch failed: {e}", flush=True)
        print(f"[MES] external push async dispatch toggled: {'ON' if enabled else 'OFF'}", flush=True)

    def _submit_gateway_dispatch(self, event_type: str, ctx: dict, channel_id):
        """把一次外部 MES 推送甩到"每工位一条"的执行器, 同工位严格保序, 慢工位不拖累他人。
        每工位积压超上限 → 丢最新一条 + 告警 (MES 长时间不通的背压)。"""
        cid = int(channel_id) if channel_id is not None else -1
        with self._gateway_exec_lock:
            pending = self._gateway_pending.get(cid, 0)
            if pending >= self._GATEWAY_MAX_PENDING:
                print(f"[MES] WARN channel {cid} external-push backlog reached limit {self._GATEWAY_MAX_PENDING}, "
                      f"dropping this {event_type} push (MES may be down for long)", flush=True)
                debug_center.dbg("backend.mes", "外推积压丢弃",
                                 f"ch={cid} event={event_type} pending={pending}")
                return
            ex = self._gateway_executors.get(cid)
            if ex is None:
                ex = ThreadPoolExecutor(max_workers=1,
                                        thread_name_prefix=f"mes-gw-ch{cid}")
                self._gateway_executors[cid] = ex
            self._gateway_pending[cid] = pending + 1

        def _run():
            try:
                from backend.services.mes_gateway import get_mes_gateway
                get_mes_gateway().dispatch(
                    event_type, ctx, cid if cid >= 0 else None)
            except Exception as e:
                print(f"[MES] async external push failed ch={cid} event={event_type}: {e}", flush=True)
                debug_center.dbg("backend.mes", "异步外推异常",
                                 f"ch={cid} event={event_type} err={e}")
            finally:
                with self._gateway_exec_lock:
                    self._gateway_pending[cid] = max(
                        0, self._gateway_pending.get(cid, 1) - 1)

        try:
            ex.submit(_run)
        except Exception as e:
            with self._gateway_exec_lock:
                self._gateway_pending[cid] = max(
                    0, self._gateway_pending.get(cid, 1) - 1)
            print(f"[MES] external-push submit failed, fallback inline ch={cid}: {e}", flush=True)
            try:
                from backend.services.mes_gateway import get_mes_gateway
                get_mes_gateway().dispatch(
                    event_type, ctx, cid if cid >= 0 else None)
            except Exception:
                pass

    # ==================== v3.4.2 "禁用扫码"按工位开关 ====================

    def _load_disabled_state_from_disk(self):
        """启动时从 scanner_runtime_state.json 恢复 _disabled_channels.
        文件不存在或解析失败 → 静默回退默认 (空集 = 全部启用)."""
        try:
            if not os.path.exists(self._disable_state_file):
                return
            with open(self._disable_state_file, "r", encoding="utf-8") as f:
                payload = json.load(f) or {}
            chs = payload.get("disabled_channels") or []
            with self._disable_state_lock:
                self._disabled_channels = {
                    int(x) for x in chs if isinstance(x, (int, str))
                    and str(x).lstrip("-").isdigit()
                }
            print(f"[ScannerDisable] restored disabled channels from {self._disable_state_file}: "
                  f"{sorted(self._disabled_channels)}", flush=True)
        except Exception as e:
            print(f"[ScannerDisable] load disable state failed: {e}", flush=True)

    def _save_disabled_state_to_disk(self):
        """把当前 _disabled_channels 写到磁盘. 调用方需保证已持锁."""
        try:
            os.makedirs(os.path.dirname(self._disable_state_file), exist_ok=True)
            payload = {"disabled_channels": sorted(self._disabled_channels)}
            with open(self._disable_state_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ScannerDisable] persist failed: {e}", flush=True)

    def is_channel_scan_disabled(self, channel_id: int) -> bool:
        """守门点: 该工位是否已被用户手动禁用扫码."""
        with self._disable_state_lock:
            return channel_id in self._disabled_channels

    def get_disabled_channels(self) -> list[int]:
        """API 拉状态用. 返回排序后的列表."""
        with self._disable_state_lock:
            return sorted(self._disabled_channels)

    def _compute_linked_channels(self, channel_id: int) -> set[int]:
        """计算与 channel_id 联动的所有工位.

        规则: 找出"主绑或 broadcast 包含 channel_id"的所有扫码器, 把这些
        扫码器的全部 broadcast 工位 (空 broadcast 退化成 [channel_id]) 取并集.
        即"以这个工位为锚, 牵出所有相关扫码器, 再把它们覆盖的工位都拉进来".

        没有任何扫码器配置覆盖 channel_id → 返回 {channel_id} (单工位禁用,
        起码影响自己的 4 个守门点).
        """
        linked: set[int] = {int(channel_id)}
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                chs = (list(conn.broadcast_channels)
                       if conn.broadcast_channels
                       else [conn.channel_id])
                if channel_id in chs:
                    linked.update(int(x) for x in chs)
        except Exception as e:
            print(f"[ScannerDisable] compute linked channels error ch={channel_id}: {e}",
                  flush=True)
        return linked

    def set_channel_disabled(self, channel_id: int,
                              disabled: bool) -> dict:
        """切换某工位的扫码禁用状态.

        语义:
          • disabled=True  → 把 ch 联动闭包加入 _disabled_channels, 同时让
            ScannerService 给覆盖这些 ch 的扫码器发 LOFF + 关 _scanning
          • disabled=False → 反向: 从 _disabled_channels 移除联动闭包,
            ScannerService 对正在检测的工位重发 LON
          • A 方案: 切换瞬间清空 ch (含联动) 上的 scan_pair / pending /
            inspecting 状态, 让 cycle 自然走原生 all_gone 收尾, 不强结算.

        返回 {"disabled_channels": [...], "linked": [...]} 给前端展示.
        """
        ch = int(channel_id)
        linked = self._compute_linked_channels(ch)

        with self._disable_state_lock:
            before = set(self._disabled_channels)
            if disabled:
                self._disabled_channels.update(linked)
            else:
                self._disabled_channels.difference_update(linked)
            after = set(self._disabled_channels)
            changed = (before != after)
            if changed:
                self._save_disabled_state_to_disk()
            snapshot = sorted(self._disabled_channels)

        # A 方案: 禁用瞬间清空联动 ch 的 scan_pair / pending / inspecting
        if disabled:
            for c in linked:
                with self._scan_pair_lock:
                    self._scan_pair_active.pop(c, None)
                self._cancel_scan_pair_timer(c)
                self._pending_workpiece.pop(c, None)
                self._pending_queue.pop(c, None)
                self._inspecting_workpiece.pop(c, None)
                self._last_scan_event.pop(c, None)
                self._rebind_prompt.pop(c, None)

        # 物理操作: 让扫码器灭灯 / 重新亮灯
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            svc.apply_channel_disable_change(linked, disabled)
        except Exception as e:
            print(f"[ScannerDisable] notify ScannerService failed: {e}", flush=True)

        print(f"[ScannerDisable] ch{ch} -> {'disabled' if disabled else 'enabled'} "
              f"(linked {sorted(linked)}, full_set {snapshot}, changed={changed})",
              flush=True)
        return {
            "disabled_channels": snapshot,
            "linked": sorted(linked),
            "changed": changed,
        }

    def _worker_loop(self):
        """后台工作线程: 从队列消费任务, 批量处理"""
        while not self._stop_event.is_set():
            # 优先尝试把落盘的关键事件回放回队列
            self._drain_spill_once(max_items=20)
            try:
                task = self._task_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            db = SessionLocal()
            try:
                func, args, kwargs = task
                if debug_center.is_on("backend.mes"):
                    debug_center.dbg("backend.mes", "hook 队列出队", f"handler={getattr(func, '__name__', '?')} qsize={self._task_queue.qsize()}")
                func(db, *args, **kwargs)
                db.commit()
            except Exception as e:
                db.rollback()
                debug_center.dbg("backend.mes", "worker 任务异常", f"handler={getattr(task[0], '__name__', '?') if task else '?'} err={e}")
                print(f"[MES] Hook task execution failed: {e}", flush=True)
                traceback.print_exc()
            finally:
                db.close()

    @staticmethod
    def _is_spillable_handler(handler_name: str) -> bool:
        return handler_name in {
            "_handle_scan",
            "_handle_cycle_start",
            "_handle_cycle_end",
            "_handle_session_start",
            "_handle_session_end",
        }

    def _spill_task(self, func, args, kwargs) -> bool:
        handler_name = getattr(func, "__name__", "")
        if not self._is_spillable_handler(handler_name):
            return False
        payload = {
            "handler": handler_name,
            "args": list(args),
            "kwargs": kwargs or {},
            "created_at": time.time(),
        }
        try:
            with self._spill_lock:
                os.makedirs(os.path.dirname(self._spill_file), exist_ok=True)
                with open(self._spill_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._spill_write_count += 1
            return True
        except Exception as e:
            print(f"[MES] critical task persist failed: {e}", flush=True)
            return False

    def _drain_spill_once(self, max_items: int = 20):
        if max_items <= 0:
            return
        with self._spill_lock:
            if not os.path.exists(self._spill_file):
                return
            try:
                with open(self._spill_file, "r", encoding="utf-8") as f:
                    lines = [ln.strip() for ln in f if ln.strip()]
            except Exception:
                return

            if not lines:
                try:
                    os.remove(self._spill_file)
                except Exception:
                    pass
                return

            kept_lines = []
            replayed = 0
            idx = 0
            while idx < len(lines):
                line = lines[idx]
                if replayed >= max_items:
                    kept_lines.extend(lines[idx:])
                    break
                try:
                    payload = json.loads(line)
                    handler_name = payload.get("handler")
                    if not self._is_spillable_handler(handler_name):
                        idx += 1
                        continue
                    handler = getattr(self, handler_name, None)
                    if handler is None:
                        idx += 1
                        continue
                    args = payload.get("args", [])
                    kwargs = payload.get("kwargs", {})
                    self._task_queue.put_nowait((handler, tuple(args), kwargs))
                    replayed += 1
                except queue.Full:
                    kept_lines.append(line)
                    kept_lines.extend(lines[idx + 1:])
                    break
                except Exception:
                    # 单行损坏直接跳过，避免整份补偿文件阻塞
                    pass
                idx += 1

            try:
                if kept_lines:
                    with open(self._spill_file, "w", encoding="utf-8") as f:
                        f.write("\n".join(kept_lines) + "\n")
                else:
                    os.remove(self._spill_file)
            except Exception:
                pass

            if replayed:
                self._spill_replay_count += replayed
                print(f"[MES] replayed {replayed} persisted critical tasks "
                      f"(replayed_total={self._spill_replay_count}, left={len(kept_lines)})",
                      flush=True)

    def _enqueue(self, func, *args, critical: bool = True, **kwargs):
        """将任务放入队列。

        - critical=True: 关键业务事件（scan/cycle/session）优先保证入队；
          队列满时会短暂等待而不是立即丢弃。
        - critical=False: 非关键事件仍可快速失败，避免拖慢主流程。
        """
        if not self.enabled:
            return
        try:
            self._task_queue.put_nowait((func, args, kwargs))
        except queue.Full:
            # B1①: 队列满时绝不阻塞调用方(结算/检测热路径)。
            # 原来 critical 会 put(timeout=0.8) 阻塞最多 0.8s, 队列长期满时
            # 每个周期都卡 0.8s, 直接拖垮结算节拍。改为: 关键任务立刻落盘
            # (后台 worker 恢复后回放, 不丢业务), 非关键直接丢弃, 都只计数, 立即返回。
            if critical:
                self._queue_block_count += 1  # 复用为"满队列遭遇次数"计数
            spilled = False
            if critical:
                spilled = self._spill_task(func, args, kwargs)
            self._queue_drop_count += 1
            debug_center.dbg("backend.mes", "hook 队列满丢任务", f"handler={getattr(func, '__name__', '?')} critical={critical} spilled={spilled} dropped={self._queue_drop_count}")
            print(f"[MES] task queue full, task dropped "
                  f"(critical={critical}, spilled={spilled}, dropped={self._queue_drop_count}, qsize={self._task_queue.qsize()})",
                  flush=True)

    # ====== 5 个核心 Hook ======

    def on_scan_received(self, channel_id: int, serial_no: str,
                         raw_data: str, project_id: int,
                         device_id: int = None):
        """扫码器收到数据后调用 (ScannerService -> 此方法)"""
        # v3.4.2 守门点: 该工位被用户手动禁用扫码 → 直接丢码 (扫码器物理已 LOFF,
        # 这里再防御一道, 防止串口残留字节意外触发).
        if self.is_channel_scan_disabled(channel_id):
            print(f"[ScannerDisable] ch{channel_id} disabled, dropping scan "
                  f"(serial={serial_no})", flush=True)
            return
        self._enqueue(
            self._handle_scan, channel_id, serial_no, raw_data,
            project_id, device_id, critical=True
        )

    def on_cycle_start(self, channel_id: int, cycle_id: int,
                       session_id: int, project_id: int):
        """source.py start_cycle() commit 成功后调用"""
        self._enqueue(
            self._handle_cycle_start, channel_id, cycle_id,
            session_id, project_id, critical=True
        )

    def on_cycle_end(self, channel_id: int, cycle_id: int,
                     is_good: bool, event_name: str = None,
                     result_reason: str = None, duration: float = None,
                     step_sequence: list = None, project_id: int = None):
        """source.py end_cycle() commit 成功后调用"""
        if debug_center.is_on("backend.mes"):
            debug_center.dbg("backend.mes", "cycle_end 入队", f"channel={channel_id} cycle={cycle_id} is_good={is_good} event={event_name or '-'}")
        self._enqueue(
            self._handle_cycle_end, channel_id, cycle_id,
            is_good, event_name, result_reason, duration,
            step_sequence, project_id, critical=True
        )

    def on_session_start(self, channel_id: int, session_id: int,
                         project_id: int):
        """source.py start_session() 后调用"""
        self._enqueue(
            self._handle_session_start, channel_id, session_id, project_id, critical=True
        )

    def on_session_end(self, channel_id: int, session_id: int):
        """source.py end_session() 后调用"""
        self._enqueue(
            self._handle_session_end, channel_id, session_id, critical=True
        )

    def on_external_order_changed(self, channel_id: int, session_id: int,
                                  project_id: int, allow_replace: bool = False):
        """入站开工建单/顶替后, 给检测已在跑的工位回填活跃工单 (v3.38 川南反馈)。

        没有它, 会话开始早于建单的工位永远挂不上新单:
        监控页四要素不显示、周期也不计入该任务, 要停一次检测才生效。
        """
        self._enqueue(self._handle_rebind_active_order, channel_id, session_id,
                      project_id, allow_replace, critical=True)

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
            ("_scan_pair_active", self._scan_pair_active),
        ):
            if channel_id in d:
                d.pop(channel_id, None)
                removed.append(name)
        # v3.3.0 关闭遗留的 scan_pair 超时定时器
        self._cancel_scan_pair_timer(channel_id)
        if removed:
            print(f"[MES] ch{channel_id} removed, cleaned residuals: {removed}", flush=True)

    # ====== 获取当前状态 (供 get_detection_results 使用, 需线程安全) ======

    def has_pending_workpiece(self, channel_id: int) -> bool:
        """同步检查该工位是否有待检工件（供 start_cycle 阻止无码周期）"""
        # v3.4.2 守门点: 工位禁用 → 假装总有码, 让 start_cycle 不再因"未扫码"
        # 阻塞 / 弹警告. (返回 True 表示"有码", source 那边 cycle 照常起.)
        if self.is_channel_scan_disabled(channel_id):
            return True
        return channel_id in self._pending_workpiece

    def _conn_serves_channel(self, conn, channel_id: int) -> bool:
        """判断一个扫码器连接是否服务于指定通道（含 broadcast）"""
        channels = conn.broadcast_channels if conn.broadcast_channels else [conn.channel_id]
        return channel_id in channels

    def is_scan_required(self, channel_id: int) -> bool:
        """查询该工位是否要求先扫码才能开始周期"""
        # v3.4.2 守门点: 工位禁用 → 假装不需要扫码, source 不阻塞 cycle 起新.
        if self.is_channel_scan_disabled(channel_id):
            return False
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                if self._conn_serves_channel(conn, channel_id) and conn.scan_required:
                    return True
        except Exception as e:
            print(f"[MES] is_scan_required error: {e}", flush=True)
        return False

    def is_warn_no_barcode(self, channel_id: int) -> bool:
        """查询该工位是否启用无码告警"""
        # v3.4.2 守门点: 工位禁用 → 不再弹"未扫码"警告.
        if self.is_channel_scan_disabled(channel_id):
            return False
        # v3.5.2 守门点: 系统中没有任何扫码器（连虚拟扫码器都没有）→ 也不再弹.
        if not self.has_any_scanner_present():
            return False
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                if self._conn_serves_channel(conn, channel_id) and conn.warn_no_barcode:
                    return True
        except Exception as e:
            print(f"[MES] is_warn_no_barcode error: {e}", flush=True)
        return False

    def has_any_scanner_present(self) -> bool:
        """系统中是否注册了至少一个扫码器（含虚拟扫码器）。

        用于 Monitor 端"未绑码"提示的全局守门：连一个扫码器条目都没有
        意味着用户根本不打算用扫码功能，不应再弹"⚠ 未绑码"。

        判定来源 = ScannerService._connections（含数据库 enabled 的扫码器
        + 用户在 WMax 面板启动的虚拟设备 device_id=-999）。
        """
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            return bool(svc._connections)
        except Exception as e:
            print(f"[MES] has_any_scanner_present error: {e}", flush=True)
            # 出现异常时保守地认为"有扫码器"，维持原有提示行为，避免误屏蔽
            return True

    def get_last_scan_event(self, channel_id: int) -> Optional[dict]:
        """返回最近扫码事件（供轮询接口消费，前端去重）"""
        return self._last_scan_event.get(channel_id)

    def get_current_workpiece(self, channel_id: int) -> Optional[dict]:
        """返回当前工位的工件信息 (用于 Monitor 显示)"""
        # v3.4.2 守门点: 工位禁用 → 不返回工件, 前端码栏自然空, 也不会显示
        # "已扫码 / 未扫码" 状态 (前端结合 store 里的 disabled 标志直接整栏隐藏).
        if self.is_channel_scan_disabled(channel_id):
            return None
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
            print(f"[MES] get_current_workpiece(ch{channel_id}, wp={wp_id}) failed: {e}",
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
            print(f"[MES] get_active_order(ch{channel_id}, order={order_id}) failed: {e}",
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
            pass  # 高频路径：连接未就绪/字段缺失时回退默认，避免刷屏
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
            pass  # 高频路径：连接未就绪/字段缺失时回退默认
        return "mid_cycle"

    # ============== v3.3.0 码-码闭环结算 ==============

    def is_scan_pair_mode(self, channel_id: int) -> bool:
        """该工位是否处于 bind_timing='scan_pair' 模式 (码-码闭环结算)."""
        # v3.4.2 守门点: 禁用扫码 → 假装非 scan_pair, 让 source 走原生 all_gone
        # / 容器 gone-confirm 结算路径, 不再被 scan_pair_active 拦截 settle.
        if self.is_channel_scan_disabled(channel_id):
            return False
        return self._get_bind_timing(channel_id) == "scan_pair"

    def _get_scan_pair_max_wait_sec(self, channel_id: int) -> int:
        """获取扫码 A 后等待 B 的最大秒数, 0 = 不超时."""
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                if self._conn_serves_channel(conn, channel_id):
                    return max(0, int(getattr(conn, 'scan_pair_max_wait_sec', 0) or 0))
        except Exception:
            pass
        return 0

    def get_scan_pair_active_serial(self, channel_id: int) -> Optional[str]:
        """供前端/Monitor 显示当前周期的开始码; None 表示窗口未开."""
        with self._scan_pair_lock:
            entry = self._scan_pair_active.get(channel_id)
            return entry.get("serial_no") if entry else None

    def _scan_pair_resolve_broadcast_channels(self, channel_id: int) -> list[int]:
        """返回与本工位'共享窗口'的所有工位 (含本工位).

        约定: 一扫码器 → 多工位广播是唯一拓扑, 多扫码器 → 一工位不存在.
        所以扫码事件命中的扫码器, 它的 broadcast_channels (或 [channel_id])
        就是共享窗口的全集. 同步开 / 同步结算.
        """
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                if self._conn_serves_channel(conn, channel_id):
                    if conn.broadcast_channels:
                        return list(conn.broadcast_channels)
                    return [conn.channel_id]
        except Exception:
            pass
        return [channel_id]

    def _cancel_scan_pair_timer(self, channel_id: int):
        """取消该工位的超时定时器 (若有)."""
        timer = self._scan_pair_timeout_timers.pop(channel_id, None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _arm_scan_pair_timer(self, channel_id: int):
        """启动该工位的超时定时器 (scan_pair_max_wait_sec > 0 时)."""
        wait = self._get_scan_pair_max_wait_sec(channel_id)
        if wait <= 0:
            return
        self._cancel_scan_pair_timer(channel_id)
        timer = threading.Timer(
            float(wait),
            self._on_scan_pair_timeout,
            args=(channel_id,),
        )
        timer.daemon = True
        timer.start()
        self._scan_pair_timeout_timers[channel_id] = timer

    def _on_scan_pair_timeout(self, channel_id: int):
        """超时回调: 把当前窗口强制 NG 结算 (按曾齐过判定 → 但超时强制 NG),
        并清空窗口状态. 用户没扫到下一码就强制收尾, 防止周期永远卡死."""
        try:
            with self._scan_pair_lock:
                if channel_id not in self._scan_pair_active:
                    return
                entry = self._scan_pair_active.pop(channel_id)
            self._scan_pair_timeout_timers.pop(channel_id, None)
            print(
                f"[ScanPair] ch{channel_id} timeout waiting next scan, force NG settle "
                f"(start_code={entry.get('serial_no')})",
                flush=True,
            )
            if debug_center.is_on("backend.mes"):
                debug_center.dbg("backend.mes", "scan_pair 超时翻转→强制NG", f"channel={channel_id} serial={entry.get('serial_no') or '-'}")
            self._dispatch_scan_pair_settle(channel_id, force_ng=True,
                                            reason="scan_pair_timeout")
        except Exception as e:
            print(f"[ScanPair] timeout handling error ch={channel_id}: {e}", flush=True)

    def _dispatch_scan_pair_settle(self, channel_id: int, *,
                                    force_ng: bool = False,
                                    reason: str = "") -> int:
        """把'结算当前窗口'分发给 source 的 _settle_for_scan_pair() 方法.

        force_ng=True 时直接判 NG (用于超时); 否则交给 source 看 _was_complete 判 OK/NG.
        多工位广播时由调用方负责对每个工位都调一次本方法.
        返回结算的 box 数 (容器) 或 1/0 (非容器).
        """
        try:
            from backend.api.channel_manager import channel_manager
            mgr = channel_manager.get(channel_id)
        except Exception as e:
            print(f"[ScanPair] dispatch import failed ch={channel_id}: {e}", flush=True)
            return 0
        if mgr is None:
            return 0
        fn = getattr(mgr, "settle_for_scan_pair", None)
        if not callable(fn):
            print(f"[ScanPair] ch{channel_id} backend has no settle_for_scan_pair method",
                  flush=True)
            return 0
        try:
            return int(fn(force_ng=force_ng, reason=reason) or 0)
        except Exception as e:
            print(f"[ScanPair] settle_for_scan_pair error ch={channel_id}: {e}",
                  flush=True)
            return 0

    def settle_scan_pair_for_stop(self, channel_id: int, *,
                                   discard: bool) -> int:
        """前端在 '停止/待机' 弹窗里调本方法收尾最后一码窗口.

        discard=True  → 不结算, 直接清空状态 (产能不计入)
        discard=False → 按曾齐过判定 OK/NG 结算 + 清空状态
        """
        with self._scan_pair_lock:
            entry = self._scan_pair_active.pop(channel_id, None)
        self._cancel_scan_pair_timer(channel_id)
        if not entry:
            return 0
        if discard:
            print(
                f"[ScanPair] ch{channel_id} stop/standby, user discarded last scan "
                f"(serial={entry.get('serial_no')})",
                flush=True,
            )
            return 0
        return self._dispatch_scan_pair_settle(
            channel_id, force_ng=False, reason="stop_or_standby_user_settle"
        )

    def _handle_scan_pair_event(self, db, channel_id: int, serial_no: str,
                                 wp_id: int):
        """v3.3.0 scan_pair 模式扫码事件入口 (在 _handle_scan 里被调用).

        逻辑:
          1) 同码二次扫 (== _scan_pair_active[ch].serial_no) → toast 提醒, 不动
          2) 不同码 / 首次扫 → 解析共享窗口 (broadcast 多工位)
             - 若每个工位都未开窗 → 起新窗口 (本码作为开始码), 起超时定时器
             - 若有工位已有开始码 → 触发结算上一窗口, 然后用新码起新窗口

        多工位广播 (v3.4.2 重构):
          scanner.py 对 broadcast_channels 里的每个 ch 都调一次 _handle_scan,
          各 register 一个 wp 并设 _pending_workpiece[ch]. 然后调本方法.
          - 兄弟 ch (channel_id != broadcast_chs[0]): 只 promote pending →
            inspecting, 让前端显示新码; 不重复 settle / 起窗口.
          - 主 ch (channel_id == broadcast_chs[0]): 执行完整 settle 上一窗口
            + 起新窗口, 然后 promote 自己的 pending → inspecting.

          这样两个工位会同时显示新码, 而不是"一个登记一个排队".
        """
        broadcast_chs = self._scan_pair_resolve_broadcast_channels(channel_id)
        if not broadcast_chs:
            broadcast_chs = [channel_id]

        if channel_id != broadcast_chs[0]:
            # 兄弟 ch: 只 promote, 不重复 settle / 起窗口
            self._scan_pair_promote_pending(db, channel_id, wp_id, serial_no)
            return

        with self._scan_pair_lock:
            existing = self._scan_pair_active.get(channel_id)
            same_code_dup = (
                existing is not None
                and existing.get("serial_no") == serial_no
            )

        if same_code_dup:
            print(
                f"[ScanPair] ch{channel_id} duplicate scan (serial={serial_no}), "
                f"soft-ignored, waiting new code",
                flush=True,
            )
            for ch in broadcast_chs:
                self._scan_pair_emit_dup_toast(ch, serial_no)
            return

        # 触发各工位的结算 (有开始码的工位才结算)
        triggered_settle = []
        for ch in broadcast_chs:
            with self._scan_pair_lock:
                if ch in self._scan_pair_active:
                    self._cancel_scan_pair_timer(ch)
                    triggered_settle.append(ch)
        for ch in triggered_settle:
            self._dispatch_scan_pair_settle(
                ch, force_ng=False, reason=f"scan_pair_next_code:{serial_no}"
            )
            # settle 完成 → 清掉 inspecting, 让本码 promote 上来
            self._inspecting_workpiece.pop(ch, None)

        # 用新码起新窗口
        now_ts = time.time()
        with self._scan_pair_lock:
            for ch in broadcast_chs:
                self._scan_pair_active[ch] = {
                    "serial_no": serial_no,
                    "wp_id": wp_id,
                    "scanned_at": now_ts,
                }
        for ch in broadcast_chs:
            self._arm_scan_pair_timer(ch)
        print(
            f"[ScanPair] open new window (start_code={serial_no}, wp#{wp_id}, "
            f"channels={broadcast_chs})",
            flush=True,
        )
        if debug_center.is_on("backend.mes"):
            debug_center.dbg("backend.mes", "scan_pair 起新窗口", f"serial={serial_no or '-'} wp={wp_id} channels={broadcast_chs} settled={triggered_settle}")

        # 主 ch 自己也要 promote pending → inspecting (兄弟 ch 各自已 promote)
        self._scan_pair_promote_pending(db, channel_id, wp_id, serial_no)

    def _scan_pair_promote_pending(self, db, channel_id: int, wp_id: int,
                                    serial_no: str):
        """v3.4.2 scan_pair 模式下 promote _pending_workpiece[ch] →
        _inspecting_workpiece[ch], 让前端 get_current_workpiece 立即看到新码.

        scan_pair 模式不依赖 _start_cycle/_handle_cycle_start 流程, 故需要在
        扫码当时手动 promote, 否则 _inspecting_workpiece[ch] 永远是上一码 (或
        None), 前端显示卡住.
        """
        # 上一码 inspecting 在 _dispatch_scan_pair_settle 之后应该被清; 这里再
        # 强制清一次防御异常路径.
        prev_inspecting = self._inspecting_workpiece.pop(channel_id, None)
        # _handle_scan 已把 wp_id 写到 _pending_workpiece[channel_id], 这里 pop
        # 出来 promote.
        pending_id = self._pending_workpiece.pop(channel_id, None)
        if pending_id is None:
            # 兜底: 用入参 wp_id (理论上 == _pending)
            pending_id = wp_id
        self._inspecting_workpiece[channel_id] = pending_id
        try:
            self._workpiece_svc.mark_inspecting(db, pending_id)
        except Exception as e:
            print(f"[ScanPair] mark_inspecting error ch={channel_id} "
                  f"wp#{pending_id}: {e}", flush=True)
        print(f"[ScanPair] ch{channel_id} promote: wp#{pending_id} "
              f"(serial={serial_no}) → inspecting "
              f"(prev_inspecting={prev_inspecting})", flush=True)

    def _scan_pair_emit_dup_toast(self, channel_id: int, serial_no: str):
        """同码二次扫 → 通知前端弹 toast 'duplicate scan in scan_pair mode'.

        复用 _last_scan_event 字段加一个 dup_warning 标志, 前端轮询 detection
        results 时检查该字段并弹 toast.
        """
        self._last_scan_event[channel_id] = {
            "serial_no": serial_no,
            "workpiece_id": None,
            "timestamp": time.time(),
            "scan_pair_dup_warning": True,
        }

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
            pass  # 高频路径：连接未就绪/字段缺失时回退默认
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
            pass  # 高频路径：连接未就绪/字段缺失时回退默认
        return 0

    def _get_late_bind_window(self, channel_id: int) -> int:
        """获取该工位的"迟到扫码补绑窗口秒数"配置。0 = 关闭兜底。

        v2.7.16: 解决"扫码动作晚于 cycle 结算几毫秒~几秒，导致工件未被绑定到刚结算
        的 cycle、本次 cycle_end 报未绑码"的痛点。"""
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            for conn in svc._connections.values():
                if self._conn_serves_channel(conn, channel_id):
                    return int(getattr(conn, "late_scan_bind_window_sec", 3) or 0)
        except Exception:
            pass  # 高频路径：连接未就绪/字段缺失时回退默认
        return 3

    def get_rebind_prompt(self, channel_id: int) -> Optional[dict]:
        """获取 manual rebind 弹窗数据"""
        return self._rebind_prompt.get(channel_id)

    def clear_pending_scan(self, channel_id: int, force: bool = False,
                           db=None) -> dict:
        """清除该工位的"待检 / 最近扫码 / 当前检测"状态，让工人能重扫一次。

        v2.7.16: 解决五个场景
        1) reject 模式下 _pending 挂着导致后续扫码全被拒（B4 死锁）；
        2) 工人扫错码想立即丢弃；
        3) 节拍间隙重置卡片显示；
        4) (force) 工人扫码后才发现拿错件 / 装到一半要中止，需要作废本次检测；
        5) (force) 同 cycle 内想换一个条码重新绑。

        参数:
            force: False (默认) → 只清"未绑定"前置状态，已绑到 cycle 的不动；
                   True       → 连同 _inspecting_workpiece 一起作废，
                                把工件 status 回退到 queued，
                                删除该 (workpiece, cycle) 的 WorkpieceInspection 记录，
                                让本次 cycle 自然走到 cycle_end 时报"未绑码"，
                                cycle 仍会被结算但不计入 MES/工单/集群。

        本接口跟"迟到扫码补绑"的兼容性:
            因为 _last_scan_event 一并清掉，cycle_end 的兜底不会把刚作废的 wp
            又绑回来 (workpiece_id 不匹配)。
        """
        cleared = {}
        wp_id = self._pending_workpiece.pop(channel_id, None)
        if wp_id is not None:
            cleared["pending_workpiece_id"] = wp_id
        q = self._pending_queue.pop(channel_id, None)
        if q:
            cleared["pending_queue"] = list(q)
        ev = self._last_scan_event.pop(channel_id, None)
        if ev:
            cleared["last_scan_event"] = ev
        rb = self._rebind_prompt.pop(channel_id, None)
        if rb:
            cleared["rebind_prompt"] = rb

        if force:
            # 原子 pop：避免与 worker 线程的 _handle_cycle_end 抢同一个 wp_id。
            # 若在 HTTP get→pop 之间 worker 已经把 wp 拿走结算了，这里 pop 拿到 None
            # → 不能再回退状态（worker 那边马上就要 set_result 写 ok/ng + 集群分发了）
            # → 返回 "race_lost"，前端提示用户"操作来不及，本次工件已结算完成"。
            inspecting_id = self._inspecting_workpiece.pop(channel_id, None)
            cleared["inspecting_workpiece_id"] = inspecting_id
            if inspecting_id is None:
                cleared["force_canceled_inspecting"] = False
                cleared["force_race_lost"] = True
            else:
                cleared["force_canceled_inspecting"] = True
                # 回退 workpiece status + 删除 WorkpieceInspection 关联
                local_db = db
                owns_db = False
                if local_db is None:
                    local_db = SessionLocal()
                    owns_db = True
                try:
                    from backend.models.mes_models import Workpiece, WorkpieceInspection
                    cycle_id = self._get_current_cycle_id(channel_id)
                    if cycle_id:
                        deleted = (
                            local_db.query(WorkpieceInspection)
                            .filter(WorkpieceInspection.workpiece_id == inspecting_id,
                                    WorkpieceInspection.cycle_id == cycle_id)
                            .delete()
                        )
                        cleared["deleted_inspections"] = deleted
                    wp = local_db.query(Workpiece).filter(Workpiece.id == inspecting_id).first()
                    if wp:
                        wp.status = "queued"
                        cleared["workpiece_reverted_to"] = "queued"
                    if owns_db:
                        local_db.commit()
                except Exception as e:
                    if owns_db:
                        local_db.rollback()
                    print(f"[MES] clear_pending_scan(force) rollback failed ch{channel_id} wp{inspecting_id}: {e}",
                          flush=True)
                finally:
                    if owns_db:
                        local_db.close()
        else:
            # 非 force 路径只读，不动 _inspecting_workpiece
            cleared["inspecting_workpiece_id"] = self._inspecting_workpiece.get(channel_id)

        if cleared.get("pending_workpiece_id") or cleared.get("pending_queue") \
                or cleared.get("last_scan_event") \
                or cleared.get("force_canceled_inspecting") \
                or cleared.get("force_race_lost"):
            print(f"[MES] cleared ch{channel_id} scan state (force={force}): {cleared}",
                  flush=True)
        return cleared

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
                print(f"[MES] manual rebind: workpiece#{wp_id} back to pending", flush=True)
            finally:
                db.close()

    def _handle_scan(self, db, channel_id: int, serial_no: str,
                     raw_data: str, project_id: int, device_id: int = None):
        """处理扫码事件"""
        from backend.models.mes_models import ScanLog

        # v3.14 RFC 11: WorkpieceFlow 优先路径.
        # 若本通道属于某串行流水线 → 走 flow 路径 (创建 WorkpieceFlowRun),
        # 跳过本通道的 scan_pair / pending_workpiece 状态机 (互斥, 文档明示).
        # 任何异常隔离, 不影响主流程.
        try:
            from backend.services.workpiece_flow_coordinator import get_coordinator as _get_wfc_coord
            _wfc = _get_wfc_coord()
            if _wfc.is_channel_in_flow(channel_id):
                _wfc.on_scan_received(
                    channel_id=channel_id,
                    barcode=serial_no,
                    scanner_device_id=device_id,
                    project_id=project_id,
                    db=db,
                )
                # 仍写 ScanLog 做审计
                try:
                    scan_log = ScanLog(
                        device_id=device_id, channel_id=channel_id,
                        raw_data=raw_data, parsed_serial=serial_no,
                        success=True, error_msg="走 WorkpieceFlow 路径",
                    )
                    db.add(scan_log)
                    db.commit()
                except Exception:
                    pass
                return  # 互斥: 不再走原 scan_pair 路径
        except Exception as _e_wfc_scan:
            print(f"[WorkpieceFlow] on_scan_received error (isolated, fallback to scan_pair): {_e_wfc_scan}")

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
                    print(f"[MES] scan cooldown filter: {serial_no} workpiece#{existing.id} "
                          f"{elapsed:.1f}s after OK (cooldown {cooldown}s, ch{channel_id})",
                          flush=True)
                    return

        action = self._get_duplicate_scan_action(channel_id)

        if action == "reject" and channel_id in self._pending_workpiece:
            print(f"[MES] scan rejected (pending workpiece exists): {serial_no} (ch{channel_id})", flush=True)
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
            self._enqueue_pending(channel_id, wp.id)
            if channel_id not in self._pending_workpiece:
                self._pending_workpiece[channel_id] = wp.id
            print(f"[MES] scan enqueued: {serial_no} -> workpiece#{wp.id} (ch{channel_id}, queue_len={len(self._pending_queue[channel_id])})", flush=True)
        else:
            self._pending_workpiece[channel_id] = wp.id
            print(f"[MES] scan registered: {serial_no} -> workpiece#{wp.id} (ch{channel_id})", flush=True)

        self._last_scan_event[channel_id] = {
            "serial_no": serial_no,
            "workpiece_id": wp.id,
            "timestamp": time.time(),
        }

        # v3.4.0 scan_mode='D' (容器跨线触发) 收到码后立刻发 LOFF.
        # 与 bind_timing 完全正交; 调完 LOFF 后照走 bind_timing 分支处理工件绑定.
        try:
            from backend.api.channel_manager import channel_manager
            _mgr = channel_manager.get(channel_id)
            if _mgr is not None and hasattr(_mgr, 'scan_d_on_scan_received'):
                _mgr.scan_d_on_scan_received()
        except Exception as _e:
            print(f"[ScanD] scan_d_on_scan_received error ch={channel_id}: {_e}", flush=True)

        # v3.3.0 scan_pair (码-码闭环) 模式: 扫码 A 触发结算上一窗口 + 起新窗口.
        # 这里 hijack 后续的 mid_cycle / cycle_start 路径, 由 scan_pair 单独管理
        # cycle 结算节奏, 不进入老的 bind_timing 分支.
        bind_timing = self._get_bind_timing(channel_id)
        if bind_timing == "scan_pair":
            self._handle_scan_pair_event(db, channel_id, serial_no, wp.id)
            return

        # mid_cycle: 如果当前有活跃周期且未绑定工件，立即绑定
        if bind_timing == "mid_cycle":
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
                        # v2.7.16 (B7) queue 模式 + mid_cycle 同时启用时，
                        # mid_cycle 拿走的工件也要同步从 _pending_queue 移除，
                        # 否则会泄漏在队列里、且队列后续永远消费不到下一条。
                        q = self._pending_queue.get(channel_id)
                        if q and consumed_id in q:
                            q.remove(consumed_id)
                            if q:
                                self._pending_workpiece[channel_id] = q[0]
                        print(f"[MES] mid-cycle bind: workpiece#{consumed_id} -> Cycle#{current_cid} (ch{channel_id})", flush=True)

    def _handle_cycle_start(self, db, channel_id: int, cycle_id: int,
                            session_id: int, project_id: int):
        """Cycle 开始: 关联待检工件 + (v3.7.2) 锁定扫码器旁路文件快照."""
        # v3.7.2 扫码器旁路 — C 策略 (cycle_start_snapshot):
        # 周期开始那一刻就把所有"cycle_start_snapshot"规则的 input_dir 各拍一份照,
        # 写到 DetectionCycle.external_meta. cycle_end 渲染时直接读, 不再翻文件夹.
        # 失败不抛 — 主流程 (扫码 → 工件绑定) 不能被这一步影响.
        try:
            from backend.services.export_snapshot import snapshot_for_cycle_start
            snapshot_for_cycle_start(
                db, channel_id=channel_id, cycle_id=cycle_id,
                project_id=project_id,
            )
        except Exception as e:
            print(f"[MES] cycle_start snapshot error ch{channel_id} "
                  f"cycle#{cycle_id}: {e}", flush=True)

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
        self._workpiece_svc.link_to_cycle(
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

        print(f"[MES] Cycle#{cycle_id} linked workpiece#{wp_id}", flush=True)
        if debug_center.is_on("backend.mes"):
            debug_center.dbg("backend.mes", "工件绑定成功", f"channel={channel_id} cycle={cycle_id} wp={wp_id} order={self._active_orders.get(channel_id) or '-'}")

    def _handle_cycle_end(self, db, channel_id: int, cycle_id: int,
                          is_good: bool, event_name: str, result_reason: str,
                          duration: float, step_sequence: list,
                          project_id: int):
        """Cycle 结束: 更新工件状态, 记录缺陷, 更新工单"""
        if debug_center.is_on("backend.mes"):
            debug_center.dbg("backend.mes", "_handle_cycle_end 入口", f"channel={channel_id} cycle={cycle_id} is_good={is_good} event={event_name or '-'} project={project_id or '-'}")
        # v3.4.2 hotfix-2: ScanPair 模式下, settle_for_scan_pair 触发 end_cycle
        # 是同步链, 但本方法被丢进 worker queue 异步跑. 等 worker 拿到 _inspecting
        # 时, _handle_scan_pair_event 已经 promote 把 _inspecting 改成"新码 wp"
        # 了 → pop 出来的是新码 wp_id, 上一码的结算结果就被错写到新码头上,
        # 同时上一码 (cycle 真正绑的那个) 永远拿不到 set_result → 集群拿不到 OK.
        #
        # 修复: 优先用 cycle_id 反查 WorkpieceInspection (link_to_cycle 那步已写),
        # 拿到真正属于这个 cycle 的 wp_id; 找不到才 fallback 到 pop _inspecting
        # (老 bind_timing 路径 _handle_cycle_start 也写过 inspection 行 —
        # 都拿不到才说明确实没绑工件).
        wp_id = None
        try:
            from backend.models.mes_models import WorkpieceInspection
            insp_pre = (
                db.query(WorkpieceInspection)
                .filter(WorkpieceInspection.cycle_id == cycle_id)
                .order_by(WorkpieceInspection.id.desc())
                .first()
            )
            if insp_pre and insp_pre.workpiece_id:
                wp_id = insp_pre.workpiece_id
                # 同步把 _inspecting_workpiece[ch] 也清掉, 避免野生 wp_id 泄漏
                # 但只清掉跟我们 cycle 绑的那个, 不要碰别的 (新码已经 promote 进来了).
                cur_insp = self._inspecting_workpiece.get(channel_id)
                if cur_insp == wp_id:
                    self._inspecting_workpiece.pop(channel_id, None)
        except Exception as e:
            print(f"[MES] cycle_end reverse-lookup wp_id error ch{channel_id} "
                  f"cycle#{cycle_id}: {e}", flush=True)

        if wp_id is None:
            wp_id = self._inspecting_workpiece.pop(channel_id, None)

        # v2.7.16 改进 B：迟到扫码补绑兜底。
        # 触发场景："工人放完物品 → 抬手扫码 → cycle 已经在毫秒/几秒前 settle 了"。
        # 现象：cycle_end 触发时 _inspecting_workpiece 空，但 _pending_workpiece
        # 已被 _handle_scan 写入（晚到的扫码事件正在排队 / 已落到 _pending）。
        # 兜底：如果该工件的 scan_event 时间戳与本 cycle 结束时间相差 ≤ window 秒，
        # 则把该工件补绑到刚结算的 cycle，避免"未绑码"误报。
        if not wp_id:
            window = self._get_late_bind_window(channel_id)
            pending_id = self._pending_workpiece.get(channel_id)
            scan_event = self._last_scan_event.get(channel_id)
            if window > 0 and pending_id and scan_event \
                    and scan_event.get("workpiece_id") == pending_id:
                try:
                    from backend.models.models import DetectionCycle
                    cyc = db.query(DetectionCycle).filter(
                        DetectionCycle.id == cycle_id
                    ).first()
                    cyc_end_ts = cyc.end_time.timestamp() if cyc and cyc.end_time \
                        else time.time()
                    cyc_start_ts = cyc.start_time.timestamp() if cyc and cyc.start_time \
                        else (cyc_end_ts - (duration or 0))
                    scan_ts = float(scan_event.get("timestamp", 0))
                    # 接受范围：[cycle.start - window, cycle.end + window]
                    # 用宽松窗口覆盖 worker queue 微竞争 + 工人慢半拍两种场景。
                    if (cyc_start_ts - window) <= scan_ts <= (cyc_end_ts + window):
                        wp_id = self._pending_workpiece.pop(channel_id, None)
                        if wp_id:
                            self._workpiece_svc.mark_inspecting(db, wp_id)
                            session_id = self._get_current_session_id(channel_id)
                            self._workpiece_svc.link_to_cycle(
                                db, wp_id, cycle_id,
                                session_id=session_id, channel_id=channel_id
                            )
                            # 同步清 queue（沿用 B7 修复思路）
                            q = self._pending_queue.get(channel_id)
                            if q and wp_id in q:
                                q.remove(wp_id)
                                if q:
                                    self._pending_workpiece[channel_id] = q[0]
                            delay = scan_ts - cyc_end_ts
                            print(f"[MES] late bind: workpiece#{wp_id} -> Cycle#{cycle_id} "
                                  f"(scan 距 cycle_end {delay:+.2f}s, 窗口{window}s, ch{channel_id})",
                                  flush=True)
                            if debug_center.is_on("backend.mes"):
                                debug_center.dbg("backend.mes", "迟到扫码补绑成功", f"channel={channel_id} cycle={cycle_id} wp={wp_id} delay={delay:+.2f}s")
                except Exception as e:
                    print(f"[MES] late-bind check error ch{channel_id}: {e}", flush=True)

            if not wp_id:
                # v3.7.0: 客户反馈 "MES 数据不是实时上传"。
                # 原因：原逻辑这里 return → cycle_end 整段被跳过 → 外部 MES 收不到任何数据,
                # 直到工单"完工"才(通过其它路径) 看到数据。
                # 修复：无扫码绑定也允许走 MES 推送 (按 cycle 实时推),
                # 仅跳过 workpiece-related 子操作 (workpiece_svc.set_result / defect.auto_record),
                # 因为没工件 ID 这些写操作没意义。
                # 不破坏现有行为: 有 wp_id 的路径完全不变 (扫码客户继续按工件维度推送)。
                print(f"[MES] Cycle#{cycle_id} ch{channel_id} ended without bound workpiece "
                      f"(无扫码场景) → 仍按 cycle 实时推送 MES, 跳过 workpiece 写操作", flush=True)
                if debug_center.is_on("backend.mes"):
                    debug_center.dbg("backend.mes", "工件绑定缺失(无码周期)", f"channel={channel_id} cycle={cycle_id} pending={self._pending_workpiece.get(channel_id) or '-'}")

        if wp_id:
            self._workpiece_svc.set_result(db, wp_id, is_good, cycle_id)
            self._workpiece_svc.update_inspection_result(
                db, wp_id, cycle_id,
                result="ok" if is_good else "ng",
                event_name=event_name,
                result_reason=result_reason,
                duration=duration,
            )

            if not is_good:
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
                print(f"[MES] work order#{order_id} auto-completed", flush=True)

        print(f"[MES] Cycle#{cycle_id} ended: {'OK' if is_good else 'NG'} "
              f"(工件#{wp_id if wp_id else '无绑定'})",
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
                # B1②: 开关开 → 甩到每工位执行器(慢 MES 不堵 worker); 默认关 → 内联(字节级一致)
                if self._async_dispatch_enabled:
                    self._submit_gateway_dispatch("cycle_end", ctx, channel_id)
                else:
                    gw.dispatch("cycle_end", ctx, channel_id)
            if debug_center.is_on("backend.mes"):
                debug_center.dbg("backend.mes", "_handle_cycle_end 完成", f"channel={channel_id} cycle={cycle_id} wp={wp_id or '-'} skip_cycle_push={skip_cycle_push}")
        except Exception as e:
            import traceback
            debug_center.dbg("backend.mes", "cycle_end 外部推送异常", f"channel={channel_id} cycle={cycle_id} err={e}")
            print(f"[MES] external push (cycle_end) failed: {e}\n{traceback.format_exc()}",
                  flush=True)

        # v3.5.0: 自定义导出实时规则触发（独立 try/except，不影响 MES 推送）
        # 客户的 SN.txt 三行写出在这里闭环：扫码后 cycle 结束 → 按规则渲染模板 → 落到客户指定文件夹
        try:
            from backend.services.export_realtime import dispatch_cycle_end_export
            dispatch_cycle_end_export(
                db, channel_id=channel_id, cycle_id=cycle_id,
                project_id=project_id,
            )
        except Exception as e:
            import traceback
            print(f"[ExportRealtime] cycle_end trigger failed: {e}\n{traceback.format_exc()}",
                  flush=True)

        # rebind 操作仅在有 wp_id 时才有意义 (没工件无从重绑)
        if wp_id:
            rebind = self._get_rebind_mode(channel_id)
            if rebind == "auto_rebind" and not is_good:
                from backend.models.mes_models import Workpiece
                wp_obj = db.query(Workpiece).filter(Workpiece.id == wp_id).first()
                if wp_obj:
                    wp_obj.status = "queued"
                    db.commit()
                self._pending_workpiece[channel_id] = wp_id
                print(f"[MES] auto rebind: workpiece#{wp_id} back to pending (auto_rebind)", flush=True)
            elif rebind == "manual" and not is_good:
                self._rebind_prompt[channel_id] = {
                    "workpiece_id": wp_id,
                    "cycle_id": cycle_id,
                    "timestamp": time.time(),
                }
                print(f"[MES] waiting manual selection: workpiece#{wp_id} (manual)", flush=True)

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
                print(f"[Cluster/Dispatch] ch{channel_id} skip: cluster disabled", flush=True)
                return False

            box_serial = cycle_context.get("workpiece", {}).get("serial_no")
            if not box_serial:
                wp_preview = cycle_context.get("workpiece", {})
                print(f"[Cluster/Dispatch] ch{channel_id} skip: no workpiece.serial_no in cycle_context "
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
                print(f"[Cluster/Dispatch] master local store result: {result}", flush=True)
                return sync_mode == "wait_all"
            elif role == "slave":
                master_url = config.get("master_url")
                if not master_url:
                    print("[Cluster/Dispatch] slave skip report: master_url empty", flush=True)
                    return False
                result = collector.report_to_master(
                    cycle_context=cycle_context,
                    box_serial=box_serial,
                    station_id=station_id,
                    master_url=master_url,
                    is_good=is_good,
                    event_name=event_name,
                )
                print(f"[Cluster/Dispatch] slave report to {master_url} result: {result}", flush=True)
                return sync_mode == "wait_all"
            else:
                print(f"[Cluster/Dispatch] ch{channel_id} unknown role={role}, skip", flush=True)
        except Exception as e:
            import traceback
            print(f"[Cluster/Dispatch] ch{channel_id} cluster dispatch error: {e}\n{traceback.format_exc()}",
                  flush=True)
        return False

    def _handle_session_start(self, db, channel_id: int, session_id: int,
                              project_id: int):
        """Session 开始: 查找活跃工单.

        v3.1.0: 支持按 binding_scope 匹配. cluster 模式不在 session_start 绑,
        由 cluster_collector 在 box_complete 时直接 +1, 所以 _active_orders
        里只会装 project/channels 模式的工单.
        """
        order = self._work_order_svc.get_active_order(
            db, project_id=project_id, channel_id=channel_id,
        )
        if order:
            self._active_orders[channel_id] = order.id
            from backend.models.models import DetectionSession
            session = db.query(DetectionSession).filter(
                DetectionSession.id == session_id
            ).first()
            if session and hasattr(session, 'order_id'):
                session.order_id = order.id
                db.flush()
            print(f"[MES] Session#{session_id} linked work order {order.order_no} "
                  f"(scope={order.binding_scope}, ch={channel_id})", flush=True)
        else:
            # v3.1.0: 静默处理 — cluster-only 部署下这条日志会一直刷, 没有诊断价值.
            # 真正"工单数量不+1"的排查靠工单列表"未绑定"红色 tag + cycle_end 那条
            # "Cycle#X 结束但未绑定工件" 的日志即可.
            pass

    def _handle_rebind_active_order(self, db, channel_id: int, session_id: int,
                                    project_id: int, allow_replace: bool):
        """运行中工位的活跃工单回填 (工作线程执行, 与 session_start 绑定同一套匹配)。

        规则 (对齐"最新开工为准"已有语义, 不引入新开关):
          - 工位当前没挂单 → 无条件补挂 (纯正确性: 旧行为是空到重启检测为止)
          - 已挂旧单且 allow_replace=False → 保守不动 (客户没开顶替就尊重在做的单)
          - 已挂旧单且 allow_replace=True → 切到最新在产单 (顶替时旧单已被收尾)
        """
        order = self._work_order_svc.get_active_order(
            db, project_id=project_id, channel_id=channel_id,
        )
        if not order:
            return
        cur = self._active_orders.get(channel_id)
        if cur == order.id:
            return
        if cur is not None and not allow_replace:
            debug_center.dbg("backend.mes", "活跃工单回填跳过",
                             f"ch{channel_id} 已挂工单#{cur} 且未开'最新开工为准', 不顶替")
            return
        self._active_orders[channel_id] = order.id
        from backend.models.models import DetectionSession
        session = db.query(DetectionSession).filter(
            DetectionSession.id == session_id
        ).first()
        if session and hasattr(session, 'order_id'):
            session.order_id = order.id
            db.flush()
        print(f"[MES] ch{channel_id} 运行中回填活跃工单 {order.order_no} "
              f"(session#{session_id}, 原工单#{cur or '-'})", flush=True)
        debug_center.dbg("backend.mes", "活跃工单已回填",
                         f"ch{channel_id} session#{session_id} "
                         f"{'顶替#' + str(cur) if cur else '补挂'} → {order.order_no}")

    def _handle_session_end(self, db, channel_id: int, session_id: int):
        """Session 结束: 清理状态"""
        cleared_order_id = self._active_orders.pop(channel_id, None)
        self._pending_workpiece.pop(channel_id, None)
        self._pending_queue.pop(channel_id, None)
        self._inspecting_workpiece.pop(channel_id, None)
        print(f"[MES] Session#{session_id} ended, cleaning ch{channel_id} state "
              f"(order={cleared_order_id})", flush=True)

        # 外部 MES 推送
        try:
            from backend.services.mes_gateway import get_mes_gateway
            gw = get_mes_gateway()
            ctx = gw.build_context_from_session(db, session_id)
            gw.dispatch("session_end", ctx, channel_id)
        except Exception as e:
            import traceback
            print(f"[MES] external push (session_end) failed: {e}\n{traceback.format_exc()}",
                  flush=True)

        # v3.6.2: 自定义导出实时规则 — session_end 触发器接通
        # cycle_end 是单工件文件; session_end 是整次开机的会话报告
        try:
            from backend.services.export_realtime import dispatch_session_end_export
            from backend.models.models import DetectionSession
            project_id = None
            try:
                sess = db.query(DetectionSession).filter(
                    DetectionSession.id == session_id
                ).first()
                if sess:
                    project_id = sess.project_id
            except Exception:
                pass
            dispatch_session_end_export(
                db, channel_id=channel_id, session_id=session_id,
                project_id=project_id,
            )
        except Exception as e:
            import traceback
            print(f"[ExportRealtime] session_end trigger failed: {e}\n{traceback.format_exc()}",
                  flush=True)


# 全局单例
_mes_hook_instance: Optional[MESHookManager] = None


def get_mes_hook() -> MESHookManager:
    global _mes_hook_instance
    if _mes_hook_instance is None:
        _mes_hook_instance = MESHookManager()
    return _mes_hook_instance
