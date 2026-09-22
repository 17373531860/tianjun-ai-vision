"""Fleet Hub M3 测试: 操作锁协议 + 操作网关写闭环。

拓扑同 M1/M2: 枢纽 TestClient + 主程序 app 当边缘机 (ASGITransport 进程内直连)。

覆盖:
  1. 锁协议: 无锁自动获取 / 本人续期 / 他人 409 / 过期可拿 / 夺锁权限与审计 / 释放
  2. 操作网关: 权限守门 (operator 403) / 白名单 (未登记 action 409, 未知 400)
  3. 写闭环: stop 幂等成功 → desired 落库 + 审计 ok; start 无源被边缘 409 →
     透传 + desired 保留 + 审计 failed; activate_project 全链路真激活
  4. 锁与网关联动: A 操作拿锁后 B 操作 409; B 夺锁后 B 可操作
  5. GET /nodes/{id}/projects 转发
"""
from datetime import datetime, timedelta

import pytest


API = "/api/v1"


def _station_ref(enrolled_node):
    node_id = enrolled_node["id"]
    ch = enrolled_node["stations"][0]["channel_id"] \
        if enrolled_node.get("stations") else 0
    return node_id, ch


# ============================================================
# 1. 锁协议
# ============================================================

def test_lock_acquire_and_status(hub_client, admin_headers, enrolled_node):
    node_id, ch = _station_ref(enrolled_node)
    # 初始无锁
    r = hub_client.get(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                       headers=admin_headers)
    assert r.status_code == 200 and r.json()["locked"] is False
    # 获取
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                        headers=admin_headers, json={})
    body = r.json()
    assert body["locked"] and body["holder"] == "admin" and body["mine"]
    assert 0 < body["expires_in_s"] <= 120


def test_lock_conflict_and_steal(hub_client, admin_headers, engineer_headers,
                                 enrolled_node):
    node_id, ch = _station_ref(enrolled_node)
    # engineer 拿锁
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                        headers=engineer_headers, json={})
    assert r.json()["holder"] == "eng1"
    # admin 普通获取 → 409
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                        headers=admin_headers, json={})
    assert r.status_code == 409
    assert "eng1" in r.json()["detail"]
    # engineer 无 lock.steal → 传 steal 一律 403 (即使锁是自己的, 权限先于锁归属)
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                        headers=engineer_headers, json={"steal": True})
    assert r.status_code == 403
    # admin 夺锁 (有 lock.steal)
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                        headers=admin_headers, json={"steal": True})
    assert r.status_code == 200 and r.json()["holder"] == "admin"
    # 夺锁写了审计
    r = hub_client.get(f"{API}/audit", headers=admin_headers,
                       params={"action": "lock.steal"})
    logs = r.json()["items"] if isinstance(r.json(), dict) else r.json()
    assert any(l["action"] == "lock.steal" and l["old_value"] == "eng1"
               for l in logs)


def test_lock_steal_denied_for_engineer(hub_client, admin_headers,
                                        engineer_headers, enrolled_node):
    node_id, ch = _station_ref(enrolled_node)
    hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                    headers=admin_headers, json={})
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                        headers=engineer_headers, json={"steal": True})
    assert r.status_code == 403


def test_lock_expired_is_free(hub_client, admin_headers, engineer_headers,
                              enrolled_node):
    node_id, ch = _station_ref(enrolled_node)
    hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                    headers=engineer_headers, json={})
    # 手动把锁改成已过期
    from hub.backend import db as hubdb
    from hub.backend.models import HubStationLock
    s = hubdb.SessionLocal()
    try:
        lock = s.query(HubStationLock).filter_by(
            node_id=node_id, channel_id=ch).first()
        lock.expires_at = datetime.now() - timedelta(seconds=1)
        s.commit()
    finally:
        s.close()
    # 状态按无锁报告
    r = hub_client.get(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                       headers=admin_headers)
    assert r.json()["locked"] is False
    # 他人可直接获取 (无需夺锁)
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                        headers=admin_headers, json={})
    assert r.status_code == 200 and r.json()["holder"] == "admin"


