"""Fleet Hub M2 读面测试: /wall 聚合 + 快照转发。"""


def test_wall_aggregation(hub_client, admin_headers, enrolled_node):
    nid = enrolled_node["id"]
    # 先 poll 一次让运行态/孪生就位
    hub_client.post(f"/api/v1/nodes/{nid}/poll", headers=admin_headers)

    r = hub_client.get("/api/v1/wall", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["nodes"] == 1
    assert body["totals"]["stations"] >= 1

    node = body["nodes"][0]
    for key in ("id", "name", "status", "stale", "license_state",
                "active_project_id", "resources", "stations"):
        assert key in node, f"wall 节点缺字段 {key}"
    assert node["status"] == "online"
    assert node["stale"] is False

    st = node["stations"][0]
    for key in ("channel_id", "display_name", "reported", "reported_at"):
        assert key in st, f"wall 工位缺字段 {key}"
    assert st["reported"]["detecting"] is False
    assert "properties" in st
    prop_ids = {p["id"] for p in st["properties"]}
    assert {"detecting", "active_project_id"} <= prop_ids
    assert "alerts" in body
    assert isinstance(body["alerts"], list)


def test_wall_requires_auth(hub_client):
    assert hub_client.get("/api/v1/wall").status_code == 401


def test_snapshot_proxy(hub_client, admin_headers, enrolled_node):
    """快照转发: 边缘无源时自己画黑帧, 转发侧仍应拿到 JPEG"""
    nid = enrolled_node["id"]
    ch = 0
    r = hub_client.get(f"/api/v1/nodes/{nid}/stations/{ch}/snapshot",
                       headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8", "应是 JPEG 魔数"


def test_snapshot_404_for_unknown_station(hub_client, admin_headers,
                                          enrolled_node):
    nid = enrolled_node["id"]
    r = hub_client.get(f"/api/v1/nodes/{nid}/stations/999/snapshot",
                       headers=admin_headers)
    assert r.status_code == 404


def test_snapshot_503_when_edge_down(hub_client, admin_headers,
                                     enrolled_node):
    import httpx

    from hub.backend import edge_client

    nid = enrolled_node["id"]

    def _broken(request):
        raise httpx.ConnectError("down")

    old = edge_client._transport_factory
    edge_client.set_transport_factory(
        lambda base_url: httpx.MockTransport(_broken))
    try:
        r = hub_client.get(f"/api/v1/nodes/{nid}/stations/0/snapshot",
                           headers=admin_headers)
        assert r.status_code == 503
    finally:
        edge_client.set_transport_factory(old)


def test_station_detail_schema(hub_client, admin_headers, enrolled_node):
    nid = enrolled_node["id"]
    hub_client.post(f"/api/v1/nodes/{nid}/poll", headers=admin_headers)
    r = hub_client.get(f"/api/v1/nodes/{nid}/stations/0", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("node_id", "node_name", "status", "stale", "channel_id",
                "display_name", "reported", "properties", "actions",
                "node_actions", "lock"):
        assert key in body, f"station detail 缺字段 {key}"
    assert body["node_id"] == nid
    assert body["channel_id"] == 0
    prop_ids = {p["id"] for p in body["properties"]}
    assert "detecting" in prop_ids
    # M3: 锁状态三字段 + 档案登记的动作可见 (前端按钮据此渲染)
    assert set(body["lock"].keys()) == {"locked", "holder", "expires_in_s"}
    assert {a["id"] for a in body["actions"]} >= {"start_detection",
                                                  "stop_detection"}
    assert any(a["id"] == "activate_project" for a in body["node_actions"])


def test_station_rename_local(hub_client, admin_headers, enrolled_node):
    nid = enrolled_node["id"]
    r = hub_client.put(f"/api/v1/nodes/{nid}/stations/0",
                       headers=admin_headers,
                       json={"display_name": "包装-1#", "group_name": "包装线"})
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "包装-1#"
    assert r.json()["group_name"] == "包装线"
    wall = hub_client.get("/api/v1/wall", headers=admin_headers).json()
    st = wall["nodes"][0]["stations"][0]
    assert st["display_name"] == "包装-1#"
    assert st["group_name"] == "包装线"
    audit = hub_client.get("/api/v1/audit?action=station.rename",
                           headers=admin_headers).json()
    assert audit["total"] >= 1


def test_wall_offline_alert(hub_client, admin_headers, enrolled_node):
    import httpx

    from hub.backend import edge_client
    nid = enrolled_node["id"]
    def _broken(request):
        raise httpx.ConnectError("down")

    old = edge_client._transport_factory
    edge_client.set_transport_factory(
        lambda base_url: httpx.MockTransport(_broken))
    try:
        for _ in range(3):
            hub_client.post(f"/api/v1/nodes/{nid}/poll", headers=admin_headers)
        wall = hub_client.get("/api/v1/wall", headers=admin_headers).json()
        assert wall["totals"]["offline_nodes"] == 1
        kinds = [a["kind"] for a in wall["alerts"]]
        assert "offline" in kinds
    finally:
        edge_client.set_transport_factory(old)
