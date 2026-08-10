"""mock — 联调/CI 测试注入触发源 (被动源, 无线程)。

无必填参数。两个注入口 (API 层调用):
    fire(meta)             模拟一次脉冲
    set_level(bool, meta)  模拟电平变化 (测试引擎防抖/边沿逻辑)
"""
from backend.services.triggers.sources.base import BaseTriggerSource


class MockTriggerSource(BaseTriggerSource):
    type_name = "mock"
    kind = "pulse"

    def __init__(self, params, emit_level, emit_pulse):
        super().__init__(params, emit_level, emit_pulse)
        self._fire_count = 0

    def fire(self, meta: dict = None):
        self._fire_count += 1
        self.emit_pulse({"fired_by": "mock", **(meta or {})})

    def set_level(self, value: bool, meta: dict = None):
        self.emit_level(bool(value), meta)

    def snapshot(self):
        return {"fire_count": self._fire_count}
