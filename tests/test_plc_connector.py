"""
RFC 13 通用 PLC 连接器 — 回归测试。

三层:
  1. point_codec 纯函数 (类型/字节序/编码/缩放 往返)
  2. 地址方言解析 (S7 / Modbus)
  3. mock 驱动全链路: API CRUD → 引擎轮询 → 上升沿触发规则 → 动作/握手/写回
     (bind_sn 走真实扫码链路建工件; cycle_end 写回经 dispatch_plc_event)

隔离纪律 (2026-08-07 CI 顺序污染事故教训):
  - 本文件建的连接名前缀 PLCT-, 工件序列号前缀 PLCT-, janitor 收尾全删
  - 引擎线程在 janitor 里逐一停掉, 不留后台轮询线程串场
"""
import time

import pytest

from backend.services.plc import point_codec
from backend.services.plc.drivers.s7 import parse_s7_address
from backend.services.plc.drivers.modbus import parse_modbus_address


# ============================================================
# 1. point_codec
# ============================================================

@pytest.mark.parametrize("point,value", [
    ({"type": "int16"}, -1234),
    ({"type": "uint16"}, 65535),
    ({"type": "int32"}, -70000),
    ({"type": "uint32"}, 4000000000),
    ({"type": "float32"}, 3.14),
    ({"type": "float64"}, 2.718281828),
    ({"type": "byte"}, 200),
    ({"type": "word"}, 40000),
    ({"type": "dword"}, 3000000000),
])
def test_codec_numeric_roundtrip(point, value):
    raw = point_codec.encode(value, point)
    out = point_codec.decode(raw, point)
    assert out == pytest.approx(value, rel=1e-6)


@pytest.mark.parametrize("order", ["big", "little", "word_swap", "byte_swap"])
def test_codec_byte_orders(order):
    point = {"type": "int32", "byte_order": order}
    raw = point_codec.encode(-123456, point)
    assert len(raw) == 4
    assert point_codec.decode(raw, point) == -123456


def test_codec_scale_offset():
    point = {"type": "int16", "scale": 0.1, "offset": -5}
    # 原始 123 → 123*0.1-5 = 7.3
    raw = point_codec.encode(7.3, point)
    assert point_codec.decode(raw, point) == pytest.approx(7.3, abs=0.05)


def test_codec_bcd():
    point = {"type": "bcd", "length": 2}
    raw = point_codec.encode(1234, point)
    assert raw == b"\x12\x34"
    assert point_codec.decode(raw, point) == 1234
    with pytest.raises(ValueError):
        point_codec.decode(b"\xAB\xCD", point)


def test_codec_string_s7():
    point = {"type": "string_s7", "length": 10, "encoding": "ascii"}
    raw = point_codec.encode("AB-12", point)
    assert raw[0] == 10 and raw[1] == 5      # max/cur 头
    assert point_codec.decode(raw, point) == "AB-12"


def test_codec_string_fixed_gbk():
    point = {"type": "string_fixed", "length": 12, "encoding": "gbk"}
    raw = point_codec.encode("缸体A1", point)
    assert len(raw) == 12
    assert point_codec.decode(raw, point) == "缸体A1"


def test_codec_string_cstr():
    point = {"type": "string_cstr", "length": 8}
    raw = point_codec.encode("SN1", point)
    assert point_codec.decode(raw, point) == "SN1"


def test_codec_bool_bit():
    point = {"type": "bool", "bit": 3}
    assert point_codec.decode(b"\x08", point) is True
    assert point_codec.decode(b"\xF7", point) is False


def test_validate_point():
    assert point_codec.validate_point({"key": "a", "type": "int16"}) is None
    assert point_codec.validate_point({"key": "", "type": "int16"})
    assert point_codec.validate_point({"key": "a", "type": "nope"})
    assert point_codec.validate_point({"key": "a", "type": "string_fixed"})  # 缺 length
    assert point_codec.validate_point({"key": "a", "type": "bool", "dir": "sideways"})


# ============================================================
# 2. 地址方言
# ============================================================

@pytest.mark.parametrize("addr,expected", [
    ("DB1200.DBX0.2", (1200, 0, 2)),
    ("DB1200.DBB5", (1200, 5, 0)),
    ("DB1200.DBW24", (1200, 24, 0)),
    ("DB1200.DBD4", (1200, 4, 0)),
    ("DB1200.STRING@6", (1200, 6, 0)),
    ("DB7.3.1", (7, 3, 1)),
])
def test_s7_address(addr, expected):
    assert parse_s7_address(addr) == expected


