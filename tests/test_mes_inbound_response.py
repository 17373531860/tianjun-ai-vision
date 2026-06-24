"""入站响应信封测试: 扁平 / 嵌套点路径 / 自研模板。

对应自审漏洞 #2: 响应从只能扁平扩到可嵌套 + 全自定义模板 (兼容 SOAP/复杂协议)。
"""
from backend.services.mes_inbound import MESInbound


def _cfg(**over):
    base = {"enabled": True}
    base.update(over)
    return MESInbound._with_defaults(base)


def test_flat_default():
    cfg = _cfg()
    resp = MESInbound.build_response(cfg, "success", "OK", {"task_no": "T1"})
    assert resp["code"] == 0
    assert resp["message"] == "OK"


def test_nested_code_field():
    cfg = _cfg(response={"code_field": "head.code", "message_field": "head.msg",
                         "success_code": 0})
    resp = MESInbound.build_response(cfg, "success", "OK")
    assert resp == {"head": {"code": 0, "msg": "OK"}}


def test_nested_failure_code():
    cfg = _cfg(response={"code_field": "head.code", "message_field": "head.msg",
                         "codes": {"missing_field": 40004}})
    resp = MESInbound.build_response(cfg, "missing_field", "缺字段")
    assert resp["head"]["code"] == 40004


def test_template_envelope():
    cfg = _cfg(response={
        "success_code": 0,
        "template": {
            "resultCode": "{code}",
            "resultMsg": "{message}",
            "data": {"taskNo": "{task_no}", "ok": "{success}"},
        },
    })
    resp = MESInbound.build_response(cfg, "success", "OK", {"task_no": "T9"})
    assert resp["resultCode"] == 0          # {code} 全匹配保留 int
    assert resp["resultMsg"] == "OK"
    assert resp["data"]["taskNo"] == "T9"


def test_template_with_array():
    """模板支持数组展开 (复用出站引擎)。"""
    cfg = _cfg(response={
        "success_code": 0,
        "template": {
            "code": "{code}",
            "items": {"_array_source": "rows",
                      "_item_template": {"v": "{item}"}},
        },
    })
    resp = MESInbound.build_response(cfg, "success", "OK", {"rows": [1, 2, 3]})
    assert resp["code"] == 0
    # {item} 全匹配保留原类型 (int)
    assert resp["items"] == [{"v": 1}, {"v": 2}, {"v": 3}]


def test_template_echo_appended():
    cfg = _cfg(echo_fields=["task_no"], response={
        "success_code": 0, "template": {"code": "{code}"}})
    resp = MESInbound.build_response(cfg, "success", "OK", {"task_no": "E1"})
    assert resp["task_no"] == "E1"
