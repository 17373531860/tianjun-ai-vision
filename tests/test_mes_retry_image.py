"""出站网关 重试策略 + 截图压缩 + 超时分离 单测 (川南 v1.0 §5.1 / §7.1)。

覆盖:
  - 指数退避 1→2→4 (retry_backoff=exponential)
  - 仅 5xx/超时重试, 4xx 不重试 (retry_on_4xx=False)
  - 默认 fixed + 4xx 重试 (历史行为不变)
  - 截图超限自动降质压缩 (而非丢弃)
  - REST 适配器连接/读取分离超时 → requests 收到 (connect, read) 元组
"""
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db():
    from backend.db.database import Base
    from backend.models import models as _m  # noqa: F401
    from backend.models import auth_models as _a  # noqa: F401
    from backend.models import mes_models as _mes  # noqa: F401
    from backend.models import export_models as _ex  # noqa: F401
    from backend.models import plugin_models as _p  # noqa: F401

    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


class _FakeAdapter:
    """可配状态码的假适配器, 记录 send 调用次数。"""
    def __init__(self, status_code):
        self.status_code = status_code
        self.calls = 0

    def build_payload(self, ctx, cfg):
        return {"x": 1}

    def send(self, payload, cfg):
        self.calls += 1
        return {"status_code": self.status_code, "body": {}, "error": "e",
                "duration_ms": 1}

    def check_response(self, result, cfg):
        return False  # 永远失败, 逼出重试路径


def _mk_conn(db, **cfg_over):
    from backend.models.mes_models import MESConnection
    cfg = {"url": "http://x/report"}
    cfg.update(cfg_over)
    conn = MESConnection(name="c", adapter_type="rest", enabled=True,
                         push_events=["cycle_end"], retry_count=3,
                         retry_interval_sec=1, config=cfg)
    db.add(conn)
    db.commit()
    return conn


def _run(db, monkeypatch, adapter, **cfg_over):
    from backend.services import mes_gateway as gw_mod
    from backend.services.mes_gateway import get_mes_gateway
    delays = []
    monkeypatch.setattr(gw_mod, "get_adapter", lambda t: adapter)
    monkeypatch.setattr(gw_mod.time, "sleep", lambda s: delays.append(s))
    conn = _mk_conn(db, **cfg_over)
    ok = get_mes_gateway()._send_to_connection(db, conn, "cycle_end",
                                               {"result": "NG"}, 0)
    return ok, delays


# ==================== 重试退避 ====================
def test_exponential_backoff_1_2_4(db, monkeypatch):
    ad = _FakeAdapter(500)
    ok, delays = _run(db, monkeypatch, ad,
                      retry_backoff="exponential", retry_on_4xx=True)
    assert ok is False
    assert ad.calls == 4          # 1 + 3 retry
    assert delays == [1, 2, 4]    # 指数退避


def test_fixed_backoff_default(db, monkeypatch):
    ad = _FakeAdapter(500)
    ok, delays = _run(db, monkeypatch, ad)  # 默认 fixed
    assert ad.calls == 4
    assert delays == [1, 1, 1]


# ==================== 4xx 不重试 ====================
def test_4xx_no_retry_when_disabled(db, monkeypatch):
    ad = _FakeAdapter(400)
    ok, delays = _run(db, monkeypatch, ad, retry_on_4xx=False)
    assert ok is False
    assert ad.calls == 1          # 4xx 立即收场, 不重试
    assert delays == []


def test_4xx_still_retries_by_default(db, monkeypatch):
    ad = _FakeAdapter(400)
    ok, delays = _run(db, monkeypatch, ad)  # retry_on_4xx 默认 True
    assert ad.calls == 4          # 历史行为: 4xx 也重试


def test_5xx_retries_even_when_4xx_disabled(db, monkeypatch):
    ad = _FakeAdapter(503)
    ok, delays = _run(db, monkeypatch, ad, retry_on_4xx=False)
    assert ad.calls == 4          # 5xx 仍重试


