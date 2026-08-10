"""
西门子 S7 驱动 (ISO-on-TCP, python-snap7) — S7-200Smart/300/400/1200/1500。

conn_params:
  {"ip": "172.20.11.111", "rack": 0, "slot": 2, "port": 102,
   "poll_interval_ms": 100}
  - S7-300/400: rack 0 slot 2; S7-1200/1500: rack 0 slot 0/1
    (1200/1500 还需在博途开"允许 PUT/GET" + DB 关"优化块访问", 已写进协议文档)

地址方言 (bit/长度语义由点位 type/length 决定, 地址只定位偏移):
  DB1200.DBX0.2   bool   (字节 0 第 2 位)
  DB1200.DBB5     字节偏移 5   (byte/string_fixed/string_cstr/bcd 等)
  DB1200.DBW2     字节偏移 2   (int16/uint16/word)
  DB1200.DBD4     字节偏移 4   (int32/uint32/dword/float32/float64)
  DB1200.STRING@6 S7 STRING (2 字节头) 偏移 6, 配 type=string_s7 + length=max
  兼容简写: DB1200.6 / DB1200.0.2
"""
import re
from typing import Any, Dict, List, Tuple

from backend.services.plc.drivers.base import BasePLCDriver
from backend.services.plc import point_codec

_ADDR_RE = re.compile(
    r"^DB(\d+)\.(?:"
    r"DBX(\d+)\.(\d+)|"      # bool
    r"DB[BWD](\d+)|"         # byte/word/dword 偏移
    r"STRING@(\d+)|"         # S7 STRING
    r"(\d+)(?:\.(\d+))?"     # 简写: 偏移[.位]
    r")$",
    re.IGNORECASE,
)

# 单次 db_read 区间合并参数: 间隙小于 GAP 的点位并成一段, 段长上限 CAP
_MERGE_GAP = 16
_CHUNK_CAP = 960


def parse_s7_address(addr: str) -> Tuple[int, int, int]:
    """'DB1200.DBX0.2' → (db_number, byte_offset, bit)。非法地址抛 ValueError。"""
    m = _ADDR_RE.match(str(addr or "").strip())
    if not m:
        raise ValueError(
            f"非法 S7 地址: {addr!r} (期望 DBn.DBXo.b / DBn.DBBo / DBn.DBWo / "
            f"DBn.DBDo / DBn.STRING@o)")
    db = int(m.group(1))
    if m.group(2) is not None:      # DBX o.b
        return db, int(m.group(2)), int(m.group(3))
    if m.group(4) is not None:      # DBB/DBW/DBD o
        return db, int(m.group(4)), 0
    if m.group(5) is not None:      # STRING@o
        return db, int(m.group(5)), 0
    return db, int(m.group(6)), int(m.group(7) or 0)   # 简写


class S7Driver(BasePLCDriver):
    name = "s7"

    def __init__(self, conn_params: dict, points: List[dict]):
        super().__init__(conn_params, points)
        self._client = None
        # key → (db, offset, bit, size); __init__ 解析全部地址, 配置错误当场暴露
        self._addr: Dict[str, Tuple[int, int, int, int]] = {}
        for p in (points or []):
            db, off, bit = parse_s7_address(p.get("addr"))
            self._addr[p["key"]] = (db, off, bit, point_codec.point_byte_size(p))
            # DBX 地址里的位号下沉到点位配置, codec 解码 bool 时取用
            p.setdefault("bit", bit)
        self._read_plan = self._build_read_plan()

    def _build_read_plan(self) -> List[Tuple[int, int, int, List[dict]]]:
        """把可读点位并成尽量少的 db_read 区间: [(db, start, size, points)]。"""
        by_db: Dict[int, List[dict]] = {}
        for p in self.read_points:
            db = self._addr[p["key"]][0]
            by_db.setdefault(db, []).append(p)
        plan = []
        for db, plist in by_db.items():
            plist.sort(key=lambda p: self._addr[p["key"]][1])
            seg_pts, seg_start, seg_end = [], None, None
            for p in plist:
                _, off, _, size = self._addr[p["key"]]
                end = off + size
                if seg_start is None:
                    seg_pts, seg_start, seg_end = [p], off, end
                elif off - seg_end <= _MERGE_GAP and end - seg_start <= _CHUNK_CAP:
                    seg_pts.append(p)
                    seg_end = max(seg_end, end)
                else:
                    plan.append((db, seg_start, seg_end - seg_start, seg_pts))
                    seg_pts, seg_start, seg_end = [p], off, end
            if seg_pts:
                plan.append((db, seg_start, seg_end - seg_start, seg_pts))
        return plan

    def connect(self) -> None:
        import snap7
        self.close()
        client = snap7.client.Client()
        client.connect(
            str(self.conn_params.get("ip") or ""),
            int(self.conn_params.get("rack") or 0),
            int(self.conn_params.get("slot") or 2),
            int(self.conn_params.get("port") or 102),
        )
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
                self._client.destroy()
            except Exception:
                pass
            self._client = None

    def read_all(self) -> Dict[str, Any]:
        if self._client is None:
            raise ConnectionError("S7 未连接")
        values: Dict[str, Any] = {}
        for db, start, size, plist in self._read_plan:
            chunk = bytes(self._client.db_read(db, start, size))
            for p in plist:
                _, off, _, psize = self._addr[p["key"]]
                raw = chunk[off - start: off - start + psize]
                values[p["key"]] = point_codec.decode(raw, p)
        return values

    def write(self, key: str, value: Any) -> None:
        if self._client is None:
            raise ConnectionError("S7 未连接")
        p = self.points[key]
        db, off, bit, size = self._addr[key]
        if (p.get("type") or "bool") == "bool":
            # 读改写单字节 (engine 层持锁串行, 无并发窗口)
            cur = bytearray(self._client.db_read(db, off, 1))
            if value:
                cur[0] |= (1 << bit)
            else:
                cur[0] &= ~(1 << bit)
            self._client.db_write(db, off, cur)
            return
        self._client.db_write(db, off, bytearray(point_codec.encode(value, p)))
