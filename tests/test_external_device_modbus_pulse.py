"""外设 `modbus_pulse` 完成脉冲 —— 协议层 + per_item 触发层单测。

覆盖:
  1. 协议层: 写线圈 on→off 两次、写寄存器分支、地址三种换算、连不上只落 last_error
  2. 触发层: A(all_covered) 边沿只发一次 / B(cycle_ok) 只在判合格时发 / NG 不发
  3. 绑定: 只触发绑定本工位的设备, 未绑定工位一律不发
  4. 冷却: cooldown_ms 内第二次入队被挡, 手动试发不受限
  5. 热路径: 推理线程只入队, 不建连接不做 I/O (断言 client 从未被创建 + 耗时极短)

pymodbus 全程用假客户端替身 (monkeypatch pymodbus.client 的类), 不需要真 PLC。
"""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from backend.api.source import VideoSourceManager
from backend.services import external_device_pulse as pulse_mod
from backend.services.external_device import ExternalDeviceService
from backend.services.external_device_models import DeviceConnection


# ==================== 假 pymodbus 客户端 ====================
class FakeModbusResponse:
    def __init__(self, error: bool = False):
        self._error = error

    def isError(self):
        return self._error

    def __str__(self):
        return "FakeModbusError" if self._error else "FakeModbusOk"


class FakeModbusClient:
    """记录所有写操作的假客户端。类属性累计所有实例, 便于断言"从未建连接"。"""
    instances: list = []
    connect_result = True
    write_error = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.writes: list = []
        self.connected = False
        self.closed = False
        FakeModbusClient.instances.append(self)

    # pymodbus 3.13+ 用 device_id=, 这里跟新版签名, 让 pulse_slave_kwarg 走 device_id 分支
    def write_coil(self, address, value, device_id=1):
        self.writes.append(("coil", address, value, device_id))
        return FakeModbusResponse(FakeModbusClient.write_error)

    def write_register(self, address, value, device_id=1):
        self.writes.append(("register", address, value, device_id))
        return FakeModbusResponse(FakeModbusClient.write_error)

    def connect(self):
        self.connected = FakeModbusClient.connect_result
        return FakeModbusClient.connect_result

    def close(self):
        self.closed = True

    @classmethod
    def reset(cls):
        cls.instances = []
        cls.connect_result = True
        cls.write_error = False


@pytest.fixture(autouse=True)
def fake_pymodbus(monkeypatch):
    """把 pymodbus 的 TCP/串口客户端换成假替身。"""
    FakeModbusClient.reset()
    import pymodbus.client as pmc
    monkeypatch.setattr(pmc, "ModbusTcpClient", FakeModbusClient)
    monkeypatch.setattr(pmc, "ModbusSerialClient", FakeModbusClient)
    yield
    FakeModbusClient.reset()


# ==================== 工具 ====================
def _pulse_cfg(**over) -> dict:
    cfg = {
        "transport": "tcp", "host": "127.0.0.1", "tcp_port": 502,
        "slave_id": 1, "target": "coil",
        "address_mode": "delta_m", "address": 100,
        "on_value": 1, "off_value": 0,
        "pulse_ms": 10,          # 单测里缩到 10ms, 出厂默认 300ms
        "timeout": 1,
        "trigger_enabled": True, "trigger_mode": "cycle_ok",
        "cooldown_ms": 0,
    }
    cfg.update(over)
    return cfg


def _make_pulse_conn(device_id: int = 1, channel_id: int = 0, **cfg_over) -> DeviceConnection:
    return DeviceConnection(
        device_id=device_id, name=f"PLC气阀{device_id}", device_role="plc",
        protocol="modbus_pulse", ip="127.0.0.1", port=502,
        serial_port=None, serial_baud=9600,
        protocol_config=_pulse_cfg(**cfg_over),
        parse_mode="direct", parse_config={},
        station_id=None, channel_id=channel_id,
        data_target="extra_fields", validation_rules={}, enabled=True,
    )


def _make_service(conns) -> ExternalDeviceService:
    """建一个不起线程的服务实例, 直接塞好连接对象 (测入队/过滤逻辑)。"""
    svc = ExternalDeviceService()
    svc._log_data = lambda *a, **k: None      # 不碰 DB, 日志内容另有专测
    for conn in conns:
        svc._connections[conn.device_id] = conn
    return svc


