"""B3 前端各面板轮询间隔可配: /system/polling 读写, 白名单 + 下限保护 + 默认回落。"""
import pytest

from backend.db.database import SessionLocal
from backend.models.models import SystemConfig
from backend.api.system_display import POLLING_DEFAULTS, LOG_LIMIT_DEFAULTS


@pytest.fixture
def clean_polling():
    s = SessionLocal()
    for row in s.query(SystemConfig).filter(SystemConfig.key.like("polling.%")).all():
        s.delete(row)
    s.commit()
    s.close()
    yield
    s = SessionLocal()
    for row in s.query(SystemConfig).filter(SystemConfig.key.like("polling.%")).all():
        s.delete(row)
    s.commit()
    s.close()


def test_get_defaults_when_unset(client, clean_polling):
    r = client.get("/api/v1/system/polling")
    assert r.status_code == 200
    assert r.json() == POLLING_DEFAULTS


def test_put_and_get_roundtrip(client, clean_polling):
    r = client.put("/api/v1/system/polling", json={"cluster_boxes": 3000, "order_list": 20000})
    assert r.status_code == 200
    body = r.json()
    assert set(body["updated"]) == {"cluster_boxes", "order_list"}
    assert body["config"]["cluster_boxes"] == 3000
    assert body["config"]["order_list"] == 20000
    # 重新 GET 确认落库
    g = client.get("/api/v1/system/polling").json()
    assert g["cluster_boxes"] == 3000
    assert g["order_list"] == 20000
    # 未动的保持默认
    assert g["gateway_health"] == POLLING_DEFAULTS["gateway_health"]


def test_put_rejects_unknown_key(client, clean_polling):
    r = client.put("/api/v1/system/polling", json={"not_a_panel": 1000, "scanner_status": 2000})
    assert r.status_code == 200
    assert r.json()["updated"] == ["scanner_status"]


def test_put_rejects_below_min(client, clean_polling):
    r = client.put("/api/v1/system/polling", json={"cluster_boxes": 100})
    assert r.status_code == 200
    assert r.json()["updated"] == []
    # 仍回落默认
    assert client.get("/api/v1/system/polling").json()["cluster_boxes"] == POLLING_DEFAULTS["cluster_boxes"]


def test_put_ignores_non_numeric(client, clean_polling):
    r = client.put("/api/v1/system/polling", json={"cluster_boxes": "abc", "wmax_status": 8000})
    assert r.status_code == 200
    assert r.json()["updated"] == ["wmax_status"]


# ==================== C5 日志条数 ====================
@pytest.fixture
def clean_loglimits():
    s = SessionLocal()
    for row in s.query(SystemConfig).filter(SystemConfig.key.like("loglimit.%")).all():
        s.delete(row)
    s.commit()
    s.close()
    yield
    s = SessionLocal()
    for row in s.query(SystemConfig).filter(SystemConfig.key.like("loglimit.%")).all():
        s.delete(row)
    s.commit()
    s.close()


def test_loglimit_defaults_when_unset(client, clean_loglimits):
    r = client.get("/api/v1/system/log-limits")
    assert r.status_code == 200
    assert r.json() == LOG_LIMIT_DEFAULTS


def test_loglimit_roundtrip(client, clean_loglimits):
    r = client.put("/api/v1/system/log-limits", json={"scanner": 100, "cluster": 50})
    assert r.status_code == 200
    assert set(r.json()["updated"]) == {"scanner", "cluster"}
    g = client.get("/api/v1/system/log-limits").json()
    assert g["scanner"] == 100 and g["cluster"] == 50
    assert g["inbound"] == LOG_LIMIT_DEFAULTS["inbound"]


def test_loglimit_rejects_unknown_and_out_of_range(client, clean_loglimits):
    r = client.put("/api/v1/system/log-limits",
                   json={"nope": 10, "scanner": 0, "gateway": 999, "inbound": 80})
    assert r.status_code == 200
    assert r.json()["updated"] == ["inbound"]   # 其余: 未知键/越界全忽略
