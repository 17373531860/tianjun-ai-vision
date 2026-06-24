"""入站多编码格式测试: JSON / 表单 / URL 参数 / GET / XML / query 并入。

对应自审漏洞 #1: 入站从只认 JSON 扩到全格式可选。
"""
import pytest


def _enable(client, **over):
    cfg = {"enabled": True, "echo_fields": ["task_no"], "switch_project_on_task": False}
    cfg.update(over)
    client.put("/api/v1/mes/inbound/config", json=cfg)


def _disable(client):
    client.put("/api/v1/mes/inbound/config", json={"enabled": False})


def test_form_urlencoded_auto(client):
    """表单 urlencoded, auto 按 Content-Type 识别。"""
    _enable(client)
    try:
        r = client.post("/api/v1/mes/inbound/task",
                        data={"TaskNo": "FORM-1", "ProductCode": "P1"})
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["task_no"] == "FORM-1"
    finally:
        _disable(client)


def test_form_explicit(client):
    """显式 input_format=form。"""
    _enable(client, input_format="form")
    try:
        r = client.post("/api/v1/mes/inbound/task",
                        data={"TaskNo": "FORM-2", "ProductCode": "P1"})
        assert r.json()["code"] == 0
    finally:
        _disable(client)


def test_get_query_params(client):
    """GET + URL 参数 即报文。"""
    _enable(client)
    try:
        r = client.get("/api/v1/mes/inbound/task",
                       params={"TaskNo": "GET-1", "ProductCode": "P1"})
        assert r.status_code == 200
        assert r.json()["code"] == 0
        assert r.json()["task_no"] == "GET-1"
    finally:
        _disable(client)


def test_query_format_post(client):
    """input_format=query: POST 但取 URL 参数。"""
    _enable(client, input_format="query")
    try:
        r = client.post("/api/v1/mes/inbound/task?TaskNo=Q-1&ProductCode=P1")
        assert r.json()["code"] == 0
        assert r.json()["task_no"] == "Q-1"
    finally:
        _disable(client)


def test_merge_query_into_json(client):
    """JSON body 缺字段, 由 URL query 补 (merge_query_params)。"""
    _enable(client, merge_query_params=True)
    try:
        r = client.post("/api/v1/mes/inbound/task?TaskNo=MERGE-1",
                        json={"ProductCode": "P1"})
        assert r.json()["code"] == 0
        assert r.json()["task_no"] == "MERGE-1"
    finally:
        _disable(client)


def test_xml_soap_like(client):
    """XML 报文 + 自定义字段路径 (剥命名空间, 走信封路径)。"""
    _enable(client, input_format="xml", field_map={
        "task_no": "Task.TaskNo", "product_code": "Task.ProductCode"})
    try:
        xml = b"<Task><TaskNo>XML-1</TaskNo><ProductCode>P1</ProductCode></Task>"
        r = client.post("/api/v1/mes/inbound/task", content=xml,
                        headers={"Content-Type": "text/xml"})
        assert r.status_code == 200
        assert r.json()["code"] == 0
        assert r.json()["task_no"] == "XML-1"
    finally:
        _disable(client)


def test_xml_malformed_bad_request(client):
    _enable(client, input_format="xml",
            field_map={"task_no": "Task.TaskNo", "product_code": "Task.ProductCode"})
    try:
        r = client.post("/api/v1/mes/inbound/task", content=b"<Task><broken>",
                        headers={"Content-Type": "text/xml"})
        assert r.json()["code"] == 40005  # bad_request
    finally:
        _disable(client)
