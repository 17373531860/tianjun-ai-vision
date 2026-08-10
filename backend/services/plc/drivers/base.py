"""
PLC 驱动基类 — 所有协议驱动的统一契约。

生命周期与线程约定 (由 point_engine 保证):
- 一条连接一个 driver 实例, 所有 IO 调用在持有连接锁的前提下串行发生,
  driver 内部不需要再做线程同步
- connect/read_all/write 失败一律抛异常, 引擎负责重连退避与错误计数
- __init__ 只做地址解析等纯计算, 不做任何网络 IO (配置错误 → ValueError)
"""
from typing import Any, Dict, List


class BasePLCDriver:
    name = "base"

    def __init__(self, conn_params: dict, points: List[dict]):
        self.conn_params = conn_params or {}
        self.points = {p["key"]: p for p in (points or [])}
        self.read_points = [p for p in (points or [])
                            if (p.get("dir") or "read") in ("read", "read_write")]

    # ---- 子类必须实现 ----

    def connect(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def read_all(self) -> Dict[str, Any]:
        """读全部可读点位, 返回 {key: value}。通讯失败抛异常。"""
        raise NotImplementedError

    def write(self, key: str, value: Any) -> None:
        """写单个点位 (value 已经过 point_codec.coerce_value 归一)。"""
        raise NotImplementedError

    # ---- 可选覆写 ----

    @classmethod
    def test_connection(cls, conn_params: dict, points: List[dict]) -> dict:
        """连通性测试 (API /plc/connections/{id}/test 用)。"""
        try:
            drv = cls(conn_params, points)
            drv.connect()
            try:
                values = drv.read_all()
            finally:
                drv.close()
            return {"success": True,
                    "message": f"连接成功, 读到 {len(values)} 个点位",
                    "values": values}
        except Exception as e:
            return {"success": False, "message": f"{type(e).__name__}: {e}"}
