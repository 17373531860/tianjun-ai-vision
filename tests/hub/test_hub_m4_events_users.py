"""Fleet Hub M4 收尾测试: 事件通道 (P0-10) + 用户管理。

事件通道: fake edge 注入周期事件流 → poller 游标增量拉取 (只落 NG) →
枢纽 /events 聚合 + ack 工作流。用户管理: admin CRUD / 防呆 / 本人改密。
"""
import pytest

API = "/api/v1"


@pytest.fixture
def fake_node(hub_client, admin_headers):
    """单 fake edge 纳管 (transport 覆盖为 fake edge app)"""
    from httpx import ASGITransport

    from hub.backend import edge_client
    from tests.hub.fake_edge import create_fake_edge

    app = create_fake_edge(node_id="edge-ev-01")
    edge_client.set_transport_factory(lambda base_url: ASGITransport(app=app))
    r = hub_client.post(f"{API}/nodes", headers=admin_headers, json={
        "name": "事件测试机", "base_url": "http://edge-ev:8001",
        "api_key": "tk_ev"})
    assert r.status_code == 200, r.text
    return app, r.json()


def _poll_with_events(hub_client, admin_headers, node_id):
    """poll 一次并绕过事件拉取节流 (EVENT_PULL_INTERVAL_S)"""
    hub_client.app.state.poller.runtime(node_id).last_event_pull = 0
    r = hub_client.post(f"{API}/nodes/{node_id}/poll", headers=admin_headers)
    assert r.status_code == 200, r.text


def _cursor(hub_client, node_id):
    from hub.backend import db as hubdb
    from hub.backend.models import HubNode
    s = hubdb.SessionLocal()
    try:
        return s.query(HubNode).get(node_id).event_cursor
    finally:
        s.close()


def _hub_events(hub_client, node_id):
    from hub.backend import db as hubdb
    from hub.backend.models import HubEvent
    s = hubdb.SessionLocal()
    try:
        return s.query(HubEvent).filter(HubEvent.node_id == node_id) \
            .order_by(HubEvent.edge_event_id).all()
    finally:
        s.close()


# ============================================================
# 1. 事件通道: poller 拉取
# ============================================================

def test_first_pull_subscribes_from_now(hub_client, admin_headers, fake_node):
    """首拉不翻纳管前历史帐: 事件不落库, 游标直接落到当前最大 id"""
    app, node = fake_node
    app.state.edge["cycle_events"] = [
        {"id": i, "kind": "cycle", "channel_id": 0, "result": "NG",
         "event_name": "历史NG", "reason": "纳管前旧账", "ts": None}
        for i in (1, 2, 3)]
    _poll_with_events(hub_client, admin_headers, node["id"])
    assert _hub_events(hub_client, node["id"]) == []
    assert _cursor(hub_client, node["id"]) == 3


def test_incremental_pull_ng_only(hub_client, admin_headers, fake_node):
    """增量只落 NG; OK 周期推进游标但不落库"""
    app, node = fake_node
    _poll_with_events(hub_client, admin_headers, node["id"])  # 首拉落底 cursor=0

    app.state.edge["cycle_events"] += [
        {"id": 10, "kind": "cycle", "channel_id": 0, "result": "NG",
         "event_name": "NG事件", "reason": "缺步骤", "ts": "2026-09-19T10:00:00"},
        {"id": 11, "kind": "cycle", "channel_id": 0, "result": "OK",
         "event_name": None, "reason": None, "ts": "2026-09-19T10:00:05"},
    ]
    _poll_with_events(hub_client, admin_headers, node["id"])
    rows = _hub_events(hub_client, node["id"])
    assert [r.edge_event_id for r in rows] == [10]
    assert rows[0].result == "NG" and rows[0].reason == "缺步骤"
    assert _cursor(hub_client, node["id"]) == 11


