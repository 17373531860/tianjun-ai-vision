"""串口指令应答链路的延迟回归 (2026-07 萍乡百斯特现场"延迟高"缺陷)。

旧实现两处"白等":
1. 读应答用 ser.read(256) —— 要攒满 256 字节或等满串口超时(1s)才返回,
   秤一帧只有十几字节, 每次查询固定白等 ~1s, 采样被硬锁在 ~1Hz,
   去皮稳定判定被拖到 2~3s, 工人手快时水泥被清零吃掉(结算错)。
2. 轮询间隔整段睡死 —— 去皮/置零按钮点下去要等下一轮才发,
   按钮响应 2~3s。

本文件钉死修复后的行为:
- 分隔符一到立即交货 (远小于超时);
- 迟到的应答照常收到;
- 无分隔符的帧靠短静默收尾, 不等满超时;
- 完全无应答仍按超时返回 None (行为不回退);
- 控制指令入队后立即唤醒轮询等待、马上发出。
"""
import threading
import time
from collections import deque
from types import SimpleNamespace

import pytest

from backend.services.external_device_protocols import ExternalDeviceProtocolsMixin


class ChunkSer:
    """按时间剧本供数的假串口: [(相对秒, bytes), ...]。"""

    def __init__(self, script):
        self._t0 = time.time()
        self._script = list(script)
        self._buf = b""

    def _pump(self):
        now = time.time() - self._t0
        while self._script and self._script[0][0] <= now:
            self._buf += self._script.pop(0)[1]

    @property
    def in_waiting(self):
        self._pump()
        return len(self._buf)

    def read(self, n):
        self._pump()
        out, self._buf = self._buf[:n], self._buf[n:]
        return out


FRAME = b"ST,GS,+ 1.234kg\r\n"


def test_frame_returns_immediately_on_delimiter():
    ser = ChunkSer([(0, FRAME)])
    t0 = time.time()
    line = ExternalDeviceProtocolsMixin._read_serial_frame(ser, b"\r\n", 2.0)
    elapsed = time.time() - t0
    assert line == b"ST,GS,+ 1.234kg"
    assert elapsed < 0.2, f"帧已就绪却等了 {elapsed:.2f}s (旧实现会白等满串口超时)"


def test_frame_waits_for_late_reply():
    ser = ChunkSer([(0.15, FRAME)])
    t0 = time.time()
    line = ExternalDeviceProtocolsMixin._read_serial_frame(ser, b"\r\n", 2.0)
    elapsed = time.time() - t0
    assert line == b"ST,GS,+ 1.234kg"
    assert 0.1 < elapsed < 0.6


def test_frame_without_delimiter_ends_on_quiet_gap():
    ser = ChunkSer([(0, b"12.34")])
    t0 = time.time()
    line = ExternalDeviceProtocolsMixin._read_serial_frame(ser, b"\r\n", 2.0)
    elapsed = time.time() - t0
    assert line == b"12.34"
    assert elapsed < 0.5, "无分隔符的帧应靠短静默收尾, 不等满超时"


def test_frame_no_data_returns_none_after_timeout():
    ser = ChunkSer([])
    t0 = time.time()
    line = ExternalDeviceProtocolsMixin._read_serial_frame(ser, b"\r\n", 0.3)
    elapsed = time.time() - t0
    assert line is None
    assert elapsed < 0.6


# ==================== 指令应答主循环: 控制指令即到即发 ====================

class FakeScaleSerial:
    """应答模式假秤: 收 R 回一帧重量, 收 T 记下发出时刻。"""

    def __init__(self):
        self._buf = b""
        self._lock = threading.Lock()
        self.tare_sent_at = None

    @property
    def in_waiting(self):
        with self._lock:
            return len(self._buf)

    def read(self, n):
        time.sleep(0.001)
        with self._lock:
            out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def write(self, data):
        cmd = data.decode("ascii", errors="ignore").strip()
        with self._lock:
            if cmd == "R":
                self._buf += FRAME
            elif cmd == "T":
                self.tare_sent_at = time.time()
                self._buf += b"\r\n"

    def reset_input_buffer(self):
        with self._lock:
            self._buf = b""

    def close(self):
        pass


class _Host(ExternalDeviceProtocolsMixin):
    def __init__(self):
        self.received = []

    def _on_raw_data(self, conn, raw):
        self.received.append((time.time(), raw))


def test_control_command_wakes_poll_wait():
    pytest.importorskip("serial")
    host = _Host()
    fake = FakeScaleSerial()
    host._open_serial_with_retry = lambda **kw: fake   # 不碰真串口
    conn = SimpleNamespace(
        name="秤", serial_port="COM9", serial_baud=9600, device_id=1,
        protocol_config={"query_command": "R", "poll_interval": 5.0,
                         "response_timeout": 1.0},
        _stop_event=threading.Event(), _command_queue=deque(),
        status="", last_error="",
    )
    th = threading.Thread(target=host._serial_command_loop, args=(conn,), daemon=True)
    th.start()
    try:
        # 等首轮查询完成 (证明循环已跑起来、进入 5s 轮询等待)
        t0 = time.time()
        while not host.received and time.time() - t0 < 3:
            time.sleep(0.02)
        assert host.received, "首轮查询应收到重量帧"

        # 长等待中途推入去皮指令 → 必须远早于轮询间隔(5s)发出
        push_at = time.time()
        conn._command_queue.append("T")
        while fake.tare_sent_at is None and time.time() - push_at < 3:
            time.sleep(0.02)
        assert fake.tare_sent_at is not None, "去皮指令 3s 内未发出"
        latency = fake.tare_sent_at - push_at
        assert latency < 1.0, \
            f"去皮指令入队到发出用了 {latency:.2f}s (旧实现要睡满整个轮询间隔)"
    finally:
        conn._stop_event.set()
        th.join(timeout=3)
    assert not th.is_alive(), "停止事件置位后循环应退出"
