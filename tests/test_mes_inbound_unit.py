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


# ==================== 开工后自动开始检测 (v3.37 川南) ====================
def test_start_detection_on_task_default_off(monkeypatch):
    """默认关: 开工成功也不触碰检测启动。"""
    svc = MESInbound()
    called = []
    monkeypatch.setattr(svc, "_auto_start_detection", lambda: called.append(1) or [])
    res = svc.handle_task_start(None, {"TaskNo": "T1", "ProductCode": "P1"},
                                _cfg(enabled=True, switch_project_on_task=False))
    assert res["ok"] is True
    assert called == []


def test_start_detection_on_task_enabled_calls_helper(monkeypatch):
    """开关开: 任务处理成功后调用自动开始检测, 拉起结果进 message。"""
    svc = MESInbound()
    monkeypatch.setattr(svc, "_auto_start_detection", lambda: ["ch0", "ch1"])
    cfg = _cfg(enabled=True, switch_project_on_task=False,
               start_detection_on_task=True)
    res = svc.handle_task_start(None, {"TaskNo": "T1", "ProductCode": "P1"}, cfg)
    assert res["ok"] is True


def test_auto_start_detection_gates_and_isolation():
    """门槛: 源没跑/模型没就绪/已在检测的通道都跳过; 单通道异常不拖垮整体。"""
    from unittest.mock import MagicMock, patch

    ok_mgr = MagicMock(is_running=True, is_detecting=False)
    not_running = MagicMock(is_running=False, is_detecting=False)
    already = MagicMock(is_running=True, is_detecting=True)
    boom = MagicMock(is_running=True, is_detecting=False)
    boom.start_detection.side_effect = RuntimeError("未加载模型")

    fake_cm = MagicMock()
    fake_cm.channels = {0: ok_mgr, 1: not_running, 2: already, 3: boom}
    with patch("backend.api.channel_manager.channel_manager", fake_cm):
        started = MESInbound()._auto_start_detection()

    assert started == ["ch0"]
    ok_mgr.start_detection.assert_called_once()
    not_running.start_detection.assert_not_called()
    already.start_detection.assert_not_called()
    boom.start_detection.assert_called_once()  # 异常被隔离, 不进 started


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


# ==================== 完工真值词表 (A7) ====================
def test_truthy_default_words():
    assert MESInbound._truthy("完工") is True
    assert MESInbound._truthy("YES") is True       # 大小写不敏感
    assert MESInbound._truthy("done") is False     # 不在默认表


def test_truthy_bool_and_number():
    assert MESInbound._truthy(True) is True
    assert MESInbound._truthy(1) is True
    assert MESInbound._truthy(0) is False
    assert MESInbound._truthy(None) is False


def test_truthy_custom_words():
    words = ["done", "结束"]
    assert MESInbound._truthy("done", words) is True
    assert MESInbound._truthy("结束", words) is True
    # 自定义表后默认词不再命中
    assert MESInbound._truthy("完工", words) is False
    assert MESInbound._truthy("yes", words) is False


def test_truthy_empty_words_falls_back():
    # 空表 → 回落默认
    assert MESInbound._truthy("完工", []) is True


# ==================== 响应文案覆盖 (A6) ====================
def test_response_message_override():
    cfg = _cfg()
    cfg["response"]["messages"] = {"missing_field": "自定义缺字段提示"}
    resp = MESInbound.build_response(cfg, "missing_field", "缺少必填字段: a")
    assert resp["message"] == "自定义缺字段提示"


def test_response_message_no_override_keeps_default():
    cfg = _cfg()
    resp = MESInbound.build_response(cfg, "missing_field", "缺少必填字段: a")
    assert resp["message"] == "缺少必填字段: a"


def test_response_message_empty_override_ignored():
    cfg = _cfg()
    cfg["response"]["messages"] = {"missing_field": ""}
    resp = MESInbound.build_response(cfg, "missing_field", "缺少必填字段: a")
    assert resp["message"] == "缺少必填字段: a"  # 空覆盖忽略, 用默认


def test_response_success_message_override():
    cfg = _cfg()
    cfg["response"]["messages"] = {"success": "已受理"}
    resp = MESInbound.build_response(cfg, "success", "ok")
    assert resp["message"] == "已受理"