def test_pull_dedup_on_cursor_replay(hub_client, admin_headers, fake_node):
    """游标回拨重放: 唯一约束 + 去重查询兜底, 不产生重复行"""
    app, node = fake_node
    _poll_with_events(hub_client, admin_headers, node["id"])
    app.state.edge["cycle_events"].append(
        {"id": 20, "kind": "cycle", "channel_id": 0, "result": "NG",
         "event_name": "NG", "reason": "x", "ts": None})
    _poll_with_events(hub_client, admin_headers, node["id"])
    assert len(_hub_events(hub_client, node["id"])) == 1

    # 手动回拨游标 → 再拉 → 仍只有一行
    from hub.backend import db as hubdb
    from hub.backend.models import HubNode
    s = hubdb.SessionLocal()
    try:
        s.query(HubNode).get(node["id"]).event_cursor = 19
        s.commit()
    finally:
        s.close()
    _poll_with_events(hub_client, admin_headers, node["id"])
    assert len(_hub_events(hub_client, node["id"])) == 1


def test_event_keep_max_trims_oldest(hub_client, admin_headers, fake_node,
                                     monkeypatch):
    """超保留上限删最老 (monkeypatch 上限为 3)"""
    import hub.backend.poller as poller_mod
    monkeypatch.setattr(poller_mod, "EVENT_KEEP_MAX", 3)

    app, node = fake_node
    _poll_with_events(hub_client, admin_headers, node["id"])
    app.state.edge["cycle_events"] += [
        {"id": 100 + i, "kind": "cycle", "channel_id": 0, "result": "NG",
         "event_name": f"NG{i}", "reason": None, "ts": None}
        for i in range(5)]
    _poll_with_events(hub_client, admin_headers, node["id"])
    rows = _hub_events(hub_client, node["id"])
    assert len(rows) == 3
    assert [r.edge_event_id for r in rows] == [102, 103, 104], "删的必须是最老"


# ============================================================
# 2. 事件中心端点
# ============================================================

@pytest.fixture
def node_with_ng(hub_client, admin_headers, fake_node):
    """预置 2 条 NG 已落枢纽"""
    app, node = fake_node
    _poll_with_events(hub_client, admin_headers, node["id"])
    app.state.edge["cycle_events"] += [
        {"id": 50, "kind": "cycle", "channel_id": 0, "result": "NG",
         "event_name": "NG事件", "reason": "缺步骤2", "ts": "2026-09-19T11:00:00"},
        {"id": 51, "kind": "cycle", "channel_id": 0, "result": "NG",
         "event_name": "NG事件", "reason": "超时", "ts": "2026-09-19T11:01:00"},
    ]
    _poll_with_events(hub_client, admin_headers, node["id"])
    return app, node