@pytest.fixture
def bind_service(monkeypatch):
    """把服务单例替换掉, 让 per_item 的 get_external_device_service() 拿到测试实例。"""
    def _bind(svc):
        import backend.services.external_device as ed
        monkeypatch.setattr(ed, "_service_instance", svc)
        return svc
    return _bind


# ==================== 1. 地址换算 ====================
class TestAddressResolve:
    def test_delta_m_adds_base(self):
        """台达 DVP/ES3: M0 → 2048, 所以 M100 → 2148"""
        assert pulse_mod.resolve_pulse_address({"address": 0}) == 2048
        assert pulse_mod.resolve_pulse_address({"address": 100}) == 2148

    def test_raw_mode_passthrough(self):
        assert pulse_mod.resolve_pulse_address(
            {"address": 2148, "address_mode": "raw"}) == 2148

    def test_doc1_mode(self):
        """1 基文档编号: 00001→0 线圈, 40001→0 保持寄存器"""
        assert pulse_mod.resolve_pulse_address(
            {"address": 1, "address_mode": "doc1"}) == 0
        assert pulse_mod.resolve_pulse_address(
            {"address": 40001, "address_mode": "doc1"}) == 0
        assert pulse_mod.resolve_pulse_address(
            {"address": 40011, "address_mode": "doc1"}) == 10

    def test_bad_address_does_not_raise(self):
        assert pulse_mod.resolve_pulse_address({"address": None}) == 2048
        assert pulse_mod.resolve_pulse_address({"address": "abc"}) == 2048


# ==================== 2. 协议层: 真写一次脉冲 ====================
class TestPulseExecute:
    def test_coil_pulse_writes_on_then_off(self):
        svc = _make_service([])
        conn = _make_pulse_conn()
        res = svc._pulse_execute(conn, {"source": "manual"})

        assert res["success"], res["message"]
        assert len(FakeModbusClient.instances) == 1
        client = FakeModbusClient.instances[0]
        assert client.writes == [
            ("coil", 2148, True, 1),
            ("coil", 2148, False, 1),
        ], f"应先写 ON 再写 OFF, 实际 {client.writes}"
        assert client.closed, "用完必须关连接"
        assert conn.pulse_count == 1
        assert conn.status == "connected"
        assert conn.last_error == ""

    def test_register_target_uses_write_register(self):
        svc = _make_service([])
        conn = _make_pulse_conn(target="register", address_mode="raw",
                                address=0, on_value=1, off_value=0)
        res = svc._pulse_execute(conn, {"source": "manual"})
        assert res["success"]
        assert FakeModbusClient.instances[0].writes == [
            ("register", 0, 1, 1),
            ("register", 0, 0, 1),
        ]

    def test_pulse_ms_zero_skips_reset(self):
        """脉冲宽度 0 = 只写 ON, 由 PLC 程序自己清零"""
        svc = _make_service([])
        conn = _make_pulse_conn(pulse_ms=0)
        assert svc._pulse_execute(conn, {"source": "manual"})["success"]
        assert FakeModbusClient.instances[0].writes == [("coil", 2148, True, 1)]

    def test_connect_failure_only_records_last_error(self):
        """连不上 PLC: 返回失败 + 落 last_error, 绝不抛异常"""
        FakeModbusClient.connect_result = False
        svc = _make_service([])
        conn = _make_pulse_conn()
        res = svc._pulse_execute(conn, {"source": "manual"})
        assert res["success"] is False
        assert "连接 PLC 失败" in res["message"]
        assert conn.status == "error"
        assert conn.last_error
        assert conn.pulse_count == 0

    def test_write_error_reported(self):
        FakeModbusClient.write_error = True
        svc = _make_service([])
        conn = _make_pulse_conn()
        res = svc._pulse_execute(conn, {"source": "manual"})
        assert res["success"] is False
        assert conn.pulse_count == 0

    def test_missing_host_is_config_error(self):
        svc = _make_service([])
        conn = _make_pulse_conn()
        conn.ip = None
        conn.protocol_config["host"] = ""
        res = svc._pulse_execute(conn, {"source": "manual"})
        assert res["success"] is False
        assert "配置错误" in res["message"]
        assert FakeModbusClient.instances == []

    def test_pulse_writes_log_record(self):
        """脉冲要落一条 external_device_logs, 现场靠它排查"""
        svc = ExternalDeviceService()
        logged = []
        svc._log_data = lambda conn, raw, parsed, ok, err=None, barcode=None: \
            logged.append((raw, parsed, ok, err))
        conn = _make_pulse_conn()
        svc._pulse_execute(conn, {"source": "per_item:cycle_ok"})
        assert len(logged) == 1
        raw, parsed, ok, _err = logged[0]
        assert "PULSE" in raw and "per_item:cycle_ok" in raw
        assert parsed["address"] == 2148 and parsed["pulse"] == "ok"
        assert ok is True