def test_lock_release_only_own(hub_client, admin_headers, engineer_headers,
                               enrolled_node):
    node_id, ch = _station_ref(enrolled_node)
    hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                    headers=engineer_headers, json={})
    # admin 释放他人锁: 无效果 (语义分离, 夺锁才是仲裁通道)
    hub_client.delete(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                      headers=admin_headers)
    r = hub_client.get(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                       headers=admin_headers)
    assert r.json()["holder"] == "eng1"
    # 本人释放
    hub_client.delete(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                      headers=engineer_headers)
    r = hub_client.get(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                       headers=admin_headers)
    assert r.json()["locked"] is False


# ============================================================
# 2. 操作网关: 权限与白名单
# ============================================================

def test_ops_denied_for_operator(hub_client, operator_headers, enrolled_node):
    node_id, ch = _station_ref(enrolled_node)
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/ops",
                        headers=operator_headers,
                        json={"action": "stop_detection"})
    assert r.status_code == 403


def test_ops_unknown_action_400(hub_client, admin_headers, enrolled_node):
    node_id, ch = _station_ref(enrolled_node)
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/ops",
                        headers=admin_headers, json={"action": "reboot"})
    assert r.status_code == 400


def test_ops_unregistered_action_409(hub_client, admin_headers, enrolled_node):
    """能力档案白名单: 把档案里的 actions 清空后, 合法 action 也被拒"""
    from hub.backend import db as hubdb
    from hub.backend.models import HubNode
    node_id, ch = _station_ref(enrolled_node)
    s = hubdb.SessionLocal()
    try:
        node = s.query(HubNode).get(node_id)
        profile = dict(node.profile or {})
        stations = [dict(st) for st in profile.get("stations") or []]
        for st in stations:
            st["actions"] = []
        profile["stations"] = stations
        node.profile = profile
        s.commit()
    finally:
        s.close()
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/ops",
                        headers=admin_headers,
                        json={"action": "stop_detection"})
    assert r.status_code == 409
    assert "未登记" in r.json()["detail"]


# ============================================================
# 3. 写闭环 (经 ASGITransport 打到真边缘 app)
# ============================================================

def _audit_items(resp):
    body = resp.json()
    return body["items"] if isinstance(body, dict) else body


def test_ops_stop_success_writes_desired_and_audit(
        hub_client, admin_headers, enrolled_node):
    node_id, ch = _station_ref(enrolled_node)
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/ops",
                        headers=admin_headers,
                        json={"action": "stop_detection"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["desired"] == {"detecting": False}

    # desired 落库
    from hub.backend import db as hubdb
    from hub.backend.models import HubTwin
    s = hubdb.SessionLocal()
    try:
        twin = s.query(HubTwin).filter_by(node_id=node_id, channel_id=ch).first()
        assert twin and twin.desired.get("detecting") is False
    finally:
        s.close()

    # 审计 ok
    r = hub_client.get(f"{API}/audit", headers=admin_headers,
                       params={"action": "ops.stop_detection"})
    assert any(l["result"] == "ok" for l in _audit_items(r))

    # 操作自动拿了锁
    r = hub_client.get(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                       headers=admin_headers)
    assert r.json()["holder"] == "admin"


def test_ops_start_edge_409_passthrough(hub_client, admin_headers,
                                        enrolled_node, app):
    """边缘无视频源 → 边缘 409 → 枢纽透传 detail + 审计 failed + desired 保留"""
    from backend.api.channel_manager import channel_manager
    node_id, ch = _station_ref(enrolled_node)
    mgr = channel_manager.channels.get(ch)
    olds = (getattr(mgr, "is_detecting", False),
            getattr(mgr, "is_running", False),
            getattr(mgr, "source_type", None))
    mgr.is_detecting = False
    mgr.is_running = False
    mgr.source_type = None
    try:
        r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/ops",
                            headers=admin_headers,
                            json={"action": "start_detection"})
        assert r.status_code == 409
        assert "视频源" in r.json()["detail"]
    finally:
        mgr.is_detecting, mgr.is_running, mgr.source_type = olds

    r = hub_client.get(f"{API}/audit", headers=admin_headers,
                       params={"action": "ops.start_detection"})
    assert any(l["result"] == "failed" for l in _audit_items(r))

    from hub.backend import db as hubdb
    from hub.backend.models import HubTwin
    s = hubdb.SessionLocal()
    try:
        twin = s.query(HubTwin).filter_by(node_id=node_id, channel_id=ch).first()
        assert twin and twin.desired.get("detecting") is True  # 意图未达成但保留
    finally:
        s.close()


