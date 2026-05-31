"""FlowTriggerBase — 工件流转触发器抽象 (RFC 11).

每个 enabled 的 flow_config 对应一个 trigger 实例.
Coordinator 在 reload_flows 时实例化 / detach.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class FlowTriggerBase(ABC):
    """工件流转触发器抽象.

    生命周期:
      __init__ → attach(coordinator, flow) → ...运行... → detach()

    评估接口:
      evaluate_cycle_start(channel_id) -> Optional[serial_no]
        在每次 VSM cycle_start 时调用. 返回 None 表示不触发新工件入口;
        返回非 None 表示自动生成的 serial_no, Coordinator 将立即调
        on_workpiece_enter(self.flow['id'], serial_no, trigger_mode, ...).

      evaluate_scan(barcode, scanner_device_id) -> Optional[serial_no]
        在每次 mes_hooks.on_scan_received 时调用. 返回 None 表示不触发;
        返回非 None 表示扫到的码即工件 serial_no.

      evaluate_physical_signal(device_id, signal_key) -> Optional[serial_no]
        在每次 external_device IO 信号到达时调用. 返回 None 表示不触发.

    默认实现都返回 None, 子类按需 override.
    """

    def __init__(self) -> None:
        self._coordinator: Any = None
        self._flow: Optional[Dict[str, Any]] = None
        self._attached = False

    def attach(self, coordinator: Any, flow: Dict[str, Any]) -> None:
        """绑定到协调器 + flow 配置. 子类可 override 注册外部回调."""
        self._coordinator = coordinator
        self._flow = dict(flow)  # snapshot 防止外部修改
        self._on_attach()
        self._attached = True

    def detach(self) -> None:
        """卸载. 子类可 override 注销外部回调."""
        if not self._attached:
            return
        self._on_detach()
        self._attached = False
        self._coordinator = None
        self._flow = None

    def _on_attach(self) -> None:
        """子类可 override 注册外部回调 (scan / physical 用)."""
        pass

    def _on_detach(self) -> None:
        """子类可 override 注销外部回调."""
        pass

    # ===== 评估接口 (默认全返回 None, 子类按需 override) =====

    def evaluate_cycle_start(self, channel_id: int) -> Optional[str]:
        return None

    def evaluate_scan(
        self,
        barcode: str,
        scanner_device_id: Optional[int] = None,
    ) -> Optional[str]:
        return None

    def evaluate_physical_signal(
        self,
        device_id: int,
        signal_key: str,
    ) -> Optional[str]:
        return None

    # ===== 工具 =====

    @property
    def flow(self) -> Optional[Dict[str, Any]]:
        return self._flow

    @property
    def coordinator(self) -> Any:
        return self._coordinator
