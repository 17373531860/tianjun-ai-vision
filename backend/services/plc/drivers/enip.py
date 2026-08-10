"""
EtherNet/IP 驱动 (pycomm3) — AB/罗克韦尔 CompactLogix/ControlLogix、欧姆龙 NJ/NX。

conn_params:
  {"ip": "192.168.1.40",            # 可带槽位路径 "192.168.1.40/1"
   "poll_interval_ms": 200}

地址方言 = 控制器 tag 名 (原生带类型, 无需字节级解码):
  {"key": "trigger", "addr": "Program:MainProgram.ReadDone", "type": "bool"}
  {"key": "sn", "addr": "ProductSN", "type": "string_fixed", "length": 20}
点位 type 仅做值归一 (coerce) 与前端展示; scale/offset 照常生效。
"""
from typing import Any, Dict, List

from backend.services.plc.drivers.base import BasePLCDriver


class EnipDriver(BasePLCDriver):
    name = "ethernet_ip"

    def __init__(self, conn_params: dict, points: List[dict]):
        super().__init__(conn_params, points)
        self._client = None
        for p in (points or []):
            if not str(p.get("addr") or "").strip():
                raise ValueError(f"点位 {p['key']}: EtherNet/IP 地址(tag 名)不能为空")

    def connect(self) -> None:
        from pycomm3 import LogixDriver
        self.close()
        client = LogixDriver(str(self.conn_params.get("ip") or ""))
        client.open()
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    @staticmethod
    def _post(p: dict, value):
        scale, offset = p.get("scale"), p.get("offset")
        if value is not None and (scale is not None or offset is not None):
            value = float(value) * float(scale if scale is not None else 1.0) \
                + float(offset if offset is not None else 0.0)
        return value

    def read_all(self) -> Dict[str, Any]:
        if self._client is None:
            raise ConnectionError("EtherNet/IP 未连接")
        tags = [p["addr"] for p in self.read_points]
        if not tags:
            return {}
        results = self._client.read(*tags)
        if not isinstance(results, list):
            results = [results]
        values: Dict[str, Any] = {}
        for p, res in zip(self.read_points, results):
            if res is None or res.error:
                raise ConnectionError(
                    f"EtherNet/IP 读 {p['addr']} 失败: {getattr(res, 'error', '?')}")
            values[p["key"]] = self._post(p, res.value)
        return values

    def write(self, key: str, value: Any) -> None:
        if self._client is None:
            raise ConnectionError("EtherNet/IP 未连接")
        p = self.points[key]
        scale, offset = p.get("scale"), p.get("offset")
        if scale is not None or offset is not None:
            value = (float(value) - float(offset if offset is not None else 0.0)) \
                / float(scale if scale is not None else 1.0)
        res = self._client.write((p["addr"], value))
        if res is None or (hasattr(res, "error") and res.error):
            raise ConnectionError(
                f"EtherNet/IP 写 {p['addr']} 失败: {getattr(res, 'error', '?')}")
