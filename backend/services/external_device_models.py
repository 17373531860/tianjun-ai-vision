"""DeviceConnection 数据类 (从 external_device.py 抽出, 供 mixin 共用避免循环 import)。"""
import threading
from collections import deque
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

    # v3.31 串口指令应答模式: 外部(去皮/置零等)控制指令排入此队列，
    # 由设备线程在下一轮轮询时取出发送，避免多线程并发写同一串口。
    _command_queue: deque = field(default_factory=deque, repr=False)

    # modbus_pulse「完成脉冲」运行时状态。推理线程只往 _pulse_queue append +
    # set(_pulse_wakeup)，真正的 Modbus 写由设备线程执行 —— 热路径零 I/O。
    _pulse_queue: deque = field(default_factory=deque, repr=False)
    _pulse_wakeup: threading.Event = field(default_factory=threading.Event, repr=False)
    _last_pulse_enqueued_at: float = 0.0     # 冷却(cooldown_ms)基准, 入队即刷新
    pulse_count: int = 0
    last_pulse_result: Optional[str] = None
    last_pulse_at_wall: float = 0.0

    # v2.7.5 稳定判定运行时状态
    _stable_samples: list = field(default_factory=list, repr=False)
    _stable_state: str = "idle"
    _stable_value: Optional[float] = None
    _last_reported_value: Optional[float] = None
    _weight_onset_time: float = 0
    _no_barcode_alarm_fired: bool = False