def test_ops_activate_project_full_chain(hub_client, admin_headers,
                                         enrolled_node, client):
    """全链路: 枢纽下拉列表 → 执行切项目 → 边缘真激活"""
    import uuid as _uuid
    node_id, ch = _station_ref(enrolled_node)

    name = f"hub-m3-{_uuid.uuid4().hex[:6]}"
    r = client.post("/api/v1/projects", json={"name": name,
                                              "task_type": "detection"})
    pid = r.json()["id"]
    try:
        # 下拉列表可见
        r = hub_client.get(f"{API}/nodes/{node_id}/projects",
                           headers=admin_headers)
        assert r.status_code == 200
        assert any(p["id"] == pid for p in r.json()["items"])

        # 缺 project_id → 400
        r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/ops",
                            headers=admin_headers,
                            json={"action": "activate_project"})
        assert r.status_code == 400

        # 执行切项目
        r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/ops",
                            headers=admin_headers,
                            json={"action": "activate_project",
                                  "project_id": pid})
        assert r.status_code == 200, r.text

        # 边缘真的激活了
        items = client.get("/api/v1/hub/projects").json()["items"]
        active = [p for p in items if p["is_active"]]
        assert active and active[0]["id"] == pid
    finally:
        client.delete(f"/api/v1/projects/{pid}")


def test_ops_ack_alarm_no_desired(hub_client, admin_headers, enrolled_node):
    """M4 消警: 一次性 RPC 动作 —— 执行成功 + 审计, 但绝不写 desired (RFC §10.3-5)"""
    node_id, ch = _station_ref(enrolled_node)
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/ops",
                        headers=admin_headers, json={"action": "ack_alarm"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["desired"] == {}

    from hub.backend import db as hubdb
    from hub.backend.models import HubTwin
    s = hubdb.SessionLocal()
    try:
        twin = s.query(HubTwin).filter_by(node_id=node_id, channel_id=ch).first()
        # 孪生可能因轮询存在, 但 desired 里绝不能有 ack 痕迹
        if twin:
            assert "ack_alarm" not in (twin.desired or {})
    finally:
        s.close()

    r = hub_client.get(f"{API}/audit", headers=admin_headers,
                       params={"action": "ops.ack_alarm"})
    assert any(l["result"] == "ok" for l in _audit_items(r))


# ============================================================
# 4. 锁与网关联动
# ============================================================

def test_ops_blocked_by_others_lock(hub_client, admin_headers,
                                    engineer_headers, enrolled_node):
    node_id, ch = _station_ref(enrolled_node)
    # engineer 先拿锁
    hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                    headers=engineer_headers, json={})
    # admin 操作 → 409 (锁裁决先于转发, 边缘不被打扰)
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/ops",
                        headers=admin_headers,
                        json={"action": "stop_detection"})
    assert r.status_code == 409
    assert "eng1" in r.json()["detail"]
    # admin 夺锁后可操作
    hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/lock",
                    headers=admin_headers, json={"steal": True})
    r = hub_client.post(f"{API}/nodes/{node_id}/stations/{ch}/ops",
                        headers=admin_headers,
                        json={"action": "stop_detection"})
    assert r.status_code == 200
