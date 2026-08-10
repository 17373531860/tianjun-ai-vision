"""TriggerChannelEngine — 一个触发源实例的运行时。

职责: 源生命周期监督 + 信号判定 (防抖/边沿/最小间隔/生效窗口) + 动作派发。
判定在源回调线程内做 (纯字典运算, 微秒级); 动作经 manager 共享动作线程执行,
不阻塞源采样 (不变量 15)。

对动作注册表暴露鸭子接口 (与 PLCPointEngine 同形):
    .name / .vars / .options / ._log(dir, detail) / ._trigger_alarm(event)

规则 schema (rules 列表每项):
    {
      "name": "结算",                        # 可选
      "when": [{"trigger": "rising"}],       # rising|falling|both|pulse
                                             # 缺省: 电平源 rising, 脉冲源 pulse
      "debounce_ms": 300,                    # 电平稳定 N ms 才认 (电平源)
      "min_interval_ms": 3000,               # 两次触发最小间隔 (防连击)
      "active_window": {                     # 可选, 全部条件 AND
          "only_detecting": true,            # 绑定工位检测运行中才生效
          "time_from": "08:00", "time_to": "20:00"   # 每日时间窗 (可跨零点)
      },
      "channel": 0,                          # 动作默认工位 (让位于 action.channel)
      "actions": [{"do": "manual_settle"}, ...]
    }
"""
import logging
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.services.triggers.sources import get_source_class

logger = logging.getLogger(__name__)

_EDGE_WHEN = ("rising", "falling", "both")


class _RuleState:
    __slots__ = ("last_fire", "pending_value", "pending_since", "confirmed")

    def __init__(self):
        self.last_fire = 0.0
        self.pending_value: Optional[bool] = None
        self.pending_since = 0.0
        # None = 未锁存 (启动时信号已 active 不触发, 对齐 PLC "边沿要有边")
        self.confirmed: Optional[bool] = None