# ==================== 截图超限自动压缩 ====================
def test_recompress_jpeg_under_limit():
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    from backend.services.mes_gateway import _recompress_jpeg_under

    # 平滑渐变图 (可压缩), q95 编码后取一半上限, 应能压到限内
    grad = np.tile(np.linspace(0, 255, 1280, dtype=np.uint8), (720, 1))
    img = cv2.cvtColor(grad, cv2.COLOR_GRAY2BGR)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    jpeg = buf.tobytes()
    limit = max(1, len(jpeg) // 2)
    out = _recompress_jpeg_under(jpeg, limit)
    assert out is not None
    assert len(out) <= limit


def test_snapshot_compress_params_from_config():
    """连接配置能覆盖压缩阶梯/缩放/最小边/缩图质量, 非法值忽略回落默认。"""
    from backend.services.mes_gateway import _snapshot_compress_params, _SNAPSHOT_DEFAULTS
    # 缺省 → 默认
    assert _snapshot_compress_params(None) == _SNAPSHOT_DEFAULTS
    assert _snapshot_compress_params({}) == _SNAPSHOT_DEFAULTS
    # 覆盖
    p = _snapshot_compress_params({
        "snapshot_quality_ladder": [80, 50],
        "snapshot_scale_factor": 0.5,
        "snapshot_scale_rounds": 2,
        "snapshot_min_edge": 64,
        "snapshot_scaled_quality": 40,
    })
    assert p["quality_ladder"] == [80, 50]
    assert p["scale_factor"] == 0.5
    assert p["scale_rounds"] == 2
    assert p["min_edge"] == 64
    assert p["scaled_quality"] == 40
    # 非法值忽略 (越界质量/空阶梯) → 回落默认
    p2 = _snapshot_compress_params({
        "snapshot_quality_ladder": [999, 0],   # 全越界 → 空 → 回落
        "snapshot_scale_factor": 5,            # 越界 → 回落
    })
    assert p2["quality_ladder"] == _SNAPSHOT_DEFAULTS["quality_ladder"]
    assert p2["scale_factor"] == _SNAPSHOT_DEFAULTS["scale_factor"]


def test_recompress_uses_config_ladder():
    """配置的质量阶梯应被 _recompress_jpeg_under 采用。"""
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    from backend.services.mes_gateway import _recompress_jpeg_under
    grad = np.tile(np.linspace(0, 255, 1280, dtype=np.uint8), (720, 1))
    img = cv2.cvtColor(grad, cv2.COLOR_GRAY2BGR)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    jpeg = buf.tobytes()
    out = _recompress_jpeg_under(jpeg, len(jpeg) // 2,
                                 {"quality_ladder": [40], "scale_factor": 0.75,
                                  "scale_rounds": 4, "min_edge": 32, "scaled_quality": 35})
    assert out is not None and len(out) <= len(jpeg) // 2


def test_reencode_jpeg_quality_shrinks():
    """按指定质量重编码: 低质量应明显更小。"""
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    from backend.services.mes_gateway import _reencode_jpeg_quality
    grad = np.tile(np.linspace(0, 255, 1280, dtype=np.uint8), (720, 1))
    img = cv2.cvtColor(grad, cv2.COLOR_GRAY2BGR)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    big = buf.tobytes()
    small = _reencode_jpeg_quality(big, 20)
    assert small is not None and len(small) < len(big)


def test_recompress_returns_none_for_impossible_limit():
    pytest.importorskip("cv2")
    import cv2
    import numpy as np
    from backend.services.mes_gateway import _recompress_jpeg_under

    img = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    out = _recompress_jpeg_under(buf.tobytes(), max_bytes=50)  # 50B 不可能
    assert out is None


# ==================== REST 超时分离 ====================
def test_rest_adapter_timeout_tuple(monkeypatch):
    import backend.services.mes_adapters.rest_adapter as ra
    captured = {}

    class _Resp:
        status_code = 200
        def json(self):
            return {"code": 0}

    def fake_request(method, url, **kw):
        captured.update(kw)
        return _Resp()

    monkeypatch.setattr(ra.requests, "request", fake_request)
    ra.RESTAdapter().send({"a": 1},
                          {"url": "http://x", "connect_timeout": 5, "read_timeout": 10})
    assert captured.get("timeout") == (5.0, 10.0)


def test_complete_template_keeps_boolean_iscomplete():
    """完工上报模板里 IsComplete:true 字面布尔值应原样保留, 不被转成字符串。"""
    from backend.services.mes_adapters.base import render_template
    tpl = {
        "TaskNo": "{order.order_no}",
        "IsComplete": True,
        "BeginTime": "{timestamp}",
    }
    ctx = {"order": {"order_no": "T-1"}, "timestamp": "2026-06-27T10:00:00"}
    out = render_template(tpl, ctx)
    assert out["TaskNo"] == "T-1"
    assert out["IsComplete"] is True            # 真布尔, 非 "True"
    assert out["BeginTime"] == "2026-06-27T10:00:00"


def test_rest_adapter_timeout_single_default(monkeypatch):
    import backend.services.mes_adapters.rest_adapter as ra
    captured = {}

    class _Resp:
        status_code = 200
        def json(self):
            return {}

    monkeypatch.setattr(ra.requests, "request",
                        lambda method, url, **kw: captured.update(kw) or _Resp())
    ra.RESTAdapter().send({"a": 1}, {"url": "http://x"})
    assert captured.get("timeout") == 30