# ==================== 3. 入队 / 冷却 / 设备线程 ====================
class TestEnqueueAndCooldown:
    def test_enqueue_is_io_free(self):
        """入队只 append, 不许建任何 Modbus 连接"""
        conn = _make_pulse_conn()
        svc = _make_service([conn])
        res = svc._enqueue_pulse(conn, source="per_item:cycle_ok")
        assert res["success"] and len(conn._pulse_queue) == 1
        assert FakeModbusClient.instances == [], "入队阶段不得建连接"
        assert conn._pulse_wakeup.is_set(), "入队要唤醒设备线程"

    def test_cooldown_blocks_second_enqueue(self):
        conn = _make_pulse_conn(cooldown_ms=5000)
        svc = _make_service([conn])
        assert svc._enqueue_pulse(conn, source="a")["success"]
        second = svc._enqueue_pulse(conn, source="b")
        assert second["success"] is False
        assert second["skipped"] == "cooldown"
        assert len(conn._pulse_queue) == 1

    def test_cooldown_expires(self):
        conn = _make_pulse_conn(cooldown_ms=30)
        svc = _make_service([conn])
        assert svc._enqueue_pulse(conn, source="a")["success"]
        time.sleep(0.05)
        assert svc._enqueue_pulse(conn, source="b")["success"]
        assert len(conn._pulse_queue) == 2

    def test_manual_pulse_ignores_cooldown(self):
        """现场调试要能连点「试发脉冲」, 手动不受 cooldown 限制"""
        conn = _make_pulse_conn(cooldown_ms=60000)
        svc = _make_service([conn])
        svc._enqueue_pulse(conn, source="per_item:cycle_ok")   # 先占掉冷却
        res = svc.pulse_device(conn.device_id, wait=False)
        assert res["success"], res["message"]
        assert len(conn._pulse_queue) == 2

    def test_pulse_device_rejects_wrong_protocol(self):
        conn = _make_pulse_conn()
        conn.protocol = "serial_command"
        svc = _make_service([conn])
        res = svc.pulse_device(conn.device_id)
        assert res["success"] is False
        assert "Modbus 完成脉冲" in res["message"]

    def test_pulse_device_missing_device(self):
        svc = _make_service([])
        assert svc.pulse_device(999)["success"] is False

    def test_device_thread_consumes_queue(self):
        """真起一次设备线程: 入队 → 线程写 PLC → wait=True 拿到真实结果"""
        conn = _make_pulse_conn()
        svc = _make_service([conn])
        t = threading.Thread(target=svc._modbus_pulse_loop, args=(conn,), daemon=True)
        t.start()
        try:
            res = svc.pulse_device(conn.device_id, wait=True)
            assert res["success"], res["message"]
            assert conn.pulse_count == 1
        finally:
            conn._stop_event.set()
            conn._pulse_wakeup.set()
            t.join(timeout=5)


# ==================== 4. per_item 触发 ====================
SCREW_POSITIONS = [(0.05 + i * 0.07, 0.50, 0.04, 0.04) for i in range(6)]
_DUMMY_FRAME = np.zeros((100, 100, 3), dtype=np.uint8)


def _det(label, x, y, w, h, conf=0.85):
    return {"label": label, "x": x, "y": y, "w": w, "h": h,
            "confidence": conf, "class_name": label}


def _screws():
    return [_det("螺丝", *p) for p in SCREW_POSITIONS]


def _screws_with_action(idx):
    return _screws() + [_det("打螺丝", *SCREW_POSITIONS[idx])]


def _per_item_project(finish_label="翻面"):
    return {
        "id": 9911, "name": "TEST_PLC_PULSE", "task_type": "detection",
        "logic_mode": "per_item",
        "steps_config": [{
            "id": 1, "label": "打螺丝", "displayLabel": "打螺丝",
            "enabled": True, "threshold": 50,
            "per_item": {
                "item_label": "螺丝", "action_label": "打螺丝",
                "item_tracking_iou": 0.3, "coverage_iou": 0.3,
                "sustain_frames": 3, "completion": "all_covered",
            },
        }],
        "events_config": [],
        "pipeline_config": {
            "per_item": {
                "stability_window_frames": 5,
                "stability_iou_threshold": 0.7,
                "item_timeout_seconds": 3.0,
                "lock_count_on_start": True,
                "finish_label": finish_label,
                "finish_sustain_frames": 3,
            },
        },
    }


