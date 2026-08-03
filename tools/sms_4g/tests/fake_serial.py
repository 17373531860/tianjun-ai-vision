"""可脚本化的 pyserial fake，不连接真实硬件。"""

from __future__ import annotations

from collections.abc import Mapping


class FakeSerial:
    """按写入的 AT 指令返回预设响应。"""

    def __init__(
        self,
        responses: Mapping[str, bytes],
        *,
        payload_response: bytes = b"\r\n+CMGS: 42\r\n\r\nOK\r\n",
        **_kwargs: object,
    ) -> None:
        self.responses = dict(responses)
        self.payload_response = payload_response
        self.is_open = True
        self.writes: list[bytes] = []
        self._incoming = bytearray()

    @property
    def in_waiting(self) -> int:
        return len(self._incoming)

    def write(self, payload: bytes) -> int:
        if not self.is_open:
            raise OSError("fake serial is closed")
        self.writes.append(payload)
        if payload.endswith(b"\r"):
            command = payload[:-1].decode("ascii")
            response = self.responses.get(command)
            if response is None and command.startswith("AT+CMGS="):
                response = self.responses.get("AT+CMGS=*")
            if response is None:
                response = b"\r\nERROR\r\n"
            self._incoming.extend(response)
        elif payload.endswith(b"\x1a"):
            self._incoming.extend(self.payload_response)
        return len(payload)

    def read(self, size: int = 1) -> bytes:
        if not self._incoming:
            return b""
        size = max(1, min(size, len(self._incoming)))
        result = bytes(self._incoming[:size])
        del self._incoming[:size]
        return result

    def flush(self) -> None:
        return None

    def reset_input_buffer(self) -> None:
        self._incoming.clear()

    def close(self) -> None:
        self.is_open = False


def registered_responses() -> dict[str, bytes]:
    """返回一个已插卡、已注册网络且支持 Text Mode 的通用脚本。"""

    return {
        "AT": b"\r\nOK\r\n",
        "ATE0": b"\r\nOK\r\n",
        "ATI": b"\r\nSIMCOM_SIM7600\r\nRevision: TEST\r\n\r\nOK\r\n",
        "AT+CPIN?": b"\r\n+CPIN: READY\r\n\r\nOK\r\n",
        "AT+CEREG?": b"\r\n+CEREG: 0,1\r\n\r\nOK\r\n",
        "AT+CSQ": b"\r\n+CSQ: 20,99\r\n\r\nOK\r\n",
        "AT+COPS?": b'\r\n+COPS: 0,0,"CHINA MOBILE",7\r\n\r\nOK\r\n',
        "AT+CMGF=?": b"\r\n+CMGF: (0,1)\r\n\r\nOK\r\n",
        "AT+CMGF=1": b"\r\nOK\r\n",
        'AT+CSCS="UCS2"': b"\r\nOK\r\n",
        'AT+CSCS="GSM"': b"\r\nOK\r\n",
        "AT+CSMP=17,167,0,8": b"\r\nOK\r\n",
        "AT+CSMP=17,167,0,0": b"\r\nOK\r\n",
        "AT+CMGS=*": b"\r\n> ",
    }
