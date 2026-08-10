"""
点位引擎 — 每条 PLC 连接一个实例一条线程 (RFC 13 维度三/四/六)。

线程模型 (关键设计, 改动前想清楚):
- **引擎线程独占全部 driver IO**: 轮询读、写队列消费、心跳写、重连,
  全部串行发生在本线程 → driver 无需加锁, 也不存在读写并发窗口
- 规则动作在 manager 的共享动作线程执行 (switch_project 加载模型可达秒级,
  不能卡轮询); 动作里的 write_points 通过写队列回到引擎线程
- 检测/结算热路径从不直接碰引擎: 写回经 write_dispatcher 队列进来 (不变量 15)

规则语义:
- 条件分两类: 边沿型 (rising/falling/change) 与 状态型 (equals/not_equals/
  in/range/level)。有边沿条件的规则: 任一边沿条件确认(过防抖) 且 全部状态
  条件当前为真 → 触发; 纯状态条件规则: 全体条件的 AND 从假变真(过防抖) → 触发
- debounce_ms: 触发前值须稳定保持; min_interval_ms: 两次触发最小间隔
- ack_mode: none / write_ack / self_clear / level_follow (维度四全枚举)
- stuck_timeout_s: 触发位卡住超时 → stuck_action (alarm/ignore, 恢复后自动复位)
"""
import logging
import queue
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_EDGE_TRIGGERS = ("rising", "falling", "change")
_STATE_TRIGGERS = ("equals", "not_equals", "in", "range", "level")


def _loose_eq(a, b) -> bool:
    """宽松相等: PLC INT 1 与配置 "1" 视为相等 (JSON 配置里数字常被写成字符串)。"""
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


class _WriteJob:
    __slots__ = ("key", "value", "done", "error")

    def __init__(self, key: str, value: Any):
        self.key = key
        self.value = value
        self.done = threading.Event()
        self.error: Optional[str] = None


class _CondState:
    """单个条件的运行态 (边沿检测 + 防抖 FSM)。"""
    __slots__ = ("prev_raw", "pending_ts", "pending_value", "level_since",
                 "level_fired")

    def __init__(self):
        self.prev_raw: Optional[bool] = None
        self.pending_ts: Optional[float] = None
        self.pending_value: Any = None
        self.level_since: Optional[float] = None
        self.level_fired = False


class _RuleState:
    __slots__ = ("conds", "last_fire_ts", "stuck_since", "stuck_reported")

    def __init__(self, n_conds: int):
        self.conds = [_CondState() for _ in range(n_conds)]
        self.last_fire_ts = 0.0
        self.stuck_since: Optional[float] = None
        self.stuck_reported = False


