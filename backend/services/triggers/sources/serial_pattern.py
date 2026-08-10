"""serial_pattern — 串口报文正则匹配触发源 (光电开关/继电器板/任意串口小设备)。

线程模型对照 external_device_protocols 串口循环: 独立线程阻塞读行,
断链指数退避自动重连, 拔线不拖垮主程序。

params:
    port         str    串口号 ("COM3" / "/dev/ttyUSB0")
    baudrate     int    默认 9600
    bytesize/parity/stopbits    可选 (默认 8/N/1)
    line_ending  str    行分隔符, 默认 "\\n" (支持 "\\r\\n"; "raw" 表示按块读不分行)
    pattern      str    正则; 命中即发脉冲。命名捕获组 (?P<var>...) 提为变量
    encoding     str    默认 "ascii", errors=ignore

信号语义: 脉冲型。meta = {"fired_by": "serial", "line": ..., **命名捕获组}
"""
import re
import threading
import time
from typing import Optional

from backend.services.triggers.sources.base import BaseTriggerSource

_RECONNECT_BACKOFF = (1, 2, 5, 10, 30)   # 秒, 之后保持 30


class SerialPatternSource(BaseTriggerSource):
    type_name = "serial_pattern"
    kind = "pulse"

    def __init__(self, params, emit_level, emit_pulse):
        super().__init__(params, emit_level, emit_pulse)
        err = self.validate_params(self.params)
        if err:
            raise ValueError(err)
        self.port = str(self.params["port"]).strip()
        self.baudrate = int(self.params.get("baudrate") or 9600)
        self.pattern = re.compile(str(self.params["pattern"]))
        self.line_ending = self.params.get("line_ending") or "\n"
        self.encoding = self.params.get("encoding") or "ascii"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._reconnects = 0
        self._match_count = 0
        self._last_line: Optional[str] = None

    @classmethod
    def validate_params(cls, params: dict) -> Optional[str]:
        p = params or {}
        if not str(p.get("port") or "").strip():
            return "serial_pattern 需要 port (串口号)"
        if not str(p.get("pattern") or "").strip():
            return "serial_pattern 需要 pattern (正则)"
        try:
            re.compile(str(p["pattern"]))
        except re.error as e:
            return f"pattern 正则不合法: {e}"
        return None

    def start(self):
        import serial  # noqa: F401  惰性探测 pyserial (缺库只影响本类型)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            name=f"trigger-serial-{self.port}")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None

    def _loop(self):
        import serial
        backoff_idx = 0
        while not self._stop.is_set():
            conn = None
            try:
                conn = serial.Serial(
                    port=self.port, baudrate=self.baudrate,
                    bytesize=int(self.params.get("bytesize") or 8),
                    parity=str(self.params.get("parity") or "N"),
                    stopbits=float(self.params.get("stopbits") or 1),
                    timeout=0.5)
                self._connected = True
                backoff_idx = 0
                buf = b""
                sep = self.line_ending.encode() if self.line_ending != "raw" else None
                while not self._stop.is_set():
                    chunk = conn.read(256)
                    if not chunk:
                        continue
                    if sep is None:
                        self._handle_line(chunk.decode(self.encoding, errors="ignore"))
                        continue
                    buf += chunk
                    while sep in buf:
                        line, buf = buf.split(sep, 1)
                        self._handle_line(line.decode(self.encoding, errors="ignore"))
                    if len(buf) > 4096:   # 无分隔符的野流量防积压
                        buf = buf[-1024:]
            except Exception:
                self._connected = False
                delay = _RECONNECT_BACKOFF[min(backoff_idx, len(_RECONNECT_BACKOFF) - 1)]
                backoff_idx += 1
                self._reconnects += 1
                self._stop.wait(delay)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
        self._connected = False

    def _handle_line(self, line: str):
        line = line.strip()
        if not line:
            return
        self._last_line = line
        m = self.pattern.search(line)
        if not m:
            return
        self._match_count += 1
        meta = {"fired_by": "serial", "line": line}
        meta.update({k: v for k, v in m.groupdict().items() if v is not None})
        self.emit_pulse(meta)

    def snapshot(self):
        return {"port": self.port, "baudrate": self.baudrate,
                "connected": self._connected, "reconnects": self._reconnects,
                "match_count": self._match_count, "last_line": self._last_line}
