"""Fleet Hub M5 数据中心测试: 统计通道 + 小时桶聚合 + /stats 查询面。

链路: fake edge 注入全量周期流 (OK+NG) → poller cycle_cursor 增量拉取落
hub_cycles 明细 + 标脏 → rollup 重算小时桶 → /stats/* 查询口径。
口径铁律: 合格率 sum(ok)/sum(total); 分母 0 回 None; 缺桶零柱+断线。
"""
import time
from datetime import datetime

import pytest

API = "/api/v1"


# ============================================================
# fixtures / helpers
# ============================================================

@pytest.fixture
def fake_node(hub_client, admin_headers):
    from httpx import ASGITransport

    from hub.backend import edge_client
    from tests.hub.fake_edge import create_fake_edge

    app = create_fake_edge(node_id="edge-m5-01", station_count=2)
    edge_client.set_transport_factory(lambda base_url: ASGITransport(app=app))
    r = hub_client.post(f"{API}/nodes", headers=admin_headers, json={
        "name": "统计测试机", "base_url": "http://edge-m5:8001",
        "api_key": "tk_m5"})
    assert r.status_code == 200, r.text
    return app, r.json()


def _poll_cycles(hub_client, admin_headers, node_id):
    """poll 一次并绕过统计通道节流 (CYCLE_PULL_INTERVAL_S)"""
    rt = hub_client.app.state.poller.runtime(node_id)
    rt.last_cycle_pull = 0
    rt.last_event_pull = time.time()  # 屏蔽报警通道, 隔离被测链路
    r = hub_client.post(f"{API}/nodes/{node_id}/poll", headers=admin_headers)
    assert r.status_code == 200, r.text


def _rollup():
    from hub.backend import db as hubdb
    from hub.backend.rollup import recompute_dirty
    s = hubdb.SessionLocal()
    try:
        return recompute_dirty(s)
    finally:
        s.close()


def _cycles(node_id):
    from hub.backend import db as hubdb
    from hub.backend.models import HubCycle
    s = hubdb.SessionLocal()
    try:
        return s.query(HubCycle).filter(HubCycle.node_id == node_id) \
            .order_by(HubCycle.edge_cycle_id).all()
    finally:
        s.close()


def _cursor(node_id):
    from hub.backend import db as hubdb
    from hub.backend.models import HubNode
    s = hubdb.SessionLocal()
    try:
        return s.query(HubNode).get(node_id).cycle_cursor
    finally:
        s.close()


# 固定时间基准: 今天 10:00 整 (本地), 断言桶起点不受跑测时刻影响
_now = datetime.now()
T0 = int(datetime(_now.year, _now.month, _now.day, 10, 0, 0).timestamp())


def _ev(eid, ts_epoch, result="OK", ch=0, reason=None, event_name=None,
        duration_ms=2000, project_id=1, project_name="装配检测A"):
    return {"id": eid, "kind": "cycle", "channel_id": ch, "result": result,
            "event_name": event_name, "reason": reason,
            "ts": datetime.fromtimestamp(ts_epoch).isoformat(),
            "duration_ms": duration_ms, "project_id": project_id,
            "project_name": project_name}


def _inject_and_settle(hub_client, admin_headers, fake_node, events):
    """注入事件 → 首拉落底游标 → 再注入 → 增量拉 → rollup。返回 node dict。"""
    app, node = fake_node
    _poll_cycles(hub_client, admin_headers, node["id"])   # 首拉 (订阅从现在)
    app.state.edge["cycle_events"] += events
    _poll_cycles(hub_client, admin_headers, node["id"])
    _rollup()
    return node


# ============================================================
# 1. 统计通道拉取
# ============================================================

def test_cycle_first_pull_subscribes_from_now(hub_client, admin_headers,
                                              fake_node):
    """首拉不翻历史帐: 明细不落, cycle_cursor 落底; 与 event_cursor 独立"""
    app, node = fake_node
    app.state.edge["cycle_events"] = [_ev(i, T0) for i in (1, 2, 3)]
    _poll_cycles(hub_client, admin_headers, node["id"])
    assert _cycles(node["id"]) == []
    assert _cursor(node["id"]) == 3