class TriggerChannelEngine:

    def __init__(self, row: dict, manager):
        self.trigger_id: int = row["id"]
        self.name: str = row["name"]
        self.type: str = row["type"]
        self.params: dict = row.get("params") or {}
        self.rules: List[dict] = row.get("rules") or []
        self.options: dict = row.get("options") or {}
        self.manager = manager

        self.vars: Dict[str, Any] = {}
        self.status = "stopped"       # starting/running/config_error/error/stopped
        self.last_error: Optional[str] = None
        self.started_at: Optional[float] = None
        self.counters = {"signals": 0, "pulses": 0, "fires": 0, "suppressed": 0}
        self.io_log: deque = deque(maxlen=int(self.options.get("log_size") or 200))
        self.history: deque = deque(maxlen=int(self.options.get("history_size") or 100))
        self.source = None
        self._rule_states = [_RuleState() for _ in self.rules]
        self._sig_lock = threading.Lock()

    # ================= 生命周期 =================

    def start(self):
        self.status = "starting"
        try:
            cls = get_source_class(self.type)
            self.source = cls(self.params, self._on_level, self._on_pulse)
        except (ValueError, RuntimeError) as e:
            self.status = "config_error"
            self.last_error = str(e)
            self._log("error", f"触发源初始化失败: {e}")
            return
        try:
            self.source.start()
            self.status = "running"
            self.started_at = time.time()
            self._log("event", f"触发源已启动 (type={self.type})")
        except Exception as e:
            self.status = "error"
            self.last_error = str(e)
            self._log("error", f"触发源启动失败: {e}")
            logger.exception("[Trigger] %s 启动失败", self.name)

    def stop(self):
        if self.source is not None:
            try:
                self.source.stop()
            except Exception:
                pass
        self.status = "stopped"

    # ================= 信号入口 (源回调线程) =================

    def _on_level(self, value: bool, meta: dict = None):
        """电平型信号: 每规则独立 防抖 → 边沿锁存 → 触发。"""
        now = time.time()
        with self._sig_lock:
            self.counters["signals"] += 1
            for idx, rule in enumerate(self.rules):
                trig = self._rule_trigger(rule)
                if trig not in _EDGE_WHEN:
                    continue
                rs = self._rule_states[idx]
                if value != rs.pending_value:
                    rs.pending_value = value
                    rs.pending_since = now
                if value == rs.confirmed:
                    continue
                debounce = float(rule.get("debounce_ms") or 0) / 1000.0
                if (now - rs.pending_since) < debounce:
                    continue  # 还没稳定够 (debounce=0 时当次即确认)
                prev, rs.confirmed = rs.confirmed, value
                if prev is None:
                    continue  # 首次锁存不触发 (启动即 active 不算边沿)
                edge = "rising" if value else "falling"
                if trig == "both" or trig == edge:
                    self._try_fire(idx, rule, edge, meta, now)

    def _on_pulse(self, meta: dict = None):
        """脉冲型信号: 跳过边沿, 只做 min_interval / 生效窗口。"""
        now = time.time()
        with self._sig_lock:
            self.counters["pulses"] += 1
            for idx, rule in enumerate(self.rules):
                if self._rule_trigger(rule) != "pulse":
                    continue
                self._try_fire(idx, rule, "pulse", meta, now)

    def _rule_trigger(self, rule: dict) -> str:
        when = rule.get("when") or []
        for cond in when:
            if isinstance(cond, dict) and cond.get("trigger"):
                return str(cond["trigger"]).lower()
        # 缺省按源形态
        kind = getattr(self.source, "kind", "pulse") if self.source else "pulse"
        return "rising" if kind == "level" else "pulse"

    # ================= 触发判定 =================

    def _try_fire(self, idx: int, rule: dict, edge: str, meta: dict, now: float):
        rs = self._rule_states[idx]
        rule_name = rule.get("name") or f"规则{idx + 1}"
        min_iv = float(rule.get("min_interval_ms") or 0) / 1000.0
        if min_iv and (now - rs.last_fire) < min_iv:
            self.counters["suppressed"] += 1
            self._log("event", f"[{rule_name}] {edge} 被 min_interval 拦截")
            return
        blocked = self._window_blocked(rule)
        if blocked:
            self.counters["suppressed"] += 1
            self._log("event", f"[{rule_name}] {edge} 在生效窗口外 ({blocked})")
            return
        rs.last_fire = now
        self.counters["fires"] += 1
        snapshot = {**(meta or {}), "trigger_name": self.name,
                    "trigger_type": self.type, "edge": edge}
        self.history.appendleft({
            "ts": now, "rule": rule_name, "edge": edge,
            "meta": {k: v for k, v in (meta or {}).items()},
        })
        self._log("event", f"[{rule_name}] 触发 ({edge}) → "
                           f"{len(rule.get('actions') or [])} 个动作")
        self.manager.submit_actions(self, rule, snapshot)

    def _window_blocked(self, rule: dict) -> Optional[str]:
        """返回窗口外原因或 None (在窗口内)。"""
        win = rule.get("active_window") or {}
        if not win:
            return None
        if win.get("only_detecting"):
            ch = rule.get("channel")
            if ch is None:
                ch = (self.options or {}).get("default_channel") or 0
            try:
                from backend.api.channel_manager import channel_manager
                mgr = channel_manager.channels.get(int(ch))
                if not (mgr and mgr.is_detecting):
                    return f"工位{ch}未在检测"
            except Exception:
                return "工位状态不可查"
        t_from, t_to = win.get("time_from"), win.get("time_to")
        if t_from and t_to:
            hm = datetime.now().strftime("%H:%M")
            if t_from <= t_to:
                if not (t_from <= hm <= t_to):
                    return f"不在 {t_from}~{t_to}"
            else:  # 跨零点
                if not (hm >= t_from or hm <= t_to):
                    return f"不在 {t_from}~{t_to}"
        return None

    # ================= 鸭子接口 (动作注册表用) =================

    def _log(self, direction: str, detail: str):
        self.io_log.appendleft({"ts": time.time(), "dir": direction, "detail": detail})

    def _trigger_alarm(self, event_type: str):
        try:
            ch = int((self.options or {}).get("default_channel") or 0)
            from backend.api.alarm import alarm_router
            alarm_router.trigger_alarm(event_type, channel_id=ch)
        except Exception as e:
            logger.warning("[Trigger] %s 报警触发失败: %s", self.name, e)

    # ================= 诊断 =================

    def snapshot(self) -> dict:
        return {
            "trigger_id": self.trigger_id,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "last_error": self.last_error,
            "started_at": self.started_at,
            "counters": dict(self.counters),
            "source": (self.source.snapshot() if self.source else {}),
            "vars": dict(self.vars),
        }
