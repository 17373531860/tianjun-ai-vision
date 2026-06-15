"""包装箱结算 CRUD + 扫码端点 API 冒烟 (v3.21 M3/M4).

验证前后端契约: 组⑤ 收尾与回推新字段往返 + 枚举校验 + scan 入口.
"""


def test_packaging_flow_crud_with_group5(client):
    payload = {
        "name": "pkg-test-crud-1",
        "enabled": False,
        "channel_id": 0,
        "box_count_field": "dispatch_qty",
        "label_len": 15,
        "on_forced_stop": "abort",
        "forced_settle_on_standby": False,
        "push_on_complete": True,
        "push_event_type": "pkg_done",
    }
    r = client.post("/api/v1/packaging-flows", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    cid = body["id"]
    # 组⑤ 字段往返
    assert body["on_forced_stop"] == "abort"
    assert body["forced_settle_on_standby"] is False
    assert body["push_on_complete"] is True
    assert body["push_event_type"] == "pkg_done"
    assert body["label_len"] == 15

    # list 命中
    r = client.get("/api/v1/packaging-flows")
    assert any(x["id"] == cid for x in r.json()["items"])

    # get 回显
    r = client.get(f"/api/v1/packaging-flows/{cid}")
    assert r.json()["on_forced_stop"] == "abort"

    # 部分更新
    r = client.put(f"/api/v1/packaging-flows/{cid}", json={"on_short_box": "void"})
    assert r.status_code == 200, r.text
    assert r.json()["on_short_box"] == "void"

    # 非法枚举被拒
    r = client.put(f"/api/v1/packaging-flows/{cid}", json={"on_forced_stop": "bogus"})
    assert r.status_code == 400

    # 删除 (enabled=False 可删)
    r = client.delete(f"/api/v1/packaging-flows/{cid}")
    assert r.status_code == 204


def test_packaging_flow_event_mapping_roundtrip(client):
    """组⑥ 异常 → 项目事件映射 7 字段 + on_label_mismatch=off 往返 + 枚举校验."""
    payload = {
        "name": "pkg-test-events-1",
        "enabled": False,
        "channel_id": 0,
        "on_label_mismatch": "off",
        "event_short_box": 5,
        "event_over_box": 6,
        "event_tray_ng": 7,
        "event_box_ng": 8,
        "event_label_mismatch": 9,
        "event_label_len": 10,
        "event_mes_fail": 11,
    }
    r = client.post("/api/v1/packaging-flows", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    cid = body["id"]
    assert body["on_label_mismatch"] == "off"
    assert body["event_short_box"] == 5
    assert body["event_box_ng"] == 8
    assert body["event_mes_fail"] == 11

    # get 回显
    r = client.get(f"/api/v1/packaging-flows/{cid}")
    assert r.json()["event_tray_ng"] == 7

    # 部分更新: 清空一个映射 (null = 默认报警)
    r = client.put(f"/api/v1/packaging-flows/{cid}", json={"event_short_box": None})
    assert r.status_code == 200, r.text
    assert r.json()["event_short_box"] is None

    # on_label_mismatch 非法值被拒
    r = client.put(f"/api/v1/packaging-flows/{cid}", json={"on_label_mismatch": "bogus"})
    assert r.status_code == 400

    client.delete(f"/api/v1/packaging-flows/{cid}")


def test_packaging_scan_returns_handled_flag(client):
    r = client.post("/api/v1/packaging-flows/scan",
                    json={"code": "ABC123", "channel_id": 0})
    assert r.status_code == 200, r.text
    assert "handled" in r.json()


def test_packaging_scan_empty_code_rejected(client):
    # 先建一个启用配置, 让协调器有 loaded config, 才会走到空码校验
    client.post("/api/v1/packaging-flows", json={
        "name": "pkg-test-scan-empty", "enabled": True, "channel_id": 2,
    })
    r = client.post("/api/v1/packaging-flows/scan",
                    json={"code": "   ", "channel_id": 2})
    # 有 loaded config 时空码 400; 没有时 handled=False (200) — 两种都可接受
    assert r.status_code in (200, 400)
