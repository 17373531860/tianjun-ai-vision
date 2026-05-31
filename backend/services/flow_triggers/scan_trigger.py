"""ScanTrigger — 扫码绑定模式 (RFC 11 M3).

策略:
  入口扫一次, 扫到的码即工件 serial_no. 后续工位的 cycle_start 按 FIFO 顺序
  绑到队列首个 in-flight run.

实现路径:
  1. Coordinator 在 mes_hooks.on_scan_received 的钩子点调
     coordinator.on_scan_received(channel_id, barcode, device_id, db).
  2. Coordinator 找到 channel 对应的 flow, 分发给本 trigger.evaluate_scan.
  3. ScanTrigger 判断: 本 channel 是否入口工位 + scan_device_id 是否匹配
     → 是 → 返回 barcode 作为 serial_no, Coordinator 调 on_workpiece_enter.

scan_pair 互斥:
  若该 channel 属于某 flow, mes_hooks 的 scan_pair 路径会跳过 (互斥).
  实现位置: mes_hooks._handle_scan 开头新增守门点, 查询
  WorkpieceFlowCoordinator.is_channel_in_flow(channel_id) → True 则走 flow 路径.

scan_bind_strategy:
  - entry: 入口工位的扫码即工件入口 (依赖 ScannerDevice.broadcast_channels 把
    扫到的码广播给后续工位的 cycle_start). 流水线中段工位收到的 scan_received
    会被 ScanTrigger 静默忽略 (返回 None).
  - each_station: 每工位前都扫一次. 第一次扫到 → 创建 flow_run + 绑入口工位.
    后续工位扫到同 serial_no → 1 秒内幂等去重 (由 Coordinator.on_workpiece_enter
    层处理), 不创建新 run.
"""
from __future__ import annotations

from typing import Optional

from backend.services.flow_triggers.base import FlowTriggerBase


class ScanTrigger(FlowTriggerBase):
    """扫码绑定触发器."""

    def evaluate_scan(
        self,
        barcode: str,
        scanner_device_id: Optional[int] = None,
        channel_id: Optional[int] = None,
    ) -> Optional[str]:
        """扫码事件 → 决定是否触发新工件入口.

        策略:
          - entry 模式: 仅当 channel_id 是入口工位 (station_channel_ids[0]) 时触发.
          - each_station 模式: 任何工位扫码都触发, Coordinator 层做幂等去重.

        scan_device_id 匹配:
          - 若 flow 配置了 scan_device_id (非 None) → 仅匹配的扫码器触发.
          - 否则 → 任何扫码器都可触发 (适用于 broadcast_channels 场景).

        返回:
          barcode (触发新工件入口) 或 None (静默忽略).
        """
        if not self._flow or not barcode:
            return None

        # scan_device_id 匹配检查
        configured_device = self._flow.get("scan_device_id")
        if configured_device is not None and \
           scanner_device_id is not None and \
           int(configured_device) != int(scanner_device_id):
            return None

        stations = self._flow.get("station_channel_ids", [])
        if not stations:
            return None

        strategy = self._flow.get("scan_bind_strategy", "entry")

        if strategy == "entry":
            # 仅入口工位触发
            entry_channel = stations[0]
            if channel_id is not None and int(channel_id) != int(entry_channel):
                return None
            return barcode

        # each_station: 任何工位扫码都触发. 上层 Coordinator 做幂等.
        if channel_id is not None and int(channel_id) not in stations:
            return None  # 该 channel 不属于本 flow
        return barcode

    def evaluate_cycle_start(self, channel_id: int) -> Optional[str]:
        """ScanTrigger 不在 cycle_start 自动创建工件 (必须扫码触发)."""
        return None
