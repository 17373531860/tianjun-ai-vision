"""USB 4G 模块诊断与短信发送引擎。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .at_client import AtClient, AtClientError, AtCommandError, SerialConfig
from .utils import (
    choose_encoding,
    encode_ucs2,
    mask_phone,
    normalize_phone,
    validate_message,
)


class SmsModemError(RuntimeError):
    """模块状态或短信发送不满足要求。"""


@dataclass(frozen=True)
class DiagnosticItem:
    """一项模块诊断结果。"""

    name: str
    level: str
    detail: str


@dataclass(frozen=True)
class DiagnosticReport:
    """模块诊断报告；存在 error 项时 ``success`` 为 False。"""

    items: tuple[DiagnosticItem, ...]

    @property
    def success(self) -> bool:
        return not any(item.level == "error" for item in self.items)


@dataclass(frozen=True)
class SmsSendResult:
    """单个手机号的一次成功发送结果。"""

    phone: str
    encoding: str
    message_reference: str
    raw_response: str


class SmsModem:
    """对标准 AT Text Mode 模块执行诊断和单条短信发送。"""

    REGISTERED_STATUSES = {1: "已注册本地网络", 5: "已注册漫游网络"}

    def __init__(
        self, client: AtClient, *, logger: Callable[[str], None] | None = None
    ) -> None:
        self.client = client
        self._logger = logger or (lambda _message: None)

    @classmethod
    def from_config(
        cls,
        config: SerialConfig,
        *,
        logger: Callable[[str], None] | None = None,
        serial_factory: Callable[..., object] | None = None,
    ) -> "SmsModem":
        """从串口配置创建模块对象，便于二期服务层注入 fake serial。"""

        client = AtClient(config, logger=logger, serial_factory=serial_factory)
        return cls(client, logger=logger)

    def diagnose(self) -> DiagnosticReport:
        """检查 AT、模块信息、SIM、网络、信号和 Text Mode 能力。"""

        items: list[DiagnosticItem] = []
        self.client.open()
        items.append(DiagnosticItem("串口连接", "ok", "短信专用 COM 已打开（8N1）"))

        try:
            self.client.command("AT", timeout=3)
            items.append(DiagnosticItem("AT 基础通信", "ok", "模块返回 OK"))
        except AtClientError as exc:
            items.append(DiagnosticItem("AT 基础通信", "error", str(exc)))
            return DiagnosticReport(tuple(items))

        try:
            self.client.command("ATE0", timeout=3)
        except AtClientError as exc:
            items.append(DiagnosticItem("关闭指令回显", "warning", str(exc)))
        else:
            items.append(DiagnosticItem("关闭指令回显", "ok", "ATE0 已生效"))

        self._diagnose_module_info(items)
        self._diagnose_sim(items)
        self._diagnose_network(items)
        self._diagnose_signal(items)
        self._diagnose_operator(items)
        self._diagnose_sms_mode(items)
        return DiagnosticReport(tuple(items))

    def ensure_ready(self) -> None:
        """发送前确认模块响应、SIM READY 且已注册移动网络。"""

        self.client.command("AT", timeout=3)
        try:
            self.client.command("ATE0", timeout=3)
        except AtClientError:
            self._logger("模块不接受 ATE0，将继续发送")

        sim = self.client.command("AT+CPIN?", timeout=5)
        if "+CPIN: READY" not in sim.raw.upper():
            raise SmsModemError(
                "SIM 卡未就绪；请确认已插卡、未要求 PIN，且不是损坏或不兼容的卡"
            )

        statuses = self._query_registration_statuses()
        registered = next(
            (
                status
                for _name, status in statuses
                if status in self.REGISTERED_STATUSES
            ),
            None,
        )
        if registered is None:
            detail = (
                ", ".join(f"{name}={status}" for name, status in statuses)
                or "模块不支持注册查询"
            )
            raise SmsModemError(
                f"模块尚未注册移动网络（{detail}）。请检查天线、SIM 状态、资费和现场信号"
            )

    def send_sms(
        self, phone: str, message: str, *, encoding: str = "auto"
    ) -> SmsSendResult:
        """以 GSM 或 UCS2 Text Mode 向一个号码发送一条短信。"""

        normalized = normalize_phone(phone)
        selected = choose_encoding(message, encoding)
        validate_message(message, selected)
        self._logger(f"准备向 {mask_phone(normalized)} 发送 {selected.upper()} 短信")

        self.client.command("AT+CMGF=1", timeout=5)
        if selected == "ucs2":
            self.client.command('AT+CSCS="UCS2"', timeout=5)
            self.client.command("AT+CSMP=17,167,0,8", timeout=5)
            phone_payload = encode_ucs2(normalized)
            message_payload = encode_ucs2(message).encode("ascii")
        else:
            self.client.command('AT+CSCS="GSM"', timeout=5)
            self.client.command("AT+CSMP=17,167,0,0", timeout=5)
            phone_payload = normalized
            message_payload = message.encode("ascii")

        self.client.command(
            f'AT+CMGS="{phone_payload}"',
            timeout=10,
            expect_prompt=True,
            display_command='AT+CMGS="<手机号已隐藏>"',
        )
        response = self.client.submit_sms_payload(message_payload, timeout=60)
        match = re.search(r"\+CMGS:\s*([^\r\n]+)", response.raw, re.IGNORECASE)
        if not match:
            raise SmsModemError(
                "模块返回 OK，但没有 +CMGS 消息引用；为避免误报，本次不判定为发送成功"
            )
        reference = match.group(1).strip()
        self._logger(f"{mask_phone(normalized)} 已提交运营商，消息引用 {reference}")
        return SmsSendResult(
            phone=normalized,
            encoding=selected,
            message_reference=reference,
            raw_response=response.raw,
        )

    def _diagnose_module_info(self, items: list[DiagnosticItem]) -> None:
        try:
            response = self.client.command("ATI", timeout=5)
        except AtClientError as exc:
            items.append(DiagnosticItem("模块信息", "warning", str(exc)))
            return
        useful = [line for line in response.lines if line.upper() not in {"ATI", "OK"}]
        items.append(
            DiagnosticItem("模块信息", "ok", " / ".join(useful) or "模块未返回型号文本")
        )

    def _diagnose_sim(self, items: list[DiagnosticItem]) -> None:
        try:
            response = self.client.command("AT+CPIN?", timeout=5)
        except AtClientError as exc:
            items.append(DiagnosticItem("SIM 卡", "error", str(exc)))
            return
        if "+CPIN: READY" in response.raw.upper():
            items.append(DiagnosticItem("SIM 卡", "ok", "SIM READY"))
        else:
            items.append(
                DiagnosticItem(
                    "SIM 卡", "error", "SIM 未就绪；可能未插卡、要求 PIN 或卡不兼容"
                )
            )

    def _diagnose_network(self, items: list[DiagnosticItem]) -> None:
        statuses = self._query_registration_statuses()
        for name, status in statuses:
            if status in self.REGISTERED_STATUSES:
                items.append(
                    DiagnosticItem(
                        "网络注册", "ok", f"{name}：{self.REGISTERED_STATUSES[status]}"
                    )
                )
                return
        detail = ", ".join(f"{name}={status}" for name, status in statuses)
        items.append(
            DiagnosticItem(
                "网络注册",
                "error",
                f"未注册（{detail or '无可用查询结果'}）；状态 2 常表示正在搜网，3 表示被拒绝",
            )
        )

    def _query_registration_statuses(self) -> list[tuple[str, int]]:
        statuses: list[tuple[str, int]] = []
        for command in ("AT+CEREG?", "AT+CREG?", "AT+CGREG?"):
            try:
                response = self.client.command(command, timeout=4)
            except AtCommandError:
                continue
            except AtClientError:
                continue
            match = re.search(r"\+(?:CE|C|CG)REG:\s*(?:\d+\s*,\s*)?(\d+)", response.raw)
            if match:
                statuses.append((command[3:-1], int(match.group(1))))
                if int(match.group(1)) in self.REGISTERED_STATUSES:
                    break
        return statuses

    def _diagnose_signal(self, items: list[DiagnosticItem]) -> None:
        try:
            response = self.client.command("AT+CSQ", timeout=5)
        except AtClientError as exc:
            items.append(DiagnosticItem("信号强度", "warning", str(exc)))
            return
        match = re.search(r"\+CSQ:\s*(\d+)\s*,", response.raw)
        if not match:
            items.append(
                DiagnosticItem("信号强度", "warning", "模块未返回标准 +CSQ 格式")
            )
            return
        rssi = int(match.group(1))
        if rssi == 99:
            items.append(DiagnosticItem("信号强度", "warning", "RSSI 未知（CSQ=99）"))
            return
        dbm = -113 + 2 * rssi
        level = "ok" if rssi >= 10 else "warning"
        hint = "可发送测试" if level == "ok" else "信号偏弱，短信可能延迟或失败"
        items.append(
            DiagnosticItem("信号强度", level, f"CSQ={rssi}，约 {dbm} dBm；{hint}")
        )

    def _diagnose_operator(self, items: list[DiagnosticItem]) -> None:
        try:
            response = self.client.command("AT+COPS?", timeout=8)
        except AtClientError as exc:
            items.append(DiagnosticItem("运营商", "warning", str(exc)))
            return
        useful = [line for line in response.lines if line.startswith("+COPS:")]
        items.append(
            DiagnosticItem(
                "运营商",
                "ok" if useful else "warning",
                useful[0] if useful else "未返回运营商名称",
            )
        )

    def _diagnose_sms_mode(self, items: list[DiagnosticItem]) -> None:
        try:
            response = self.client.command("AT+CMGF=?", timeout=5)
        except AtClientError as exc:
            items.append(DiagnosticItem("短信 Text Mode", "error", str(exc)))
            return
        supported = bool(re.search(r"\+CMGF:\s*\([^)]*1[^)]*\)", response.raw))
        if supported:
            items.append(DiagnosticItem("短信 Text Mode", "ok", "模块声明支持 CMGF=1"))
        else:
            items.append(
                DiagnosticItem(
                    "短信 Text Mode",
                    "error",
                    "模块未声明支持 CMGF=1；可能需要厂商专用指令或 PDU 适配",
                )
            )
