"""
OPC UA 驱动 (asyncua 同步封装) — 协议兜底: 一切带 UA Server 的 PLC/网关。

客户 PLC 品牌太杂/太老时, 行业标准做法是加一台 OPC UA 网关 (KEPServer 等),
视觉侧统一走 UA —— 这条驱动保证"再冷门的 PLC 也有配置化出路"。

conn_params:
  {"url": "opc.tcp://192.168.1.50:4840",
   "username": null, "password": null,
   "poll_interval_ms": 200}

地址方言 = NodeId 字符串: "ns=2;s=Channel1.Device1.ReadDone" / "ns=3;i=1005"
点位 type 用于写值时的 UA Variant 类型映射 (Int16/Float/String/Boolean),
读值原生带类型, scale/offset 照常生效。
"""
from typing import Any, Dict, List

from backend.services.plc.drivers.base import BasePLCDriver

_VARIANT_BY_TYPE = {
    "bool": "Boolean",
    "byte": "Byte",
    "int16": "Int16", "uint16": "UInt16", "word": "UInt16",
    "int32": "Int32", "uint32": "UInt32", "dword": "UInt32",
    "float32": "Float", "float64": "Double",
    "string_s7": "String", "string_fixed": "String", "string_cstr": "String",
}


class OpcUaDriver(BasePLCDriver):
    name = "opcua"

    def __init__(self, conn_params: dict, points: List[dict]):
        super().__init__(conn_params, points)
        self._client = None
        self._nodes: Dict[str, Any] = {}
        if not str(self.conn_params.get("url") or "").strip():
            raise ValueError("OPC UA 缺少 url (形如 opc.tcp://IP:4840)")
        for p in (points or []):
            if not str(p.get("addr") or "").strip():
                raise ValueError(f"点位 {p['key']}: OPC UA NodeId 不能为空")

    def connect(self) -> None:
        from asyncua.sync import Client
        self.close()
        client = Client(str(self.conn_params.get("url")))
        if self.conn_params.get("username"):
            client.set_user(str(self.conn_params["username"]))
            client.set_password(str(self.conn_params.get("password") or ""))
        client.connect()
        self._client = client
        self._nodes = {p["key"]: client.get_node(p["addr"])
                       for p in self.point_list}

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
            self._nodes = {}

    @staticmethod
    def _post(p: dict, value):
        scale, offset = p.get("scale"), p.get("offset")
        if value is not None and (scale is not None or offset is not None):
            value = float(value) * float(scale if scale is not None else 1.0) \
                + float(offset if offset is not None else 0.0)
        return value

    def read_all(self) -> Dict[str, Any]:
        if self._client is None:
            raise ConnectionError("OPC UA 未连接")
        return {p["key"]: self._post(p, self._nodes[p["key"]].read_value())
                for p in self.read_points}

    def write(self, key: str, value: Any) -> None:
        if self._client is None:
            raise ConnectionError("OPC UA 未连接")
        from asyncua import ua
        p = self.points[key]
        scale, offset = p.get("scale"), p.get("offset")
        if scale is not None or offset is not None:
            value = (float(value) - float(offset if offset is not None else 0.0)) \
                / float(scale if scale is not None else 1.0)
        vt_name = _VARIANT_BY_TYPE.get(p.get("type") or "bool")
        variant_type = getattr(ua.VariantType, vt_name, None) if vt_name else None
        if variant_type is not None:
            if variant_type in (ua.VariantType.Int16, ua.VariantType.UInt16,
                                ua.VariantType.Int32, ua.VariantType.UInt32,
                                ua.VariantType.Byte):
                value = int(round(float(value)))
            self._nodes[key].write_value(ua.DataValue(ua.Variant(value, variant_type)))
        else:
            self._nodes[key].write_value(value)
