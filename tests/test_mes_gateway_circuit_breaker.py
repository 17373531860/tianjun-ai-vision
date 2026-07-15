# -*- coding: utf-8 -*-
"""v3.38 推送熔断器单测: 连续失败熔断 → 冷却期跳过 → 半开探测 → 成功恢复。

背景 (川南现场): 对端接收服务没起时, 每个周期的出站推送都要傻等完整超时
(默认 30s×重试), 白耗 MES 工作线程。熔断器让"端点持续不在线"的代价从
每周期一次超时降为冷却期内零网络请求 + 到点一次探测。

覆盖矩阵:
- B1 阈值前不熔断 (逐次失败仍然真实发送)
- B2 达阈值熔断, 冷却期内跳过 (adapter.send 不被调用)
- B3 冷却到期半开: 放一次探测; 探测失败 → 重新熔断
- B4 半开探测成功 → 熔断关闭, 恢复正常推送
- B5 cb_enabled=False 时完全禁用 (行为与老版一致)
- B6 手动复位 reset_circuit 立即恢复
- B7 熔断状态快照 get_circuit_state 字段正确
"""
import time

from backend.services.mes_gateway import MESGateway


# ==================== 测试替身 (与 test_mes_gateway_lock_release 同族) ====================
class _Session:
    def __init__(self, conns):
        self._conns = conns

    def add(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def query(self, model):
        return _Query(self._conns)


class _Query:
    def __init__(self, items):
        self._items = items

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._items


class _Conn:
    def __init__(self, cid=1, name="测试连接", cb_cfg=None):
        self.id = cid
        self.name = name
        self.adapter_type = "rest"
        self.enabled = True
        self.config = {"url": "http://127.0.0.1:9/dead", "timeout": 1,
                       **(cb_cfg or {})}
        self.push_events = ["cycle_end"]
        self.bound_channels = None
        self.retry_count = 0
        self.retry_interval_sec = 0
        self.last_sync_at = None


class _Adapter:
    """可切换成败的假适配器 (失败返回 error 字典, 与真实 REST 适配器一致)。"""

    def __init__(self):
        self.send_calls = 0
        self.next_ok = False

    def build_payload(self, full_context, config):
        return full_context

    def send(self, payload, config):
        self.send_calls += 1
        if self.next_ok:
            return {"status_code": 200, "body": {"ok": True}, "duration_ms": 5}
        return {"status_code": 0, "body": None, "duration_ms": 1000,
                "error": "连接失败: 模拟死端点"}

    def check_response(self, result, config):
        return (result or {}).get("status_code") == 200


def _make_gateway(monkeypatch, conn, adapter):
    session = _Session([conn])
    monkeypatch.setattr("backend.services.mes_gateway.SessionLocal", lambda: session)
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)
    monkeypatch.setattr(MESGateway, "_alarm_dedup_should_skip",
                        lambda self, db, et, ctx: False)
    monkeypatch.setattr(MESGateway, "_record_active_alarm_if_alarm",
                        lambda self, db, et, ctx, channel_id=None: None)
    return MESGateway()


def _dispatch(gw, n=1):
    for _ in range(n):
        gw.dispatch("cycle_end", {"cycle": {"result": "OK"}}, channel_id=0)


# ==================== 用例 ====================
def test_b1_below_threshold_keeps_sending(monkeypatch):
    conn = _Conn(cb_cfg={"cb_fail_threshold": 5})
    adapter = _Adapter()
    gw = _make_gateway(monkeypatch, conn, adapter)

    _dispatch(gw, 4)  # 4 次失败 < 阈值 5
    assert adapter.send_calls == 4, "阈值前每次都应真实发送"
    assert gw.get_circuit_state(conn.id)["open"] is False


def test_b2_open_after_threshold_then_skip(monkeypatch):
    conn = _Conn(cb_cfg={"cb_fail_threshold": 3, "cb_cooldown_sec": 3600})
    adapter = _Adapter()
    gw = _make_gateway(monkeypatch, conn, adapter)

    _dispatch(gw, 3)  # 达阈值 → 熔断
    assert gw.get_circuit_state(conn.id)["open"] is True
    calls_at_open = adapter.send_calls

    _dispatch(gw, 5)  # 冷却期内 (1h) 全部跳过
    assert adapter.send_calls == calls_at_open, "熔断期内不得发起网络请求"


def _expire_cooldown(gw, conn_id):
    """把熔断截止时间拨到过去, 模拟冷却期结束 (生产冷却下限 1s, 测试不真等)。"""
    gw._cb_state[conn_id]["open_until"] = time.time() - 0.01


def test_b3_half_open_probe_fail_reopens(monkeypatch):
    conn = _Conn(cb_cfg={"cb_fail_threshold": 2})
    adapter = _Adapter()
    gw = _make_gateway(monkeypatch, conn, adapter)

    _dispatch(gw, 2)  # 熔断
    assert gw.get_circuit_state(conn.id)["open"] is True

    _expire_cooldown(gw, conn.id)
    calls_before = adapter.send_calls
    _dispatch(gw, 1)  # 半开: 放行一次探测 (仍失败)
    assert adapter.send_calls == calls_before + 1, "冷却到期应放行探测请求"
    assert gw.get_circuit_state(conn.id)["open"] is True, "探测失败应重新熔断"


def test_b4_half_open_probe_success_recovers(monkeypatch):
    conn = _Conn(cb_cfg={"cb_fail_threshold": 2})
    adapter = _Adapter()
    gw = _make_gateway(monkeypatch, conn, adapter)

    _dispatch(gw, 2)  # 熔断
    _expire_cooldown(gw, conn.id)
    adapter.next_ok = True  # 端点恢复
    _dispatch(gw, 1)  # 探测成功
    assert gw.get_circuit_state(conn.id)["open"] is False, "探测成功应关闭熔断"

    calls_before = adapter.send_calls
    _dispatch(gw, 3)  # 恢复后正常推送
    assert adapter.send_calls == calls_before + 3


def test_b5_disabled_never_opens(monkeypatch):
    conn = _Conn(cb_cfg={"cb_enabled": False, "cb_fail_threshold": 2})
    adapter = _Adapter()
    gw = _make_gateway(monkeypatch, conn, adapter)

    _dispatch(gw, 10)
    assert adapter.send_calls == 10, "禁用熔断时应保持老版行为: 每次都发"
    assert gw.get_circuit_state(conn.id)["open"] is False


def test_b6_manual_reset(monkeypatch):
    conn = _Conn(cb_cfg={"cb_fail_threshold": 2, "cb_cooldown_sec": 3600})
    adapter = _Adapter()
    gw = _make_gateway(monkeypatch, conn, adapter)

    _dispatch(gw, 2)
    assert gw.get_circuit_state(conn.id)["open"] is True
    gw.reset_circuit(conn.id)
    assert gw.get_circuit_state(conn.id)["open"] is False

    calls_before = adapter.send_calls
    _dispatch(gw, 1)
    assert adapter.send_calls == calls_before + 1, "复位后应立即恢复真实发送"


def test_b7_state_snapshot_fields(monkeypatch):
    conn = _Conn(cb_cfg={"cb_fail_threshold": 2, "cb_cooldown_sec": 3600})
    adapter = _Adapter()
    gw = _make_gateway(monkeypatch, conn, adapter)

    assert gw.get_circuit_state(999) == {"open": False, "fails": 0, "episodes": 0}
    _dispatch(gw, 2)
    st = gw.get_circuit_state(conn.id)
    assert st["open"] is True and st["fails"] == 2 and st["episodes"] == 1
    assert st["reopen_in_sec"] > 0
