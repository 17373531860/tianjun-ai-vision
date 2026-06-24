"""M2 入站接收器 HTTP 端到端测试 (TestClient, 端点契约层)。

覆盖外部系统真实调用路径: POST /api/v1/mes/inbound/task + 配置 CRUD + 日志。
鉴权默认关 → config 端点直通。
"""
import pytest

from backend.db.database import SessionLocal
from backend.services.mes_inbound import get_mes_inbound


@pytest.fixture
def inbound_enabled():
    """启用入站对接 (默认字段映射 + 必填 task_no/product_code), 测试结束复位为关。"""
    svc = get_mes_inbound()
    db = SessionLocal()
    try:
        # 端点契约层测试: 关掉"开工切项目"(切项目由 M3 测试覆盖), 专测接收/映射/响应/日志
        svc.save_config(db, {"enabled": True, "echo_fields": ["task_no"],
                             "switch_project_on_task": False})
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        svc.save_config(db, {"enabled": False})
        db.commit()
    finally:
        db.close()


def test_config_put_get_roundtrip(client):
    r = client.put("/api/v1/mes/inbound/config", json={"enabled": True, "max_body_bytes": 2048})
    assert r.status_code == 200
    assert r.json()["enabled"] is True
    g = client.get("/api/v1/mes/inbound/config")
    assert g.status_code == 200
    assert g.json()["max_body_bytes"] == 2048
    # 复位
    client.put("/api/v1/mes/inbound/config", json={"enabled": False})


def test_receive_task_success(client, inbound_enabled):
    r = client.post("/api/v1/mes/inbound/task",
                    json={"TaskNo": "API-T1", "ProductCode": "P1", "Operator": "李四"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["task_no"] == "API-T1"   # echo_fields 回显


def test_receive_task_missing_field(client, inbound_enabled):
    r = client.post("/api/v1/mes/inbound/task", json={"TaskNo": "API-T2"})  # 缺 ProductCode
    assert r.status_code == 200
    assert r.json()["code"] == 40004


def test_receive_task_malformed_json(client, inbound_enabled):
    r = client.post("/api/v1/mes/inbound/task",
                    content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["code"] == 40005  # bad_request


def test_receive_task_when_disabled(client):
    # 确保关闭
    client.put("/api/v1/mes/inbound/config", json={"enabled": False})
    r = client.post("/api/v1/mes/inbound/task", json={"TaskNo": "X", "ProductCode": "Y"})
    assert r.status_code == 200
    assert r.json()["code"] == 40006  # disabled


def test_receive_task_body_too_large(client):
    # 设极小上限触发 413
    client.put("/api/v1/mes/inbound/config", json={"enabled": True, "max_body_bytes": 10})
    try:
        r = client.post("/api/v1/mes/inbound/task",
                        json={"TaskNo": "A-very-long-task-number", "ProductCode": "PPPPP"})
        assert r.status_code == 413
    finally:
        client.put("/api/v1/mes/inbound/config", json={"enabled": False})


def test_inbound_logs_recorded(client, inbound_enabled):
    client.post("/api/v1/mes/inbound/task",
                json={"TaskNo": "LOG-T1", "ProductCode": "P1"})
    r = client.get("/api/v1/mes/inbound/logs?limit=10")
    assert r.status_code == 200
    logs = r.json()
    assert any(l["event_type"] == "task_start" for l in logs)