def test_events_list_schema_and_unacked(hub_client, admin_headers,
                                        node_with_ng):
    _, node = node_with_ng
    r = hub_client.get(f"{API}/events", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and body["unacked"] == 2
    row = body["items"][0]
    for key in ("id", "node_id", "node_name", "channel_id", "kind", "result",
                "event_name", "reason", "ts", "acked_by", "acked_at"):
        assert key in row, f"事件行缺字段 {key}"
    assert row["node_name"] == "事件测试机"
    assert row["reason"] == "超时", "必须时间倒序 (新的在前)"


def test_event_ack_flow(hub_client, admin_headers, engineer_headers,
                        operator_headers, node_with_ng):
    _, node = node_with_ng
    items = hub_client.get(f"{API}/events", headers=admin_headers) \
        .json()["items"]
    ev_id = items[0]["id"]

    # operator 无 ops.execute → 403
    r = hub_client.post(f"{API}/events/{ev_id}/ack", headers=operator_headers)
    assert r.status_code == 403

    # engineer ack → 记名
    r = hub_client.post(f"{API}/events/{ev_id}/ack", headers=engineer_headers)
    assert r.status_code == 200 and r.json()["acked_by"] == "eng1"

    # admin 重复 ack → 幂等保留首个处理人
    r = hub_client.post(f"{API}/events/{ev_id}/ack", headers=admin_headers)
    assert r.json()["acked_by"] == "eng1"

    body = hub_client.get(f"{API}/events", headers=admin_headers).json()
    assert body["unacked"] == 1
    # unacked_only 过滤
    body = hub_client.get(f"{API}/events", headers=admin_headers,
                          params={"unacked_only": True}).json()
    assert all(i["acked_by"] is None for i in body["items"])
    assert len(body["items"]) == 1

    # 审计
    r = hub_client.get(f"{API}/audit", headers=admin_headers,
                       params={"action": "event.ack"})
    assert any(l["username"] == "eng1" for l in r.json()["items"])


# ============================================================
# 3. 用户管理
# ============================================================

def test_users_crud_admin_only(hub_client, admin_headers, engineer_headers):
    # engineer 无 node.manage → 403
    assert hub_client.get(f"{API}/users",
                          headers=engineer_headers).status_code == 403

    r = hub_client.post(f"{API}/users", headers=admin_headers, json={
        "username": "u_test1", "password": "pw123456",
        "display_name": "测试用户", "role": "engineer"})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]

    # 校验: 重名 409 / 短密码 400 / 坏角色 400
    assert hub_client.post(f"{API}/users", headers=admin_headers, json={
        "username": "u_test1", "password": "pw123456"}).status_code == 409
    assert hub_client.post(f"{API}/users", headers=admin_headers, json={
        "username": "u_x", "password": "123"}).status_code == 400
    assert hub_client.post(f"{API}/users", headers=admin_headers, json={
        "username": "u_x", "password": "pw123456",
        "role": "superman"}).status_code == 400

    # 新用户能登录并按角色拿权限
    r = hub_client.post(f"{API}/auth/login",
                        json={"username": "u_test1", "password": "pw123456"})
    assert r.status_code == 200 and r.json()["role"] == "engineer"

    # 改角色 + 禁用 → token 全吊销
    tok = {"Authorization": f"Bearer {r.json()['token']}"}
    r = hub_client.put(f"{API}/users/{uid}", headers=admin_headers,
                       json={"role": "director", "active": False})
    assert r.status_code == 200 and r.json()["role"] == "director"
    assert hub_client.get(f"{API}/auth/me", headers=tok).status_code == 401
    assert hub_client.post(f"{API}/auth/login", json={
        "username": "u_test1", "password": "pw123456"}).status_code == 401


def test_last_admin_guard(hub_client, admin_headers):
    """不能禁用/降级最后一个活跃 admin"""
    users = hub_client.get(f"{API}/users", headers=admin_headers).json()["items"]
    admin_row = [u for u in users if u["username"] == "admin"][0]
    r = hub_client.put(f"{API}/users/{admin_row['id']}", headers=admin_headers,
                       json={"active": False})
    assert r.status_code == 409
    r = hub_client.put(f"{API}/users/{admin_row['id']}", headers=admin_headers,
                       json={"role": "operator"})
    assert r.status_code == 409


def test_change_password_flow(hub_client, admin_headers):
    hub_client.post(f"{API}/users", headers=admin_headers, json={
        "username": "u_pwd", "password": "oldpw123", "role": "operator"})
    r = hub_client.post(f"{API}/auth/login",
                        json={"username": "u_pwd", "password": "oldpw123"})
    tok = {"Authorization": f"Bearer {r.json()['token']}"}

    # 旧密错 → 403
    r = hub_client.post(f"{API}/auth/change-password", headers=tok, json={
        "old_password": "wrong", "new_password": "newpw123"})
    assert r.status_code == 403

    # 正确改密 → 全部会话吊销 → 新密可登录
    r = hub_client.post(f"{API}/auth/change-password", headers=tok, json={
        "old_password": "oldpw123", "new_password": "newpw123"})
    assert r.status_code == 200
    assert hub_client.get(f"{API}/auth/me", headers=tok).status_code == 401
    assert hub_client.post(f"{API}/auth/login", json={
        "username": "u_pwd", "password": "newpw123"}).status_code == 200