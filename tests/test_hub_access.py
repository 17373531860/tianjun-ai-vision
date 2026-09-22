"""RFC 15 M0 边缘侧 hub 接入端点测试。

覆盖:
  1. 默认关守门: hub_access.enabled 未开时 /hub/* 业务端点 404 (存量客户零差异)
  2. 开关端点: PUT /hub/config 开启后 handshake/profile/health-summary 可用
  3. 能力档案 schema 契约: RFC 15 §9.4 关键字段快照 (字段增删必须过这里)
  4. profile_hash 稳定性: 同状态两次拉取 hash 一致
  5. 激活守门: 任一通道 is_detecting=True 时 POST /projects/{id}/activate → 409
  6. API Key scope 白名单: scope=hub 可创建
  7. M3 写操作入口 POST /hub/ops: 启停幂等/门槛 409/切项目守门 + GET /hub/projects

注意: 测试环境 auth.enabled 默认 false → require_api_key 放行 (与生产
未开鉴权一致); scope 强制走 auth 开启路径的行为由 core/api_key 自身测试覆盖。
"""
import uuid

import pytest


HUB = "/api/v1/hub"


@pytest.fixture
def hub_enabled(client):
    """开启 hub 接入, 测试后复位关闭 (避免污染其他测试的 404 预期)。"""
    r = client.put(f"{HUB}/config", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    yield
    client.put(f"{HUB}/config", json={"enabled": False})


# ============================================================
# 1. 默认关守门
# ============================================================

def test_hub_endpoints_404_when_disabled(client):
    # 确保关闭态
    client.put(f"{HUB}/config", json={"enabled": False})
    for path in ("/handshake", "/profile", "/health-summary"):
        r = client.get(f"{HUB}{path}")
        assert r.status_code == 404, f"{path} 未开启时应 404, 实际 {r.status_code}"


def test_hub_config_readable_when_disabled(client):
    """config 端点不受守门 (否则永远开不了)"""
    r = client.get(f"{HUB}/config")
    assert r.status_code == 200
    assert "enabled" in r.json()


# ============================================================
# 2/3/4. 开启后的握手 / 档案契约 / hash 稳定性
# ============================================================

def test_handshake_identity_fields(client, hub_enabled):
    r = client.get(f"{HUB}/handshake")
    assert r.status_code == 200
    body = r.json()
    ident = body["identity"]
    # schema 契约: 枢纽消费的关键字段 (增删字段必须同步 RFC 15 §9.4 + 本测试)
    for key in ("node_id", "hostname", "machine_id", "app_version",
                "api_contract", "license"):
        assert key in ident, f"identity 缺字段 {key}"
    assert ident["api_contract"] == 1
    assert ident["node_id"].startswith("edge-")
    assert ident["license"]["state"] in ("valid", "expired", "unknown")
    assert body["profile_hash"].startswith("sha256:")
    assert isinstance(body["station_count"], int)


def test_profile_schema_contract(client, hub_enabled):
    r = client.get(f"{HUB}/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["profile_schema"] == 1
    for key in ("identity", "stations", "node_actions", "profile_hash"):
        assert key in body, f"profile 缺字段 {key}"
    assert isinstance(body["stations"], list) and body["stations"]
    st = body["stations"][0]
    # 工位三元组契约 (RFC 15 §9.4)
    for key in ("channel_id", "logic_mode", "bound_project_id",
                "properties", "actions", "events"):
        assert key in st, f"station 缺字段 {key}"
    prop_ids = {p["id"] for p in st["properties"]}
    # 工位 Device Type 1.0 强制 property
    assert {"detecting", "active_project_id"} <= prop_ids
    for p in st["properties"]:
        assert set(p["access"]) <= {"read", "write", "notify"}


def test_profile_hash_stable(client, hub_enabled):
    h1 = client.get(f"{HUB}/profile").json()["profile_hash"]
    h2 = client.get(f"{HUB}/profile").json()["profile_hash"]
    assert h1 == h2


def test_health_summary_fields(client, hub_enabled):
    r = client.get(f"{HUB}/health-summary")
    assert r.status_code == 200
    body = r.json()
    for key in ("node_id", "active_project_id", "stations",
                "resources", "detecting_stations"):
        assert key in body, f"health-summary 缺字段 {key}"
    assert isinstance(body["stations"], list) and body["stations"]
    st = body["stations"][0]
    for key in ("channel_id", "is_running", "is_detecting",
                "logic_mode", "source_type", "fps_inference"):
        assert key in st, f"health station 缺字段 {key}"
    assert set(body["resources"].keys()) == {"cpu", "memory", "disk", "gpus"}


def test_node_id_persistent(client, hub_enabled):
    """node_id 首次生成后落 KV, 多次握手返回同一身份"""
    n1 = client.get(f"{HUB}/handshake").json()["identity"]["node_id"]
    n2 = client.get(f"{HUB}/handshake").json()["identity"]["node_id"]
    assert n1 == n2


# ============================================================
# 5. 激活守门: 检测中拒绝
# ============================================================

@pytest.fixture
def temp_project(client):
    """建一个最小项目供激活测试; 测试后恢复原激活项目并删除临时项目。"""
    prev_active = None
    for p in client.get("/api/v1/projects").json().get("items", []):
        if p.get("is_active"):
            prev_active = p["id"]
            break

    name = f"hub-m0-test-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/v1/projects", json={
        "name": name,
        "task_type": "detection",
    })
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    yield pid

    if prev_active is not None:
        client.post(f"/api/v1/projects/{prev_active}/activate")
    client.delete(f"/api/v1/projects/{pid}")


def test_activate_rejected_while_detecting(client, temp_project):
    from backend.api.channel_manager import channel_manager

    ch_id = sorted(channel_manager.channels.keys())[0]
    mgr = channel_manager.channels[ch_id]
    old = getattr(mgr, "is_detecting", False)
    mgr.is_detecting = True
    try:
        r = client.post(f"/api/v1/projects/{temp_project}/activate")
        assert r.status_code == 409, f"检测中激活应 409, 实际 {r.status_code}"
        assert "停止检测" in r.json()["detail"]
    finally:
        mgr.is_detecting = old


def test_activate_allowed_when_idle(client, temp_project):
    from backend.api.channel_manager import channel_manager

    # 确保无通道在检
    olds = {}
    for ch_id, mgr in channel_manager.channels.items():
        olds[ch_id] = getattr(mgr, "is_detecting", False)
        mgr.is_detecting = False
    try:
        r = client.post(f"/api/v1/projects/{temp_project}/activate")
        assert r.status_code == 200, r.text
        assert r.json()["is_active"] is True
    finally:
        for ch_id, mgr in channel_manager.channels.items():
            mgr.is_detecting = olds[ch_id]


# ============================================================
# 6. API Key scope 白名单
# ============================================================

def test_profile_registers_m3_actions(client, hub_enabled):
    """M3/M4 能力档案契约: 工位 actions 含启停+消警, node_actions 含切项目"""
    body = client.get(f"{HUB}/profile").json()
    st_actions = {a["id"] for a in body["stations"][0]["actions"]}
    assert {"start_detection", "stop_detection", "ack_alarm"} <= st_actions
    node_actions = {a["id"] for a in body["node_actions"]}
    assert "activate_project" in node_actions


# ============================================================
# 7. M3 写操作入口 /hub/ops
# ============================================================

def test_ops_404_when_disabled(client):
    client.put(f"{HUB}/config", json={"enabled": False})
    r = client.post(f"{HUB}/ops", json={"action": "stop_detection", "channel": 0})
    assert r.status_code == 404


def test_ops_unknown_action(client, hub_enabled):
    r = client.post(f"{HUB}/ops", json={"action": "reboot", "channel": 0})
    assert r.status_code == 400


def test_ops_unknown_channel(client, hub_enabled):
    r = client.post(f"{HUB}/ops", json={"action": "stop_detection", "channel": 99})
    assert r.status_code == 404


def test_ops_stop_idempotent(client, hub_enabled):
    """未在检测时 stop → ok=True 幂等返回 (枢纽收敛循环可能重放意图)"""
    from backend.api.channel_manager import channel_manager
    ch_id = sorted(channel_manager.channels.keys())[0]
    mgr = channel_manager.channels[ch_id]
    old = getattr(mgr, "is_detecting", False)
    mgr.is_detecting = False
    try:
        r = client.post(f"{HUB}/ops",
                        json={"action": "stop_detection", "channel": ch_id})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
    finally:
        mgr.is_detecting = old


def test_ops_start_rejects_without_source(client, hub_enabled):
    """无视频源时 start → 409 (对齐 _auto_start_detection 门槛, 不许瞎拉)"""
    from backend.api.channel_manager import channel_manager
    ch_id = sorted(channel_manager.channels.keys())[0]
    mgr = channel_manager.channels[ch_id]
    olds = (getattr(mgr, "is_detecting", False),
            getattr(mgr, "is_running", False),
            getattr(mgr, "source_type", None))
    mgr.is_detecting = False
    mgr.is_running = False
    mgr.source_type = None
    try:
        r = client.post(f"{HUB}/ops",
                        json={"action": "start_detection", "channel": ch_id})
        assert r.status_code == 409
        assert "视频源" in r.json()["detail"]
    finally:
        mgr.is_detecting, mgr.is_running, mgr.source_type = olds


def test_ops_start_idempotent_when_detecting(client, hub_enabled):
    from backend.api.channel_manager import channel_manager
    ch_id = sorted(channel_manager.channels.keys())[0]
    mgr = channel_manager.channels[ch_id]
    old = getattr(mgr, "is_detecting", False)
    mgr.is_detecting = True
    try:
        r = client.post(f"{HUB}/ops",
                        json={"action": "start_detection", "channel": ch_id})
        assert r.status_code == 200
        assert "已在检测中" in r.json()["message"]
    finally:
        mgr.is_detecting = old


def test_ops_activate_project(client, hub_enabled, temp_project):
    """经 /hub/ops 激活项目: 检测中 409 守门 + 空闲时成功 (与手动激活同核心)"""
    from backend.api.channel_manager import channel_manager
    ch_id = sorted(channel_manager.channels.keys())[0]
    mgr = channel_manager.channels[ch_id]
    old = getattr(mgr, "is_detecting", False)

    # 缺 project_id → 400
    r = client.post(f"{HUB}/ops", json={"action": "activate_project"})
    assert r.status_code == 400

    # 检测中 → 409
    mgr.is_detecting = True
    try:
        r = client.post(f"{HUB}/ops", json={
            "action": "activate_project", "project_id": temp_project})
        assert r.status_code == 409
    finally:
        mgr.is_detecting = False

    # 空闲 → 成功且真的激活
    try:
        r = client.post(f"{HUB}/ops", json={
            "action": "activate_project", "project_id": temp_project})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        items = client.get(f"{HUB}/projects").json()["items"]
        active = [p for p in items if p["is_active"]]
        assert active and active[0]["id"] == temp_project
    finally:
        mgr.is_detecting = old


def test_ops_ack_alarm_idempotent(client, hub_enabled):
    """M4 消警: 对齐 /alarm/stop 语义, 报警器没在响也安全返回 (幂等)"""
    from backend.api.channel_manager import channel_manager
    ch_id = sorted(channel_manager.channels.keys())[0]
    r = client.post(f"{HUB}/ops", json={"action": "ack_alarm", "channel": ch_id})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert "报警已消除" in r.json()["message"]


def test_hub_projects_list(client, hub_enabled, temp_project):
    r = client.get(f"{HUB}/projects")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(p["id"] == temp_project for p in items)
    assert set(items[0].keys()) == {"id", "name", "is_active"}


# ============================================================
# 8. P0-10 事件通道 /hub/events
# ============================================================

@pytest.fixture
def cycle_rows(client, db_session, temp_project):
    """造一个会话 + 3 个已结算周期 (OK/NG/OK), 返回 (session, [cycles])"""
    from datetime import datetime, timedelta

    from backend.models.models import DetectionCycle, DetectionSession

    sess = DetectionSession(
        session_uuid=uuid.uuid4().hex[:8], project_id=temp_project,
        start_time=datetime.now() - timedelta(minutes=5),
        channel_id=1, status="completed")
    db_session.add(sess)
    db_session.flush()
    cycles = []
    for i, good in enumerate([True, False, True]):
        c = DetectionCycle(
            cycle_uuid=uuid.uuid4().hex[:12], session_id=sess.id,
            cycle_number=i + 1,
            start_time=datetime.now() - timedelta(minutes=4 - i),
            end_time=datetime.now() - timedelta(minutes=4 - i, seconds=-30),
            is_good=good, duration=2.5,
            event_name=None if good else "NG事件",
            result_reason=None if good else "缺步骤2")
        db_session.add(c)
        cycles.append(c)
    db_session.commit()
    yield sess, cycles
    for c in cycles:
        db_session.delete(c)
    db_session.delete(sess)
    db_session.commit()


def test_events_first_pull_and_increment(client, hub_enabled, cycle_rows,
                                         db_session):
    """首拉回最近事件 + 游标; 增量拉空; 新周期落库后增量拉到"""
    sess, cycles = cycle_rows
    r = client.get(f"{HUB}/events", params={"limit": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [e["id"] for e in body["events"]]
    assert ids == sorted(ids), "事件必须按 id 升序"
    assert {c.id for c in cycles} <= set(ids) or len(body["events"]) == 10
    assert body["next_cursor"] >= max(c.id for c in cycles)
    ev = [e for e in body["events"] if e["id"] == cycles[1].id][0]
    assert ev["result"] == "NG" and ev["reason"] == "缺步骤2"
    assert ev["channel_id"] == 1 and ev["kind"] == "cycle"
    # M5 数据中心维度: 耗时毫秒 + 项目 (session 侧 join)
    assert ev["duration_ms"] == 2500
    assert ev["project_id"] == sess.project_id
    assert ev["project_name"]

    # 增量: 从 next_cursor 起是空
    cur = body["next_cursor"]
    r = client.get(f"{HUB}/events", params={"cursor": cur})
    assert r.json()["events"] == []
    assert r.json()["next_cursor"] >= cur

    # 新落一个 NG 周期 → 增量拉到它
    from datetime import datetime

    from backend.models.models import DetectionCycle
    c4 = DetectionCycle(
        cycle_uuid=uuid.uuid4().hex[:12], session_id=sess.id, cycle_number=4,
        start_time=datetime.now(), end_time=datetime.now(),
        is_good=False, event_name="NG事件", result_reason="超时")
    db_session.add(c4)
    db_session.commit()
    try:
        r = client.get(f"{HUB}/events", params={"cursor": cur})
        evs = r.json()["events"]
        assert [e["id"] for e in evs] == [c4.id]
        assert evs[0]["result"] == "NG"
    finally:
        db_session.delete(c4)
        db_session.commit()


def test_events_ng_filter(client, hub_enabled, cycle_rows):
    """result=ng 只回 NG 周期, 且游标不被过滤卡住 (盖过被跳过的 OK 行)"""
    _, cycles = cycle_rows
    first = client.get(f"{HUB}/events", params={"limit": 10}).json()
    base = min(c.id for c in cycles) - 1
    r = client.get(f"{HUB}/events", params={"cursor": base, "result": "ng"})
    body = r.json()
    assert all(e["result"] == "NG" for e in body["events"])
    assert any(e["id"] == cycles[1].id for e in body["events"])
    # 游标必须 ≥ 全表最大 id (即使最大 id 是被过滤的 OK 周期)
    assert body["next_cursor"] >= first["next_cursor"]


def test_events_404_when_disabled(client):
    client.put(f"{HUB}/config", json={"enabled": False})
    assert client.get(f"{HUB}/events").status_code == 404


def test_api_key_hub_scope_allowed(client):
    r = client.post("/api/v1/api-keys", json={
        "name": f"hub-test-{uuid.uuid4().hex[:6]}",
        "scope": "hub",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "hub"
    assert body["plaintext"].startswith("tk_")
    # 清理
    client.delete(f"/api/v1/api-keys/{body['id']}")