def _make_vsm(channel_id=0):
    vsm = VideoSourceManager(channel_id=channel_id)
    vsm.set_project_config(_per_item_project())
    vsm._trigger_event = lambda event_id, reason: True
    return vsm


def _feed(vsm, dets, repeat=1):
    for _ in range(repeat):
        vsm._update_step_stats(list(dets), _DUMMY_FRAME)


def _cover_all(vsm):
    for i in range(len(SCREW_POSITIONS)):
        _feed(vsm, _screws_with_action(i), repeat=6)
        _feed(vsm, _screws(), repeat=2)


def _sources(conn):
    return [r["source"] for r in conn._pulse_queue]


class TestPerItemTrigger:
    def test_mode_b_fires_only_on_ok_settle(self, bind_service):
        """B(cycle_ok): 全部打完 → 收尾标签 → 判合格落账 → 发一次脉冲"""
        conn = _make_pulse_conn(trigger_mode="cycle_ok")
        bind_service(_make_service([conn]))

        vsm = _make_vsm()
        _feed(vsm, _screws(), repeat=7)
        assert vsm._per_item_session.cycle_active
        _cover_all(vsm)
        assert not conn._pulse_queue, "还没结算就不该发脉冲"

        _feed(vsm, [_det("翻面", 0.4, 0.1, 0.2, 0.1)], repeat=5)
        assert _sources(conn) == ["per_item:cycle_ok"]

    def test_mode_b_not_fired_on_ng(self, bind_service):
        """漏打 → 收尾 → 判 NG → 绝不发脉冲 (气阀不能给不合格件放行)"""
        conn = _make_pulse_conn(trigger_mode="cycle_ok")
        bind_service(_make_service([conn]))

        vsm = _make_vsm()
        _feed(vsm, _screws(), repeat=7)
        for i in range(len(SCREW_POSITIONS) - 2):        # 故意漏 2 颗
            _feed(vsm, _screws_with_action(i), repeat=6)
            _feed(vsm, _screws(), repeat=2)
        _feed(vsm, [_det("翻面", 0.4, 0.1, 0.2, 0.1)], repeat=5)

        assert vsm._per_item_last_ng_detail is not None, "这一轮应判 NG"
        assert list(conn._pulse_queue) == [], "NG 周期不许发完成脉冲"

    def test_mode_a_fires_once_on_edge(self, bind_service):
        """A(all_covered): 首次全覆盖那一帧发一次, 之后再多喂帧也不重复"""
        conn = _make_pulse_conn(trigger_mode="all_covered", cooldown_ms=0)
        bind_service(_make_service([conn]))

        vsm = _make_vsm()
        _feed(vsm, _screws(), repeat=7)
        _cover_all(vsm)
        assert _sources(conn) == ["per_item:all_covered"]

        _feed(vsm, _screws(), repeat=20)                # 继续跑, 不许再发
        assert _sources(conn) == ["per_item:all_covered"]

    def test_mode_a_device_ignores_ok_settle(self, bind_service):
        """配 A 的设备不该被 B 的结算路径二次触发 (整周期总共只有一次)"""
        conn = _make_pulse_conn(trigger_mode="all_covered", cooldown_ms=0)
        bind_service(_make_service([conn]))

        vsm = _make_vsm()
        _feed(vsm, _screws(), repeat=7)
        _cover_all(vsm)
        _feed(vsm, [_det("翻面", 0.4, 0.1, 0.2, 0.1)], repeat=5)
        assert _sources(conn) == ["per_item:all_covered"]

    def test_new_cycle_can_fire_again(self, bind_service):
        """边沿锁是"每周期"的: 下一板打完还要能发"""
        conn = _make_pulse_conn(trigger_mode="all_covered", cooldown_ms=0)
        bind_service(_make_service([conn]))

        vsm = _make_vsm()
        for _ in range(2):
            _feed(vsm, _screws(), repeat=7)
            _cover_all(vsm)
            _feed(vsm, [_det("翻面", 0.4, 0.1, 0.2, 0.1)], repeat=5)
        assert _sources(conn) == ["per_item:all_covered"] * 2

    def test_only_bound_channel_fires(self, bind_service):
        """多设备: 只发绑定本工位的; 别的工位 / 未绑定的都不发"""
        mine = _make_pulse_conn(device_id=1, channel_id=0, trigger_mode="cycle_ok")
        other = _make_pulse_conn(device_id=2, channel_id=1, trigger_mode="cycle_ok")
        unbound = _make_pulse_conn(device_id=3, channel_id=None, trigger_mode="cycle_ok")
        bind_service(_make_service([mine, other, unbound]))

        vsm = _make_vsm(channel_id=0)
        _feed(vsm, _screws(), repeat=7)
        _cover_all(vsm)
        _feed(vsm, [_det("翻面", 0.4, 0.1, 0.2, 0.1)], repeat=5)

        assert len(mine._pulse_queue) == 1
        assert len(other._pulse_queue) == 0
        assert len(unbound._pulse_queue) == 0

    def test_trigger_disabled_device_not_fired(self, bind_service):
        conn = _make_pulse_conn(trigger_enabled=False)
        bind_service(_make_service([conn]))
        vsm = _make_vsm()
        _feed(vsm, _screws(), repeat=7)
        _cover_all(vsm)
        _feed(vsm, [_det("翻面", 0.4, 0.1, 0.2, 0.1)], repeat=5)
        assert list(conn._pulse_queue) == []

    def test_disabled_device_not_fired(self, bind_service):
        conn = _make_pulse_conn()
        conn.enabled = False
        bind_service(_make_service([conn]))
        vsm = _make_vsm()
        _feed(vsm, _screws(), repeat=7)
        _cover_all(vsm)
        _feed(vsm, [_det("翻面", 0.4, 0.1, 0.2, 0.1)], repeat=5)
        assert list(conn._pulse_queue) == []

    def test_no_pulse_device_is_noop(self, bind_service):
        """没配脉冲外设时整条链路是纯 no-op, 不许影响原有结算"""
        bind_service(_make_service([]))
        vsm = _make_vsm()
        events = []
        vsm._trigger_event = lambda eid, reason: events.append((eid, reason)) or True
        _feed(vsm, _screws(), repeat=7)
        _cover_all(vsm)
        _feed(vsm, [_det("翻面", 0.4, 0.1, 0.2, 0.1)], repeat=5)
        assert events and events[-1][0] == 1


