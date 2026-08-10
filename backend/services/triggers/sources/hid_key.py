"""hid_key — HID 键盘类设备按键触发源 (脚踏板/USB 按钮盒/无线遥控器)。

这类设备插上就是一把键盘, 用 pynput 全局钩子监听 (依赖惰性 import,
缺库只影响本类型)。与 v3.20 USB 键盘扫码枪 (前端捕获成串条码) 互补:
扫码枪听"字符串+回车", 本源听"单个功能键", 建议配 F 区/小键盘等不常用键
避免与正常打字冲突。

params:
    key            str   目标键名: "f9" / "a" / "space" / "enter" / "num_5" ...
                         (pynput 键名; 字符键直接写字符)
    long_press_ms  int   可选; 配了则按住超过 N ms 判长按,
                         meta.press = "long"|"short" (松开时发脉冲)
                         不配则按下即发脉冲

信号语义: 脉冲型。meta = {"fired_by": "hid_key", "key": ..., "press": ...}
"""
import threading
import time
from typing import Optional

from backend.services.triggers.sources.base import BaseTriggerSource


def _normalize_key(k) -> str:
    """pynput 的 Key.f9 → 'f9'; KeyCode 'a' → 'a'。"""
    s = str(k)
    if s.startswith("Key."):
        return s[4:].lower()
    return s.strip("'").lower()


class HidKeySource(BaseTriggerSource):
    type_name = "hid_key"
    kind = "pulse"

    def __init__(self, params, emit_level, emit_pulse):
        super().__init__(params, emit_level, emit_pulse)
        err = self.validate_params(self.params)
        if err:
            raise ValueError(err)
        self.key = str(self.params["key"]).strip().lower()
        self.long_press_ms = self.params.get("long_press_ms")
        self._listener = None
        self._pressed_at: Optional[float] = None
        self._press_lock = threading.Lock()
        self._hit_count = 0
        self._last_seen_key: Optional[str] = None   # 联调: 观察踏板实际输出键

    @classmethod
    def validate_params(cls, params: dict) -> Optional[str]:
        if not str((params or {}).get("key") or "").strip():
            return "hid_key 需要 key (目标键名, 如 f9)"
        lp = (params or {}).get("long_press_ms")
        if lp is not None and (not isinstance(lp, (int, float)) or lp < 50):
            return "long_press_ms 须为 >=50 的毫秒数"
        return None

    def start(self):
        from pynput import keyboard  # 惰性: 缺库只影响本类型
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release, daemon=True)
        self._listener.start()

    def stop(self):
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def _on_press(self, k):
        name = _normalize_key(k)
        self._last_seen_key = name
        if name != self.key:
            return
        if self.long_press_ms:
            with self._press_lock:
                if self._pressed_at is None:   # 系统按住自动重发只记首按
                    self._pressed_at = time.time()
        else:
            self._hit_count += 1
            self.emit_pulse({"fired_by": "hid_key", "key": name})

    def _on_release(self, k):
        if _normalize_key(k) != self.key or not self.long_press_ms:
            return
        with self._press_lock:
            pressed_at, self._pressed_at = self._pressed_at, None
        if pressed_at is None:
            return
        held_ms = (time.time() - pressed_at) * 1000
        press = "long" if held_ms >= float(self.long_press_ms) else "short"
        self._hit_count += 1
        self.emit_pulse({"fired_by": "hid_key", "key": self.key,
                         "press": press, "held_ms": round(held_ms)})

    def snapshot(self):
        return {"key": self.key, "long_press_ms": self.long_press_ms,
                "hit_count": self._hit_count,
                "last_seen_key": self._last_seen_key}
