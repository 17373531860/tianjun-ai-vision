"""Fleet Hub M8 WS 推送加速器测试。

纪律: 轮询是真相源, WS 只发提示帧 {"topic": wall|events}。
覆盖: 鉴权 (坏 token 4401 关闭) / 工位状态变化推 wall / 新 NG 事件推
events / 节点判离线推 wall / 无连接时轮询链路零影响。
"""
import httpx
import pytest

API = "/api/v1"


@pytest.fixture
def one_edge(hub_client, admin_headers):
    """单假边缘 + 可切断网 transport (与 m7 opsalarm 同构)"""
    from httpx import ASGITransport

    from hub.backend import edge_client
    from tests.hub.fake_edge import create_fake_edge

    url = "http://edge-m8:8001"
    app = create_fake_edge(node_id="edge-m8-01", station_count=1)
    down = set()

    def factory(base_url: str):
        base = base_url.rstrip("/")
        if base in down:
            return httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(
                    httpx.ConnectError("simulated down")))
        if base == url:
            return ASGITransport(app=app)
        raise AssertionError(f"未知边缘: {base_url}")

    edge_client.set_transport_factory(factory)
    r = hub_client.post(f"{API}/nodes", headers=admin_headers, json={
        "name": "WS测试机", "base_url": url, "api_key": "tk_m8"})
    assert r.status_code == 200, r.text
    yield {"node": r.json(), "down": down, "url": url, "app": app}


def _poll(hub_client, admin_headers, node_id, times=1):
    for _ in range(times):
        hub_client.post(f"{API}/nodes/{node_id}/poll", headers=admin_headers)


def _admin_token(hub_client):
    return hub_client.post(f"{API}/auth/login", json={
        "username": "admin", "password": "admin123"}).json()["token"]


def test_ws_rejects_bad_token(hub_client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as exc:
        with hub_client.websocket_connect(f"{API}/ws?token=BAD") as ws:
            ws.receive_json()
    assert exc.value.code == 4401

    # 无 token 同样拒绝
    with pytest.raises(WebSocketDisconnect):
        with hub_client.websocket_connect(f"{API}/ws") as ws:
            ws.receive_json()


def test_ws_hints_on_twin_change_and_events(hub_client, admin_headers,
                                            one_edge):
    nid = one_edge["node"]["id"]
    token = _admin_token(hub_client)
    poller = hub_client.app.state.poller

    with hub_client.websocket_connect(f"{API}/ws?token={token}") as ws:
        # 首轮 poll: 孪生 reported 初次写入 (changed=True) → 推 wall
        _poll(hub_client, admin_headers, nid)
        assert ws.receive_json() == {"topic": "wall"}

        # 注入 NG 事件 + 强制事件通道到期 → 推 events
        one_edge["app"].state.edge["cycle_events"].append({
            "id": 1, "kind": "cycle", "channel_id": 0, "result": "NG",
            "event_name": "NG事件", "reason": "缺步骤",
            "ts": "2026-09-22T15:00:00"})
        poller.runtime(nid).last_event_pull = 0.0   # 绕开 4s 节流
        _poll(hub_client, admin_headers, nid)
        assert ws.receive_json() == {"topic": "events"}

        # 断网 3 连败判离线 → 推 wall (状态切换)
        one_edge["down"].add(one_edge["url"])
        _poll(hub_client, admin_headers, nid, times=3)
        assert ws.receive_json() == {"topic": "wall"}


def test_poll_unaffected_without_ws_clients(hub_client, admin_headers,
                                            one_edge):
    """无 WS 连接时 notify 零开销不报错 (加速器纯可选)。"""
    nid = one_edge["node"]["id"]
    _poll(hub_client, admin_headers, nid)
    r = hub_client.get(f"{API}/wall", headers=admin_headers)
    assert r.json()["totals"]["nodes"] == 1