class TestHotPathIsNonBlocking:
    def test_settle_frame_does_no_modbus_io(self, bind_service):
        """结算那一帧: 不许建连接、不许同步等 —— 断言 0 个 client + 帧耗时极短"""
        conn = _make_pulse_conn(trigger_mode="cycle_ok",
                                host="10.255.255.1", timeout=5)  # 故意不可达
        bind_service(_make_service([conn]))

        vsm = _make_vsm()
        _feed(vsm, _screws(), repeat=7)
        _cover_all(vsm)

        started = time.time()
        _feed(vsm, [_det("翻面", 0.4, 0.1, 0.2, 0.1)], repeat=5)
        elapsed = time.time() - started

        assert len(conn._pulse_queue) == 1, "脉冲应只入队"
        assert FakeModbusClient.instances == [], "推理线程不得创建 Modbus 客户端"
        assert elapsed < 1.0, f"结算帧耗时 {elapsed:.3f}s, 疑似在热路径里同步等 PLC"

    def test_notify_never_raises(self, bind_service):
        """服务内部抛错也不许穿透到推理线程"""
        svc = _make_service([_make_pulse_conn()])

        def boom(*_a, **_k):
            raise RuntimeError("模拟外设服务炸了")
        svc._enqueue_pulse = boom
        bind_service(svc)

        vsm = _make_vsm()
        _feed(vsm, _screws(), repeat=7)
        _cover_all(vsm)
        _feed(vsm, [_det("翻面", 0.4, 0.1, 0.2, 0.1)], repeat=5)   # 不该抛
        assert vsm._per_item_session.cycle_active is False


# ==================== 5. 连通性测试分支 ====================
class TestTestConnection:
    def test_test_connection_probe_only(self):
        svc = _make_service([])
        res = svc.test_connection(protocol="modbus_pulse", ip="127.0.0.1", port=502,
                                  protocol_config=_pulse_cfg())
        assert res["success"] is True
        assert "coil@2148" in res["message"]
        # 只连不写
        assert FakeModbusClient.instances[0].writes == []

    def test_test_connection_failure_message(self):
        FakeModbusClient.connect_result = False
        svc = _make_service([])
        res = svc.test_connection(protocol="modbus_pulse", ip="127.0.0.1", port=502,
                                  protocol_config=_pulse_cfg())
        assert res["success"] is False
        assert "连接失败" in res["message"]