def test_s7_address_invalid():
    with pytest.raises(ValueError):
        parse_s7_address("MW100")


@pytest.mark.parametrize("addr,expected", [
    ("hr:100", ("hr", 100)),
    ("ir:0", ("ir", 0)),
    ("co:5", ("co", 5)),
    ("di:2", ("di", 2)),
    ("100", ("hr", 100)),
])
def test_modbus_address(addr, expected):
    assert parse_modbus_address(addr) == expected


# ============================================================
# 3. mock 驱动全链路
# ============================================================

def _wait_until(fn, timeout=3.0, interval=0.03):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


# 空剧本: 全程无检出。bind_sn 要走通"扫码→建工件"必须让工位挂上项目
# (project_id empty 时 ScannerService 直接丢码), 与 usb_scan_gun_parity 同法。
_EMPTY_SCENARIO = {"name": "plc_bind_idle", "fps": 30,
                   "timeline": [{"from": 0, "to": 10_000, "detections": []}]}


def _mount_minimal_project(client):
    pr = client.get("/api/v1/projects")
    assert pr.status_code == 200, pr.text[:200]
    body = pr.json()
    items = body if isinstance(body, list) else \
        (body.get("items") or body.get("data") or [])
    assert items, "测试库应有种子项目"
    r = client.post("/api/v1/test/synthetic/start", json={
        "channel": 0, "with_project": True, "logic_mode": "sequential",
        "scenario_json": _EMPTY_SCENARIO,
        "project_steps": ["step_a"],
        "project_id": items[0]["id"],
    })
    assert r.status_code == 200, r.text[:300]
    assert r.json().get("project_applied") is True, r.text[:300]


@pytest.fixture()
def plc_janitor(client):
    """收尾: 删本文件建的 PLCT- 连接 (引擎随删除停止) + PLCT- 工件 + 撤剧本。"""
    yield
    client.post("/api/v1/test/synthetic/stop?channel=0")
    r = client.get("/api/v1/plc/connections")
    if r.status_code == 200:
        for c in r.json().get("connections", []):
            if str(c.get("name", "")).startswith("PLCT-"):
                client.delete(f"/api/v1/plc/connections/{c['id']}")
    from backend.db.database import SessionLocal
    from backend.models.mes_models import Workpiece
    db = SessionLocal()
    try:
        db.query(Workpiece).filter(Workpiece.serial_no.like("PLCT-%")).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _mk_connection(client, name, *, read_rules=None, write_rules=None,
                   options=None, store_id=None):
    body = {
        "name": name,
        "driver": "mock",
        "enabled": True,
        "conn_params": {"store_id": store_id or name, "poll_interval_ms": 30},
        "points": [
            {"key": "read_done", "addr": "m0", "type": "bool", "dir": "read"},
            {"key": "product_no", "addr": "m1", "type": "string_fixed",
             "length": 20, "dir": "read"},
            {"key": "cyl_type", "addr": "m2", "type": "int16", "dir": "read"},
            {"key": "data_received", "addr": "m3", "type": "bool", "dir": "read_write"},
            {"key": "result_code", "addr": "m4", "type": "int16", "dir": "write"},
        ],
        "read_rules": read_rules or [],
        "write_rules": write_rules or [],
        "options": options or {"default_channel": 0},
    }
    r = client.post("/api/v1/plc/connections", json=body)
    assert r.status_code == 200, r.text[:400]
    return r.json()["id"]


def test_plc_crud_and_validation(client, plc_janitor):
    # 非法点位被 400 拦下
    bad = {"name": "PLCT-bad", "driver": "mock", "enabled": False,
           "points": [{"key": "x", "type": "string_fixed"}]}   # 缺 length
    assert client.post("/api/v1/plc/connections", json=bad).status_code == 400
    # 重复 key 拦下
    bad2 = {"name": "PLCT-bad2", "driver": "mock", "enabled": False,
            "points": [{"key": "x", "type": "bool"}, {"key": "x", "type": "bool"}]}
    assert client.post("/api/v1/plc/connections", json=bad2).status_code == 400

    cid = _mk_connection(client, "PLCT-crud")
    r = client.get("/api/v1/plc/connections")
    names = [c["name"] for c in r.json()["connections"]]
    assert "PLCT-crud" in names

    # 引擎已随 enabled=True 拉起并连上 mock
    live = _wait_until(lambda: (
        lambda rr: rr.status_code == 200 and rr.json().get("status") == "connected"
        and rr.json())(client.get(f"/api/v1/plc/connections/{cid}/live")))
    assert live, "引擎未进入 connected 状态"

    # 禁用 → 引擎撤下, live 404
    client.post(f"/api/v1/plc/connections/{cid}/enable", json={"enabled": False})
    assert _wait_until(
        lambda: client.get(f"/api/v1/plc/connections/{cid}/live").status_code == 404)

    assert client.delete(f"/api/v1/plc/connections/{cid}").json()["success"] is True


