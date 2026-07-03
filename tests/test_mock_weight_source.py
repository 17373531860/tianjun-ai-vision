"""模拟称重协议 (_mock_weight_loop) 单测 —— 无硬件验证脚本播放 + 去皮归零。

模拟源不连真串口, 按 protocol_config.mock_script 生成重量数据流, 复用 _on_raw_data 上报。
这里用桩替身拦截 _on_raw_data, 隔离验证模拟循环本身(不碰解析/派发/DB)。
"""
import threading
import time

from backend.services.external_device_models import DeviceConnection
from backend.services.external_device_protocols import ExternalDeviceProtocolsMixin


class _Harness(ExternalDeviceProtocolsMixin):
    def __init__(self):
        self.emitted = []

    def _on_raw_data(self, conn, raw):
        self.emitted.append(raw)


def _make_conn(**pc):
    return DeviceConnection(
        device_id=1, name="模拟秤", device_role="weight", protocol="mock_weight",
        ip=None, port=None, serial_port=None, serial_baud=9600,
        protocol_config=pc, parse_mode="direct", parse_config={},
        station_id=None, channel_id=0, data_target="cluster",
        validation_rules={}, enabled=True,
    )


def test_mock_weight_plays_script_to_target():
    """按脚本播放, 末段应输出目标净重 +0.500。"""
    h = _Harness()
    conn = _make_conn(
        mock_script=[{"weight": 0.0, "hold": 0.2}, {"weight": 0.5, "hold": 0.3}],
        poll_interval=0.05, loop=False, decimals=3,
    )
    t = threading.Thread(target=h._mock_weight_loop, args=(conn,))
    t.start()
    t.join(timeout=3)
    assert not t.is_alive(), "loop=False 应自然结束"
    assert h.emitted, "应产生模拟读数"
    assert any(v.strip() == "+0.500" for v in h.emitted), f"末段应到量 0.500, 实际: {h.emitted}"


def test_mock_weight_tare_zeroes_net():
    """运行中途下发去皮(T)指令, 之后净重应归零。"""
    h = _Harness()
    conn = _make_conn(
        mock_script=[{"weight": 0.5, "hold": 1.0}],
        poll_interval=0.05, loop=False, decimals=3,
    )

    def fire_tare():
        time.sleep(0.2)
        conn._command_queue.append("T")

    threading.Thread(target=fire_tare).start()
    h._mock_weight_loop(conn)

    assert h.emitted[0].strip() == "+0.500", "去皮前应显示毛重 0.500"
    assert h.emitted[-1].strip() == "+0.000", f"去皮后净重应归零, 实际末值: {h.emitted[-1]}"


def test_mock_weight_custom_format():
    """mock_format 可模仿真秤帧格式。"""
    h = _Harness()
    conn = _make_conn(
        mock_script=[{"weight": 0.071, "hold": 0.2}],
        poll_interval=0.05, loop=False, decimals=3,
        mock_format="ST,NT,{value}kg",
    )
    h._mock_weight_loop(conn)
    assert h.emitted, "应产生读数"
    assert h.emitted[0] == "ST,NT,+0.071kg", f"格式应套用, 实际: {h.emitted[0]}"
