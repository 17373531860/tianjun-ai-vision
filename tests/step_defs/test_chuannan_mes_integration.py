"""川南火工 MES 双向对接全流程 BDD step 实现。

把《川南火工对接 配置操作手册》里 14 个用户场景表达成可执行 gherkin, 服务层直驱,
进 CI 当回归护栏 (不起服务)。出站真实投递到中控由 tests/uat 里的可见浏览器 UAT 覆盖,
本文件聚焦"后端业务码 + 工单状态 + 在途报警台账 + 出站报文格式"的契约。
"""
from __future__ import annotations

import base64

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

from backend.db.database import SessionLocal
from backend.models.models import Project
from backend.models.mes_models import WorkOrder, ExternalActiveAlarm
from backend.services.mes_inbound import MESInbound
from backend.services import external_alarm
from backend.services.mes_adapters.base import render_template


scenarios("../features/chuannan_mes_integration.feature")


# ============================================================
# 工具
# ============================================================
def _cfg(**over):
    base = {"enabled": True}
    base.update(over)
    return MESInbound._with_defaults(base)


def _code(cfg, result):
    """从 handle_* 返回值里取川南业务码 (response.code)。"""
    resp = result.get("response", result)
    cf = (cfg.get("response") or {}).get("code_field", "code")
    return resp.get(cf)


def _get_order(order_no):
    db = SessionLocal()
    try:
        return db.query(WorkOrder).filter(WorkOrder.order_no == order_no).first()
    finally:
        db.close()


def _start(cfg, body):
    """跑一次开工, 提交, 返回 result。"""
    svc = MESInbound()
    db = SessionLocal()
    try:
        res = svc.handle_task_start(db, body, cfg)
        db.commit()
        return res
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _wipe_cn():
    """前后各清一次本组产物 (CN- 前缀工单 / 项目 / 在途报警) + 复位激活态。"""
    def _wipe():
        db = SessionLocal()
        try:
            db.query(WorkOrder).filter(WorkOrder.order_no.like("CN-%")).delete(
                synchronize_session=False)
            db.query(ExternalActiveAlarm).filter(
                ExternalActiveAlarm.task_no.like("CN-%")).delete(
                synchronize_session=False)
            db.query(Project).filter(Project.name.like("CN-%")).delete(
                synchronize_session=False)
            db.query(Project).update({Project.is_active: False})
            db.commit()
        finally:
            db.close()
    _wipe()
    yield
    _wipe()


# ============================================================
# Given — 项目 / 配置 / 报警预置
# ============================================================
@given(parsers.parse('存在名为 "{name}" 的检测项目'))
def given_project(name):
    db = SessionLocal()
    try:
        db.add(Project(name=name, task_type="detection", logic_mode="sequential"))
        db.commit()
    finally:
        db.close()


@given(parsers.parse('入站配置为 "{preset}"'))
def given_cfg(ctx, preset, monkeypatch):
    if preset == "开工切项目并建单":
        ctx["cfg"] = _cfg(switch_project_on_task=True, match_project_by_name=True,
                          create_work_order_on_task=True)
    elif preset == "必填任务号":
        ctx["cfg"] = _cfg(required_fields=["task_no"])
    elif preset == "顶替并回传完工":
        calls = []

        class _FakeGW:
            def dispatch(self, event_type, context, channel_id=None):
                calls.append((event_type, context))

        monkeypatch.setattr("backend.services.mes_gateway.get_mes_gateway",
                            lambda: _FakeGW())
        ctx["gw_calls"] = calls
        ctx["cfg"] = _cfg(create_work_order_on_task=True,
                          supersede_previous_task=True,
                          report_complete_on_supersede=True,
                          complete_event_type="task_complete")
    elif preset == "拒绝重复任务":
        ctx["cfg"] = _cfg(create_work_order_on_task=True, reject_duplicate_task=True)
    elif preset == "未启用":
        ctx["cfg"] = MESInbound._with_defaults({"enabled": False})
    elif preset == "四要素上屏并切项目建单":
        ctx["cfg"] = _cfg(switch_project_on_task=True, match_project_by_name=True,
                          create_work_order_on_task=True, store_mapped_extra=True,
                          task_info_display={"show_task_no": True, "show_product_code": True,
                                             "show_step_code": True, "show_operator": True})
    elif preset == "开工自动开始检测":
        ctx["cfg"] = _cfg(switch_project_on_task=True, match_project_by_name=True,
                          create_work_order_on_task=True,
                          start_detection_on_task=True)
    else:  # 默认
        ctx["cfg"] = _cfg()