def test_plc_drivers_and_templates(client):
    drivers = {d["name"]: d for d in client.get("/api/v1/plc/drivers").json()["drivers"]}
    assert drivers["mock"]["available"] is True
    assert drivers["modbus_tcp"]["available"] is True
    assert "s7" in drivers      # 可用性取决于 snap7 是否安装, 不断言
    tpls = client.get("/api/v1/plc/templates").json()["templates"]
    assert any(t["template_id"] == "s7_db_handshake" for t in tpls)


def test_plc_rising_edge_bind_sn_and_ack(client, plc_janitor):
    """天永范式核心链路: 读完成上升沿 → 绑定产品号(真实扫码链路) + 回执写。"""
    _mount_minimal_project(client)
    cid = _mk_connection(
        client, "PLCT-edge",
        read_rules=[{
            "name": "读完成",
            "when": [{"point": "read_done", "trigger": "rising"}],
            "debounce_ms": 0,
            "min_interval_ms": 100,
            "ack_mode": "none",
            "actions": [
                {"do": "bind_sn", "template": "{{product_no}}", "channel": 0},
                {"do": "write_points", "writes": [
                    {"point": "data_received", "value": 1},
                ]},
                {"do": "set_var", "name": "last_sn", "value": "{{product_no}}"},
            ],
        }])

    _wait_until(lambda: client.get(
        f"/api/v1/plc/connections/{cid}/live").json().get("status") == "connected")

    # PLC 侧: 先写产品号, 再置起读完成 (真实时序)
    client.post(f"/api/v1/plc/connections/{cid}/mock-set",
                json={"point": "product_no", "value": "PLCT-SN001"})
    client.post(f"/api/v1/plc/connections/{cid}/mock-set",
                json={"point": "read_done", "value": True})

    # 规则触发: data_received 被写 1 (可读点位, 轮询能看到)
    live = _wait_until(lambda: (
        lambda d: d if d.get("values", {}).get("data_received") else None
    )(client.get(f"/api/v1/plc/connections/{cid}/live").json()))
    assert live, "上升沿规则未触发 (data_received 未写)"
    assert live["counters"]["rule_fires"] >= 1

    # bind_sn 走真实扫码链路 → 工件被创建
    def _wp_created():
        from backend.db.database import SessionLocal
        from backend.models.mes_models import Workpiece
        db = SessionLocal()
        try:
            return db.query(Workpiece).filter(
                Workpiece.serial_no == "PLCT-SN001").first() is not None
        finally:
            db.close()
    assert _wait_until(_wp_created), "bind_sn 未经扫码链路建出工件"

    # set_var 生效
    assert _wait_until(lambda: client.get(
        f"/api/v1/plc/connections/{cid}/live").json()
        .get("vars", {}).get("last_sn") == "PLCT-SN001")

    # 复位再触发一次: min_interval 已过, 应再次触发
    client.post(f"/api/v1/plc/connections/{cid}/mock-set",
                json={"point": "read_done", "value": False})
    time.sleep(0.15)
    fires0 = client.get(f"/api/v1/plc/connections/{cid}/live").json()[
        "counters"]["rule_fires"]
    client.post(f"/api/v1/plc/connections/{cid}/mock-set",
                json={"point": "read_done", "value": True})
    assert _wait_until(lambda: client.get(
        f"/api/v1/plc/connections/{cid}/live").json()[
        "counters"]["rule_fires"] > fires0), "复位后第二次上升沿未触发"


