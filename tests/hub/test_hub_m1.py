"""Fleet Hub M1 骨架测试: auth / 纳管门槛 / poller / 孪生 / 审计。

多数用例走 hub TestClient → hub API → EdgeClient(ASGITransport) → 主程序 app
的完整链路 (进程内双服务集成, 无网络端口)。
"""
import pytest


# ============================================================
# 认证
# ============================================================

def test_login_ok_and_me(hub_client):
    r = hub_client.post("/api/v1/auth/login",
                        json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token.startswith("ht_")

    me = hub_client.get("/api/v1/auth/me",
                        headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "admin"
    assert "*" in me.json()["permissions"]


def test_login_wrong_password_401_and_audited(hub_client, admin_headers):
    r = hub_client.post("/api/v1/auth/login",
                        json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    # 失败登录必须留审计
    audit = hub_client.get("/api/v1/audit?action=auth.login",
                           headers=admin_headers).json()
    assert any(row["result"] == "denied" for row in audit["items"])


def test_endpoints_require_auth(hub_client):
    assert hub_client.get("/api/v1/nodes").status_code == 401
    assert hub_client.get("/api/v1/audit").status_code == 401


# ============================================================
# 纳管门槛
# ============================================================

def test_enroll_happy_path(enrolled_node):
    n = enrolled_node
    assert n["license_state"] == "valid"
    assert n["api_contract"] == 1
    assert n["node_uid"].startswith("edge-")
    assert n["machine_id"] == "HUB-TEST-MACHINE"
    assert n["station_count"] >= 1
    assert n["profile_hash"].startswith("sha256:")


def test_enroll_rejected_when_edge_hub_disabled(hub_client, admin_headers, client):
    """边缘没开 hub_access → handshake 404 → 纳管 400 且提示开启"""
    client.put("/api/v1/hub/config", json={"enabled": False})
    r = hub_client.post("/api/v1/nodes", headers=admin_headers, json={
        "name": "关着的边缘机", "base_url": "http://edge-off:8001",
        "api_key": "tk_x",
    })
    assert r.status_code == 400
    assert "未开启枢纽接入" in r.json()["detail"]


def test_enroll_rejected_when_license_expired(hub_client, admin_headers, client):
    """License 过期 → 403 拒绝纳管 (RFC 15 §3.4)"""
    client.put("/api/v1/hub/config", json={"enabled": True})
    client.put("/api/v1/system/license-cache", json={
        "machine_id": "M-EXP", "is_perpetual": False, "days_remaining": -3,
    })
    try:
        r = hub_client.post("/api/v1/nodes", headers=admin_headers, json={
            "name": "过期边缘机", "base_url": "http://edge-expired:8001",
            "api_key": "tk_x",
        })
        assert r.status_code == 403
        assert "License" in r.json()["detail"]
    finally:
        client.put("/api/v1/system/license-cache",
                   json={"machine_id": "M-EXP", "is_perpetual": True})
        client.put("/api/v1/hub/config", json={"enabled": False})


def test_enroll_duplicate_base_url_409(hub_client, admin_headers,
                                       enrolled_node):
    r = hub_client.post("/api/v1/nodes", headers=admin_headers, json={
        "name": "重复", "base_url": enrolled_node["base_url"],
        "api_key": "tk_y",
    })
    assert r.status_code == 409


def test_enroll_requires_node_manage_perm(hub_client, admin_headers,
                                          edge_ready):
    """operator 角色不能纳管节点"""
    from hub.backend import db as hubdb
    from hub.backend.models import HubUser
    from hub.backend.security import hash_password
    db = hubdb.SessionLocal()
    db.add(HubUser(username="op1", password_hash=hash_password("op1pass"),
                   role="operator", active=True))
    db.commit()
    db.close()

    tok = hub_client.post("/api/v1/auth/login", json={
        "username": "op1", "password": "op1pass"}).json()["token"]
    r = hub_client.post("/api/v1/nodes",
                        headers={"Authorization": f"Bearer {tok}"},
                        json={"name": "x", "base_url": "http://e:1",
                              "api_key": "k"})
    assert r.status_code == 403


# ============================================================
# 轮询 + 孪生
# ============================================================

def test_poll_now_updates_runtime_and_twins(hub_client, admin_headers,
                                            enrolled_node):
    nid = enrolled_node["id"]
    r = hub_client.post(f"/api/v1/nodes/{nid}/poll", headers=admin_headers)
    assert r.status_code == 200
    rt = r.json()
    assert rt["status"] == "online"
    assert rt["summary"]["stations"]

    status = hub_client.get(f"/api/v1/nodes/{nid}/status",
                            headers=admin_headers).json()
    assert status["node"]["status"] == "online"
    twins = status["twins"]
    assert twins, "poll 后应有孪生行"
    assert twins[0]["reported"]["detecting"] is False
    assert twins[0]["version"] >= 1


def test_twin_version_bumps_on_state_change(hub_client, admin_headers,
                                            enrolled_node):
    """边缘检测态翻转 → 再 poll → reported 变化且 version++ (无变化不涨)"""
    from backend.api.channel_manager import channel_manager
    nid = enrolled_node["id"]

    hub_client.post(f"/api/v1/nodes/{nid}/poll", headers=admin_headers)
    t0 = hub_client.get(f"/api/v1/nodes/{nid}/status",
                        headers=admin_headers).json()["twins"][0]

    # 无变化再 poll: version 不涨
    hub_client.post(f"/api/v1/nodes/{nid}/poll", headers=admin_headers)
    t1 = hub_client.get(f"/api/v1/nodes/{nid}/status",
                        headers=admin_headers).json()["twins"][0]
    assert t1["version"] == t0["version"]

    ch_id = sorted(channel_manager.channels.keys())[0]
    mgr = channel_manager.channels[ch_id]
    old = mgr.is_detecting
    mgr.is_detecting = True
    try:
        hub_client.post(f"/api/v1/nodes/{nid}/poll", headers=admin_headers)
        t2 = [t for t in hub_client.get(
            f"/api/v1/nodes/{nid}/status",
            headers=admin_headers).json()["twins"]
            if t["channel_id"] == ch_id][0]
        assert t2["reported"]["detecting"] is True
        assert t2["version"] == t0["version"] + 1
    finally:
        mgr.is_detecting = old


def test_offline_after_consecutive_failures(hub_client, admin_headers,
                                            enrolled_node):
    """边缘断连 → 连续 3 次失败判离线; 恢复 → 一次成功即在线"""
    import httpx

    from hub.backend import edge_client

    nid = enrolled_node["id"]

    def _broken(request):
        raise httpx.ConnectError("simulated network down")

    old_factory = edge_client._transport_factory
    edge_client.set_transport_factory(
        lambda base_url: httpx.MockTransport(_broken))
    try:
        for i in range(3):
            rt = hub_client.post(f"/api/v1/nodes/{nid}/poll",
                                 headers=admin_headers).json()
        assert rt["status"] == "offline"
        assert rt["consecutive_failures"] == 3
    finally:
        edge_client.set_transport_factory(old_factory)

    rt = hub_client.post(f"/api/v1/nodes/{nid}/poll",
                         headers=admin_headers).json()
    assert rt["status"] == "online"
    assert rt["consecutive_failures"] == 0


# ============================================================
# 审计 + 移除
# ============================================================

def test_enroll_and_remove_audited(hub_client, admin_headers, enrolled_node):
    nid = enrolled_node["id"]
    r = hub_client.delete(f"/api/v1/nodes/{nid}", headers=admin_headers)
    assert r.status_code == 200

    audit = hub_client.get("/api/v1/audit", headers=admin_headers).json()
    actions = [row["action"] for row in audit["items"]]
    assert "node.enroll" in actions
    assert "node.remove" in actions
    # 审计行带操作者与节点
    enroll_row = next(row for row in audit["items"]
                      if row["action"] == "node.enroll" and row["result"] == "ok")
    assert enroll_row["username"] == "admin"
    assert enroll_row["node_id"] == nid

    # 节点已删干净
    nodes = hub_client.get("/api/v1/nodes", headers=admin_headers).json()
    assert all(n["id"] != nid for n in nodes)


def test_poller_loop_real(tmp_path, monkeypatch, app, client):
    """真实轮询循环冒烟: 开 HUB_ENABLE_POLLER, enroll 后不手动 poll,
    后台循环自己把节点拉到 online (lifespan 事件循环里跑真 asyncio 任务)。"""
    import time

    from httpx import ASGITransport

    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "hub_loop"))
    monkeypatch.setenv("HUB_ENABLE_POLLER", "1")
    monkeypatch.setenv("HUB_HEALTH_INTERVAL", "0.2")

    client.put("/api/v1/hub/config", json={"enabled": True})
    client.put("/api/v1/system/license-cache",
               json={"machine_id": "LOOP-M", "is_perpetual": True})

    from hub.backend import edge_client
    edge_client.set_transport_factory(lambda b: ASGITransport(app=app))
    try:
        # 环境变量在 config 模块 import 期固化, 循环间隔直接改 poller 模块常量
        from hub.backend import poller as poller_mod
        monkeypatch.setattr(poller_mod, "HEALTH_INTERVAL_S", 0.2)

        from fastapi.testclient import TestClient

        from hub.backend.main import create_app
        with TestClient(create_app()) as hc:
            tok = hc.post("/api/v1/auth/login", json={
                "username": "admin", "password": "admin123"}).json()["token"]
            hd = {"Authorization": f"Bearer {tok}"}
            r = hc.post("/api/v1/nodes", headers=hd, json={
                "name": "循环边缘机", "base_url": "http://edge-loop:8001",
                "api_key": "tk_loop"})
            assert r.status_code == 200, r.text
            nid = r.json()["id"]

            deadline = time.time() + 5
            status = "unknown"
            while time.time() < deadline:
                status = next(n["status"] for n in hc.get(
                    "/api/v1/nodes", headers=hd).json() if n["id"] == nid)
                if status == "online":
                    break
                time.sleep(0.2)
            assert status == "online", f"后台循环 5s 内未拉到 online: {status}"
    finally:
        edge_client.set_transport_factory(None)
        client.put("/api/v1/hub/config", json={"enabled": False})


def test_audit_requires_perm(hub_client, enrolled_node):
    """engineer 无 audit.view → 403"""
    from hub.backend import db as hubdb
    from hub.backend.models import HubUser
    from hub.backend.security import hash_password
    db = hubdb.SessionLocal()
    db.add(HubUser(username="eng1", password_hash=hash_password("eng1pass"),
                   role="engineer", active=True))
    db.commit()
    db.close()

    tok = hub_client.post("/api/v1/auth/login", json={
        "username": "eng1", "password": "eng1pass"}).json()["token"]
    r = hub_client.get("/api/v1/audit",
                       headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
