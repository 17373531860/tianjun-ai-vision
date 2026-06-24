"""M2 入站接收器纯逻辑单测 (不走 HTTP)。"""
from backend.services.mes_inbound import MESInbound, DEFAULT_INBOUND_CONFIG


def _cfg(**over):
    cfg = MESInbound._with_defaults({})
    cfg.update(over)
    return cfg


# ==================== 字段映射 ====================
def test_map_fields_flat():
    svc = MESInbound()
    body = {"TaskNo": "T1", "ProductCode": "P9", "Operator": "张三"}
    fm = {"task_no": "TaskNo", "product_code": "ProductCode", "operator": "Operator"}
    out = svc._map_fields(body, fm)
    assert out == {"task_no": "T1", "product_code": "P9", "operator": "张三"}


def test_map_fields_nested_dotted_path():
    svc = MESInbound()
    body = {"data": {"order": {"no": "T2"}}}
    out = svc._map_fields(body, {"task_no": "data.order.no"})
    assert out == {"task_no": "T2"}


def test_map_fields_missing_external_key_omitted():
    svc = MESInbound()
    out = svc._map_fields({"TaskNo": "T1"}, {"task_no": "TaskNo", "product_code": "ProductCode"})
    assert out == {"task_no": "T1"}  # product_code 外部没给 → 不出现


# ==================== 配置默认值 / 深合并 ====================
def test_with_defaults_partial_codes_merge():
    cfg = MESInbound._with_defaults({"response": {"codes": {"duplicate": 99999}}})
    assert cfg["response"]["codes"]["duplicate"] == 99999          # 用户覆盖
    assert cfg["response"]["codes"]["missing_field"] == 40004      # 默认保留
    assert cfg["response"]["success_code"] == 0


def test_with_defaults_empty_returns_full_defaults():
    cfg = MESInbound._with_defaults({})
    assert cfg["field_map"]["task_no"] == "TaskNo"
    assert cfg["enabled"] is False
    # 与上银包装线对齐: 自动切项目默认关
    assert cfg["switch_project_on_task"] is False


# ==================== handle_task_start ====================
def test_disabled_returns_disabled_code():
    svc = MESInbound()
    res = svc.handle_task_start(None, {"TaskNo": "T1"}, _cfg(enabled=False))
    assert res["ok"] is False
    assert res["code_key"] == "disabled"
    assert res["response"]["code"] == 40006


def test_missing_required_field():
    svc = MESInbound()
    # 只给 task_no, 缺 product_code (默认必填)
    res = svc.handle_task_start(None, {"TaskNo": "T1"}, _cfg(enabled=True))
    assert res["ok"] is False
    assert res["code_key"] == "missing_field"
    assert res["response"]["code"] == 40004
    assert "product_code" in res["response"]["message"]


def test_success_path():
    svc = MESInbound()
    body = {"TaskNo": "T1", "ProductCode": "P9"}
    # 关掉"开工切项目", 单测此处只验响应组装+映射 (切项目逻辑由 M3 测试覆盖)
    res = svc.handle_task_start(None, body, _cfg(enabled=True, switch_project_on_task=False))
    assert res["ok"] is True
    assert res["code_key"] == "success"
    assert res["response"]["code"] == 0
    assert res["response"]["message"] == "OK"
    assert res["mapped"]["task_no"] == "T1"


def test_action_failure_maps_to_error_code(monkeypatch):
    svc = MESInbound()
    monkeypatch.setattr(svc, "_apply_task_action",
                        lambda db, mapped, cfg: (False, "unknown_product", "产品码未配置项目"))
    res = svc.handle_task_start(None, {"TaskNo": "T1", "ProductCode": "PX"}, _cfg(enabled=True))
    assert res["ok"] is False
    assert res["code_key"] == "unknown_product"
    assert res["response"]["code"] == 40002


def test_action_exception_falls_to_internal_error(monkeypatch):
    svc = MESInbound()
    def _boom(db, mapped, cfg):
        raise RuntimeError("db down")
    monkeypatch.setattr(svc, "_apply_task_action", _boom)
    res = svc.handle_task_start(None, {"TaskNo": "T1", "ProductCode": "P1"}, _cfg(enabled=True))
    assert res["code_key"] == "internal_error"
    assert res["response"]["code"] == 40006


# ==================== 响应组装可配置 ====================
def test_custom_response_envelope():
    svc = MESInbound()
    cfg = _cfg(enabled=True, switch_project_on_task=False, response={
        "code_field": "resultCode", "message_field": "resultMsg",
        "success_code": 200, "success_message": "成功",
        "codes": {"missing_field": 1001},
    })
    res = svc.handle_task_start(None, {"TaskNo": "T1", "ProductCode": "P1"}, cfg)
    assert res["response"] == {"resultCode": 200, "resultMsg": "成功"}


def test_echo_fields_appended():
    svc = MESInbound()
    cfg = _cfg(enabled=True, switch_project_on_task=False, echo_fields=["task_no"])
    res = svc.handle_task_start(None, {"TaskNo": "T7", "ProductCode": "P1"}, cfg)
    assert res["response"]["task_no"] == "T7"