def test_cycle_pull_full_ok_and_ng(hub_client, admin_headers, fake_node):
    """增量拉 OK+NG 全落明细 (报警通道只 NG, 统计通道全量)"""
    node = _inject_and_settle(hub_client, admin_headers, fake_node, [
        _ev(10, T0, "OK"), _ev(11, T0 + 60, "NG", reason="缺步骤"),
    ])
    rows = _cycles(node["id"])
    assert [(r.edge_cycle_id, r.result) for r in rows] == [(10, "OK"),
                                                           (11, "NG")]
    assert rows[0].bucket_epoch == T0 - T0 % 3600
    assert rows[0].project_name == "装配检测A"
    assert rows[0].duration_ms == 2000


def test_cycle_dedup_on_replay(hub_client, admin_headers, fake_node):
    """游标回拨重放不产生重复明细/重复计数"""
    node = _inject_and_settle(hub_client, admin_headers, fake_node,
                              [_ev(20, T0)])
    from hub.backend import db as hubdb
    from hub.backend.models import HubNode
    s = hubdb.SessionLocal()
    try:
        s.query(HubNode).get(node["id"]).cycle_cursor = 19
        s.commit()
    finally:
        s.close()
    _poll_cycles(hub_client, admin_headers, node["id"])
    _rollup()
    assert len(_cycles(node["id"])) == 1
    r = hub_client.get(f"{API}/stats/summary",
                       params={"start": T0 - 3600, "end": T0 + 3600},
                       headers=admin_headers)
    assert r.json()["current"]["total"] == 1


# ============================================================
# 2. rollup 正确性
# ============================================================

def test_rollup_hourly_counts_and_reasons(hub_client, admin_headers,
                                          fake_node):
    """小时桶分子分母 + NG 原因规整 (空原因回退事件名/未知原因)"""
    node = _inject_and_settle(hub_client, admin_headers, fake_node, [
        _ev(30, T0, "OK", ch=0, duration_ms=1000),
        _ev(31, T0 + 10, "OK", ch=0, duration_ms=3000),
        _ev(32, T0 + 20, "NG", ch=0, reason="缺步骤"),
        _ev(33, T0 + 30, "NG", ch=1, reason=None, event_name="超时NG",
            duration_ms=None),
        _ev(34, T0 + 40, "NG", ch=1, reason=None, event_name=None),
    ])
    from hub.backend import db as hubdb
    from hub.backend.models import HubCycleHourly, HubNgHourly
    s = hubdb.SessionLocal()
    try:
        hourly = s.query(HubCycleHourly).filter(
            HubCycleHourly.node_id == node["id"]).all()
        by_ch = {h.channel_id: h for h in hourly}
        assert by_ch[0].count_total == 3 and by_ch[0].count_ok == 2
        assert by_ch[0].count_ng == 1
        # 均耗时分母只数有耗时的行: ch0 三行全有 → (1000+3000+2000)/3
        assert by_ch[0].sum_duration_ms == 6000
        assert by_ch[0].cnt_duration == 3
        # ch1: 一行无耗时 → 分母 1 (2000), 不被 NULL 拉低
        assert by_ch[1].cnt_duration == 1

        reasons = {(r.channel_id, r.reason): r.count_ng
                   for r in s.query(HubNgHourly).filter(
                       HubNgHourly.node_id == node["id"]).all()}
        assert reasons[(0, "缺步骤")] == 1
        assert reasons[(1, "超时NG")] == 1          # 空 reason 回退事件名
        assert reasons[(1, "未知原因")] == 1        # 双空收口
    finally:
        s.close()


def test_rollup_idempotent_on_late_data(hub_client, admin_headers, fake_node):
    """迟到数据进旧桶 → 重新标脏 → 整桶覆盖重算, 计数一致不翻倍"""
    node = _inject_and_settle(hub_client, admin_headers, fake_node,
                              [_ev(40, T0, "OK")])
    app, _ = fake_node
    # 迟到: 同一小时桶又来一件 (id 更大但 ts 属旧桶)
    app.state.edge["cycle_events"].append(_ev(41, T0 + 5, "NG", reason="迟到"))
    _poll_cycles(hub_client, admin_headers, node["id"])
    _rollup()
    _rollup()   # 二次消费 (无脏桶) 不改结果
    r = hub_client.get(f"{API}/stats/summary",
                       params={"start": T0 - 3600, "end": T0 + 3600},
                       headers=admin_headers)
    cur = r.json()["current"]
    assert (cur["total"], cur["ok"], cur["ng"]) == (2, 1, 1)


