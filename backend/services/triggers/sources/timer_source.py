"""timer — 系统级定时触发源 (与检测运行状态解耦; 区别于 v3.5.x 周期内强制动作)。

params (三选一):
    interval_s   int         每 N 秒一发 (启动即计时)
    daily        ["HH:MM"]   每日定点 (本地时区, 可多个)
    (两者都配则都生效)

信号语义: 脉冲型。meta 带 {"fired_by": "interval"|"daily", "at": "..."}
"""
import threading
import time
from datetime import datetime
from typing import Optional

from backend.services.triggers.sources.base import BaseTriggerSource


class TimerSource(BaseTriggerSource):
    type_name = "timer"
    kind = "pulse"

    def __init__(self, params, emit_level, emit_pulse):
        super().__init__(params, emit_level, emit_pulse)
        err = self.validate_params(self.params)
        if err:
            raise ValueError(err)
        self.interval_s = self.params.get("interval_s")
        self.daily = list(self.params.get("daily") or [])
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fired_daily_keys = set()   # "2026-08-09 08:00" 去重

    @classmethod
    def validate_params(cls, params: dict) -> Optional[str]:
        p = params or {}
        interval, daily = p.get("interval_s"), p.get("daily")
        if not interval and not daily:
            return "timer 需要 interval_s 或 daily 至少一项"
        if interval is not None and (not isinstance(interval, (int, float)) or interval < 1):
            return "interval_s 须为 >=1 的秒数"
        for t in (daily or []):
            try:
                datetime.strptime(str(t), "%H:%M")
            except ValueError:
                return f"daily 时间格式须为 HH:MM: {t!r}"
        return None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="trigger-timer")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def _loop(self):
        next_interval = (time.time() + float(self.interval_s)) if self.interval_s else None
        while not self._stop.is_set():
            now = time.time()
            if next_interval is not None and now >= next_interval:
                next_interval = now + float(self.interval_s)
                self.emit_pulse({"fired_by": "interval"})
            if self.daily:
                dt = datetime.now()
                hm = dt.strftime("%H:%M")
                key = dt.strftime("%Y-%m-%d ") + hm
                if hm in self.daily and key not in self._fired_daily_keys:
                    self._fired_daily_keys.add(key)
                    if len(self._fired_daily_keys) > 100:
                        self._fired_daily_keys = set(list(self._fired_daily_keys)[-20:])
                    self.emit_pulse({"fired_by": "daily", "at": hm})
            self._stop.wait(0.5)

    def snapshot(self):
        return {"interval_s": self.interval_s, "daily": self.daily}
