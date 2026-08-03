"""USB 4G 短信模块的串口 AT 指令客户端。"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import serial


class AtClientError(RuntimeError):
    """AT 客户端基础异常。"""


class SerialConnectionError(AtClientError):
    """短信模块串口无法打开或写入。"""


class AtTimeoutError(AtClientError):
    """等待短信模块响应超时。"""


class AtCommandError(AtClientError):
    """短信模块明确返回 ERROR、CME ERROR 或 CMS ERROR。"""

    def __init__(self, message: str, *, response: str = "") -> None:
        super().__init__(message)
        self.response = response


@dataclass(frozen=True)
class SerialConfig:
    """短信专用串口参数；严禁复用灯塔/蜂鸣器的 COM 对象。"""

    port: str
    baudrate: int = 115200
    read_timeout: float = 0.15
    write_timeout: float = 3.0

    def validate(self) -> None:
        """在打开串口前拒绝空端口和非法波特率。"""

        if not self.port.strip():
            raise SerialConnectionError("未选择短信模块 COM 口")
        try:
            baudrate = int(self.baudrate)
        except (TypeError, ValueError) as exc:
            raise SerialConnectionError("短信模块波特率必须是整数") from exc
        if not 300 <= baudrate <= 4_000_000:
            raise SerialConnectionError(f"短信模块波特率不合法：{self.baudrate}")


@dataclass(frozen=True)
class AtResponse:
    """一条 AT 指令的原始响应与清洗后行列表。"""

    command: str
    raw: str
    lines: tuple[str, ...]
    elapsed_seconds: float


class AtClient:
    """以 8N1 打开短信专用 COM，并串行执行 AT 指令。"""

    _ERROR_PATTERN = re.compile(
        r"(?:^|\r?\n)(?:ERROR|\+CM[ES] ERROR:[^\r\n]*)(?:\r?\n|$)"
    )
    _OK_PATTERN = re.compile(r"(?:^|\r?\n)OK(?:\r?\n|$)")

    def __init__(
        self,
        config: SerialConfig,
        *,
        logger: Callable[[str], None] | None = None,
        serial_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self._logger = logger or (lambda _message: None)
        self._serial_factory = serial_factory or serial.Serial
        self._serial: Any | None = None
        self._lock = threading.RLock()
        self._serial_state_lock = threading.Lock()
        self._aborted = False

    @property
    def is_open(self) -> bool:
        """返回串口对象是否处于打开状态。"""

        with self._serial_state_lock:
            serial_obj = self._serial
        return bool(serial_obj is not None and getattr(serial_obj, "is_open", True))

    def open(self) -> None:
        """打开短信专用串口；默认关闭配置不会调用本方法。"""

        with self._lock:
            if self.is_open:
                return
            self.config.validate()
            try:
                serial_obj = self._serial_factory(
                    port=self.config.port.strip(),
                    baudrate=int(self.config.baudrate),
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=float(self.config.read_timeout),
                    write_timeout=float(self.config.write_timeout),
                )
            except (OSError, serial.SerialException, ValueError) as exc:
                raise SerialConnectionError(
                    f"无法打开短信模块串口 {self.config.port}。"
                    "请确认 COM 未被其他程序占用，并且不要选择灯塔串口"
                ) from exc
            with self._serial_state_lock:
                if self._aborted:
                    try:
                        serial_obj.close()
                    finally:
                        raise SerialConnectionError("短信服务正在关闭，串口打开已取消")
                self._serial = serial_obj
            self._logger(
                f"已打开短信串口 {self.config.port}，{self.config.baudrate} baud，8N1"
            )

    def close(self) -> None:
        """关闭串口并隔离驱动关闭异常。"""

        with self._lock:
            with self._serial_state_lock:
                current, self._serial = self._serial, None
            if current is None:
                return
            try:
                current.close()
            except Exception as exc:
                self._logger(f"关闭短信串口时出现可忽略异常：{type(exc).__name__}")
            else:
                self._logger("短信串口已关闭")

    def abort(self) -> None:
        """不等待 AT 命令锁，立即中断正在发送的串口。

        仅供 ``SmsService.shutdown`` 在有限等待到期后调用。正常路径仍使用
        ``close``；这里先摘除串口引用再关闭，使正在读取的 worker 尽快退出。
        """

        with self._serial_state_lock:
            self._aborted = True
            current, self._serial = self._serial, None
        if current is None:
            return
        try:
            current.close()
        except Exception as exc:
            self._logger(f"强制关闭短信串口时出现可忽略异常：{type(exc).__name__}")
        else:
            self._logger("短信串口已强制关闭")

    def command(
        self,
        command: str,
        *,
        timeout: float = 5.0,
        expect_prompt: bool = False,
        display_command: str | None = None,
    ) -> AtResponse:
        """发送 AT 指令并等待 ``OK`` 或短信正文提示符 ``>``。"""

        with self._lock:
            self._require_open()
            shown = display_command or self._sanitize_command(command)
            self._logger(f"> {shown}")
            self._clear_input()
            self._write((command + "\r").encode("ascii"))
            response = self._read_until(
                command=shown,
                timeout=timeout,
                expect_prompt=expect_prompt,
                redact_sms=command.upper().startswith("AT+CMGS="),
            )
            self._log_response(
                response.raw,
                redact_sms=command.upper().startswith("AT+CMGS="),
            )
            return response

    def submit_sms_payload(self, payload: bytes, *, timeout: float = 60.0) -> AtResponse:
        """写入正文并以 Ctrl+Z 提交；日志不记录正文。"""

        with self._lock:
            self._require_open()
            self._logger(f"> [短信正文已隐藏，{len(payload)} bytes] + Ctrl+Z")
            self._write(payload + b"\x1a")
            response = self._read_until(
                command="短信正文提交",
                timeout=timeout,
                expect_prompt=False,
                redact_sms=True,
            )
            self._log_response(response.raw, redact_sms=True)
            return response

    def _read_until(
        self,
        *,
        command: str,
        timeout: float,
        expect_prompt: bool,
        redact_sms: bool,
    ) -> AtResponse:
        started = time.monotonic()
        deadline = started + timeout
        buffer = bytearray()

        while time.monotonic() < deadline:
            with self._serial_state_lock:
                serial_obj = self._serial
            if serial_obj is None:
                raise SerialConnectionError("短信模块串口已被关闭")
            waiting = max(1, int(getattr(serial_obj, "in_waiting", 0) or 0))
            try:
                chunk = serial_obj.read(waiting)
            except (OSError, serial.SerialException) as exc:
                raise SerialConnectionError("读取短信模块串口失败") from exc
            if chunk:
                buffer.extend(chunk)
                raw = buffer.decode("utf-8", errors="replace")
                if self._ERROR_PATTERN.search(raw):
                    self._log_response(raw, redact_sms=redact_sms)
                    raise AtCommandError(
                        f"模块执行 {command} 失败：{self._friendly_error(raw)}",
                        response=raw,
                    )
                if expect_prompt and ">" in raw:
                    return self._make_response(command, raw, started)
                if not expect_prompt and self._OK_PATTERN.search(raw):
                    return self._make_response(command, raw, started)
            else:
                time.sleep(0.01)

        raw = buffer.decode("utf-8", errors="replace")
        if raw.strip():
            self._log_response(raw, redact_sms=redact_sms)
        if redact_sms and raw.strip():
            suffix = "；模块返回内容已隐藏"
        else:
            suffix = f"；已收到：{raw.strip()}" if raw.strip() else "；模块没有返回任何内容"
        raise AtTimeoutError(f"等待 {command} 响应超时（{timeout:.1f}s）{suffix}")

    @staticmethod
    def _make_response(command: str, raw: str, started: float) -> AtResponse:
        lines = tuple(
            line.strip() for line in raw.replace("\r", "").split("\n") if line.strip()
        )
        return AtResponse(command, raw, lines, time.monotonic() - started)

    def _require_open(self) -> None:
        if not self.is_open:
            raise SerialConnectionError("短信模块串口尚未打开")

    def _clear_input(self) -> None:
        with self._serial_state_lock:
            serial_obj = self._serial
        if serial_obj is None:
            raise SerialConnectionError("短信模块串口已被关闭")
        try:
            serial_obj.reset_input_buffer()
        except AttributeError:
            pass

    def _write(self, payload: bytes) -> None:
        with self._serial_state_lock:
            serial_obj = self._serial
        if serial_obj is None:
            raise SerialConnectionError("短信模块串口已被关闭")
        try:
            written = serial_obj.write(payload)
            serial_obj.flush()
        except (OSError, serial.SerialException) as exc:
            raise SerialConnectionError("写入短信模块串口失败") from exc
        if written is not None and written != len(payload):
            raise SerialConnectionError(
                f"短信串口只写入 {written}/{len(payload)} bytes，连接可能已断开"
            )

    def _log_response(self, raw: str, *, redact_sms: bool = False) -> None:
        hidden_echo_logged = False
        for line in (line.strip() for line in raw.replace("\r", "").split("\n")):
            if not line or line == ">":
                continue
            if line.upper().startswith("AT+CMGS="):
                self._logger('< AT+CMGS="<手机号已隐藏>"')
                continue
            if redact_sms and not re.match(
                r"^(?:\+CMGS:|OK$|ERROR$|\+CM[ES] ERROR:)", line, re.IGNORECASE
            ):
                if not hidden_echo_logged:
                    self._logger("< [短信正文回显已隐藏]")
                    hidden_echo_logged = True
                continue
            self._logger(f"< {line}")
        if ">" in raw:
            self._logger("< > 等待短信正文")

    @staticmethod
    def _sanitize_command(command: str) -> str:
        if command.upper().startswith("AT+CMGS="):
            return 'AT+CMGS="<手机号已隐藏>"'
        return command

    @staticmethod
    def _friendly_error(raw: str) -> str:
        match = re.search(r"\+CMS ERROR:\s*(\d+)", raw)
        if match:
            code = int(match.group(1))
            descriptions = {
                302: "操作不允许，请检查 SIM 是否开通短信",
                310: "未检测到 SIM 卡",
                311: "SIM 卡需要 PIN",
                330: "短信中心号码未知",
                500: "模块未知错误",
                515: "模块忙，请稍后重试",
            }
            return f"CMS ERROR {code}（{descriptions.get(code, '请查模块 AT 手册')}）"
        match = re.search(r"\+CME ERROR:\s*([^\r\n]+)", raw)
        if match:
            return f"CME ERROR {match.group(1).strip()}"
        return "模块返回 ERROR，请核对模块型号和 AT 指令集"
