"""入站业务结果 → HTTP 状态码映射测试。

对应自审漏洞 #3: 默认一律 200, 可选把失败映射到非 2xx。
"""


def _enable(client, **over):
    cfg = {"enabled": True, "switch_project_on_task": False}
    cfg.update(over)
    client.put("/api/v1/mes/inbound/config", json=cfg)


def _disable(client):
    client.put("/api/v1/mes/inbound/config", json={"enabled": False})


def test_default_failure_still_200(client):
    _enable(client)
    try:
        r = client.post("/api/v1/mes/inbound/task", json={"TaskNo": "S1"})  # 缺产品码
        assert r.status_code == 200
        assert r.json()["code"] == 40004
    finally:
        _disable(client)


def test_missing_field_maps_to_400(client):
    _enable(client, http_status_map={"missing_field": 400})
    try:
        r = client.post("/api/v1/mes/inbound/task", json={"TaskNo": "S2"})
        assert r.status_code == 400
        assert r.json()["code"] == 40004
    finally:
        _disable(client)


def test_success_still_200_when_only_failures_mapped(client):
    _enable(client, http_status_map={"internal_error": 500})
    try:
        r = client.post("/api/v1/mes/inbound/task",
                        json={"TaskNo": "S3", "ProductCode": "P1"})
        assert r.status_code == 200
        assert r.json()["code"] == 0
    finally:
        _disable(client)


def test_disabled_maps_to_503(client):
    # 先配好 503 映射, 再关闭功能
    client.put("/api/v1/mes/inbound/config",
               json={"enabled": False, "http_status_map": {"disabled": 503}})
    r = client.post("/api/v1/mes/inbound/task",
                    json={"TaskNo": "S4", "ProductCode": "P1"})
    assert r.status_code == 503
    assert r.json()["code"] == 40006
    client.put("/api/v1/mes/inbound/config", json={"http_status_map": {}})


def test_bad_request_maps_to_422(client):
    _enable(client, http_status_map={"bad_request": 422})
    try:
        r = client.post("/api/v1/mes/inbound/task", content=b"{bad",
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 422
        assert r.json()["code"] == 40005
    finally:
        _disable(client)
        client.put("/api/v1/mes/inbound/config", json={"http_status_map": {}})
