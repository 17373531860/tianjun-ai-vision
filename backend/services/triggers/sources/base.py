"""触发源基类 — 两种信号形态:

- 电平型 (pixel_region 等): 源线程按采样周期持续 emit_level(bool),
  引擎做 防抖 → 边沿 (rising/falling) 判定
- 脉冲型 (http/timer/mock/hid_key 等): 事件到来时 emit_pulse(meta),
  引擎跳过边沿判定, 只做 min_interval / 生效窗口

源自身线程崩溃由引擎监督重启 (错误隔离); 无线程的被动源 (http/mock)
start/stop 为空操作。
"""
from typing import Any, Callable, Dict, Optional


class BaseTriggerSource:
    """params 校验错误直接在 __init__ raise, 引擎标 config_error。"""

    type_name = "base"
    kind = "pulse"          # "level" | "pulse" (决定规则 when 的默认语义)

    def __init__(self, params: dict,
                 emit_level: Callable[[bool, Optional[dict]], None],
                 emit_pulse: Callable[[Optional[dict]], None]):
        self.params = params or {}
        self.emit_level = emit_level
        self.emit_pulse = emit_pulse

    def start(self) -> None:
        """拉起源线程 (被动源空操作)。"""

    def stop(self) -> None:
        """停源线程, 幂等。"""

    def snapshot(self) -> Dict[str, Any]:
        """源侧诊断信息 (进 /status)。"""
        return {}

    @classmethod
    def validate_params(cls, params: dict) -> Optional[str]:
        """返回错误文案或 None (API 保存前校验)。"""
        return None
