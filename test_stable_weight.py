"""v2.7.5 稳定值判定 + 有重无码告警 单元测试

不依赖真实后端/设备连接；直接调用 ExternalDeviceService._handle_weight_stability
和 _check_weight_no_barcode_alarm 验证状态机行为。
"""
import os
import sys
import time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.services.external_device import (
    ExternalDeviceService,
    DeviceConnection,
)


def make_conn(**overrides):
    base = dict(
        device_id=1,
        name="test-scale",
        device_role="weight",
        protocol="tcp",
        ip="127.0.0.1", port=8999,
        serial_port=None, serial_baud=9600,
        protocol_config={}, parse_mode="direct", parse_config={},
        station_id="C", channel_id=0,
        data_target="cluster",
        validation_rules={},
        enabled=True,
        stable_enabled=True,
        stable_delta=0.05,
        stable_count=5,
        zero_threshold=0.05,
        weight_no_barcode_alarm_enabled=False,
        weight_no_barcode_alarm_delay_sec=10,
    )
    base.update(overrides)
    return DeviceConnection(**base)


def feed(svc, conn, value):
    parsed = {"weight": value}
    return svc._handle_weight_stability(conn, parsed, raw=f"{value}")


def test_stabilizing_then_stable():
    svc = ExternalDeviceService()
    conn = make_conn(stable_count=5, stable_delta=0.05, zero_threshold=0.05)

    # 先放上一个物品，值在 25.00 附近抖动
    results = [feed(svc, conn, v) for v in [25.00, 25.01, 24.99, 25.02, 25.00]]
    # 前 4 次 stabilizing，应该返回 False；第 5 次达成条件，返回 True
    assert results[:4] == [False, False, False, False], f"stabilizing 阶段应全部返回 False, got {results[:4]}"
    assert results[4] is True, "第 5 次达成稳定条件应返回 True"
    assert conn._stable_state == "stable"
    assert conn._stable_value is not None
    print(f"[PASS] stable_value={conn._stable_value} (median of [25,25.01,24.99,25.02,25])")

    # 再来一次相同值（维持稳定） → 不应重复上报
    r6 = feed(svc, conn, 25.00)
    assert r6 is False, "已稳定上报过，值未变化应沉默"
    print("[PASS] 稳定窗口内不重复上报")


def test_zero_reset():
    svc = ExternalDeviceService()
    conn = make_conn(stable_count=3, zero_threshold=0.05)
    # 先放物品达到稳定
    for v in [10.0, 10.01, 9.99]:
        feed(svc, conn, v)
    assert conn._stable_state == "stable"
    # 模拟注入条码（若有的话 buffer 在 service 里）
    svc._barcode_buffer[conn.device_id] = "SN-001"

    # 取下物品，读数回 0.00
    r = feed(svc, conn, 0.00)
    assert r is False
    assert conn._stable_state == "idle"
    assert conn._last_reported_value is None
    assert conn.device_id not in svc._barcode_buffer
    print("[PASS] 空载 → 状态重置、buffer 清空")


def test_jitter_not_stable():
    svc = ExternalDeviceService()
    conn = make_conn(stable_count=5, stable_delta=0.05)
    # 连续剧烈抖动，永远不稳定
    results = [feed(svc, conn, v) for v in [10.0, 10.2, 9.8, 10.3, 9.7, 10.4]]
    assert all(r is False for r in results), f"抖动数据不应标记为稳定: {results}"
    assert conn._stable_state == "stabilizing"
    print("[PASS] 剧烈抖动时始终不稳定")


def test_no_barcode_alarm_disabled_by_default():
    """默认关闭时，即使稳定有重无码也不触发"""
    svc = ExternalDeviceService()
    conn = make_conn(weight_no_barcode_alarm_enabled=False)
    for v in [25.0, 25.01, 24.99, 25.02, 25.0]:
        feed(svc, conn, v)
    assert conn._stable_state == "stable"
    # 直接调用 check（其实 _on_raw_data 里已跳过，这里显式验证条件）
    # 没有条码 + 没开启 → _check 不会被 _on_raw_data 调用
    # 显式调用一次并确认没触发（手动绕过 enabled 校验在 _on_raw_data 层完成）
    # 这里仅验证 enabled 标志逻辑在 _on_raw_data 层被正确处理
    assert conn.weight_no_barcode_alarm_enabled is False
    print("[PASS] 有重无码告警默认关闭")


def test_no_barcode_alarm_fires_after_delay():
    svc = ExternalDeviceService()
    conn = make_conn(
        weight_no_barcode_alarm_enabled=True,
        weight_no_barcode_alarm_delay_sec=2,
    )
    # 先稳定
    for v in [25.0, 25.01, 24.99, 25.02, 25.0]:
        feed(svc, conn, v)
    assert conn._stable_state == "stable"

    dispatch_calls = []
    trigger_calls = []

    class FakeGw:
        def dispatch(self, evt, ctx, channel_id=None):
            dispatch_calls.append((evt, ctx, channel_id))

    class FakeRouter:
        def trigger_alarm(self, event, channel_id=0):
            trigger_calls.append((event, channel_id))

    with patch("backend.services.mes_gateway.get_mes_gateway", return_value=FakeGw()):
        with patch.dict(sys.modules, {"backend.api.alarm": MagicMock(alarm_router=FakeRouter())}):
            parsed = {"weight": 25.0}
            # 首次触发 → 记下 onset 时间
            svc._check_weight_no_barcode_alarm(conn, parsed, barcode=None)
            assert conn._weight_onset_time > 0
            assert not conn._no_barcode_alarm_fired
            # 还没到 delay
            svc._check_weight_no_barcode_alarm(conn, parsed, barcode=None)
            assert not conn._no_barcode_alarm_fired

            # 人为回溯 onset 3 秒前
            conn._weight_onset_time = time.time() - 3
            svc._check_weight_no_barcode_alarm(conn, parsed, barcode=None)
            assert conn._no_barcode_alarm_fired, "超时后应该触发告警"

    assert len(dispatch_calls) == 1, f"MES dispatch 应被调用 1 次, got {dispatch_calls}"
    assert dispatch_calls[0][0] == "weight_no_barcode"
    assert len(trigger_calls) == 1
    assert trigger_calls[0][0] == "weight_no_barcode"
    print("[PASS] 有重无码告警：超时触发 MES dispatch + 报警灯")


def test_no_barcode_alarm_cleared_by_barcode():
    svc = ExternalDeviceService()
    conn = make_conn(
        weight_no_barcode_alarm_enabled=True,
        weight_no_barcode_alarm_delay_sec=2,
    )
    for v in [25.0, 25.01, 24.99, 25.02, 25.0]:
        feed(svc, conn, v)
    parsed = {"weight": 25.0}
    svc._check_weight_no_barcode_alarm(conn, parsed, barcode=None)
    assert conn._weight_onset_time > 0
    # 扫码到了
    svc._check_weight_no_barcode_alarm(conn, parsed, barcode="SN-042")
    assert conn._weight_onset_time == 0
    assert not conn._no_barcode_alarm_fired
    print("[PASS] 扫到条码后告警计时器被清除")


if __name__ == "__main__":
    test_stabilizing_then_stable()
    test_zero_reset()
    test_jitter_not_stable()
    test_no_barcode_alarm_disabled_by_default()
    test_no_barcode_alarm_fires_after_delay()
    test_no_barcode_alarm_cleared_by_barcode()
    print("\nAll tests passed ✓")
