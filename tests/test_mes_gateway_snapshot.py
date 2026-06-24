"""M1a 出站报文附带"当前画面截图"能力测试.

覆盖:
- _capture_snapshot_base64: 正常抓帧 / 工位未配置 / 无帧 / 超 max_bytes
- _send_to_connection 注入: attach_snapshot 开关守门 / data_uri / 抓帧失败不阻断推送
"""
import base64

import backend.api.channel_manager as cm_mod
from backend.services.mes_gateway import MESGateway


# ==================== 测试替身 ====================
class _FakeMgr:
    def __init__(self, jpeg):
        self._jpeg = jpeg

    def get_snapshot(self):
        return self._jpeg


class _FakeChannelManager:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, cid=0):
        if cid not in self._mapping:
            raise ValueError(f"Channel {cid} not configured")
        return self._mapping[cid]


class _RecordingAdapter:
    """记录 build_payload 收到的 full_context, 永远返回成功."""
    def __init__(self):
        self.last_context = None

    def build_payload(self, full_context, config):
        self.last_context = full_context
        return full_context

    def send(self, payload, config):
        return {"status_code": 200, "body": {}, "duration_ms": 1}

    def check_response(self, result, config):
        return True


class _FakeConn:
    def __init__(self, config):
        self.id = 1
        self.name = "test-conn"
        self.adapter_type = "rest"
        self.config = config
        self.push_events = ["cycle_end"]
        self.bound_channels = None
        self.retry_count = 0
        self.retry_interval_sec = 1
        self.last_sync_at = None


class _FakeDB:
    def add(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


# ==================== _capture_snapshot_base64 ====================
def test_capture_snapshot_returns_base64(monkeypatch):
    jpeg = b"\xff\xd8\xff\xe0fakejpeg"
    monkeypatch.setattr(cm_mod, "get_channel_manager",
                        lambda: _FakeChannelManager({0: _FakeMgr(jpeg)}))
    out = MESGateway._capture_snapshot_base64(0, {})
    assert out == base64.b64encode(jpeg).decode("ascii")


def test_capture_snapshot_channel_none_falls_back_to_0(monkeypatch):
    jpeg = b"frame-0"
    monkeypatch.setattr(cm_mod, "get_channel_manager",
                        lambda: _FakeChannelManager({0: _FakeMgr(jpeg)}))
    assert MESGateway._capture_snapshot_base64(None, {}) == base64.b64encode(jpeg).decode("ascii")


def test_capture_snapshot_unconfigured_channel_returns_none(monkeypatch):
    monkeypatch.setattr(cm_mod, "get_channel_manager",
                        lambda: _FakeChannelManager({}))
    assert MESGateway._capture_snapshot_base64(3, {}) is None


def test_capture_snapshot_no_frame_returns_none(monkeypatch):
    monkeypatch.setattr(cm_mod, "get_channel_manager",
                        lambda: _FakeChannelManager({0: _FakeMgr(None)}))
    assert MESGateway._capture_snapshot_base64(0, {}) is None


def test_capture_snapshot_over_max_bytes_returns_none(monkeypatch):
    jpeg = b"x" * 5000
    monkeypatch.setattr(cm_mod, "get_channel_manager",
                        lambda: _FakeChannelManager({0: _FakeMgr(jpeg)}))
    assert MESGateway._capture_snapshot_base64(0, {"snapshot_max_bytes": 1000}) is None
    # 不超限正常返回
    assert MESGateway._capture_snapshot_base64(0, {"snapshot_max_bytes": 10000}) is not None


# ==================== 注入行为 ====================
def _send(gw, config, monkeypatch, snapshot_b64="QUJD"):
    adapter = _RecordingAdapter()
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)
    monkeypatch.setattr(MESGateway, "_capture_snapshot_base64",
                        staticmethod(lambda cid, cfg: snapshot_b64))
    conn = _FakeConn(config)
    gw._send_to_connection(_FakeDB(), conn, "cycle_end", {"cycle": {"result": "NG"}}, 0)
    return adapter.last_context


def test_attach_snapshot_off_no_snapshot_key(monkeypatch):
    ctx = _send(MESGateway(), {}, monkeypatch)
    assert "snapshot" not in ctx


def test_attach_snapshot_on_injects_base64(monkeypatch):
    ctx = _send(MESGateway(), {"attach_snapshot": True}, monkeypatch)
    assert ctx["snapshot"]["image_base64"] == "QUJD"
    assert "image_data_uri" not in ctx["snapshot"]


def test_attach_snapshot_data_uri_flag(monkeypatch):
    ctx = _send(MESGateway(), {"attach_snapshot": True, "snapshot_data_uri": True}, monkeypatch)
    assert ctx["snapshot"]["image_data_uri"] == "data:image/jpeg;base64,QUJD"


def test_attach_snapshot_capture_fail_does_not_block(monkeypatch):
    # 抓帧返回 None → 推送照常进行, 仅无 snapshot 字段
    ctx = _send(MESGateway(), {"attach_snapshot": True}, monkeypatch, snapshot_b64=None)
    assert ctx is not None
    assert "snapshot" not in ctx


def test_attach_snapshot_skipped_by_push_on_result(monkeypatch):
    # push_on_result=["OK"] 而结果 NG → 整条推送被过滤, 不应抓帧/注入
    called = {"snap": False}

    def _fake_cap(cid, cfg):
        called["snap"] = True
        return "QUJD"

    adapter = _RecordingAdapter()
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)
    monkeypatch.setattr(MESGateway, "_capture_snapshot_base64", staticmethod(_fake_cap))
    conn = _FakeConn({"attach_snapshot": True, "push_on_result": ["OK"]})
    MESGateway()._send_to_connection(_FakeDB(), conn, "cycle_end", {"cycle": {"result": "NG"}, "overall_result": "NG"}, 0)
    assert called["snap"] is False
    assert adapter.last_context is None
