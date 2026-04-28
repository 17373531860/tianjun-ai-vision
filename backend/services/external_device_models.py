"""DeviceConnection 数据类 (从 external_device.py 抽出, 供 mixin 共用避免循环 import)。"""
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DeviceConnection:
    device_id: int
    name: str
    device_role: str
    protocol: str
    ip: Optional[str]
    port: Optional[int]
    serial_port: Optional[str]
    serial_baud: int
    protocol_config: dict
    parse_mode: str
    parse_config: dict
    station_id: Optional[str]
    channel_id: Optional[int]
    data_target: str
    validation_rules: dict
    enabled: bool
    pairing_group: Optional[str] = None

    # v2.7.5 稳定值判定配置
    stable_enabled: bool = True
    stable_delta: float = 0.05
    stable_count: int = 5
    zero_threshold: float = 0.05
    # v2.7.5 有重无码告警配置
    weight_no_barcode_alarm_enabled: bool = False
    weight_no_barcode_alarm_delay_sec: int = 10

    # v3.1.1 配对模式: stable (老逻辑) / instant (扫码瞬间立即绑最近读数, 派发后清 buffer)
    pairing_mode: str = "stable"

    status: str = "disconnected"
    last_data: Optional[str] = None
    last_data_time: float = 0
    last_parsed: Optional[dict] = None
    last_error: str = ""
    _thread: Optional[threading.Thread] = field(default=None, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)

    # v2.7.5 稳定判定运行时状态
    _stable_samples: list = field(default_factory=list, repr=False)
    _stable_state: str = "idle"
    _stable_value: Optional[float] = None
    _last_reported_value: Optional[float] = None
    _weight_onset_time: float = 0
    _no_barcode_alarm_fired: bool = False
