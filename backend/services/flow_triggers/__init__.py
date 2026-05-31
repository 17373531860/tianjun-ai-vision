"""工件流转触发器 (RFC 11).

三种触发模式:
  - TimeWindowTrigger: 无扫码器 FIFO. 入口工位 cycle_start 即视为新工件进入.
  - ScanTrigger (M3): 入口扫码触发, 复用 mes_hooks.on_scan_received.
  - PhysicalTrigger (M7): 接 external_device IO 信号.

设计哲学: 触发器是纯策略对象, 不持有 long-living 状态. Coordinator 在
reload_flows 时为每个 flow 实例化一个 trigger, 在事件来时分发给 trigger
评估; trigger 决定是否触发 on_workpiece_enter.
"""
from backend.services.flow_triggers.base import FlowTriggerBase
from backend.services.flow_triggers.time_window_trigger import TimeWindowTrigger
from backend.services.flow_triggers.scan_trigger import ScanTrigger
from backend.services.flow_triggers.physical_trigger import PhysicalTrigger

__all__ = ["FlowTriggerBase", "TimeWindowTrigger", "ScanTrigger", "PhysicalTrigger"]
