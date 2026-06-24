"""入站来源校验测试: 共享密钥头 + IP 白名单 (默认关)。

对应自审漏洞 #4。
"""
from backend.services.mes_inbound import check_inbound_auth, _ip_allowed


class _FakeReq:
    def __init__(self, headers=None, host="1.2.3.4"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": host})()


def _enable(client, **over):
    cfg = {"enabled": True, "switch_project_on_task": False}
    cfg.update(over)
    client.put("/api/v1/mes/inbound/config", json=cfg)


def _disable(client):
    client.put("/api/v1/mes/inbound/config", json={"enabled": False, "auth": {"enabled": False}})


# ---- 纯逻辑 ----
def test_auth_off_passes():
    assert check_inbound_auth(_FakeReq(), {"auth": {"enabled": False}}) is None


def test_secret_header_match():
    cfg = {"auth": {"enabled": True, "header_name": "X-Key", "header_value": "s3cr3t"}}
    assert check_inbound_auth(_FakeReq(headers={"X-Key": "s3cr3t"}), cfg) is None
    assert check_inbound_auth(_FakeReq(headers={"X-Key": "wrong"}), cfg) is not None
    assert check_inbound_auth(_FakeReq(headers={}), cfg) is not None


def test_ip_whitelist_exact_and_cidr():
    assert _ip_allowed("192.168.1.10", ["192.168.1.10"]) is True
    assert _ip_allowed("192.168.1.10", ["192.168.1.0/24"]) is True
    assert _ip_allowed("10.0.0.5", ["192.168.1.0/24"]) is False
    assert _ip_allowed(None, ["192.168.1.0/24"]) is False


# ---- HTTP 端到端 (TestClient 的 client.host == 'testclient') ----
def test_api_secret_required(client):
    _enable(client, auth={"enabled": True, "header_name": "X-Key", "header_value": "abc"})
    try:
        # 不带密钥 → 401 + unauthorized 码
        r = client.post("/api/v1/mes/inbound/task",
                        json={"TaskNo": "A1", "ProductCode": "P1"})
        assert r.status_code == 401
        assert r.json()["code"] == 40007
        # 带正确密钥 → 放行
        r2 = client.post("/api/v1/mes/inbound/task",
                         json={"TaskNo": "A2", "ProductCode": "P1"},
                         headers={"X-Key": "abc"})
        assert r2.status_code == 200
        assert r2.json()["code"] == 0
    finally:
        _disable(client)


def test_api_ip_whitelist_blocks(client):
    # 白名单只放真实 IP, TestClient 的 testclient 不在内 → 拦
    _enable(client, auth={"enabled": True, "ip_whitelist": ["1.2.3.4"]})
    try:
        r = client.post("/api/v1/mes/inbound/task",
                        json={"TaskNo": "A3", "ProductCode": "P1"})
        assert r.status_code == 401
    finally:
        _disable(client)


def test_api_ip_whitelist_allows_testclient(client):
    _enable(client, auth={"enabled": True, "ip_whitelist": ["testclient"]})
    try:
        r = client.post("/api/v1/mes/inbound/task",
                        json={"TaskNo": "A4", "ProductCode": "P1"})
        assert r.status_code == 200
        assert r.json()["code"] == 0
    finally:
        _disable(client)