class PLCPointEngine:

    def __init__(self, row: dict, manager):
        self.conn_id: int = row["id"]
        self.name: str = row["name"]
        self.driver_name: str = row["driver"]
        self.conn_params: dict = row.get("conn_params") or {}
        self.point_list: List[dict] = row.get("points") or []
        self.points: Dict[str, dict] = {p["key"]: p for p in self.point_list}
        self.read_rules: List[dict] = row.get("read_rules") or []
        self.write_rules: List[dict] = row.get("write_rules") or []
        self.options: dict = row.get("options") or {}
        self._manager = manager

        self.driver = None
        self.status = "starting"           # starting/connected/error/config_error/stopped
        self.last_error: Optional[str] = None
        self.values: Dict[str, Any] = {}
        self.value_changed_ts: Dict[str, float] = {}
        self.last_poll_ts: Optional[float] = None
        self.vars: Dict[str, Any] = {}
        self.io_log: deque = deque(maxlen=300)
        self.counters = {"polls": 0, "read_errors": 0, "writes": 0,
                         "write_errors": 0, "rule_fires": 0, "reconnects": 0}

        self._write_q: "queue.Queue[_WriteJob]" = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._rule_states = [_RuleState(len(r.get("when") or [])) for r in self.read_rules]
        self._hb_states: Dict[int, dict] = {}      # 心跳规则序号 → {next_due, toggle, counter}
        self._hb_watch_alerted = False
        self._reset_timers: List[threading.Timer] = []

        self.poll_interval = max(0.02, float(
            self.conn_params.get("poll_interval_ms") or 100) / 1000.0)

    # ================= 生命周期 =================

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"plc-{self.conn_id}-{self.name}")
        self._thread.start()

    def stop(self):
        self._stop.set()
        for t in self._reset_timers:
            try:
                t.cancel()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.status = "stopped"

    # ================= 对外入口 (任意线程可调) =================

    def enqueue_write(self, key: str, value: Any, *, reset_after_ms: int = None,
                      reset_value: Any = None) -> _WriteJob:
        """写请求入队 (调用方永不被 IO 阻塞)。返回 job 可选等待结果。"""
        point = self.points.get(key)
        if point is None:
            raise ValueError(f"连接 {self.name} 无点位: {key}")
        from backend.services.plc import point_codec
        job = _WriteJob(key, point_codec.coerce_value(value, point))
        try:
            self._write_q.put_nowait(job)
        except queue.Full:
            job.error = "PLC 写队列满, 已丢弃"
            job.done.set()
            self.counters["write_errors"] += 1
            self._log("error", f"写队列满丢弃 {key}={value}")
            return job
        if reset_after_ms:
            rv = reset_value if reset_value is not None else (
                False if (point.get("type") or "bool") == "bool" else 0)
            timer = threading.Timer(
                float(reset_after_ms) / 1000.0,
                lambda: self.enqueue_write(key, rv))
            timer.daemon = True
            timer.start()
            self._reset_timers = [t for t in self._reset_timers if t.is_alive()]
            self._reset_timers.append(timer)
        return job

    def write_sync(self, key: str, value: Any, timeout: float = 4.0) -> None:
        """手动写值 (API 用): 入队并等待引擎线程执行完, 失败抛异常。"""
        job = self.enqueue_write(key, value)
        if not job.done.wait(timeout):
            raise TimeoutError("PLC 写超时 (连接可能中断)")
        if job.error:
            raise RuntimeError(job.error)

    def snapshot(self) -> dict:
        return {
            "connection_id": self.conn_id,
            "name": self.name,
            "driver": self.driver_name,
            "status": self.status,
            "last_error": self.last_error,
            "last_poll_ts": self.last_poll_ts,
            "values": dict(self.values),
            "value_changed_ts": dict(self.value_changed_ts),
            "vars": dict(self.vars),
            "counters": dict(self.counters),
        }

    # ================= 引擎线程主体 =================

    def _run(self):
        try:
            from backend.services.plc.drivers import get_driver_class
            driver_cls = get_driver_class(self.driver_name)
            self.driver = driver_cls(self.conn_params, self.point_list)
        except Exception as e:
            # 配置/依赖错误: 不重试, 等用户改配置重启连接
            self.status = "config_error"
            self.last_error = str(e)
            self._log("error", f"驱动初始化失败: {e}")
            logger.error("[PLC] %s 驱动初始化失败: %s", self.name, e)
            return

        backoff = float(self.options.get("reconnect_initial_s") or 2)
        backoff_max = float(self.options.get("reconnect_max_s") or 30)
        while not self._stop.is_set():
            try:
                self.driver.connect()
            except Exception as e:
                if self.status != "error":
                    self._on_disconnected(f"连接失败: {e}")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, backoff_max)
                continue

            backoff = float(self.options.get("reconnect_initial_s") or 2)
            self.status = "connected"
            self.last_error = None
            self.counters["reconnects"] += 1
            self._log("event", "连接成功")
            self._dispatch_conn_event("connection_up")

            try:
                self._poll_loop()
            except Exception as e:
                self._on_disconnected(f"通讯中断: {e}")
                try:
                    self.driver.close()
                except Exception:
                    pass
        try:
            self.driver.close()
        except Exception:
            pass

    def _poll_loop(self):
        next_poll = 0.0
        while not self._stop.is_set():
            now = time.time()

            if now >= next_poll:
                self._do_poll()
                now = time.time()
                next_poll = now + self.poll_interval

            self._do_heartbeats(now)
            self._check_heartbeat_watch(now)

            # 阻塞在写队列上直到下一个到期点 → 写请求近实时执行且不空转
            timeout = max(0.005, min(next_poll - time.time(),
                                     self._next_heartbeat_delay()))
            try:
                job = self._write_q.get(timeout=timeout)
            except queue.Empty:
                continue
            self._execute_write(job)
            # 排空积压写 (不让轮询周期饿死写队列)
            while True:
                try:
                    job = self._write_q.get_nowait()
                except queue.Empty:
                    break
                self._execute_write(job)

    def _do_poll(self):
        new_values = self.driver.read_all()     # 通讯异常向上抛 → 重连
        self.counters["polls"] += 1
        self.last_poll_ts = time.time()
        for key, val in new_values.items():
            if key not in self.values or self.values[key] != val:
                self.value_changed_ts[key] = self.last_poll_ts
                if key in self.values:      # 首帧不刷日志
                    self._log("read", f"{key}: {self.values.get(key)!r} → {val!r}")
        prev = self.values
        self.values = new_values
        self._eval_rules(prev, new_values)

    def _execute_write(self, job: _WriteJob):
        try:
            self.driver.write(job.key, job.value)
            self.counters["writes"] += 1
            self.values[job.key] = job.value
            self._log("write", f"{job.key} = {job.value!r}")
        except Exception as e:
            self.counters["write_errors"] += 1
            job.error = str(e)
            self._log("error", f"写 {job.key} 失败: {e}")
        finally:
            job.done.set()

    def _on_disconnected(self, reason: str):
        was_connected = self.status == "connected"
        self.status = "error"
        self.last_error = reason
        self.counters["read_errors"] += 1
        self._log("error", reason)
        logger.warning("[PLC] %s %s", self.name, reason)
        if was_connected:
            self._dispatch_conn_event("connection_down")
            alarm_event = (self.options.get("on_disconnect") or {}).get("alarm_event")
            if alarm_event:
                self._trigger_alarm(alarm_event)

    # ================= 心跳写出 + PLC 心跳监视 =================

    def _heartbeat_rules(self):
        return [(i, r) for i, r in enumerate(self.write_rules)
                if (r.get("on") or "") == "heartbeat"]

    def _next_heartbeat_delay(self) -> float:
        delays = []
        now = time.time()
        for i, r in self._heartbeat_rules():
            st = self._hb_states.get(i)
            delays.append(max(0.0, (st["next_due"] - now)) if st else 0.0)
        return min(delays) if delays else 3600.0

    def _do_heartbeats(self, now: float):
        if self.status != "connected":
            return
        for i, rule in self._heartbeat_rules():
            st = self._hb_states.setdefault(
                i, {"next_due": 0.0, "toggle": False, "counter": 0})
            if now < st["next_due"]:
                continue
            st["next_due"] = now + max(0.05, float(rule.get("period_ms") or 1000) / 1000.0)
            for w in (rule.get("writes") or []):
                mode = w.get("value")
                if mode == "toggle":
                    st["toggle"] = not st["toggle"]
                    value = st["toggle"]
                elif mode == "increment":
                    st["counter"] = (st["counter"] + 1) % 32767
                    value = st["counter"]
                else:
                    value = mode
                try:
                    self.enqueue_write(w.get("point"), value)
                except Exception as e:
                    self._log("error", f"心跳写配置错误: {e}")

    def _check_heartbeat_watch(self, now: float):
        cfg = self.options.get("heartbeat_watch") or {}
        point, timeout_s = cfg.get("point"), cfg.get("timeout_s")
        if not point or not timeout_s or self.status != "connected":
            return
        last = self.value_changed_ts.get(point)
        if last is None:
            return
        if now - last > float(timeout_s):
            if not self._hb_watch_alerted:
                self._hb_watch_alerted = True
                self._log("error", f"PLC 心跳丢失: {point} 已 {now - last:.0f}s 未变化")
                if (cfg.get("action") or "alarm") == "alarm":
                    self._trigger_alarm(cfg.get("event") or "event2")
        else:
            if self._hb_watch_alerted:
                self._log("event", "PLC 心跳恢复")
            self._hb_watch_alerted = False

    # ================= 触发规则求值 =================

    def _cond_raw(self, cond: dict, values: dict) -> bool:
        """条件的瞬时布尔态 (change 型另路处理)。"""
        v = values.get(cond.get("point"))
        trig = (cond.get("trigger") or "rising").lower()
        if trig in ("rising", "level"):
            return bool(v)
        if trig == "falling":
            return not bool(v)
        if trig == "equals":
            return _loose_eq(v, cond.get("value"))
        if trig == "not_equals":
            return not _loose_eq(v, cond.get("value"))
        if trig == "in":
            return any(_loose_eq(v, x) for x in (cond.get("value") or []))
        if trig == "range":
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return False
            lo, hi = cond.get("min"), cond.get("max")
            return (lo is None or fv >= float(lo)) and (hi is None or fv <= float(hi))
        return False

    def _eval_rules(self, prev_values: dict, values: dict):
        now = time.time()
        for idx, rule in enumerate(self.read_rules):
            try:
                self._eval_rule(idx, rule, prev_values, values, now)
            except Exception as e:
                self._log("error", f"规则[{rule.get('name') or idx}]求值异常: {e}")
                logger.exception("[PLC] %s 规则求值异常", self.name)

    def _eval_rule(self, idx: int, rule: dict, prev_values: dict,
                   values: dict, now: float):
        conds = rule.get("when") or []
        if not conds:
            return
        state = self._rule_states[idx]
        debounce = float(rule.get("debounce_ms") or 0) / 1000.0

        edge_confirmed = False       # 本轮是否有边沿条件确认
        has_edge = False
        all_state_ok = True

        for ci, cond in enumerate(conds):
            cs = state.conds[ci]
            trig = (cond.get("trigger") or "rising").lower()
            point = cond.get("point")

            if trig == "change":
                has_edge = True
                cur = values.get(point)
                if point in prev_values and prev_values.get(point) != cur:
                    cs.pending_ts, cs.pending_value = now, cur
                if cs.pending_ts is not None:
                    if values.get(point) != cs.pending_value:
                        cs.pending_ts = None       # 又变了, 重新计防抖
                    elif now - cs.pending_ts >= debounce:
                        cs.pending_ts = None
                        edge_confirmed = True
                continue

            raw = self._cond_raw(cond, values)

            if trig in ("rising", "falling") or (
                    trig in ("equals", "not_equals", "in", "range")
                    and ci == 0 and not any(
                        (c.get("trigger") or "rising").lower() in _EDGE_TRIGGERS
                        for c in conds)):
                # 边沿语义: rising/falling 恒为边沿; 无边沿条件的规则,
                # 首条件的 假→真 转变承担边沿角色 (纯状态规则的进入沿)
                has_edge = True
                if cs.prev_raw is False and raw:
                    cs.pending_ts = now
                if not raw:
                    cs.pending_ts = None
                if cs.pending_ts is not None and now - cs.pending_ts >= debounce:
                    cs.pending_ts = None
                    edge_confirmed = True
                cs.prev_raw = raw
                # rising 触发位卡住检测数据
                if trig == "rising":
                    if raw:
                        if state.stuck_since is None:
                            state.stuck_since = now
                    else:
                        state.stuck_since = None
                        state.stuck_reported = False
                continue

            if trig == "level":
                min_ms = float(cond.get("min_ms") or 0) / 1000.0
                if raw:
                    if cs.level_since is None:
                        cs.level_since = now
                    if now - cs.level_since < min_ms:
                        all_state_ok = False
                else:
                    cs.level_since = None
                    cs.level_fired = False
                    all_state_ok = False
                continue

            # 其余状态型条件 (equals 家族作为伴随条件)
            if not raw:
                all_state_ok = False

        # 纯 level 规则: 保持满足 → 触发一次, 掉下去后重新武装
        if not has_edge:
            level_conds = [(ci, c) for ci, c in enumerate(conds)
                           if (c.get("trigger") or "").lower() == "level"]
            if level_conds and all_state_ok:
                cs0 = state.conds[level_conds[0][0]]
                if not cs0.level_fired:
                    cs0.level_fired = True
                    self._fire_rule(idx, rule, values, now)
            return

        if edge_confirmed and all_state_ok:
            min_interval = float(rule.get("min_interval_ms") or 0) / 1000.0
            if now - state.last_fire_ts >= min_interval:
                state.last_fire_ts = now
                self._fire_rule(idx, rule, values, now)

        # 卡住兜底 (触发位长期不复位 → PLC 侧握手逻辑可能死了)
        stuck_timeout = rule.get("stuck_timeout_s")
        if stuck_timeout and state.stuck_since is not None \
                and not state.stuck_reported \
                and now - state.stuck_since > float(stuck_timeout):
            state.stuck_reported = True
            action = (rule.get("stuck_action") or "log").lower()
            self._log("error",
                      f"规则[{rule.get('name') or idx}]触发位卡住超过 {stuck_timeout}s")
            if action == "alarm":
                self._trigger_alarm(rule.get("stuck_event") or "event2")

    def _fire_rule(self, idx: int, rule: dict, values: dict, now: float):
        self.counters["rule_fires"] += 1
        rule_name = rule.get("name") or f"规则{idx}"
        self._log("event", f"触发 [{rule_name}]")
        snapshot = dict(values)
        self._manager.submit_actions(self, rule, snapshot)

        # 握手 (维度四): 动作提交后立刻按 ack_mode 回执/复位
        ack_mode = (rule.get("ack_mode") or "none").lower()
        try:
            if ack_mode == "write_ack" and rule.get("ack_point"):
                ack_value = rule.get("ack_value", 1)
                self.enqueue_write(
                    rule["ack_point"], ack_value,
                    reset_after_ms=rule.get("clear_after_ms"),
                    reset_value=rule.get("ack_clear_value"))
            elif ack_mode == "self_clear":
                # 视觉直接把(第一个边沿条件的)触发位写回 0
                for cond in (rule.get("when") or []):
                    if (cond.get("trigger") or "rising").lower() in _EDGE_TRIGGERS:
                        self.enqueue_write(cond.get("point"), False)
                        break
            # none / level_follow: 不回执 (PLC 自复位 / 电平语义)
        except Exception as e:
            self._log("error", f"规则[{rule_name}]握手回执失败: {e}")

    # ================= 杂项 =================

    def _dispatch_conn_event(self, event: str):
        try:
            self._manager.dispatch_event(event, {"connection": self.name},
                                         channel_id=None, only_engine=self)
        except Exception:
            pass

    def _trigger_alarm(self, event_type: str):
        try:
            from backend.api.alarm import alarm_router
            ch = int(self.options.get("default_channel") or 0)
            alarm_router.trigger_alarm(event_type, channel_id=ch)
        except Exception as e:
            logger.warning("[PLC] %s 触发报警失败: %s", self.name, e)

    def _log(self, direction: str, detail: str):
        self.io_log.append({"ts": time.time(), "dir": direction, "detail": detail})
