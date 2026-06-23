"""B2 称重稳定中过程日志按秒采样写测试。

stabilizing(抖动)阶段不再每帧落库, 节流到 ~1 条/秒; 业务记录不受影响。
"""
import time
import types


class _StubPipeline:
    """只取 _log_data_sampled 这一个方法来验证节流, 不拖整个 service。"""
    def __init__(self):
        from backend.services.external_device_pipeline import ExternalDevicePipelineMixin
        self._mixin = ExternalDevicePipelineMixin
        self.writes = 0

    def _log_data(self, conn, raw, parsed, is_valid, error=None, barcode=None):
        self.writes += 1


def _bind_sampled(stub):
    from backend.services.external_device_pipeline import ExternalDevicePipelineMixin
    return types.MethodType(ExternalDevicePipelineMixin._log_data_sampled, stub)


def _fake_conn():
    c = types.SimpleNamespace()
    c.device_id = 1
    c.name = "scale"
    return c


def test_b2_stabilizing_logs_throttled_to_one_per_second():
    stub = _StubPipeline()
    sampled = _bind_sampled(stub)
    conn = _fake_conn()

    # 1 秒内疯狂写 100 次 → 实际只落 1 条
    t0 = time.time()
    for _ in range(100):
        sampled(conn, "raw", {"weight": 1.0}, True, "stabilizing", None, min_interval=1.0)
        if time.time() - t0 > 0.4:
            break
    assert stub.writes == 1, f"节流失效, 落了 {stub.writes} 条"


def test_b2_after_interval_logs_again():
    stub = _StubPipeline()
    sampled = _bind_sampled(stub)
    conn = _fake_conn()

    sampled(conn, "raw", {"weight": 1.0}, True, "stabilizing", None, min_interval=0.05)
    time.sleep(0.06)
    sampled(conn, "raw", {"weight": 1.0}, True, "stabilizing", None, min_interval=0.05)
    assert stub.writes == 2  # 间隔过了, 第二条放行