def test_retention_trims_old_cycles(hub_client, admin_headers, fake_node,
                                    monkeypatch):
    """明细超保留天数被清; 小时桶仍在 (历史只留聚合)"""
    import hub.backend.rollup as rollup_mod
    monkeypatch.setattr(rollup_mod, "CYCLE_KEEP_DAYS", 1)

    old_ts = T0 - 3 * 86400
    node = _inject_and_settle(hub_client, admin_headers, fake_node, [
        _ev(50, old_ts, "OK"), _ev(51, T0, "OK"),
    ])
    from hub.backend import db as hubdb
    from hub.backend.models import HubCycleHourly
    from hub.backend.rollup import run_retention
    s = hubdb.SessionLocal()
    try:
        run_retention(s)
        remaining = [c.edge_cycle_id for c in _cycles(node["id"])]
        assert remaining == [51], "3 天前明细应被清"
        buckets = {h.bucket_epoch for h in s.query(HubCycleHourly).filter(
            HubCycleHourly.node_id == node["id"]).all()}
        assert (old_ts - old_ts % 3600) in buckets, "聚合桶必须保留"
    finally:
        s.close()


# ============================================================
# 3. /stats 查询面
# ============================================================

def test_summary_kpi_and_compare(hub_client, admin_headers, fake_node):
    """KPI 口径 + 环比等长窗口 + 分母 0 → None"""
    node = _inject_and_settle(hub_client, admin_headers, fake_node, [
        _ev(60, T0 - 7200, "OK"),                       # 上一窗口 (环比)
        _ev(61, T0, "OK"), _ev(62, T0 + 10, "OK"),
        _ev(63, T0 + 20, "NG", reason="缺步骤"),        # 当前窗口 3 件
    ])
    r = hub_client.get(f"{API}/stats/summary",
                       params={"start": T0 - 3600, "end": T0 + 3600},
                       headers=admin_headers)
    body = r.json()
    cur = body["current"]
    assert (cur["total"], cur["ok"], cur["ng"]) == (3, 2, 1)
    assert cur["yield_rate"] == round(2 / 3, 4)
    assert body["prev"]["total"] == 1
    assert body["stations_active"] == 1
    assert body["catching_up"] is False

    # 空窗口: 分母 0 → yield None (不是 0)
    r = hub_client.get(f"{API}/stats/summary",
                       params={"start": T0 + 86400, "end": T0 + 90000},
                       headers=admin_headers)
    cur = r.json()["current"]
    assert cur["total"] == 0 and cur["yield_rate"] is None


def test_timeseries_fills_gaps(hub_client, admin_headers, fake_node):
    """服务端填桶: 有产桶带数, 空桶 total=0 且 yield None (断线不插值)"""
    node = _inject_and_settle(hub_client, admin_headers, fake_node, [
        _ev(70, T0, "OK"), _ev(71, T0 + 7200, "NG", reason="x"),
    ])
    h0 = T0 - T0 % 3600
    r = hub_client.get(f"{API}/stats/timeseries",
                       params={"start": h0, "end": h0 + 3 * 3600,
                               "interval": "hour"},
                       headers=admin_headers)
    body = r.json()
    assert body["interval"] == "hour"
    b = {x["bucket_epoch"]: x for x in body["buckets"]}
    assert len(b) == 3, "三个连续桶都要出"
    assert b[h0]["total"] == 1 and b[h0]["yield_rate"] == 1.0
    assert b[h0 + 3600]["total"] == 0 and b[h0 + 3600]["yield_rate"] is None
    assert b[h0 + 7200]["ng"] == 1


def test_pareto_top_and_other(hub_client, admin_headers, fake_node):
    """Pareto 降序 + 累计占比 + Other 尾部合并"""
    events, eid = [], 80
    for reason, n in [("缺步骤", 5), ("扫码失败", 3), ("超时", 2), ("异物", 1)]:
        for _ in range(n):
            events.append(_ev(eid, T0 + eid, "NG", reason=reason))
            eid += 1
    node = _inject_and_settle(hub_client, admin_headers, fake_node, events)
    r = hub_client.get(f"{API}/stats/pareto",
                       params={"start": T0 - 3600, "end": T0 + 3600,
                               "top": 2},
                       headers=admin_headers)
    body = r.json()
    assert body["total_ng"] == 11
    assert [i["reason"] for i in body["items"]] == ["缺步骤", "扫码失败"]
    assert body["items"][0]["pct"] == round(5 / 11, 4)
    assert body["items"][1]["cum_pct"] == round(8 / 11, 4)
    assert body["other_count"] == 3


