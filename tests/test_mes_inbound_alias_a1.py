"""A1 入站接收路径别名: 启动/保存时动态注册根路径接收 URL, 支持增删改。

走 TestClient 端到端: PUT 配置带 receive_paths → 别名即时生效 → 清空后即时移除。
"""
import pytest


@pytest.fixture
def alias_cfg(client):
    """配置一组别名路径 (task_start / alarm_clear / health), 测试后复位清空。"""
    client.put("/api/v1/mes/inbound/config", json={
        "enabled": True,
        "switch_project_on_task": False,
        "echo_fields": ["task_no"],
        "receive_paths": [
            {"path": "/task/start2", "action": "task_start", "methods": ["POST", "GET"]},
            {"path": "/warning/clear", "action": "alarm_clear", "methods": ["POST"]},
            {"path": "/probe", "action": "health", "methods": ["GET"]},
        ],
    })
    yield
    client.put("/api/v1/mes/inbound/config",
               json={"enabled": False, "receive_paths": []})


def test_alias_task_start_routes(client, alias_cfg):
    r = client.post("/task/start2",
                    json={"TaskNo": "ALIAS-1", "ProductCode": "P1"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["task_no"] == "ALIAS-1"   # echo_fields 回显, 确实进了 task_start


def test_alias_health_routes(client, alias_cfg):
    r = client.get("/probe")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["message"] == "ok"


def test_alias_alarm_clear_routes(client, alias_cfg):
    # 无匹配在途报警 → 返回 alarm_not_found 业务码, 但确实路由到了 alarm_clear 处理
    r = client.post("/warning/clear",
                    json={"TaskNo": "NO-SUCH", "ProductCode": "P9"})
    assert r.status_code == 200
    assert "code" in r.json()


def test_alias_removed_after_clear(client, alias_cfg):
    # 先确认存在
    assert client.post("/task/start2",
                       json={"TaskNo": "T", "ProductCode": "P"}).status_code == 200
    # 清空 receive_paths → 别名应被移除
    client.put("/api/v1/mes/inbound/config",
               json={"enabled": True, "receive_paths": []})
    r = client.post("/task/start2", json={"TaskNo": "T", "ProductCode": "P"})
    assert r.status_code == 404   # 别名已移除


def test_alias_rejects_api_prefix(client):
    """/api/ 前缀别名被拒, 不劫持主程序 API。"""
    client.put("/api/v1/mes/inbound/config", json={
        "enabled": True,
        "receive_paths": [{"path": "/api/v1/hijack", "action": "task_start"}],
    })
    try:
        r = client.post("/api/v1/hijack", json={"TaskNo": "X", "ProductCode": "Y"})
        assert r.status_code == 404   # 未注册
    finally:
        client.put("/api/v1/mes/inbound/config",
                   json={"enabled": False, "receive_paths": []})