def test_plc_self_clear_ack(client, plc_janitor):
    """self_clear 握手: 触发后视觉把触发位写回 0。"""
    cid = _mk_connection(
        client, "PLCT-selfclear",
        read_rules=[{
            "name": "自清",
            "when": [{"point": "read_done", "trigger": "rising"}],
            "debounce_ms": 0,
            "ack_mode": "self_clear",
            "actions": [{"do": "set_var", "name": "hit", "value": "1"}],
        }])
    _wait_until(lambda: client.get(
        f"/api/v1/plc/connections/{cid}/live").json().get("status") == "connected")
    client.post(f"/api/v1/plc/connections/{cid}/mock-set",
                json={"point": "read_done", "value": True})
    # 触发后 read_done 被引擎写回 False
    assert _wait_until(lambda: (
        lambda d: d.get("vars", {}).get("hit") == "1"
        and d.get("values", {}).get("read_done") is False
    )(client.get(f"/api/v1/plc/connections/{cid}/live").json())), \
        "self_clear 未把触发位写回 0"


def test_plc_cycle_end_writeback_value_map(client, plc_janitor):
    """维度五: cycle_end 事件 → value_map 编码结果码写回 (客户自定义 OK/NG 编码)。"""
    cid = _mk_connection(
        client, "PLCT-writeback",
        write_rules=[{
            "on": "cycle_end",
            "channels": [0],          # 只服务工位 0
            "writes": [
                {"point": "result_code", "source": "result",
                 "value_map": {"OK": 7, "NG": 9}, "default": 0},
                {"point": "data_received", "value": 1, "reset_after_ms": 200},
            ],
        }])
    _wait_until(lambda: client.get(
        f"/api/v1/plc/connections/{cid}/live").json().get("status") == "connected")

    from backend.services.plc.write_dispatcher import dispatch_plc_event
    dispatch_plc_event("cycle_end", {"cycle": {"id": 1, "result": "NG"}}, 0)

    def _mock_store():
        from backend.services.plc.manager import get_plc_manager
        eng = get_plc_manager().get_engine(cid)
        return eng.driver.dump_store() if eng else {}

    assert _wait_until(lambda: _mock_store().get("result_code") == 9), \
        f"NG 未按 value_map 写 9: {_mock_store()}"
    # 脉冲复位: data_received 先 1 后 0
    assert _wait_until(lambda: _mock_store().get("data_received") in (False, 0),
                       timeout=2.0), "reset_after_ms 未把脉冲位归零"

    # OK 编码
    dispatch_plc_event("cycle_end", {"cycle": {"id": 2, "result": "OK"}}, 0)
    assert _wait_until(lambda: _mock_store().get("result_code") == 7)

    # 工位过滤: 规则 channels=[0], 工位 5 的周期不该写
    dispatch_plc_event("cycle_end", {"cycle": {"id": 3, "result": "NG"}}, 5)
    time.sleep(0.3)
    assert _mock_store().get("result_code") == 7, "channels 过滤失效: 工位 5 事件误写"


def test_plc_heartbeat_and_manual_write(client, plc_janitor):
    cid = _mk_connection(
        client, "PLCT-hb",
        write_rules=[{
            "on": "heartbeat", "period_ms": 80,
            "writes": [{"point": "data_received", "value": "toggle"}],
        }])
    _wait_until(lambda: client.get(
        f"/api/v1/plc/connections/{cid}/live").json().get("status") == "connected")

    # 心跳翻转: 观察到值两种状态都出现
    seen = set()

    def _observe():
        v = client.get(f"/api/v1/plc/connections/{cid}/live").json()[
            "values"].get("data_received")
        seen.add(bool(v))
        return len(seen) == 2
    assert _wait_until(_observe, timeout=3.0), f"心跳未翻转: {seen}"

    # 手动写值 + 类型归一 (字符串 "42" → int16 42)
    r = client.post(f"/api/v1/plc/connections/{cid}/write",
                    json={"point": "result_code", "value": "42"})
    assert r.status_code == 200, r.text[:300]
    from backend.services.plc.manager import get_plc_manager
    assert get_plc_manager().get_engine(cid).driver.dump_store()["result_code"] == 42

    # 未知点位 → 400
    r = client.post(f"/api/v1/plc/connections/{cid}/write",
                    json={"point": "nope", "value": 1})
    assert r.status_code == 400

    # IO 日志有写记录
    logs = client.get(f"/api/v1/plc/connections/{cid}/logs").json()["logs"]
    assert any(item["dir"] == "write" for item in logs)


def test_plc_export_import(client, plc_janitor):
    cid = _mk_connection(client, "PLCT-export")
    cfg = client.get(f"/api/v1/plc/connections/{cid}/export").json()
    assert "id" not in cfg and cfg["name"] == "PLCT-export"
    cfg["name"] = "PLCT-import"
    r = client.post("/api/v1/plc/import", json=cfg)
    assert r.status_code == 200
    assert r.json()["enabled"] is False      # 导入默认不启用