def test_ng_matrix_reason_by_station(hub_client, admin_headers, fake_node):
    """原因 × 工位交叉表: 谁的病一目了然"""
    node = _inject_and_settle(hub_client, admin_headers, fake_node, [
        _ev(90, T0, "NG", ch=0, reason="缺步骤"),
        _ev(91, T0 + 1, "NG", ch=0, reason="缺步骤"),
        _ev(92, T0 + 2, "NG", ch=1, reason="扫码失败"),
    ])
    r = hub_client.get(f"{API}/stats/ng-matrix",
                       params={"start": T0 - 3600, "end": T0 + 3600},
                       headers=admin_headers)
    body = r.json()
    assert body["reasons"] == ["缺步骤", "扫码失败"]
    chs = [s["channel_id"] for s in body["stations"]]
    i_ch0, i_ch1 = chs.index(0), chs.index(1)
    assert body["cells"][0][i_ch0] == 2 and body["cells"][0][i_ch1] == 0
    assert body["cells"][1][i_ch1] == 1


def test_stations_table_sort_and_spark(hub_client, admin_headers, fake_node):
    """工位表: 最差合格率在上, 无产出工位沉底但仍出行; spark 桶对齐"""
    node = _inject_and_settle(hub_client, admin_headers, fake_node, [
        _ev(100, T0, "OK", ch=0), _ev(101, T0 + 1, "OK", ch=0),
        _ev(102, T0 + 2, "NG", ch=1, reason="x"),
    ])
    r = hub_client.get(f"{API}/stats/stations",
                       params={"start": T0 - 3600, "end": T0 + 3600},
                       headers=admin_headers)
    body = r.json()
    items = body["items"]
    assert len(items) == 2, "两个纳管工位都要出行"
    assert items[0]["channel_id"] == 1, "合格率 0% 的排最上"
    assert items[0]["yield_rate"] == 0.0
    assert items[1]["yield_rate"] == 1.0
    # spark 与 spark_buckets 对齐
    assert len(items[0]["spark"]) == len(body["spark_buckets"])
    assert sum(items[1]["spark"]) == 2


def test_cycles_detail_filters(hub_client, admin_headers, fake_node):
    """明细分页: result 过滤 / 规整原因前缀命中 / 未知原因特判"""
    node = _inject_and_settle(hub_client, admin_headers, fake_node, [
        _ev(110, T0, "OK"),
        _ev(111, T0 + 1, "NG", reason="缺步骤"),
        _ev(112, T0 + 2, "NG", reason=None, event_name=None),
    ])
    params = {"start": T0 - 3600, "end": T0 + 3600}
    r = hub_client.get(f"{API}/stats/cycles",
                       params={**params, "result": "NG"},
                       headers=admin_headers)
    assert r.json()["total"] == 2
    r = hub_client.get(f"{API}/stats/cycles",
                       params={**params, "reason": "缺步骤"},
                       headers=admin_headers)
    assert r.json()["total"] == 1
    # "未知原因" 匹配双空行 —— OK 行天然双空, 样本表按惯例组合 result=NG
    r = hub_client.get(f"{API}/stats/cycles",
                       params={**params, "reason": "未知原因",
                               "result": "NG"},
                       headers=admin_headers)
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] and body["items"][0]["node_name"]


def test_stats_requires_login_but_operator_can_read(hub_client, admin_headers,
                                                    operator_headers,
                                                    fake_node):
    """权限: 未登录 401; operator (只读) 可看数据中心"""
    params = {"start": T0 - 3600, "end": T0 + 3600}
    r = hub_client.get(f"{API}/stats/summary", params=params)
    assert r.status_code == 401
    r = hub_client.get(f"{API}/stats/summary", params=params,
                       headers=operator_headers)
    assert r.status_code == 200
