"""
虚拟 PLC 驱动 — 无硬件联调 / CI 测试 / 展会演示 (对照外设家族 mock_weight 先例)。

值存在进程内存 (类级 dict, 按 store_id 隔离), 两侧都能动:
- 视觉侧: 正常 write() (写回规则/手动写值走这里)
- "PLC 侧": API POST /plc/connections/{id}/mock-set 调 set_external(),
  模拟 PLC 程序改点位 (如置起"读完成"、写产品号), 驱动完整触发规则链路

conn_params: {"store_id": "任意隔离键, 默认 default", "initial": {key: value}}
地址方言: mock 不解析地址, 任意字符串即可 (点位仍要 type, codec 语义一致)。
"""
import threading
from typing import Any, Dict, List

from backend.services.plc.drivers.base import BasePLCDriver

# store_id → {key: value}; 类级共享让"重启连接后值仍在", 贴近真 PLC 断线重连语义
_STORES: Dict[str, Dict[str, Any]] = {}
_STORES_LOCK = threading.Lock()


def _default_value(point: dict) -> Any:
    ptype = point.get("type") or "bool"
    if ptype == "bool":
        return False
    if ptype.startswith("string"):
        return ""
    return 0


class MockPLCDriver(BasePLCDriver):
    name = "mock"

    def __init__(self, conn_params: dict, points: List[dict]):
        super().__init__(conn_params, points)
        self._store_id = str(self.conn_params.get("store_id") or "default")
        self._connected = False
        with _STORES_LOCK:
            store = _STORES.setdefault(self._store_id, {})
            for p in (points or []):
                store.setdefault(p["key"], _default_value(p))
            for k, v in (self.conn_params.get("initial") or {}).items():
                store.setdefault(k, v)

    def connect(self) -> None:
        self._connected = True

    def close(self) -> None:
        self._connected = False

    def read_all(self) -> Dict[str, Any]:
        if not self._connected:
            raise ConnectionError("mock 驱动未连接")
        with _STORES_LOCK:
            store = _STORES.get(self._store_id, {})
            return {p["key"]: store.get(p["key"]) for p in self.read_points}

    def write(self, key: str, value: Any) -> None:
        if not self._connected:
            raise ConnectionError("mock 驱动未连接")
        with _STORES_LOCK:
            _STORES.setdefault(self._store_id, {})[key] = value

    # ---- mock 专属: 模拟 PLC 侧改值 (不区分点位方向, PLC 自己想写啥写啥) ----

    def set_external(self, key: str, value: Any) -> None:
        with _STORES_LOCK:
            _STORES.setdefault(self._store_id, {})[key] = value

    def dump_store(self) -> Dict[str, Any]:
        with _STORES_LOCK:
            return dict(_STORES.get(self._store_id, {}))
