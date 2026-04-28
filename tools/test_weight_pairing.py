"""端到端仿真:称重器 + 扫码器配对模式测试。

覆盖:
- stable 模式 (老逻辑, 回归):
    * 抖动 → 不派发
    * 连续稳定 → 派发一次
    * 扫码迟到 (set_barcode) → 立即用 _last_reported_value 补发
- instant 模式 (v3.1.1 新):
    * 称重数据流入但无条码 → 不派发, 只更新 last_parsed
    * 扫码先到 → 暂存等称重兜底
    * 称重先到 → 扫码瞬间立即用 last_parsed 派发, buffer 清空
    * 工件未离开秤, 新工件直接扫码 → 用秤上当前读数 (含旧重) 派发, 不串台
    * 同一码连续派发不会因为 buffer 残留再触发第二次

运行: python tools/test_weight_pairing.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# noqa: E402 - 必须在 sys.path 之后
from backend.services.external_device import ExternalDeviceService
from backend.services.external_device_models import DeviceConnection


# ----------------------- 测试支架 -----------------------

class _FakeService(ExternalDeviceService):
    """屏蔽 DB / cluster / extra_fields 副作用, 只记录 dispatch 调用。"""
    def __init__(self):
        super().__init__()
        self.dispatched: list[tuple[str, dict, str]] = []  # (device_name, parsed, barcode)
        self.logs: list[tuple[str, dict, bool, str, str]] = []  # (raw, parsed, is_valid, error, barcode)

    def _log_data(self, conn, raw, parsed, is_valid, error=None, barcode=None):
        self.logs.append((raw, parsed, is_valid, error, barcode))

    def _dispatch(self, conn, parsed, barcode=None):
        self.dispatched.append((conn.name, dict(parsed), barcode))


def _make_conn(*, pairing_mode="stable", stable_enabled=True, name="scale-A",
               device_id=1) -> DeviceConnection:
    return DeviceConnection(
        device_id=device_id,
        name=name,
        device_role="weight",
        protocol="virtual",
        ip=None, port=None, serial_port=None, serial_baud=9600,
        protocol_config={},
        parse_mode="direct",
        parse_config={},
        station_id="A",
        channel_id=0,
        data_target="extra_fields",
        validation_rules={},
        enabled=True,
        stable_enabled=stable_enabled,
        stable_delta=0.05,
        stable_count=3,
        zero_threshold=0.05,
        weight_no_barcode_alarm_enabled=False,
        weight_no_barcode_alarm_delay_sec=10,
        pairing_mode=pairing_mode,
    )


# ----------------------- 测试用例 -----------------------

def case_stable_jitter_then_settle():
    """stable 模式: 抖动期不派发, 稳定后才派发一次"""
    svc = _FakeService()
    conn = _make_conn(pairing_mode="stable", stable_enabled=True)
    svc._connections[conn.device_id] = conn

    # 模拟 3 帧抖动 (差值 > 0.05) + 3 帧稳定 (落在 0.02 区间)
    for raw in ["10.5", "10.7", "10.4"]:
        svc._on_raw_data(conn, raw)
    assert svc.dispatched == [], f"stable 抖动期不应派发, 却派发了 {svc.dispatched}"

    for raw in ["12.50", "12.51", "12.49"]:
        svc._on_raw_data(conn, raw)

    # 没条码 → dispatch 会被调用但 cluster 分支拦截; 我们的 mock 只记录调用
    # 此处期望 dispatch 被调用一次 (稳定首报)
    assert len(svc.dispatched) == 1, \
        f"stable 稳定后应派发 1 次, 实际 {len(svc.dispatched)}: {svc.dispatched}"
    name, parsed, barcode = svc.dispatched[0]
    assert abs(parsed["weight"] - 12.50) < 0.05, f"中位数偏差: {parsed}"
    assert barcode is None
    print("[OK] stable_jitter_then_settle")


def case_stable_late_barcode_rebind():
    """stable 模式: 已稳定后扫码到达 → set_barcode 立即用 _last_reported_value 补发"""
    svc = _FakeService()
    conn = _make_conn(pairing_mode="stable")
    svc._connections[conn.device_id] = conn

    for raw in ["20.00", "20.01", "20.02"]:
        svc._on_raw_data(conn, raw)
    assert len(svc.dispatched) == 1, "稳定首报应派发 1 次"
    initial = len(svc.dispatched)

    svc.set_barcode(conn.device_id, "BC_LATE_001")
    assert len(svc.dispatched) == initial + 1, "扫码迟到应再派发一次"
    name, parsed, bc = svc.dispatched[-1]
    assert bc == "BC_LATE_001"
    assert abs(parsed["weight"] - 20.0) < 0.1
    print("[OK] stable_late_barcode_rebind")


def case_instant_no_barcode_no_dispatch():
    """instant 模式: 没条码时单纯收数据不应派发, 只更新 last_parsed"""
    svc = _FakeService()
    conn = _make_conn(pairing_mode="instant", stable_enabled=False)
    svc._connections[conn.device_id] = conn

    for raw in ["8.10", "8.20", "8.30", "8.40"]:
        svc._on_raw_data(conn, raw)

    assert svc.dispatched == [], \
        f"instant 模式无条码不应派发, 实际 {svc.dispatched}"
    assert conn.last_parsed is not None
    assert abs(conn.last_parsed["weight"] - 8.40) < 1e-6, \
        f"last_parsed 应为最新读数, got {conn.last_parsed}"
    print("[OK] instant_no_barcode_no_dispatch")


def case_instant_scan_after_weight():
    """instant 模式: 称重先到 + 扫码后到 → 立即用最新 last_parsed 派发, buffer 清空"""
    svc = _FakeService()
    conn = _make_conn(pairing_mode="instant", stable_enabled=False)
    svc._connections[conn.device_id] = conn

    svc._on_raw_data(conn, "15.55")  # last_parsed.weight = 15.55
    assert svc.dispatched == []

    svc.set_barcode(conn.device_id, "BC_INSTANT_A")

    assert len(svc.dispatched) == 1, f"扫码即派发 1 次, 实际 {svc.dispatched}"
    name, parsed, bc = svc.dispatched[0]
    assert bc == "BC_INSTANT_A"
    assert abs(parsed["weight"] - 15.55) < 1e-6, f"应绑定最新读数 15.55, 实际 {parsed}"
    assert conn.device_id not in svc._barcode_buffer, "派发后应立即清空 buffer"
    print("[OK] instant_scan_after_weight")


def case_instant_scan_before_weight():
    """instant 模式: 扫码先到, 此时 last_parsed=None → 暂存; 等下一帧称重数据兜底派发"""
    svc = _FakeService()
    conn = _make_conn(pairing_mode="instant", stable_enabled=False)
    svc._connections[conn.device_id] = conn

    svc.set_barcode(conn.device_id, "BC_PRE_SCAN")
    assert svc.dispatched == [], "扫码先到 last_parsed=None 不应派发"
    assert svc._barcode_buffer.get(conn.device_id) == "BC_PRE_SCAN"

    svc._on_raw_data(conn, "9.99")
    assert len(svc.dispatched) == 1, f"称重兜底应派发 1 次, 实际 {svc.dispatched}"
    name, parsed, bc = svc.dispatched[0]
    assert bc == "BC_PRE_SCAN"
    assert abs(parsed["weight"] - 9.99) < 1e-6
    assert conn.device_id not in svc._barcode_buffer, "兜底派发后应清 buffer"
    print("[OK] instant_scan_before_weight")


def case_instant_continuous_flow_no_zero():
    """instant 模式连续上料场景:
    工件 A (5kg) 上秤 → 扫 A 的码 → 派发 (5kg, A);
    A 没回零, B (3kg) 直接压上 (秤显示 8kg) → 扫 B 的码 → 派发 (8kg, B);
    "宁可不准也不能丢" 是用户明确接受的语义。
    """
    svc = _FakeService()
    conn = _make_conn(pairing_mode="instant", stable_enabled=False, device_id=2)
    svc._connections[conn.device_id] = conn

    svc._on_raw_data(conn, "5.00")
    svc.set_barcode(conn.device_id, "BC_A")
    assert len(svc.dispatched) == 1
    name1, p1, bc1 = svc.dispatched[0]
    assert bc1 == "BC_A" and abs(p1["weight"] - 5.0) < 1e-6

    # B 压上去, 秤跳到 8.0; 接下来扫 B 的码
    svc._on_raw_data(conn, "8.00")
    # 此时 last_parsed 已更新, buffer 是空的, 不会重复派发
    assert len(svc.dispatched) == 1, "中间帧无条码不应派发"

    svc.set_barcode(conn.device_id, "BC_B")
    assert len(svc.dispatched) == 2, "B 扫码应触发第二次派发"
    name2, p2, bc2 = svc.dispatched[1]
    assert bc2 == "BC_B" and abs(p2["weight"] - 8.0) < 1e-6, \
        f"B 应绑定 8.0kg (含 A 的合重), 实际 {p2}"
    print("[OK] instant_continuous_flow_no_zero")


def case_instant_no_double_dispatch_for_same_barcode():
    """instant 模式:同一条码扫一次后, 后续每帧称重数据不会因为残留 buffer 再触发派发。"""
    svc = _FakeService()
    conn = _make_conn(pairing_mode="instant", stable_enabled=False, device_id=3)
    svc._connections[conn.device_id] = conn

    svc._on_raw_data(conn, "1.20")
    svc.set_barcode(conn.device_id, "BC_X")
    assert len(svc.dispatched) == 1

    for raw in ["1.21", "1.22", "1.23", "1.24"]:
        svc._on_raw_data(conn, raw)

    assert len(svc.dispatched) == 1, \
        f"扫码消费后续帧不应再派发, 实际 {svc.dispatched}"
    print("[OK] instant_no_double_dispatch_for_same_barcode")


# ----------------------- 主入口 -----------------------

def main():
    cases = [
        case_stable_jitter_then_settle,
        case_stable_late_barcode_rebind,
        case_instant_no_barcode_no_dispatch,
        case_instant_scan_after_weight,
        case_instant_scan_before_weight,
        case_instant_continuous_flow_no_zero,
        case_instant_no_double_dispatch_for_same_barcode,
    ]
    failed = 0
    for c in cases:
        try:
            c()
        except AssertionError as e:
            print(f"[FAIL] {c.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {c.__name__}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n=== Total: {len(cases)}, Passed: {len(cases) - failed}, Failed: {failed} ===")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