@given("工位0 视频源在跑且未在检测")
def given_ch0_running_not_detecting(ctx, monkeypatch):
    """伪造 channel_manager: ch0 源在跑、未在检测 (BDD 层不真起视频源)。"""
    from unittest.mock import MagicMock

    mgr = MagicMock(is_running=True, is_detecting=False)
    fake_cm = MagicMock()
    fake_cm.channels = {0: mgr}
    monkeypatch.setattr("backend.api.channel_manager.channel_manager", fake_cm)
    ctx["ch0_mgr"] = mgr


@given(parsers.parse(
    '一条 NG 报警已登记 任务 "{task}" 产品 "{product}" 工步 "{step}" 操作员 "{op}"'))
def given_alarm(task, product, step, op):
    db = SessionLocal()
    try:
        external_alarm.record_active_alarm(
            db, {"task_no": task, "product_code": product,
                 "step_code": step, "operator": op, "warning_text": "缺件"},
            event_type="ng", channel_id=0)
        db.commit()
    finally:
        db.close()


@given(parsers.parse('去重窗口为 {sec:d} 秒'))
def given_dedup(ctx, sec):
    ctx["dedup_sec"] = sec


@given(parsers.parse(
    '一条 NG 周期上下文 任务 "{task}" 产品 "{product}" 工步 "{step}" 操作员 "{op}"'))
def given_ng_context(ctx, task, product, step, op):
    ctx["ng_ctx"] = {
        "order": {"order_no": task, "product_code": product,
                  "extra_data": {"inbound": {"step_code": step, "operator": op}}},
        "cycle": {"ng_reason": "缺件", "result": "NG"},
        "snapshot": {"image_base64": base64.b64encode(b"\xff\xd8\xfffakejpeg").decode()},
    }


# ============================================================
# When — 入站动作
# ============================================================
@when(parsers.parse(
    '上游发来开工 任务 "{task}" 产品 "{product}" 工步 "{step}" 操作员 "{op}"'))
def when_start(ctx, task, product, step, op):
    ctx["result"] = _start(ctx["cfg"], {"TaskNo": task, "ProductCode": product,
                                        "StepCode": step, "Operator": op})


@when("上游发来缺任务号的开工报文")
def when_start_missing(ctx):
    ctx["result"] = _start(ctx["cfg"], {"ProductCode": "CN-X", "Operator": "张三"})


@when(parsers.parse('上游先后发来开工任务 "{a}" 和 "{b}"'))
def when_two_starts(ctx, a, b):
    for t in (a, b):
        _start(ctx["cfg"], {"TaskNo": t, "ProductCode": "P1"})


@when(parsers.parse('上游两次发来同一开工任务 "{task}"'))
def when_dup_starts(ctx, task):
    _start(ctx["cfg"], {"TaskNo": task, "ProductCode": "P1"})
    ctx["result"] = _start(ctx["cfg"], {"TaskNo": task, "ProductCode": "P1"})


@when("报文无法解析")
def when_bad_request(ctx):
    svc = MESInbound()
    ctx["result"] = svc.error_response(ctx["cfg"], "bad_request", "报文无法解析")


@when("上游探活")
def when_health(ctx):
    svc = MESInbound()
    ctx["result"] = svc.build_response(ctx["cfg"], "success", "ok")


@when(parsers.parse(
    '同一报警在窗口内登记两次 任务 "{task}" 工步 "{step}" 操作员 "{op}"'))
def when_dup_alarm(ctx, task, step, op):
    sec = ctx.get("dedup_sec", 5)
    fields = {"task_no": task, "product_code": "CN-P1",
              "step_code": step, "operator": op, "warning_text": "缺件"}
    db = SessionLocal()
    try:
        external_alarm.record_active_alarm(db, fields, event_type="ng",
                                           channel_id=0, dedup_sec=sec)
        external_alarm.record_active_alarm(db, fields, event_type="ng",
                                           channel_id=0, dedup_sec=sec)
        db.commit()
    finally:
        db.close()


@when(parsers.parse(
    '中控发来消除命令 任务 "{task}" 工步 "{step}" 操作员 "{op}"'))
def when_clear(ctx, task, step, op):
    svc = MESInbound()
    db = SessionLocal()
    try:
        ctx["result"] = svc.handle_alarm_clear(
            db, {"TaskNo": task, "StepCode": step, "Operator": op}, ctx["cfg"])
        db.commit()
    finally:
        db.close()


