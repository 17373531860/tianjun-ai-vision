"""Fleet Hub M7 运维告警链路后端测试。

覆盖: 上下线切换记账 (unknown→online 不记 / 判离线记行带错误 /
恢复行带离线时长) + status-events 查询与 7 日统计 + 通知规则
(离线超阈值外推 / 恢复补发 / 阈值内闪断静默销账 / 关闭态不积压) +
notify 配置端点权限 + PUT /nodes 改名与身份防呆 + 钉钉加签。
"""
import asyncio

import httpx
import pytest

API = "/api/v1"


@pytest.fixture
def one_edge(hub_client, admin_headers):
    """单假边缘 + 可切断网的 transport; 返回 {node, down: set}"""
    from httpx import ASGITransport

    from hub.backend import edge_client
    from tests.hub.fake_edge import create_fake_edge

    url = "http://edge-m7:8001"
    app = create_fake_edge(node_id="edge-m7-01", station_count=1)
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
        "name": "运维测试机", "base_url": url, "api_key": "tk_m7"})
    assert r.status_code == 200, r.text
    yield {"node": r.json(), "down": down, "url": url, "app": app}


def _poll(hub_client, admin_headers, node_id, times=1):
    for _ in range(times):
        hub_client.post(f"{API}/nodes/{node_id}/poll", headers=admin_headers)


