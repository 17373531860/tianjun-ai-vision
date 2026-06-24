"""入站 XML/SOAP 响应体输出测试 (残留 1)。"""
from backend.services.mes_inbound import to_xml, MESInbound


def _enable(client, **over):
    cfg = {"enabled": True, "switch_project_on_task": False}
    cfg.update(over)
    client.put("/api/v1/mes/inbound/config", json=cfg)


def _disable(client):
    client.put("/api/v1/mes/inbound/config", json={"enabled": False})


# ---- to_xml 纯函数 ----
def test_to_xml_flat():
    xml = to_xml({"code": 0, "message": "OK"}, "response", False)
    assert xml == "<response><code>0</code><message>OK</message></response>"


def test_to_xml_nested_and_list():
    xml = to_xml({"head": {"code": 0}, "items": [1, 2]}, "resp", False)
    assert "<head><code>0</code></head>" in xml
    assert "<items>1</items><items>2</items>" in xml


def test_to_xml_escape_and_none():
    xml = to_xml({"m": "a&b<c", "x": None}, "r", False)
    assert "a&amp;b&lt;c" in xml
    assert "<x/>" in xml


def test_to_xml_declaration():
    xml = to_xml({"code": 0}, "r", True)
    assert xml.startswith("<?xml version=\"1.0\" encoding=\"UTF-8\"?>")


# ---- HTTP 端到端: 响应体为 XML ----
def test_http_xml_response(client):
    _enable(client, response={"format": "xml", "xml_root": "Result",
                              "code_field": "code", "message_field": "msg",
                              "success_code": 0})
    try:
        r = client.post("/api/v1/mes/inbound/task",
                        json={"TaskNo": "XR-1", "ProductCode": "P1"})
        assert r.status_code == 200
        assert "xml" in r.headers.get("content-type", "")
        assert "<Result>" in r.text
        assert "<code>0</code>" in r.text
        assert "<msg>OK</msg>" in r.text
    finally:
        _disable(client)


def test_http_xml_with_template(client):
    _enable(client, response={
        "format": "xml", "xml_root": "Envelope", "success_code": 0,
        "template": {"Body": {"resultCode": "{code}", "taskNo": "{task_no}"}},
    }, echo_fields=[])
    try:
        r = client.post("/api/v1/mes/inbound/task",
                        json={"TaskNo": "XR-2", "ProductCode": "P1"})
        assert "<Envelope><Body>" in r.text
        assert "<resultCode>0</resultCode>" in r.text
        assert "<taskNo>XR-2</taskNo>" in r.text
    finally:
        _disable(client)
