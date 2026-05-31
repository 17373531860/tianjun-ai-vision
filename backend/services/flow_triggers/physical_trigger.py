"""PhysicalTrigger — 物理 GPIO/Modbus 信号触发器 (RFC 11 M7).

设计哲学:
  - 物理触发器是「策略对象」, 不直接持有硬件循环.
  - 真实硬件信号由 external_device (GPIO/Modbus) 的协议循环上报, 由 Coordinator
    on_physical_signal 入口统一分发. 本 trigger 仅做"该信号是否应触发新工件"
    的判定.
  - v1 阶段不强行接管 external_device, 只暴露 evaluate_physical_signal 钩子 +
    自动序号生成. 客户场景:
      - GPIO 高电平 → 整条流水线 cycle_start
      - 光电开关边沿 → 工件入口
      - Modbus holding register 0x100 变化 → 工件入口

配置 (WorkpieceFlowConfig.physical_trigger_config JSON):
{
  "device_id": 1,             # external_device ID (必填)
  "signal_key": "io_in_1",    # 信号 key, 与 device 上报 signal_key 必须匹配
  "edge": "rising",           # rising / falling / any (v1 默认 rising)
  "serial_prefix": "PHY",     # 自动序号前缀 (默认 PHY)
  "dedup_window_ms": 500      # 同 signal_key 去抖窗口, 防机械抖动
}

v1 实现简化:
  - 不真做硬件去抖, 仅在 trigger 实例内做"同 signal_key + 500ms 内重复忽略"
  - 不做边沿过滤 (上层 external_device 已做)
  - 自动序号: f"{serial_prefix}-{int(time.time()*1000)}"
"""
from __future__ import annotations

import time
import threading
from typing import Any, Dict, Optional

from backend.services.flow_triggers.base import FlowTriggerBase


class PhysicalTrigger(FlowTriggerBase):
    """物理 GPIO/Modbus 信号触发器."""

    def __init__(self) -> None:
        super().__init__()
        self._last_signal_at: Dict[str, float] = {}  # signal_key -> last ts
        self._lock = threading.Lock()

    def _resolve_cfg(self) -> Dict[str, Any]:
        if not self._flow:
            return {}
        raw = self._flow.get("physical_trigger_config")
        if isinstance(raw, dict):
            return raw
        return {}

    def evaluate_physical_signal(
        self,
        device_id: int,
        signal_key: str,
    ) -> Optional[str]:
        """物理信号到来 → 判定是否触发新工件入口.

        匹配规则:
          1. device_id 必须等于 config.device_id
          2. signal_key 必须等于 config.signal_key (或 config 未配置时全接)
          3. 同 signal_key 在 dedup_window_ms 内重复 → 忽略
          4. 通过 → 返回自动序号

        Returns:
            serial_no (e.g. "PHY-1716000000000") 或 None
        """
        if not self._attached or not self._flow:
            return None

        cfg = self._resolve_cfg()
        target_device = cfg.get("device_id")
        if target_device is not None and target_device != device_id:
            return None  # 不是本 flow 关注的设备

        target_signal = cfg.get("signal_key")
        if target_signal and target_signal != signal_key:
            return None  # 不是本 flow 关注的信号

        # 去抖
        dedup_ms = int(cfg.get("dedup_window_ms", 500))
        now = time.time() * 1000
        with self._lock:
            last = self._last_signal_at.get(signal_key, 0.0)
            if dedup_ms > 0 and (now - last) < dedup_ms:
                return None
            self._last_signal_at[signal_key] = now

        # 生成序号
        prefix = cfg.get("serial_prefix") or "PHY"
        return f"{prefix}-{int(now)}"

    def _on_detach(self) -> None:
        with self._lock:
            self._last_signal_at.clear()
