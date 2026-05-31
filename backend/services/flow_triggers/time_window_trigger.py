"""TimeWindowTrigger — 无扫码器 FIFO 模式 (RFC 11 M2).

策略:
  入口工位 (station_channel_ids[0]) 的每次 cycle_start 即视为"新工件进入流水线".
  自动生成 serial_no = "auto-<flow_id>-<8hex>".
  后续工位的 cycle_start 按 FIFO 顺序绑到队列首个 in-flight run.

适用场景:
  无扫码器, 工件按传送带 FIFO 顺序到达各工位. 节拍稳定时可用,
  节拍抖动 / 跳工位时容易错绑 — 文档明示, 强烈推荐升级硬件加扫码器.

无状态:
  Trigger 自身不维护任何状态, 序号生成是无副作用的 uuid.
  fifo_max_in_flight 上限的判断在 Coordinator.on_workpiece_enter 里做.
"""
from __future__ import annotations

import uuid
from typing import Optional

from backend.services.flow_triggers.base import FlowTriggerBase


class TimeWindowTrigger(FlowTriggerBase):
    """时间窗 FIFO 触发器."""

    def evaluate_cycle_start(self, channel_id: int) -> Optional[str]:
        """入口工位 cycle_start → 生成自动 serial.

        非入口工位返回 None (后续工位由 Coordinator 走 FIFO 绑 cycle, 不触发新工件入口).
        """
        if not self._flow:
            return None
        stations = self._flow.get("station_channel_ids", [])
        if not stations:
            return None
        entry_channel = stations[0]
        if channel_id != entry_channel:
            return None
        flow_id = self._flow.get("id", 0)
        return f"auto-{flow_id}-{uuid.uuid4().hex[:8]}"
