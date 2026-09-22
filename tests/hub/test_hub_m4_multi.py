"""Fleet Hub M4 测试: 多边缘并管 — 故障隔离 / 操作定向 / 锁按工位隔离。

拓扑: 3 个契约级假边缘 (fake_edge, 不同 node_id/base_url), transport factory
按 base_url 路由到各自 ASGITransport —— 模拟"一枢纽管多台工控机"。

覆盖:
  1. 三节点纳管: 墙聚合 totals 正确, 各节点工位独立
  2. 故障隔离: 单节点断网判 offline + 墙 alerts, 其他节点不受影响; 恢复回 online
  3. 操作定向: 对节点 2 的写操作只改节点 2 的边缘状态
  4. 锁隔离: 节点 1 工位被锁不影响节点 2 同号工位操作
  5. ack_alarm 多边缘: 消警定向到目标边缘
"""
import httpx
import pytest

API = "/api/v1"


@pytest.fixture
def three_edges(hub_client, admin_headers):
    """三假边缘 + 按 base_url 路由的 transport; 返回 {url: (app, node_dict)}"""
    from httpx import ASGITransport

    from hub.backend import edge_client
    from tests.hub.fake_edge import create_fake_edge

    apps = {}
    down = set()          # 放进来的 base_url 模拟断网

    for i in range(1, 4):
        url = f"http://edge-m4-{i}:8001"
        apps[url] = create_fake_edge(node_id=f"edge-m4-{i:02d}")

    def factory(base_url: str):
        base = base_url.rstrip("/")
        if base in down:
            raise_transport = httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(
                    httpx.ConnectError("simulated down")))
            return raise_transport
        if base in apps:
            return ASGITransport(app=apps[base])
        raise AssertionError(f"未知边缘: {base_url}")

    edge_client.set_transport_factory(factory)

    nodes = {}
    for i, (url, app) in enumerate(apps.items(), start=1):
        r = hub_client.post(f"{API}/nodes", headers=admin_headers, json={
            "name": f"边缘{i}", "base_url": url, "api_key": f"tk_m4_{i}"})
        assert r.status_code == 200, r.text
        nodes[url] = (app, r.json())

    yield {"nodes": nodes, "down": down}
    # transport factory 由 conftest hub_client 退出时复位


def _poll(hub_client, admin_headers, node_id):
    return hub_client.post(f"{API}/nodes/{node_id}/poll",
                           headers=admin_headers)


def test_three_nodes_wall_totals(hub_client, admin_headers, three_edges):
    for _, node in three_edges["nodes"].values():
        _poll(hub_client, admin_headers, node["id"])
    r = hub_client.get(f"{API}/wall", headers=admin_headers)
    body = r.json()
    assert body["totals"]["nodes"] == 3
    assert body["totals"]["stations"] == 3
    assert body["totals"]["offline_nodes"] == 0
    names = {n["name"] for n in body["nodes"]}
    assert names == {"边缘1", "边缘2", "边缘3"}


def test_single_node_failure_isolated(hub_client, admin_headers, three_edges):
    nodes = list(three_edges["nodes"].items())
    down_url, (_, down_node) = nodes[0]
    for _, node in three_edges["nodes"].values():
        _poll(hub_client, admin_headers, node["id"])

    # 断网节点 1: 连续 3 次失败判 offline (OFFLINE_AFTER_FAILURES)
    three_edges["down"].add(down_url)
    for _ in range(3):
        _poll(hub_client, admin_headers, down_node["id"])

    r = hub_client.get(f"{API}/wall", headers=admin_headers)
    body = r.json()
    assert body["totals"]["offline_nodes"] == 1
    off = [n for n in body["nodes"] if n["id"] == down_node["id"]][0]
    assert off["status"] == "offline"
    # 其他两节点不受影响
    others = [n for n in body["nodes"] if n["id"] != down_node["id"]]
    assert all(n["status"] == "online" for n in others)
    # 墙 alerts 有该节点的 offline 项
    assert any(a["kind"] == "offline" and a["node_id"] == down_node["id"]
               for a in body["alerts"])

    # 恢复
    three_edges["down"].discard(down_url)
    _poll(hub_client, admin_headers, down_node["id"])
    r = hub_client.get(f"{API}/wall", headers=admin_headers)
    off = [n for n in r.json()["nodes"] if n["id"] == down_node["id"]][0]
    assert off["status"] == "online"


def test_ops_targets_correct_edge(hub_client, admin_headers, three_edges):
    """对节点 2 启动检测: 只有节点 2 的假边缘状态翻 True"""
    items = list(three_edges["nodes"].values())
    target_app, target_node = items[1]
    r = hub_client.post(
        f"{API}/nodes/{target_node['id']}/stations/0/ops",
        headers=admin_headers, json={"action": "start_detection"})
    assert r.status_code == 200, r.text
    assert target_app.state.edge["detecting"][0] is True
    for app, node in items:
        if node["id"] != target_node["id"]:
            assert app.state.edge["detecting"][0] is False, \
                f"节点 {node['name']} 不应被波及"


def test_lock_isolated_per_station(hub_client, admin_headers, engineer_headers,
                                   three_edges):
    """节点 1 工位 0 被 engineer 锁住, 不妨碍 admin 操作节点 2 工位 0"""
    items = list(three_edges["nodes"].values())
    _, node1 = items[0]
    app2, node2 = items[1]
    hub_client.post(f"{API}/nodes/{node1['id']}/stations/0/lock",
                    headers=engineer_headers, json={})
    # admin 操作节点 1 → 409 (被锁)
    r = hub_client.post(f"{API}/nodes/{node1['id']}/stations/0/ops",
                        headers=admin_headers, json={"action": "stop_detection"})
    assert r.status_code == 409
    # admin 操作节点 2 → 畅通
    r = hub_client.post(f"{API}/nodes/{node2['id']}/stations/0/ops",
                        headers=admin_headers, json={"action": "stop_detection"})
    assert r.status_code == 200, r.text


def test_ack_alarm_targets_correct_edge(hub_client, admin_headers, three_edges):
    items = list(three_edges["nodes"].values())
    app3, node3 = items[2]
    assert app3.state.edge["alarm_active"][0] is True
    r = hub_client.post(f"{API}/nodes/{node3['id']}/stations/0/ops",
                        headers=admin_headers, json={"action": "ack_alarm"})
    assert r.status_code == 200, r.text
    assert app3.state.edge["alarm_active"][0] is False
    for app, node in items[:2]:
        assert app.state.edge["alarm_active"][0] is True, \
            f"节点 {node['name']} 的报警不应被波及"