def _events(hub_client, admin_headers, **params):
    r = hub_client.get(f"{API}/nodes/status-events", headers=admin_headers,
                       params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_status_transitions_recorded(hub_client, admin_headers, one_edge):
    nid = one_edge["node"]["id"]

    # 首轮在线: unknown→online 初始转换不记 (枢纽重启不刷噪音)
    _poll(hub_client, admin_headers, nid)
    body = _events(hub_client, admin_headers)
    assert body["total"] == 0

    # 断网 3 连败判离线: 记 offline 行带错误
    one_edge["down"].add(one_edge["url"])
    _poll(hub_client, admin_headers, nid, times=3)
    body = _events(hub_client, admin_headers)
    assert body["total"] == 1
    assert body["items"][0]["status"] == "offline"
    assert body["items"][0]["error"]

    # 恢复: 记 online 行带离线时长; 7 日统计断连 1 次
    one_edge["down"].clear()
    _poll(hub_client, admin_headers, nid)
    body = _events(hub_client, admin_headers)
    assert body["total"] == 2
    assert body["items"][0]["status"] == "online"
    assert body["items"][0]["duration_s"] is not None
    assert body["stats"][str(nid)]["offline_count_7d"] == 1

    # 再断再恢复: 不重复计初始转换, 共 4 行 2 次断连
    one_edge["down"].add(one_edge["url"])
    _poll(hub_client, admin_headers, nid, times=3)
    one_edge["down"].clear()
    _poll(hub_client, admin_headers, nid)
    body = _events(hub_client, admin_headers, node_id=nid)
    assert body["total"] == 4
    assert body["stats"][str(nid)]["offline_count_7d"] == 2


def _run_notify_once(hub_client):
    """驱动一次通知巡检 (测试态无循环, 显式调)。

    在独立线程起新事件循环: 全量套跑时主线程可能已有 running loop
    (前序 e2e 遗留), 直接 asyncio.run 会 RuntimeError。
    """
    import threading

    from hub.backend import notify
    poller = hub_client.app.state.poller
    errs = []

    def runner():
        try:
            asyncio.run(poller._notify_once(notify))
        except Exception as e:  # 让断言看到真实异常
            errs.append(e)

    t = threading.Thread(target=runner)
    t.start()
    t.join(timeout=10)
    if errs:
        raise errs[0]


@pytest.fixture
def sent(monkeypatch):
    """捕获 broadcast 外推 (不真发网络)"""
    calls = []

    async def fake_broadcast(cfg, title, text, payload):
        calls.append({"title": title, "payload": payload})
        return []

    from hub.backend import notify
    monkeypatch.setattr(notify, "broadcast", fake_broadcast)
    return calls


def test_notify_offline_and_recover(hub_client, admin_headers, one_edge, sent):
    nid = one_edge["node"]["id"]
    # 阈值 0 = 判离线即外推; 开恢复通知
    r = hub_client.put(f"{API}/notify/config", headers=admin_headers, json={
        "enabled": True, "offline_threshold_min": 0, "notify_recover": True,
        "channels": [{"type": "webhook", "url": "http://x/hook"}]})
    assert r.status_code == 200

    _poll(hub_client, admin_headers, nid)
    one_edge["down"].add(one_edge["url"])
    _poll(hub_client, admin_headers, nid, times=3)
    _run_notify_once(hub_client)
    assert len(sent) == 1
    assert sent[0]["payload"]["event"] == "node_offline"

    # 幂等: 再巡检不重发
    _run_notify_once(hub_client)
    assert len(sent) == 1

    # 恢复: 补发恢复通知 (离线侧已通知过)
    one_edge["down"].clear()
    _poll(hub_client, admin_headers, nid)
    _run_notify_once(hub_client)
    assert len(sent) == 2
    assert sent[1]["payload"]["event"] == "node_recover"


def test_notify_flap_within_threshold_silent(hub_client, admin_headers,
                                             one_edge, sent):
    """阈值内闪断恢复: 离线/恢复两行一起静默销账, 一条都不发。"""
    nid = one_edge["node"]["id"]
    hub_client.put(f"{API}/notify/config", headers=admin_headers, json={
        "enabled": True, "offline_threshold_min": 5, "notify_recover": True,
        "channels": [{"type": "webhook", "url": "http://x/hook"}]})

    _poll(hub_client, admin_headers, nid)
    one_edge["down"].add(one_edge["url"])
    _poll(hub_client, admin_headers, nid, times=3)
    one_edge["down"].clear()
    _poll(hub_client, admin_headers, nid)   # 阈值 5min 内已恢复
    _run_notify_once(hub_client)
    assert sent == []

    # 行都已销账 (notified), 后续巡检也不会翻旧账
    _run_notify_once(hub_client)
    assert sent == []


def test_notify_disabled_no_backlog(hub_client, admin_headers, one_edge, sent):
    """通知关闭期间的事件静默销账 —— 之后开启不会洪水外推旧事件。"""
    nid = one_edge["node"]["id"]
    _poll(hub_client, admin_headers, nid)
    one_edge["down"].add(one_edge["url"])
    _poll(hub_client, admin_headers, nid, times=3)
    _run_notify_once(hub_client)    # enabled=False 默认
    assert sent == []

    # 开启后 (节点还在离线) 不再翻已销账的旧行
    hub_client.put(f"{API}/notify/config", headers=admin_headers, json={
        "enabled": True, "offline_threshold_min": 0, "notify_recover": True,
        "channels": [{"type": "webhook", "url": "http://x/hook"}]})
    _run_notify_once(hub_client)
    assert sent == []


def test_notify_config_permissions(hub_client, admin_headers,
                                   engineer_headers):
    # engineer 无 node.manage: 读写配置都 403
    assert hub_client.get(f"{API}/notify/config",
                          headers=engineer_headers).status_code == 403
    assert hub_client.put(f"{API}/notify/config", headers=engineer_headers,
                          json={"enabled": True}).status_code == 403
    # admin 读写回环
    r = hub_client.put(f"{API}/notify/config", headers=admin_headers, json={
        "enabled": True, "offline_threshold_min": 10, "notify_recover": False,
        "channels": [{"type": "dingtalk", "url": "http://d/hook",
                      "secret": "SEC123"}]})
    assert r.status_code == 200
    r = hub_client.get(f"{API}/notify/config", headers=admin_headers)
    body = r.json()
    assert body["offline_threshold_min"] == 10
    assert body["channels"][0]["type"] == "dingtalk"


def test_update_node_name_and_identity_guard(hub_client, admin_headers,
                                             one_edge):
    from httpx import ASGITransport

    from hub.backend import edge_client
    from tests.hub.fake_edge import create_fake_edge

    nid = one_edge["node"]["id"]
    # 改名: 本地生效不连边缘
    r = hub_client.put(f"{API}/nodes/{nid}", headers=admin_headers,
                       json={"name": "改名机"})
    assert r.status_code == 200
    assert r.json()["name"] == "改名机"

    # 换地址但目标是另一台机器 (node_uid 不同): 409 拒绝
    other_url = "http://edge-m7-other:8001"
    other_app = create_fake_edge(node_id="edge-m7-OTHER", station_count=1)

    def factory(base_url: str):
        base = base_url.rstrip("/")
        if base == other_url:
            return ASGITransport(app=other_app)
        return ASGITransport(app=one_edge["app"])

    edge_client.set_transport_factory(factory)
    r = hub_client.put(f"{API}/nodes/{nid}", headers=admin_headers,
                       json={"base_url": other_url})
    assert r.status_code == 409
    assert "身份不符" in r.json()["detail"]


def test_alarm_escalation(hub_client, admin_headers, one_edge, sent):
    """M9 报警升级: 未确认 NG 超阈值 → 冷却窗内一条汇总; 确认后不再触发。"""
    from datetime import datetime, timedelta

    from hub.backend import db as hubdb
    from hub.backend.models import HubEvent

    nid = one_edge["node"]["id"]
    old_ts = (datetime.now() - timedelta(minutes=10)).isoformat()
    s = hubdb.SessionLocal()
    s.add(HubEvent(node_id=nid, channel_id=0, edge_event_id=100,
                   kind="cycle", result="NG", event_name="NG事件",
                   reason="缺步骤", ts=old_ts))
    s.commit()
    ev_id = s.query(HubEvent).order_by(HubEvent.id.desc()).first().id
    s.close()

    # 升级关 (escalate_min=0 默认): 挂 10 分钟也不发
    hub_client.put(f"{API}/notify/config", headers=admin_headers, json={
        "enabled": True, "offline_threshold_min": 5, "notify_recover": True,
        "channels": [{"type": "webhook", "url": "http://x/hook"}]})
    _run_notify_once(hub_client)
    assert sent == []

    # 开 5 分钟档: 事件已挂 10 分钟 → 发一条汇总
    hub_client.put(f"{API}/notify/config", headers=admin_headers, json={
        "enabled": True, "offline_threshold_min": 5, "notify_recover": True,
        "alarm_escalate_min": 5, "alarm_escalate_cooldown_min": 30,
        "channels": [{"type": "webhook", "url": "http://x/hook"}]})
    _run_notify_once(hub_client)
    assert len(sent) == 1
    assert sent[0]["payload"]["event"] == "alarm_escalate"
    assert sent[0]["payload"]["unacked"] == 1
    assert sent[0]["payload"]["oldest_age_min"] >= 10

    # 冷却窗内不重发
    _run_notify_once(hub_client)
    assert len(sent) == 1

    # 确认处理后即使冷却过了也不再触发 (无未确认事件)
    r = hub_client.post(f"{API}/events/{ev_id}/ack", headers=admin_headers)
    assert r.status_code == 200
    from hub.backend.models import HubSetting
    s = hubdb.SessionLocal()
    s.query(HubSetting).filter(
        HubSetting.key == "alarm_escalate_last").delete()  # 人为清冷却
    s.commit()
    s.close()
    _run_notify_once(hub_client)
    assert len(sent) == 1


def test_station_live_projection_fake_edge(hub_client, admin_headers,
                                           one_edge):
    """M7.5 生产实况: 枢纽按需代理边缘 /hub/live 白名单投影。"""
    nid = one_edge["node"]["id"]
    _poll(hub_client, admin_headers, nid)   # 建工位行
    r = hub_client.get(f"{API}/nodes/{nid}/stations/0/live",
                       headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counters"]["ok"] == 128
    assert body["cycle"]["average_time"] == 45.2
    labels = [s["label"] for s in body["steps"]]
    assert "拧紧螺丝" in labels
    assert body["recent_events"][0]["kind"] == "ng"
    # 大载荷必须被剥掉 (截图/检测框/配置 JSON)
    for heavy in ("step_screenshots", "detections", "steps_config",
                  "pipeline_config"):
        assert heavy not in body
    # 不存在的工位 404
    r = hub_client.get(f"{API}/nodes/{nid}/stations/99/live",
                       headers=admin_headers)
    assert r.status_code == 404


def test_station_live_real_edge(hub_client, admin_headers, enrolled_node):
    """真主程序当边缘: /hub/live 投影链路端到端可用 (契约防漂移)。"""
    nid = enrolled_node["id"]
    hub_client.post(f"{API}/nodes/{nid}/poll", headers=admin_headers)
    r = hub_client.get(f"{API}/nodes/{nid}/stations/0/live",
                       headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # 空跑主程序: 结构齐全, 数值为空/零
    assert "counters" in body and "cycle" in body and "steps" in body
    assert body["channel_id"] == 0
    assert isinstance(body["recent_events"], list)


def test_dingtalk_sign_url():
    """钉钉加签: timestamp+sign 拼接进 URL (官方 HMAC-SHA256 算法)"""
    from hub.backend.notify import _dingtalk_url
    url = _dingtalk_url("https://oapi.dingtalk.com/robot/send?access_token=x",
                        "SECabc")
    assert "timestamp=" in url and "sign=" in url
    # 无密钥原样返回
    assert _dingtalk_url("http://u", None) == "http://u"