@when("套用川南报警上报预设渲染报文")
def when_render_preset(ctx):
    template = {
        "TaskNo": "{order.order_no}",
        "ProductCode": "{order.product_code}",
        "StepCode": "{order.extra_data.inbound.step_code}",
        "Operator": "{order.extra_data.inbound.operator}",
        "WarningText": "{cycle.ng_reason}",
        "Image": "{snapshot.image_base64}",
    }
    ctx["payload"] = render_template(template, ctx["ng_ctx"])


# ============================================================
# Then — 校验
# ============================================================
@then(parsers.parse("入站业务码应为 {code:d}"))
def then_code(ctx, code):
    got = _code(ctx["cfg"], ctx["result"])
    assert got == code, f"业务码={got}, 期望 {code}"


@then(parsers.parse("第二次入站业务码应为 {code:d}"))
def then_code2(ctx, code):
    then_code(ctx, code)


@then(parsers.parse('项目 "{name}" 应处于激活状态'))
def then_project_active(name):
    db = SessionLocal()
    try:
        p = db.query(Project).filter(Project.name == name).first()
        assert p is not None and p.is_active, f"项目 {name} 未激活"
    finally:
        db.close()


@then(parsers.parse('工单 "{order_no}" 状态应为 {status}'))
def then_order_status(order_no, status):
    o = _get_order(order_no)
    assert o is not None, f"工单 {order_no} 不存在"
    assert o.status == status, f"工单 {order_no} 状态={o.status}, 期望 {status}"


@then(parsers.parse('上游应收到 "{order_no}" 的完工回传'))
def then_complete_dispatched(ctx, order_no):
    calls = ctx.get("gw_calls", [])
    assert any(ev == "task_complete" and c["order"]["order_no"] == order_no
               for ev, c in calls), f"未捕获 {order_no} 的完工回传, calls={calls}"


@then(parsers.parse("在途报警应有 {n:d} 条"))
def then_active_count(n):
    db = SessionLocal()
    try:
        rows = external_alarm.list_active_alarms(db)
        cn = [r for r in rows if (r.get("task_no") or "").startswith("CN-")]
        assert len(cn) == n, f"在途报警 CN-* 数={len(cn)}, 期望 {n}: {cn}"
    finally:
        db.close()


@then(parsers.parse('在途报警应含 任务 "{task}" 工步 "{step}" 操作员 "{op}"'))
def then_active_has(task, step, op):
    db = SessionLocal()
    try:
        rows = external_alarm.list_active_alarms(db)
        hit = [r for r in rows if r.get("task_no") == task and
               r.get("step_code") == step and r.get("operator") == op]
        assert hit, f"未找到含四要素的在途报警: {rows}"
    finally:
        db.close()


@then(parsers.parse(
    '出站报文应含 任务 "{task}" 产品 "{product}" 工步 "{step}" 操作员 "{op}"'))
def then_payload_fields(ctx, task, product, step, op):
    p = ctx["payload"]
    assert p["TaskNo"] == task and p["ProductCode"] == product \
        and p["StepCode"] == step and p["Operator"] == op, f"报文字段不符: {p}"


@then("出站报文应含 Base64 截图")
def then_payload_image(ctx):
    img = ctx["payload"].get("Image")
    assert img and base64.b64decode(img), f"报文缺 Base64 截图: {img!r}"


@then(parsers.parse('工单 "{order_no}" 应留痕 工步 "{step}" 操作员 "{op}"'))
def then_order_extra(order_no, step, op):
    o = _get_order(order_no)
    assert o is not None, f"工单 {order_no} 不存在"
    inbound = (o.extra_data or {}).get("inbound") or {}
    assert inbound.get("step_code") == step and inbound.get("operator") == op, \
        f"工单留痕不符: {o.extra_data}"


@then("四要素显示配置应全部开启")
def then_taskinfo_on(ctx):
    tinfo = ctx["cfg"].get("task_info_display") or {}
    assert all(tinfo.get(k) for k in
               ("show_task_no", "show_product_code", "show_step_code", "show_operator")), \
        f"四要素显示配置未全开: {tinfo}"


@then("工位0 应已被拉起检测")
def then_ch0_started(ctx):
    ctx["ch0_mgr"].start_detection.assert_called_once()


@then("工位0 不应被拉起检测")
def then_ch0_not_started(ctx):
    ctx["ch0_mgr"].start_detection.assert_not_called()
