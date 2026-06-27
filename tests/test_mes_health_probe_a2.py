"""A2 出站主动健康探测: 参数解析 / 探测判定 / 状态缓存。"""
import pytest

from backend.services import mes_health_probe as hp


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _clear_status():
    with hp._STATUS_LOCK:
        hp._STATUS.clear()
    yield
    with hp._STATUS_LOCK:
        hp._STATUS.clear()


def test_probe_params_defaults():
    p = hp._probe_params({})
    assert p["interval"] == 60 and p["timeout"] == 5.0
    assert p["method"] == "GET" and p["expect"] is None


def test_probe_params_overrides_and_clamps():
    p = hp._probe_params({
        "health_probe_interval_sec": 2,     # < 5 → 夹到 5
        "health_probe_timeout_sec": 8,
        "health_probe_url": "http://x/health",
        "health_probe_method": "post",
        "health_probe_expect_status": "204",
    })
    assert p["interval"] == 5
    assert p["timeout"] == 8.0
    assert p["url"] == "http://x/health"
    assert p["method"] == "POST"
    assert p["expect"] == 204


def test_probe_url_falls_back_to_push_url():
    p = hp._probe_params({"url": "http://push/endpoint"})
    assert p["url"] == "http://push/endpoint"


def test_probe_connection_ok(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "request", lambda *a, **k: _FakeResp(200))
    st = hp.probe_connection(1, "conn-A", {"health_probe_url": "http://x/h"})
    assert st["ok"] is True and st["status_code"] == 200
    assert hp.get_health_status()[1]["ok"] is True


def test_probe_connection_expect_mismatch(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "request", lambda *a, **k: _FakeResp(200))
    st = hp.probe_connection(2, "conn-B", {
        "health_probe_url": "http://x/h", "health_probe_expect_status": 204})
    assert st["ok"] is False
    assert "200" in st["error"]


def test_probe_connection_no_url():
    st = hp.probe_connection(3, "conn-C", {})
    assert st["ok"] is False and "探测地址" in st["error"]


def test_probe_connection_exception(monkeypatch):
    import requests

    def _boom(*a, **k):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(requests, "request", _boom)
    st = hp.probe_connection(4, "conn-D", {"health_probe_url": "http://x/h"})
    assert st["ok"] is False and "refused" in st["error"]


def test_get_health_status_strips_internal_ts(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "request", lambda *a, **k: _FakeResp(200))
    hp.probe_connection(5, "conn-E", {"health_probe_url": "http://x/h"})
    snap = hp.get_health_status()
    assert "_ts" not in snap[5]
    assert "checked_at" in snap[5]
