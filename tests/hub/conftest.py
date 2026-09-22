"""Fleet Hub M1 测试 fixtures。

拓扑: 枢纽 TestClient + 主程序 app 当"边缘机"(httpx.ASGITransport 进程内直连,
无网络端口)。根 conftest 的 client fixture 即边缘机的 TestClient。
"""
import pytest


@pytest.fixture
def edge_ready(client):
    """把主程序调成"可纳管的边缘机": 开 hub_access + 写有效 License 缓存。"""
    client.put("/api/v1/hub/config", json={"enabled": True})
    client.put("/api/v1/system/license-cache", json={
        "customer": "hub-test", "machine_id": "HUB-TEST-MACHINE",
        "is_perpetual": True,
    })
    yield
    client.put("/api/v1/hub/config", json={"enabled": False})


@pytest.fixture
def hub_client(tmp_path, monkeypatch, app):
    """独立数据目录的枢纽 TestClient; EdgeClient 流量经 ASGITransport 进主程序 app。"""
    from httpx import ASGITransport

    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "hub_data"))
    monkeypatch.setenv("HUB_ENABLE_POLLER", "0")   # 测试直接驱动 poll, 不起循环

    from hub.backend import edge_client
    edge_client.set_transport_factory(lambda base_url: ASGITransport(app=app))

    from fastapi.testclient import TestClient

    from hub.backend.main import create_app
    hub_app = create_app()
    with TestClient(hub_app) as c:
        yield c
    edge_client.set_transport_factory(None)


@pytest.fixture
def admin_headers(hub_client):
    """admin 登录拿 token"""
    r = hub_client.post("/api/v1/auth/login",
                        json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed_user(username: str, password: str, role: str, display: str):
    """直插 hub DB 造用户 (M3/M4 无用户管理端点)"""
    from hub.backend import db as hubdb
    from hub.backend.models import HubUser
    from hub.backend.security import hash_password

    s = hubdb.SessionLocal()
    try:
        if not s.query(HubUser).filter(HubUser.username == username).first():
            s.add(HubUser(username=username, password_hash=hash_password(password),
                          display_name=display, role=role, active=True))
            s.commit()
    finally:
        s.close()


@pytest.fixture
def engineer_headers(hub_client):
    """第二个用户 (engineer, 有 ops.execute 无 lock.steal)"""
    _seed_user("eng1", "eng1pwd", "engineer", "工程师1")
    r = hub_client.post("/api/v1/auth/login",
                        json={"username": "eng1", "password": "eng1pwd"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def operator_headers(hub_client):
    """只读用户 (operator, 仅 wall.view)"""
    _seed_user("op1", "op1pwd", "operator", "操作员1")
    r = hub_client.post("/api/v1/auth/login",
                        json={"username": "op1", "password": "op1pwd"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def enrolled_node(hub_client, admin_headers, edge_ready):
    """已纳管的边缘节点 (走完整 enroll 链路)"""
    r = hub_client.post("/api/v1/nodes", headers=admin_headers, json={
        "name": "测试边缘机",
        "base_url": "http://edge-test:8001",
        "api_key": "tk_test_dummy_key",
    })
    assert r.status_code == 200, r.text
    return r.json()
